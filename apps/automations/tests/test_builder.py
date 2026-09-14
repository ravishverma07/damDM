import pytest

from apps.automations.forms import AutomationBuilderForm
from apps.automations.models import Automation, AutomationKeyword, AutomationStep
from apps.automations.services.builder import (
    describe_automation,
    initial_from_automation,
    save_automation,
    split_keywords,
)
from apps.automations.services.matching import automation_matches
from apps.instagram.models import ConnectedAccount, Post

pytestmark = pytest.mark.django_db

StepType = AutomationStep.StepType


def builder_data(account, **overrides) -> dict:
    data = {
        "account": account.pk,
        "name": "Reel link",
        "trigger_type": "comment",
        "post_scope": "all_posts",
        "audience": "everyone",
        "keywords": "LINK, guide, link",
        "public_reply_enabled": "on",
        "public_replies": "Sent! Check your DMs\nDone, look in your inbox",
        "follow_prompt": "Follow me first",
        "follow_button": "I'm following",
        "not_following_text": "Not following yet",
        "dm_text": "Here you go",
        "button_title": "Open guide",
        "button_url": "https://example.com/guide",
        "once_per_contact": "on",
        "go_live": "on",
    }
    data.update(overrides)
    return {key: value for key, value in data.items() if value not in ("", None, False)}


def build(user, account, automation=None, **overrides) -> Automation:
    form = AutomationBuilderForm(
        builder_data(account, **overrides), user=user, automation=automation
    )
    assert form.is_valid(), form.errors
    return save_automation(
        user, form.cleaned_data, automation=automation, go_live=form.cleaned_data["go_live"]
    )


def errors(user, account, **overrides) -> dict:
    form = AutomationBuilderForm(builder_data(account, **overrides), user=user)
    assert not form.is_valid()
    return form.errors


def test_split_keywords_trims_and_dedupes_ignoring_case():
    assert split_keywords(" LINK, guide ,link\nPrice ,, ") == ["LINK", "guide", "Price"]


def test_comment_automation_creates_keywords_and_ordered_steps(user, account):
    automation = build(user, account, audience="follow_first")

    assert automation.status == Automation.Status.ACTIVE
    assert sorted(automation.keywords.values_list("keyword", flat=True)) == ["LINK", "guide"]
    assert [s.step_type for s in automation.steps.all()] == [
        StepType.PUBLIC_REPLY, StepType.ASK_TO_FOLLOW, StepType.SEND_DM,
    ]
    dm = automation.steps.get(step_type=StepType.SEND_DM).config
    assert dm == {"text": "Here you go",
                  "buttons": [{"title": "Open guide", "url": "https://example.com/guide"}]}
    assert automation_matches(automation, "send me the link please")


def test_specific_posts_are_linked(user, account, post):
    automation = build(user, account, post_scope="specific_posts", posts=[post.pk])
    assert list(automation.posts.all()) == [post]


def test_non_comment_triggers_drop_posts_and_public_replies(user, account, post):
    automation = build(
        user, account, trigger_type="dm_keyword", post_scope="specific_posts", posts=[post.pk]
    )
    assert automation.post_scope == Automation.PostScope.ALL_POSTS
    assert not automation.posts.exists()
    assert [s.step_type for s in automation.steps.all()] == [StepType.SEND_DM]


def test_story_mentions_need_no_keywords(user, account):
    automation = build(user, account, trigger_type="story_mention", keywords="")
    assert not automation.keywords.exists()
    assert automation_matches(automation, "")


def test_reply_to_everyone_uses_a_match_any_rule(user, account):
    automation = build(user, account, keywords="", reply_to_all="on")
    rule = automation.keywords.get()
    assert rule.match_type == AutomationKeyword.MatchType.ANY
    assert automation_matches(automation, "anything at all")


