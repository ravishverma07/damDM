"""Settings used by pytest. No network, no Redis, fast hashing."""

from .base import *  # noqa: F403

DEBUG = False
ALLOWED_HOSTS = ["testserver", "localhost"]

SECRET_KEY = "test-secret-key-not-used-anywhere-else"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

STORAGES["staticfiles"]["BACKEND"] = (  # noqa: F405
    "django.contrib.staticfiles.storage.StaticFilesStorage"
)

# Nothing is collected during tests, so WhiteNoise has nothing to serve.
MIDDLEWARE = [m for m in MIDDLEWARE if "whitenoise" not in m]  # noqa: F405

# Push is off unless a test turns it on, whatever the developer's .env holds.
VAPID_PRIVATE_KEY = ""

# Never hit Meta, Google or Razorpay from a test.
META_MOCK = True
META_APP_ID = "test-app-id"
META_APP_SECRET = "test-app-secret"
META_WEBHOOK_VERIFY_TOKEN = "test-verify-token"
META_GRAPH_API_VERSION = "v21.0"

GOOGLE_MOCK = True
GOOGLE_CLIENT_ID = "test-google-client-id"
GOOGLE_CLIENT_SECRET = "test-google-client-secret"

RAZORPAY_MOCK = True
RAZORPAY_KEY_ID = "rzp_test_dummy"
RAZORPAY_KEY_SECRET = "test-razorpay-secret"
RAZORPAY_WEBHOOK_SECRET = "test-webhook-secret"
RAZORPAY_PLAN_ID = "plan_test_pro"
TRIAL_FINGERPRINT_KEY = "test-fingerprint-key"

# Deterministic Fernet key for encryption round-trip tests.
TOKEN_ENCRYPTION_KEY = "dGVzdC1rZXktZm9yLWRhbWRtLXVuaXQtdGVzdHMtMzJieQ=="
IP_HASH_SALT = "test-salt"

LOGGING["root"]["level"] = "ERROR"  # noqa: F405
