"""
Turn stored webhook events into the long-term record: contacts, comments and
messages.

Nothing here calls Instagram or sends anything; it only records what happened,
so a repeated delivery changes nothing. Replying is the automation runner's job.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from django.db import transaction
from django.utils import timezone

from apps.core.queue import enqueue
from apps.messaging.models import Comment, Contact, Conversation, Message

from ..models import ConnectedAccount, Post, WebhookEvent
from .lifecycle import accounts_for_platform_user

logger = logging.getLogger("damdm.instagram")

EventType = WebhookEvent.EventType
Status = WebhookEvent.Status

#: Attachment types Instagram sends that are really story interactions.
STORY_ATTACHMENTS = {"story_mention": Message.MessageType.STORY_MENTION}
ATTACHMENT_TYPES = {
    "image": Message.MessageType.IMAGE,
    "video": Message.MessageType.VIDEO,
    "audio": Message.MessageType.AUDIO,
    "file": Message.MessageType.FILE,
    "share": Message.MessageType.SHARE,
    "story_mention": Message.MessageType.STORY_MENTION,
}


def process(event: WebhookEvent) -> str:
    """Record one event. Returns the processing status to store."""
    # Webhooks name the account by its Instagram-scoped id, which may be either
    # the one we stored at connect time or its app-scoped twin.
    account = event.account or (
        accounts_for_platform_user(event.external_account_id)
        .filter(status__in=ConnectedAccount.CLAIMED_STATUSES)
        .select_related("owner")
        .first()
    )
    if account is None:
        return Status.IGNORED
    event.account = account

    handler = {
        EventType.COMMENT: _comment,
        EventType.MENTION: _comment,
        EventType.MESSAGE: _message,
        EventType.MESSAGE_ECHO: _message,
        EventType.POSTBACK: _postback,
    }.get(event.event_type)
    if handler is None:
        return Status.IGNORED
    return handler(account, event.payload or {})


def _is_the_account(account: ConnectedAccount, user_id: str) -> bool:
    """Instagram names an account by either of its two ids, depending on the event."""
    return bool(user_id) and user_id in {
        account.external_account_id, account.app_scoped_user_id
    }


def _moment(value) -> datetime:
    """Instagram's millisecond timestamps as a datetime; now if missing."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return timezone.now()
    if number > 1e11:  # milliseconds
        number /= 1000
    return datetime.fromtimestamp(number, tz=UTC)


def _contact(account: ConnectedAccount, *, user_id: str, source: str, moment, **profile) -> Contact:
    """Find or create the person, keeping their profile and activity current."""
    contact, _ = Contact.objects.get_or_create(
        account=account,
        external_user_id=user_id,
        defaults={"source": source, "first_seen_at": moment, "last_interaction_at": moment,
                  **profile},
    )
    changed = []
    for name, value in profile.items():
        if value and getattr(contact, name) != value:
            setattr(contact, name, value)
            changed.append(name)
    if contact.last_interaction_at < moment:
        contact.last_interaction_at = moment
        changed.append("last_interaction_at")
    if changed:
        contact.save(update_fields=[*changed, "updated_at"])
    return contact


@transaction.atomic
def _comment(account: ConnectedAccount, payload: dict) -> str:
    comment_id = str(payload.get("id") or "")
    author = payload.get("from") if isinstance(payload.get("from"), dict) else {}
    media = payload.get("media") if isinstance(payload.get("media"), dict) else {}
    author_id = str(author.get("id") or "")
    media_id = str(media.get("id") or payload.get("media_id") or "")
    if not comment_id or not media_id:
        return Status.IGNORED
    if Comment.objects.filter(account=account, external_comment_id=comment_id).exists():
        return Status.PROCESSED  # a repeated delivery

    moment = _moment(payload.get("_time"))
    from_owner = _is_the_account(account, author_id)
    contact = None
    if not from_owner and author_id:
        contact = _contact(
            account, user_id=author_id, source=Contact.Source.COMMENT, moment=moment,
            username=str(author.get("username") or ""),
        )

    parent_id = str(payload.get("parent_id") or "")
    comment = Comment.objects.create(
        account=account,
        post=Post.objects.filter(account=account, external_media_id=media_id).first(),
        external_media_id=media_id,
        contact=contact,
        external_comment_id=comment_id,
        parent=Comment.objects.filter(
            account=account, external_comment_id=parent_id
        ).first() if parent_id else None,
        text=str(payload.get("text") or ""),
        is_from_owner=from_owner,
        received_at=moment,
    )
    if contact is not None:
        Contact.objects.filter(pk=contact.pk).update(
            comments_count=contact.comments_count + 1, updated_at=timezone.now()
        )
        _run_automations("run_for_comment", comment.pk)
    return Status.PROCESSED


