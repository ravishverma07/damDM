"""
Connected channel accounts, their posts, and raw webhook envelopes.

Instagram is the only live platform, but every model carries ``platform`` so
WhatsApp and Telegram can be added without a parallel set of tables.
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.crypto import TokenDecryptionError, decrypt, encrypt
from apps.core.models import TimeStampedModel

# Refresh long-lived tokens once they are inside this window of expiring.
TOKEN_REFRESH_WINDOW = timedelta(days=7)
WEBHOOK_PAYLOAD_RETENTION = timedelta(days=30)


class Platform(models.TextChoices):
    INSTAGRAM = "instagram", "Instagram"
    WHATSAPP = "whatsapp", "WhatsApp"
    TELEGRAM = "telegram", "Telegram"


class ConnectedAccount(TimeStampedModel):
    """
    A messaging account a user connected to DamDM (an Instagram professional
    account today).

    The access token is stored encrypted and is never exposed through
    serializers, admin, logs or API responses.
    """

    class AccountType(models.TextChoices):
        BUSINESS = "BUSINESS", "Business"
        CREATOR = "MEDIA_CREATOR", "Creator"
        PERSONAL = "PERSONAL", "Personal"
        UNKNOWN = "UNKNOWN", "Unknown"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        EXPIRED = "expired", "Token expired"
        ERROR = "error", "Error"
        REVOKED = "revoked", "Revoked by user"
        DISCONNECTED = "disconnected", "Disconnected"

    #: Statuses in which the account is still claimed by its owner.
    CLAIMED_STATUSES = (Status.ACTIVE, Status.EXPIRED, Status.ERROR)

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="connected_accounts"
    )
    platform = models.CharField(
        max_length=20, choices=Platform.choices, default=Platform.INSTAGRAM
    )
    # Instagram user id / WhatsApp phone-number id / Telegram bot id.
    external_account_id = models.CharField(max_length=64, db_index=True)
    # The same account's app-scoped id ("id" from /me). Deauthorize and
    # data-deletion callbacks may identify the account by either id.
    app_scoped_user_id = models.CharField(max_length=64, blank=True, db_index=True)
    username = models.CharField(max_length=150, blank=True)
    display_name = models.CharField(max_length=150, blank=True)
    account_type = models.CharField(
        max_length=32, choices=AccountType.choices, default=AccountType.UNKNOWN
    )
    profile_picture_url = models.URLField(max_length=1000, blank=True)
    followers_count = models.PositiveIntegerField(null=True, blank=True)

    encrypted_access_token = models.TextField(blank=True)
    token_expires_at = models.DateTimeField(null=True, blank=True)
    scopes = models.JSONField(default=list, blank=True)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    last_error = models.TextField(blank=True)
    webhooks_subscribed = models.BooleanField(default=False)
    connected_at = models.DateTimeField(default=timezone.now)
    last_synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "connected account"
        verbose_name_plural = "connected accounts"
        ordering = ("-created_at",)
        constraints = [
            # A webhook names only the platform account, so exactly one DamDM
            # user may hold it at a time or events could not be routed.
            models.UniqueConstraint(
                fields=["platform", "external_account_id"],
                condition=models.Q(status__in=["active", "expired", "error"]),
                name="uniq_claimed_account_per_platform",
            ),
        ]
        indexes = [
            models.Index(fields=["status", "token_expires_at"]),
            models.Index(fields=["platform", "external_account_id", "status"]),
            models.Index(fields=["owner", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.platform}:@{self.username or self.external_account_id}"

    # --- token handling -------------------------------------------------

    def set_access_token(self, token: str, expires_at=None) -> None:
        """Encrypt and store an access token. Never store plaintext."""
        self.encrypted_access_token = encrypt(token)
        if expires_at is not None:
            self.token_expires_at = expires_at

    @property
    def access_token(self) -> str:
        """Decrypt the stored token. Callers must never log the result."""
        return decrypt(self.encrypted_access_token)

    def has_usable_token(self) -> bool:
        if not self.encrypted_access_token:
            return False
        try:
            _ = self.access_token
        except TokenDecryptionError:
            return False
        return not self.is_token_expired

    @property
    def is_token_expired(self) -> bool:
        return bool(self.token_expires_at and self.token_expires_at <= timezone.now())

    @property
    def needs_refresh(self) -> bool:
        if not self.token_expires_at:
            return False
        return self.token_expires_at - timezone.now() <= TOKEN_REFRESH_WINDOW

    @property
    def is_claimed(self) -> bool:
        return self.status in self.CLAIMED_STATUSES

    @property
    def is_connected(self) -> bool:
        return self.status == self.Status.ACTIVE and not self.is_token_expired

    def disconnect(self, status: str | None = None) -> None:
        """Drop the token and mark the account inactive."""
        self.encrypted_access_token = ""
        self.token_expires_at = None
        self.status = status or self.Status.DISCONNECTED
        self.save(
            update_fields=["encrypted_access_token", "token_expires_at", "status", "updated_at"]
        )


class Post(TimeStampedModel):
    """A post, Reel or story belonging to a connected account."""

    class MediaType(models.TextChoices):
        IMAGE = "IMAGE", "Image"
        VIDEO = "VIDEO", "Video"
        CAROUSEL_ALBUM = "CAROUSEL_ALBUM", "Carousel"

    class ProductType(models.TextChoices):
        FEED = "FEED", "Feed"
        REELS = "REELS", "Reel"
        AD = "AD", "Ad"
        STORY = "STORY", "Story"

    account = models.ForeignKey(ConnectedAccount, on_delete=models.CASCADE, related_name="posts")
    external_media_id = models.CharField(max_length=64, db_index=True)
    media_type = models.CharField(
        max_length=32, choices=MediaType.choices, default=MediaType.IMAGE
    )
    product_type = models.CharField(
        max_length=32, choices=ProductType.choices, default=ProductType.FEED
    )
    caption = models.TextField(blank=True)
    permalink = models.URLField(max_length=1000, blank=True)
    thumbnail_url = models.URLField(max_length=1000, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    comments_count = models.PositiveIntegerField(default=0)
    like_count = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "post"
        verbose_name_plural = "posts"
        ordering = ("-published_at", "-created_at")
        constraints = [
            models.UniqueConstraint(
                fields=["account", "external_media_id"], name="uniq_post_per_account"
            ),
        ]
        indexes = [
            models.Index(fields=["account", "-published_at"]),
            models.Index(fields=["product_type", "is_active"]),
        ]

    def __str__(self) -> str:
        label = (self.caption or "").strip().replace("\n", " ")[:40]
        return f"{self.get_product_type_display()} {self.external_media_id} {label}".strip()

    @property
    def is_reel(self) -> bool:
        return self.product_type == self.ProductType.REELS


class WebhookEvent(models.Model):
    """
    Raw platform webhook envelope, stored idempotently before any processing.

    The endpoint saves this and returns 200 immediately; all API work happens
    later in Celery. Payloads contain third-party personal data, so they are
    blanked after ``WEBHOOK_PAYLOAD_RETENTION`` - the parsed rows in
    ``messaging`` are the long-term record.
    """

    class EventType(models.TextChoices):
        COMMENT = "comment", "Comment"
        MESSAGE = "message", "Message"
        MESSAGE_ECHO = "message_echo", "Message echo"
        POSTBACK = "postback", "Postback"
        REACTION = "reaction", "Reaction"
        READ = "read", "Read receipt"
        MENTION = "mention", "Mention"
        DEAUTHORIZE = "deauthorize", "Deauthorize"
        UNKNOWN = "unknown", "Unknown"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        PROCESSED = "processed", "Processed"
        IGNORED = "ignored", "Ignored"
        FAILED = "failed", "Failed"

    platform = models.CharField(
        max_length=20, choices=Platform.choices, default=Platform.INSTAGRAM
    )
    # Idempotency key derived from event type + platform object id.
    external_event_id = models.CharField(max_length=191)
    event_type = models.CharField(
        max_length=20, choices=EventType.choices, default=EventType.UNKNOWN
    )
    external_account_id = models.CharField(max_length=64, blank=True, db_index=True)
    # Resolved during processing; null when no claimed account matches.
    account = models.ForeignKey(
        ConnectedAccount, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="webhook_events",
    )
    payload = models.JSONField(default=dict)
    processing_status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    received_at = models.DateTimeField(auto_now_add=True, db_index=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True)

    class Meta:
        verbose_name = "webhook event"
        verbose_name_plural = "webhook events"
        ordering = ("-received_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["platform", "external_event_id"], name="uniq_webhook_event_per_platform"
            ),
        ]
        indexes = [
            models.Index(fields=["processing_status", "-received_at"]),
            models.Index(fields=["event_type", "-received_at"]),
            models.Index(fields=["external_account_id", "-received_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.event_type}:{self.external_event_id} ({self.processing_status})"

    def mark_processed(self, status: str = Status.PROCESSED, error: str = "") -> None:
        self.processing_status = status
        self.processed_at = timezone.now()
        self.error_message = error[:2000]
        self.save(update_fields=["processing_status", "processed_at", "error_message"])

    @classmethod
    def purge_old_payloads(cls, older_than: timedelta = WEBHOOK_PAYLOAD_RETENTION) -> int:
        """Blank payloads of settled events past retention. Returns rows touched."""
        cutoff = timezone.now() - older_than
        return (
            cls.objects.filter(received_at__lt=cutoff)
            .exclude(processing_status__in=[cls.Status.PENDING, cls.Status.PROCESSING])
            .exclude(payload={})
            .update(payload={})
        )
