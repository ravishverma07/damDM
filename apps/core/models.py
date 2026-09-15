from __future__ import annotations

import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone


class TimeStampedModel(models.Model):
    """Abstract base giving every concrete model created_at / updated_at."""

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class AuditLog(models.Model):
    """
    Append-only record of security-relevant actions.

    Never stores tokens, passwords or raw payloads - only the shape of what
    happened, so it is safe to retain and export.
    """

    class Action(models.TextChoices):
        USER_REGISTERED = "user_registered", "User registered"
        USER_LOGIN = "user_login", "User login"
        USER_LOGIN_FAILED = "user_login_failed", "User login failed"
        USER_LOGOUT = "user_logout", "User logout"
        PASSWORD_RESET = "password_reset", "Password reset"
        ACCOUNT_DELETED = "account_deleted", "Account deleted"
        INSTAGRAM_CONNECTED = "instagram_connected", "Instagram connected"
        INSTAGRAM_DISCONNECTED = "instagram_disconnected", "Instagram disconnected"
        INSTAGRAM_TOKEN_REFRESHED = "instagram_token_refreshed", "Instagram token refreshed"
        AUTOMATION_CREATED = "automation_created", "Automation created"
        AUTOMATION_UPDATED = "automation_updated", "Automation updated"
        AUTOMATION_DELETED = "automation_deleted", "Automation deleted"
        SUBSCRIPTION_CHANGED = "subscription_changed", "Subscription changed"
        WEBHOOK_REJECTED = "webhook_rejected", "Webhook rejected"
        SOCIAL_LOGIN_LINKED = "social_login_linked", "Social login linked"
        DATA_DELETION_REQUESTED = "data_deletion_requested", "Data deletion requested"
        DATA_DELETION_COMPLETED = "data_deletion_completed", "Data deletion completed"

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
    )
    action = models.CharField(max_length=64, choices=Action.choices)
    target_type = models.CharField(max_length=64, blank=True)
    target_id = models.CharField(max_length=64, blank=True)
    # Small, non-sensitive key/value context only.
    context = models.JSONField(default=dict, blank=True)
    ip_hash = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "audit log"
        verbose_name_plural = "audit logs"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["action", "-created_at"]),
            models.Index(fields=["actor", "-created_at"]),
            models.Index(fields=["target_type", "target_id"]),
        ]

    def __str__(self) -> str:
        who = self.actor_id or "system"
        return f"{self.action} by {who} at {self.created_at:%Y-%m-%d %H:%M}"


class DataDeletionRequest(TimeStampedModel):
    """
    A platform data-deletion callback (required by Meta App Review).

    Fired when a person asks Meta to delete what an app holds about them. The
    ``confirmation_code`` is returned to Meta and shown on the public status
    page, so it must be unguessable. Only counts are kept once work is done.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    platform = models.CharField(max_length=20, default="instagram")
    # The person whose data must go - usually a contact, sometimes an owner.
    external_user_id = models.CharField(max_length=64, db_index=True)
    confirmation_code = models.CharField(max_length=64, unique=True, db_index=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    # e.g. {"contacts": 2, "messages": 41, "comments": 3}
    deleted_counts = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "data deletion request"
        verbose_name_plural = "data deletion requests"
        ordering = ("-created_at",)
        indexes = [models.Index(fields=["status", "-created_at"])]

    def __str__(self) -> str:
        return f"{self.platform} deletion {self.confirmation_code} ({self.status})"

    @classmethod
    def issue(cls, *, platform: str, external_user_id: str) -> DataDeletionRequest:
        return cls.objects.create(
            platform=platform,
            external_user_id=external_user_id,
            confirmation_code=secrets.token_urlsafe(24),
        )

    def mark_completed(self, **counts: int) -> None:
        self.status = self.Status.COMPLETED
        self.deleted_counts = counts
        self.completed_at = timezone.now()
        self.save(update_fields=["status", "deleted_counts", "completed_at", "updated_at"])


class Notification(models.Model):
    """An in-app message shown on the dashboard until the user dismisses it."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications"
    )
    kind = models.CharField(max_length=40)
    title = models.CharField(max_length=120)
    body = models.CharField(max_length=300, blank=True)
    action_label = models.CharField(max_length=40, blank=True)
    action_url = models.CharField(max_length=255, blank=True)
    # Makes creation idempotent: one notification per user and key.
    dedupe_key = models.CharField(max_length=100, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "notification"
        verbose_name_plural = "notifications"
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["user", "dedupe_key"],
                condition=~models.Q(dedupe_key=""),
                name="uniq_notification_per_user_and_key",
            ),
        ]
        indexes = [models.Index(fields=["user", "read_at"])]

    def __str__(self) -> str:
        return f"{self.title} → {self.user_id}"
