"""
Translate between the automation builder form and the automation tables.

The builder offers a fixed shape - public reply, optional follow check, DM -
so each step type appears at most once and steps are matched by type when
an automation is edited. Runs already waiting on a step keep their place.
"""

from __future__ import annotations

import re

from django.db import transaction

from ..models import (
    DEFAULT_FOLLOW_PROMPT,
    DEFAULT_NOT_FOLLOWING_MESSAGE,
    Automation,
    AutomationKeyword,
    AutomationStep,
)

Trigger = Automation.TriggerType
Scope = Automation.PostScope
StepType = AutomationStep.StepType

DEFAULT_PUBLIC_REPLIES = (
    "Sent! Check your DMs 📩",
    "Just sent it to your inbox ✨",
    "Done! Take a look in your DMs 🙌",
)
DEFAULT_FOLLOW_BUTTON = "I'm following"

AUDIENCE_EVERYONE = "everyone"
AUDIENCE_FOLLOWERS = "followers"
AUDIENCE_FOLLOW_FIRST = "follow_first"
AUDIENCE_CHOICES = (
    (AUDIENCE_EVERYONE, "Everyone"),
    (AUDIENCE_FOLLOWERS, "Only my followers"),
    (AUDIENCE_FOLLOW_FIRST, "Ask to follow first"),
)
DEFAULT_MAX_FOLLOW_CHECKS = 3
#: Triggers that are filtered by keywords.
KEYWORD_TRIGGERS = (Trigger.COMMENT, Trigger.DM_KEYWORD, Trigger.STORY_REPLY)

STEP_LABELS = {
    StepType.PUBLIC_REPLY: "Public reply",
    StepType.ASK_TO_FOLLOW: "Follow check",
    StepType.SEND_DM: "DM",
}


def split_keywords(raw: str | None) -> list[str]:
    """Comma- or newline-separated keywords, trimmed, de-duplicated ignoring case."""
    seen: set[str] = set()
    words = []
    for word in re.split(r"[,\n]", raw or ""):
        word = " ".join(word.split())
        if word and word.casefold() not in seen:
            seen.add(word.casefold())
            words.append(word)
    return words


def split_lines(raw: str | None) -> list[str]:
    return [line.strip() for line in (raw or "").splitlines() if line.strip()]


def default_initial() -> dict:
    return {
        "trigger_type": Trigger.COMMENT,
        "post_scope": Scope.ALL_POSTS,
        "public_reply_enabled": False,
        "public_replies": "\n".join(DEFAULT_PUBLIC_REPLIES),
        "audience": AUDIENCE_EVERYONE,
        "follow_prompt": DEFAULT_FOLLOW_PROMPT,
        "follow_button": DEFAULT_FOLLOW_BUTTON,
        "not_following_text": DEFAULT_NOT_FOLLOWING_MESSAGE,
        "once_per_contact": True,
        "go_live": True,
    }


@transaction.atomic
def save_automation(
    owner, data: dict, *, automation: Automation | None = None, go_live: bool
) -> Automation:
    """Create or update an automation from ``AutomationBuilderForm.cleaned_data``."""
    is_new = automation is None
    automation = automation or Automation(owner=owner)
    trigger = data["trigger_type"]

    automation.account = data["account"]
    automation.name = data["name"]
    automation.trigger_type = trigger
    automation.post_scope = Scope.ALL_POSTS
    if trigger == Trigger.COMMENT:
        automation.post_scope = data.get("post_scope") or Scope.ALL_POSTS
    automation.once_per_contact = bool(data.get("once_per_contact"))
    automation.audience = (
        Automation.Audience.FOLLOWERS
        if data.get("audience") == AUDIENCE_FOLLOWERS
        else Automation.Audience.EVERYONE
    )
    if go_live:
        automation.status = Automation.Status.ACTIVE
    elif is_new:
        automation.status = Automation.Status.DRAFT
    elif automation.status == Automation.Status.ACTIVE:
        automation.status = Automation.Status.PAUSED
    automation.save()

    if automation.post_scope == Scope.SPECIFIC_POSTS:
        automation.posts.set(data.get("posts") or [])
    else:
        automation.posts.clear()
    _replace_keywords(automation, data)
    _sync_steps(automation, _wanted_steps(data))
    return automation


def initial_from_automation(automation: Automation) -> dict:
    """Builder form values for an existing automation."""
    steps = {step.step_type: step.config for step in automation.steps.all()}
    rules = list(automation.keywords.all())
    public = steps.get(StepType.PUBLIC_REPLY)
    follow = steps.get(StepType.ASK_TO_FOLLOW)
    dm = steps.get(StepType.SEND_DM) or {}
    button = (dm.get("buttons") or [{}])[0]

    initial = default_initial()
    initial.update(
        account=automation.account_id,
        name=automation.name,
        trigger_type=automation.trigger_type,
        post_scope=automation.post_scope,
        posts=[post.pk for post in automation.posts.all()],
        reply_to_all=any(rule.match_type == AutomationKeyword.MatchType.ANY for rule in rules),
        keywords=", ".join(rule.keyword for rule in rules if rule.keyword),
        public_reply_enabled=public is not None,
        audience=AUDIENCE_FOLLOW_FIRST if follow is not None else automation.audience,
        dm_text=dm.get("text", ""),
        button_title=button.get("title", ""),
        button_url=button.get("url", ""),
        once_per_contact=automation.once_per_contact,
        go_live=automation.is_active,
    )
    if public is not None:
        initial["public_replies"] = "\n".join(public.get("variants") or [])
    if follow is not None:
        initial.update(
            follow_prompt=follow.get("prompt", ""),
            follow_button=follow.get("button") or DEFAULT_FOLLOW_BUTTON,
            not_following_text=follow.get("not_following_text", ""),
        )
    return initial


