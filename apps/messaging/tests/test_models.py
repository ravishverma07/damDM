from datetime import timedelta

import pytest
from django.db import IntegrityError
from django.utils import timezone

from apps.automations.models import AutomationRun
from apps.messaging.models import (
    Comment,
    Contact,
    ContactTag,
    Conversation,
    FollowCheck,
    Message,
    Tag,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def comment(account, post, contact, automation):
    return Comment.objects.create(
        account=account,
        post=post,
        external_media_id=post.external_media_id,
        contact=contact,
        external_comment_id="17900000000000009",
        text="LINK",
        automation=automation,
    )


@pytest.fixture
def conversation(account, contact):
    return Conversation.objects.create(account=account, contact=contact)


def _outbound(conversation, **kwargs):
    return Message.objects.create(
        account=conversation.account,
        conversation=conversation,
        contact=conversation.contact,
        direction=Message.Direction.OUTBOUND,
        sender_type=Message.SenderType.AUTOMATION,
        text="Here is your link!",
        **kwargs,
    )


# --- contacts --------------------------------------------------------

def test_contact_is_unique_per_account(contact, account):
    with pytest.raises(IntegrityError):
        Contact.objects.create(account=account, external_user_id=contact.external_user_id)


def test_opted_out_or_blocked_contacts_cannot_be_automated(contact):
    assert contact.can_be_automated
    contact.opted_out_at = timezone.now()
    assert not contact.can_be_automated
    contact.opted_out_at = None
    contact.is_blocked = True
    assert not contact.can_be_automated


def test_messaging_window_opens_on_inbound_and_shuts_after_24h(contact):
    assert not contact.messaging_window_open
    contact.last_inbound_at = timezone.now() - timedelta(hours=1)
    assert contact.messaging_window_open
    contact.last_inbound_at = timezone.now() - timedelta(hours=25)
    assert not contact.messaging_window_open


def test_tags_are_unique_per_contact(contact, account):
    tag = Tag.objects.create(account=account, name="lead")
    contact.tags.add(tag)
    with pytest.raises(IntegrityError):
        ContactTag.objects.create(contact=contact, tag=tag)


def test_deleting_a_contact_removes_their_data(contact, comment, conversation):
    _outbound(conversation)
    FollowCheck.objects.create(contact=contact, is_following=True)
    contact.delete()
    assert not Comment.objects.exists()
    assert not Conversation.objects.exists()
    assert not Message.objects.exists()
    assert not FollowCheck.objects.exists()


# --- comments --------------------------------------------------------

def test_comment_id_is_unique_per_account(comment, account):
    with pytest.raises(IntegrityError):
        Comment.objects.create(
            account=account,
            external_media_id=comment.external_media_id,
            external_comment_id=comment.external_comment_id,
        )


def test_only_one_automated_public_reply_per_comment(comment, account):
    def reply(external_id):
        return Comment.objects.create(
            account=account, external_media_id=comment.external_media_id,
            external_comment_id=external_id, parent=comment,
            is_from_owner=True, is_automated_reply=True, text="Sent! Check your DM",
        )

    reply("reply-1")
    with pytest.raises(IntegrityError):
        reply("reply-2")


def test_owner_may_reply_manually_as_often_as_they_like(comment, account):
    for n in range(2):
        Comment.objects.create(
            account=account, external_media_id=comment.external_media_id,
            external_comment_id=f"manual-{n}", parent=comment, is_from_owner=True,
        )
    assert comment.replies.count() == 2


# --- messages --------------------------------------------------------

def test_one_conversation_per_contact(conversation, account, contact):
    with pytest.raises(IntegrityError):
        Conversation.objects.create(account=account, contact=contact)


def test_only_one_private_reply_per_comment(conversation, comment):
    _outbound(conversation, comment=comment)
    with pytest.raises(IntegrityError):
        _outbound(conversation, comment=comment)


def test_follow_up_messages_are_not_capped(conversation):
    _outbound(conversation)
    assert _outbound(conversation).pk


def test_external_message_id_is_unique_once_set(conversation):
    _outbound(conversation, external_message_id="mid.1")
    _outbound(conversation)  # blank ids do not collide
    with pytest.raises(IntegrityError):
        _outbound(conversation, external_message_id="mid.1")


def test_mark_sent_and_failed(conversation):
    message = _outbound(conversation)
    message.mark_sent("mid.123")
    assert message.status == Message.Status.SENT
    assert message.sent_at is not None

    message.mark_failed("rate limited")
    assert message.status == Message.Status.FAILED
    message.mark_failed("no permission", permanent=True)
    assert message.status == Message.Status.PERMANENT_FAILURE


def test_follow_check_records_all_three_outcomes(contact, automation):
    run = AutomationRun.objects.create(automation=automation, contact=contact)
    for value in (True, False, None):
        check = FollowCheck.objects.create(contact=contact, automation_run=run, is_following=value)
        assert str(check)
    assert run.follow_checks.count() == 3
