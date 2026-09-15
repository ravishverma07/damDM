from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.billing.models import Payment, Subscription
from apps.billing.services.subscriptions import record_payment
from apps.billing.tasks import sync_subscriptions
from apps.core.middleware import DEVICE_COOKIE
from apps.instagram.models import ConnectedAccount
from apps.referrals.models import Payout, Referral, ReferralReward
from apps.referrals.services import (
    ReferralError,
    get_or_create_code,
    mark_payout_paid,
    mask_email,
    reject_payout,
    release_matured_rewards,
    request_payout,
)

pytestmark = pytest.mark.django_db

PASSWORD = "a-really-long-password"


def _invite(browser, referrer):
    code = get_or_create_code(referrer)
    return browser.get(reverse("referral-link", args=[code.code]))


def _sign_up(browser, email):
    return browser.post(
        reverse("accounts:register"),
        {"email": email, "password1": PASSWORD, "password2": PASSWORD},
    )


def _invited_friend(browser, referrer, django_user_model, email="friend@example.com"):
    _invite(browser, referrer)
    _sign_up(browser, email)
    return django_user_model.objects.get(email=email)


def _pay(user, plan, payment_id, vpa="friend@okaxis", subscription=None):
    subscription = subscription or Subscription.objects.create(
        user=user, plan=plan, status=Subscription.Status.ACTIVE,
        provider_subscription_id=f"sub_{payment_id}",
    )
    payment = record_payment(
        subscription,
        {"id": payment_id, "amount": 9900, "currency": "INR", "method": "upi", "vpa": vpa},
        Payment.Status.CAPTURED,
    )
    return subscription, payment


def _available_reward(referrer, django_user_model, n, amount=4950):
    friend = django_user_model.objects.create_user(email=f"f{n}@example.com", password=PASSWORD)
    referral = Referral.objects.create(
        referrer=referrer, referred=friend, code="X", status=Referral.Status.REWARDED
    )
    return ReferralReward.objects.create(
        referrer=referrer, referral=referral, amount=amount,
        status=ReferralReward.Status.AVAILABLE, available_at=timezone.now(),
    )


# --- invites ------------------------------------------------------------------

def test_invite_link_remembers_the_code_and_opens_sign_up(client, user):
    response = _invite(client, user)
    assert response["Location"] == f"{reverse('core:landing')}?auth=signup"
    assert "damdm_ref" in client.cookies
    body = client.get(response["Location"]).content.decode()
    assert "A friend invited you" in body


def test_unknown_code_is_ignored(client):
    response = client.get(reverse("referral-link", args=["NOPE1234"]))
    assert response.status_code == 302
    assert "damdm_ref" not in client.cookies


def test_new_account_from_an_invite_is_linked(client, user, django_user_model):
    friend = _invited_friend(client, user, django_user_model)
    referral = Referral.objects.get(referred=friend)
    assert referral.referrer == user
    assert referral.status == Referral.Status.JOINED


def test_existing_accounts_signing_in_are_never_linked(client, user, other_user):
    type(other_user).objects.filter(pk=other_user.pk).update(
        created_at=timezone.now() - timedelta(days=3)
    )
    _invite(client, user)
    client.post(
        reverse("accounts:login"),
        {"username": other_user.email, "password": "sup3r-secret-pass"},
    )
    assert not Referral.objects.exists()


def test_signing_up_in_the_referrers_own_browser_does_not_count(client, user, django_user_model):
    client.force_login(user)
    client.get(reverse("referrals:overview"))  # the referrer's browser is noted here
    device = client.cookies[DEVICE_COOKIE].value
    client.logout()
    client.cookies[DEVICE_COOKIE] = device

    friend = _invited_friend(client, user, django_user_model)
    assert Referral.objects.get(referred=friend).status == Referral.Status.REJECTED


def test_a_gmail_alias_of_the_referrer_does_not_count(client, user, django_user_model):
    user.email = "ravish.kumar@gmail.com"
    user.save()
    friend = _invited_friend(client, user, django_user_model, email="ravishkumar+1@gmail.com")
    assert Referral.objects.get(referred=friend).status == Referral.Status.REJECTED


# --- rewards --------------------------------------------------------------------

def test_first_payment_earns_half_on_hold(client, user, pro_plan, django_user_model):
    friend = _invited_friend(client, user, django_user_model)
    _, payment = _pay(friend, pro_plan, "pay_f1")

    reward = ReferralReward.objects.get(referrer=user)
    assert reward.amount == 4950 and reward.payment == payment
    assert reward.status == ReferralReward.Status.PENDING
    assert reward.available_at > timezone.now() + timedelta(days=6)
    assert Referral.objects.get(referred=friend).status == Referral.Status.REWARDED


def test_only_the_first_payment_earns(client, user, pro_plan, django_user_model):
    friend = _invited_friend(client, user, django_user_model)
    subscription, _ = _pay(friend, pro_plan, "pay_f1")
    _pay(friend, pro_plan, "pay_f2", subscription=subscription)
    assert ReferralReward.objects.count() == 1


