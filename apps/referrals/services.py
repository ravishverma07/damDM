"""
Refer and earn.

Anyone can share ``/r/<code>/``. The code is remembered for 30 days, and an
account created during that time is linked to the referrer when it first
signs in. When the friend's first real
payment succeeds, the referrer earns 50% of it. The reward is held for 7 days
(refund window), then can be withdrawn to UPI once the balance reaches ₹100.

Self-referrals are refused using the same hashed identity signals as the free
trial - email, Google account, Instagram accounts, browser and payment method -
checked both when the friend signs up and again when they pay.
"""

from __future__ import annotations

import re
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.urls import reverse
from django.utils import timezone

from apps.billing.models import Payment, TrialFingerprint
from apps.billing.services.trials import fingerprint, identity_signals

from .models import Payout, Referral, ReferralCode, ReferralReward

REWARD_PERCENT = 50
HOLD_PERIOD = timedelta(days=7)
#: Minimum withdrawal, in paise.
MIN_PAYOUT = 10_000
COOKIE_NAME = "damdm_ref"
COOKIE_SALT = "damdm.referral"
COOKIE_MAX_AGE = 60 * 60 * 24 * 30
#: Only accounts created this recently can be attached to an invite.
NEW_ACCOUNT_WINDOW = timedelta(minutes=30)
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O or 1/I
CODE_LENGTH = 8
MAX_DEVICE_HASHES = 20
UPI_PATTERN = re.compile(r"^[a-z0-9._-]{2,256}@[a-z][a-z0-9]{1,63}$")
SAME_PERSON = "Same person as the referrer"

Kind = TrialFingerprint.Kind


class ReferralError(Exception):
    """A referral problem whose message is safe to show to the user."""


# --- codes and links ---------------------------------------------------------

def get_or_create_code(user) -> ReferralCode:
    existing = ReferralCode.objects.filter(user=user).first()
    if existing is not None:
        return existing
    for _ in range(10):
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        try:
            with transaction.atomic():
                return ReferralCode.objects.create(user=user, code=code)
        except IntegrityError:
            existing = ReferralCode.objects.filter(user=user).first()
            if existing is not None:
                return existing
    raise ReferralError("Couldn't create your invite code. Please try again.")


def referral_url(code: ReferralCode) -> str:
    return f"{settings.APP_BASE_URL}{reverse('referral-link', args=[code.code])}"


def find_code(raw: str | None) -> ReferralCode | None:
    code = (raw or "").strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{4,16}", code):
        return None
    return ReferralCode.objects.select_related("user").filter(code=code).first()


def remember_device(request, code: ReferralCode) -> None:
    """Note the referrer's browser, so their own second account can't earn."""
    device_id = getattr(request, "device_id", "")
    if not device_id:
        return
    device_hash = fingerprint(Kind.DEVICE, device_id)
    if device_hash not in code.device_hashes:
        code.device_hashes = [*code.device_hashes, device_hash][-MAX_DEVICE_HASHES:]
        code.save(update_fields=["device_hashes", "updated_at"])


def set_referral_cookie(response, code: ReferralCode) -> None:
    response.set_signed_cookie(
        COOKIE_NAME,
        code.code,
        salt=COOKIE_SALT,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        samesite="Lax",
        secure=settings.SESSION_COOKIE_SECURE,
    )


def code_from_request(request) -> str:
    return request.get_signed_cookie(
        COOKIE_NAME, default="", salt=COOKIE_SALT, max_age=COOKIE_MAX_AGE
    )


# --- attaching and rewarding ------------------------------------------------

def attach_referral(request, user) -> Referral | None:
    """Link a brand-new account to the invite it arrived through, if any."""
    code = find_code(code_from_request(request))
    if code is None or code.user_id == user.pk:
        return None
    if user.created_at < timezone.now() - NEW_ACCOUNT_WINDOW:
        return None
    if Referral.objects.filter(referred=user).exists():
        return None

    same_person = bool(_hashes_for(user, request) & _hashes_for(code.user))
    try:
        with transaction.atomic():
            return Referral.objects.create(
                referrer=code.user,
                referred=user,
                code=code.code,
                status=Referral.Status.REJECTED if same_person else Referral.Status.JOINED,
                rejected_reason=SAME_PERSON if same_person else "",
            )
    except IntegrityError:
        return None


@transaction.atomic
def reward_for_payment(payment: Payment) -> ReferralReward | None:
    """Credit the referrer for a friend's first successful payment."""
    if payment.status != Payment.Status.CAPTURED or payment.amount <= 0:
        return None
    referral = (
        Referral.objects.select_for_update()
        .select_related("referrer")
        .filter(referred=payment.user, status=Referral.Status.JOINED)
        .first()
    )
    if referral is None or ReferralReward.objects.filter(referral=referral).exists():
        return None
    earlier = Payment.objects.filter(user=payment.user, status=Payment.Status.CAPTURED)
    if earlier.exclude(pk=payment.pk).exists():
        return None

    # Check again now the friend has connected Instagram and paid with something.
    if _hashes_for(payment.user) & _hashes_for(referral.referrer):
        referral.status = Referral.Status.REJECTED
        referral.rejected_reason = SAME_PERSON
        referral.save(update_fields=["status", "rejected_reason", "updated_at"])
        return None

    reward = ReferralReward.objects.create(
        referrer=referral.referrer,
        referral=referral,
        payment=payment,
        amount=payment.amount * REWARD_PERCENT // 100,
        available_at=timezone.now() + HOLD_PERIOD,
    )
    referral.status = Referral.Status.REWARDED
    referral.save(update_fields=["status", "updated_at"])
    return reward


