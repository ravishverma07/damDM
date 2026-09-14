from __future__ import annotations

from django.db import models
from django.db.models import F
from django.urls import reverse

from apps.automations.models import Automation, AutomationRun, AutomationStep
from apps.core.models import TimeStampedModel
from apps.core.utils import random_slug
from apps.instagram.models import ConnectedAccount


class TrackedLink(TimeStampedModel):
    """A short link wrapping an automation's destination URL, for click tracking."""

    automation = models.ForeignKey(
        Automation, on_delete=models.CASCADE, related_name="tracked_links"
    )
    # The step whose message carries this link, when known.
    step = models.ForeignKey(
        AutomationStep, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="tracked_links",
    )
    slug = models.SlugField(max_length=16, unique=True, db_index=True)
    destination_url = models.URLField(max_length=1000)
    click_count = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "tracked link"
        verbose_name_plural = "tracked links"
        ordering = ("-created_at",)
        indexes = [models.Index(fields=["automation", "-created_at"])]

    def __str__(self) -> str:
        return f"/l/{self.slug} -> {self.destination_url[:50]}"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = self._unique_slug()
        return super().save(*args, **kwargs)

    @classmethod
    def _unique_slug(cls) -> str:
        for _ in range(10):
            candidate = random_slug()
            if not cls.objects.filter(slug=candidate).exists():
                return candidate
        return random_slug(12)  # pragma: no cover - astronomically unlikely

    def get_absolute_url(self) -> str:
        return reverse("analytics:link-redirect", kwargs={"slug": self.slug})

    def register_click(self) -> None:
        """Atomically bump the denormalised counter."""
        type(self).objects.filter(pk=self.pk).update(click_count=F("click_count") + 1)


class LinkClick(models.Model):
    """
    A single click on a tracked link.

    Raw IP addresses are never stored - only a salted hash, used solely to
    estimate duplicate clicks.
    """

    tracked_link = models.ForeignKey(
        TrackedLink, on_delete=models.CASCADE, related_name="clicks"
    )
    # Raw recipient hint from the link's query string; unverified.
    recipient_scoped_id = models.CharField(max_length=64, blank=True, db_index=True)
    # Resolved attribution, set when the click can be tied to a contact/run.
    contact = models.ForeignKey(
        "messaging.Contact", on_delete=models.CASCADE, null=True, blank=True,
        related_name="link_clicks",
    )
    automation_run = models.ForeignKey(
        AutomationRun, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="link_clicks",
    )
    ip_hash = models.CharField(max_length=64, blank=True)
    user_agent = models.CharField(max_length=400, blank=True)
    clicked_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "link click"
        verbose_name_plural = "link clicks"
        ordering = ("-clicked_at",)
        indexes = [
            models.Index(fields=["tracked_link", "-clicked_at"]),
            models.Index(fields=["ip_hash", "-clicked_at"]),
        ]

    def __str__(self) -> str:
        return f"click on /l/{self.tracked_link.slug} at {self.clicked_at:%Y-%m-%d %H:%M}"


class DailyStat(TimeStampedModel):
    """
    Per-day rollup used by the dashboard so charts never scan raw event tables.

    Written by a Celery beat job; every counter is derived, never authoritative.
    """

    account = models.ForeignKey(
        ConnectedAccount, on_delete=models.CASCADE, related_name="daily_stats"
    )
    automation = models.ForeignKey(
        Automation, on_delete=models.CASCADE, related_name="daily_stats",
        null=True, blank=True,
    )
    date = models.DateField(db_index=True)

    comments_received = models.PositiveIntegerField(default=0)
    keyword_matches = models.PositiveIntegerField(default=0)
    runs_started = models.PositiveIntegerField(default=0)
    runs_completed = models.PositiveIntegerField(default=0)
    dms_sent = models.PositiveIntegerField(default=0)
    dms_failed = models.PositiveIntegerField(default=0)
    dms_received = models.PositiveIntegerField(default=0)
    public_replies_sent = models.PositiveIntegerField(default=0)
    follow_checks = models.PositiveIntegerField(default=0)
    follow_checks_passed = models.PositiveIntegerField(default=0)
    link_clicks = models.PositiveIntegerField(default=0)
    new_contacts = models.PositiveIntegerField(default=0)
    unique_contacts = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "daily stat"
        verbose_name_plural = "daily stats"
        ordering = ("-date",)
        constraints = [
            # NULLs compare as distinct in SQL, so the account-wide rollup
            # (automation IS NULL) needs its own constraint.
            models.UniqueConstraint(
                fields=["account", "automation", "date"],
                condition=models.Q(automation__isnull=False),
                name="uniq_daily_stat_per_automation",
            ),
            models.UniqueConstraint(
                fields=["account", "date"],
                condition=models.Q(automation__isnull=True),
                name="uniq_daily_stat_per_account",
            ),
        ]
        indexes = [models.Index(fields=["account", "-date"])]

    def __str__(self) -> str:
        return f"{self.account} {self.date}"

    @property
    def conversion_rate(self) -> float:
        """Link clicks as a share of keyword matches."""
        if not self.keyword_matches:
            return 0.0
        return round(self.link_clicks / self.keyword_matches * 100, 2)

    @property
    def delivery_rate(self) -> float:
        attempted = self.dms_sent + self.dms_failed
        if not attempted:
            return 0.0
        return round(self.dms_sent / attempted * 100, 2)
