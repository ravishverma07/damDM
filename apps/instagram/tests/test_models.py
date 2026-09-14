from datetime import timedelta

import pytest
from django.db import IntegrityError
from django.utils import timezone

from apps.instagram.models import ConnectedAccount, Post, WebhookEvent

pytestmark = pytest.mark.django_db


def test_token_is_stored_encrypted_not_plaintext(account):
    assert account.encrypted_access_token
    assert "IGQVJtest-token-value" not in account.encrypted_access_token
    assert account.access_token == "IGQVJtest-token-value"


def test_claimed_account_cannot_be_connected_by_anyone_else(account, other_user):
    with pytest.raises(IntegrityError):
        ConnectedAccount.objects.create(
            owner=other_user, external_account_id=account.external_account_id
        )


def test_disconnected_account_can_be_claimed_again(account, other_user):
    account.disconnect()
    assert ConnectedAccount.objects.create(
        owner=other_user, external_account_id=account.external_account_id
    ).pk


def test_same_external_id_on_another_platform_is_a_different_account(account, user):
    assert ConnectedAccount.objects.create(
        owner=user, platform="telegram", external_account_id=account.external_account_id
    ).pk


def test_two_users_may_connect_different_accounts(account, other_user):
    second = ConnectedAccount.objects.create(
        owner=other_user, external_account_id="17841400000000002"
    )
    assert second.pk != account.pk


def test_expired_token_is_reported_as_expired(account):
    account.token_expires_at = timezone.now() - timedelta(minutes=1)
    assert account.is_token_expired
    assert not account.is_connected
    assert not account.has_usable_token()


def test_needs_refresh_inside_the_window(account):
    account.token_expires_at = timezone.now() + timedelta(days=3)
    assert account.needs_refresh
    account.token_expires_at = timezone.now() + timedelta(days=30)
    assert not account.needs_refresh


def test_disconnect_removes_the_token(account):
    account.disconnect()
    account.refresh_from_db()
    assert account.encrypted_access_token == ""
    assert account.token_expires_at is None
    assert account.status == ConnectedAccount.Status.DISCONNECTED
    assert not account.has_usable_token()


def test_media_id_is_unique_per_account(post, account):
    with pytest.raises(IntegrityError):
        Post.objects.create(account=account, external_media_id=post.external_media_id)


def test_same_media_id_allowed_on_a_different_account(post, other_user):
    other = ConnectedAccount.objects.create(
        owner=other_user, external_account_id="17841400000000003"
    )
    assert Post.objects.create(account=other, external_media_id=post.external_media_id).pk


def test_reel_detection(post):
    assert post.is_reel


def test_webhook_event_id_is_unique_per_platform(db):
    WebhookEvent.objects.create(external_event_id="comment:1", event_type="comment")
    assert WebhookEvent.objects.create(
        platform="whatsapp", external_event_id="comment:1", event_type="comment"
    ).pk
    with pytest.raises(IntegrityError):
        WebhookEvent.objects.create(external_event_id="comment:1", event_type="comment")


def test_old_settled_payloads_are_purged_but_pending_ones_kept(db):
    old = timezone.now() - timedelta(days=31)
    done = WebhookEvent.objects.create(
        external_event_id="a", payload={"x": 1}, processing_status="processed"
    )
    pending = WebhookEvent.objects.create(external_event_id="b", payload={"x": 1})
    fresh = WebhookEvent.objects.create(
        external_event_id="c", payload={"x": 1}, processing_status="processed"
    )
    WebhookEvent.objects.filter(pk__in=[done.pk, pending.pk]).update(received_at=old)

    assert WebhookEvent.purge_old_payloads() == 1
    for event in (done, pending, fresh):
        event.refresh_from_db()
    assert done.payload == {}
    assert pending.payload == {"x": 1}
    assert fresh.payload == {"x": 1}
