"""
Web Push: send a notification to a browser's push service.

Payloads are encrypted for the browser with aes128gcm (RFC 8291, via
``http_ece``) and the request is signed with our VAPID key (RFC 8292), so
push services know it comes from us. Keys are base64url strings in settings.
"""

from __future__ import annotations

import base64
import functools
import json
import logging
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import http_ece
import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from django.conf import settings

logger = logging.getLogger("damdm.push")

TIMEOUT_SECONDS = 10.0
#: How long push services keep an undelivered message (phone offline).
DEFAULT_TTL = 60 * 60 * 24
VAPID_LIFETIME = 60 * 60 * 12
#: Browsers' push services. Endpoints elsewhere are refused, so a crafted
#: subscription can't make the server call arbitrary URLs.
PUSH_SERVICE_HOSTS = (
    "fcm.googleapis.com",
    "android.googleapis.com",
    "updates.push.services.mozilla.com",
    "push.services.mozilla.com",
    ".push.apple.com",
    ".notify.windows.com",
)


class PushGone(Exception):
    """The subscription no longer exists (the user unsubscribed or reinstalled)."""


@dataclass(frozen=True)
class Message:
    title: str
    body: str = ""
    url: str = "/dashboard/"
    #: Notifications with the same tag replace each other on the device.
    tag: str = ""

    def to_json(self) -> bytes:
        return json.dumps(
            {"title": self.title, "body": self.body, "url": self.url, "tag": self.tag},
            ensure_ascii=False,
        ).encode()


def b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def is_configured() -> bool:
    return bool(settings.VAPID_PRIVATE_KEY)


def _private_key() -> ec.EllipticCurvePrivateKey:
    value = int.from_bytes(b64url_decode(settings.VAPID_PRIVATE_KEY), "big")
    return ec.derive_private_key(value, ec.SECP256R1())


def _raw_public_key(key: ec.EllipticCurvePrivateKey | ec.EllipticCurvePublicKey) -> bytes:
    public = key.public_key() if isinstance(key, ec.EllipticCurvePrivateKey) else key
    return public.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )


def public_key() -> str:
    """The ``applicationServerKey`` browsers subscribe with."""
    return _public_key_for(settings.VAPID_PRIVATE_KEY) if is_configured() else ""


@functools.lru_cache(maxsize=4)
def _public_key_for(private_key: str) -> str:
    return b64url_encode(_raw_public_key(_private_key()))


def generate_private_key() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    return b64url_encode(key.private_numbers().private_value.to_bytes(32, "big"))


def is_allowed_endpoint(endpoint: str) -> bool:
    parts = urlsplit(endpoint or "")
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host or len(endpoint) > 1000:
        return False
    return any(
        host.endswith(allowed) if allowed.startswith(".") else host == allowed
        for allowed in PUSH_SERVICE_HOSTS
    )


def vapid_header(endpoint: str) -> str:
    parts = urlsplit(endpoint)
    header = b64url_encode(json.dumps({"typ": "JWT", "alg": "ES256"}).encode())
    claims = b64url_encode(json.dumps({
        "aud": f"{parts.scheme}://{parts.netloc}",
        "exp": int(time.time()) + VAPID_LIFETIME,
        "sub": settings.VAPID_SUBJECT,
    }).encode())
    key = _private_key()
    der = key.sign(f"{header}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    signature = b64url_encode(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return f"vapid t={header}.{claims}.{signature}, k={b64url_encode(_raw_public_key(key))}"


def encrypt(payload: bytes, *, p256dh: str, auth: str) -> bytes:
    return http_ece.encrypt(
        payload,
        private_key=ec.generate_private_key(ec.SECP256R1()),
        dh=b64url_decode(p256dh),
        auth_secret=b64url_decode(auth),
        version="aes128gcm",
    )


def send(
    subscription,
    message: Message,
    *,
    ttl: int = DEFAULT_TTL,
    urgency: str = "normal",
    client: httpx.Client | None = None,
) -> bool:
    """
    Deliver one message. Returns True on success; raises PushGone when the
    subscription should be deleted. Other failures are logged and return False.
    """
    endpoint = subscription.endpoint
    if not is_configured() or not is_allowed_endpoint(endpoint):
        return False
    body = encrypt(message.to_json(), p256dh=subscription.p256dh, auth=subscription.auth)
    headers = {
        "Authorization": vapid_header(endpoint),
        "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream",
        "TTL": str(ttl),
        "Urgency": urgency,
    }
    if message.tag:
        headers["Topic"] = "".join(c for c in message.tag if c.isalnum())[:32]
    http = client or httpx.Client(timeout=TIMEOUT_SECONDS)
    try:
        response = http.post(endpoint, content=body, headers=headers)
    except httpx.HTTPError:
        logger.warning("Push to %s failed to connect", urlsplit(endpoint).hostname)
        return False
    finally:
        if client is None:
            http.close()
    if response.status_code in (404, 410):
        raise PushGone
    if response.status_code >= 400:
        logger.warning(
            "Push to %s rejected: HTTP %s %s",
            urlsplit(endpoint).hostname, response.status_code, response.text[:200],
        )
        return False
    return True
