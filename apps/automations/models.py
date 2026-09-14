"""
Automation configuration and execution.

An ``Automation`` is a trigger (which event, which posts, which keywords)
plus an ordered list of ``AutomationStep`` rows. Each time a contact sets it
off, an ``AutomationRun`` tracks that contact's progress through the steps.
Messaging models are referenced by string to keep imports one-directional.
"""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinLengthValidator
from django.db import models
from django.utils import timezone

from apps.core.models import TimeStampedModel
from apps.instagram.models import ConnectedAccount, Post

#: Instagram rejects DM text longer than this.
MAX_DM_LENGTH = 1000

DEFAULT_FOLLOW_PROMPT = (
    "Almost there! Follow the account and tap the button below and I'll send your link."
)
DEFAULT_NOT_FOLLOWING_MESSAGE = (
    "I couldn't see a follow yet. Follow the account, then tap Check Again."
)


class Automation(TimeStampedModel):
    """What sets an automation off, and the rules around re-triggering it."""

    class TriggerType(models.TextChoices):
        COMMENT = "comment", "Comment on a post"
        DM_KEYWORD = "dm_keyword", "Keyword in a DM"
        STORY_REPLY = "story_reply", "Reply to a story"
        STORY_MENTION = "story_mention", "Story mention"

    class PostScope(models.TextChoices):
        ALL_POSTS = "all_posts", "All posts"
        SPECIFIC_POSTS = "specific_posts", "Specific posts"
        NEXT_POST = "next_post", "Next post published"

    class Audience(models.TextChoices):
        EVERYONE = "everyone", "Everyone"
        FOLLOWERS = "followers", "Only followers"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        PAUSED = "paused", "Paused"
        ARCHIVED = "archived", "Archived"

    #: Triggers that fire without any keyword rows.
    KEYWORDLESS_TRIGGERS = (TriggerType.STORY_MENTION,)

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="automations"
    )
    account = models.ForeignKey(
        ConnectedAccount, on_delete=models.CASCADE, related_name="automations"
    )
    name = models.CharField(max_length=120, validators=[MinLengthValidator(2)])
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)

    trigger_type = models.CharField(
        max_length=20, choices=TriggerType.choices, default=TriggerType.COMMENT
    )
    post_scope = models.CharField(
        max_length=20, choices=PostScope.choices, default=PostScope.ALL_POSTS
    )
    posts = models.ManyToManyField(
        Post, through="AutomationPost", related_name="automations", blank=True
    )
    # Higher wins when several active automations match the same event.
    priority = models.SmallIntegerField(default=0)

    once_per_contact = models.BooleanField(
        default=True, help_text="Never start this automation twice for the same person."
    )
    cooldown_minutes = models.PositiveIntegerField(
        default=0, help_text="Minimum gap before the same person can trigger it again."
    )
    # Asking non-followers to follow first is an ask_to_follow step, not an audience.
    audience = models.CharField(
        max_length=20,
        choices=Audience.choices,
        default=Audience.EVERYONE,
        help_text="Only followers: people who don't follow the account are skipped.",
    )
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)

    # Denormalised for list views; the runs table is authoritative.
    runs_count = models.PositiveIntegerField(default=0)
    completed_runs_count = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "automation"
        verbose_name_plural = "automations"
        ordering = ("-priority", "-created_at")
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(starts_at__isnull=True)
                    | models.Q(ends_at__isnull=True)
                    | models.Q(ends_at__gt=models.F("starts_at"))
                ),
                name="automation_ends_after_it_starts",
            ),
        ]
        indexes = [
            models.Index(fields=["account", "status", "trigger_type"]),
            models.Index(fields=["owner", "status"]),
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.get_trigger_type_display()})"

    @property
    def is_active(self) -> bool:
        return self.status == self.Status.ACTIVE

    @property
    def applies_to_all_posts(self) -> bool:
        return self.post_scope == self.PostScope.ALL_POSTS

    @property
    def requires_keywords(self) -> bool:
        return self.trigger_type not in self.KEYWORDLESS_TRIGGERS

    def is_live_at(self, moment=None) -> bool:
        """Active and inside its optional schedule window."""
        moment = moment or timezone.now()
        if not self.is_active:
            return False
        if self.starts_at and moment < self.starts_at:
            return False
        return not (self.ends_at and moment >= self.ends_at)

    def activate(self) -> None:
        self.status = self.Status.ACTIVE
        self.save(update_fields=["status", "updated_at"])

    def pause(self) -> None:
        self.status = self.Status.PAUSED
        self.save(update_fields=["status", "updated_at"])


