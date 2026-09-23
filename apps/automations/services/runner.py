"""
Running automations: decide what matched, then carry out its steps.

Everything here is driven by webhooks already stored by ``apps.instagram``,
so each entry point must be safe to call twice for the same event.

Instagram's rules shape the flow:

* one private reply per comment, enforced by a database constraint;
* DMs only inside the 24-hour window that the contact's last message opened;
* a comment's private reply is the reliable way to start a conversation.
"""

from __future__ import annotations

import logging
import random
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.billing.models import UsageRecord
from apps.billing.services.access import has_access
from apps.instagram.services import send
from apps.instagram.services.graph import GraphAPIError
from apps.messaging.models import Comment, Contact, ContactTag, Conversation, Message

from ..models import Automation, AutomationRun, AutomationStep
from .matching import automation_matches

logger = logging.getLogger("damdm.automations")

StepType = AutomationStep.StepType
RunStatus = AutomationRun.Status
#: How long a run waits for a follow or a reply before giving up.
WAIT_LIMIT = timedelta(hours=settings.META_MESSAGING_WINDOW_HOURS)
FOLLOW_PAYLOAD = "follow_check"


class StepFailed(Exception):
    """A step couldn't be carried out; the run stops and records why."""


# --- entry points -------------------------------------------------------------

def on_comment(comment: Comment) -> AutomationRun | None:
    """A new comment arrived. Reply publicly and by DM if an automation matches."""
    if comment.is_from_owner or comment.contact is None or comment.matched:
        return None
    automation = _match_for_comment(comment)
    if automation is None:
        return None

    comment.automation = automation
    comment.save(update_fields=["automation", "updated_at"])
    return _start(automation, comment.contact, trigger_comment=comment)


def on_message(message: Message) -> AutomationRun | None:
    """A DM, story reply or story mention arrived."""
    if message.direction != Message.Direction.INBOUND:
        return None
    if message.message_type == Message.MessageType.POSTBACK:
        return _on_postback(message)

    live = _live_run_for(message.contact)
    if live is not None:
        return _answer_waiting_run(live, message)

    trigger = {
        Message.MessageType.STORY_REPLY: Automation.TriggerType.STORY_REPLY,
        Message.MessageType.STORY_MENTION: Automation.TriggerType.STORY_MENTION,
    }.get(message.message_type, Automation.TriggerType.DM_KEYWORD)

    automation = _match(message.account, trigger, message.text)
    if automation is None:
        return None
    return _start(automation, message.contact, trigger_message=message)


def resume(run: AutomationRun) -> AutomationRun:
    """Continue a run that was waiting on a delay."""
    if run.status == RunStatus.RUNNING and run.current_step is not None:
        _advance(run, run.current_step)
    return run


# --- choosing an automation ---------------------------------------------------

def _match_for_comment(comment: Comment) -> Automation | None:
    candidates = _candidates(comment.account, Automation.TriggerType.COMMENT)
    for automation in candidates:
        if not _covers_post(automation, comment):
            continue
        if automation_matches(automation, comment.text):
            return automation
    return None


def _match(account, trigger: str, text: str | None) -> Automation | None:
    for automation in _candidates(account, trigger):
        if automation_matches(automation, text):
            return automation
    return None


def _candidates(account, trigger: str):
    """Live automations for this account and trigger, best first."""
    now = timezone.now()
    return [
        automation
        for automation in Automation.objects.filter(
            account=account, status=Automation.Status.ACTIVE, trigger_type=trigger
        )
        .select_related("owner", "account")
        .prefetch_related("keywords", "steps", "automation_posts")
        .order_by("-priority", "-created_at")
        if automation.is_live_at(now)
    ]


def _covers_post(automation: Automation, comment: Comment) -> bool:
    if automation.applies_to_all_posts:
        return True
    if automation.post_scope == Automation.PostScope.NEXT_POST:
        return comment.post is not None and comment.post.created_at >= automation.created_at
    return any(
        link.post_id == comment.post_id for link in automation.automation_posts.all()
    ) and comment.post_id is not None


# --- starting and stepping ----------------------------------------------------

