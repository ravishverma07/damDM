from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.utils import timezone

from apps.automations.models import (
    Automation,
    AutomationKeyword,
    AutomationPost,
    AutomationRun,
    AutomationStep,
)
from apps.automations.services.matching import automation_matches
from apps.instagram.models import ConnectedAccount, Post

pytestmark = pytest.mark.django_db


def test_keyword_is_required_unless_match_type_is_any(automation):
    with pytest.raises(IntegrityError):
        AutomationKeyword.objects.create(
            automation=automation, keyword="", match_type=AutomationKeyword.MatchType.EXACT
        )


def test_match_any_keyword_may_be_empty(automation):
    assert AutomationKeyword.objects.create(
        automation=automation, keyword="", match_type=AutomationKeyword.MatchType.ANY
    ).pk


def test_an_automation_can_have_many_keywords_and_posts(automation, account):
    AutomationKeyword.objects.create(automation=automation, keyword="price")
    second = Post.objects.create(account=account, external_media_id="18000000000000002")
    automation.posts.add(second)
    assert automation.keywords.count() == 2
    assert automation.posts.count() == 2
    assert automation_matches(automation, "what's the price?")
    assert automation_matches(automation, "link")
    assert not automation_matches(automation, "hello")


def test_automation_without_keywords_never_matches_unless_keywordless(user, account):
    rule = Automation.objects.create(owner=user, account=account, name="Empty")
    assert not automation_matches(rule, "anything")
    rule.trigger_type = Automation.TriggerType.STORY_MENTION
    assert automation_matches(rule, "anything")


def test_default_scope_applies_to_all_posts(user, account):
    assert Automation.objects.create(owner=user, account=account, name="All").applies_to_all_posts


def test_post_from_another_account_is_rejected(automation, other_user):
    foreign = ConnectedAccount.objects.create(owner=other_user, external_account_id="999")
    foreign_post = Post.objects.create(account=foreign, external_media_id="1")
    with pytest.raises(ValidationError):
        AutomationPost(automation=automation, post=foreign_post).clean()


def test_activate_pause_and_schedule(automation):
    automation.pause()
    assert not automation.is_active
    automation.activate()
    assert automation.is_live_at()
    automation.starts_at = timezone.now() + timedelta(hours=1)
    assert not automation.is_live_at()


def test_schedule_must_end_after_it_starts(user, account):
    now = timezone.now()
    with pytest.raises(IntegrityError):
        Automation.objects.create(
            owner=user, account=account, name="Bad", starts_at=now, ends_at=now
        )


def test_step_order_is_unique_per_automation(automation):
    with pytest.raises(IntegrityError):
        AutomationStep.objects.create(
            automation=automation, order=1, step_type=AutomationStep.StepType.DELAY,
            config={"seconds": 5},
        )


@pytest.mark.parametrize(
    ("step_type", "config"),
    [
        ("send_dm", {}),
        ("public_reply", {"variants": "not a list"}),
        ("ask_to_follow", {"prompt": "x" * 1001}),
    ],
)
def test_step_config_is_validated(automation, step_type, config):
    step = AutomationStep(automation=automation, order=2, step_type=step_type, config=config)
    with pytest.raises(ValidationError):
        step.clean()


def test_valid_step_config_passes(automation):
    AutomationStep(
        automation=automation, order=2, step_type="public_reply",
        config={"variants": ["Sent! Check your DM", "Link sent"]},
    ).clean()


def test_next_step_follows_order(automation):
    first = automation.steps.get(order=1)
    third = AutomationStep.objects.create(
        automation=automation, order=3, step_type="handoff"
    )
    assert first.next_step() == third
    assert third.next_step() is None


def test_only_one_live_run_per_automation_and_contact(automation, contact):
    AutomationRun.objects.create(automation=automation, contact=contact)
    with pytest.raises(IntegrityError):
        AutomationRun.objects.create(
            automation=automation, contact=contact, status=AutomationRun.Status.WAITING_FOLLOW
        )


def test_finished_runs_do_not_block_a_new_one(automation, contact):
    run = AutomationRun.objects.create(automation=automation, contact=contact)
    run.finish()
    assert not run.is_live and run.completed_at
    assert AutomationRun.objects.create(automation=automation, contact=contact).is_live


def test_one_contact_can_be_in_two_automations(automation, contact, user, account):
    other = Automation.objects.create(owner=user, account=account, name="Second reel")
    AutomationRun.objects.create(automation=automation, contact=contact)
    assert AutomationRun.objects.create(automation=other, contact=contact).pk
