"""End-to-end: a webhook arrives, an automation matches, replies go out."""

import hashlib
import hmac
import json
from datetime import timedelta
from itertools import count

import pytest
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.automations.models import Automation, AutomationKeyword, AutomationRun, AutomationStep
from apps.automations.services import runner
from apps.billing.models import Plan, Subscription, UsageRecord
from apps.messaging.models import Comment, Contact, Conversation, Message

pytestmark = pytest.mark.django_db

APP_SECRET = "test-meta-app-secret"
CONTACT_ID = "5566778899"
StepType = AutomationStep.StepType
RunStatus = AutomationRun.Status

meta_app = override_settings(META_APP_SECRET=APP_SECRET, META_MOCK=False)


@pytest.fixture
def paid(user, pro_plan):
    """The owner has an active plan, so automations may run."""
    return Subscription.objects.create(
        user=user, plan=pro_plan, provider_subscription_id="sub_live", status="active",
        current_period_end=timezone.now() + timedelta(days=20),
    )


@pytest.fixture(autouse=True)
def run_on_commit(monkeypatch):
    """Tests run inside a transaction, so on_commit work would never fire."""
    monkeypatch.setattr(
        "django.db.transaction.on_commit", lambda func, using=None, robust=False: func()
    )


@pytest.fixture
def sent(monkeypatch):
    """Record what would be sent to Instagram; ``follows`` steers the follow check."""
    calls = {"public": [], "private": [], "dm": [], "postback": [], "follows": True}
    counter = count(1)

    def record(bucket, *args):
        calls[bucket].append(args)
        return f"{bucket}-{next(counter)}"  # Instagram gives every message its own id

    monkeypatch.setattr(
        runner.send, "reply_to_comment",
        lambda account, comment_id, text: record("public", comment_id, text),
    )
    monkeypatch.setattr(
        runner.send, "private_reply",
        lambda account, comment_id, text, buttons=None:
            record("private", comment_id, text, buttons),
    )
    monkeypatch.setattr(
        runner.send, "send_dm",
        lambda account, recipient, text, buttons=None: record("dm", recipient, text, buttons),
    )
    monkeypatch.setattr(
        runner.send, "send_postback_prompt",
        lambda account, recipient, text, title, payload:
            record("postback", recipient, text, title, payload),
    )
    monkeypatch.setattr(runner.send, "is_follower", lambda account, user_id: calls["follows"])
    return calls


def _automation(account, user, *, trigger=Automation.TriggerType.COMMENT, keyword="LINK",
                public=True, follow_gate=False, dm_text="Here's your link!", **fields):
    automation = Automation.objects.create(
        owner=user, account=account, name="Test automation",
        status=Automation.Status.ACTIVE, trigger_type=trigger, **fields,
    )
    if keyword:
        AutomationKeyword.objects.create(
            automation=automation, keyword=keyword,
            match_type=AutomationKeyword.MatchType.WHOLE_WORD,
        )
    order = 0
    if public and trigger == Automation.TriggerType.COMMENT:
        AutomationStep.objects.create(
            automation=automation, order=order, step_type=StepType.PUBLIC_REPLY,
            config={"variants": ["Sent! Check your DMs 💌"]},
        )
        order += 1
    if follow_gate:
        AutomationStep.objects.create(
            automation=automation, order=order, step_type=StepType.ASK_TO_FOLLOW,
            config={"prompt": "Follow me first!", "button": "I'm following",
                    "not_following_text": "Still can't see a follow.", "max_checks": 2},
        )
        order += 1
    AutomationStep.objects.create(
        automation=automation, order=order, step_type=StepType.SEND_DM,
        config={"text": dm_text, "buttons": [{"title": "Open", "url": "https://damdm.test/x"}]},
    )
    return automation


