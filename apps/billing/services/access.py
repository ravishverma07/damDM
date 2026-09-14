"""Whether a user's plan currently lets their automations run."""

from __future__ import annotations

from ..models import Subscription

LIVE_STATUSES = (
    Subscription.Status.TRIALING,
    Subscription.Status.ACTIVE,
    Subscription.Status.PAST_DUE,
)


def current_subscription(user) -> Subscription | None:
    """The live subscription if there is one, otherwise the most recent."""
    subscriptions = Subscription.objects.filter(user=user).select_related("plan")
    return (
        subscriptions.filter(status__in=LIVE_STATUSES).order_by("-created_at").first()
        or subscriptions.order_by("-created_at").first()
    )


def has_access(user) -> bool:
    if user is None or not user.is_authenticated:
        return False
    subscription = current_subscription(user)
    return bool(subscription and subscription.has_access)
