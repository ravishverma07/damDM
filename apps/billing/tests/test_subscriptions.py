import hashlib
import hmac
import json
from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
from django.test import Client, RequestFactory, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.automations.models import Automation
from apps.billing.models import BillingWebhookEvent, Payment, Subscription, TrialClaim
from apps.billing.providers.base import BillingError, SubscriptionState
from apps.billing.providers.razorpay_provider import RazorpayBillingProvider
from apps.billing.services.trials import check_eligibility, normalize_email
from apps.billing.tasks import sync_subscriptions
from apps.instagram.models import ConnectedAccount

pytestmark = pytest.mark.django_db
Status = Subscription.Status


def _start(client):
    return client.post(reverse("billing:start"))


def _second_user_with_instagram(django_user_model, email="second@example.com", ig_id="999"):
    other = django_user_model.objects.create_user(email=email, password="x" * 12)
    ConnectedAccount.objects.create(owner=other, external_account_id=ig_id, username="second")
    browser = Client()
    browser.force_login(other)
    return other, browser


def _webhook(client, body, event_id="evt_1", secret="test-webhook-secret"):
    raw = json.dumps(body).encode()
    signature = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return client.post(
        reverse("billing:razorpay-webhook"), raw, content_type="application/json",
        HTTP_X_RAZORPAY_SIGNATURE=signature, HTTP_X_RAZORPAY_EVENT_ID=event_id,
    )


def _event(name, subscription, status, payment=None, **entity):
    body = {"event": name, "payload": {"subscription": {"entity": {
        "id": subscription.provider_subscription_id, "status": status,
        "customer_id": "cust_1", "plan_id": "plan_test_pro", **entity,
    }}}}
    if payment:
        body["payload"]["payment"] = {"entity": payment}
    return body


@pytest.fixture
def trial(client, user, account, pro_plan):
    client.force_login(user)
    _start(client)
    return Subscription.objects.get(user=user)


# --- starting a trial ----------------------------------------------------------

@pytest.mark.parametrize(
    ("raw", "normal"),
    [("Ravish.Kumar+promo@Gmail.com", "ravishkumar@gmail.com"),
     ("ravish@googlemail.com", "ravish@gmail.com"),
     ("a.b+x@company.in", "a.b@company.in")],
)
def test_email_normalisation_ignores_gmail_dots_and_tags(raw, normal):
    assert normalize_email(raw) == normal


def test_billing_page_offers_the_trial(client, user, account, pro_plan):
    client.force_login(user)
    body = client.get(reverse("billing:overview")).content.decode()
    assert "Start 14-day free trial" in body
    assert "₹99" in body


def test_trial_needs_an_instagram_account_first(client, user, pro_plan):
    client.force_login(user)
    assert "Connect Instagram" in client.get(reverse("billing:overview")).content.decode()
    _start(client)
    assert not Subscription.objects.exists()


def test_checkout_starts_a_14_day_trial_and_records_who_used_it(trial):
    assert trial.status == Status.TRIALING
    assert trial.has_access
    assert trial.trial_days_left in (13, 14)
    claim = TrialClaim.objects.get(subscription=trial)
    kinds = set(claim.fingerprints.values_list("kind", flat=True))
    assert {"email", "instagram_account", "device"} <= kinds
    assert all(len(h) == 64 for h in claim.fingerprints.values_list("value_hash", flat=True))


def test_same_instagram_account_gets_no_second_trial(trial, account, django_user_model):
    account.disconnect()
    other, browser = _second_user_with_instagram(
        django_user_model, ig_id=account.external_account_id
    )
    assert "Subscribe for ₹99/month" in browser.get(reverse("billing:overview")).content.decode()
    _start(browser)
    second = Subscription.objects.get(user=other)
    assert second.trial_ends_at is None
    assert second.status == Status.ACTIVE


def test_gmail_aliases_share_one_trial(client, user, account, pro_plan, django_user_model):
    user.email = "ravish.kumar@gmail.com"
    user.save()
    client.force_login(user)
    _start(client)
    other, browser = _second_user_with_instagram(django_user_model, email="ravishkumar+2@gmail.com")
    _start(browser)
    assert Subscription.objects.get(user=other).trial_ends_at is None


