"""
The subscription lifecycle: start checkout, confirm the mandate, apply
provider updates (webhooks and sync), and cancel.

Access is only ever granted from state the provider reports - a verified
checkout signature followed by an API read, or a signed webhook - never from
what the browser says.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.automations.models import Automation
from apps.core.utils import client_ip, hash_ip

from ..models import Payment, Plan, Subscription, TrialClaim
from ..providers.base import BillingError, SubscriptionState, WebhookEventData
from ..providers.razorpay_provider import timestamp
from ..providers.registry import get_provider
from .access import LIVE_STATUSES, current_subscription, has_access
from .trials import (
    check_eligibility,
    grant_trial,
    identity_signals,
    payment_fingerprint,
    payment_method_already_trialed,
    record_payment_method,
)

logger = logging.getLogger("damdm.billing")

PLAN_SLUG = "pro"
MOCK_PERIOD = timedelta(days=30)
Status = Subscription.Status

#: Razorpay status -> ours. "authenticated" is resolved separately (trial or not).
PROVIDER_STATUS = {
    "created": Status.INCOMPLETE,
    "active": Status.ACTIVE,
    "pending": Status.PAST_DUE,
    "halted": Status.EXPIRED,
    "paused": Status.EXPIRED,
    "cancelled": Status.CANCELED,
    "completed": Status.EXPIRED,
    "expired": Status.EXPIRED,
}


class SubscriptionError(Exception):
    """A subscription problem whose message is safe to show to the user."""


@dataclass(frozen=True)
class Checkout:
    subscription: Subscription
    trial: bool
    #: True in mock mode, where the mandate is approved without Razorpay.
    completed: bool


def active_plan() -> Plan:
    plan = Plan.objects.filter(slug=PLAN_SLUG, is_active=True).first()
    if plan is None:
        raise SubscriptionError("Payments aren't set up yet. Please try again later.")
    return plan


def start_checkout(request, user) -> Checkout:
    """Create a provider subscription, with a trial when the person is eligible."""
    existing = current_subscription(user)
    if existing is not None and existing.has_access:
        raise SubscriptionError("Your plan is already active.")

    plan = active_plan()
    eligibility = check_eligibility(request, user)
    if eligibility.reason == "no_instagram":
        raise SubscriptionError(eligibility.message)
    trial = eligibility.eligible and plan.trial_days > 0
    trial_ends_at = timezone.now() + timedelta(days=plan.trial_days) if trial else None

    provider = get_provider()
    previous = Subscription.objects.filter(user=user).exclude(provider_customer_id="").first()
    try:
        customer_id = previous.provider_customer_id if previous else provider.create_customer(user)
        state = provider.create_subscription(
            user=user, plan=plan, customer_id=customer_id, start_at=trial_ends_at
        )
    except BillingError as exc:
        raise SubscriptionError(str(exc)) from exc

    now = timezone.now()
    with transaction.atomic():
        # Abandoned checkouts and lapsed subscriptions make way for the new one.
        Subscription.objects.filter(
            user=user, status__in=(Status.INCOMPLETE, *LIVE_STATUSES)
        ).update(status=Status.EXPIRED, ended_at=now)
        subscription = Subscription.objects.create(
            user=user,
            plan=plan,
            provider=provider.name,
            provider_customer_id=customer_id,
            provider_subscription_id=state.subscription_id,
            status=Status.INCOMPLETE,
            trial_ends_at=trial_ends_at,
            trial_signals=[list(pair) for pair in identity_signals(request, user)] if trial else [],
            trial_ip_hash=hash_ip(client_ip(request)) if trial else "",
        )

    if settings.RAZORPAY_MOCK:
        apply_provider_status(
            subscription,
            "authenticated",
            period_start=None if trial else now,
            period_end=None if trial else now + MOCK_PERIOD,
            payment_method="upi",
        )
        return Checkout(subscription, trial, completed=True)
    return Checkout(subscription, trial, completed=False)


def confirm_checkout(
    user, *, payment_id: str, subscription_id: str, signature: str
) -> Subscription:
    """Called when the checkout widget reports success: verify, then read the truth."""
    subscription = Subscription.objects.filter(
        user=user, provider_subscription_id=subscription_id
    ).first()
    if subscription is None:
        raise SubscriptionError("We couldn't find that checkout. Please try again.")

    provider = get_provider()
    if not provider.verify_checkout(
        payment_id=payment_id, subscription_id=subscription_id, signature=signature
    ):
        raise SubscriptionError("We couldn't verify that payment. Please try again.")
    try:
        state = provider.fetch_subscription(subscription_id)
    except BillingError as exc:
        raise SubscriptionError(str(exc)) from exc
    return apply_state(subscription, state)


def cancel_subscription(user) -> Subscription:
    """Stop renewals. Access continues until the trial or paid month ends."""
    subscription = current_subscription(user)
    if subscription is None or subscription.status not in LIVE_STATUSES:
        raise SubscriptionError("There's no active plan to cancel.")
    if subscription.cancel_at_period_end:
        return subscription

    in_trial = subscription.status == Status.TRIALING
    try:
        # A trial is cancelled right away at Razorpay so the first charge never
        # happens; locally the trial runs to its end.
        get_provider().cancel_subscription(
            subscription.provider_subscription_id, at_period_end=not in_trial
        )
    except BillingError as exc:
        raise SubscriptionError(str(exc)) from exc
    subscription.cancel_at_period_end = True
    subscription.save(update_fields=["cancel_at_period_end", "updated_at"])
    return subscription


def apply_state(
    subscription: Subscription, state: SubscriptionState, *, payment: dict | None = None
) -> Subscription:
    return apply_provider_status(
        subscription,
        state.status,
        period_start=state.current_period_start,
        period_end=state.current_period_end,
        ended_at=state.ended_at,
        payment_method=state.payment_method,
        payment=payment,
    )


@transaction.atomic
def apply_provider_status(
    subscription: Subscription,
    provider_status: str,
    *,
    period_start=None,
    period_end=None,
    ended_at=None,
    payment_method: str = "",
    payment: dict | None = None,
) -> Subscription:
    """Move a subscription to the provider's reported state and follow the consequences."""
    subscription.status = _local_status(subscription, provider_status)
    if period_start:
        subscription.current_period_start = period_start
    if period_end:
        subscription.current_period_end = period_end
    if payment and payment.get("method"):
        payment_method = str(payment["method"])
    if payment_method:
        subscription.payment_method = payment_method
    if subscription.status in (Status.CANCELED, Status.EXPIRED):
        subscription.ended_at = ended_at or subscription.ended_at or timezone.now()
    subscription.save()

    if subscription.trial_ends_at:
        _update_trial(subscription, payment)
    # Judged across all the user's subscriptions, so an old one ending can't pause a new one.
    if not has_access(subscription.user):
        pause_automations(subscription.user)
    return subscription


