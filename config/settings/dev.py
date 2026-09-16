"""Local development settings."""

from urllib.parse import urlsplit

from .base import *  # noqa: F403
from .base import env  # noqa: F401

DEBUG = False
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "0.0.0.0", "testserver", "dammai.in", "damdm.dammai.in"]

# Instagram only redirects to HTTPS, so real sign-in in development goes through a
# tunnel (ngrok, cloudflared) whose https URL is APP_BASE_URL. Trust that host.
_public_url = urlsplit(APP_BASE_URL)  # noqa: F405
if _public_url.hostname and _public_url.hostname not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(_public_url.hostname)
# Every public host (the tunnel URL and the domains above) is served over https.
CSRF_TRUSTED_ORIGINS = [
    f"https://{host}" for host in ALLOWED_HOSTS
    if host not in ("localhost", "127.0.0.1", "0.0.0.0", "testserver")
]
if _public_url.scheme == "https" and _public_url.netloc != _public_url.hostname:
    CSRF_TRUSTED_ORIGINS.append(f"https://{_public_url.netloc}")
# The tunnel terminates TLS and forwards plain HTTP with this header.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

INTERNAL_IPS = ["127.0.0.1"]

# Manifest storage requires collectstatic; keep it simple while developing.
# With DEBUG off (the live site) keep the hashed file names from base settings, so
# Cloudflare never serves an old CSS or icon; run collectstatic after changing them.
if DEBUG:
    STORAGES["staticfiles"]["BACKEND"] = (  # noqa: F405
        "whitenoise.storage.CompressedStaticFilesStorage"
    )

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
