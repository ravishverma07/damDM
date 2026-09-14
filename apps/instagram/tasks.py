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
from .services.lifecycle import delete_platform_user_data
from .services.sync import record_failure, refresh_account_token, sync_account

logger = logging.getLogger("damdm.instagram")

#: Accounts are re-synced once their last sync is older than this.
SYNC_INTERVAL = timedelta(hours=6)
#: A pending deletion older than this was never picked up and is re-queued.
DELETION_PICKUP_DELAY = timedelta(minutes=10)


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