def test_draft_when_not_turned_on(user, account):
    assert build(user, account, go_live="").status == Automation.Status.DRAFT


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"keywords": ""}, "keywords"),
        ({"post_scope": "specific_posts"}, "posts"),
        ({"public_replies": ""}, "public_replies"),
        ({"audience": "follow_first", "follow_prompt": ""}, "follow_prompt"),
        ({"audience": ""}, "audience"),
        ({"button_title": ""}, "button_title"),
        ({"button_url": ""}, "button_url"),
        ({"dm_text": ""}, "dm_text"),
        ({"button_title": "This title is far too long for Instagram"}, "button_title"),
    ],
)
def test_builder_explains_what_is_missing(user, account, overrides, field):
    assert field in errors(user, account, **overrides)


def test_posts_from_someone_elses_account_are_rejected(user, account, other_user):
    theirs = ConnectedAccount.objects.create(owner=other_user, external_account_id="999")
    their_post = Post.objects.create(account=theirs, external_media_id="1")
    assert "posts" in errors(user, account, post_scope="specific_posts", posts=[their_post.pk])


def test_an_account_you_do_not_own_is_rejected(user, other_user):
    theirs = ConnectedAccount.objects.create(owner=other_user, external_account_id="999")
    assert "account" in errors(user, theirs)


def test_editing_round_trips_through_the_form(user, account, post):
    automation = build(
        user, account, audience="follow_first", post_scope="specific_posts", posts=[post.pk]
    )
    initial = initial_from_automation(automation)
    assert initial["keywords"] == "LINK, guide"
    assert initial["public_replies"] == "Sent! Check your DMs\nDone, look in your inbox"
    assert initial["audience"] == "follow_first"
    assert initial["posts"] == [post.pk]
    assert initial["button_url"] == "https://example.com/guide"
    assert initial["go_live"] is True


def test_editing_updates_steps_in_place(user, account):
    automation = build(user, account, audience="follow_first")
    ids = {s.step_type: s.pk for s in automation.steps.all()}

    build(
        user, account, automation=automation, audience="follow_first",
        public_reply_enabled="", dm_text="New text",
    )

    steps = list(automation.steps.all())
    assert [(s.step_type, s.order) for s in steps] == [
        (StepType.ASK_TO_FOLLOW, 1), (StepType.SEND_DM, 2),
    ]
    assert steps[0].pk == ids[StepType.ASK_TO_FOLLOW]
    assert steps[1].pk == ids[StepType.SEND_DM]
    assert steps[1].config["text"] == "New text"


def test_turning_off_a_live_automation_pauses_it(user, account):
    automation = build(user, account)
    build(user, account, automation=automation, go_live="")
    automation.refresh_from_db()
    assert automation.status == Automation.Status.PAUSED


def test_description_reads_like_a_sentence(user, account, post):
    automation = build(
        user, account, audience="follow_first", post_scope="specific_posts", posts=[post.pk]
    )
    summary = describe_automation(automation)
    assert summary["trigger"] == "Someone comments “LINK”, “guide” on 1 post"
    assert summary["actions"] == ["Public reply", "Follow check", "DM with link"]


def test_followers_only_needs_no_follow_step(user, account):
    automation = build(user, account, audience="followers")
    assert automation.audience == Automation.Audience.FOLLOWERS
    assert StepType.ASK_TO_FOLLOW not in [s.step_type for s in automation.steps.all()]
    assert initial_from_automation(automation)["audience"] == "followers"
    assert describe_automation(automation)["actions"][0] == "Followers only"


def test_follow_prompt_is_only_required_when_asking_to_follow(user, account):
    assert build(user, account, audience="followers", follow_prompt="").pk


def test_switching_audience_removes_the_follow_step(user, account):
    automation = build(user, account, audience="follow_first")
    build(user, account, automation=automation, audience="everyone")
    automation.refresh_from_db()
    assert automation.audience == Automation.Audience.EVERYONE
    assert not automation.steps.filter(step_type=StepType.ASK_TO_FOLLOW).exists()
