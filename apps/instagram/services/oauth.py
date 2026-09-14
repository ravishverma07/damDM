"""
Instagram API with Instagram Login: OAuth code exchange and profile lookup.

The same flow serves "Continue with Instagram" and "Connect Instagram"; the
caller decides what to do with the resulting identity.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlencode

import httpx
from django.conf import settings
from django.urls import reverse
from django.utils import timezone

TOKEN_URL = "https://api.instagram.com/oauth/access_token"
PROFILE_FIELDS = "id,user_id,username,name,account_type,profile_picture_url,followers_count"
TIMEOUT_SECONDS = 10.0

MOCK_USER_ID = "17841400000000099"
MOCK_USERNAME = "mock_creator"
MOCK_TOKEN = "mock-instagram-token"
MOCK_APP_SCOPED_ID = "mock-app-scoped-id"


class InstagramOAuthError(Exception):
    """Instagram sign-in failed. The message is safe to show to the user."""


@dataclass(frozen=True)
class InstagramIdentity:
    # Professional account id - the id webhooks are addressed to.
    user_id: str
    username: str
    access_token: str
    name: str = ""
    account_type: str = "UNKNOWN"
    profile_picture_url: str = ""
    followers_count: int | None = None
    expires_at: datetime | None = None
    scopes: tuple[str, ...] = ()
    # App-scoped id of the same account ("id" from /me).
    app_scoped_id: str = ""


def is_configured() -> bool:
    return settings.META_MOCK or bool(settings.META_APP_ID and settings.META_APP_SECRET)


def authorize_url(state: str) -> str:
    """Where to send the browser to start the Instagram flow."""
    if settings.META_MOCK:
        # Relative, so the callback lands on whatever host the visitor is using.
        query = urlencode({"code": "mock", "state": state})
        return f"{reverse('instagram:callback')}?{query}"
    params = {
        "client_id": settings.META_APP_ID,
        "redirect_uri": settings.META_REDIRECT_URI,
        "response_type": "code",
        "scope": ",".join(settings.META_SCOPES),
        "state": state,
    }
    return f"{settings.META_OAUTH_BASE_URL}/oauth/authorize?{urlencode(params)}"


def exchange_code(code: str, *, client: httpx.Client | None = None) -> InstagramIdentity:
    """Trade the callback ``code`` for a long-lived token and the account profile."""
    if settings.META_MOCK:
        return InstagramIdentity(
            user_id=MOCK_USER_ID,
            username=MOCK_USERNAME,
            access_token=MOCK_TOKEN,
            name="Mock Creator",
            account_type="MEDIA_CREATOR",
            expires_at=timezone.now() + timedelta(days=60),
            scopes=tuple(settings.META_SCOPES),
            app_scoped_id=MOCK_APP_SCOPED_ID,
        )

    graph = settings.META_GRAPH_BASE_URL
    http = client or httpx.Client(timeout=TIMEOUT_SECONDS)
    try:
        short = _json(
            http.post(
                TOKEN_URL,
                data={
                    "client_id": settings.META_APP_ID,
                    "client_secret": settings.META_APP_SECRET,
                    "grant_type": "authorization_code",
                    "redirect_uri": settings.META_REDIRECT_URI,
                    "code": code,
                },
            )
        )
        # Newer responses wrap the token in a one-item "data" list.
        if isinstance(short.get("data"), list) and short["data"]:
            short = short["data"][0]
        if not short.get("access_token"):
            raise InstagramOAuthError("Instagram didn't complete the sign-in. Please try again.")

        long_lived = _json(
            http.get(
                f"{graph}/access_token",
                params={
                    "grant_type": "ig_exchange_token",
                    "client_secret": settings.META_APP_SECRET,
                    "access_token": short["access_token"],
                },
            )
        )
        token = long_lived.get("access_token") or short["access_token"]
        profile = _json(
            http.get(
                f"{graph}/{settings.META_GRAPH_API_VERSION}/me",
                params={"fields": PROFILE_FIELDS, "access_token": token},
            )
        )
    except httpx.HTTPError as exc:
        raise InstagramOAuthError("Couldn't reach Instagram. Please try again.") from exc
    finally:
        if client is None:
            http.close()

    user_id = str(profile.get("user_id") or short.get("user_id") or "")
    if not user_id:
        raise InstagramOAuthError("Instagram didn't return an account id.")

    expires_in = long_lived.get("expires_in")
    return InstagramIdentity(
        user_id=user_id,
        username=str(profile.get("username") or ""),
        access_token=token,
        name=str(profile.get("name") or ""),
        account_type=str(profile.get("account_type") or "UNKNOWN"),
        profile_picture_url=str(profile.get("profile_picture_url") or ""),
        followers_count=profile.get("followers_count"),
        expires_at=timezone.now() + timedelta(seconds=int(expires_in)) if expires_in else None,
        scopes=_scopes(short.get("permissions")),
        app_scoped_id=str(profile.get("id") or ""),
    )


def _scopes(permissions) -> tuple[str, ...]:
    if isinstance(permissions, str):
        permissions = permissions.split(",")
    return tuple(p.strip() for p in permissions or () if p and p.strip())


def _json(response: httpx.Response) -> dict:
    if response.status_code != 200:
        raise InstagramOAuthError("Instagram rejected the sign-in. Please try again.")
    try:
        return response.json()
    except ValueError as exc:
        raise InstagramOAuthError("Instagram sent an unexpected response.") from exc
