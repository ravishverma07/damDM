"""
Tell users about things that happened: an in-app notification on the
dashboard, a push notification on their devices, or both.

Every in-app Notification is also pushed (see ``signals.py``); ``push_only``
is for news that doesn't need to stay on the dashboard.
"""

from __future__ import annotations

import logging

from django.db import IntegrityError, transaction
from django.utils import timezone

from . import push
from .models import Notification, PushSubscription
from .queue import enqueue

logger = logging.getLogger("damdm.push")

#: Stop trying a device after this many failures in a row.
MAX_FAILURES = 5


def notify(
    user,
    *,
    kind: str,
    title: str,
    body: str = "",
    action_label: str = "",
    action_url: str = "",
    dedupe_key: str = "",
) -> Notification | None:
    """Create a dashboard notification (pushed automatically). Idempotent per dedupe_key."""
    if dedupe_key and Notification.objects.filter(user=user, dedupe_key=dedupe_key).exists():
        return None
    try:
        with transaction.atomic():
            return Notification.objects.create(
                user=user, kind=kind, title=title[:120], body=body[:300],
                action_label=action_label[:40], action_url=action_url[:255],
                dedupe_key=dedupe_key[:100],
            )
    except IntegrityError:
        return None


def push_only(user, *, title: str, body: str = "", url: str = "/dashboard/", tag: str = "") -> None:
    """Push to the user's devices without adding a dashboard notification."""
    if not push.is_configured():
        return
    from .tasks import send_push

    payload = {"title": title, "body": body, "url": url, "tag": tag}
    transaction.on_commit(lambda: enqueue(send_push, user.pk, payload))


def deliver(user_id: int, payload: dict) -> int:
    """Send a push to every device of ``user_id``. Returns how many accepted it."""
    message = push.Message(
        title=str(payload.get("title", ""))[:120],
        body=str(payload.get("body", ""))[:300],
        url=_same_site(str(payload.get("url") or "/dashboard/")),
        tag=str(payload.get("tag", ""))[:64],
    )
    sent = 0
    for subscription in PushSubscription.objects.filter(user_id=user_id):
        try:
            ok = push.send(subscription, message)
        except push.PushGone:
            subscription.delete()
            continue
        except Exception:  # never let one device break the others
            logger.exception("Push to subscription %s crashed", subscription.pk)
            ok = False
        if ok:
            sent += 1
            subscription.failure_count = 0
            subscription.last_sent_at = timezone.now()
            subscription.save(update_fields=["failure_count", "last_sent_at"])
        elif subscription.failure_count + 1 >= MAX_FAILURES:
            subscription.delete()
        else:
            subscription.failure_count += 1
            subscription.save(update_fields=["failure_count"])
    return sent


def _same_site(url: str) -> str:
    """Notifications only ever open pages on this site."""
    return url if url.startswith("/") and not url.startswith("//") else "/dashboard/"
