from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import SocialLogin, User
from apps.instagram.models import ConnectedAccount
from apps.instagram.services import oauth
from apps.instagram.services.connect import PENDING_SESSION_KEY
from apps.instagram.services.oauth import InstagramIdentity, InstagramOAuthError

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}


def _start(client, **params):
    response = client.get(reverse("instagram:start"), params)
    assert response.status_code == 302
    return parse_qs(urlsplit(response["Location"]).query)["state"][0]


def _callback(client, state):
    return client.get(reverse("instagram:callback"), {"code": "mock", "state": state})


def _identity(external_id, username="existing"):
    return InstagramIdentity(
        user_id=external_id,
        username=username,
        access_token="IGQVJfresh-token",
        account_type="BUSINESS",
        expires_at=timezone.now() + timedelta(days=60),
    )


def _signed_in_as(client):
    return client.session.get("_auth_user_id")


# --- first-time Instagram sign-in ------------------------------------

def test_new_instagram_account_is_asked_for_just_an_email(client):
    response = _callback(client, _start(client))
    assert response["Location"] == f"{reverse('core:landing')}?auth=finish"

    body = client.get(response["Location"]).content.decode()
    assert "One last step" in body
    assert f"@{oauth.MOCK_USERNAME}" in body
    assert 'data-mode="finish"' in body
    assert 'data-auto-open="true"' in body
    assert not ConnectedAccount.objects.exists()
    assert _signed_in_as(client) is None


def test_parked_token_is_encrypted_in_the_session(client):
    _callback(client, _start(client))
    assert oauth.MOCK_TOKEN not in str(client.session[PENDING_SESSION_KEY])


def test_email_finishes_sign_up_and_connects_the_account(client):
    _callback(client, _start(client))
    response = client.post(
        reverse("accounts:instagram-finish"), {"email": "New@Example.com"}, **HTMX
    )
    assert response["HX-Redirect"] == reverse("core:dashboard")

    user = User.objects.get(email="new@example.com")
    assert not user.has_usable_password()
    assert user.onboarding_state == User.Onboarding.INSTAGRAM_CONNECTED
    account = ConnectedAccount.objects.get()
    assert account.owner == user
    assert account.external_account_id == oauth.MOCK_USER_ID
    assert account.access_token == oauth.MOCK_TOKEN
    assert SocialLogin.objects.filter(user=user, provider="instagram").exists()
    assert _signed_in_as(client) == str(user.pk)
    assert PENDING_SESSION_KEY not in client.session


def test_email_that_already_has_an_account_is_refused(client, user):
    _callback(client, _start(client))
    response = client.post(reverse("accounts:instagram-finish"), {"email": user.email}, **HTMX)
    assert response.status_code == 200
    assert "Sign in to it" in response.content.decode()
    assert not ConnectedAccount.objects.exists()
    assert _signed_in_as(client) is None


def test_signing_in_by_email_connects_the_parked_account(client, user):
    _callback(client, _start(client))
    client.post(
        reverse("accounts:login"), {"username": user.email, "password": "sup3r-secret-pass"}
    )
    assert ConnectedAccount.objects.get().owner == user


def test_finish_without_a_parked_account_creates_nothing(client):
    response = client.post(reverse("accounts:instagram-finish"), {"email": "a@example.com"})
    assert response.status_code == 302
    assert not User.objects.exists()


def test_the_instagram_account_can_sign_in_again_after_sign_up(client):
    _callback(client, _start(client))
    client.post(reverse("accounts:instagram-finish"), {"email": "new@example.com"})
    client.logout()

    response = _callback(client, _start(client))
    assert response["Location"] == reverse("core:dashboard")
    assert _signed_in_as(client) == str(User.objects.get().pk)


# --- returning users and connecting ----------------------------------

def test_instagram_sign_in_logs_in_the_accounts_owner_and_refreshes_the_token(
    client, user, account, monkeypatch
):
    monkeypatch.setattr(oauth, "exchange_code", lambda code: _identity(account.external_account_id))
    response = _callback(client, _start(client))
    assert response["Location"] == reverse("core:dashboard")
    assert _signed_in_as(client) == str(user.pk)
    account.refresh_from_db()
    assert account.access_token == "IGQVJfresh-token"
    assert ConnectedAccount.objects.count() == 1


