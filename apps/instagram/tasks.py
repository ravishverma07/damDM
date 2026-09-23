"""Background jobs for connected Instagram accounts. Schedules live in settings."""

from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.db.models import Q
from django.utils import timezone

from apps.core.models import DataDeletionRequest

from .models import TOKEN_REFRESH_WINDOW, ConnectedAccount, WebhookEvent
from .services.graph import GraphAPIError
from .services.ingest import process as ingest_event
from .services.lifecycle import delete_platform_user_data
from .services.subscriptions import subscribe as subscribe_to_webhooks
from .services.sync import record_failure, refresh_account_token, sync_account

logger = logging.getLogger("damdm.instagram")

#: Accounts are re-synced once their last sync is older than this.
SYNC_INTERVAL = timedelta(hours=6)
#: A pending deletion older than this was never picked up and is re-queued.
DELETION_PICKUP_DELAY = timedelta(minutes=10)
#: A webhook event still pending after this never reached a worker.
WEBHOOK_PICKUP_DELAY = timedelta(minutes=5)


def _usable_account(account_id: int) -> ConnectedAccount | None:
    account = ConnectedAccount.objects.filter(
        pk=account_id, status=ConnectedAccount.Status.ACTIVE
    ).first()
    return account if account is not None and account.has_usable_token() else None


@shared_task(bind=True, max_retries=3, default_retry_delay=300, ignore_result=True)
def sync_account_task(self, account_id: int) -> None:
    account = _usable_account(account_id)
    if account is None:
        return
    try:
        result = sync_account(account)
    except GraphAPIError as exc:
        record_failure(account, exc)
        if exc.is_transient and self.request.retries < self.max_retries:
            raise self.retry(exc=exc) from exc
        return
    logger.info(
        "Synced account %s: %s new posts, %s updated", account.pk, result.created, result.updated
    )


@shared_task(bind=True, max_retries=3, default_retry_delay=600, ignore_result=True)
def refresh_account_token_task(self, account_id: int) -> None:
    account = _usable_account(account_id)
    if account is None:
        return
    try:
        refresh_account_token(account)
    except GraphAPIError as exc:
        record_failure(account, exc)
        if exc.is_transient and self.request.retries < self.max_retries:
            raise self.retry(exc=exc) from exc


@shared_task(ignore_result=True)
def refresh_expiring_tokens() -> None:
    """Mark lapsed tokens expired and refresh those inside the refresh window."""
    now = timezone.now()
    ConnectedAccount.objects.filter(
        status=ConnectedAccount.Status.ACTIVE, token_expires_at__lte=now
    ).update(status=ConnectedAccount.Status.EXPIRED, updated_at=now)

    due = ConnectedAccount.objects.filter(
        status=ConnectedAccount.Status.ACTIVE,
        token_expires_at__gt=now,
        token_expires_at__lte=now + TOKEN_REFRESH_WINDOW,
    ).values_list("pk", flat=True)
    for account_id in due:
        refresh_account_token_task.delay(account_id)


@shared_task(ignore_result=True)
def sync_stale_accounts() -> None:
    cutoff = timezone.now() - SYNC_INTERVAL
    stale = ConnectedAccount.objects.filter(status=ConnectedAccount.Status.ACTIVE).filter(
        Q(last_synced_at__isnull=True) | Q(last_synced_at__lt=cutoff)
    )
    for account_id in stale.values_list("pk", flat=True):
        sync_account_task.delay(account_id)


@shared_task(ignore_result=True)
def process_data_deletion(deletion_id: int) -> None:
    deletion = DataDeletionRequest.objects.filter(
        pk=deletion_id, status=DataDeletionRequest.Status.PENDING
    ).first()
    if deletion is not None:
        delete_platform_user_data(deletion)


@shared_task(ignore_result=True)
def process_pending_data_deletions() -> None:
    """Safety net for deletion requests whose task was never queued or failed."""
    stuck = DataDeletionRequest.objects.filter(
        status=DataDeletionRequest.Status.PENDING,
        created_at__lt=timezone.now() - DELETION_PICKUP_DELAY,
    )
    for deletion_id in stuck.values_list("pk", flat=True):
        process_data_deletion.delay(deletion_id)


@shared_task(ignore_result=True)
def purge_webhook_payloads() -> None:
    purged = WebhookEvent.purge_old_payloads()
    if purged:
        logger.info("Blanked %s old webhook payloads", purged)


@shared_task(bind=True, max_retries=3, default_retry_delay=60, ignore_result=True)
def process_webhook_event(self, event_id: int) -> None:
    """Record one stored webhook event as contacts, comments and messages."""
    event = WebhookEvent.objects.filter(
        pk=event_id,
        processing_status__in=(WebhookEvent.Status.PENDING, WebhookEvent.Status.FAILED),
    ).first()
    if event is None:
        return
    WebhookEvent.objects.filter(pk=event.pk).update(
        processing_status=WebhookEvent.Status.PROCESSING
    )
    try:
        status = ingest_event(event)
    except Exception as exc:
        logger.exception("Webhook event %s failed", event.pk)
        event.processing_status = WebhookEvent.Status.FAILED
        event.error_message = str(exc)[:2000]
        event.save(update_fields=["processing_status", "error_message"])
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc) from exc
        return
    event.processing_status = status
    event.processed_at = timezone.now()
    event.error_message = ""
    event.save(
        update_fields=["account", "processing_status", "processed_at", "error_message"]
    )


@shared_task(ignore_result=True)
def process_pending_webhooks() -> None:
    """Safety net for events whose task was never queued, or that failed."""
    stuck = WebhookEvent.objects.filter(
        processing_status__in=(WebhookEvent.Status.PENDING, WebhookEvent.Status.FAILED),
        received_at__lt=timezone.now() - WEBHOOK_PICKUP_DELAY,
    )
    for event_id in stuck.values_list("pk", flat=True)[:500]:
        process_webhook_event.delay(event_id)


@shared_task(ignore_result=True)
def subscribe_account_task(account_id: int) -> None:
    """Ask Instagram to send webhooks for one account."""
    account = _usable_account(account_id)
    if account is not None:
        subscribe_to_webhooks(account)


@shared_task(ignore_result=True)
def subscribe_pending_accounts() -> None:
    """Retry accounts that aren't receiving webhooks yet."""
    pending = ConnectedAccount.objects.filter(
        status=ConnectedAccount.Status.ACTIVE, webhooks_subscribed=False
    )
    for account_id in pending.values_list("pk", flat=True)[:100]:
        subscribe_account_task.delay(account_id)