def test_same_browser_gets_no_second_trial(client, trial, django_user_model):
    device = client.cookies["damdm_device"].value
    client.logout()  # the test client drops every cookie on logout; a browser keeps this one
    client.cookies["damdm_device"] = device
    other, _ = _second_user_with_instagram(django_user_model)
    client.force_login(other)
    _start(client)
    assert Subscription.objects.get(user=other).trial_ends_at is None


def test_shared_ip_alone_never_blocks_a_trial(trial, django_user_model):
    other, browser = _second_user_with_instagram(django_user_model)
    _start(browser)  # same 127.0.0.1 as the first trial
    assert Subscription.objects.get(user=other).status == Status.TRIALING


def test_deleting_the_account_does_not_reset_the_trial(trial, user, account, django_user_model):
    email, ig_id = user.email, account.external_account_id
    user.delete()
    assert TrialClaim.objects.filter(user__isnull=True).exists()
    again = django_user_model.objects.create_user(email=email, password="x" * 12)
    ConnectedAccount.objects.create(owner=again, external_account_id=ig_id)
    request = RequestFactory().get("/")
    request.device_id = "f" * 32
    assert check_eligibility(request, again).reason == "used"


# --- cancelling and renewing --------------------------------------------------

def test_cancelling_a_trial_keeps_access_until_it_ends(client, trial):
    client.post(reverse("billing:cancel"))
    trial.refresh_from_db()
    assert trial.cancel_at_period_end
    assert trial.status == Status.TRIALING and trial.has_access


def test_trial_renews_into_a_paid_month(trial):
    ended = timezone.now() - timedelta(minutes=1)
    Subscription.objects.filter(pk=trial.pk).update(trial_ends_at=ended)
    sync_subscriptions()
    trial.refresh_from_db()
    assert trial.status == Status.ACTIVE
    assert trial.current_period_end > timezone.now() + timedelta(days=29)
    assert TrialClaim.objects.get().status == TrialClaim.Status.CONVERTED


def test_cancelled_trial_ends_and_pauses_automations(client, trial, automation):
    client.post(reverse("billing:cancel"))
    ended = timezone.now() - timedelta(days=2)
    Subscription.objects.filter(pk=trial.pk).update(trial_ends_at=ended)
    sync_subscriptions()
    trial.refresh_from_db()
    automation.refresh_from_db()
    assert trial.status == Status.CANCELED
    assert automation.status == Automation.Status.PAUSED


# --- webhooks -----------------------------------------------------------------

def test_charged_webhook_records_the_payment_once(client, trial):
    now = int(timezone.now().timestamp())
    body = _event(
        "subscription.charged", trial, "active",
        current_start=now, current_end=now + 30 * 86400,
        payment={"id": "pay_1", "amount": 9900, "currency": "INR", "status": "captured",
                 "method": "upi", "vpa": "ravish@okaxis", "created_at": now},
    )
    assert _webhook(client, body).status_code == 200
    assert _webhook(client, body).status_code == 200  # Razorpay retry
    trial.refresh_from_db()
    assert trial.status == Status.ACTIVE and trial.payment_method == "upi"
    payment = Payment.objects.get()
    assert payment.amount_display == Decimal("99")
    assert BillingWebhookEvent.objects.count() == 1


def test_halted_webhook_pauses_automations(client, trial, automation):
    _webhook(client, _event("subscription.halted", trial, "halted"))
    trial.refresh_from_db()
    automation.refresh_from_db()
    assert not trial.has_access
    assert automation.status == Automation.Status.PAUSED


