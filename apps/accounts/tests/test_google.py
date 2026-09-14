from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from django.test import Client, override_settings
from django.urls import reverse

from apps.accounts.models import SocialLogin, User
from apps.accounts.services import google
from apps.accounts.services.google import GoogleIdentity, GoogleOAuthError

pytestmark = pytest.mark.django_db


def _start(client, **params):
    response = client.get(reverse("accounts:google-start"), params)
    assert response.status_code == 302
    return parse_qs(urlsplit(response["Location"]).query)["state"][0]


def _callback(client, state):
    return client.get(reverse("accounts:google-callback"), {"code": "mock", "state": state})


def _signed_in_as(client):
    return client.session.get("_auth_user_id")


def test_google_sign_in_creates_a_confirmed_passwordless_user(client):
    response = _callback(client, _start(client))
    assert response["Location"] == reverse("core:dashboard")

    user = User.objects.get(email=google.MOCK_IDENTITY.email)
    assert user.email_verified
    assert not user.has_usable_password()
    assert SocialLogin.objects.filter(
        user=user, provider="google", provider_uid=google.MOCK_IDENTITY.sub
    ).exists()
    assert _signed_in_as(client) == str(user.pk)


def test_signing_in_with_google_again_reuses_the_account(client):
    _callback(client, _start(client))
    client.logout()
    _callback(client, _start(client))
    assert User.objects.count() == 1
    assert SocialLogin.objects.count() == 1


def test_google_sign_in_returns_to_next(client):
    response = _callback(client, _start(client, next="/accounts/settings/"))
    assert response["Location"] == "/accounts/settings/"


def test_google_turns_off_the_password_of_an_unconfirmed_email_account(client, user, monkeypatch):
    monkeypatch.setattr(
        google, "exchange_code",
        lambda code: GoogleIdentity(sub="g-123", email=user.email, email_verified=True),
    )
    _callback(client, _start(client))
    user.refresh_from_db()
    assert _signed_in_as(client) == str(user.pk)
    assert user.email_verified
    assert not user.has_usable_password()


def test_google_keeps_the_password_of_a_confirmed_account(client, user, monkeypatch):
    user.email_verified = True
    user.save()
    monkeypatch.setattr(
        google, "exchange_code",
        lambda code: GoogleIdentity(sub="g-123", email=user.email, email_verified=True),
    )
    _callback(client, _start(client))
    user.refresh_from_db()
    assert user.has_usable_password()


def test_unverified_google_email_is_refused(client, monkeypatch):
    monkeypatch.setattr(
        google, "exchange_code",
        lambda code: GoogleIdentity(sub="g-9", email="x@example.com", email_verified=False),
    )
    response = _callback(client, _start(client))
    assert response["Location"].endswith("?auth=signin")
    assert not User.objects.exists()
    assert _signed_in_as(client) is None


def test_forged_state_is_refused(client):
    _start(client)
    _callback(client, "forged-state")
    assert not User.objects.exists()


def test_state_started_in_another_browser_is_refused(client):
    state = _start(client)
    other_browser = Client()
    _callback(other_browser, state)
    assert not User.objects.exists()
    assert _signed_in_as(other_browser) is None


@override_settings(GOOGLE_MOCK=False, GOOGLE_CLIENT_ID="")
def test_google_without_credentials_explains_instead_of_redirecting(client):
    response = client.get(reverse("accounts:google-start"))
    assert response["Location"] == f"{reverse('core:landing')}?auth=signin"


def test_mock_sign_in_returns_to_the_same_host(client):
    response = client.get(reverse("accounts:google-start"))
    assert response["Location"].startswith(f"{reverse('accounts:google-callback')}?")


@override_settings(
    GOOGLE_MOCK=False, GOOGLE_CLIENT_ID="cid", GOOGLE_CLIENT_SECRET="secret",
    GOOGLE_REDIRECT_URI="http://localhost/accounts/google/callback/",
)
def test_real_sign_in_first_moves_to_the_callback_host(client):
    response = client.get(reverse("accounts:google-start"), {"next": "/x/"})
    assert response["Location"] == "http://localhost/accounts/google/start/?next=%2Fx%2F"

    same_host = client.get(reverse("accounts:google-start"), HTTP_HOST="localhost")
    assert same_host["Location"].startswith(google.AUTHORIZE_URL)


def test_cancelled_google_sign_in(client):
    response = client.get(reverse("accounts:google-callback"), {"error": "access_denied"})
    assert response.status_code == 302
    assert not User.objects.exists()


# --- the HTTP client, without mock mode ------------------------------

@override_settings(GOOGLE_MOCK=False, GOOGLE_CLIENT_ID="cid")
def test_authorize_url_asks_for_openid_email_and_profile():
    query = parse_qs(urlsplit(google.authorize_url("s1")).query)
    assert query["scope"] == ["openid email profile"]
    assert query["state"] == ["s1"]
    assert query["client_id"] == ["cid"]


@override_settings(GOOGLE_MOCK=False)
def test_exchange_code_reads_userinfo_with_the_access_token():
    def handler(request):
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "ya29.token"})
        assert request.headers["Authorization"] == "Bearer ya29.token"
        return httpx.Response(
            200, json={"sub": "1", "email": "A@Example.com", "email_verified": True, "name": "A"}
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        identity = google.exchange_code("code", client=http)
    assert identity == GoogleIdentity(sub="1", email="a@example.com", email_verified=True, name="A")


@override_settings(GOOGLE_MOCK=False)
def test_exchange_code_raises_on_a_rejected_code():
    transport = httpx.MockTransport(lambda request: httpx.Response(400, json={"error": "bad"}))
    with httpx.Client(transport=transport) as http, pytest.raises(GoogleOAuthError):
        google.exchange_code("bad", client=http)
