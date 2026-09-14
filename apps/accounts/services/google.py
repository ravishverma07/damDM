"""
Google sign-in: OAuth 2.0 authorization-code flow against OpenID Connect.

The identity comes from the userinfo endpoint over TLS using the access token
we just received, so no ID-token signature verification is needed.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
from django.conf import settings
from django.urls import reverse

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
TIMEOUT_SECONDS = 10.0


class GoogleOAuthError(Exception):
    """Google sign-in failed. The message is safe to show to the user."""


@dataclass(frozen=True)
class GoogleIdentity:
    sub: str
    email: str
    email_verified: bool
    name: str = ""
    picture: str = ""


#: Returned by ``exchange_code`` when ``GOOGLE_MOCK`` is on.
MOCK_IDENTITY = GoogleIdentity(
    sub="mock-google-sub-1",
    email="mock.google.user@example.com",
    email_verified=True,
    name="Mock Google User",
)


def is_configured() -> bool:
    return settings.GOOGLE_MOCK or bool(settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET)


def authorize_url(state: str) -> str:
    """Where to send the browser to start sign-in."""
    if settings.GOOGLE_MOCK:
        # Relative, so the callback lands on whatever host the visitor is using.
        query = urlencode({"code": "mock", "state": state})
        return f"{reverse('accounts:google-callback')}?{query}"
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": settings.GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def exchange_code(code: str, *, client: httpx.Client | None = None) -> GoogleIdentity:
    """Trade the callback ``code`` for the signed-in person's identity."""
    if settings.GOOGLE_MOCK:
        return MOCK_IDENTITY

    http = client or httpx.Client(timeout=TIMEOUT_SECONDS)
    try:
        token = _json(
            http.post(
                TOKEN_URL,
                data={
                    "code": code,
                    "client_id": settings.GOOGLE_CLIENT_ID,
                    "client_secret": settings.GOOGLE_CLIENT_SECRET,
                    "redirect_uri": settings.GOOGLE_REDIRECT_URI,
                    "grant_type": "authorization_code",
                },
            )
        )
        access_token = token.get("access_token")
        if not access_token:
            raise GoogleOAuthError("Google didn't complete the sign-in. Please try again.")
        info = _json(http.get(USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}))
    except httpx.HTTPError as exc:
        raise GoogleOAuthError("Couldn't reach Google. Please try again.") from exc
    finally:
        if client is None:
            http.close()

    if not info.get("sub") or not info.get("email"):
        raise GoogleOAuthError("Google didn't share an email address for this account.")
    return GoogleIdentity(
        sub=str(info["sub"]),
        email=str(info["email"]).strip().lower(),
        email_verified=info.get("email_verified") in (True, "true"),
        name=str(info.get("name") or ""),
        picture=str(info.get("picture") or ""),
    )


def _json(response: httpx.Response) -> dict:
    if response.status_code != 200:
        raise GoogleOAuthError("Google rejected the sign-in. Please try again.")
    try:
        return response.json()
    except ValueError as exc:
        raise GoogleOAuthError("Google sent an unexpected response.") from exc
