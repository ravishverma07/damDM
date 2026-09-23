"""
Google sign-in, two ways:

* The button: OAuth 2.0 authorization-code flow. The identity comes from the
  userinfo endpoint over TLS with the access token we just received.
* One Tap: the browser hands us a Google-signed ID token, so its RS256
  signature, issuer, audience, expiry and nonce are all checked here.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from django.conf import settings
from django.core.cache import cache
from django.urls import reverse
from django.utils.crypto import constant_time_compare, salted_hmac

logger = logging.getLogger("damdm.accounts")

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
CERTS_URL = "https://www.googleapis.com/oauth2/v3/certs"
TIMEOUT_SECONDS = 10.0
ISSUERS = ("accounts.google.com", "https://accounts.google.com")
CERTS_CACHE_KEY = "google-oauth-certs"
#: Allowed clock difference when checking token times.
LEEWAY_SECONDS = 60
#: One Tap nonces change every hour; Google's ID tokens live for an hour.
NONCE_WINDOW_SECONDS = 60 * 60
USED_TOKEN_CACHE_PREFIX = "google-one-tap-used:"


class GoogleOAuthError(Exception):
    """Google sign-in failed. The message is safe to show to the user."""


@dataclass(frozen=True)
class GoogleIdentity:
    sub: str
    email: str
    email_verified: bool
    name: str = ""
    picture: str = ""


#: Returned by ``exchange_code`` when ``GOOGLE_MOCK`` is on.
MOCK_IDENTITY = GoogleIdentity(
    sub="mock-google-sub-1",
    email="mock.google.user@example.com",
    email_verified=True,
    name="Mock Google User",
)


def is_configured() -> bool:
    return settings.GOOGLE_MOCK or bool(settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET)


def one_tap_enabled() -> bool:
    """One Tap needs a real client ID; mock mode has no Google-signed tokens."""
    return bool(settings.GOOGLE_CLIENT_ID) and not settings.GOOGLE_MOCK


def one_tap_nonce(device_id: str, *, windows_ago: int = 0) -> str:
    """
    The nonce Google embeds in the ID token, tied to this browser and the hour.

    Derived rather than stored, so showing the prompt never creates a session.
    """
    window = int(time.time() // NONCE_WINDOW_SECONDS) - windows_ago
    return salted_hmac("google-one-tap", f"{device_id}:{window}", algorithm="sha256").hexdigest()


def verify_one_tap(token: str, *, device_id: str, client: httpx.Client | None = None):
    """Verify a One Tap credential for this browser, accepting it only once."""
    claims_nonce = _unverified_claims(token).get("nonce", "")
    nonce = next(
        (
            candidate
            for candidate in (one_tap_nonce(device_id), one_tap_nonce(device_id, windows_ago=1))
            if constant_time_compare(candidate, str(claims_nonce))
        ),
        "",
    )
    identity, token_id = _verify(token, nonce=nonce, client=client)
    if not cache.add(f"{USED_TOKEN_CACHE_PREFIX}{token_id}", True, 2 * NONCE_WINDOW_SECONDS):
        raise GoogleOAuthError("That Google sign-in was already used. Please try again.")
    return identity


def authorize_url(state: str) -> str:
    """Where to send the browser to start sign-in."""
    if settings.GOOGLE_MOCK:
        # Relative, so the callback lands on whatever host the visitor is using.
        query = urlencode({"code": "mock", "state": state})
        return f"{reverse('accounts:google-callback')}?{query}"
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": settings.GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def exchange_code(code: str, *, client: httpx.Client | None = None) -> GoogleIdentity:
    """Trade the callback ``code`` for the signed-in person's identity."""
    if settings.GOOGLE_MOCK:
        return MOCK_IDENTITY

    http = client or httpx.Client(timeout=TIMEOUT_SECONDS)
    try:
        token = _json(
            http.post(
                TOKEN_URL,
                data={
                    "code": code,
                    "client_id": settings.GOOGLE_CLIENT_ID,
                    "client_secret": settings.GOOGLE_CLIENT_SECRET,
                    "redirect_uri": settings.GOOGLE_REDIRECT_URI,
                    "grant_type": "authorization_code",
                },
            )
        )
        access_token = token.get("access_token")
        if not access_token:
            raise GoogleOAuthError("Google didn't complete the sign-in. Please try again.")
        info = _json(http.get(USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}))
    except httpx.HTTPError as exc:
        raise GoogleOAuthError("Couldn't reach Google. Please try again.") from exc
    finally:
        if client is None:
            http.close()

    if not info.get("sub") or not info.get("email"):
        raise GoogleOAuthError("Google didn't share an email address for this account.")
    return GoogleIdentity(
        sub=str(info["sub"]),
        email=str(info["email"]).strip().lower(),
        email_verified=info.get("email_verified") in (True, "true"),
        name=str(info.get("name") or ""),
        picture=str(info.get("picture") or ""),
    )


