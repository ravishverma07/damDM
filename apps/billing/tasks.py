"""Background jobs for billing. Schedules live in settings."""

from __future__ import annotations

from celery import shared_task
from django.db.models import Q
from django.utils import timezone

from .models import Subscription
from .services.subscriptions import sync_with_provider


@shared_task(ignore_result=True)
def sync_subscriptions() -> None:
    """
    Re-read subscriptions whose trial or paid month has ended, in case a
    renewal, failure or cancellation webhook never arrived.
    """
    now = timezone.now()
    due = Subscription.objects.filter(
        Q(status=Subscription.Status.TRIALING, trial_ends_at__lte=now)
        | Q(status__in=[Subscription.Status.ACTIVE, Subscription.Status.PAST_DUE],
            current_period_end__lte=now)
    ).select_related("user")
    for subscription in due:
        sync_with_provider(subscription)