def _start(automation, contact, *, trigger_comment=None, trigger_message=None):
    if not _may_run(automation, contact):
        return None
    try:
        with transaction.atomic():
            run = AutomationRun.objects.create(
                automation=automation,
                contact=contact,
                trigger_comment=trigger_comment,
                trigger_message=trigger_message,
                current_step=automation.steps.first(),
            )
    except IntegrityError:
        return None  # a live run already exists for this person

    Automation.objects.filter(pk=automation.pk).update(runs_count=automation.runs_count + 1)
    UsageRecord.for_current_period(automation.owner).increment(comments_processed=1)
    if run.current_step is None:
        run.finish()
        return run
    return _advance(run, run.current_step)


def _may_run(automation: Automation, contact: Contact) -> bool:
    """Plan, opt-out, audience and re-trigger rules."""
    if not has_access(automation.owner):
        return False
    if not contact.can_be_automated:
        return False

    previous = AutomationRun.objects.filter(automation=automation, contact=contact)
    if automation.once_per_contact and previous.exists():
        return False
    if automation.cooldown_minutes:
        since = timezone.now() - timedelta(minutes=automation.cooldown_minutes)
        if previous.filter(started_at__gte=since).exists():
            return False
    if automation.audience == Automation.Audience.FOLLOWERS:
        following = _check_follow(automation.account, contact)
        if following is False:
            return False
    return True


def _advance(run: AutomationRun, step: AutomationStep | None) -> AutomationRun:
    """Carry out ``step`` and everything after it, until the run waits or ends."""
    while step is not None:
        run.current_step = step
        run.save(update_fields=["current_step", "updated_at"])
        try:
            outcome = _perform(run, step)
        except StepFailed as exc:
            logger.warning("Run %s stopped: %s", run.pk, exc)
            run.finish(RunStatus.FAILED, str(exc))
            return run
        except GraphAPIError as exc:
            logger.exception("Run %s failed to reach Instagram", run.pk)
            run.finish(RunStatus.FAILED, str(exc))
            return run
        if outcome is not None:  # the run is now waiting for something
            return outcome
        step = step.next_step()

    run.finish()
    Automation.objects.filter(pk=run.automation_id).update(
        completed_runs_count=run.automation.completed_runs_count + 1
    )
    return run


def _perform(run: AutomationRun, step: AutomationStep) -> AutomationRun | None:
    """Do one step. Returns the run when it should stop and wait, else None."""
    config = step.config if isinstance(step.config, dict) else {}
    match step.step_type:
        case StepType.PUBLIC_REPLY:
            _public_reply(run, config)
        case StepType.SEND_DM:
            _send_dm(run, step, config)
        case StepType.ASK_TO_FOLLOW:
            return _ask_to_follow(run, step, config)
        case StepType.ADD_TAG:
            _add_tag(run, config)
        case StepType.DELAY:
            return _delay(run, config)
        case StepType.WAIT_FOR_REPLY | StepType.COLLECT_EMAIL:
            return _wait_for_reply(run, step, config)
        case StepType.HANDOFF:
            _handoff(run)
            run.finish()
            return run
    return None


# --- the steps ----------------------------------------------------------------

def _public_reply(run: AutomationRun, config: dict) -> None:
    comment = run.trigger_comment
    variants = [str(v) for v in (config.get("variants") or []) if str(v).strip()]
    if comment is None or comment.public_replied_at or not variants:
        return
    text = random.choice(variants)  # noqa: S311 - variety, not security
    reply_id = send.reply_to_comment(run.automation.account, comment.external_comment_id, text)
    Comment.objects.filter(pk=comment.pk).update(
        public_replied_at=timezone.now(), updated_at=timezone.now()
    )
    if reply_id:
        Comment.objects.get_or_create(
            account=run.automation.account,
            external_comment_id=reply_id,
            defaults={
                "external_media_id": comment.external_media_id,
                "post": comment.post,
                "parent": comment,
                "text": text,
                "is_from_owner": True,
                "is_automated_reply": True,
                "automation": run.automation,
            },
        )


def _send_dm(run: AutomationRun, step: AutomationStep, config: dict) -> None:
    text = _fill(str(config.get("text") or ""), run)
    if not text:
        return
    buttons = config.get("buttons") if isinstance(config.get("buttons"), list) else []
    _deliver(run, step, text, buttons)


