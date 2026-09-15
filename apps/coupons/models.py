"""
Coupons that admins give to specific Instagram accounts.

A coupon lists the exact Instagram usernames allowed to use it. The holder
uses it when starting their plan: a 100% coupon adds free months after the
trial (the first AutoPay charge simply moves later); a smaller discount is
applied by a matching Razorpay Offer on the subscription.
"""

from __future__ import annotations

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from apps.core.models import TimeStampedModel


class Coupon(TimeStampedModel):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        ARCHIVED = "archived", "Archived"

    code = models.CharField(
        max_length=32, unique=True, blank=True,
        help_text="Leave blank to generate one.",
    )
    name = models.CharField(
        max_length=120, help_text="Internal label, e.g. “Followers – September”."
    )
    discount_percent = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(100)],
        help_text="1 to 100. 100 means those months are free.",
    )
    discount_months = models.PositiveSmallIntegerField(
        default=1,
        validators=[MinValueValidator(1), MaxValueValidator(24)],
        help_text="How many monthly payments get the discount.",
    )
    razorpay_offer_id = models.CharField(
        max_length=64, blank=True,
        help_text="Discounts under 100% only: the offer_… id of a Razorpay Offer with the same "
                  "percentage and number of cycles, created in the Razorpay Dashboard.",
    )
    expires_at = models.DateTimeField(help_text="The coupon can't be used after this.")
    notify_on_connect = models.BooleanField(
        default=True,
        help_text="Show a notification in DamDM when their Instagram account is connected.",
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    archived_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+",
    )

    class Meta:
        verbose_name = "coupon"
        verbose_name_plural = "coupons"
        ordering = ("-created_at",)
        constraints = [
            models.CheckConstraint(
                condition=models.Q(discount_percent__gte=1, discount_percent__lte=100),
                name="coupon_discount_between_1_and_100",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.code} ({self.summary})"

    @property
    def is_free(self) -> bool:
        return self.discount_percent == 100

    @property
    def is_usable(self) -> bool:
        return self.status == self.Status.ACTIVE and self.expires_at > timezone.now()

    @property
    def summary(self) -> str:
        months = f"{self.discount_months} month{'s' if self.discount_months != 1 else ''}"
        if self.is_free:
            return f"{months} free"
        return f"{self.discount_percent}% off for {months}"


class CouponRecipient(models.Model):
    """An Instagram username allowed to use a coupon (stored lowercase, without @)."""

    coupon = models.ForeignKey(Coupon, on_delete=models.CASCADE, related_name="recipients")
    instagram_username = models.CharField(max_length=30)
    notified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "coupon recipient"
        verbose_name_plural = "coupon recipients"
        constraints = [
            models.UniqueConstraint(
                fields=["coupon", "instagram_username"], name="uniq_coupon_recipient"
            ),
        ]
        indexes = [models.Index(fields=["instagram_username"])]

    def __str__(self) -> str:
        return f"@{self.instagram_username}"


class CouponRedemption(TimeStampedModel):
    """A coupon used to start a subscription. One per username, one per user."""

    coupon = models.ForeignKey(Coupon, on_delete=models.CASCADE, related_name="redemptions")
    recipient = models.ForeignKey(
        CouponRecipient, on_delete=models.CASCADE, related_name="redemptions"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="coupon_redemptions"
    )
    subscription = models.OneToOneField(
        "billing.Subscription", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="coupon_redemption",
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "coupon redemption"
        verbose_name_plural = "coupon redemptions"
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(fields=["coupon", "user"], name="uniq_coupon_per_user"),
            models.UniqueConstraint(fields=["recipient"], name="uniq_redemption_per_recipient"),
        ]

    def __str__(self) -> str:
        return f"{self.coupon.code} by {self.user_id}"
