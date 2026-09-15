"""Background jobs for refer and earn. Schedules live in settings."""

from __future__ import annotations

from celery import shared_task

from .services import release_matured_rewards


@shared_task(ignore_result=True)
def release_referral_rewards() -> None:
    """Referral earnings past the 7-day hold become withdrawable."""
    release_matured_rewards()
