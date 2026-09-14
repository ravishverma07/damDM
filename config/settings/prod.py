"""Production settings. HTTPS-ready, strict hosts, secure cookies."""

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403
from .base import env  # noqa: F401

DEBUG = False

# Mock OAuth signs anyone in as a fixed fake user, so it must never reach production.
if META_MOCK or GOOGLE_MOCK or RAZORPAY_MOCK:  # noqa: F405
    raise ImproperlyConfigured(
        "META_MOCK, GOOGLE_MOCK and RAZORPAY_MOCK must all be False in production."
    )

# ALLOWED_HOSTS must be supplied explicitly in production.
ALLOWED_HOSTS = env("ALLOWED_HOSTS")

CSRF_TRUSTED_ORIGINS = env.list(
    "CSRF_TRUSTED_ORIGINS", default=["https://dm.dammai.in"]
)

# --- HTTPS / transport ---
SECURE_SSL_REDIRECT = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_HSTS_SECONDS = 60 * 60 * 24 * 365
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

# --- Cookies ---
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SECURE = True
CSRF_COOKIE_HTTPONLY = False  # readable by HTMX for the X-CSRFToken header
CSRF_COOKIE_SAMESITE = "Lax"

# --- Headers ---
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

SESSION_EXPIRE_AT_BROWSER_CLOSE = False
SESSION_COOKIE_AGE = 60 * 60 * 24 * 14