def release_matured_rewards() -> int:
    """Move held rewards past the refund window into the wallet."""
    matured = ReferralReward.objects.filter(
        status=ReferralReward.Status.PENDING, available_at__lte=timezone.now()
    )
    matured.filter(payment__status=Payment.Status.REFUNDED).update(
        status=ReferralReward.Status.CANCELLED, updated_at=timezone.now()
    )
    return matured.update(status=ReferralReward.Status.AVAILABLE, updated_at=timezone.now())


# --- wallet and payouts ----------------------------------------------------

def wallet(user) -> dict[str, int]:
    """Totals in paise by state."""
    rows = (
        ReferralReward.objects.filter(referrer=user)
        .values("status")
        .annotate(total=Sum("amount"))
    )
    totals = {row["status"]: row["total"] for row in rows}
    Status = ReferralReward.Status
    return {
        "pending": totals.get(Status.PENDING, 0),
        "available": totals.get(Status.AVAILABLE, 0),
        "withdrawing": totals.get(Status.WITHDRAWN, 0),
        "paid": totals.get(Status.PAID, 0),
    }


def normalize_upi(upi_id: str) -> str:
    upi_id = (upi_id or "").strip().lower()
    if not UPI_PATTERN.fullmatch(upi_id):
        raise ReferralError("Enter a valid UPI ID, like yourname@okaxis.")
    return upi_id


@transaction.atomic
def request_payout(user, upi_id: str) -> Payout:
    upi_id = normalize_upi(upi_id)
    if Payout.objects.filter(user=user, status=Payout.Status.REQUESTED).exists():
        raise ReferralError("You already have a withdrawal in progress.")
    rewards = list(
        ReferralReward.objects.select_for_update().filter(
            referrer=user, status=ReferralReward.Status.AVAILABLE, payout__isnull=True
        )
    )
    amount = sum(reward.amount for reward in rewards)
    if amount < MIN_PAYOUT:
        raise ReferralError(f"You can withdraw once you have ₹{MIN_PAYOUT // 100} available.")

    payout = Payout(user=user, amount=amount)
    payout.set_upi_id(upi_id)
    try:
        with transaction.atomic():
            payout.save()
    except IntegrityError as exc:
        raise ReferralError("You already have a withdrawal in progress.") from exc
    ReferralReward.objects.filter(pk__in=[reward.pk for reward in rewards]).update(
        status=ReferralReward.Status.WITHDRAWN, payout=payout, updated_at=timezone.now()
    )
    return payout


@transaction.atomic
def mark_payout_paid(payout: Payout, *, reference: str) -> Payout:
    if payout.status != Payout.Status.REQUESTED:
        raise ReferralError("Only requested withdrawals can be marked as paid.")
    if not (reference or "").strip():
        raise ReferralError("Add the UPI transaction reference (UTR) first.")
    payout.status = Payout.Status.PAID
    payout.reference = reference.strip()
    payout.processed_at = timezone.now()
    payout.save(update_fields=["status", "reference", "processed_at", "updated_at"])
    payout.rewards.update(status=ReferralReward.Status.PAID, updated_at=timezone.now())
    return payout


@transaction.atomic
def reject_payout(payout: Payout, *, note: str) -> Payout:
    """Refuse a withdrawal and put its earnings back in the wallet."""
    if payout.status != Payout.Status.REQUESTED:
        raise ReferralError("Only requested withdrawals can be rejected.")
    payout.status = Payout.Status.REJECTED
    payout.note = note[:200]
    payout.processed_at = timezone.now()
    payout.save(update_fields=["status", "note", "processed_at", "updated_at"])
    payout.rewards.update(
        status=ReferralReward.Status.AVAILABLE, payout=None, updated_at=timezone.now()
    )
    return payout


def mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    return f"{local[:1]}•••@{domain}"


# --- internals ---------------------------------------------------------------

def _hashes_for(user, request=None) -> set[str]:
    """Every hashed identifier we hold for ``user``."""
    hashes = {value_hash for _, value_hash in identity_signals(request, user)}
    hashes |= set(
        TrialFingerprint.objects.filter(claim__user=user).values_list("value_hash", flat=True)
    )
    hashes |= set(
        Payment.objects.filter(user=user)
        .exclude(method_hash="")
        .values_list("method_hash", flat=True)
    )
    code = ReferralCode.objects.filter(user=user).first()
    if code is not None:
        hashes |= set(code.device_hashes)
    return hashes