def _deliver(client, payload):
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(APP_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return client.post(
        reverse("instagram:webhooks"), body, content_type="application/json",
        HTTP_X_HUB_SIGNATURE_256=signature,
    )


def _comment(client, account, text="LINK", comment_id="c-1", media_id="media-1"):
    return _deliver(client, {"object": "instagram", "entry": [{
        "id": account.external_account_id, "time": int(timezone.now().timestamp() * 1000),
        "changes": [{"field": "comments", "value": {
            "id": comment_id, "text": text,
            "from": {"id": CONTACT_ID, "username": "fan"},
            "media": {"id": media_id},
        }}],
    }]})


def _dm(client, account, text="PRICE", mid="m-1", **message):
    return _deliver(client, {"object": "instagram", "entry": [{
        "id": account.external_account_id, "time": int(timezone.now().timestamp() * 1000),
        "messaging": [{
            "sender": {"id": CONTACT_ID}, "recipient": {"id": account.external_account_id},
            "message": {"mid": mid, "text": text, **message},
        }],
    }]})


# --- comment to DM ------------------------------------------------------------

@meta_app
def test_a_matching_comment_gets_a_public_reply_and_a_dm(client, account, user, paid, sent):
    automation = _automation(account, user)
    _comment(client, account)

    assert sent["public"] == [("c-1", "Sent! Check your DMs 💌")]
    comment_id, text, buttons = sent["private"][0]
    assert comment_id == "c-1"
    assert text == "Here's your link!"
    assert buttons == [{"title": "Open", "url": "https://damdm.test/x"}]
    assert not sent["dm"]  # the first message must be the private reply

    run = AutomationRun.objects.get()
    assert run.automation == automation and run.status == RunStatus.COMPLETED
    comment = Comment.objects.get(external_comment_id="c-1")
    assert comment.automation == automation
    assert comment.public_replied_at and comment.private_replied_at

    message = Message.objects.get(direction=Message.Direction.OUTBOUND, text="Here's your link!")
    assert message.sender_type == Message.SenderType.AUTOMATION
    assert message.status == Message.Status.SENT
    assert message.comment == comment
    usage = UsageRecord.objects.get(user=user)
    assert usage.messages_sent == 1 and usage.private_replies_sent == 1


@meta_app
def test_a_comment_without_the_keyword_is_left_alone(client, account, user, paid, sent):
    _automation(account, user)
    _comment(client, account, text="nice post")
    assert not sent["public"] and not sent["private"]
    assert not AutomationRun.objects.exists()


@meta_app
def test_nothing_runs_without_an_active_plan(client, account, user, sent):
    _automation(account, user)
    _comment(client, account)
    assert not sent["private"]
    assert not AutomationRun.objects.exists()


@meta_app
def test_paused_automations_do_not_reply(client, account, user, paid, sent):
    automation = _automation(account, user)
    automation.pause()
    _comment(client, account)
    assert not sent["private"]


@meta_app
def test_the_same_person_only_triggers_once(client, account, user, paid, sent):
    _automation(account, user, once_per_contact=True)
    _comment(client, account, comment_id="c-1")
    _comment(client, account, comment_id="c-2")
    assert len(sent["private"]) == 1
    assert AutomationRun.objects.count() == 1


@meta_app
def test_a_repeated_webhook_never_sends_twice(client, account, user, paid, sent):
    _automation(account, user)
    _comment(client, account)
    _comment(client, account)  # Meta retrying the same delivery
    assert len(sent["public"]) == 1 and len(sent["private"]) == 1


@meta_app
def test_our_own_comment_never_triggers_an_automation(client, account, user, paid, sent):
    _automation(account, user)
    _deliver(client, {"object": "instagram", "entry": [{
        "id": account.external_account_id, "time": int(timezone.now().timestamp() * 1000),
        "changes": [{"field": "comments", "value": {
            "id": "own-1", "text": "LINK",
            "from": {"id": account.external_account_id, "username": account.username},
            "media": {"id": "media-1"},
        }}],
    }]})
    assert not sent["private"]


@meta_app
def test_opted_out_people_are_never_messaged(client, account, user, paid, sent):
    _automation(account, user)
    Contact.objects.create(
        account=account, external_user_id=CONTACT_ID, opted_out_at=timezone.now()
    )
    _comment(client, account)
    assert not sent["private"]
    assert not AutomationRun.objects.exists()


@meta_app
def test_only_chosen_posts_are_covered(client, account, user, paid, sent, post):
    automation = _automation(account, user, post_scope=Automation.PostScope.SPECIFIC_POSTS)
    automation.posts.set([post])
    _comment(client, account, comment_id="c-other", media_id="not-this-post")
    assert not sent["private"]
    _comment(client, account, comment_id="c-chosen", media_id=post.external_media_id)
    assert len(sent["private"]) == 1


# --- follow to unlock ---------------------------------------------------------

@meta_app
def test_non_followers_are_asked_to_follow_first(client, account, user, paid, sent):
    _automation(account, user, follow_gate=True)
    sent["follows"] = False
    _comment(client, account)

    assert sent["postback"], "should ask them to follow"
    recipient, text, title, payload = sent["postback"][0]
    assert recipient == {"comment_id": "c-1"}
    assert text == "Follow me first!" and title == "I'm following"
    run = AutomationRun.objects.get()
    assert run.status == RunStatus.WAITING_FOLLOW
    assert payload == f"follow_check:{run.pk}"
    assert not sent["private"], "the link must wait until they follow"


@meta_app
def test_the_link_unlocks_once_they_follow(client, account, user, paid, sent):
    _automation(account, user, follow_gate=True)
    sent["follows"] = False
    _comment(client, account)
    run = AutomationRun.objects.get()

    sent["follows"] = True
    _deliver(client, {"object": "instagram", "entry": [{
        "id": account.external_account_id, "time": int(timezone.now().timestamp() * 1000),
        "messaging": [{
            "sender": {"id": CONTACT_ID}, "recipient": {"id": account.external_account_id},
            "postback": {"mid": "pb-1", "title": "I'm following",
                         "payload": f"follow_check:{run.pk}"},
        }],
    }]})

    run.refresh_from_db()
    assert run.status == RunStatus.COMPLETED
    assert sent["dm"], "the link goes by DM once the comment reply is used up"
    recipient, text, buttons = sent["dm"][0]
    assert recipient == CONTACT_ID and text == "Here's your link!"


@meta_app
def test_people_who_still_do_not_follow_are_asked_again_then_dropped(
    client, account, user, paid, sent
):
    _automation(account, user, follow_gate=True)
    sent["follows"] = False
    _comment(client, account)
    run = AutomationRun.objects.get()

    for _ in range(2):
        _deliver(client, {"object": "instagram", "entry": [{
            "id": account.external_account_id, "time": int(timezone.now().timestamp() * 1000),
            "messaging": [{
                "sender": {"id": CONTACT_ID}, "recipient": {"id": account.external_account_id},
                "postback": {"mid": f"pb-{_}", "title": "I'm following",
                             "payload": f"follow_check:{run.pk}"},
            }],
        }]})
    run.refresh_from_db()
    assert run.status == RunStatus.SKIPPED
    assert "Not following" in run.last_error
    assert not sent["dm"]


@meta_app
def test_followers_only_automations_skip_everyone_else(client, account, user, paid, sent):
    _automation(account, user, audience=Automation.Audience.FOLLOWERS)
    sent["follows"] = False
    _comment(client, account)
    assert not sent["private"] and not AutomationRun.objects.exists()


@meta_app
def test_an_unknown_follow_state_never_blocks_the_link(
    client, account, user, paid, sent, monkeypatch
):
    _automation(account, user, follow_gate=True)
    monkeypatch.setattr(runner.send, "is_follower", lambda account, user_id: None)
    _comment(client, account)
    assert sent["private"], "Instagram not answering must not cost the person their link"


# --- DMs, stories and the messaging window ------------------------------------

@meta_app
def test_a_dm_keyword_is_answered(client, account, user, paid, sent):
    _automation(account, user, trigger=Automation.TriggerType.DM_KEYWORD, keyword="PRICE",
                public=False, dm_text="Here are our prices")
    _dm(client, account, text="hi, PRICE?")
    recipient, text, _ = sent["dm"][0]
    assert recipient == CONTACT_ID and text == "Here are our prices"
    assert AutomationRun.objects.get().status == RunStatus.COMPLETED


@meta_app
def test_story_replies_and_mentions_use_their_own_automations(client, account, user, paid, sent):
    _automation(account, user, trigger=Automation.TriggerType.STORY_REPLY, keyword="YES",
                public=False, dm_text="Thanks for replying!")
    _dm(client, account, text="YES", mid="m-story", reply_to={"story": {"id": "s-1"}})
    assert sent["dm"][0][1] == "Thanks for replying!"


@meta_app
def test_a_second_dm_needs_an_open_messaging_window(client, account, user, paid, sent):
    """
    Someone who only commented can be sent the one private reply, and nothing
    more until they answer: Instagram's window has never opened for them.
    """
    automation = _automation(account, user, public=False)
    AutomationStep.objects.create(
        automation=automation, order=9, step_type=StepType.SEND_DM,
        config={"text": "And one more thing…"},
    )
    _comment(client, account)

    assert len(sent["private"]) == 1, "the private reply is allowed"
    assert not sent["dm"], "the follow-up DM is not"
    run = AutomationRun.objects.get()
    assert run.status == RunStatus.FAILED
    assert "window" in run.last_error


@meta_app
def test_a_failed_send_is_recorded_on_the_message(client, account, user, paid, sent, monkeypatch):
    from apps.instagram.services.graph import GraphAPIError

    _automation(account, user, public=False)

    def explode(*args, **kwargs):
        raise GraphAPIError("Message failed to send", code=10)

    monkeypatch.setattr(runner.send, "private_reply", explode)
    _comment(client, account)

    run = AutomationRun.objects.get()
    assert run.status == RunStatus.FAILED
    message = Message.objects.get(direction=Message.Direction.OUTBOUND)
    assert message.status == Message.Status.PERMANENT_FAILURE
    assert "failed to send" in message.last_error


# --- other steps ---------------------------------------------------------------

@meta_app
def test_a_handoff_step_hands_the_chat_to_the_owner(client, account, user, paid, sent):
    automation = _automation(account, user, public=False)
    AutomationStep.objects.create(
        automation=automation, order=5, step_type=StepType.HANDOFF, config={}
    )
    _comment(client, account)
    assert Conversation.objects.get().mode == Conversation.Mode.HUMAN
    assert AutomationRun.objects.get().status == RunStatus.COMPLETED


@meta_app
def test_waiting_runs_expire_when_the_window_closes(client, account, user, paid, sent):
    from apps.automations.tasks import expire_waiting_runs

    _automation(account, user, follow_gate=True)
    sent["follows"] = False
    _comment(client, account)
    AutomationRun.objects.update(expires_at=timezone.now() - timedelta(minutes=1))
    expire_waiting_runs()
    assert AutomationRun.objects.get().status == RunStatus.EXPIRED


@meta_app
def test_the_highest_priority_automation_wins(client, account, user, paid, sent):
    _automation(account, user, dm_text="low priority", priority=0, public=False)
    _automation(account, user, dm_text="high priority", priority=5, public=False)
    _comment(client, account)
    assert sent["private"][0][1] == "high priority"


@meta_app
def test_placeholders_are_filled_in(client, account, user, paid, sent):
    _automation(account, user, public=False, dm_text="Hi {first_name}, here you go!")
    _comment(client, account)
    assert sent["private"][0][1] == "Hi fan, here you go!"


def test_plans_fixture_is_not_needed_for_matching(account, user, sent):
    """Matching is pure: no plan, no database writes."""
    automation = _automation(account, user)
    assert runner.automation_matches(automation, "please send LINK")
    assert not runner.automation_matches(automation, "no keyword here")
    assert not Plan.objects.filter(slug="nonexistent").exists()
