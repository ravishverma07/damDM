"""
Coupon rules: who can use a coupon, notifying them, redeeming, archiving.

Only accounts whose connected Instagram username exactly matches a username on
the coupon can use it (case and a leading @ are ignored). Each username and
each DamDM user can use a coupon once.
"""

from __future__ import annotations

import logging
import re
import secrets

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from apps.billing.models import Subscription
from apps.billing.providers.base import BillingError
from apps.billing.providers.registry import get_provider
from apps.billing.services.access import LIVE_STATUSES
from apps.billing.services.subscriptions import apply_provider_status
from apps.core.models import Notification
from apps.instagram.models import ConnectedAccount, Platform

from .models import Coupon, CouponRecipient, CouponRedemption

logger = logging.getLogger("damdm.coupons")

USERNAME_PATTERN = re.compile(r"^[a-z0-9._]{1,30}$")
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
NOT_AVAILABLE = "This coupon isn't available for your Instagram account."


class CouponError(Exception):
    """A coupon problem whose message is safe to show to the user."""


# --- usernames and codes -----------------------------------------------------

def normalize_username(raw: str | None) -> str:
    return (raw or "").strip().lstrip("@").lower()


def parse_usernames(text: str | None) -> tuple[list[str], list[str]]:
    """Split admin input into (valid, invalid) usernames, de-duplicated."""
    valid, invalid = [], []
    for token in re.split(r"[\s,]+", text or ""):
        username = normalize_username(token)
        if not username:
            continue
        bucket = valid if USERNAME_PATTERN.fullmatch(username) else invalid
        if username not in bucket:
            bucket.append(username)
    return valid, invalid


def generate_code() -> str:
    for _ in range(20):
        code = "DAMDM-" + "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))
        if not Coupon.objects.filter(code=code).exists():
            return code
    raise CouponError("Couldn't generate a unique coupon code.")


@transaction.atomic
def sync_recipients(coupon: Coupon, usernames: list[str]) -> None:
    """Make the coupon's usernames match the list, keeping ones already redeemed."""
    wanted = {normalize_username(name) for name in usernames} - {""}
    coupon.recipients.exclude(instagram_username__in=wanted).filter(
        redemptions__isnull=True
    ).delete()
    existing = set(coupon.recipients.values_list("instagram_username", flat=True))
    CouponRecipient.objects.bulk_create(
        [CouponRecipient(coupon=coupon, instagram_username=name) for name in wanted - existing]
    )


# --- who can use what ---------------------------------------------------------

def _usernames_for(user) -> set[str]:
    return {
        normalize_username(username)
        for username in ConnectedAccount.objects.filter(
            owner=user,
            platform=Platform.INSTAGRAM,
            status__in=ConnectedAccount.CLAIMED_STATUSES,
        ).values_list("username", flat=True)
        if username
    }


def _recipient_for(coupon: Coupon, usernames: set[str]) -> CouponRecipient | None:
    if not usernames:
        return None
    return coupon.recipients.filter(instagram_username__in=usernames).first()


def _usable_by(coupon: Coupon, user, usernames: set[str]) -> bool:
    if not coupon.is_usable:
        return False
    recipient = _recipient_for(coupon, usernames)
    if recipient is None:
        return False
    redemption = (
        CouponRedemption.objects.select_related("subscription")
        .filter(Q(coupon=coupon, user=user) | Q(recipient=recipient))
        .first()
    )
    if redemption is None:
        return True
    # The same person abandoning checkout can try again.
    return redemption.user_id == user.pk and (
        redemption.subscription is None
        or redemption.subscription.status == Subscription.Status.INCOMPLETE
    )


def available_coupons(user) -> list[Coupon]:
    usernames = _usernames_for(user)
    if not usernames:
        return []
    candidates = Coupon.objects.filter(
        status=Coupon.Status.ACTIVE,
        expires_at__gt=timezone.now(),
        recipients__instagram_username__in=usernames,
    ).distinct()
    return [coupon for coupon in candidates if _usable_by(coupon, user, usernames)]


def coupon_for_checkout(user, code: str) -> Coupon:
    coupon = Coupon.objects.filter(code__iexact=(code or "").strip()).first()
    if coupon is None or not _usable_by(coupon, user, _usernames_for(user)):
        raise CouponError(NOT_AVAILABLE)
    return coupon