def test_signed_in_user_connects_instagram(client, user):
    client.force_login(user)
    response = _callback(client, _start(client))
    assert response["Location"] == reverse("instagram:accounts")
    account = ConnectedAccount.objects.get()
    assert account.owner == user
    user.refresh_from_db()
    assert user.onboarding_state == User.Onboarding.INSTAGRAM_CONNECTED


def test_one_instagram_account_cannot_join_two_damdm_accounts(
    client, account, other_user, monkeypatch
):
    monkeypatch.setattr(oauth, "exchange_code", lambda code: _identity(account.external_account_id))
    client.force_login(other_user)
    _callback(client, _start(client))

    account.refresh_from_db()
    assert account.owner != other_user
    assert account.access_token == "IGQVJtest-token-value"
    assert ConnectedAccount.objects.count() == 1


def test_state_started_in_another_browser_is_refused(client):
    state = _start(client)
    other_browser = Client()
    _callback(other_browser, state)
    assert PENDING_SESSION_KEY not in other_browser.session
    assert not ConnectedAccount.objects.exists()


@override_settings(META_MOCK=False, META_APP_ID="")
def test_instagram_without_credentials_explains_instead_of_redirecting(client):
    response = client.get(reverse("instagram:start"))
    assert response["Location"] == f"{reverse('core:landing')}?auth=signin"


def test_mock_instagram_returns_to_the_same_host(client):
    response = client.get(reverse("instagram:start"))
    assert response["Location"].startswith(f"{reverse('instagram:callback')}?")


@override_settings(
    META_MOCK=False, META_APP_ID="app-1", META_APP_SECRET="secret",
    META_REDIRECT_URI="http://localhost/instagram/callback/",
)
def test_real_instagram_first_moves_to_the_callback_host(client):
    response = client.get(reverse("instagram:start"))
    assert response["Location"] == "http://localhost/instagram/start/"


def test_cancelled_instagram_sign_in(client):
    response = client.get(reverse("instagram:callback"), {"error": "access_denied"})
    assert response.status_code == 302
    assert PENDING_SESSION_KEY not in client.session


# --- the HTTP client, without mock mode ------------------------------

@override_settings(META_MOCK=False, META_APP_ID="app-1")
def test_authorize_url_requests_the_messaging_scopes():
    query = parse_qs(urlsplit(oauth.authorize_url("s1")).query)
    assert query["client_id"] == ["app-1"]
    assert "instagram_business_manage_messages" in query["scope"][0]
    assert query["state"] == ["s1"]


@override_settings(META_MOCK=False)
@pytest.mark.parametrize(
    "token_body",
    [
        {"access_token": "short", "user_id": 1, "permissions": ["instagram_business_basic"]},
        {"data": [{"access_token": "short", "user_id": "1",
                   "permissions": "instagram_business_basic"}]},
    ],
)
def test_exchange_code_returns_a_long_lived_identity(token_body):
    def handler(request):
        if request.url.host == "api.instagram.com":
            return httpx.Response(200, json=token_body)
        if request.url.path == "/access_token":
            assert request.url.params["access_token"] == "short"
            return httpx.Response(200, json={"access_token": "long", "expires_in": 5184000})
        assert request.url.params["access_token"] == "long"
        return httpx.Response(200, json={
            "user_id": "17841400000000555", "username": "realcreator", "name": "Real",
            "account_type": "MEDIA_CREATOR", "followers_count": 1200,
        })

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        identity = oauth.exchange_code("code", client=http)

    assert identity.user_id == "17841400000000555"
    assert identity.username == "realcreator"
    assert identity.access_token == "long"
    assert identity.followers_count == 1200
    assert identity.scopes == ("instagram_business_basic",)
    assert identity.expires_at > timezone.now() + timedelta(days=59)


@override_settings(META_MOCK=False)
def test_exchange_code_raises_on_a_rejected_code():
    transport = httpx.MockTransport(lambda request: httpx.Response(400, json={"error": "bad"}))
    with httpx.Client(transport=transport) as http, pytest.raises(InstagramOAuthError):
        oauth.exchange_code("bad", client=http)
