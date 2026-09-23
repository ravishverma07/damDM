"""
The notifications DamDM sends, in one place so the wording stays consistent.

Each creates a dashboard notification (also pushed to the user's devices)
or, for good news that needs no action, a push only.
"""

from __future__ import annotations

import zoneinfo
from datetime import datetime

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from .notify import notify, push_only


def _local(user, moment: datetime) -> datetime:
    """``moment`` in the user's timezone (their last browser zone, else the default)."""
    name = user.timezone if user.timezone and user.timezone != "UTC" else ""
    try:
        zone = zoneinfo.ZoneInfo(name or settings.DEFAULT_DISPLAY_TIME_ZONE)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError):
        zone = zoneinfo.ZoneInfo(settings.DEFAULT_DISPLAY_TIME_ZONE)
    return timezone.localtime(moment, zone)


def _rupees(paise: int) -> str:
    amount = paise / 100
    return f"₹{amount:,.0f}" if amount == int(amount) else f"₹{amount:,.2f}"


# --- billing ----------------------------------------------------------------

def payment_failed(subscription) -> None:
    period = subscription.current_period_end
    notify(
        subscription.user,
        kind="payment_failed",
        title="Your payment didn't go through",
        body="Razorpay will try again. Keep enough balance in your UPI account so your "
             "automations don't pause.",
        action_label="View billing",
        action_url=reverse("billing:overview"),
        dedupe_key=f"payment-failed:{subscription.pk}:{period:%Y%m%d}" if period
        else f"payment-failed:{subscription.pk}",
    )


def plan_ended(subscription, paused: int) -> None:
    body = "Start a plan to keep replying to comments and DMs."
    if paused:
        body = f"{paused} automation{'s were' if paused != 1 else ' was'} paused. " + body
    notify(
        subscription.user,
        kind="plan_ended",
        title="Your DamDM plan has ended",
        body=body,
        action_label="Choose a plan",
        action_url=reverse("billing:overview"),
        dedupe_key=f"plan-ended:{subscription.pk}",
    )


def trial_ending(subscription) -> None:
    ends = _local(subscription.user, subscription.trial_ends_at)
    notify(
        subscription.user,
        kind="trial_ending",
        title=f"Your free trial ends on {ends:%d %b}",
        body=f"Your first ₹{settings.PLAN_PRICE} AutoPay payment is on {ends:%d %b %Y}. "
             "Nothing to do if you'd like to continue.",
        action_label="Manage plan",
        action_url=reverse("billing:overview"),
        dedupe_key=f"trial-ending:{subscription.pk}",
    )


def payment_received(payment) -> None:
    push_only(
        payment.user,
        title=f"Payment received: {_rupees(payment.amount)}",
        body="Thanks! Your DamDM plan is active.",
        url=reverse("billing:overview"),
        tag="payment",
    )


# --- referrals --------------------------------------------------------------

def reward_earned(reward) -> None:
    available = _local(reward.referrer, reward.available_at)
    notify(
        reward.referrer,
        kind="reward_earned",
        title=f"You earned {_rupees(reward.amount)} 🎉",
        body=f"A friend you invited paid for DamDM. You can withdraw it from {available:%d %b}.",
        action_label="See earnings",
        action_url=reverse("referrals:overview"),
        dedupe_key=f"reward-earned:{reward.pk}",
    )


def rewards_available(user, amount: int, batch_key: str) -> None:
    notify(
        user,
        kind="rewards_available",
        title=f"{_rupees(amount)} is ready to withdraw",
        body="Your referral earnings have cleared. Withdraw them to your UPI ID any time.",
        action_label="Withdraw",
        action_url=reverse("referrals:overview"),
        dedupe_key=f"rewards-available:{batch_key}",
    )


def payout_paid(payout) -> None:
    notify(
        payout.user,
        kind="payout_paid",
        title=f"{_rupees(payout.amount)} sent to your UPI",
        body=f"Transaction reference: {payout.reference}",
        action_url=reverse("referrals:overview"),
        dedupe_key=f"payout-paid:{payout.pk}",
    )


def payout_rejected(payout) -> None:
    notify(
        payout.user,
        kind="payout_rejected",
        title="Your withdrawal wasn't processed",
        body=(payout.note or "The amount is back in your wallet.")[:300],
        action_label="See earnings",
        action_url=reverse("referrals:overview"),
        dedupe_key=f"payout-rejected:{payout.pk}",
    )


# --- instagram --------------------------------------------------------------

def instagram_needs_attention(account) -> None:
    if account.owner_id is None:
        return
    name = f"@{account.username}" if account.username else "your Instagram account"
    notify(
        account.owner,
        kind="instagram_disconnected",
        title=f"Reconnect {name}",
        body="DamDM lost access to this account, so its automations can't reply. "
             "Reconnect it to keep them running.",
        action_label="Reconnect",
        action_url=reverse("instagram:accounts"),
        dedupe_key=f"ig-attention:{account.pk}:{timezone.now():%Y%m%d}",
    )
