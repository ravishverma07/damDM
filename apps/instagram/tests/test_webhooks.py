import hashlib
import hmac
import json
from datetime import timedelta

import pytest
from celery.exceptions import Retry
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.instagram.models import Post, WebhookEvent
from apps.instagram.tasks import process_pending_webhooks, process_webhook_event
from apps.messaging.models import Comment, Contact, Conversation, Message

pytestmark = pytest.mark.django_db

APP_SECRET = "test-meta-app-secret"
VERIFY_TOKEN = "test-verify-token"
IG_ID = "17841400000000001"  # conftest's account
CONTACT_ID = "9988776655"
Status = WebhookEvent.Status
#: Webhook timestamps are milliseconds; recent ones keep the messaging window open.
NOW_MS = int(timezone.now().timestamp() * 1000)

meta_app = override_settings(
    META_APP_SECRET=APP_SECRET, META_WEBHOOK_VERIFY_TOKEN=VERIFY_TOKEN, META_MOCK=False
)


def _post(client, payload, *, secret=APP_SECRET, signature=None):
    body = json.dumps(payload).encode()
    if signature is None:
        signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post(
        reverse("instagram:webhooks"), body, content_type="application/json",
        HTTP_X_HUB_SIGNATURE_256=signature,
    )


def _comment_payload(comment_id="comment-1", text="LINK please", media_id="media-1", **value):
    return {"object": "instagram", "entry": [{
        "id": IG_ID, "time": NOW_MS,
        "changes": [{"field": "comments", "value": {
            "id": comment_id, "text": text,
            "from": {"id": CONTACT_ID, "username": "curious_fan"},
            "media": {"id": media_id, "media_product_type": "REELS"},
            **value,
        }}],
    }]}


def _message_payload(mid="mid-1", text="PRICE?", **message):
    return {"object": "instagram", "entry": [{
        "id": IG_ID, "time": NOW_MS,
        "messaging": [{
            "sender": {"id": CONTACT_ID}, "recipient": {"id": IG_ID},
            "timestamp": NOW_MS,
            "message": {"mid": mid, "text": text, **message},
        }],
    }]}


# --- the endpoint -------------------------------------------------------------

@meta_app
def test_meta_can_verify_the_subscription(client):
    response = client.get(reverse("instagram:webhooks"), {
        "hub.mode": "subscribe", "hub.verify_token": VERIFY_TOKEN, "hub.challenge": "1158201444",
    })
    assert response.status_code == 200
    assert response.content == b"1158201444"


