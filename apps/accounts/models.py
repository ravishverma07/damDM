from __future__ import annotations

import secrets
from datetime import timedelta

from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.utils import timezone

from apps.core.models import TimeStampedModel

from .managers import UserManager

OAUTH_STATE_TTL = timedelta(minutes=15)


class User(AbstractBaseUser, PermissionsMixin):
    """Creator account. Email is the login identifier; there is no username."""

    class AccountStatus(models.TextChoices):
        ACTIVE = "active", "Active"
        DELETED = "deleted", "Deleted"

    class Onboarding(models.TextChoices):
        SIGNED_UP = "signed_up", "Signed up"
        INSTAGRAM_CONNECTED = "instagram_connected", "Instagram connected"
        AUTOMATION_CREATED = "automation_created", "Automation created"
        COMPLETED = "completed", "Completed"

    email = models.EmailField(unique=True, db_index=True)
    full_name = models.CharField(max_length=150, blank=True)
    avatar_url = models.URLField(max_length=1000, blank=True)
    # IANA name, e.g. "Asia/Kolkata"; used for schedules and daily reports.
    timezone = models.CharField(max_length=64, default="UTC")

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    # True once an identity provider (Google) has proven the address.
    email_verified = models.BooleanField(default=False)

    # Deleting an account from the app closes it; the row is kept (see close_account).
    account_status = models.CharField(
        max_length=20, choices=AccountStatus.choices, default=AccountStatus.ACTIVE, db_index=True
    )
    deleted_at = models.DateTimeField(null=True, blank=True)
    # The email the account had before it was deleted (the email field is freed for re-use).
    original_email = models.EmailField(blank=True)

    onboarding_state = models.CharField(
        max_length=32, choices=Onboarding.choices, default=Onboarding.SIGNED_UP
    )

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        verbose_name = "user"
        verbose_name_plural = "users"
        ordering = ("-created_at",)
        indexes = [models.Index(fields=["email_verified", "is_active"])]

    def __str__(self) -> str:
        return self.email

    def save(self, *args, **kwargs):
        self.email = self.email.lower().strip()
        return super().save(*args, **kwargs)

    def get_full_name(self) -> str:
        return self.full_name or self.email

    def get_short_name(self) -> str:
        return self.full_name.split(" ")[0] if self.full_name else self.email.split("@")[0]

    def advance_onboarding(self, state: str) -> None:
        """Move onboarding forward, never backwards."""
        order = [c for c, _ in User.Onboarding.choices]
        if order.index(state) > order.index(self.onboarding_state):
            self.onboarding_state = state
            self.save(update_fields=["onboarding_state", "updated_at"])


class SocialLogin(TimeStampedModel):
    """
    A third-party identity a user can sign in with ("Continue with Google").

    This only proves who the person is. It is deliberately separate from
    ``instagram.ConnectedAccount``, which holds the token used for messaging:
    someone may sign in with Google and connect several Instagram accounts.
    """

    class Provider(models.TextChoices):
        GOOGLE = "google", "Google"
        INSTAGRAM = "instagram", "Instagram"
        FACEBOOK = "facebook", "Facebook"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="social_logins")
    provider = models.CharField(max_length=20, choices=Provider.choices)
    # Google `sub` / Instagram user id - stable, unlike email or username.
    provider_uid = models.CharField(max_length=191)
    email = models.EmailField(blank=True)
    # Non-sensitive profile fields only (name, picture). Never tokens.
    extra_data = models.JSONField(default=dict, blank=True)
    last_login_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "social login"
        verbose_name_plural = "social logins"
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "provider_uid"], name="uniq_social_login_per_provider_uid"
            ),
            models.UniqueConstraint(
                fields=["user", "provider"], name="uniq_social_login_per_user_and_provider"
            ),
        ]
        indexes = [models.Index(fields=["provider", "email"])]

    def __str__(self) -> str:
        return f"{self.get_provider_display()} login for {self.user_id}"


class OAuthState(models.Model):
    """
    One-time OAuth `state` value for both sign-in and account-connect flows.

    For ``connect`` it is bound to the user who started the flow, so a stolen
    callback URL cannot attach someone else's account to an attacker's DamDM
    account. ``login`` flows start anonymous, so ``user`` is empty.
    """

    class Purpose(models.TextChoices):
        LOGIN = "login", "Sign in"
        CONNECT = "connect", "Connect account"

    state = models.CharField(max_length=64, unique=True, db_index=True)
    provider = models.CharField(max_length=20, choices=SocialLogin.Provider.choices)
    purpose = models.CharField(max_length=20, choices=Purpose.choices, default=Purpose.CONNECT)
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="oauth_states", null=True, blank=True
    )
    redirect_to = models.CharField(max_length=255, blank=True)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "OAuth state"
        verbose_name_plural = "OAuth states"
        ordering = ("-created_at",)
        constraints = [
            models.CheckConstraint(
                condition=models.Q(purpose="login") | models.Q(user__isnull=False),
                name="oauth_state_connect_requires_user",
            ),
        ]
        indexes = [models.Index(fields=["user", "consumed_at"])]

    def __str__(self) -> str:
        return f"{self.provider} {self.purpose} state for {self.user_id or 'anonymous'}"

    @classmethod
    def issue(
        cls, *, provider: str, purpose: str = Purpose.CONNECT, user=None, redirect_to: str = ""
    ) -> OAuthState:
        return cls.objects.create(
            state=secrets.token_urlsafe(32),
            provider=provider,
            purpose=purpose,
            user=user,
            redirect_to=redirect_to,
            expires_at=timezone.now() + OAUTH_STATE_TTL,
        )

    @property
    def is_valid(self) -> bool:
        return self.consumed_at is None and self.expires_at > timezone.now()

    def consume(self) -> None:
        self.consumed_at = timezone.now()
        self.save(update_fields=["consumed_at"])
