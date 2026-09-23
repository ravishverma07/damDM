"""Sign-in plumbing shared by the email, Google and Instagram flows."""

from __future__ import annotations

import secrets
from urllib.parse import urlsplit

from django.contrib.auth import login
from django.db import transaction
from django.http import HttpResponseRedirect
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from apps.core.models import AuditLog
from apps.core.services import audit
from apps.instagram.services.connect import (
    AccountAlreadyClaimed,
    attach_pending,
    claimed_account,
    connect_account,
    pending_username,
)

from ..forms import EmailLoginForm, InstagramEmailForm, RegistrationForm
from ..models import OAuthState, SocialLogin, User

BACKEND = "apps.accounts.backends.EmailBackend"
AUTH_MODES = ("signin", "signup", "finish")


class SocialAuthError(Exception):
    """A sign-in problem whose message is safe to show to the user."""


def safe_next(request, candidate: str | None) -> str:
    """``candidate`` if it is a same-site URL, else an empty string."""
    if candidate and len(candidate) <= 255 and url_has_allowed_host_and_scheme(
        candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return candidate
    return ""


def complete_login(request, user: User, *, method: str) -> None:
    """Log ``user`` in and connect any Instagram account parked during sign-up."""
    from apps.referrals.services import attach_referral

    login(request, user, backend=BACKEND)
    audit(AuditLog.Action.USER_LOGIN, actor=user, request=request, method=method)
    attach_referral(request, user)
    attach_pending(request, user)


def build_auth_panel(
    request,
    *,
    mode: str = "signin",
    next_url: str | None = None,
    login_form=None,
    signup_form=None,
    finish_form=None,
) -> dict:
    """Context for ``accounts/partials/auth_panel.html``."""
    from apps.referrals.services import code_from_request, find_code

    pending = pending_username(request)
    if mode not in AUTH_MODES or (mode == "finish" and not pending):
        mode = "signin"
    return {
        "mode": mode,
        "next": safe_next(request, next_url),
        "pending_instagram": pending,
        "invited": find_code(code_from_request(request)) is not None,
        "login_form": login_form or EmailLoginForm(request, auto_id="signin_%s"),
        "signup_form": signup_form or RegistrationForm(auto_id="signup_%s"),
        "finish_form": finish_form or InstagramEmailForm(auto_id="finish_%s"),
        "auto_open": False,
    }


# --- OAuth state ------------------------------------------------------------

def redirect_to_callback_host(request, callback_uri: str) -> HttpResponseRedirect | None:
    """
    Restart the flow on the callback's host when the visitor is on another one
    (127.0.0.1 vs localhost, or localhost vs a tunnel). OAuth state lives in a
    host-bound session cookie, so it must be issued where the provider returns.
    """
    target = urlsplit(callback_uri)
    if not target.netloc or target.netloc == request.get_host():
        return None
    return HttpResponseRedirect(f"{target.scheme}://{target.netloc}{request.get_full_path()}")


def _state_session_key(provider: str) -> str:
    return f"oauth_state:{provider}"


def begin_oauth(
    request, *, provider: str, purpose: str, user: User | None = None, next_url: str | None = None
) -> OAuthState:
    """
    Issue a state and pin it to this browser's session, so a callback URL
    started in someone else's browser cannot sign this browser in.
    """
    state = OAuthState.issue(
        provider=provider, purpose=purpose, user=user, redirect_to=safe_next(request, next_url)
    )
    request.session[_state_session_key(provider)] = state.state
    return state


def finish_oauth(request, provider: str) -> OAuthState:
    received = request.GET.get("state", "")
    expected = request.session.pop(_state_session_key(provider), "")
    if not received or not expected or not secrets.compare_digest(received, expected):
        raise SocialAuthError("That sign-in link isn't valid any more. Please try again.")
    state = OAuthState.objects.filter(state=received, provider=provider).first()
    if state is None or not state.is_valid:
        raise SocialAuthError("That sign-in took too long. Please try again.")
    state.consume()
    return state


# --- identities -------------------------------------------------------------

def link_social_login(
    user: User, *, provider: str, uid: str, email: str = "", extra: dict | None = None
) -> SocialLogin | None:
    """
    Record (or move) a sign-in identity to ``user``. Returns None when the user
    already has a different identity for this provider.
    """
    if SocialLogin.objects.filter(user=user, provider=provider).exclude(provider_uid=uid).exists():
        return None
    link = SocialLogin.objects.filter(provider=provider, provider_uid=uid).first()
    link = link or SocialLogin(provider=provider, provider_uid=uid)
    link.user = user
    link.email = email or link.email
    link.extra_data = extra or link.extra_data
    link.last_login_at = timezone.now()
    link.save()
    return link


@transaction.atomic
def user_for_google(identity) -> tuple[User, bool]:
    """
    Find or create the user for a Google identity.

    Returns ``(user, password_disabled)``. DamDM never confirms emails, so when
    Google proves ownership of an address an unconfirmed password account
    already uses, whoever set that password may not own the address. The
    password stops working (which also ends its sessions); the owner can keep
    using Google or reset the password by email.
    """
    if not identity.email_verified:
        raise SocialAuthError("Your Google account's email address isn't verified.")

    password_disabled = False
    link = (
        SocialLogin.objects.select_related("user")
        .filter(provider=SocialLogin.Provider.GOOGLE, provider_uid=identity.sub)
        .first()
    )
    if link is not None:
        user = link.user
    else:
        user = User.objects.filter(email=identity.email).first()
        if user is None:
            user = User.objects.create_user(
                email=identity.email, full_name=identity.name[:150], email_verified=True
            )
        else:
            if SocialLogin.objects.filter(user=user, provider=SocialLogin.Provider.GOOGLE).exists():
                raise SocialAuthError("This email is already linked to a different Google account.")
            if not user.email_verified:
                if user.has_usable_password():
                    user.set_unusable_password()
                    password_disabled = True
                user.email_verified = True
                user.save(update_fields=["password", "email_verified", "updated_at"])

    if not user.is_active:
        raise SocialAuthError("This account has been disabled.")
    _refresh_profile_from_google(user, identity)
    link_social_login(
        user,
        provider=SocialLogin.Provider.GOOGLE,
        uid=identity.sub,
        email=identity.email,
        extra={"name": identity.name, "picture": identity.picture},
    )
    return user, password_disabled


def _refresh_profile_from_google(user: User, identity) -> None:
    """Keep the Google photo current, and fill in a missing name."""
    changed = []
    picture = identity.picture if identity.picture.startswith("https://") else ""
    if picture and len(picture) <= 1000 and user.avatar_url != picture:
        user.avatar_url = picture
        changed.append("avatar_url")
    if identity.name and not user.full_name:
        user.full_name = identity.name[:150]
        changed.append("full_name")
    if changed:
        user.save(update_fields=[*changed, "updated_at"])


@transaction.atomic
def user_for_instagram(identity) -> User | None:
    """
    The user an Instagram sign-in belongs to, refreshing their connection.

    The current holder of the connected account wins; otherwise a previous
    Instagram sign-in identity is used. Returns None for an unknown account,
    in which case the caller asks for an email to create one.
    """
    account = claimed_account(identity.user_id)
    if account is not None:
        user = account.owner
    else:
        link = (
            SocialLogin.objects.select_related("user")
            .filter(provider=SocialLogin.Provider.INSTAGRAM, provider_uid=identity.user_id)
            .first()
        )
        if link is None:
            return None
        user = link.user

    if not user.is_active:
        raise SocialAuthError("This account has been disabled.")
    try:
        connect_account(user, identity)
    except AccountAlreadyClaimed as exc:
        raise SocialAuthError("This Instagram account is connected elsewhere.") from exc
    link_social_login(
        user,
        provider=SocialLogin.Provider.INSTAGRAM,
        uid=identity.user_id,
        extra={"username": identity.username},
    )
    return user
