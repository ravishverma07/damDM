"""Request middleware shared across apps."""

from __future__ import annotations

import secrets
import zoneinfo

from django.conf import settings
from django.utils import timezone

DEVICE_COOKIE = "damdm_device"
DEVICE_COOKIE_MAX_AGE = 60 * 60 * 24 * 730  # two years


class DeviceIdMiddleware:
    """
    Give each browser a random, long-lived id, exposed as ``request.device_id``.

    It identifies nothing about the person; it is one of the signals used to
    stop the same browser claiming several free trials.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        device_id = request.COOKIES.get(DEVICE_COOKIE, "")
        is_new = not (len(device_id) == 32 and device_id.isalnum())
        if is_new:
            device_id = secrets.token_hex(16)
        request.device_id = device_id

        response = self.get_response(request)
        if is_new:
            response.set_cookie(
                DEVICE_COOKIE,
                device_id,
                max_age=DEVICE_COOKIE_MAX_AGE,
                httponly=True,
                samesite="Lax",
                secure=request.is_secure(),
            )
        return response


TIMEZONE_COOKIE = "damdm_tz"


def valid_timezone(name: str) -> str:
    """``name`` if it is a real IANA zone such as ``Asia/Kolkata``, else ""."""
    if not name or len(name) > 64:
        return ""
    try:
        zoneinfo.ZoneInfo(name)
    except (zoneinfo.ZoneInfoNotFoundError, ValueError, OSError):
        return ""
    return name


class BrowserTimezoneMiddleware:
    """
    Show dates and times in the visitor's own timezone.

    Everything is stored and calculated in UTC; only rendering changes. The
    browser reports its zone in a cookie (see base.html). Signed-in users keep
    their last zone on their account, for pages rendered before the cookie exists.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        name = valid_timezone(request.COOKIES.get(TIMEZONE_COOKIE, ""))
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            if name and user.timezone != name:
                user.timezone = name
                user.save(update_fields=["timezone"])
            # "UTC" is the field's default, meaning the zone isn't known yet.
            if not name and user.timezone != "UTC":
                name = valid_timezone(user.timezone)
        timezone.activate(zoneinfo.ZoneInfo(name or settings.DEFAULT_DISPLAY_TIME_ZONE))
        try:
            return self.get_response(request)
        finally:
            timezone.deactivate()
