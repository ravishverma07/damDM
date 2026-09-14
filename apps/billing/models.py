from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F
from django.utils import timezone

from apps.core.models import TimeStampedModel
from apps.core.utils import month_period

TRIAL_GRACE = timedelta(days=1)


class Plan(TimeStampedModel):
    """A subscription tier. Limits here are the single source of truth."""

    name = models.CharField(max_length=60)
    slug = models.SlugField(max_length=60, unique=True)
    monthly_price = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    currency = models.CharField(max_length=3, default="INR")

    monthly_automation_limit = models.PositiveIntegerField(
        default=1, help_text="0 means unlimited."
    )
    monthly_message_limit = models.PositiveIntegerField(
        default=100, help_text="Automated contacts per billing period. 0 means unlimited."
    )
    instagram_account_limit = models.PositiveIntegerField(default=1)

    analytics_enabled = models.BooleanField(default=False)
    follow_gate_enabled = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    display_order = models.PositiveSmallIntegerField(default=0)
    #: Free days before the first charge, for accounts that haven't had a trial.
    trial_days = models.PositiveSmallIntegerField(default=0)
    # Plan id at the billing provider (Razorpay plan_...).
    provider_price_id = models.CharField(max_length=128, blank=True)

    class Meta:
        verbose_name = "plan"
        verbose_name_plural = "plans"
        ordering = ("display_order", "monthly_price")
        indexes = [models.Index(fields=["is_active", "display_order"])]

    def __str__(self) -> str:
        if not self.monthly_price:
            return f"{self.name} (free)"
        return f"{self.name} ({self.currency} {self.monthly_price}/mo)"

    @property
    def is_free(self) -> bool:
        return self.monthly_price == Decimal("0.00")

    @property
    def unlimited_automations(self) -> bool:
        return self.monthly_automation_limit == 0

    @property
    def unlimited_messages(self) -> bool:
        return self.monthly_message_limit == 0

    def amount_in_minor_units(self) -> int:
        """Providers charge in the smallest currency unit (cents/paise)."""
        return int(self.monthly_price * 100)


class Subscription(TimeStampedModel):
    """A user's subscription at a billing provider."""

    class Provider(models.TextChoices):
        RAZORPAY = "razorpay", "Razorpay"
        MANUAL = "manual", "Manual / comped"

    class Status(models.TextChoices):
        INCOMPLETE = "incomplete", "Incomplete"
        TRIALING = "trialing", "Trialing"
        ACTIVE = "active", "Active"
        PAST_DUE = "past_due", "Past due"
        CANCELED = "canceled", "Canceled"
        EXPIRED = "expired", "Expired"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="subscriptions"
    )
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="subscriptions")
    provider = models.CharField(
        max_length=20, choices=Provider.choices, default=Provider.RAZORPAY
    )
    provider_customer_id = models.CharField(max_length=128, blank=True, db_index=True)
    provider_subscription_id = models.CharField(max_length=128, blank=True, db_index=True)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.INCOMPLETE
    )
    current_period_start = models.DateTimeField(null=True, blank=True)
    current_period_end = models.DateTimeField(null=True, blank=True)
    cancel_at_period_end = models.BooleanField(default=False)
    trial_ends_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    # upi / card / netbanking, from the mandate or latest payment.
    payment_method = models.CharField(max_length=20, blank=True)
    # Hashed trial fingerprints gathered at checkout, turned into a TrialClaim
    # once the mandate is approved: [[kind, value_hash], ...].
    trial_signals = models.JSONField(default=list, blank=True)
    trial_ip_hash = models.CharField(max_length=64, blank=True)

    class Meta:
        verbose_name = "subscription"
        verbose_name_plural = "subscriptions"
        ordering = ("-created_at",)
        constraints = [
            # One live subscription per user; historical rows stay queryable.
            models.UniqueConstraint(
                fields=["user"],
                condition=models.Q(status__in=["trialing", "active", "past_due"]),
                name="uniq_live_subscription_per_user",
            ),
            models.UniqueConstraint(
                fields=["provider", "provider_subscription_id"],
                condition=~models.Q(provider_subscription_id=""),
                name="uniq_provider_subscription_id",
            ),
        ]
        indexes = [models.Index(fields=["user", "status"])]

    def __str__(self) -> str:
        return f"{self.user.email} - {self.plan.name} ({self.status})"

    @property
    def is_live(self) -> bool:
        return self.status in {self.Status.TRIALING, self.Status.ACTIVE, self.Status.PAST_DUE}

    @property
    def has_access(self) -> bool:
        """
        Whether automations may run. A trial keeps access for a day past its end
        so a slightly late first-charge webhook doesn't pause anything. Past-due
        accounts keep access while Razorpay retries the charge.
        """
        if self.status == self.Status.TRIALING:
            return bool(self.trial_ends_at and self.trial_ends_at + TRIAL_GRACE > timezone.now())
        return self.status in {self.Status.ACTIVE, self.Status.PAST_DUE}

    @property
    def trial_days_left(self) -> int:
        if self.status != self.Status.TRIALING or not self.trial_ends_at:
            return 0
        return max(0, (self.trial_ends_at - timezone.now()).days + 1)


