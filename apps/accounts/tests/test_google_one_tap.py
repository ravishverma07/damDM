import base64
import json
import time

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse

from apps.accounts.models import SocialLogin, User
from apps.accounts.services import google

pytestmark = pytest.mark.django_db

CLIENT_ID = "one-tap-client.apps.googleusercontent.com"
DEVICE = "a" * 32
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)

real_google = override_settings(GOOGLE_MOCK=False, GOOGLE_CLIENT_ID=CLIENT_ID)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _jwk(key, kid="k1") -> dict:
    numbers = key.public_key().public_numbers()
    return {
        "kty": "RSA", "kid": kid, "alg": "RS256", "use": "sig",
        "n": _b64(numbers.n.to_bytes(256, "big")), "e": _b64(numbers.e.to_bytes(3, "big")),
    }


def _token(key=KEY, kid="k1", **overrides) -> str:
    now = int(time.time())
    claims = {
        "iss": "https://accounts.google.com", "aud": CLIENT_ID, "sub": "google-sub-42",
        "email": "One.Tap@Example.com", "email_verified": True, "name": "One Tap",
        "iat": now, "exp": now + 3600, "jti": f"jti-{time.time_ns()}",
        "nonce": google.one_tap_nonce(DEVICE),
        **overrides,
    }
    header = _b64(json.dumps({"alg": "RS256", "kid": kid, "typ": "JWT"}).encode())
    payload = _b64(json.dumps(claims).encode())
    signature = key.sign(f"{header}.{payload}".encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{header}.{payload}.{_b64(signature)}"


@pytest.fixture(autouse=True)
def google_certs(monkeypatch):
    """Serve our test key as Google's signing key, counting fetches."""
    cache.clear()
    calls = []

    def handler(request):
        calls.append(request.url)
        return httpx.Response(
            200, json={"keys": [_jwk(KEY)]}, headers={"Cache-Control": "public, max-age=1234"}
        )

    real_client = httpx.Client
    monkeypatch.setattr(
        google.httpx, "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler)),
    )
    return calls


def _post(client, credential, **extra):
    client.cookies["damdm_device"] = DEVICE
    return client.post(reverse("accounts:google-one-tap"), {"credential": credential, **extra})


@real_google
def test_one_tap_signs_in_and_creates_the_user(client):
    response = _post(client, _token())
    assert response.status_code == 302
    assert response["Location"] == reverse("core:dashboard")
    user = User.objects.get(email="one.tap@example.com")
    assert client.session["_auth_user_id"] == str(user.pk)
    assert SocialLogin.objects.filter(
        user=user, provider="google", provider_uid="google-sub-42"
    ).exists()


@real_google
@pytest.mark.parametrize(
    ("next_url", "expected"),
    [("/pricing/", "/pricing/"), ("https://evil.example/", "/dashboard/")],
)
def test_one_tap_returns_only_to_pages_on_this_site(client, next_url, expected):
    assert _post(client, _token(), next=next_url)["Location"] == expected


@real_google
@pytest.mark.parametrize(
    "credential",
    [
        pytest.param(lambda: _token(key=OTHER_KEY), id="forged-signature"),
        pytest.param(lambda: _token(aud="someone-else"), id="other-app"),
        pytest.param(lambda: _token(iss="https://evil.example"), id="wrong-issuer"),
        pytest.param(lambda: _token(exp=int(time.time()) - 600), id="expired"),
        pytest.param(lambda: _token(nonce="stolen"), id="wrong-nonce"),
        pytest.param(lambda: _token(nonce=google.one_tap_nonce("b" * 32)), id="other-browser"),
        pytest.param(lambda: _token(kid="unknown"), id="unknown-key"),
        pytest.param(lambda: _token(email_verified=False), id="unverified-email"),
        pytest.param(lambda: "not-a-token", id="garbage"),
    ],
)
def test_one_tap_rejects_bad_tokens(client, credential):
    response = _post(client, credential())
    assert response.status_code == 302
    assert "_auth_user_id" not in client.session
    assert not User.objects.filter(email="one.tap@example.com").exists()


@real_google
def test_a_token_works_only_once(client, google_certs):
    token = _token()
    _post(client, token)
    client.logout()
    _post(client, token)
    assert "_auth_user_id" not in client.session
    assert len(google_certs) == 1  # Google's keys were cached


@real_google
def test_prompt_is_offered_to_signed_out_visitors_only(client, user):
    body = client.get(reverse("marketing:pricing")).content.decode()
    assert "accounts.google.com/gsi/client" in body
    assert "apps.googleusercontent.com" in body  # the client id, JS-escaped
    client.force_login(user)
    assert "gsi/client" not in client.get(reverse("core:dashboard")).content.decode()


def test_prompt_is_hidden_in_mock_mode(client):
    assert "gsi/client" not in client.get(reverse("core:landing")).content.decode()


def test_one_tap_endpoint_is_off_in_mock_mode(client):
    assert _post(client, _token()).status_code == 302
    assert "_auth_user_id" not in client.session


def test_signing_key_reads_googles_published_format():
    pem = KEY.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    cache.set(google.CERTS_CACHE_KEY, {"k1": _jwk(KEY)})
    key = google._signing_key("k1", client=None)
    assert key.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ) == pem
