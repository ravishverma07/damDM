from django.conf import settings
from django.urls import reverse


def app_meta(request):
    """Expose product identity to every template."""
    return {
        "APP_NAME": settings.APP_NAME,
        "APP_VENDOR": settings.APP_VENDOR,
        "APP_BASE_URL": settings.APP_BASE_URL,
    }


#: (key, label, url name). The key doubles as the icon name.
NAV_ITEMS = (
    ("home", "Home", "core:dashboard"),
    ("automations", "Automations", "automations:list"),
    ("instagram", "Instagram", "instagram:accounts"),
    ("settings", "Settings", "accounts:profile"),
)
_SECTION_BY_NAMESPACE = {
    "automations": "automations",
    "instagram": "instagram",
    "accounts": "settings",
}


def navigation(request):
    """App navigation for signed-in users, with the current section marked."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    match = getattr(request, "resolver_match", None)
    namespace = match.namespace if match else ""
    url_name = match.url_name if match else ""
    active = _SECTION_BY_NAMESPACE.get(namespace) or ("home" if url_name == "dashboard" else "")
    return {
        "nav_items": [
            {"key": key, "icon": key, "label": label, "url": reverse(name), "active": key == active}
            for key, label, name in NAV_ITEMS
        ]
    }