class AutomationKeyword(models.Model):
    """One keyword rule. An automation matches when any of its keywords does."""

    class MatchType(models.TextChoices):
        EXACT = "exact", "Exact match"
        CONTAINS = "contains", "Contains keyword"
        WHOLE_WORD = "whole_word", "Whole word"
        ANY = "any", "Anything"

    automation = models.ForeignKey(
        Automation, on_delete=models.CASCADE, related_name="keywords"
    )
    keyword = models.CharField(max_length=100, blank=True)
    match_type = models.CharField(
        max_length=20, choices=MatchType.choices, default=MatchType.WHOLE_WORD
    )
    case_sensitive = models.BooleanField(default=False)

    class Meta:
        verbose_name = "automation keyword"
        verbose_name_plural = "automation keywords"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(match_type="any") | ~models.Q(keyword=""),
                name="keyword_required_unless_match_any",
            ),
            models.UniqueConstraint(
                fields=["automation", "keyword", "match_type"],
                name="uniq_keyword_rule_per_automation",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.get_match_type_display()}: {self.keyword or '*'}"


class AutomationPost(models.Model):
    """Links an automation to a post when ``post_scope`` is specific."""

    automation = models.ForeignKey(
        Automation, on_delete=models.CASCADE, related_name="automation_posts"
    )
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="automation_posts")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "automation post"
        verbose_name_plural = "automation posts"
        constraints = [
            models.UniqueConstraint(fields=["automation", "post"], name="uniq_post_per_automation"),
        ]

    def __str__(self) -> str:
        return f"{self.automation_id} -> {self.post_id}"

    def clean(self) -> None:
        if self.post.account_id != self.automation.account_id:
            raise ValidationError("The post must belong to the automation's account.")


