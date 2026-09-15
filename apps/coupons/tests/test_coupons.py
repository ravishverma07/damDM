from datetime import timedelta

import pytest
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.automations.models import Automation
from apps.billing.models import Payment, Subscription, TrialClaim
from apps.billing.providers.base import SubscriptionState
from apps.billing.providers.razorpay_provider import RazorpayBillingProvider
from apps.core.models import Notification
from apps.coupons.admin import CouponAdminForm
from apps.coupons.models import Coupon, CouponRedemption
from apps.coupons.services import (
    archive_coupon,
    available_coupons,
    parse_usernames,
    sync_recipients,
)
from apps.instagram.models import ConnectedAccount
from apps.instagram.services.connect import connect_account
from apps.instagram.services.oauth import InstagramIdentity

pytestmark = pytest.mark.django_db


def _coupon(usernames=("testcreator",), percent=100, months=1, code=None, **extra):
    coupon = Coupon.objects.create(
        code=code or f"TEST{percent}X{months}",
        name="Test coupon",
        discount_percent=percent,
        discount_months=months,
        expires_at=extra.pop("expires_at", timezone.now() + timedelta(days=30)),
        **extra,
    )
    sync_recipients(coupon, list(usernames))
    return coupon


def _form(**overrides):
    data = {
        "name": "Followers",
        "code": "",
        "usernames": "@DamDM_Fan\nsecond.user",
        "discount_percent": 30,
        "discount_months": 3,
        "razorpay_offer_id": "offer_ABC123",
        "expires_at": (timezone.now() + timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S"),
        "notify_on_connect": "on",
    }
    data.update(overrides)
    return CouponAdminForm(data={k: v for k, v in data.items() if v is not None})


def _redeem(client, user, coupon):
    client.force_login(user)
    return client.post(reverse("billing:start"), {"coupon": coupon.code})


# --- admin ---------------------------------------------------------------------

def test_usernames_are_cleaned_and_bad_ones_reported():
    valid, invalid = parse_usernames("@DamDM_Fan, second.user\nnot-valid!  @DamDM_Fan")
    assert valid == ["damdm_fan", "second.user"]
    assert invalid == ["not-valid!"]


def test_admin_form_accepts_a_valid_coupon():
    form = _form()
    assert form.is_valid(), form.errors
    assert form.cleaned_data["usernames"] == ["damdm_fan", "second.user"]


@override_settings(RAZORPAY_MOCK=False)
def test_partial_discounts_need_a_razorpay_offer():
    assert "razorpay_offer_id" in _form(razorpay_offer_id="").errors
    assert _form(discount_percent=100, razorpay_offer_id="").is_valid()


@pytest.mark.parametrize(
    ("overrides", "field"),
    [({"usernames": "bad name!"}, "usernames"), ({"usernames": ""}, "usernames"),
     ({"discount_percent": 101}, "discount_percent"), ({"discount_percent": 0}, "discount_percent"),
     ({"expires_at": "2020-01-01 00:00:00"}, "expires_at")],
)
def test_admin_form_explains_mistakes(overrides, field):
    assert field in _form(**overrides).errors


def test_admin_creates_a_coupon_and_notifies_the_connected_account(
    client, django_user_model, user, account
):
    staff = django_user_model.objects.create_superuser(
        email="admin@example.com", password="pw-long-enough"
    )
    client.force_login(staff)
    expires = timezone.now() + timedelta(days=10)
    response = client.post(reverse("admin:coupons_coupon_add"), {
        "name": "Followers", "code": "", "usernames": "@TestCreator",
        "discount_percent": 100, "discount_months": 1, "razorpay_offer_id": "",
        "expires_at_0": expires.strftime("%Y-%m-%d"), "expires_at_1": "12:00:00",
        "notify_on_connect": "on",
        "redemptions-TOTAL_FORMS": "0", "redemptions-INITIAL_FORMS": "0",
        "redemptions-MIN_NUM_FORMS": "0", "redemptions-MAX_NUM_FORMS": "1000",
        "_save": "Save",
    })
    assert response.status_code == 302
    coupon = Coupon.objects.get()
    assert coupon.code.startswith("DAMDM-")
    assert list(coupon.recipients.values_list("instagram_username", flat=True)) == ["testcreator"]
    assert Notification.objects.filter(user=user, kind="coupon").count() == 1


# --- notifications -------------------------------------------------------------

def test_connecting_the_named_account_sends_one_notification(user):
    _coupon(usernames=["lucky.fan"])
    identity = InstagramIdentity(user_id="178999", username="Lucky.Fan", access_token="IGQVJx")
    connect_account(user, identity)
    connect_account(user, identity)  # reconnect or refresh: still one notification
    notification = Notification.objects.get(user=user)
    assert "1 month free" in notification.title
    assert notification.action_url == reverse("billing:overview")


def test_no_notification_when_the_box_is_unticked(user):
    _coupon(usernames=["lucky.fan"], notify_on_connect=False)
    identity = InstagramIdentity(user_id="178999", username="lucky.fan", access_token="x")
    connect_account(user, identity)
    assert not Notification.objects.exists()


def test_dashboard_shows_and_dismisses_the_notification(client, user, account):
    coupon = _coupon()
    from apps.coupons.services import notify_matching_coupons

    notify_matching_coupons(account)
    client.force_login(user)
    assert coupon.summary in client.get(reverse("core:dashboard")).content.decode()
    note = Notification.objects.get()
    client.post(reverse("core:notification-dismiss", args=[note.pk]))
    assert coupon.summary not in client.get(reverse("core:dashboard")).content.decode()


# --- who can use it ------------------------------------------------------------

def test_only_the_exact_username_can_use_it(user, account):
    mine = _coupon(usernames=["TestCreator"], code="MINE")
    _coupon(usernames=["testcreator2"], code="NOTMINE")
    assert available_coupons(user) == [mine]


@pytest.mark.parametrize("change", ["expired", "archived"])
def test_expired_or_archived_coupons_are_hidden(user, account, change):
    if change == "expired":
        _coupon(expires_at=timezone.now() - timedelta(minutes=1))
    else:
        archive_coupon(_coupon())
    assert available_coupons(user) == []


def test_someone_else_cannot_use_the_code(client, other_user, pro_plan):
    coupon = _coupon()
    ConnectedAccount.objects.create(owner=other_user, external_account_id="999", username="other")
    _redeem(client, other_user, coupon)
    assert not Subscription.objects.exists()
    assert not CouponRedemption.objects.exists()


def test_billing_page_shows_the_coupon(client, user, account, pro_plan):
    coupon = _coupon()
    client.force_login(user)
    body = client.get(reverse("billing:overview")).content.decode()
    assert coupon.summary in body and coupon.code in body


# --- checkout ------------------------------------------------------------------

def test_free_coupon_adds_months_after_the_trial(client, user, account, pro_plan):
    coupon = _coupon(months=1)
    _redeem(client, user, coupon)

    subscription = Subscription.objects.get(user=user)
    first_charge_in = subscription.trial_ends_at - timezone.now()
    assert timedelta(days=43) < first_charge_in <= timedelta(days=44)
    assert subscription.has_access
    assert not Payment.objects.exists()
    assert CouponRedemption.objects.get().subscription == subscription
    assert TrialClaim.objects.filter(subscription=subscription).exists()
    assert available_coupons(user) == []


def test_free_coupon_without_a_trial_starts_free_today(client, user, account, pro_plan):
    pro_plan.trial_days = 0
    pro_plan.save()
    _redeem(client, user, _coupon(months=2))
    subscription = Subscription.objects.get(user=user)
    assert timedelta(days=59) < subscription.trial_ends_at - timezone.now() <= timedelta(days=60)
    assert subscription.has_access
    assert not Payment.objects.exists()
    assert not TrialClaim.objects.exists()


@override_settings(RAZORPAY_MOCK=False)
def test_discount_coupon_attaches_its_razorpay_offer(client, user, account, pro_plan, monkeypatch):
    seen = {}

    def create_subscription(self, **kwargs):
        seen.update(kwargs)
        return SubscriptionState(
            provider="razorpay", customer_id="cust_1", subscription_id="sub_offer", status="created"
        )

    monkeypatch.setattr(RazorpayBillingProvider, "create_customer", lambda self, user: "cust_1")
    monkeypatch.setattr(RazorpayBillingProvider, "create_subscription", create_subscription)
    coupon = _coupon(percent=30, months=3, razorpay_offer_id="offer_ABC123")
    _redeem(client, user, coupon)

    assert seen["offer_id"] == "offer_ABC123"
    assert timedelta(days=13) < seen["start_at"] - timezone.now() <= timedelta(days=14)
    assert CouponRedemption.objects.get().subscription.provider_subscription_id == "sub_offer"


# --- archiving -----------------------------------------------------------------

def test_archiving_a_free_coupon_cancels_its_plans(client, user, automation, pro_plan):
    coupon = _coupon()
    _redeem(client, user, coupon)

    assert archive_coupon(coupon) == 1
    subscription = Subscription.objects.get(user=user)
    automation.refresh_from_db()
    assert subscription.status == Subscription.Status.CANCELED
    assert automation.status == Automation.Status.PAUSED
    assert Notification.objects.filter(user=user, dedupe_key=f"coupon-ended:{coupon.pk}").exists()


def test_archiving_a_discount_coupon_keeps_its_plans(client, user, account, pro_plan):
    coupon = _coupon(percent=40, months=2)
    _redeem(client, user, coupon)
    assert archive_coupon(coupon) == 0
    assert Subscription.objects.get(user=user).has_access


def test_archive_is_also_an_admin_action(user, account, pro_plan, django_user_model):
    coupon = _coupon()
    staff = django_user_model.objects.create_superuser(
        email="admin@example.com", password="pw-long-enough"
    )
    browser = Client()
    _redeem(browser, user, coupon)
    browser.force_login(staff)
    browser.post(reverse("admin:coupons_coupon_changelist"), {
        "action": "archive", "_selected_action": [coupon.pk],
    })
    coupon.refresh_from_db()
    assert coupon.status == Coupon.Status.ARCHIVED
    assert Subscription.objects.get(user=user).status == Subscription.Status.CANCELED