class BillingWebhookEvent(models.Model):
    """
    Provider webhook envelope, stored for idempotency.

    A subscription is only ever activated from a signature-verified webhook or
    a server-side lookup - never from anything the browser reports.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSED = "processed", "Processed"
        IGNORED = "ignored", "Ignored"
        FAILED = "failed", "Failed"

    provider = models.CharField(max_length=20, choices=Subscription.Provider.choices)
    provider_event_id = models.CharField(max_length=191, db_index=True)
    event_type = models.CharField(max_length=100, blank=True)
    payload = models.JSONField(default=dict)
    processing_status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    received_at = models.DateTimeField(auto_now_add=True, db_index=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True)

    class Meta:
        verbose_name = "billing webhook event"
        verbose_name_plural = "billing webhook events"
        ordering = ("-received_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "provider_event_id"],
                name="uniq_billing_event_per_provider",
            ),
        ]
        indexes = [models.Index(fields=["processing_status", "-received_at"])]

    def __str__(self) -> str:
        return f"{self.provider}:{self.event_type}:{self.provider_event_id}"


class UsageRecord(TimeStampedModel):
    """Per-user counters for one billing period; the basis of plan enforcement."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="usage_records"
    )
    period_start = models.DateField(db_index=True)
    period_end = models.DateField()

    comments_processed = models.PositiveIntegerField(default=0)
    private_replies_sent = models.PositiveIntegerField(default=0)
    messages_sent = models.PositiveIntegerField(default=0)
    link_clicks = models.PositiveIntegerField(default=0)
    # "Automated contacts" = distinct people messaged in the period; this is
    # the number plan limits are enforced against.
    unique_contacts = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "usage record"
        verbose_name_plural = "usage records"
        ordering = ("-period_start",)
        constraints = [
            models.UniqueConstraint(
                fields=["user", "period_start"],
                name="uniq_usage_per_user_and_period",
            ),
        ]
        indexes = [models.Index(fields=["user", "-period_start"])]

    def __str__(self) -> str:
        return f"{self.user.email} {self.period_start} - {self.period_end}"

    @classmethod
    def for_current_period(cls, user) -> UsageRecord:
        """Get or create the usage row covering right now."""
        start, end = month_period(timezone.now())
        record, _ = cls.objects.get_or_create(
            user=user, period_start=start, defaults={"period_end": end}
        )
        return record

    def increment(self, **counters: int) -> None:
        """Atomically bump one or more counters."""
        if not counters:
            return
        type(self).objects.filter(pk=self.pk).update(
            **{field: F(field) + amount for field, amount in counters.items()}
        )
        self.refresh_from_db(fields=list(counters))


class Payment(TimeStampedModel):
    """One charge attempt reported by the billing provider (monthly renewals)."""

    class Status(models.TextChoices):
        CAPTURED = "captured", "Paid"
        FAILED = "failed", "Failed"
        REFUNDED = "refunded", "Refunded"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="payments"
    )
    subscription = models.ForeignKey(
        Subscription, on_delete=models.SET_NULL, null=True, blank=True, related_name="payments"
    )
    provider = models.CharField(max_length=20, choices=Subscription.Provider.choices)
    provider_payment_id = models.CharField(max_length=64)
    #: In the smallest currency unit (paise).
    amount = models.PositiveIntegerField()
    currency = models.CharField(max_length=3, default="INR")
    status = models.CharField(max_length=20, choices=Status.choices)
    method = models.CharField(max_length=20, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "payment"
        verbose_name_plural = "payments"
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "provider_payment_id"], name="uniq_payment_per_provider"
            ),
        ]
        indexes = [models.Index(fields=["user", "-created_at"])]

    def __str__(self) -> str:
        return f"{self.provider_payment_id} {self.amount_display} {self.currency} ({self.status})"

    @property
    def amount_display(self) -> Decimal:
        return Decimal(self.amount) / 100


class TrialClaim(TimeStampedModel):
    """
    A free trial that was granted. Kept after the user deletes their account,
    so deleting and signing up again never earns a second trial.
    """

    class Status(models.TextChoices):
        ACTIVE = "active", "In trial"
        CONVERTED = "converted", "Converted to paid"
        REVOKED = "revoked", "Revoked (trial already used)"
        ENDED = "ended", "Ended without paying"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="trial_claims",
    )
    subscription = models.OneToOneField(
        Subscription, on_delete=models.SET_NULL, null=True, blank=True, related_name="trial_claim"
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    # Soft signal only: many people share one IP on mobile networks.
    ip_hash = models.CharField(max_length=64, blank=True, db_index=True)
    revoked_reason = models.CharField(max_length=200, blank=True)

    class Meta:
        verbose_name = "trial claim"
        verbose_name_plural = "trial claims"
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"Trial {self.pk} for {self.user_id or 'deleted user'} ({self.status})"


class TrialFingerprint(models.Model):
    """A keyed hash of something that identifies who used a trial."""

    class Kind(models.TextChoices):
        INSTAGRAM = "instagram_account", "Instagram account"
        GOOGLE = "google_account", "Google account"
        EMAIL = "email", "Email address"
        DEVICE = "device", "Browser"
        PAYMENT = "payment_method", "UPI ID or card"

    claim = models.ForeignKey(TrialClaim, on_delete=models.CASCADE, related_name="fingerprints")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    value_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "trial fingerprint"
        verbose_name_plural = "trial fingerprints"
        constraints = [
            models.UniqueConstraint(fields=["kind", "value_hash"], name="uniq_trial_fingerprint"),
        ]

    def __str__(self) -> str:
        return f"{self.kind}:{self.value_hash[:10]}…"
