"""
Receiving Instagram webhooks.

Meta signs every delivery with the app secret and expects a 200 within a few
seconds, so the endpoint only verifies the signature and stores each event.
Turning events into contacts, comments and messages happens in ``ingest``.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field

from django.conf import settings

from ..models import Platform, WebhookEvent

EventType = WebhookEvent.EventType

#: Change fields we act on; anything else is stored as unknown.
COMMENT_FIELDS = ("comments", "live_comments")
MENTION_FIELDS = ("mentions",)


class InvalidSignature(Exception):
    """The X-Hub-Signature-256 header doesn't match the body."""


@dataclass(frozen=True)
class ParsedEvent:
    """One thing that happened, pulled out of a webhook delivery."""

    event_type: str
    #: Unique per event, so a repeated delivery is stored once.
    event_id: str
    #: The Instagram account the event belongs to.
    account_id: str
    payload: dict = field(default_factory=dict)


def signature_report(body: bytes, header: str) -> dict:
    """Safe facts about a rejected delivery, for the audit log. No secrets."""
    prefix, _, signature = (header or "").partition("=")
    return {
        "header_present": bool(header),
        "algorithm": prefix[:16],
        "body_bytes": len(body or b""),
        "app_secret_set": bool(settings.META_APP_SECRET),
        "signature_length": len(signature),
    }


def token_report(params) -> dict:
    """Why a verification failed, without revealing either token."""
    given = str(params.get("hub.verify_token", ""))
    expected = settings.META_WEBHOOK_VERIFY_TOKEN
    return {
        "mode": str(params.get("hub.mode", ""))[:16],
        "token_given": bool(given),
        "token_expected_set": bool(expected),
        "same_length": len(given) == len(expected),
        "matches": bool(expected) and hmac.compare_digest(given, expected),
    }


def verify_signature(body: bytes, header: str) -> None:
    """Check Meta's ``X-Hub-Signature-256``. Raises InvalidSignature."""
    secret = settings.META_APP_SECRET
    prefix, _, signature = (header or "").partition("=")
    if not secret or prefix != "sha256" or not signature:
        raise InvalidSignature("Missing or malformed signature header.")
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise InvalidSignature("Signature does not match the body.")


def verify_subscription(params) -> str | None:
    """Meta's one-time GET check. Returns the challenge to echo back, or None."""
    token = settings.META_WEBHOOK_VERIFY_TOKEN
    challenge = str(params.get("hub.challenge", ""))
    matches = token and hmac.compare_digest(str(params.get("hub.verify_token", "")), token)
    return challenge if params.get("hub.mode") == "subscribe" and matches and challenge else None


def parse(payload: dict) -> list[ParsedEvent]:
    """Split a delivery into individual events, ignoring anything unusable."""
    events: list[ParsedEvent] = []
    for entry in payload.get("entry") or []:
        if not isinstance(entry, dict):
            continue
        account_id = str(entry.get("id") or "")
        timestamp = entry.get("time")
        for change in entry.get("changes") or []:
            parsed = _parse_change(change, account_id, timestamp)
            if parsed is not None:
                events.append(parsed)
        for item in entry.get("messaging") or []:
            parsed = _parse_messaging(item, account_id, timestamp)
            if parsed is not None:
                events.append(parsed)
    return events


def store(payload: dict) -> list[WebhookEvent]:
    """Save every new event in the delivery. Repeats are skipped."""
    stored = []
    for event in parse(payload):
        row, created = WebhookEvent.objects.get_or_create(
            platform=Platform.INSTAGRAM,
            external_event_id=event.event_id[:191],
            defaults={
                "event_type": event.event_type,
                "external_account_id": event.account_id[:64],
                "payload": event.payload,
            },
        )
        if created:
            stored.append(row)
    return stored


# --- parsing ------------------------------------------------------------------

def _parse_change(change, account_id: str, timestamp) -> ParsedEvent | None:
    if not isinstance(change, dict) or not isinstance(change.get("value"), dict):
        return None
    field_name = str(change.get("field") or "")
    value = dict(change["value"], _account_id=account_id, _time=timestamp)
    object_id = str(value.get("id") or "")
    if field_name in COMMENT_FIELDS and object_id:
        return ParsedEvent(EventType.COMMENT, f"comment:{object_id}", account_id, value)
    if field_name in MENTION_FIELDS and object_id:
        return ParsedEvent(EventType.MENTION, f"mention:{object_id}", account_id, value)
    if not object_id:
        return None
    return ParsedEvent(EventType.UNKNOWN, f"{field_name}:{object_id}", account_id, value)


def _parse_messaging(item, account_id: str, timestamp) -> ParsedEvent | None:
    if not isinstance(item, dict):
        return None
    value = dict(item, _account_id=account_id, _time=timestamp)
    sender = str((item.get("sender") or {}).get("id") or "")
    message = item.get("message") if isinstance(item.get("message"), dict) else None

    if message:
        mid = str(message.get("mid") or "")
        if not mid:
            return None
        is_echo = bool(message.get("is_echo")) or sender == account_id
        kind = EventType.MESSAGE_ECHO if is_echo else EventType.MESSAGE
        return ParsedEvent(kind, f"message:{mid}", account_id, value)

    for key, kind in (
        ("postback", EventType.POSTBACK),
        ("reaction", EventType.REACTION),
        ("read", EventType.READ),
    ):
        section = item.get(key)
        if isinstance(section, dict):
            marker = str(section.get("mid") or section.get("payload") or timestamp or "")
            return ParsedEvent(kind, f"{key}:{sender}:{marker}", account_id, value)
    return None
