from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.db import IntegrityError
from django.utils import timezone

from apps.billing.models import Payment, Plan, Subscription, UsageRecord
from apps.billing.providers.base import BillingProvider
from apps.billing.providers.registry import get_provider

pytestmark = pytest.mark.django_db


def test_seed_plans_creates_pro_and_retires_old_plans(free_plan):
    call_command("seed_plans", verbosity=0)
    pro = Plan.objects.get(slug="pro")
    assert (pro.monthly_price, pro.currency, pro.trial_days) == (Decimal("99.00"), "INR", 14)
    assert pro.unlimited_messages and pro.unlimited_automations
    assert pro.provider_price_id == "plan_test_pro"
    free_plan.refresh_from_db()
    assert not free_plan.is_active


def test_seed_plans_is_idempotent():
    call_command("seed_plans", verbosity=0)
    call_command("seed_plans", verbosity=0)
    assert Plan.objects.filter(slug="pro").count() == 1


def test_amount_in_minor_units(pro_plan):
    assert pro_plan.amount_in_minor_units() == 9900


def test_only_one_live_subscription_per_user(user, pro_plan):
    Subscription.objects.create(user=user, plan=pro_plan, status=Subscription.Status.ACTIVE)
    with pytest.raises(IntegrityError):
        Subscription.objects.create(user=user, plan=pro_plan, status=Subscription.Status.TRIALING)


def test_cancelled_subscriptions_do_not_block_a_new_one(user, pro_plan):
    Subscription.objects.create(user=user, plan=pro_plan, status=Subscription.Status.CANCELED)
    assert Subscription.objects.create(
        user=user, plan=pro_plan, status=Subscription.Status.ACTIVE
    ).pk


def test_trial_access_lasts_until_a_day_after_it_ends(user, pro_plan):
    now = timezone.now()
    trial = Subscription(user=user, plan=pro_plan, status=Subscription.Status.TRIALING)
    trial.trial_ends_at = now + timedelta(days=3)
    assert trial.has_access and trial.trial_days_left == 3
    trial.trial_ends_at = now - timedelta(hours=2)
    assert trial.has_access
    trial.trial_ends_at = now - timedelta(days=2)
    assert not trial.has_access


@pytest.mark.parametrize(
    ("status", "access"),
    [("active", True), ("past_due", True), ("canceled", False), ("expired", False),
     ("incomplete", False)],
)
def test_access_by_status(user, pro_plan, status, access):
    assert Subscription(user=user, plan=pro_plan, status=status).has_access is access


def test_payment_amount_is_shown_in_rupees(user):
    assert Payment(user=user, amount=9900).amount_display == Decimal("99")


def test_usage_is_unique_per_user_and_period(user):
    UsageRecord.objects.create(
        user=user, period_start=date(2026, 9, 1), period_end=date(2026, 10, 1)
    )
    with pytest.raises(IntegrityError):
        UsageRecord.objects.create(
            user=user, period_start=date(2026, 9, 1), period_end=date(2026, 10, 1)
        )


def test_current_period_record_is_reused(user):
    first = UsageRecord.for_current_period(user)
    assert UsageRecord.for_current_period(user).pk == first.pk


def test_increment_is_atomic_and_refreshes(user):
    record = UsageRecord.for_current_period(user)
    record.increment(messages_sent=2, comments_processed=1)
    record.increment(messages_sent=3)
    record.refresh_from_db()
    assert (record.messages_sent, record.comments_processed) == (5, 1)


def test_registry_returns_the_razorpay_provider():
    provider = get_provider()
    assert isinstance(provider, BillingProvider)
    assert provider.name == "razorpay"


def test_registry_rejects_unknown_providers():
    from django.core.exceptions import ImproperlyConfigured

    with pytest.raises(ImproperlyConfigured):
        get_provider("paypal")
