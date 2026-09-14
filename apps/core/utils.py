"""Small shared helpers."""

from __future__ import annotations

import hashlib
import secrets
import string
from datetime import date, datetime

from django.conf import settings
from django.utils import timezone

_SLUG_ALPHABET = string.ascii_lowercase + string.digits


def hash_ip(ip_address: str | None) -> str:
    """
    Return a salted SHA-256 hash of an IP address.

    Raw IPs are never stored; the hash exists only to estimate duplicate
    clicks. An empty input yields an empty string rather than a hash of "".
    """
    if not ip_address:
        return ""
    salted = f"{settings.IP_HASH_SALT}:{ip_address}".encode()
    return hashlib.sha256(salted).hexdigest()


def client_ip(request) -> str:
    """Best-effort client IP, honouring a single proxy hop."""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def random_slug(length: int = 8) -> str:
    """URL-safe random slug for tracked links."""
    return "".join(secrets.choice(_SLUG_ALPHABET) for _ in range(length))


def month_period(moment: datetime | date | None = None) -> tuple[date, date]:
    """
    Return the (start, end) dates of the calendar month containing ``moment``.

    Used as the default billing/usage period for accounts without an active
    provider subscription.
    """
    moment = moment or timezone.now()
    if isinstance(moment, datetime):
        moment = timezone.localtime(moment).date() if timezone.is_aware(moment) else moment.date()
    start = moment.replace(day=1)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start, end
