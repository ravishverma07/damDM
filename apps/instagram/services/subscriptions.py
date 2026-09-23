"""
Telling Instagram to send us webhooks for a connected account.

Registering the callback URL in the Meta dashboard is not enough: each
account must also subscribe itself, with its own token.
"""

from __future__ import annotations

import logging

from ..models import ConnectedAccount
from .graph import GraphAPIError, InstagramGraph

logger = logging.getLogger("damdm.instagram")

#: What we ask Instagram to notify us about.
SUBSCRIBED_FIELDS = (
    "comments",
    "live_comments",
    "messages",
    "messaging_postbacks",
    "messaging_seen",
    "message_reactions",
    "mentions",
)


def subscribe(account: ConnectedAccount) -> bool:
    """Subscribe ``account`` to webhooks. Returns whether Instagram accepted it."""
    if not account.has_usable_token():
        return False
    try:
        with InstagramGraph(account.access_token) as graph:
            response = graph.post(
                "me/subscribed_apps", {"subscribed_fields": ",".join(SUBSCRIBED_FIELDS)}
            )
    except GraphAPIError:
        logger.exception("Could not subscribe account %s to webhooks", account.pk)
        return False

    subscribed = bool(response.get("success", True))
    if account.webhooks_subscribed != subscribed:
        account.webhooks_subscribed = subscribed
        account.save(update_fields=["webhooks_subscribed", "updated_at"])
    return subscribed


def subscribed_fields(account: ConnectedAccount) -> list[str]:
    """What Instagram thinks this account is subscribed to (for diagnosis)."""
    with InstagramGraph(account.access_token) as graph:
        data = graph.get("me/subscribed_apps").get("data") or []
    return [field for item in data for field in (item.get("subscribed_fields") or [])]