def test_reused_upi_id_revokes_the_second_trial(client, trial, django_user_model):
    upi = {"id": "pay_auth_1", "method": "upi", "vpa": "same@okaxis", "amount": 500}
    _webhook(client, _event("subscription.authenticated", trial, "authenticated", payment=upi),
             event_id="evt_a")

    other, browser = _second_user_with_instagram(django_user_model)
    _start(browser)
    second = Subscription.objects.get(user=other)
    assert second.status == Status.TRIALING
    upi_again = {**upi, "id": "pay_auth_2", "vpa": "SAME@okaxis"}
    _webhook(browser, _event("subscription.authenticated", second, "authenticated",
                             payment=upi_again), event_id="evt_b")

    second.refresh_from_db()
    assert second.status == Status.CANCELED and not second.has_access
    assert TrialClaim.objects.get(subscription=second).status == TrialClaim.Status.REVOKED


def test_webhook_with_a_bad_signature_is_rejected(client):
    assert _webhook(client, {"event": "subscription.charged"}, secret="wrong").status_code == 400


# --- real Razorpay mode --------------------------------------------------------

@override_settings(RAZORPAY_MOCK=False)
def test_checkout_opens_razorpay_for_the_new_subscription(
    client, user, account, pro_plan, monkeypatch
):
    monkeypatch.setattr(RazorpayBillingProvider, "create_customer", lambda self, user: "cust_1")
    monkeypatch.setattr(
        RazorpayBillingProvider, "create_subscription",
        lambda self, **kwargs: SubscriptionState(
            provider="razorpay", customer_id="cust_1", subscription_id="sub_real_1",
            status="created",
        ),
    )
    client.force_login(user)
    body = _start(client).content.decode()
    assert "checkout.razorpay.com" in body and "sub_real_1" in body
    subscription = Subscription.objects.get()
    assert subscription.status == Status.INCOMPLETE and not subscription.has_access


@override_settings(RAZORPAY_MOCK=False)
@pytest.mark.parametrize("valid", [True, False])
def test_confirm_trusts_only_a_valid_signature(client, user, account, pro_plan, monkeypatch, valid):
    subscription = Subscription.objects.create(
        user=user, plan=pro_plan, provider_subscription_id="sub_real_2", status=Status.INCOMPLETE,
        trial_ends_at=timezone.now() + timedelta(days=14), trial_signals=[["email", "a" * 64]],
    )
    monkeypatch.setattr(
        RazorpayBillingProvider, "fetch_subscription",
        lambda self, sid: SubscriptionState(
            provider="razorpay", customer_id="cust_1", subscription_id=sid, status="authenticated"
        ),
    )
    secret = b"test-razorpay-secret" if valid else b"forged"
    signature = hmac.new(secret, b"pay_9|sub_real_2", hashlib.sha256).hexdigest()
    client.force_login(user)
    client.post(reverse("billing:confirm"), {
        "razorpay_payment_id": "pay_9", "razorpay_subscription_id": "sub_real_2",
        "razorpay_signature": signature,
    })
    subscription.refresh_from_db()
    assert (subscription.status == Status.TRIALING) is valid


@override_settings(RAZORPAY_MOCK=False)
def test_razorpay_client_schedules_the_first_charge_after_the_trial(user, pro_plan):
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers["Authorization"]
        return httpx.Response(200, json={
            "id": "sub_1", "status": "created", "customer_id": "cust_1",
            "plan_id": "plan_test_pro", "short_url": "https://rzp.io/i/x",
        })

    provider = RazorpayBillingProvider(client=httpx.Client(transport=httpx.MockTransport(handler)))
    start = timezone.now() + timedelta(days=14)
    state = provider.create_subscription(
        user=user, plan=pro_plan, customer_id="cust_1", start_at=start
    )
    assert state.subscription_id == "sub_1"
    assert seen["body"]["start_at"] == int(start.timestamp())
    assert seen["body"]["plan_id"] == "plan_test_pro"
    assert seen["auth"].startswith("Basic ")


@override_settings(RAZORPAY_MOCK=False)
def test_razorpay_errors_show_their_description(user, pro_plan):
    transport = httpx.MockTransport(lambda request: httpx.Response(
        400, json={"error": {"description": "The id provided does not exist"}}
    ))
    provider = RazorpayBillingProvider(client=httpx.Client(transport=transport))
    with pytest.raises(BillingError, match="does not exist"):
        provider.fetch_subscription("sub_missing")
