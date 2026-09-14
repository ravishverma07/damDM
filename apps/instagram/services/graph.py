"""
Thin client for the Instagram Graph API (graph.instagram.com).

Every call goes through ``InstagramGraph`` so errors are classified in one
place. With ``META_MOCK`` on, fixtures are returned instead of calling Meta.
"""

from __future__ import annotations

from collections.abc import Iterator

import httpx
from django.conf import settings

from .oauth import MOCK_APP_SCOPED_ID, MOCK_USER_ID, MOCK_USERNAME

TIMEOUT_SECONDS = 15.0
TOKEN_ERROR_CODES = frozenset({102, 190})
RATE_LIMIT_CODES = frozenset({4, 17, 32, 613})


class GraphAPIError(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: int | None = None,
        subcode: int | None = None,
        status: int | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.subcode = subcode
        self.status = status

    @property
    def is_token_error(self) -> bool:
        """The token is invalid, expired or revoked; the user must reconnect."""
        return self.code in TOKEN_ERROR_CODES

    @property
    def is_rate_limited(self) -> bool:
        return self.code in RATE_LIMIT_CODES

    @property
    def is_permission_error(self) -> bool:
        return self.code == 10 or (self.code is not None and 200 <= self.code < 300)

    @property
    def is_transient(self) -> bool:
        """Worth retrying later: network failures, 5xx and rate limits."""
        return self.code is None or self.is_rate_limited


class InstagramGraph:
    def __init__(self, access_token: str, *, client: httpx.Client | None = None):
        self._token = access_token
        self._client = client
        self._owns_client = client is None

    def __enter__(self) -> InstagramGraph:
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client and self._client is not None:
            self._client.close()
            self._client = None

    def get(self, path: str, params: dict | None = None) -> dict:
        """GET a Graph path (``"me/media"``) or a ``paging.next`` URL."""
        if settings.META_MOCK:
            return mock_response(path)
        if path.startswith(("http://", "https://")):
            # Paging links already carry every parameter, including the token.
            if not path.startswith(f"{settings.META_GRAPH_BASE_URL}/"):
                raise GraphAPIError("Refusing to follow a link outside the Graph API.")
            url, query = path, None
        else:
            base = f"{settings.META_GRAPH_BASE_URL}/{settings.META_GRAPH_API_VERSION}"
            url = f"{base}/{path.lstrip('/')}"
            query = {**(params or {}), "access_token": self._token}
        return self._request(url, query)

    def paginate(
        self, path: str, params: dict | None = None, *, max_pages: int = 10
    ) -> Iterator[dict]:
        """Yield items across ``paging.next`` links, fetching at most ``max_pages`` pages."""
        next_path, next_params = path, params
        for _ in range(max_pages):
            page = self.get(next_path, next_params)
            yield from page.get("data") or []
            next_path, next_params = (page.get("paging") or {}).get("next"), None
            if not next_path:
                return

    def refresh_token(self) -> dict:
        """Exchange a long-lived token (at least 24 hours old) for a fresh 60-day one."""
        if settings.META_MOCK:
            return {"access_token": self._token, "token_type": "bearer", "expires_in": 5184000}
        return self._request(
            f"{settings.META_GRAPH_BASE_URL}/refresh_access_token",
            {"grant_type": "ig_refresh_token", "access_token": self._token},
        )

    def _request(self, url: str, params: dict | None) -> dict:
        if self._client is None:
            self._client = httpx.Client(timeout=TIMEOUT_SECONDS)
        try:
            response = self._client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise GraphAPIError("Couldn't reach Instagram.") from exc
        return _parse(response)


def _parse(response: httpx.Response) -> dict:
    try:
        body = response.json()
    except ValueError:
        body = None
    error = body.get("error") if isinstance(body, dict) else None
    if response.status_code == 200 and isinstance(body, dict) and error is None:
        return body
    if isinstance(error, dict):
        raise GraphAPIError(
            str(error.get("message") or "Instagram API error")[:500],
            code=error.get("code"),
            subcode=error.get("error_subcode"),
            status=response.status_code,
        )
    raise GraphAPIError(
        f"Instagram API returned HTTP {response.status_code}.", status=response.status_code
    )


# --- mock mode ---------------------------------------------------------------

MOCK_PROFILE = {
    "id": MOCK_APP_SCOPED_ID,
    "user_id": MOCK_USER_ID,
    "username": MOCK_USERNAME,
    "name": "Mock Creator",
    "account_type": "MEDIA_CREATOR",
    "profile_picture_url": "",
    "followers_count": 1234,
}

MOCK_MEDIA = [
    {
        "id": "18000000000000101",
        "caption": "Comment LINK and I'll DM you the free guide",
        "media_type": "VIDEO",
        "media_product_type": "REELS",
        "permalink": "https://www.instagram.com/reel/mock101/",
        "timestamp": "2026-09-01T10:00:00+0000",
        "comments_count": 42,
        "like_count": 310,
    },
    {
        "id": "18000000000000102",
        "caption": "Behind the scenes of this week's shoot",
        "media_type": "IMAGE",
        "media_product_type": "FEED",
        "permalink": "https://www.instagram.com/p/mock102/",
        "timestamp": "2026-08-28T18:30:00+0000",
        "comments_count": 8,
        "like_count": 95,
    },
    {
        "id": "18000000000000103",
        "caption": "5 tips carousel - comment TIPS for the checklist",
        "media_type": "CAROUSEL_ALBUM",
        "media_product_type": "FEED",
        "permalink": "https://www.instagram.com/p/mock103/",
        "timestamp": "2026-08-20T09:15:00+0000",
        "comments_count": 17,
        "like_count": 204,
    },
]


def mock_response(path: str) -> dict:
    path = path.strip("/")
    if path == "me":
        return dict(MOCK_PROFILE)
    if path == "me/media":
        return {"data": [dict(item) for item in MOCK_MEDIA]}
    return {}