def handle_webhook(event: WebhookEventData) -> str:
    """Apply a verified provider webhook. Returns the processing status to record."""
    body = event.payload.get("payload") or {}
    entity = (body.get("subscription") or {}).get("entity")
    payment = (body.get("payment") or {}).get("entity")
    if not entity:
        return "ignored"
    subscription = (
        Subscription.objects.select_related("user")
        .filter(provider=Subscription.Provider.RAZORPAY, provider_subscription_id=entity.get("id"))
        .first()
    )
    if subscription is None:
        return "ignored"

    apply_state(subscription, get_provider().state_from_entity(entity), payment=payment)
    if event.event_type == "subscription.charged" and payment:
        record_payment(subscription, payment, Payment.Status.CAPTURED)
    return "processed"


def record_payment(subscription: Subscription, payment: dict, status: str) -> Payment:
    record, _ = Payment.objects.update_or_create(
        provider=subscription.provider,
        provider_payment_id=str(payment["id"]),
        defaults={
            "user": subscription.user,
            "subscription": subscription,
            "amount": int(payment.get("amount") or 0),
            "currency": str(payment.get("currency") or "INR"),
            "status": status,
            "method": str(payment.get("method") or ""),
            "paid_at": timestamp(payment.get("created_at")),
        },
    )
    return record