@meta_app
@pytest.mark.parametrize(
    "params",
    [
        {"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "x"},
        {"hub.mode": "unsubscribe", "hub.verify_token": VERIFY_TOKEN, "hub.challenge": "x"},
        {"hub.mode": "subscribe", "hub.verify_token": VERIFY_TOKEN},
        {},
    ],
)
def test_verification_needs_the_right_token(client, params):
    assert client.get(reverse("instagram:webhooks"), params).status_code == 403


@meta_app
def test_events_are_stored_and_answered_immediately(client, account):
    response = _post(client, _comment_payload())
    assert response.status_code == 200
    event = WebhookEvent.objects.get()
    assert event.event_type == WebhookEvent.EventType.COMMENT
    assert event.external_account_id == IG_ID
    assert event.external_event_id == "comment:comment-1"


@meta_app
@pytest.mark.parametrize(
    "signature",
    ["", "sha256=deadbeef", "sha1=deadbeef", "deadbeef"],
)
def test_forged_signatures_are_refused(client, signature):
    assert _post(client, _comment_payload(), signature=signature).status_code == 403
    assert not WebhookEvent.objects.exists()


@meta_app
def test_a_repeated_delivery_is_stored_once(client, account):
    _post(client, _comment_payload())
    _post(client, _comment_payload())
    assert WebhookEvent.objects.count() == 1
    assert Comment.objects.count() == 1


def test_webhooks_are_refused_when_the_app_secret_is_missing(client):
    assert _post(client, _comment_payload(), secret="").status_code == 403


@meta_app
def test_malformed_bodies_are_rejected(client):
    body = b"not json"
    signature = "sha256=" + hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    response = client.post(
        reverse("instagram:webhooks"), body, content_type="application/json",
        HTTP_X_HUB_SIGNATURE_256=signature,
    )
    assert response.status_code == 400


# --- what gets recorded -------------------------------------------------------

@meta_app
def test_a_comment_becomes_a_contact_and_a_comment(client, account):
    post = Post.objects.create(account=account, external_media_id="media-1")
    _post(client, _comment_payload())

    contact = Contact.objects.get()
    assert contact.account == account
    assert contact.external_user_id == CONTACT_ID
    assert contact.username == "curious_fan"
    assert contact.source == Contact.Source.COMMENT
    assert contact.comments_count == 1

    comment = Comment.objects.get()
    assert comment.text == "LINK please"
    assert comment.post == post
    assert comment.contact == contact
    assert not comment.is_from_owner
    assert WebhookEvent.objects.get().processing_status == Status.PROCESSED


@meta_app
def test_the_owners_own_comment_is_recorded_without_a_contact(client, account):
    payload = _comment_payload(comment_id="own-1")
    payload["entry"][0]["changes"][0]["value"]["from"] = {"id": IG_ID, "username": "mock_creator"}
    _post(client, payload)
    comment = Comment.objects.get()
    assert comment.is_from_owner and comment.contact is None
    assert not Contact.objects.exists()


@meta_app
def test_replies_are_linked_to_the_comment_they_answer(client, account):
    _post(client, _comment_payload(comment_id="parent-1"))
    _post(client, _comment_payload(comment_id="reply-1", text="thanks!", parent_id="parent-1"))
    reply = Comment.objects.get(external_comment_id="reply-1")
    assert reply.parent.external_comment_id == "parent-1"


@meta_app
def test_a_dm_becomes_a_conversation_and_message(client, account):
    _post(client, _message_payload())

    contact = Contact.objects.get()
    message = Message.objects.get()
    assert message.direction == Message.Direction.INBOUND
    assert message.sender_type == Message.SenderType.CONTACT
    assert message.message_type == Message.MessageType.TEXT
    assert message.text == "PRICE?"
    assert message.status == Message.Status.RECEIVED
    assert contact.last_inbound_at is not None and contact.messaging_window_open
    assert contact.messages_count == 1

    conversation = Conversation.objects.get()
    assert conversation.unread_count == 1
    assert conversation.last_message_text == "PRICE?"


@meta_app
@pytest.mark.parametrize(
    ("extra", "expected", "source"),
    [
        ({"reply_to": {"story": {"id": "story-1"}}}, Message.MessageType.STORY_REPLY,
         Contact.Source.STORY_REPLY),
        ({"attachments": [{"type": "story_mention", "payload": {"url": "https://x/s.jpg"}}]},
         Message.MessageType.STORY_MENTION, Contact.Source.STORY_MENTION),
        ({"attachments": [{"type": "image", "payload": {"url": "https://x/i.jpg"}}]},
         Message.MessageType.IMAGE, Contact.Source.DM),
    ],
)
def test_story_replies_mentions_and_media_are_labelled(client, account, extra, expected, source):
    _post(client, _message_payload(**extra))
    assert Message.objects.get().message_type == expected
    assert Contact.objects.get().source == source


@meta_app
def test_our_own_dms_are_recorded_as_outbound(client, account):
    payload = _message_payload(mid="echo-1", is_echo=True)
    payload["entry"][0]["messaging"][0]["sender"] = {"id": IG_ID}
    payload["entry"][0]["messaging"][0]["recipient"] = {"id": CONTACT_ID}
    _post(client, payload)
    message = Message.objects.get()
    assert message.direction == Message.Direction.OUTBOUND
    assert message.sender_type == Message.SenderType.ECHO
    assert message.status == Message.Status.SENT
    assert Conversation.objects.get().unread_count == 0
    assert Contact.objects.get().last_inbound_at is None  # doesn't open the messaging window


@meta_app
def test_button_taps_are_recorded(client, account):
    _post(client, {"object": "instagram", "entry": [{
        "id": IG_ID, "time": NOW_MS,
        "messaging": [{
            "sender": {"id": CONTACT_ID}, "recipient": {"id": IG_ID},
            "postback": {"mid": "pb-1", "title": "I'm following", "payload": "follow_check:12"},
        }],
    }]})
    message = Message.objects.get()
    assert message.message_type == Message.MessageType.POSTBACK
    assert message.postback_payload == "follow_check:12"
    assert Contact.objects.get().messaging_window_open


@meta_app
def test_events_for_unknown_accounts_are_ignored(client, account):
    payload = _comment_payload()
    payload["entry"][0]["id"] = "17841400000000999"
    _post(client, payload)
    assert WebhookEvent.objects.get().processing_status == Status.IGNORED
    assert not Comment.objects.exists()


@meta_app
def test_unsupported_events_are_stored_but_ignored(client, account):
    _post(client, {"object": "instagram", "entry": [{
        "id": IG_ID, "time": NOW_MS,
        "changes": [{"field": "story_insights", "value": {"id": "x1", "impressions": 5}}],
    }]})
    event = WebhookEvent.objects.get()
    assert event.event_type == WebhookEvent.EventType.UNKNOWN
    assert event.processing_status == Status.IGNORED


@meta_app
def test_several_events_in_one_delivery_are_all_stored(client, account):
    _post(client, {"object": "instagram", "entry": [
        {"id": IG_ID, "time": NOW_MS, "changes": [
            {"field": "comments", "value": {
                "id": "c1", "text": "LINK", "from": {"id": CONTACT_ID, "username": "a"},
                "media": {"id": "m1"}}},
        ]},
        {"id": IG_ID, "time": NOW_MS, "messaging": [
            {"sender": {"id": CONTACT_ID}, "recipient": {"id": IG_ID},
             "message": {"mid": "m-9", "text": "hi"}},
        ]},
    ]})
    assert WebhookEvent.objects.count() == 2
    assert Comment.objects.count() == 1 and Message.objects.count() == 1


# --- retries and the safety net -----------------------------------------------

@meta_app
def test_a_failed_event_is_retried_later(client, account, monkeypatch):
    import apps.instagram.tasks as tasks

    monkeypatch.setattr(tasks, "ingest_event", lambda event: 1 / 0)
    with pytest.raises(Retry):  # eager mode raises the retry itself
        _post(client, _comment_payload())
    event = WebhookEvent.objects.get()
    assert event.processing_status == Status.FAILED
    assert "division" in event.error_message

    monkeypatch.undo()
    WebhookEvent.objects.update(received_at=timezone.now() - timedelta(minutes=10))
    process_pending_webhooks()
    event.refresh_from_db()
    assert event.processing_status == Status.PROCESSED
    assert Comment.objects.count() == 1


@meta_app
def test_processing_twice_changes_nothing(client, account):
    _post(client, _comment_payload())
    event = WebhookEvent.objects.get()
    WebhookEvent.objects.update(processing_status=Status.PENDING)
    process_webhook_event(event.pk)
    assert Comment.objects.count() == 1


# --- subscribing accounts so Instagram sends events ---------------------------

@meta_app
def test_connecting_subscribes_the_account_to_webhooks(account, monkeypatch):
    from apps.instagram.services import subscriptions

    calls = []
    monkeypatch.setattr(
        subscriptions, "InstagramGraph",
        lambda token, **kw: _FakeGraph(calls, {"success": True}),
    )
    assert subscriptions.subscribe(account)
    account.refresh_from_db()
    assert account.webhooks_subscribed
    path, data = calls[0]
    assert path == "me/subscribed_apps"
    assert "comments" in data["subscribed_fields"]
    assert "messages" in data["subscribed_fields"]


@meta_app
def test_a_failed_subscription_is_retried_later(account, monkeypatch):
    from apps.instagram.services import graph as graph_module
    from apps.instagram.services import subscriptions

    def explode(token, **kwargs):
        raise graph_module.GraphAPIError("nope")

    monkeypatch.setattr(subscriptions, "InstagramGraph", explode)
    assert not subscriptions.subscribe(account)
    account.refresh_from_db()
    assert not account.webhooks_subscribed

    calls = []
    monkeypatch.setattr(
        subscriptions, "InstagramGraph",
        lambda token, **kw: _FakeGraph(calls, {"success": True}),
    )
    from apps.instagram.tasks import subscribe_pending_accounts

    subscribe_pending_accounts()
    account.refresh_from_db()
    assert account.webhooks_subscribed


class _FakeGraph:
    """Stands in for InstagramGraph, recording what was posted."""

    def __init__(self, calls, response):
        self.calls, self.response = calls, response

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def post(self, path, data=None):
        self.calls.append((path, data or {}))
        return self.response


@meta_app
def test_webhooks_addressed_to_the_app_scoped_id_are_matched(client, account):
    """Instagram names the account by its app-scoped id in webhooks."""
    account.app_scoped_user_id = "28335387666131630"
    account.save(update_fields=["app_scoped_user_id"])

    payload = _comment_payload(comment_id="scoped-1")
    payload["entry"][0]["id"] = account.app_scoped_user_id
    _post(client, payload)

    event = WebhookEvent.objects.get()
    assert event.processing_status == Status.PROCESSED
    assert event.account == account
    assert Comment.objects.get().external_comment_id == "scoped-1"


@meta_app
def test_our_own_comment_is_recognised_by_either_id(client, account):
    account.app_scoped_user_id = "28335387666131630"
    account.save(update_fields=["app_scoped_user_id"])

    payload = _comment_payload(comment_id="own-scoped")
    payload["entry"][0]["id"] = account.app_scoped_user_id
    payload["entry"][0]["changes"][0]["value"]["from"] = {
        "id": account.app_scoped_user_id, "username": account.username,
    }
    _post(client, payload)
    assert Comment.objects.get().is_from_owner
    assert not Contact.objects.exists()
