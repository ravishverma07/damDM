"""Background jobs for billing. Schedules live in settings."""

from __future__ import annotations

from datetime import timedelta

from celery import shared_task
from django.db.models import Q
from django.utils import timezone

from apps.core import notices

from .models import Subscription
from .services.subscriptions import sync_with_provider

TRIAL_REMINDER_BEFORE = timedelta(days=2)


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



@shared_task(ignore_result=True)
def remind_trial_ending() -> None:
    """Two days before a trial ends, remind people their first payment is coming."""
    now = timezone.now()
    ending = Subscription.objects.filter(
        status=Subscription.Status.TRIALING,
        cancel_at_period_end=False,
        trial_ends_at__gt=now,
        trial_ends_at__lte=now + TRIAL_REMINDER_BEFORE,
    ).exclude(trial_signals=[]).select_related("user")  # coupon free months aren't a trial
    for subscription in ending:
        notices.trial_ending(subscription)
