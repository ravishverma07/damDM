import json
from datetime import timedelta

import http_ece
import httpx
import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.core import push
from apps.core.models import Notification, PushSubscription
from apps.core.notify import deliver, notify

pytestmark = pytest.mark.django_db

VAPID_KEY = push.generate_private_key()
ENDPOINT = "https://fcm.googleapis.com/fcm/send/abc123"
push_on = override_settings(VAPID_PRIVATE_KEY=VAPID_KEY, VAPID_SUBJECT="mailto:test@example.com")


class Browser:
    """A fake browser subscription whose messages we can decrypt."""

    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.auth = b"0123456789abcdef"

    @property
    def p256dh(self) -> str:
        return push.b64url_encode(self.key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        ))

    @property
    def auth_b64(self) -> str:
        return push.b64url_encode(self.auth)

    def decrypt(self, body: bytes) -> dict:
        return json.loads(http_ece.decrypt(
            body, private_key=self.key, auth_secret=self.auth, version="aes128gcm"
        ))

    def subscription(self, user, endpoint=ENDPOINT) -> PushSubscription:
        return PushSubscription.objects.create(
            user=user, endpoint=endpoint, p256dh=self.p256dh, auth=self.auth_b64
        )


@pytest.fixture
def push_service(monkeypatch):
    """Capture requests to push services; ``status`` sets the reply."""
    service = {"requests": [], "status": 201}

    def handler(request):
        service["requests"].append(request)
        return httpx.Response(service["status"])

    real_client = httpx.Client
    monkeypatch.setattr(
        push.httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(handler))
    )
    return service


# --- protocol ---------------------------------------------------------------

