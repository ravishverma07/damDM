import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.automations.models import Automation
from apps.automations.services.recipes import RECIPES
from apps.automations.tests.test_builder import builder_data
from apps.instagram.models import ConnectedAccount

pytestmark = pytest.mark.django_db


def test_list_requires_login(client):
    assert client.get(reverse("automations:list")).status_code == 302


def test_list_shows_your_automations_in_plain_english(client, user, automation, other_user):
    theirs = ConnectedAccount.objects.create(owner=other_user, external_account_id="999")
    Automation.objects.create(owner=other_user, account=theirs, name="Not yours")
    client.force_login(user)
    body = client.get(reverse("automations:list")).content.decode()
    assert "Reel link" in body
    assert "Someone comments “LINK” on 1 post" in body
    assert "Not yours" not in body


def test_empty_list_offers_the_starting_points(client, user, account):
    client.force_login(user)
    body = client.get(reverse("automations:list")).content.decode()
    for recipe in RECIPES:
        assert recipe.title in body


def test_new_automation_needs_a_connected_account(client, user):
    client.force_login(user)
    response = client.get(reverse("automations:new"))
    assert response["Location"] == reverse("instagram:accounts")


def test_starting_point_prefills_the_builder(client, user, account):
    client.force_login(user)
    body = client.get(reverse("automations:create", args=["follow-to-unlock"])).content.decode()
    assert 'value="Follow to unlock"' in body
    assert "Who should get it?" in body
    assert 'name="audience" value="follow_first" checked' in body


def test_unknown_starting_point_is_a_404(client, user, account):
    client.force_login(user)
    assert client.get(reverse("automations:create", args=["nope"])).status_code == 404


def test_creating_an_automation(client, paid_user, account):
    user = paid_user
    client.force_login(user)
    response = client.post(
        reverse("automations:create", args=["comment-to-dm"]), builder_data(account)
    )
    assert response["Location"] == reverse("automations:list")
    automation = Automation.objects.get(owner=user)
    assert automation.is_active
    user.refresh_from_db()
    assert user.onboarding_state == User.Onboarding.AUTOMATION_CREATED


def test_invalid_builder_shows_errors_and_keeps_input(client, user, account):
    client.force_login(user)
    response = client.post(
        reverse("automations:create", args=["comment-to-dm"]),
        builder_data(account, keywords="", dm_text="Keep this text"),
    )
    body = response.content.decode()
    assert response.status_code == 200
    assert "Add at least one keyword" in body
    assert "Keep this text" in body
    assert not Automation.objects.exists()


def test_automation_on_an_expired_account_is_saved_as_a_draft(client, user, account):
    account.status = ConnectedAccount.Status.EXPIRED
    account.save()
    client.force_login(user)
    client.post(reverse("automations:create", args=["comment-to-dm"]), builder_data(account))
    assert Automation.objects.get().status == Automation.Status.DRAFT


def test_editing_an_automation(client, user, automation):
    client.force_login(user)
    url = reverse("automations:edit", args=[automation.pk])
    assert "Reel link" in client.get(url).content.decode()

    client.post(url, builder_data(automation.account, name="Renamed", keywords="GUIDE"))
    automation.refresh_from_db()
    assert automation.name == "Renamed"
    assert list(automation.keywords.values_list("keyword", flat=True)) == ["GUIDE"]


def test_other_users_cannot_touch_an_automation(client, other_user, automation):
    client.force_login(other_user)
    assert client.get(reverse("automations:edit", args=[automation.pk])).status_code == 404
    for name in ("automations:status", "automations:delete"):
        assert client.post(reverse(name, args=[automation.pk])).status_code == 404
    assert Automation.objects.filter(pk=automation.pk, status=Automation.Status.ACTIVE).exists()


def test_pause_and_turn_back_on(client, paid_user, automation):
    user = paid_user
    client.force_login(user)
    url = reverse("automations:status", args=[automation.pk])
    client.post(url, {"action": "pause"})
    automation.refresh_from_db()
    assert automation.status == Automation.Status.PAUSED
    client.post(url, {"action": "activate"})
    automation.refresh_from_db()
    assert automation.is_active


def test_cannot_turn_on_when_the_account_is_disconnected(client, user, automation):
    automation.pause()
    automation.account.disconnect()
    client.force_login(user)
    client.post(reverse("automations:status", args=[automation.pk]), {"action": "activate"})
    automation.refresh_from_db()
    assert automation.status == Automation.Status.PAUSED


def test_deleting_an_automation(client, user, automation):
    client.force_login(user)
    response = client.post(reverse("automations:delete", args=[automation.pk]))
    assert response["Location"] == reverse("automations:list")
    assert not Automation.objects.exists()


def test_dashboard_counts_live_automations(client, user, automation):
    client.force_login(user)
    body = client.get(reverse("core:dashboard")).content.decode()
    assert 'stat__value stat__value--on">1<' in body


def test_turning_on_needs_a_trial_or_plan(client, user, account):
    client.force_login(user)
    response = client.post(
        reverse("automations:create", args=["comment-to-dm"]), builder_data(account), follow=True
    )
    assert Automation.objects.get().status == Automation.Status.DRAFT
    assert "Start your free trial" in response.content.decode()


def test_users_without_a_plan_see_the_trial_banner(client, user):
    client.force_login(user)
    assert "Start your 14-day free trial" in client.get(reverse("core:dashboard")).content.decode()


def test_paying_users_see_no_banner(client, paid_user):
    client.force_login(paid_user)
    assert "plan-banner" not in client.get(reverse("core:dashboard")).content.decode()