def describe_automation(automation: Automation) -> dict:
    """A plain-English summary for lists: what triggers it and what it does."""
    rules = list(automation.keywords.all())
    words = [rule.keyword for rule in rules if rule.keyword]
    everyone = not words or any(
        rule.match_type == AutomationKeyword.MatchType.ANY for rule in rules
    )
    quoted = ", ".join(f"“{word}”" for word in words[:3])
    if len(words) > 3:
        quoted += f" +{len(words) - 3} more"

    trigger = automation.trigger_type
    if trigger == Trigger.STORY_MENTION:
        text = "Someone mentions you in their story"
    elif trigger == Trigger.DM_KEYWORD:
        text = "Someone sends you a DM" if everyone else f"Someone DMs {quoted}"
    elif trigger == Trigger.STORY_REPLY:
        text = (
            "Someone replies to your story"
            if everyone
            else f"Someone replies {quoted} to your story"
        )
    else:
        text = "Someone comments" if everyone else f"Someone comments {quoted}"
        if automation.post_scope == Scope.SPECIFIC_POSTS:
            count = len(automation.posts.all())
            text += f" on {count} post{'s' if count != 1 else ''}"
        elif automation.post_scope == Scope.NEXT_POST:
            text += " on your next post"
        else:
            text += " on any post"

    actions = []
    for step in automation.steps.all():
        if step.step_type == StepType.SEND_DM and step.config.get("buttons"):
            actions.append("DM with link")
        else:
            actions.append(STEP_LABELS.get(step.step_type) or step.get_step_type_display())
    if automation.audience == Automation.Audience.FOLLOWERS:
        actions.insert(0, "Followers only")
    return {"trigger": text, "actions": actions}


# --- internals ---------------------------------------------------------------

def _replace_keywords(automation: Automation, data: dict) -> None:
    automation.keywords.all().delete()
    if automation.trigger_type not in KEYWORD_TRIGGERS:
        return
    if data.get("reply_to_all"):
        rules = [
            AutomationKeyword(
                automation=automation, keyword="", match_type=AutomationKeyword.MatchType.ANY
            )
        ]
    else:
        words = data.get("keyword_list") or split_keywords(data.get("keywords"))
        rules = [
            AutomationKeyword(
                automation=automation,
                keyword=word,
                match_type=AutomationKeyword.MatchType.WHOLE_WORD,
            )
            for word in words
        ]
    AutomationKeyword.objects.bulk_create(rules)


def _wanted_steps(data: dict) -> list[tuple[str, dict]]:
    steps: list[tuple[str, dict]] = []
    if data["trigger_type"] == Trigger.COMMENT and data.get("public_reply_enabled"):
        variants = data.get("public_reply_list") or split_lines(data.get("public_replies"))
        steps.append((StepType.PUBLIC_REPLY, {"variants": variants}))
    if data.get("audience") == AUDIENCE_FOLLOW_FIRST:
        steps.append((StepType.ASK_TO_FOLLOW, {
            "prompt": (data.get("follow_prompt") or DEFAULT_FOLLOW_PROMPT).strip(),
            "button": (data.get("follow_button") or DEFAULT_FOLLOW_BUTTON).strip(),
            "not_following_text": (
                data.get("not_following_text") or DEFAULT_NOT_FOLLOWING_MESSAGE
            ).strip(),
            "max_checks": DEFAULT_MAX_FOLLOW_CHECKS,
        }))
    dm = {"text": data["dm_text"].strip()}
    if data.get("button_url"):
        dm["buttons"] = [{"title": data["button_title"].strip(), "url": data["button_url"]}]
    steps.append((StepType.SEND_DM, dm))
    return steps


def _sync_steps(automation: Automation, wanted: list[tuple[str, dict]]) -> None:
    """Update steps in place, matched by type, then renumber them."""
    wanted_types = [step_type for step_type, _ in wanted]
    kept: dict[str, AutomationStep] = {}
    for step in automation.steps.order_by("order"):
        if step.step_type in wanted_types and step.step_type not in kept:
            kept[step.step_type] = step
        else:
            step.delete()

    # Park kept steps on high numbers first so renumbering never collides.
    for offset, step in enumerate(kept.values(), start=1):
        step.order = 1000 + offset
        step.save(update_fields=["order", "updated_at"])

    for position, (step_type, config) in enumerate(wanted, start=1):
        step = kept.get(step_type) or AutomationStep(automation=automation, step_type=step_type)
        step.order = position
        step.config = config
        step.clean()
        step.save()