@push_on
def test_messages_are_encrypted_for_the_browser_and_signed(user, push_service):
    browser = Browser()
    subscription = browser.subscription(user)
    assert push.send(subscription, push.Message("Hi", "There", "/billing/", "pay"))

    request = push_service["requests"][0]
    assert request.headers["Content-Encoding"] == "aes128gcm"
    assert request.headers["TTL"] == str(push.DEFAULT_TTL)
    assert request.headers["Topic"] == "pay"
    assert browser.decrypt(request.content) == {
        "title": "Hi", "body": "There", "url": "/billing/", "tag": "pay",
    }

    # VAPID: a JWT for the push service's origin, signed by the key browsers subscribed with.
    token, key = request.headers["Authorization"].removeprefix("vapid t=").split(", k=")
    assert key == push.public_key()
    header, claims, signature = token.split(".")
    payload = json.loads(push.b64url_decode(claims))
    assert payload["aud"] == "https://fcm.googleapis.com"
    assert payload["sub"] == "mailto:test@example.com"
    assert payload["exp"] > timezone.now().timestamp()
    raw = push.b64url_decode(signature)
    public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), push.b64url_decode(key))
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    public.verify(der, f"{header}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))
    with pytest.raises(InvalidSignature):
        public.verify(der, b"tampered", ec.ECDSA(hashes.SHA256()))


@pytest.mark.parametrize(
    ("endpoint", "allowed"),
    [
        (ENDPOINT, True),
        ("https://web.push.apple.com/QGuQyavXutnMH", True),
        ("https://updates.push.services.mozilla.com/wpush/v2/x", True),
        ("https://wns2-par02p.notify.windows.com/w/?token=x", True),
        ("http://fcm.googleapis.com/fcm/send/x", False),
        ("https://evil.example/fcm.googleapis.com", False),
        ("https://fcm.googleapis.com.evil.example/x", False),
        ("https://localhost/x", False),
        ("https://169.254.169.254/latest", False),
        ("", False),
    ],
)
def test_only_real_push_services_are_called(endpoint, allowed):
    assert push.is_allowed_endpoint(endpoint) is allowed


def test_nothing_is_sent_without_a_key(user, push_service):
    assert not push.send(Browser().subscription(user), push.Message("Hi"))
    assert push_service["requests"] == []
    assert push.public_key() == ""


# --- delivery ---------------------------------------------------------------

@push_on
def test_deliver_reaches_every_device_and_forgets_gone_ones(user, push_service):
    Browser().subscription(user)
    Browser().subscription(user, endpoint="https://web.push.apple.com/device-2")
    assert deliver(user.pk, {"title": "Hello", "url": "https://evil.example/"}) == 2
    browser_payloads = [r for r in push_service["requests"]]
    assert len(browser_payloads) == 2

    push_service["status"] = 410
    assert deliver(user.pk, {"title": "Hello"}) == 0
    assert not PushSubscription.objects.exists()


@push_on
def test_repeated_failures_retire_a_device(user, push_service):
    subscription = Browser().subscription(user)
    push_service["status"] = 500
    for _ in range(4):
        deliver(user.pk, {"title": "Hello"})
    subscription.refresh_from_db()
    assert subscription.failure_count == 4
    deliver(user.pk, {"title": "Hello"})
    assert not PushSubscription.objects.exists()


@push_on
def test_links_in_notifications_stay_on_this_site(user, push_service):
    browser = Browser()
    browser.subscription(user)
    deliver(user.pk, {"title": "x", "url": "//evil.example/"})
    assert browser.decrypt(push_service["requests"][0].content)["url"] == "/dashboard/"


@push_on
def test_dashboard_notifications_are_pushed(user, push_service, django_capture_on_commit_callbacks):
    browser = Browser()
    browser.subscription(user)
    with django_capture_on_commit_callbacks(execute=True):
        notify(user, kind="demo", title="Hello", body="World", action_url="/refer/", dedupe_key="k")
        notify(user, kind="demo", title="Hello again", dedupe_key="k")  # deduplicated
    assert Notification.objects.count() == 1
    assert len(push_service["requests"]) == 1
    assert browser.decrypt(push_service["requests"][0].content)["url"] == "/refer/"


# --- endpoints and pages ------------------------------------------------------

def _post(client, name, data):
    return client.post(reverse(name), json.dumps(data), content_type="application/json")


@push_on
def test_subscribe_and_unsubscribe(client, user):
    browser = Browser()
    data = {"endpoint": ENDPOINT, "keys": {"p256dh": browser.p256dh, "auth": browser.auth_b64}}
    assert _post(client, "core:push-subscribe", data).status_code == 403  # signed out

    client.force_login(user)
    assert _post(client, "core:push-subscribe", data).json() == {"ok": True}
    assert _post(client, "core:push-subscribe", data).status_code == 200  # idempotent
    assert PushSubscription.objects.get().user == user

    bad = {**data, "endpoint": "https://evil.example/x"}
    assert _post(client, "core:push-subscribe", bad).status_code == 400
    assert _post(client, "core:push-subscribe", {"endpoint": ENDPOINT}).status_code == 400

    _post(client, "core:push-unsubscribe", {"endpoint": ENDPOINT})
    assert not PushSubscription.objects.exists()


def test_subscribe_is_refused_when_push_is_off(client, user):
    client.force_login(user)
    data = {"endpoint": ENDPOINT, "keys": {"p256dh": "a", "auth": "b"}}
    assert _post(client, "core:push-subscribe", data).status_code == 503


@push_on
def test_another_users_device_moves_to_whoever_subscribes(client, user, django_user_model):
    other = django_user_model.objects.create_user(email="other@example.com", password="x" * 12)
    Browser().subscription(other)
    client.force_login(user)
    browser = Browser()
    data = {"endpoint": ENDPOINT, "keys": {"p256dh": browser.p256dh, "auth": browser.auth_b64}}
    _post(client, "core:push-subscribe", data)
    assert PushSubscription.objects.get().user == user


@push_on
def test_logging_out_stops_this_devices_notifications(client, user):
    browser = Browser()
    client.force_login(user)
    _post(client, "core:push-subscribe", {
        "endpoint": ENDPOINT, "keys": {"p256dh": browser.p256dh, "auth": browser.auth_b64},
    })
    Browser().subscription(user, endpoint="https://web.push.apple.com/other-phone")
    client.post(reverse("accounts:logout"))
    assert list(PushSubscription.objects.values_list("endpoint", flat=True)) == [
        "https://web.push.apple.com/other-phone"
    ]


@push_on
def test_test_notification(client, user, push_service):
    Browser().subscription(user)
    client.force_login(user)
    assert _post(client, "core:push-test", {}).json() == {"sent": 1}


def test_service_worker_is_served_from_the_root(client):
    response = client.get("/sw.js")
    assert response["Content-Type"].startswith("application/javascript")
    assert response["Service-Worker-Allowed"] == "/"
    body = response.content.decode()
    assert 'addEventListener("push"' in body and reverse("core:offline") in body


def test_offline_page_holds_nothing_personal(client, user):
    client.force_login(user)
    body = client.get(reverse("core:offline")).content.decode()
    assert "You're offline" in body
    assert user.email not in body


@push_on
def test_settings_offer_install_and_notifications(client, user):
    client.force_login(user)
    body = client.get(reverse("accounts:profile")).content.decode()
    assert "data-push-toggle" in body and "data-install-app" in body
    assert f'<meta name="vapid-public-key" content="{push.public_key()}">' in body
    assert "js/pwa" in body


def test_settings_hide_the_switch_when_push_is_off(client, user):
    client.force_login(user)
    body = client.get(reverse("accounts:profile")).content.decode()
    assert "data-push-toggle" not in body
    assert "vapid-public-key" not in body


def test_public_pages_do_not_expose_push_details(client):
    with override_settings(VAPID_PRIVATE_KEY=VAPID_KEY):
        body = client.get("/").content.decode()
    assert "vapid-public-key" not in body and "csrf-token" not in body
    assert "data-install-app" in body


def test_generate_vapid_key_command(capsys):
    from django.core.management import call_command

    call_command("generate_vapid_key")
    out = capsys.readouterr().out
    key = out.split("VAPID_PRIVATE_KEY=")[1].split()[0]
    assert len(push.b64url_decode(key)) == 32


# --- what triggers notifications ---------------------------------------------

def test_payment_failure_and_plan_end_notify(user, pro_plan):
    from apps.billing.models import Subscription
    from apps.billing.services.subscriptions import apply_provider_status

    subscription = Subscription.objects.create(
        user=user, plan=pro_plan, provider_subscription_id="sub_n", status="active",
        current_period_end=timezone.now() + timedelta(days=3),
    )
    apply_provider_status(subscription, "pending")
    apply_provider_status(subscription, "pending")  # no duplicate
    apply_provider_status(subscription, "halted")
    kinds = list(Notification.objects.filter(user=user).values_list("kind", flat=True))
    assert sorted(kinds) == ["payment_failed", "plan_ended"]


def test_trial_reminder_is_sent_once(user, pro_plan, django_user_model):
    from apps.billing.models import Subscription
    from apps.billing.tasks import remind_trial_ending

    soon = timezone.now() + timedelta(days=1)
    Subscription.objects.create(
        user=user, plan=pro_plan, provider_subscription_id="sub_t", status="trialing",
        trial_ends_at=soon, trial_signals=[["email", "a" * 64]],
    )
    coupon_user = django_user_model.objects.create_user(email="c@example.com", password="x" * 12)
    Subscription.objects.create(  # free months from a coupon aren't a trial
        user=coupon_user, plan=pro_plan, provider_subscription_id="sub_c", status="trialing",
        trial_ends_at=soon,
    )
    remind_trial_ending()
    remind_trial_ending()
    reminders = Notification.objects.filter(kind="trial_ending")
    assert reminders.count() == 1
    assert reminders.get().user == user
    assert "first ₹99 AutoPay payment" in reminders.get().body


def test_instagram_losing_access_notifies_the_owner(user, account):
    from apps.instagram.services.graph import GraphAPIError
    from apps.instagram.services.sync import record_failure

    error = GraphAPIError("Session has expired", code=190)
    assert error.is_token_error
    record_failure(account, error)
    notice = Notification.objects.get(user=user, kind="instagram_disconnected")
    assert account.username in notice.title
