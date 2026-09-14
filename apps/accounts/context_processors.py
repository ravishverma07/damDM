from .services.auth import AUTH_MODES, build_auth_panel


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
    return {"auth_panel": panel}
