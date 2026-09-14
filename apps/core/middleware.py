"""Request middleware shared across apps."""

from __future__ import annotations

import secrets

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
