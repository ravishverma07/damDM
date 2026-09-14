"""Shared pytest fixtures for the whole project."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.automations.models import Automation, AutomationKeyword, AutomationStep
from apps.billing.models import Plan, Subscription
from apps.instagram.models import ConnectedAccount, Platform, Post
from apps.messaging.models import Contact


@pytest.fixture
def user(django_user_model):
    return django_user_model.objects.create_user(
        email="creator@example.com", password="sup3r-secret-pass", full_name="Test Creator"
    )


@pytest.fixture
def other_user(django_user_model):
    return django_user_model.objects.create_user(
        email="someone.else@example.com", password="sup3r-secret-pass"
    )


@pytest.fixture
def account(user):
    acct = ConnectedAccount(
        owner=user,
        platform=Platform.INSTAGRAM,
        external_account_id="17841400000000001",
        username="testcreator",
        account_type=ConnectedAccount.AccountType.CREATOR,
        scopes=["instagram_business_basic"],
    )
    acct.set_access_token("IGQVJtest-token-value", timezone.now() + timedelta(days=60))
    acct.save()
    return acct


@pytest.fixture
def post(account):
    return Post.objects.create(
        account=account,
        external_media_id="18000000000000001",
        media_type=Post.MediaType.VIDEO,
        product_type=Post.ProductType.REELS,
        caption="Comment LINK for the guide",
        published_at=timezone.now(),
    )


@pytest.fixture
def automation(user, account, post):
    rule = Automation.objects.create(
        owner=user,
        account=account,
        name="Reel link",
        post_scope=Automation.PostScope.SPECIFIC_POSTS,
        status=Automation.Status.ACTIVE,
    )
    rule.posts.add(post)
    AutomationKeyword.objects.create(
        automation=rule, keyword="LINK", match_type=AutomationKeyword.MatchType.EXACT
    )
    AutomationStep.objects.create(
        automation=rule,
        order=1,
        step_type=AutomationStep.StepType.SEND_DM,
        config={"text": "Here is your link!"},
    )
    return rule


@pytest.fixture
def contact(account):
    return Contact.objects.create(
        account=account, external_user_id="99001", username="fan"
    )


@pytest.fixture
def free_plan(db):
    return Plan.objects.create(
        name="Free",
        slug="free",
        monthly_message_limit=100,
        monthly_automation_limit=1,
        instagram_account_limit=1,
    )


@pytest.fixture
def pro_plan(db):
    return Plan.objects.create(
        name="Pro",
        slug="pro",
        monthly_price=Decimal("99.00"),
        currency="INR",
        trial_days=14,
        monthly_message_limit=0,
        monthly_automation_limit=0,
        provider_price_id="plan_test_pro",
    )


@pytest.fixture
def paid_user(user, pro_plan):
    """``user`` with an active paid subscription, so automations can be turned on."""
    Subscription.objects.create(
        user=user,
        plan=pro_plan,
        status=Subscription.Status.ACTIVE,
        provider_subscription_id="sub_paid",
        current_period_end=timezone.now() + timedelta(days=30),
    )
    return user
