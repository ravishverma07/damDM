"""
Keyword matching.

Comment text arrives from an untrusted source, so it is normalised
defensively before any comparison: unicode-normalised, zero-width
characters stripped, whitespace collapsed.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

from apps.automations.models import Automation, AutomationKeyword

# Zero-width and directional marks people use to defeat naive matching.
_INVISIBLE = re.compile("[​-‏‪-‮⁠-⁤﻿]")
_WHITESPACE = re.compile(r"\s+")
MAX_COMMENT_LENGTH = 2200


def normalize(text: str | None) -> str:
    """Return a safe, comparable form of a raw comment."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text[:MAX_COMMENT_LENGTH])
    text = _INVISIBLE.sub("", text)
    return _WHITESPACE.sub(" ", text).strip()


def matches(rule: AutomationKeyword, comment_text: str | None) -> bool:
    """Evaluate one keyword rule against a comment or message."""
    text = normalize(comment_text)
    keyword = normalize(rule.keyword)

    if rule.match_type == AutomationKeyword.MatchType.ANY:
        return True
    if not keyword:
        return False

    if not rule.case_sensitive:
        text = text.casefold()
        keyword = keyword.casefold()

    match rule.match_type:
        case AutomationKeyword.MatchType.EXACT:
            return text == keyword
        case AutomationKeyword.MatchType.CONTAINS:
            return keyword in text
        case AutomationKeyword.MatchType.WHOLE_WORD:
            pattern = rf"(?<!\w){re.escape(keyword)}(?!\w)"
            return re.search(pattern, text) is not None
    return False


def automation_matches(
    automation: Automation,
    text: str | None,
    rules: Iterable[AutomationKeyword] | None = None,
) -> bool:
    """
    True when any of the automation's keyword rules matches ``text``.

    Keywordless triggers (story mentions) always match. An automation that
    needs keywords but has none never matches. Pass ``rules`` when they are
    already prefetched to avoid a query.
    """
    if not automation.requires_keywords:
        return True
    rules = automation.keywords.all() if rules is None else rules
    return any(matches(rule, text) for rule in rules)
