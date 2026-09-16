import pytest
from django.conf import settings
from django.contrib.auth import authenticate
from django.test import Client
from django.urls import reverse

from apps.accounts.models import User
from apps.automations.models import Automation
from apps.billing.models import Subscription
from apps.billing.providers.razorpay_provider import RazorpayBillingProvider
from apps.instagram.models import ConnectedAccount

pytestmark = pytest.mark.django_db

PASSWORD = "sup3r-secret-pass"


def _delete(client, user):
    client.force_login(user)
    return client.post(reverse("accounts:delete-account"), {"confirm_email": user.email})


def test_deleting_only_flags_the_account(client, user, account, automation, monkeypatch):
    calls = []
    monkeypatch.setattr(
        RazorpayBillingProvider, "cancel_subscription", lambda *args, **kwargs: calls.append(args)
    )
    token_before = account.encrypted_access_token

    response = _delete(client, user)
    assert response["Location"] == reverse("core:landing")

    user.refresh_from_db()
    assert user.account_status == User.AccountStatus.DELETED
    assert user.deleted_at is not None and not user.is_active
    assert user.email == "creator@example.com"

    account.refresh_from_db()
    automation.refresh_from_db()
    assert account.status == ConnectedAccount.Status.ACTIVE
    assert account.encrypted_access_token == token_before
    assert account.username == "testcreator"
    assert automation.status == Automation.Status.ACTIVE
    assert calls == []


def test_a_renewing_plan_must_be_cancelled_first(client, paid_user):
    response = _delete(client, paid_user)
    assert response.status_code == 200
    assert "Cancel your plan" in response.content.decode()
    paid_user.refresh_from_db()
    assert paid_user.account_status == User.AccountStatus.ACTIVE


def test_deleting_is_allowed_once_the_plan_is_cancelled(client, paid_user):
    Subscription.objects.filter(user=paid_user).update(cancel_at_period_end=True)
    _delete(client, paid_user)
    paid_user.refresh_from_db()
    assert paid_user.account_status == User.AccountStatus.DELETED


def test_wrong_confirmation_changes_nothing(client, user):
    client.force_login(user)
    client.post(reverse("accounts:delete-account"), {"confirm_email": "someone@else.com"})
    user.refresh_from_db()
    assert user.account_status == User.AccountStatus.ACTIVE


def test_deleted_account_cannot_sign_in_and_other_sessions_end(client, user):
    other_browser = Client()
    other_browser.force_login(user)
    _delete(client, user)
    assert authenticate(username="creator@example.com", password=PASSWORD) is None
    assert other_browser.get(reverse("core:dashboard")).status_code == 302


def test_delete_page_explains_what_happens(client, user):
    client.force_login(user)
    body = client.get(reverse("accounts:delete-account")).content.decode()
    assert "cancel it first" in body
    assert settings.PRIVACY_EMAIL in body
