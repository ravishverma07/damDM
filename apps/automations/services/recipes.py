"""Ready-made starting points shown when someone creates an automation."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..models import Automation

Trigger = Automation.TriggerType


@dataclass(frozen=True)
class Recipe:
    slug: str
    icon: str
    title: str
    description: str
    badge: str = ""
    #: Builder form values layered over ``builder.default_initial()``.
    initial: dict = field(default_factory=dict)


RECIPES = (
    Recipe(
        slug="comment-to-dm",
        icon="💬",
        title="Send a link when people comment",
        description=(
            "Someone comments a keyword on your post or Reel and instantly gets your link "
            "in their DMs."
        ),
        badge="Most popular",
        initial={
            "name": "Send link on comment",
            "trigger_type": Trigger.COMMENT,
            "keywords": "LINK",
            "public_reply_enabled": True,
            "dm_text": "Hey! 👋 Thanks for your comment. Here's the link you asked for:",
            "button_title": "Get the link",
        },
    ),
    Recipe(
        slug="follow-to-unlock",
        icon="🔒",
        title="Grow followers before sharing",
        description="Ask commenters to follow you first. Your link unlocks the moment they do.",
        badge="Grow faster",
        initial={
            "name": "Follow to unlock",
            "trigger_type": Trigger.COMMENT,
            "keywords": "GUIDE",
            "public_reply_enabled": True,
            "audience": "follow_first",
            "dm_text": "Thanks for following! 🎉 Here's your free guide:",
            "button_title": "Open the guide",
        },
    ),
    Recipe(
        slug="dm-keyword",
        icon="✉️",
        title="Auto-reply to a DM keyword",
        description=(
            "When someone messages you a keyword like PRICE, reply instantly with the details."
        ),
        initial={
            "name": "DM keyword reply",
            "trigger_type": Trigger.DM_KEYWORD,
            "keywords": "PRICE",
            "dm_text": "Hi! 💛 Thanks for reaching out. Here are our prices:",
            "button_title": "See prices",
        },
    ),
    Recipe(
        slug="story-reply",
        icon="↩️",
        title="Reply when people answer your story",
        description="Turn story replies into conversations with an instant, friendly DM.",
        initial={
            "name": "Story reply",
            "trigger_type": Trigger.STORY_REPLY,
            "reply_to_all": True,
            "dm_text": "Thanks for replying to my story! 🙌",
        },
    ),
    Recipe(
        slug="story-mention",
        icon="📣",
        title="Thank people who mention you",
        description="Send a thank-you DM whenever someone tags you in their story.",
        initial={
            "name": "Story mention thanks",
            "trigger_type": Trigger.STORY_MENTION,
            "dm_text": "Thank you so much for the mention! 💛 It means a lot.",
        },
    ),
    Recipe(
        slug="blank",
        icon="✨",
        title="Start from scratch",
        description="Build your own automation step by step.",
        initial={"name": "My automation"},
    ),
)

RECIPES_BY_SLUG = {recipe.slug: recipe for recipe in RECIPES}