class AutomationStep(TimeStampedModel):
    """
    One action in an automation, run in ``order``.

    ``config`` holds the settings for the step type, for example::

        public_reply   {"variants": ["Sent! Check your DM", "Link sent, check DMs"]}
        send_dm        {"text": "Hey {first_name}!", "buttons": [{"title": "Open",
                        "url": "https://..."}]}
        ask_to_follow  {"prompt": "...", "button": "I've followed",
                        "not_following_text": "...", "max_checks": 3}
        collect_email  {"prompt": "What's your email?", "field": "email"}
        add_tag        {"tag_id": 12}
        delay          {"seconds": 30}

    Keeping these in JSON lets new step types (and other platforms' features)
    ship without migrations; ``clean`` validates the required keys.
    """

    class StepType(models.TextChoices):
        PUBLIC_REPLY = "public_reply", "Reply publicly to the comment"
        SEND_DM = "send_dm", "Send a DM"
        ASK_TO_FOLLOW = "ask_to_follow", "Ask to follow before continuing"
        WAIT_FOR_REPLY = "wait_for_reply", "Wait for a reply"
        COLLECT_EMAIL = "collect_email", "Ask for an email"
        ADD_TAG = "add_tag", "Tag the contact"
        DELAY = "delay", "Wait"
        HANDOFF = "handoff", "Hand over to a human"

    #: Keys each step type must define in ``config``.
    REQUIRED_CONFIG: dict[str, tuple[str, ...]] = {
        StepType.PUBLIC_REPLY: ("variants",),
        StepType.SEND_DM: ("text",),
        StepType.ASK_TO_FOLLOW: ("prompt",),
        StepType.WAIT_FOR_REPLY: (),
        StepType.COLLECT_EMAIL: ("prompt",),
        StepType.ADD_TAG: ("tag_id",),
        StepType.DELAY: ("seconds",),
        StepType.HANDOFF: (),
    }

    automation = models.ForeignKey(Automation, on_delete=models.CASCADE, related_name="steps")
    order = models.PositiveSmallIntegerField()
    step_type = models.CharField(max_length=20, choices=StepType.choices)
    config = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "automation step"
        verbose_name_plural = "automation steps"
        ordering = ("automation", "order")
        constraints = [
            models.UniqueConstraint(
                fields=["automation", "order"], name="uniq_step_order_per_automation"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.order}. {self.get_step_type_display()}"

    def clean(self) -> None:
        config = self.config if isinstance(self.config, dict) else {}
        missing = [k for k in self.REQUIRED_CONFIG.get(self.step_type, ()) if not config.get(k)]
        if missing:
            raise ValidationError({"config": f"Missing required settings: {', '.join(missing)}."})

        texts = [config.get("text"), config.get("prompt"), config.get("not_following_text")]
        if self.step_type == self.StepType.PUBLIC_REPLY:
            if not isinstance(config["variants"], list):
                raise ValidationError({"config": "Public reply variants must be a list."})
            texts.extend(config["variants"])
        if any(isinstance(t, str) and len(t) > MAX_DM_LENGTH for t in texts):
            raise ValidationError(
                {"config": f"Messages must be {MAX_DM_LENGTH} characters or fewer."}
            )

    def next_step(self) -> AutomationStep | None:
        return (
            AutomationStep.objects.filter(automation_id=self.automation_id, order__gt=self.order)
            .order_by("order")
            .first()
        )


class AutomationRun(TimeStampedModel):
    """
    One contact's pass through one automation.

    A contact may be inside several automations at once; each has its own
    run, so a second comment on another Reel never clobbers the first flow.
    """

    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        WAITING_FOLLOW = "waiting_follow", "Waiting for follow"
        WAITING_REPLY = "waiting_reply", "Waiting for reply"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        EXPIRED = "expired", "Expired (messaging window closed)"
        SKIPPED = "skipped", "Skipped (not a follower, limit, opt-out or cooldown)"

    LIVE_STATUSES = (Status.RUNNING, Status.WAITING_FOLLOW, Status.WAITING_REPLY)

    automation = models.ForeignKey(Automation, on_delete=models.CASCADE, related_name="runs")
    contact = models.ForeignKey(
        "messaging.Contact", on_delete=models.CASCADE, related_name="automation_runs"
    )
    trigger_comment = models.ForeignKey(
        "messaging.Comment", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="triggered_runs",
    )
    trigger_message = models.ForeignKey(
        "messaging.Message", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="triggered_runs",
    )
    current_step = models.ForeignKey(
        AutomationStep, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RUNNING)
    follow_check_count = models.PositiveIntegerField(default=0)
    # Values collected while the flow ran, e.g. {"email": "a@b.com"}.
    context = models.JSONField(default=dict, blank=True)
    last_error = models.TextField(blank=True)

    started_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    # When a waiting run gives up (tracks the messaging window).
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "automation run"
        verbose_name_plural = "automation runs"
        ordering = ("-started_at",)
        constraints = [
            # A duplicated webhook must never start a second live run.
            models.UniqueConstraint(
                fields=["automation", "contact"],
                condition=models.Q(status__in=["running", "waiting_follow", "waiting_reply"]),
                name="uniq_live_run_per_automation_and_contact",
            ),
        ]
        indexes = [
            models.Index(fields=["automation", "status"]),
            models.Index(fields=["contact", "-started_at"]),
            models.Index(fields=["status", "expires_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.automation_id} for {self.contact_id} ({self.status})"

    @property
    def is_live(self) -> bool:
        return self.status in self.LIVE_STATUSES

    def finish(self, status: str = Status.COMPLETED, error: str = "") -> None:
        self.status = status
        self.completed_at = timezone.now()
        self.last_error = error[:2000]
        self.save(update_fields=["status", "completed_at", "last_error", "updated_at"])
