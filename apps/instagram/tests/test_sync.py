from datetime import timedelta

import httpx
import pytest
from django.test import override_settings
from django.utils import timezone

from apps.instagram.models import ConnectedAccount, Post
from apps.instagram.services.connect import connect_account
from apps.instagram.services.graph import MOCK_MEDIA, GraphAPIError, InstagramGraph
from apps.instagram.services.oauth import InstagramIdentity
from apps.instagram.services.sync import sync_account
from apps.instagram.tasks import refresh_expiring_tokens, sync_account_task, sync_stale_accounts

pytestmark = pytest.mark.django_db


def test_sync_updates_the_profile_and_creates_posts(account):
    result = sync_account(account)
    account.refresh_from_db()
    assert (result.created, result.updated) == (len(MOCK_MEDIA), 0)
    assert account.followers_count == 1234
    assert account.last_synced_at is not None
    assert account.posts.count() == len(MOCK_MEDIA)


def test_syncing_again_updates_instead_of_duplicating(account):
    sync_account(account)
    result = sync_account(account)
    assert (result.created, result.updated) == (0, len(MOCK_MEDIA))
    assert account.posts.count() == len(MOCK_MEDIA)


@override_settings(META_MOCK=False)
def test_sync_maps_graph_fields_onto_posts(account):
    def handler(request):
        if request.url.path.endswith("/me"):
            return httpx.Response(200, json={
                "id": "app-1", "user_id": account.external_account_id,
                "username": "renamed", "followers_count": 99,
            })
        return httpx.Response(200, json={"data": [
            {"id": "m1", "media_type": "VIDEO", "media_product_type": "REELS", "caption": "LINK",
             "permalink": "https://www.instagram.com/reel/m1/",
             "thumbnail_url": "https://cdn.example/t1.jpg",
             "timestamp": "2026-09-01T10:00:00+0000", "comments_count": 5, "like_count": 7},
            {"id": "m2", "media_type": "IMAGE", "media_url": "https://cdn.example/m2.jpg",
             "timestamp": "not-a-date"},
            {"caption": "no id, so skipped"},
        ]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    sync_account(account, graph=InstagramGraph(account.access_token, client=client))

    account.refresh_from_db()
    assert account.username == "renamed"
    assert account.app_scoped_user_id == "app-1"
    reel = Post.objects.get(external_media_id="m1")
    assert reel.is_reel
    assert reel.comments_count == 5
    assert reel.thumbnail_url == "https://cdn.example/t1.jpg"
    assert reel.published_at.year == 2026
    image = Post.objects.get(external_media_id="m2")
    assert image.thumbnail_url == "https://cdn.example/m2.jpg"
    assert image.published_at is None
    assert Post.objects.count() == 2


def test_token_error_during_sync_marks_the_account_expired(account, monkeypatch):
    def fail(acct):
        raise GraphAPIError("Session has expired", code=190)

    monkeypatch.setattr("apps.instagram.tasks.sync_account", fail)
    sync_account_task(account.pk)
    account.refresh_from_db()
    assert account.status == ConnectedAccount.Status.EXPIRED
    assert "expired" in account.last_error


def test_sync_task_skips_disconnected_accounts(account, monkeypatch):
    account.disconnect()
    calls = []
    monkeypatch.setattr("apps.instagram.tasks.sync_account", calls.append)
    sync_account_task(account.pk)
    assert calls == []


def test_refresh_job_renews_tokens_close_to_expiry(account):
    account.token_expires_at = timezone.now() + timedelta(days=3)
    account.save()
    refresh_expiring_tokens()
    account.refresh_from_db()
    assert account.token_expires_at > timezone.now() + timedelta(days=59)


def test_refresh_job_leaves_fresh_tokens_alone(account):
    expires_at = account.token_expires_at
    refresh_expiring_tokens()
    account.refresh_from_db()
    assert account.token_expires_at == expires_at


def test_refresh_job_marks_lapsed_tokens_expired(account):
    account.token_expires_at = timezone.now() - timedelta(hours=1)
    account.save()
    refresh_expiring_tokens()
    account.refresh_from_db()
    assert account.status == ConnectedAccount.Status.EXPIRED


def test_stale_accounts_are_synced_and_fresh_ones_skipped(account):
    sync_stale_accounts()
    account.refresh_from_db()
    first_sync = account.last_synced_at
    assert first_sync is not None
    sync_stale_accounts()
    account.refresh_from_db()
    assert account.last_synced_at == first_sync


def test_connecting_an_account_starts_a_sync(user, django_capture_on_commit_callbacks):
    identity = InstagramIdentity(user_id="17841400000000777", username="new", access_token="IGQVJx")
    with django_capture_on_commit_callbacks(execute=True):
        account = connect_account(user, identity)
    assert account.posts.count() == len(MOCK_MEDIA)


def test_reconnecting_reuses_the_earlier_connection(account, user, post):
    account.disconnect()
    identity = InstagramIdentity(
        user_id=account.external_account_id, username="back", access_token="IGQVJnew"
    )
    reconnected = connect_account(user, identity)
    assert reconnected.pk == account.pk
    assert reconnected.status == ConnectedAccount.Status.ACTIVE
    assert ConnectedAccount.objects.count() == 1
    assert reconnected.posts.filter(pk=post.pk).exists()