def pause_automations(user) -> int:
    return Automation.objects.filter(owner=user, status=Automation.Status.ACTIVE).update(
        status=Automation.Status.PAUSED, updated_at=timezone.now()
    )


def sync_with_provider(subscription: Subscription) -> Subscription:
    """Re-read one subscription from the provider (safety net for missed webhooks)."""
    if settings.RAZORPAY_MOCK:
        return _advance_mock(subscription)
    try:
        state = get_provider().fetch_subscription(subscription.provider_subscription_id)
    except BillingError:
        logger.exception("Could not sync subscription %s", subscription.pk)
        return subscription
    return apply_state(subscription, state)


# --- internals ---------------------------------------------------------------

def _local_status(subscription: Subscription, provider_status: str) -> str:
    now = timezone.now()
    in_trial = bool(subscription.trial_ends_at and subscription.trial_ends_at > now)
    if provider_status == "authenticated":
        return Status.TRIALING if in_trial else Status.ACTIVE
    if provider_status == "cancelled" and subscription.cancel_at_period_end and in_trial:
        return Status.TRIALING  # cancelled during the trial: keep it until it ends
    return PROVIDER_STATUS.get(provider_status, subscription.status)


def _update_trial(subscription: Subscription, payment: dict | None) -> None:
    status = subscription.status
    claim = TrialClaim.objects.filter(subscription=subscription).first()
    if claim is None:
        if status not in LIVE_STATUSES:
            return
        claim = grant_trial(subscription)

    payment_hash = payment_fingerprint(payment) if payment else ""
    if payment_hash:
        if status == Status.TRIALING and payment_method_already_trialed(claim, payment_hash):
            _revoke_trial(subscription, claim)
            return
        record_payment_method(claim, payment_hash)

    if status == Status.ACTIVE and claim.status == TrialClaim.Status.ACTIVE:
        claim.status = TrialClaim.Status.CONVERTED
        claim.save(update_fields=["status", "updated_at"])
    elif status in (Status.CANCELED, Status.EXPIRED) and claim.status == TrialClaim.Status.ACTIVE:
        claim.status = TrialClaim.Status.ENDED
        claim.save(update_fields=["status", "updated_at"])


def _revoke_trial(subscription: Subscription, claim: TrialClaim) -> None:
    """The mandate's UPI ID or card already had a trial: end this one now."""
    try:
        get_provider().cancel_subscription(
            subscription.provider_subscription_id, at_period_end=False
        )
    except BillingError:
        logger.exception("Could not cancel revoked trial %s at the provider", subscription.pk)
    claim.status = TrialClaim.Status.REVOKED
    claim.revoked_reason = "UPI ID or card already used for a free trial"
    claim.save(update_fields=["status", "revoked_reason", "updated_at"])
    subscription.status = Status.CANCELED
    subscription.ended_at = timezone.now()
    subscription.save(update_fields=["status", "ended_at", "updated_at"])
    pause_automations(subscription.user)


def _advance_mock(subscription: Subscription) -> Subscription:
    """Mock mode: a finished trial or month renews as if the charge succeeded."""
    now = timezone.now()
    due = (subscription.status == Status.TRIALING and subscription.trial_ends_at
           and subscription.trial_ends_at <= now) or (
        subscription.status == Status.ACTIVE and subscription.current_period_end
        and subscription.current_period_end <= now
    )
    if not due:
        return subscription
    if subscription.cancel_at_period_end:
        return apply_provider_status(subscription, "cancelled")
    start = subscription.current_period_end or subscription.trial_ends_at or now
    return apply_provider_status(
        subscription, "active", period_start=start, period_end=start + MOCK_PERIOD
    )
