"""Resolve the configured billing provider."""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from .base import BillingProvider
from .razorpay_provider import RazorpayBillingProvider

_PROVIDERS: dict[str, type[BillingProvider]] = {
    RazorpayBillingProvider.name: RazorpayBillingProvider,
}


def get_provider(name: str | None = None) -> BillingProvider:
    key = (name or settings.BILLING_PROVIDER).lower()
    try:
        return _PROVIDERS[key]()
    except KeyError as exc:
        raise ImproperlyConfigured(f"Unknown billing provider: {key!r}") from exc
