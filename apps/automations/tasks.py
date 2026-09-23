"""Background jobs for automations. Schedules live in settings."""

from __future__ import annotations

import logging

from celery import shared_task
from django.utils import timezone

from apps.messaging.models import Comment, Message

from .models import AutomationRun
from .services import runner

logger = logging.getLogger("damdm.automations")


@shared_task(bind=True, max_retries=3, default_retry_delay=60, ignore_result=True)
def run_for_comment(self, comment_id: int) -> None:
    """A comment arrived: reply publicly and by DM if an automation matches."""
    comment = (
        Comment.objects.select_related("account", "account__owner", "contact", "post")
        .filter(pk=comment_id)
        .first()
    )
    if comment is None:
        return
    try:
        runner.on_comment(comment)
    except Exception as exc:
        logger.exception("Automation for comment %s failed", comment_id)
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc) from exc


@shared_task(bind=True, max_retries=3, default_retry_delay=60, ignore_result=True)
def run_for_message(self, message_id: int) -> None:
    """A DM, story reply, story mention or button tap arrived."""
    message = (
        Message.objects.select_related("account", "account__owner", "contact")
        .filter(pk=message_id)
        .first()
    )
    if message is None:
        return
    try:
        runner.on_message(message)
    except Exception as exc:
        logger.exception("Automation for message %s failed", message_id)
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc) from exc


@shared_task(ignore_result=True)
def resume_run(run_id: int) -> None:
    """Continue a run that was waiting out a delay step."""
    run = (
        AutomationRun.objects.select_related(
            "automation", "automation__account", "automation__owner", "contact", "current_step"
        )
        .filter(pk=run_id, status=AutomationRun.Status.RUNNING)
        .first()
    )
    if run is not None:
        runner.resume(run)


@shared_task(ignore_result=True)
def expire_waiting_runs() -> None:
    """Runs whose messaging window closed can never continue."""
    stale = AutomationRun.objects.filter(
        status__in=(AutomationRun.Status.WAITING_FOLLOW, AutomationRun.Status.WAITING_REPLY),
        expires_at__lte=timezone.now(),
    )
    expired = stale.update(
        status=AutomationRun.Status.EXPIRED, completed_at=timezone.now(),
        updated_at=timezone.now(),
    )
    if expired:
        logger.info("Expired %s waiting automation runs", expired)
