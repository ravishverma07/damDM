from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.db import models

from apps.core.crypto import decrypt, encrypt
from apps.core.models import TimeStampedModel


class ReferralCode(TimeStampedModel):
    """A user's personal invite code, shared as /r/<code>/."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="referral_code"
    )
    code = models.CharField(max_length=16, unique=True, db_index=True)
    # Hashed browser ids seen on the referrer's own pages, to spot self-referrals.
    device_hashes = models.JSONField(default=list, blank=True)

    class Meta:
        verbose_name = "referral code"
        verbose_name_plural = "referral codes"

    def __str__(self) -> str:
        return self.code


class Referral(TimeStampedModel):
    """A new account that signed up through someone's invite link."""

    class Status(models.TextChoices):
        JOINED = "joined", "Joined"
        REWARDED = "rewarded", "Paid their first month"
        REJECTED = "rejected", "Not eligible"

    referrer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="referrals_made"
    )
    # One referrer per account, ever.
    referred = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="referred_by"
    )
    code = models.CharField(max_length=16)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.JOINED)
    rejected_reason = models.CharField(max_length=200, blank=True)

    class Meta:
        verbose_name = "referral"
        verbose_name_plural = "referrals"
        ordering = ("-created_at",)
        indexes = [models.Index(fields=["referrer", "-created_at"])]

    def __str__(self) -> str:
        return f"{self.referrer_id} referred {self.referred_id} ({self.status})"


class Payout(TimeStampedModel):
    """A withdrawal of referral earnings to the referrer's UPI ID."""

    class Status(models.TextChoices):
        REQUESTED = "requested", "Requested"
        PAID = "paid", "Paid"
        REJECTED = "rejected", "Rejected"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="payouts"
    )
    #: In paise.
    amount = models.PositiveIntegerField()
    encrypted_upi_id = models.TextField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.REQUESTED)
    # Bank/UPI transaction reference (UTR) once paid.
    reference = models.CharField(max_length=64, blank=True)
    note = models.CharField(max_length=200, blank=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "payout"
        verbose_name_plural = "payouts"
        ordering = ("-created_at",)
        constraints = [
            # One open withdrawal at a time per user.
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(status="requested"),
                name="uniq_open_payout_per_user",
            ),
        ]

    def __str__(self) -> str:
        return f"Payout {self.pk} ₹{self.amount_display} ({self.status})"

    @property
    def amount_display(self) -> Decimal:
        return Decimal(self.amount) / 100

    @property
    def upi_id(self) -> str:
        return decrypt(self.encrypted_upi_id)

    def set_upi_id(self, upi_id: str) -> None:
        self.encrypted_upi_id = encrypt(upi_id)


class ReferralReward(TimeStampedModel):
    """The referrer's share of a friend's first payment."""

    class Status(models.TextChoices):
        PENDING = "pending", "On hold"
        AVAILABLE = "available", "Available"
        WITHDRAWN = "withdrawn", "Withdrawal requested"
        PAID = "paid", "Paid out"
        CANCELLED = "cancelled", "Cancelled"

    referrer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="referral_rewards"
    )
    # One reward per referred friend, however many times they pay.
    referral = models.OneToOneField(Referral, on_delete=models.CASCADE, related_name="reward")
    payment = models.OneToOneField(
        "billing.Payment", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="referral_reward",
    )
    #: In paise.
    amount = models.PositiveIntegerField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    # Held until the refund window passes.
    available_at = models.DateTimeField()
    payout = models.ForeignKey(
        Payout, on_delete=models.SET_NULL, null=True, blank=True, related_name="rewards"
    )

    class Meta:
        verbose_name = "referral reward"
        verbose_name_plural = "referral rewards"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["referrer", "status"]),
            models.Index(fields=["status", "available_at"]),
        ]

    def __str__(self) -> str:
        return f"₹{self.amount_display} for {self.referrer_id} ({self.status})"

    @property
    def amount_display(self) -> Decimal:
        return Decimal(self.amount) / 100
