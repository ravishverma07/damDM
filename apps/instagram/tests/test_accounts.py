import base64
import hashlib
import hmac
import json

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse

from apps.automations.models import Automation
from apps.core.models import DataDeletionRequest
from apps.instagram.models import ConnectedAccount, Post
from apps.instagram.services.graph import MOCK_MEDIA
from apps.messaging.models import Contact

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


def _signed_request(payload: dict, secret: str = "test-app-secret") -> str:
    def encode(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    body = encode(json.dumps({"algorithm": "HMAC-SHA256", **payload}).encode())
    signature = encode(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
    return f"{signature}.{body}"


# --- pages -------------------------------------------------------------------

def test_accounts_page_requires_login(client):
    assert client.get(reverse("instagram:accounts")).status_code == 302


def test_accounts_page_lists_only_your_accounts(client, user, account, other_user):
    ConnectedAccount.objects.create(
        owner=other_user, external_account_id="999", username="notyours"
    )
    client.force_login(user)
    body = client.get(reverse("instagram:accounts")).content.decode()
    assert f"@{account.username}" in body
    assert "notyours" not in body


def test_empty_accounts_page_offers_to_connect(client, user):
    client.force_login(user)
    assert "Connect Instagram" in client.get(reverse("instagram:accounts")).content.decode()


def test_account_page_shows_synced_posts(client, user, account, post):
    client.force_login(user)
    body = client.get(reverse("instagram:account-detail", args=[account.pk])).content.decode()
    assert "Comment LINK for the guide" in body
    assert "Reel" in body


def test_other_users_cannot_see_or_change_an_account(client, other_user, account):
    client.force_login(other_user)
    assert client.get(reverse("instagram:account-detail", args=[account.pk])).status_code == 404
    assert client.post(reverse("instagram:account-sync", args=[account.pk])).status_code == 404
    assert client.post(
        reverse("instagram:account-disconnect", args=[account.pk])
    ).status_code == 404
    account.refresh_from_db()
    assert account.is_connected


def test_sync_now_fetches_posts(client, user, account):
    client.force_login(user)
    response = client.post(reverse("instagram:account-sync", args=[account.pk]))
    assert response["Location"] == reverse("instagram:account-detail", args=[account.pk])
    assert account.posts.count() == len(MOCK_MEDIA)


def test_sync_now_is_throttled(client, user, account, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "apps.instagram.views.enqueue", lambda task, *args: calls.append(args) or True
    )
    client.force_login(user)
    for _ in range(2):
        client.post(reverse("instagram:account-sync", args=[account.pk]))
    assert len(calls) == 1


def test_sync_requires_post(client, user, account):
    client.force_login(user)
    assert client.get(reverse("instagram:account-sync", args=[account.pk])).status_code == 405


def test_disconnect_drops_the_token_and_pauses_automations(client, user, account, automation):
    client.force_login(user)
    response = client.post(reverse("instagram:account-disconnect", args=[account.pk]))
    assert response["Location"] == reverse("instagram:accounts")
    account.refresh_from_db()
    automation.refresh_from_db()
    assert account.status == ConnectedAccount.Status.DISCONNECTED
    assert account.encrypted_access_token == ""
    assert automation.status == Automation.Status.PAUSED


# --- deauthorize -------------------------------------------------------------

def test_deauthorize_revokes_the_account(client, account, automation):
    response = client.post(
        reverse("instagram:deauthorize"),
        {"signed_request": _signed_request({"user_id": account.external_account_id})},
    )
    assert response.status_code == 200
    account.refresh_from_db()
    automation.refresh_from_db()
    assert account.status == ConnectedAccount.Status.REVOKED
    assert automation.status == Automation.Status.PAUSED


def test_deauthorize_also_matches_the_app_scoped_id(client, account):
    account.app_scoped_user_id = "app-scoped-7"
    account.save()
    client.post(
        reverse("instagram:deauthorize"),
        {"signed_request": _signed_request({"user_id": "app-scoped-7"})},
    )
    account.refresh_from_db()
    assert account.status == ConnectedAccount.Status.REVOKED


def test_empty_user_id_matches_no_account(client, account):
    client.post(
        reverse("instagram:deauthorize"), {"signed_request": _signed_request({"user_id": ""})}
    )
    account.refresh_from_db()
    assert account.is_connected


@pytest.mark.parametrize("name", ["instagram:deauthorize", "instagram:data-deletion"])
def test_callbacks_reject_a_bad_signature(client, account, name):
    forged = _signed_request({"user_id": account.external_account_id}, secret="wrong-secret")
    assert client.post(reverse(name), {"signed_request": forged}).status_code == 400
    assert client.post(reverse(name), {"signed_request": "garbage"}).status_code == 400
    account.refresh_from_db()
    assert account.is_connected
    assert not DataDeletionRequest.objects.exists()


# --- data deletion -----------------------------------------------------------

def test_data_deletion_removes_the_accounts_data(client, user, account, post, contact, automation):
    response = client.post(
        reverse("instagram:data-deletion"),
        {"signed_request": _signed_request({"user_id": account.external_account_id})},
    )
    body = response.json()
    deletion = DataDeletionRequest.objects.get(confirmation_code=body["confirmation_code"])
    status_path = reverse("core:data-deletion-status", args=[deletion.confirmation_code])
    assert body["url"].endswith(status_path)

    assert deletion.status == DataDeletionRequest.Status.COMPLETED
    assert deletion.deleted_counts["posts"] == 1
    assert deletion.deleted_counts["contacts"] == 1
    assert not ConnectedAccount.objects.exists()
    assert not Post.objects.exists()
    assert not Contact.objects.exists()
    assert not Automation.objects.exists()
    assert get_user_model().objects.filter(pk=user.pk).exists()

    page = client.get(status_path)
    assert page.status_code == 200
    assert "Completed" in page.content.decode()


def test_data_deletion_removes_the_person_as_a_contact_elsewhere(client, account, other_user):
    elsewhere = ConnectedAccount.objects.create(
        owner=other_user, external_account_id="555", username="brand"
    )
    Contact.objects.create(account=elsewhere, external_user_id="42")
    client.post(
        reverse("instagram:data-deletion"), {"signed_request": _signed_request({"user_id": "42"})}
    )
    assert not Contact.objects.filter(external_user_id="42").exists()
    assert ConnectedAccount.objects.filter(pk=elsewhere.pk).exists()
    assert ConnectedAccount.objects.filter(pk=account.pk).exists()


def test_unknown_deletion_code_is_a_404(client):
    url = reverse("core:data-deletion-status", args=["not-a-real-code"])
    assert client.get(url).status_code == 404
