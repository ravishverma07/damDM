from django.conf import settings

from .services import google
from .services.auth import AUTH_MODES, build_auth_panel, safe_next


def auth_panel(request):
    """
    The sign-in popup for signed-out visitors on every page.

    ``?auth=signin|signup|finish`` opens it on load; ``?next=`` is carried
    through every sign-in method.
    """
    user = getattr(request, "user", None)
    if user is None or user.is_authenticated:
        return {}
    requested = request.GET.get("auth", "")
    panel = build_auth_panel(
        request,
        mode=requested if requested in AUTH_MODES else "finish",
        next_url=request.GET.get("next"),
    )
    panel["auto_open"] = requested in AUTH_MODES
    context = {"auth_panel": panel}
    if google.one_tap_enabled():
        context["google_one_tap"] = {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "nonce": google.one_tap_nonce(getattr(request, "device_id", "")),
            # Stay on content pages after signing in; the home page goes to the dashboard.
            "next": safe_next(request, request.GET.get("next"))
            or ("" if request.path == "/" else request.get_full_path()),
        }
    return context