def _deliver(run: AutomationRun, step: AutomationStep | None, text: str, buttons: list) -> Message:
    """
    Send one message, as a private reply to the comment when we can.

    The first message of a comment automation must be the private reply: it is
    the only way to open a conversation with someone who only commented.
    """
    account = run.automation.account
    contact = run.contact
    comment = run.trigger_comment
    use_private_reply = _private_reply_available(comment)

    if not use_private_reply and not contact.messaging_window_open:
        raise StepFailed("The 24-hour messaging window has closed.")

    row = Message.objects.create(
        account=account,
        conversation=Conversation.objects.get_or_create(account=account, contact=contact)[0],
        contact=contact,
        direction=Message.Direction.OUTBOUND,
        sender_type=Message.SenderType.AUTOMATION,
        text=text,
        attachments=buttons,
        comment=comment if use_private_reply else None,
        automation_run=run,
        step=step,
        status=Message.Status.PROCESSING,
    )
    try:
        if use_private_reply:
            external_id = send.private_reply(
                account, comment.external_comment_id, text, buttons
            )
        else:
            external_id = send.send_dm(account, contact.external_user_id, text, buttons)
    except GraphAPIError as exc:
        row.mark_failed(str(exc), permanent=not exc.is_transient)
        raise

    row.mark_sent(external_id)
    if use_private_reply:
        Comment.objects.filter(pk=comment.pk).update(
            private_replied_at=timezone.now(), updated_at=timezone.now()
        )
    UsageRecord.for_current_period(run.automation.owner).increment(
        messages_sent=1, **({"private_replies_sent": 1} if use_private_reply else {})
    )
    Conversation.objects.filter(pk=row.conversation_id).update(
        last_message_at=timezone.now(), last_message_text=text[:200], updated_at=timezone.now()
    )
    return row


def _ask_to_follow(run: AutomationRun, step: AutomationStep, config: dict) -> AutomationRun | None:
    """Send the link only once the person follows; ask them if they don't yet."""
    if _check_follow(run.automation.account, run.contact) is not False:
        return None  # following, or Instagram wouldn't say - don't block them

    limit = int(config.get("max_checks") or 3)
    if run.follow_check_count >= limit:
        run.finish(RunStatus.SKIPPED, "Not following after several checks.")
        return run

    prompt = _fill(str(config.get("prompt") or ""), run)
    if run.follow_check_count:
        prompt = _fill(str(config.get("not_following_text") or prompt), run)
    title = str(config.get("button") or "I'm following")
    _deliver_postback(run, step, prompt, title)

    run.follow_check_count += 1
    run.status = RunStatus.WAITING_FOLLOW
    run.expires_at = timezone.now() + WAIT_LIMIT
    run.save(update_fields=["follow_check_count", "status", "expires_at", "updated_at"])
    return run


def _deliver_postback(run: AutomationRun, step: AutomationStep, text: str, title: str) -> None:
    account = run.automation.account
    comment = run.trigger_comment
    use_private_reply = _private_reply_available(comment)
    if not use_private_reply and not run.contact.messaging_window_open:
        raise StepFailed("The 24-hour messaging window has closed.")

    recipient = (
        {"comment_id": comment.external_comment_id} if use_private_reply
        else {"id": run.contact.external_user_id}
    )
    row = Message.objects.create(
        account=account,
        conversation=Conversation.objects.get_or_create(account=account, contact=run.contact)[0],
        contact=run.contact,
        direction=Message.Direction.OUTBOUND,
        sender_type=Message.SenderType.AUTOMATION,
        message_type=Message.MessageType.TEMPLATE,
        text=text,
        comment=comment if use_private_reply else None,
        automation_run=run,
        step=step,
        status=Message.Status.PROCESSING,
    )
    external_id = send.send_postback_prompt(
        account, recipient, text, title, f"{FOLLOW_PAYLOAD}:{run.pk}"
    )
    row.mark_sent(external_id)
    if use_private_reply:
        Comment.objects.filter(pk=comment.pk).update(
            private_replied_at=timezone.now(), updated_at=timezone.now()
        )
    UsageRecord.for_current_period(run.automation.owner).increment(messages_sent=1)


def _private_reply_available(comment: Comment | None) -> bool:
    """Instagram allows one private reply per comment, so check we haven't used it."""
    if comment is None or comment.private_replied_at is not None:
        return False
    return not Message.objects.filter(comment=comment).exists()


def _add_tag(run: AutomationRun, config: dict) -> None:
    tag_id = config.get("tag_id")
    if tag_id:
        ContactTag.objects.get_or_create(contact=run.contact, tag_id=tag_id)


