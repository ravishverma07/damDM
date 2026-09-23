from django.conf import settings
from django.templatetags.static import static
from django.urls import reverse

from . import push


def app_meta(request):
    """Expose product identity to every template."""
    return {
        "APP_NAME": settings.APP_NAME,
        "APP_VENDOR": settings.APP_VENDOR,
        "APP_BASE_URL": settings.APP_BASE_URL,
        # Search engines and link previews always get the public domain.
        "CANONICAL_URL": f"{settings.APP_BASE_URL}{request.path}",
        "SHARE_IMAGE_URL": f"{settings.APP_BASE_URL}{static('img/icon-512.png')}",
        "BUSINESS_LEGAL_NAME": settings.BUSINESS_LEGAL_NAME,
        "SUPPORT_EMAIL": settings.SUPPORT_EMAIL,
        "PRIVACY_EMAIL": settings.PRIVACY_EMAIL,
        "VAPID_PUBLIC_KEY": push.public_key(),
        "PLAN_PRICE": settings.PLAN_PRICE,
        # Rupees, for the checkout page; blank when the trial costs nothing.
        "TRIAL_UPFRONT_DISPLAY": _rupees(settings.TRIAL_UPFRONT_AMOUNT),
        "PLAN_LIST_PRICE": settings.PLAN_LIST_PRICE,
        "PLAN_DISCOUNT_PERCENT": _discount_percent(settings.PLAN_PRICE, settings.PLAN_LIST_PRICE),
        "SUPPORT_PHONE": settings.SUPPORT_PHONE,
        "BUSINESS_ADDRESS": settings.BUSINESS_ADDRESS,
    }


def _rupees(paise: int) -> str:
    if paise <= 0:
        return ""
    amount = paise / 100
    return f"{amount:.0f}" if amount == int(amount) else f"{amount:.2f}"


def _discount_percent(price: int, list_price: int) -> int:
    return round((list_price - price) * 100 / list_price) if list_price > price else 0


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