def test_the_free_trial_earns_nothing_until_it_converts(client, user, pro_plan, django_user_model):
    friend = _invited_friend(client, user, django_user_model)
    ConnectedAccount.objects.create(owner=friend, external_account_id="555", username="friend")
    client.post(reverse("billing:start"))
    trial = Subscription.objects.get(user=friend)
    assert trial.status == Subscription.Status.TRIALING
    assert not ReferralReward.objects.exists()

    ended = timezone.now() - timedelta(minutes=1)
    Subscription.objects.filter(pk=trial.pk).update(trial_ends_at=ended)
    sync_subscriptions()
    assert ReferralReward.objects.get(referrer=user).amount == 4950


def test_paying_with_the_referrers_upi_id_does_not_count(
    client, user, pro_plan, django_user_model
):
    _pay(user, pro_plan, "pay_referrer", vpa="ravish@okaxis")
    friend = _invited_friend(client, user, django_user_model)
    _pay(friend, pro_plan, "pay_f1", vpa="RAVISH@okaxis")

    assert not ReferralReward.objects.exists()
    assert Referral.objects.get(referred=friend).status == Referral.Status.REJECTED


def test_rewards_become_available_after_the_hold(client, user, pro_plan, django_user_model):
    friend = _invited_friend(client, user, django_user_model)
    _pay(friend, pro_plan, "pay_f1")
    ReferralReward.objects.update(available_at=timezone.now() - timedelta(minutes=1))
    assert release_matured_rewards() == 1
    assert ReferralReward.objects.get().status == ReferralReward.Status.AVAILABLE


def test_a_refunded_payment_cancels_the_reward(client, user, pro_plan, django_user_model):
    friend = _invited_friend(client, user, django_user_model)
    _, payment = _pay(friend, pro_plan, "pay_f1")
    Payment.objects.filter(pk=payment.pk).update(status=Payment.Status.REFUNDED)
    ReferralReward.objects.update(available_at=timezone.now() - timedelta(minutes=1))
    release_matured_rewards()
    assert ReferralReward.objects.get().status == ReferralReward.Status.CANCELLED


# --- payouts ----------------------------------------------------------------

def test_withdrawal_needs_100_rupees(user, django_user_model):
    _available_reward(user, django_user_model, 1)
    with pytest.raises(ReferralError, match="₹100"):
        request_payout(user, "ravish@okaxis")


def test_withdrawal_collects_available_earnings(user, django_user_model):
    for n in range(3):
        _available_reward(user, django_user_model, n)
    payout = request_payout(user, " Ravish@OKAXIS ")

    assert payout.amount == 14850
    assert payout.upi_id == "ravish@okaxis"
    assert "ravish@okaxis" not in payout.encrypted_upi_id
    statuses = set(payout.rewards.values_list("status", flat=True))
    assert statuses == {ReferralReward.Status.WITHDRAWN}
    with pytest.raises(ReferralError, match="in progress"):
        request_payout(user, "ravish@okaxis")


@pytest.mark.parametrize("upi", ["", "not-a-upi", "name@", "@okaxis", "name@ok axis"])
def test_invalid_upi_ids_are_refused(user, django_user_model, upi):
    for n in range(3):
        _available_reward(user, django_user_model, n)
    with pytest.raises(ReferralError, match="valid UPI"):
        request_payout(user, upi)


def test_paying_and_rejecting_payouts(user, django_user_model):
    for n in range(3):
        _available_reward(user, django_user_model, n)
    payout = request_payout(user, "ravish@okaxis")
    with pytest.raises(ReferralError, match="UTR"):
        mark_payout_paid(payout, reference="")

    reject_payout(payout, note="Wrong UPI ID")
    statuses = set(ReferralReward.objects.values_list("status", flat=True))
    assert statuses == {ReferralReward.Status.AVAILABLE}

    second = request_payout(user, "ravish@okicici")
    mark_payout_paid(second, reference="UTR123456")
    second.refresh_from_db()
    assert second.status == Payout.Status.PAID
    statuses = set(ReferralReward.objects.values_list("status", flat=True))
    assert statuses == {ReferralReward.Status.PAID}


# --- page -------------------------------------------------------------------

def test_referral_page_shows_the_link_and_blocks_early_withdrawals(client, user):
    client.force_login(user)
    body = client.get(reverse("referrals:overview")).content.decode()
    code = get_or_create_code(user).code
    assert code in body
    assert "Share on WhatsApp" in body
    assert "₹49.50" in body

    client.post(reverse("referrals:overview"), {"upi_id": "ravish@okaxis"})
    assert not Payout.objects.exists()


def test_emails_are_masked():
    assert mask_email("ravish@example.com") == "r•••@example.com"


def test_refer_card_sits_on_the_instagram_pages_not_the_dashboard(client, user, account, post):
    client.force_login(user)
    card = 'class="card cta-card refer-teaser refer-teaser--section"'
    assert card in client.get(reverse("instagram:accounts")).content.decode()
    detail = client.get(reverse("instagram:account-detail", args=[account.pk])).content.decode()
    assert card in detail
    assert detail.index(card) > detail.index('class="post-grid"')
    assert "refer-teaser" not in client.get(reverse("core:dashboard")).content.decode()