@transaction.atomic
def record_redemption(coupon: Coupon, user, subscription: Subscription) -> CouponRedemption:
    recipient = _recipient_for(coupon, _usernames_for(user))
    if recipient is None:
        raise CouponError(NOT_AVAILABLE)
    redemption = CouponRedemption.objects.filter(coupon=coupon, user=user).first()
    try:
        with transaction.atomic():
            if redemption is None:
                redemption = CouponRedemption.objects.create(
                    coupon=coupon, recipient=recipient, user=user, subscription=subscription
                )
            else:
                redemption.recipient = recipient
                redemption.subscription = subscription
                redemption.save(update_fields=["recipient", "subscription", "updated_at"])
    except IntegrityError as exc:
        raise CouponError(NOT_AVAILABLE) from exc
    Notification.objects.filter(user=user, dedupe_key=_notice_key(coupon)).update(
        read_at=timezone.now()
    )
    return redemption


# --- notifications -------------------------------------------------------------

def notify_matching_coupons(account: ConnectedAccount) -> int:
    """Tell the owner of a newly connected account about coupons for its username."""
    username = normalize_username(account.username)
    if not username or not account.is_claimed:
        return 0
    recipients = CouponRecipient.objects.select_related("coupon").filter(
        instagram_username=username,
        coupon__status=Coupon.Status.ACTIVE,
        coupon__expires_at__gt=timezone.now(),
        coupon__notify_on_connect=True,
        redemptions__isnull=True,
    )
    created = 0
    for recipient in recipients:
        coupon = recipient.coupon
        _, made = Notification.objects.get_or_create(
            user=account.owner,
            dedupe_key=_notice_key(coupon),
            defaults={
                "kind": "coupon",
                "title": f"🎁 You have a coupon: {coupon.summary}",
                "body": (
                    f"For @{username}. Use it when you start your plan, before "
                    f"{timezone.localtime(coupon.expires_at):%d %b %Y}."
                ),
                "action_label": "Redeem",
                "action_url": reverse("billing:overview"),
            },
        )
        created += made
        if recipient.notified_at is None:
            recipient.notified_at = timezone.now()
            recipient.save(update_fields=["notified_at"])
    return created


def notify_recipients_of(coupon: Coupon) -> int:
    """After a coupon is saved: notify recipients whose accounts are already connected."""
    if not (coupon.notify_on_connect and coupon.is_usable):
        return 0
    usernames = list(coupon.recipients.values_list("instagram_username", flat=True))
    accounts = ConnectedAccount.objects.filter(
        platform=Platform.INSTAGRAM, status__in=ConnectedAccount.CLAIMED_STATUSES
    ).filter(username__iregex=r"^(" + "|".join(re.escape(u) for u in usernames) + r")$") \
        if usernames else ConnectedAccount.objects.none()
    return sum(notify_matching_coupons(account) for account in accounts.select_related("owner"))


# --- archiving -----------------------------------------------------------------

def archive_coupon(coupon: Coupon) -> int:
    """
    Stop a coupon being used. A 100% coupon also cancels the subscriptions it
    started, since they were free; discounted subscriptions keep running.
    Returns how many subscriptions were cancelled.
    """
    if coupon.status == Coupon.Status.ARCHIVED:
        return 0
    now = timezone.now()
    coupon.status = Coupon.Status.ARCHIVED
    coupon.archived_at = now
    coupon.save(update_fields=["status", "archived_at", "updated_at"])
    Notification.objects.filter(dedupe_key=_notice_key(coupon), read_at__isnull=True).update(
        read_at=now
    )
    if not coupon.is_free:
        return 0

    cancelled = 0
    redemptions = coupon.redemptions.select_related("subscription", "user").filter(
        subscription__status__in=LIVE_STATUSES, cancelled_at__isnull=True
    )
    for redemption in redemptions:
        subscription = redemption.subscription
        try:
            get_provider().cancel_subscription(
                subscription.provider_subscription_id, at_period_end=False
            )
        except BillingError:
            logger.exception("Could not cancel subscription %s at the provider", subscription.pk)
        subscription.cancel_at_period_end = False
        subscription.save(update_fields=["cancel_at_period_end", "updated_at"])
        apply_provider_status(subscription, "cancelled")
        redemption.cancelled_at = now
        redemption.save(update_fields=["cancelled_at", "updated_at"])
        Notification.objects.get_or_create(
            user=redemption.user,
            dedupe_key=f"coupon-ended:{coupon.pk}",
            defaults={
                "kind": "coupon",
                "title": "Your free coupon has ended",
                "body": "The coupon was withdrawn, so its free plan was cancelled. "
                        "You can subscribe any time for ₹99/month.",
                "action_label": "View plan",
                "action_url": reverse("billing:overview"),
            },
        )
        cancelled += 1
    return cancelled


def _notice_key(coupon: Coupon) -> str:
    return f"coupon:{coupon.pk}"