@transaction.atomic
def _message(account: ConnectedAccount, payload: dict) -> str:
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    mid = str(message.get("mid") or "")
    sender = str((payload.get("sender") or {}).get("id") or "")
    recipient = str((payload.get("recipient") or {}).get("id") or "")
    if not mid:
        return Status.IGNORED
    if Message.objects.filter(account=account, external_message_id=mid).exists():
        return Status.PROCESSED

    inbound = not _is_the_account(account, sender) and not message.get("is_echo")
    contact_id = sender if inbound else recipient
    if not contact_id or _is_the_account(account, contact_id):
        return Status.IGNORED

    moment = _moment(payload.get("_time") or payload.get("timestamp"))
    message_type, attachments = _message_type(message)
    source = (
        Contact.Source.STORY_MENTION if message_type == Message.MessageType.STORY_MENTION
        else Contact.Source.STORY_REPLY if message_type == Message.MessageType.STORY_REPLY
        else Contact.Source.DM
    )
    contact = _contact(account, user_id=contact_id, source=source, moment=moment)
    conversation, _ = Conversation.objects.get_or_create(account=account, contact=contact)

    text = str(message.get("text") or "")
    row = Message.objects.create(
        account=account,
        conversation=conversation,
        contact=contact,
        direction=Message.Direction.INBOUND if inbound else Message.Direction.OUTBOUND,
        sender_type=Message.SenderType.CONTACT if inbound else Message.SenderType.ECHO,
        message_type=message_type,
        external_message_id=mid,
        text=text,
        attachments=attachments,
        status=Message.Status.RECEIVED if inbound else Message.Status.SENT,
        sent_at=None if inbound else moment,
    )

    fields = {"last_message_at": moment, "last_message_text": text[:200]}
    if inbound:
        fields["unread_count"] = conversation.unread_count + 1
    Conversation.objects.filter(pk=conversation.pk).update(**fields, updated_at=timezone.now())
    contact_fields = {"messages_count": contact.messages_count + 1}
    if inbound:
        contact_fields["last_inbound_at"] = moment
    Contact.objects.filter(pk=contact.pk).update(**contact_fields, updated_at=timezone.now())
    if inbound:
        _run_automations("run_for_message", row.pk)
    return Status.PROCESSED


def _message_type(message: dict) -> tuple[str, list]:
    """The kind of DM this is, plus its attachments in our own shape."""
    reply_to = message.get("reply_to") if isinstance(message.get("reply_to"), dict) else {}
    attachments = [a for a in (message.get("attachments") or []) if isinstance(a, dict)]
    kinds = [str(a.get("type") or "") for a in attachments]
    stored = [
        {"type": kind, "url": str((a.get("payload") or {}).get("url") or "")}
        for a, kind in zip(attachments, kinds, strict=True)
    ]

    if "story_mention" in kinds:
        return Message.MessageType.STORY_MENTION, stored
    if isinstance(reply_to.get("story"), dict):
        return Message.MessageType.STORY_REPLY, stored
    for kind in kinds:
        if kind in ATTACHMENT_TYPES:
            return ATTACHMENT_TYPES[kind], stored
    return Message.MessageType.TEXT, stored


@transaction.atomic
def _postback(account: ConnectedAccount, payload: dict) -> str:
    """A tap on one of our buttons, e.g. "I'm following"."""
    postback = payload.get("postback") if isinstance(payload.get("postback"), dict) else {}
    sender = str((payload.get("sender") or {}).get("id") or "")
    mid = str(postback.get("mid") or "")
    if not sender or _is_the_account(account, sender):
        return Status.IGNORED
    if mid and Message.objects.filter(account=account, external_message_id=mid).exists():
        return Status.PROCESSED

    moment = _moment(payload.get("_time") or payload.get("timestamp"))
    contact = _contact(account, user_id=sender, source=Contact.Source.DM, moment=moment)
    conversation, _ = Conversation.objects.get_or_create(account=account, contact=contact)
    row = Message.objects.create(
        account=account,
        conversation=conversation,
        contact=contact,
        direction=Message.Direction.INBOUND,
        sender_type=Message.SenderType.CONTACT,
        message_type=Message.MessageType.POSTBACK,
        external_message_id=mid,
        text=str(postback.get("title") or ""),
        postback_payload=str(postback.get("payload") or "")[:1000],
        status=Message.Status.RECEIVED,
    )
    Contact.objects.filter(pk=contact.pk).update(
        last_inbound_at=moment, messages_count=contact.messages_count + 1,
        updated_at=timezone.now(),
    )
    Conversation.objects.filter(pk=conversation.pk).update(
        last_message_at=moment, unread_count=conversation.unread_count + 1,
        updated_at=timezone.now(),
    )
    _run_automations("run_for_message", row.pk)
    return Status.PROCESSED


def _run_automations(task_name: str, object_id: int) -> None:
    """Hand off to the automation runner once this event is safely stored."""
    from apps.automations import tasks

    task = getattr(tasks, task_name)
    transaction.on_commit(lambda: enqueue(task, object_id))