def _unverified_claims(token: str) -> dict:
    try:
        claims = json.loads(_b64decode(token.split(".")[1]))
    except (ValueError, IndexError):
        return {}
    return claims if isinstance(claims, dict) else {}


def _verify(
    token: str, *, nonce: str, client: httpx.Client | None = None
) -> tuple[GoogleIdentity, str]:
    """Check a Google ID token; return its identity and a unique id for the token."""
    invalid = GoogleOAuthError("Google sign-in couldn't be verified. Please try again.")
    try:
        header_b64, payload_b64, signature_b64 = token.split(".")
        header = json.loads(_b64decode(header_b64))
        claims = json.loads(_b64decode(payload_b64))
        signature = _b64decode(signature_b64)
    except ValueError as exc:
        raise invalid from exc
    if header.get("alg") != "RS256" or not isinstance(claims, dict):
        raise invalid

    key = _signing_key(str(header.get("kid", "")), client=client)
    if key is None:
        raise invalid
    try:
        key.verify(
            signature,
            f"{header_b64}.{payload_b64}".encode(),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except InvalidSignature as exc:
        raise invalid from exc

    now = time.time()
    if (
        claims.get("iss") not in ISSUERS
        or claims.get("aud") != settings.GOOGLE_CLIENT_ID
        or not isinstance(claims.get("exp"), int | float)
        or claims["exp"] < now - LEEWAY_SECONDS
        or float(claims.get("iat", 0)) > now + LEEWAY_SECONDS
        or not nonce
        or claims.get("nonce") != nonce
    ):
        raise invalid
    if not claims.get("sub") or not claims.get("email"):
        raise GoogleOAuthError("Google didn't share an email address for this account.")
    identity = GoogleIdentity(
        sub=str(claims["sub"]),
        email=str(claims["email"]).strip().lower(),
        email_verified=claims.get("email_verified") in (True, "true"),
        name=str(claims.get("name") or ""),
        picture=str(claims.get("picture") or ""),
    )
    return identity, str(claims.get("jti") or signature_b64)


def _signing_key(kid: str, *, client: httpx.Client | None) -> rsa.RSAPublicKey | None:
    """Google's public key for ``kid``, refreshing the cached key set once if unknown."""
    keys = cache.get(CERTS_CACHE_KEY)
    if not keys or kid not in keys:
        keys = _fetch_certs(client)
    jwk = keys.get(kid)
    if jwk is None:
        return None
    numbers = rsa.RSAPublicNumbers(
        e=int.from_bytes(_b64decode(jwk["e"]), "big"),
        n=int.from_bytes(_b64decode(jwk["n"]), "big"),
    )
    return numbers.public_key()


def _fetch_certs(client: httpx.Client | None) -> dict:
    http = client or httpx.Client(timeout=TIMEOUT_SECONDS)
    try:
        response = http.get(CERTS_URL)
    except httpx.HTTPError as exc:
        raise GoogleOAuthError("Couldn't reach Google. Please try again.") from exc
    finally:
        if client is None:
            http.close()
    data = _json(response)
    keys = {
        str(k["kid"]): {"n": k["n"], "e": k["e"]}
        for k in data.get("keys", [])
        if k.get("kty") == "RSA" and k.get("kid")
    }
    cache.set(CERTS_CACHE_KEY, keys, _max_age(response.headers.get("Cache-Control", "")))
    return keys


def _max_age(cache_control: str) -> int:
    for part in cache_control.split(","):
        name, _, value = part.strip().partition("=")
        if name == "max-age" and value.isdigit():
            return int(value)
    return 3600


def _b64decode(value: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, TypeError) as exc:
        raise ValueError("bad base64") from exc


def _json(response: httpx.Response) -> dict:
    if response.status_code != 200:
        # Google's reason (e.g. invalid_client) helps diagnose setup mistakes; it holds no secrets.
        try:
            detail = response.json()
        except ValueError:
            detail = {}
        logger.warning(
            "Google returned HTTP %s for %s: %s %s",
            response.status_code,
            response.request.url.path if response.request else "",
            detail.get("error", ""),
            detail.get("error_description", ""),
        )
        raise GoogleOAuthError("Google rejected the sign-in. Please try again.")
    try:
        return response.json()
    except ValueError as exc:
        raise GoogleOAuthError("Google sent an unexpected response.") from exc
