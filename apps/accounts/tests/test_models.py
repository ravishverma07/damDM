from datetime import timedelta

import pytest
from django.db import IntegrityError
from django.utils import timezone

from apps.accounts.models import OAuthState, SocialLogin, User

pytestmark = pytest.mark.django_db


def test_email_is_normalised_to_lowercase(django_user_model):
    user = django_user_model.objects.create_user(
        email="Creator@Example.COM", password="pw-long-enough"
    )
    assert user.email == "creator@example.com"


def test_email_must_be_unique(user, django_user_model):
    with pytest.raises(IntegrityError):
        django_user_model.objects.create(email=user.email)


def test_creating_a_user_without_email_fails(django_user_model):
    with pytest.raises(ValueError):
        django_user_model.objects.create_user(email="", password="pw-long-enough")


def test_superuser_flags(django_user_model):
    admin = django_user_model.objects.create_superuser(
        email="admin@example.com", password="pw-long-enough"
    )
    assert admin.is_staff and admin.is_superuser and admin.email_verified


def test_str_is_the_email(user):
    assert str(user) == "creator@example.com"


def test_new_user_is_unverified_and_at_the_first_onboarding_step(user):
    assert user.email_verified is False
    assert user.onboarding_state == User.Onboarding.SIGNED_UP


def test_onboarding_never_moves_backwards(user):
    user.advance_onboarding(User.Onboarding.INSTAGRAM_CONNECTED)
    user.advance_onboarding(User.Onboarding.SIGNED_UP)
    assert user.onboarding_state == User.Onboarding.INSTAGRAM_CONNECTED


def test_social_login_is_unique_per_provider_uid(user, other_user):
    SocialLogin.objects.create(user=user, provider="google", provider_uid="g-1")
    with pytest.raises(IntegrityError):
        SocialLogin.objects.create(user=other_user, provider="google", provider_uid="g-1")


def test_oauth_state_is_single_use_and_expires(user):
    state = OAuthState.issue(provider="instagram", user=user)
    assert state.is_valid
    state.consume()
    assert not state.is_valid

    stale = OAuthState.issue(provider="instagram", user=user)
    stale.expires_at = timezone.now() - timedelta(seconds=1)
    assert not stale.is_valid


def test_login_state_may_be_anonymous_but_connect_state_may_not(db):
    assert OAuthState.issue(provider="google", purpose=OAuthState.Purpose.LOGIN).pk
    with pytest.raises(IntegrityError):
        OAuthState.issue(provider="instagram", purpose=OAuthState.Purpose.CONNECT)