def _delay(run: AutomationRun, config: dict) -> AutomationRun:
    """Pause the run and pick it up again later."""
    from apps.core.queue import enqueue_later

    from ..tasks import resume_run

    seconds = max(1, min(int(config.get("seconds") or 0), 60 * 60 * 24))
    run.status = RunStatus.RUNNING
    run.save(update_fields=["status", "updated_at"])
    transaction.on_commit(lambda: enqueue_later(resume_run, seconds, run.pk))
    return run


def _wait_for_reply(run: AutomationRun, step: AutomationStep, config: dict) -> AutomationRun:
    prompt = _fill(str(config.get("prompt") or ""), run)
    if prompt:
        _deliver(run, step, prompt, [])
    run.status = RunStatus.WAITING_REPLY
    run.expires_at = timezone.now() + WAIT_LIMIT
    run.save(update_fields=["status", "expires_at", "updated_at"])
    return run


def _handoff(run: AutomationRun) -> None:
    Conversation.objects.filter(account=run.automation.account, contact=run.contact).update(
        mode=Conversation.Mode.HUMAN, updated_at=timezone.now()
    )


# --- replies that continue a waiting run --------------------------------------

def _on_postback(message: Message) -> AutomationRun | None:
    """The person tapped a button we sent, e.g. "I'm following"."""
    payload = message.postback_payload or ""
    if not payload.startswith(f"{FOLLOW_PAYLOAD}:"):
        return None
    run = AutomationRun.objects.filter(
        pk=payload.split(":", 1)[1] or 0, contact=message.contact
    ).select_related("automation", "automation__account", "automation__owner").first()
    if run is None or run.status != RunStatus.WAITING_FOLLOW:
        return None

    contact = Contact.objects.get(pk=run.contact_id)  # refreshed window
    run.contact = contact
    if _check_follow(run.automation.account, contact, force=True) is False:
        step = run.current_step
        return _ask_to_follow(run, step, step.config if step else {})

    run.status = RunStatus.RUNNING
    run.save(update_fields=["status", "updated_at"])
    return _advance(run, run.current_step.next_step() if run.current_step else None)


def _answer_waiting_run(run: AutomationRun, message: Message) -> AutomationRun | None:
    """A DM arrived while a run was waiting for one."""
    if run.status != RunStatus.WAITING_REPLY:
        return None
    step = run.current_step
    config = step.config if step and isinstance(step.config, dict) else {}
    if step and step.step_type == StepType.COLLECT_EMAIL:
        field = str(config.get("field") or "email")
        run.context = {**(run.context or {}), field: message.text.strip()[:254]}
        if field == "email" and "@" in message.text:
            Contact.objects.filter(pk=run.contact_id).update(
                email=message.text.strip()[:254], updated_at=timezone.now()
            )
    run.status = RunStatus.RUNNING
    run.save(update_fields=["status", "context", "updated_at"])
    return _advance(run, step.next_step() if step else None)


def _live_run_for(contact: Contact) -> AutomationRun | None:
    return (
        AutomationRun.objects.filter(contact=contact, status__in=AutomationRun.LIVE_STATUSES)
        .select_related("automation", "automation__account", "automation__owner", "current_step")
        .order_by("-started_at")
        .first()
    )


# --- helpers ------------------------------------------------------------------

def _check_follow(account, contact: Contact, *, force: bool = False) -> bool | None:
    """Ask Instagram whether the contact follows the account, with a short cache."""
    fresh = (
        contact.follow_checked_at
        and timezone.now() - contact.follow_checked_at < timedelta(minutes=10)
    )
    if contact.is_following is not None and fresh and not force:
        return contact.is_following

    following = send.is_follower(account, contact.external_user_id)
    if following is not None:
        contact.is_following = following
        contact.follow_checked_at = timezone.now()
        contact.save(update_fields=["is_following", "follow_checked_at", "updated_at"])
    return following


def _fill(text: str, run: AutomationRun) -> str:
    """Replace the placeholders the builder offers."""
    contact = run.contact
    first_name = (contact.name or contact.username or "").split(" ")[0]
    values = {
        "first_name": first_name,
        "name": contact.name or contact.username or "",
        "username": contact.username or "",
    }
    for key, value in values.items():
        text = text.replace("{" + key + "}", value)
    return text
