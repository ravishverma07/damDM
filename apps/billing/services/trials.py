"""
One free trial per person.

A trial is refused when anything identifying the person has had one before:
an Instagram account they connected, their Google account, their email (Gmail
dots and +tags ignored), this browser, and - once the AutoPay mandate is
approved - the UPI ID or card. Values are stored only as keyed hashes. IP
addresses are recorded as a soft signal for review but never block anyone,
because many people share one IP on mobile networks.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass

from django.conf import settings
from django.db import transaction

from apps.accounts.models import SocialLogin
from apps.instagram.models import ConnectedAccount, Platform

from ..models import Subscription, TrialClaim, TrialFingerprint

Kind = TrialFingerprint.Kind
GMAIL_DOMAINS = {"gmail.com", "googlemail.com"}
TRIAL_USED_MESSAGE = (
    "A free trial has already been used with this Instagram account, email, device or "
    "payment method."
)


@dataclass(frozen=True)
class Eligibility:
    eligible: bool
    #: "" when eligible, otherwise "no_instagram" or "used".
    reason: str = ""
    message: str = ""


def fingerprint(kind: str, value: str) -> str:
    key = settings.TRIAL_FINGERPRINT_KEY.encode()
    return hmac.new(key, f"{kind}:{value}".encode(), hashlib.sha256).hexdigest()


def normalize_email(email: str) -> str:
    local, _, domain = email.strip().lower().partition("@")
    local = local.split("+", 1)[0]
    if domain in GMAIL_DOMAINS:
        local, domain = local.replace(".", ""), "gmail.com"
    return f"{local}@{domain}"


def identity_signals(request, user) -> list[tuple[str, str]]:
    """Hashed identifiers for ``user`` in this browser, as (kind, hash) pairs."""
    raw = [(Kind.EMAIL, normalize_email(user.email))]
    raw += [
        (Kind.GOOGLE, uid)
        for uid in SocialLogin.objects.filter(
            user=user, provider=SocialLogin.Provider.GOOGLE
        ).values_list("provider_uid", flat=True)
    ]
    # Every Instagram account the user ever connected, so disconnecting doesn't reset it.
    raw += [
        (Kind.INSTAGRAM, account_id)
        for account_id in ConnectedAccount.objects.filter(
            owner=user, platform=Platform.INSTAGRAM
        ).values_list("external_account_id", flat=True)
    ]
    device_id = getattr(request, "device_id", "")
    if device_id:
        raw.append((Kind.DEVICE, device_id))
    return [(str(kind), fingerprint(kind, value)) for kind, value in dict.fromkeys(raw)]


def check_eligibility(request, user) -> Eligibility:
    if not ConnectedAccount.objects.filter(
        owner=user, status__in=ConnectedAccount.CLAIMED_STATUSES
    ).exists():
        return Eligibility(
            False, "no_instagram", "Connect your Instagram account to start your free trial."
        )
    hashes = [value_hash for _, value_hash in identity_signals(request, user)]
    if (
        TrialClaim.objects.filter(user=user).exists()
        or TrialFingerprint.objects.filter(value_hash__in=hashes).exists()
    ):
        return Eligibility(False, "used", TRIAL_USED_MESSAGE)
    return Eligibility(True)


def payment_fingerprint(payment: dict) -> str:
    """Hash of the UPI ID or saved card behind a Razorpay payment, if present."""
    method = str(payment.get("method") or "")
    if method == "upi" and payment.get("vpa"):
        value = str(payment["vpa"]).strip().lower()
    elif payment.get("card_id"):
        value = str(payment["card_id"])
    elif payment.get("token_id"):
        value = str(payment["token_id"])
    else:
        return ""
    return fingerprint(Kind.PAYMENT, f"{method}:{value}")


@transaction.atomic
def grant_trial(subscription: Subscription) -> TrialClaim:
    """Record the trial and every identifier gathered at checkout. Idempotent."""
    claim, created = TrialClaim.objects.get_or_create(
        subscription=subscription,
        defaults={"user": subscription.user, "ip_hash": subscription.trial_ip_hash},
    )
    if created:
        TrialFingerprint.objects.bulk_create(
            [
                TrialFingerprint(claim=claim, kind=kind, value_hash=value_hash)
                for kind, value_hash in subscription.trial_signals
            ],
            ignore_conflicts=True,
        )
    return claim


def payment_method_already_trialed(claim: TrialClaim, payment_hash: str) -> bool:
    return (
        TrialFingerprint.objects.filter(kind=Kind.PAYMENT, value_hash=payment_hash)
        .exclude(claim=claim)
        .exists()
    )


def record_payment_method(claim: TrialClaim, payment_hash: str) -> None:
    TrialFingerprint.objects.bulk_create(
        [TrialFingerprint(claim=claim, kind=Kind.PAYMENT, value_hash=payment_hash)],
        ignore_conflicts=True,
    )
