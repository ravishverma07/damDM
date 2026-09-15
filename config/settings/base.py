"""
Base settings shared by every environment.

Environment-specific overrides live in dev.py / prod.py / test.py.
Nothing secret is ever hard-coded here; everything comes from the environment.
"""

from pathlib import Path

import environ
from celery.schedules import crontab

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, []),
    CELERY_TASK_ALWAYS_EAGER=(bool, False),
    META_MOCK=(bool, True),
    GOOGLE_MOCK=(bool, True),
    RAZORPAY_MOCK=(bool, True),
)

# Read .env if present. In production the environment is injected by the platform.
_env_file = BASE_DIR / ".env"
if _env_file.exists():
    env.read_env(str(_env_file))

# ---------------------------------------------------------------- core

SECRET_KEY = env("SECRET_KEY", default="insecure-dev-key-do-not-use-in-production")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")

APP_NAME = "DamDM"
APP_VENDOR = "DammAI"
APP_BASE_URL = env("APP_BASE_URL", default="http://localhost:8000").rstrip("/")

# ---------------------------------------------------------------- apps

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "django_celery_beat",
    "django_htmx",
]

LOCAL_APPS = [
    "apps.core",
    "apps.accounts",
    "apps.instagram",
    "apps.automations",
    "apps.messaging",
    "apps.analytics",
    "apps.billing",
    "apps.referrals",
    "apps.coupons",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "apps.core.middleware.DeviceIdMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.app_meta",
                "apps.core.context_processors.navigation",
                "apps.accounts.context_processors.auth_panel",
                "apps.billing.context_processors.plan_banner",
            ],
        },
    },
]

# ---------------------------------------------------------------- database

DATABASES = {
    "default": env.db_url("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}"),
}
DATABASES["default"].setdefault("CONN_MAX_AGE", 60)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------- auth

AUTH_USER_MODEL = "accounts.User"

AUTHENTICATION_BACKENDS = ["apps.accounts.backends.EmailBackend"]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "core:dashboard"
LOGOUT_REDIRECT_URL = "core:landing"

# Google sign-in (OAuth client of type "Web application").
GOOGLE_CLIENT_ID = env("GOOGLE_CLIENT_ID", default="")
GOOGLE_CLIENT_SECRET = env("GOOGLE_CLIENT_SECRET", default="")
GOOGLE_REDIRECT_URI = (
    env("GOOGLE_REDIRECT_URI", default="") or f"{APP_BASE_URL}/accounts/google/callback/"
)
# When True, "Continue with Google" signs in a fixed fake user without calling Google.
GOOGLE_MOCK = env("GOOGLE_MOCK")

# ---------------------------------------------------------------- i18n

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------- static

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# ---------------------------------------------------------------- email

EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend")
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="DamDM <no-reply@dm.dammai.in>")

# ---------------------------------------------------------------- celery

REDIS_URL = env("REDIS_URL", default="redis://localhost:6379/0")
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default=REDIS_URL)
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default=REDIS_URL)
CELERY_TASK_ALWAYS_EAGER = env("CELERY_TASK_ALWAYS_EAGER")
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"
# Loaded into django-celery-beat's database on `celery beat` start.
CELERY_BEAT_SCHEDULE = {
    "instagram-refresh-expiring-tokens": {
        "task": "apps.instagram.tasks.refresh_expiring_tokens",
        "schedule": crontab(minute=10, hour="*/6"),
    },
    "instagram-sync-stale-accounts": {
        "task": "apps.instagram.tasks.sync_stale_accounts",
        "schedule": crontab(minute=40),
    },
    "instagram-process-pending-data-deletions": {
        "task": "apps.instagram.tasks.process_pending_data_deletions",
        "schedule": crontab(minute=20),
    },
    "billing-release-referral-rewards": {
        "task": "apps.referrals.tasks.release_referral_rewards",
        "schedule": crontab(minute=35),
    },
    "billing-sync-subscriptions": {
        "task": "apps.billing.tasks.sync_subscriptions",
        "schedule": crontab(minute=5),
    },
    "instagram-purge-webhook-payloads": {
        "task": "apps.instagram.tasks.purge_webhook_payloads",
        "schedule": crontab(minute=0, hour=3),
    },
}

# ---------------------------------------------------------------- meta / instagram

META_APP_ID = env("META_APP_ID", default="")
META_APP_SECRET = env("META_APP_SECRET", default="")
# Empty means "derive from APP_BASE_URL".
META_REDIRECT_URI = env("META_REDIRECT_URI", default="") or f"{APP_BASE_URL}/instagram/callback/"
META_WEBHOOK_VERIFY_TOKEN = env("META_WEBHOOK_VERIFY_TOKEN", default="")
META_GRAPH_API_VERSION = env("META_GRAPH_API_VERSION", default="v21.0")
META_GRAPH_BASE_URL = "https://graph.instagram.com"
META_OAUTH_BASE_URL = "https://www.instagram.com"
# When True the Meta client returns recorded fixtures instead of calling the network.
META_MOCK = env("META_MOCK")

# Scopes for "Instagram API with Instagram Login".
META_SCOPES = [
    "instagram_business_basic",
    "instagram_business_manage_comments",
    "instagram_business_manage_messages",
]

# Meta permits exactly one private reply per comment; keep this explicit.
META_PRIVATE_REPLIES_PER_COMMENT = 1
# Standard messaging window after an inbound user message.
META_MESSAGING_WINDOW_HOURS = 24

# ---------------------------------------------------------------- security keys

TOKEN_ENCRYPTION_KEY = env("TOKEN_ENCRYPTION_KEY", default="")
IP_HASH_SALT = env("IP_HASH_SALT", default="dev-salt")

# ---------------------------------------------------------------- billing

BILLING_PROVIDER = env("BILLING_PROVIDER", default="razorpay")
DEFAULT_CURRENCY = env("DEFAULT_CURRENCY", default="INR")
RAZORPAY_KEY_ID = env("RAZORPAY_KEY_ID", default="")
RAZORPAY_KEY_SECRET = env("RAZORPAY_KEY_SECRET", default="")
RAZORPAY_WEBHOOK_SECRET = env("RAZORPAY_WEBHOOK_SECRET", default="")
# The ₹99 monthly plan created in the Razorpay dashboard (plan_...).
RAZORPAY_PLAN_ID = env("RAZORPAY_PLAN_ID", default="")
# When True, checkout skips Razorpay and approves the AutoPay mandate instantly.
RAZORPAY_MOCK = env("RAZORPAY_MOCK")
# Key for hashing trial-abuse fingerprints (emails, Instagram ids, UPI ids).
TRIAL_FINGERPRINT_KEY = env("TRIAL_FINGERPRINT_KEY", default="") or SECRET_KEY

# ---------------------------------------------------------------- drf

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.ScopedRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "webhook": "600/min",
        "oauth": "20/min",
        "link": "120/min",
    },
}

# ---------------------------------------------------------------- logging

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "sensitive": {"()": "apps.core.logging.SensitiveDataFilter"},
    },
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {name} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
            "filters": ["sensitive"],
        },
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django.db.backends": {"level": "WARNING", "handlers": ["console"], "propagate": False},
        # httpx logs every request URL, and Graph API URLs carry access tokens.
        "httpx": {"level": "WARNING", "handlers": ["console"], "propagate": False},
        "httpcore": {"level": "WARNING", "handlers": ["console"], "propagate": False},
        "damdm": {"level": "INFO", "handlers": ["console"], "propagate": False},
    },
}
