"""
Everything a connected account's audience does, stored as the long-term record.

``Contact`` is the hub: deleting one (e.g. from a platform data-deletion
callback) cascades to that person's comments, conversation, messages, tags
and follow checks. Automation models are referenced by string to keep the
import graph one-directional.
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.core.models import TimeStampedModel
from apps.instagram.models import ConnectedAccount, Post


class Contact(TimeStampedModel):
    """
    One person who interacted with a connected account (commented, DMed,
    replied to a story). Scoped per account: the same human talking to two
    DamDM accounts is two contacts, because platform user ids are app-scoped.
    """

    class Source(models.TextChoices):
        COMMENT = "comment", "Comment"
        DM = "dm", "Direct message"
        STORY_REPLY = "story_reply", "Story reply"
        STORY_MENTION = "story_mention", "Story mention"

    account = models.ForeignKey(
        ConnectedAccount, on_delete=models.CASCADE, related_name="contacts"
    )
    # Instagram-scoped id (IGSID); the messaging recipient id.
    external_user_id = models.CharField(max_length=64)
    username = models.CharField(max_length=150, blank=True)
    name = models.CharField(max_length=150, blank=True)
    profile_pic_url = models.URLField(max_length=1000, blank=True)
    follower_count = models.PositiveIntegerField(null=True, blank=True)

    # Last value returned by the follow API - never what the person claimed.
    is_following = models.BooleanField(null=True, blank=True)
    follow_checked_at = models.DateTimeField(null=True, blank=True)

    source = models.CharField(max_length=20, choices=Source.choices, default=Source.COMMENT)
    first_seen_at = models.DateTimeField(default=timezone.now)
    last_interaction_at = models.DateTimeField(default=timezone.now, db_index=True)
    # Last inbound DM; opens the platform's standard messaging window.
    last_inbound_at = models.DateTimeField(null=True, blank=True)

    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=32, blank=True)
    # Answers collected by automation steps, keyed by field name.
    custom_fields = models.JSONField(default=dict, blank=True)

    comments_count = models.PositiveIntegerField(default=0)
    messages_count = models.PositiveIntegerField(default=0)

    # Opted-out contacts must never receive automated messages again.
    opted_out_at = models.DateTimeField(null=True, blank=True)
    is_blocked = models.BooleanField(default=False)
    notes = models.TextField(blank=True)

    tags = models.ManyToManyField(
        "Tag", through="ContactTag", related_name="contacts", blank=True
    )

    class Meta:
        verbose_name = "contact"
        verbose_name_plural = "contacts"
        ordering = ("-last_interaction_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["account", "external_user_id"], name="uniq_contact_per_account"
            ),
        ]
        indexes = [
            models.Index(fields=["account", "-last_interaction_at"]),
            models.Index(fields=["account", "username"]),
            models.Index(fields=["external_user_id"]),
        ]

    def __str__(self) -> str:
        return f"@{self.username or self.external_user_id}"

    @property
    def opted_out(self) -> bool:
        return self.opted_out_at is not None

    @property
    def can_be_automated(self) -> bool:
        return not self.opted_out and not self.is_blocked

    @property
    def messaging_window_open(self) -> bool:
        """
        True while the platform's standard messaging window is open.

        Outbound messages are only permitted inside this window; the sender
        must fail closed rather than retry when it has shut.
        """
        if not self.last_inbound_at:
            return False
        window = timedelta(hours=settings.META_MESSAGING_WINDOW_HOURS)
        return timezone.now() - self.last_inbound_at <= window


class Tag(TimeStampedModel):
    """A label for segmenting contacts, e.g. "clicked link" or "lead"."""

    account = models.ForeignKey(ConnectedAccount, on_delete=models.CASCADE, related_name="tags")
    name = models.CharField(max_length=60)
    color = models.CharField(max_length=7, default="#6b7280")

    class Meta:
        verbose_name = "tag"
        verbose_name_plural = "tags"
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(fields=["account", "name"], name="uniq_tag_name_per_account"),
        ]

    def __str__(self) -> str:
        return self.name


class ContactTag(models.Model):
    contact = models.ForeignKey(Contact, on_delete=models.CASCADE, related_name="contact_tags")
    tag = models.ForeignKey(Tag, on_delete=models.CASCADE, related_name="contact_tags")
    # Set when an automation step applied the tag; empty for manual tagging.
    added_by_run = models.ForeignKey(
        "automations.AutomationRun", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="applied_tags",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "contact tag"
        verbose_name_plural = "contact tags"
        constraints = [
            models.UniqueConstraint(fields=["contact", "tag"], name="uniq_tag_per_contact"),
        ]

    def __str__(self) -> str:
        return f"{self.contact} #{self.tag}"


class Comment(TimeStampedModel):
    """
    A comment on a connected account's post - from the audience, or the
    owner's own reply (including DamDM's automated public replies).
    """

    account = models.ForeignKey(
        ConnectedAccount, on_delete=models.CASCADE, related_name="comments"
    )
    # Null until the post is synced; ``external_media_id`` is always known.
    post = models.ForeignKey(
        Post, on_delete=models.SET_NULL, null=True, blank=True, related_name="comments"
    )
    external_media_id = models.CharField(max_length=64, db_index=True)
    # Null for comments written by the account owner.
    contact = models.ForeignKey(
        Contact, on_delete=models.CASCADE, null=True, blank=True, related_name="comments"
    )
    external_comment_id = models.CharField(max_length=64)
    parent = models.ForeignKey(
        "self", on_delete=models.CASCADE, null=True, blank=True, related_name="replies"
    )
    text = models.TextField(blank=True)

    is_from_owner = models.BooleanField(default=False)
    is_automated_reply = models.BooleanField(default=False)
    is_hidden = models.BooleanField(default=False)
    deleted_at = models.DateTimeField(null=True, blank=True)

    # The automation this comment matched, if any.
    automation = models.ForeignKey(
        "automations.Automation", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="matched_comments",
    )
    public_replied_at = models.DateTimeField(null=True, blank=True)
    private_replied_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        verbose_name = "comment"
        verbose_name_plural = "comments"
        ordering = ("-received_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["account", "external_comment_id"], name="uniq_comment_per_account"
            ),
            # One automated public reply per comment, however many webhooks arrive.
            models.UniqueConstraint(
                fields=["parent"],
                condition=models.Q(is_automated_reply=True),
                name="uniq_automated_public_reply_per_comment",
            ),
        ]
        indexes = [
            models.Index(fields=["account", "-received_at"]),
            models.Index(fields=["post", "-received_at"]),
            models.Index(fields=["contact", "-received_at"]),
            models.Index(fields=["automation", "-received_at"]),
        ]

    def __str__(self) -> str:
        who = "owner" if self.is_from_owner else str(self.contact or "unknown")
        return f"{who}: {self.text[:40]}"

    @property
    def matched(self) -> bool:
        return self.automation_id is not None


class Conversation(TimeStampedModel):
    """
    The DM thread between a connected account and one contact.

    Flow progress lives on ``automations.AutomationRun``, not here, so one
    contact can be inside several automations without them overwriting each
    other.
    """

    class Mode(models.TextChoices):
        BOT = "bot", "Automations reply"
        HUMAN = "human", "Owner is handling it"

    account = models.ForeignKey(
        ConnectedAccount, on_delete=models.CASCADE, related_name="conversations"
    )
    contact = models.OneToOneField(
        Contact, on_delete=models.CASCADE, related_name="conversation"
    )
    external_thread_id = models.CharField(max_length=128, blank=True)
    mode = models.CharField(max_length=10, choices=Mode.choices, default=Mode.BOT)
    last_message_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_message_text = models.CharField(max_length=200, blank=True)
    unread_count = models.PositiveIntegerField(default=0)
    is_archived = models.BooleanField(default=False)

    class Meta:
        verbose_name = "conversation"
        verbose_name_plural = "conversations"
        ordering = ("-last_message_at",)
        indexes = [
            models.Index(fields=["account", "is_archived", "-last_message_at"]),
        ]

    def __str__(self) -> str:
        return f"{self.account} <-> {self.contact}"

    @property
    def automations_paused(self) -> bool:
        return self.mode == self.Mode.HUMAN


class Message(TimeStampedModel):
    """
    Every DM in either direction.

    Outbound rows double as the delivery outbox (``status``/``attempt_count``).
    ``comment`` is set only on the private reply sent in response to that
    comment; the platform allows exactly one, enforced here as a constraint.
    """

    class Direction(models.TextChoices):
        INBOUND = "inbound", "Inbound"
        OUTBOUND = "outbound", "Outbound"

    class SenderType(models.TextChoices):
        CONTACT = "contact", "Contact"
        AUTOMATION = "automation", "Automation"
        HUMAN = "human", "Owner via DamDM"
        # Sent by the owner from the native app; arrives as an echo webhook.
        ECHO = "echo", "Owner via platform app"

    class MessageType(models.TextChoices):
        TEXT = "text", "Text"
        IMAGE = "image", "Image"
        VIDEO = "video", "Video"
        AUDIO = "audio", "Audio"
        FILE = "file", "File"
        SHARE = "share", "Shared post"
        STORY_REPLY = "story_reply", "Story reply"
        STORY_MENTION = "story_mention", "Story mention"
        REACTION = "reaction", "Reaction"
        POSTBACK = "postback", "Button tap"
        QUICK_REPLY = "quick_reply", "Quick reply"
        TEMPLATE = "template", "Template"

    class Status(models.TextChoices):
        RECEIVED = "received", "Received"
        PENDING = "pending", "Pending"
        PROCESSING = "processing", "Processing"
        SENT = "sent", "Sent"
        DELIVERED = "delivered", "Delivered"
        READ = "read", "Read"
        FAILED = "failed", "Failed (retryable)"
        PERMANENT_FAILURE = "permanent_failure", "Permanent failure"

    account = models.ForeignKey(
        ConnectedAccount, on_delete=models.CASCADE, related_name="messages"
    )
    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="messages"
    )
    contact = models.ForeignKey(Contact, on_delete=models.CASCADE, related_name="messages")

    direction = models.CharField(max_length=10, choices=Direction.choices)
    sender_type = models.CharField(max_length=12, choices=SenderType.choices)
    message_type = models.CharField(
        max_length=20, choices=MessageType.choices, default=MessageType.TEXT
    )
    # Platform message id (Meta `mid`); blank until an outbound send succeeds.
    external_message_id = models.CharField(max_length=191, blank=True)

    text = models.TextField(blank=True)
    # [{"type": "image", "url": "...", ...}] and outbound buttons/quick replies.
    attachments = models.JSONField(default=list, blank=True)
    postback_payload = models.CharField(max_length=1000, blank=True)

    reply_to = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="replies"
    )
    comment = models.ForeignKey(
        Comment, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="private_replies",
    )
    automation_run = models.ForeignKey(
        "automations.AutomationRun", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="messages",
    )
    step = models.ForeignKey(
        "automations.AutomationStep", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="messages",
    )
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="sent_messages",
    )

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    attempt_count = models.PositiveIntegerField(default=0)
    last_error = models.TextField(blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    # The contact unsent the message; text is cleared, the row is kept.
    deleted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "message"
        verbose_name_plural = "messages"
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=["account", "external_message_id"],
                condition=~models.Q(external_message_id=""),
                name="uniq_message_id_per_account",
            ),
            # The platform permits a single private reply per comment; enforced
            # in the database so a duplicated webhook can never send a second.
            models.UniqueConstraint(
                fields=["comment"],
                condition=models.Q(comment__isnull=False),
                name="uniq_private_reply_per_comment",
            ),
        ]
        indexes = [
            models.Index(fields=["conversation", "-created_at"]),
            models.Index(fields=["account", "-created_at"]),
            models.Index(fields=["status", "-created_at"]),
            models.Index(fields=["automation_run", "-created_at"]),
        ]

    def __str__(self) -> str:
        arrow = "<-" if self.direction == self.Direction.INBOUND else "->"
        return f"{arrow} {self.contact} [{self.status}] {self.text[:30]}"

    def mark_sent(self, external_message_id: str = "") -> None:
        self.status = self.Status.SENT
        self.external_message_id = external_message_id
        self.sent_at = timezone.now()
        self.last_error = ""
        self.save(
            update_fields=["status", "external_message_id", "sent_at", "last_error", "updated_at"]
        )

    def mark_failed(self, error: str, permanent: bool = False) -> None:
        self.status = self.Status.PERMANENT_FAILURE if permanent else self.Status.FAILED
        self.last_error = error[:2000]
        self.save(update_fields=["status", "last_error", "updated_at"])


class FollowCheck(TimeStampedModel):
    """
    Result of an `is_user_follow_business` lookup.

    A contact tapping "I've Followed" is never treated as proof; only the
    value returned by the API is. Results are kept for debugging and analytics.
    """

    contact = models.ForeignKey(Contact, on_delete=models.CASCADE, related_name="follow_checks")
    automation_run = models.ForeignKey(
        "automations.AutomationRun", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="follow_checks",
    )
    is_following = models.BooleanField(null=True)
    api_error = models.TextField(blank=True)
    checked_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        verbose_name = "follow check"
        verbose_name_plural = "follow checks"
        ordering = ("-checked_at",)
        indexes = [
            models.Index(fields=["contact", "-checked_at"]),
            models.Index(fields=["is_following", "-checked_at"]),
        ]

    def __str__(self) -> str:
        state = {True: "following", False: "not following", None: "unknown"}[self.is_following]
        return f"{self.contact_id}: {state} @ {self.checked_at:%Y-%m-%d %H:%M}"
