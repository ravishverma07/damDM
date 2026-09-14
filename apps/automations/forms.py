"""The automation builder: one friendly form that maps onto the automation tables."""

from __future__ import annotations

from django import forms
from django.db.models import Q

from apps.instagram.models import ConnectedAccount, Post

from .models import MAX_DM_LENGTH, Automation
from .services.builder import (
    AUDIENCE_CHOICES,
    AUDIENCE_EVERYONE,
    AUDIENCE_FOLLOW_FIRST,
    AUDIENCE_FOLLOWERS,
    KEYWORD_TRIGGERS,
    split_keywords,
    split_lines,
)

Trigger = Automation.TriggerType
Scope = Automation.PostScope

MAX_KEYWORDS = 20
MAX_KEYWORD_LENGTH = 100
MAX_PUBLIC_REPLY_LENGTH = 300
#: Instagram's limit for button titles.
BUTTON_TITLE_LENGTH = 20

TRIGGER_OPTIONS = (
    {"value": Trigger.COMMENT, "icon": "💬", "label": "Comments on a post",
     "hint": "Someone comments on your post or Reel"},
    {"value": Trigger.DM_KEYWORD, "icon": "✉️", "label": "Sends you a DM",
     "hint": "Someone messages you a keyword"},
    {"value": Trigger.STORY_REPLY, "icon": "↩️", "label": "Replies to your story",
     "hint": "Someone answers one of your stories"},
    {"value": Trigger.STORY_MENTION, "icon": "📣", "label": "Mentions you in a story",
     "hint": "Someone tags you in their story"},
)
AUDIENCE_OPTIONS = (
    {"value": AUDIENCE_EVERYONE, "icon": "🌍", "label": "Everyone",
     "hint": "Anyone who triggers it gets your DM"},
    {"value": AUDIENCE_FOLLOWERS, "icon": "💜", "label": "Only my followers",
     "hint": "People who don't follow you are skipped"},
    {"value": AUDIENCE_FOLLOW_FIRST, "icon": "🔒", "label": "Ask to follow first",
     "hint": "Non-followers must follow you to unlock the DM"},
)
SCOPE_OPTIONS = (
    {"value": Scope.ALL_POSTS, "label": "Any post"},
    {"value": Scope.SPECIFIC_POSTS, "label": "Specific posts"},
    {"value": Scope.NEXT_POST, "label": "My next post"},
)


class AccountChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj: ConnectedAccount) -> str:
        return f"@{obj.username}"


def _textarea(rows: int, placeholder: str = "") -> forms.Textarea:
    return forms.Textarea(attrs={"rows": rows, "placeholder": placeholder})


class AutomationBuilderForm(forms.Form):
    account = AccountChoiceField(queryset=ConnectedAccount.objects.none(), empty_label=None)
    name = forms.CharField(
        max_length=120,
        min_length=2,
        widget=forms.TextInput(
            attrs={"class": "builder-name__input", "placeholder": "Name your automation"}
        ),
    )
    trigger_type = forms.ChoiceField(choices=Trigger.choices)
    post_scope = forms.ChoiceField(choices=Scope.choices, required=False)
    posts = forms.ModelMultipleChoiceField(queryset=Post.objects.none(), required=False)

    reply_to_all = forms.BooleanField(required=False)
    keywords = forms.CharField(
        required=False,
        max_length=1000,
        widget=forms.TextInput(attrs={"placeholder": "LINK, GUIDE", "autocomplete": "off"}),
    )

    public_reply_enabled = forms.BooleanField(required=False)
    public_replies = forms.CharField(
        required=False, max_length=2000, widget=_textarea(3, "Sent! Check your DMs 📩")
    )

    audience = forms.ChoiceField(
        choices=AUDIENCE_CHOICES,
        error_messages={"required": "Choose who should get this automation."},
    )
    follow_prompt = forms.CharField(
        required=False, max_length=MAX_DM_LENGTH, widget=_textarea(3)
    )
    follow_button = forms.CharField(
        required=False,
        max_length=BUTTON_TITLE_LENGTH,
        widget=forms.TextInput(attrs={"placeholder": "I'm following"}),
    )
    not_following_text = forms.CharField(
        required=False, max_length=MAX_DM_LENGTH, widget=_textarea(2)
    )

    dm_text = forms.CharField(
        max_length=MAX_DM_LENGTH,
        widget=_textarea(4, "Hey! Here's the link you asked for:"),
        error_messages={"required": "Write the message people will receive."},
    )
    button_title = forms.CharField(
        required=False,
        max_length=BUTTON_TITLE_LENGTH,
        widget=forms.TextInput(attrs={"placeholder": "Get the link"}),
    )
    button_url = forms.URLField(
        required=False,
        max_length=1000,
        assume_scheme="https",
        widget=forms.URLInput(attrs={"placeholder": "https://yourlink.com"}),
    )

    once_per_contact = forms.BooleanField(required=False)
    go_live = forms.BooleanField(required=False)

    def __init__(self, *args, user, automation: Automation | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        usable = Q(status__in=ConnectedAccount.CLAIMED_STATUSES)
        if automation is not None:
            usable |= Q(pk=automation.account_id)
        accounts = ConnectedAccount.objects.filter(usable, owner=user).order_by("-connected_at")
        self.fields["account"].queryset = accounts
        if accounts.count() <= 1:
            self.fields["account"].widget = forms.HiddenInput()
        self.fields["posts"].queryset = Post.objects.filter(account__in=accounts, is_active=True)

    def clean(self):
        data = super().clean()
        trigger = data.get("trigger_type")
        account = data.get("account")

        words = split_keywords(data.get("keywords"))
        data["keyword_list"] = words
        if trigger in KEYWORD_TRIGGERS and not data.get("reply_to_all"):
            if not words:
                self.add_error("keywords", "Add at least one keyword, or reply to everyone.")
            elif len(words) > MAX_KEYWORDS:
                self.add_error("keywords", f"Use {MAX_KEYWORDS} keywords or fewer.")
            elif any(len(word) > MAX_KEYWORD_LENGTH for word in words):
                self.add_error(
                    "keywords", f"Keep each keyword under {MAX_KEYWORD_LENGTH} characters."
                )

        if trigger == Trigger.COMMENT and data.get("post_scope") == Scope.SPECIFIC_POSTS:
            posts = data.get("posts") or []
            if not posts:
                self.add_error("posts", "Pick at least one post, or choose Any post.")
            elif account is not None and any(post.account_id != account.pk for post in posts):
                self.add_error("posts", "Those posts belong to a different Instagram account.")

        replies = split_lines(data.get("public_replies"))
        data["public_reply_list"] = replies
        if trigger == Trigger.COMMENT and data.get("public_reply_enabled"):
            if not replies:
                self.add_error(
                    "public_replies", "Add at least one reply, or turn public replies off."
                )
            elif any(len(reply) > MAX_PUBLIC_REPLY_LENGTH for reply in replies):
                self.add_error(
                    "public_replies", f"Keep each reply under {MAX_PUBLIC_REPLY_LENGTH} characters."
                )

        if data.get("audience") == AUDIENCE_FOLLOW_FIRST:
            if not (data.get("follow_prompt") or "").strip():
                self.add_error("follow_prompt", "Write the message asking them to follow.")
            if not (data.get("follow_button") or "").strip():
                self.add_error("follow_button", "Add a label for the button.")

        title = (data.get("button_title") or "").strip()
        if data.get("button_url") and not title:
            self.add_error("button_title", "Add text for the link button.")
        if title and not data.get("button_url") and "button_url" not in self.errors:
            self.add_error("button_url", "Add the link the button opens.")
        return data
