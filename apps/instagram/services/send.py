"""
Sending to Instagram: public comment replies, private replies and DMs.

Every call needs the connected account's own token. Instagram's rules are
enforced by the caller (one private reply per comment, the 24-hour messaging
window); here we only talk to the API and normalise the result.
"""

from __future__ import annotations

import json
import logging

from ..models import ConnectedAccount
from .graph import GraphAPIError, InstagramGraph

logger = logging.getLogger("damdm.instagram")

#: Instagram allows at most three buttons on a message template.
MAX_BUTTONS = 3
#: Instagram rejects longer message text.
MAX_TEXT_LENGTH = 1000


def reply_to_comment(account: ConnectedAccount, comment_id: str, message: str) -> str:
    """Post a public reply under a comment. Returns the new comment's id."""
    with _graph(account) as graph:
        data = graph.post(f"{comment_id}/replies", {"message": message[:2200]})
    return str(data.get("id") or "")


def private_reply(
    account: ConnectedAccount, comment_id: str, text: str, buttons: list | None = None
) -> str:
    """DM the person who wrote a comment. Instagram allows exactly one per comment."""
    return _send(account, {"comment_id": comment_id}, text, buttons)


def send_dm(
    account: ConnectedAccount, recipient_id: str, text: str, buttons: list | None = None
) -> str:
    """DM a contact inside the open messaging window."""
    return _send(account, {"id": recipient_id}, text, buttons)


def is_follower(account: ConnectedAccount, user_id: str) -> bool | None:
    """
    Whether ``user_id`` follows the account, or None when Instagram won't say.

    None means "unknown", never "no": the caller must not deny someone their
    link because of a failed lookup.
    """
    try:
        with _graph(account) as graph:
            data = graph.get(user_id, {"fields": "is_user_follow_business"})
    except GraphAPIError:
        logger.warning("Follow check failed for %s on account %s", user_id, account.pk)
        return None
    value = data.get("is_user_follow_business")
    return bool(value) if isinstance(value, bool) else None


def _graph(account: ConnectedAccount) -> InstagramGraph:
    return InstagramGraph(account.access_token)


def _send(account: ConnectedAccount, recipient: dict, text: str, buttons: list | None) -> str:
    message = _message(text, buttons)
    with _graph(account) as graph:
        data = graph.post(
            "me/messages",
            {"recipient": json.dumps(recipient), "message": json.dumps(message)},
        )
    return str(data.get("message_id") or data.get("id") or "")


def _message(text: str, buttons: list | None) -> dict:
    """A plain text message, or a button template when there are links."""
    text = text[:MAX_TEXT_LENGTH]
    links = [
        {"type": "web_url", "url": str(b["url"]), "title": str(b.get("title") or "Open")[:20]}
        for b in (buttons or [])
        if isinstance(b, dict) and str(b.get("url", "")).startswith(("https://", "http://"))
    ][:MAX_BUTTONS]
    if not links:
        return {"text": text}
    return {
        "attachment": {
            "type": "template",
            "payload": {"template_type": "button", "text": text, "buttons": links},
        }
    }


def postback_message(text: str, title: str, payload: str) -> dict:
    """A message whose button sends us a postback instead of opening a link."""
    return {
        "attachment": {
            "type": "template",
            "payload": {
                "template_type": "button",
                "text": text,
                "buttons": [{"type": "postback", "title": title[:20], "payload": payload[:1000]}],
            },
        }
    }


def send_postback_prompt(
    account: ConnectedAccount, recipient: dict, text: str, title: str, payload: str
) -> str:
    """Send a message with a button that replies to us (e.g. "I'm following")."""
    with _graph(account) as graph:
        data = graph.post(
            "me/messages",
            {
                "recipient": json.dumps(recipient),
                "message": json.dumps(postback_message(text, title, payload)),
            },
        )
    return str(data.get("message_id") or data.get("id") or "")
