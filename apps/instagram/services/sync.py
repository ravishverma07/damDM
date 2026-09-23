"""Keep a connected account's profile, posts and token fresh."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.utils import timezone

from apps.core import notices
from apps.core.models import AuditLog
from apps.core.services import audit

from ..models import ConnectedAccount, Post
from .graph import GraphAPIError, InstagramGraph
from .oauth import PROFILE_FIELDS

MEDIA_FIELDS = (
    "id,caption,media_type,media_product_type,permalink,thumbnail_url,media_url,"
    "timestamp,comments_count,like_count"
)
MEDIA_PAGE_SIZE = 50
#: Up to 500 of the most recent posts per sync.
MAX_MEDIA_PAGES = 10
MAX_URL_LENGTH = 1000


@dataclass
class SyncResult:
    created: int = 0
    updated: int = 0


def sync_account(account: ConnectedAccount, *, graph: InstagramGraph | None = None) -> SyncResult:
    """Refresh the profile and upsert recent posts. Raises ``GraphAPIError``."""
    result = SyncResult()
    with graph or InstagramGraph(account.access_token) as api:
        profile = api.get("me", {"fields": PROFILE_FIELDS})
        media = api.paginate(
            "me/media",
            {"fields": MEDIA_FIELDS, "limit": MEDIA_PAGE_SIZE},
            max_pages=MAX_MEDIA_PAGES,
        )
        for item in media:
            created = _upsert_post(account, item)
            if created is None:
                continue
            if created:
                result.created += 1
            else:
                result.updated += 1

    _apply_profile(account, profile)
    account.last_synced_at = timezone.now()
    account.last_error = ""
    account.save(
        update_fields=[
            "username", "display_name", "account_type", "profile_picture_url",
            "followers_count", "app_scoped_user_id", "last_synced_at", "last_error", "updated_at",
        ]
    )
    from apps.coupons.services import notify_matching_coupons

    notify_matching_coupons(account)  # the username may have just changed
    return result


def refresh_account_token(
    account: ConnectedAccount, *, graph: InstagramGraph | None = None
) -> None:
    """Swap the long-lived token for a fresh one. Raises ``GraphAPIError``."""
    with graph or InstagramGraph(account.access_token) as api:
        data = api.refresh_token()
    token = data.get("access_token")
    if not token:
        raise GraphAPIError("Instagram didn't return a refreshed token.")
    expires_in = data.get("expires_in")
    account.set_access_token(
        token, timezone.now() + timedelta(seconds=int(expires_in)) if expires_in else None
    )
    account.status = ConnectedAccount.Status.ACTIVE
    account.last_error = ""
    account.save(
        update_fields=[
            "encrypted_access_token", "token_expires_at", "status", "last_error", "updated_at",
        ]
    )
    audit(AuditLog.Action.INSTAGRAM_TOKEN_REFRESHED, target=account)


def record_failure(account: ConnectedAccount, exc: GraphAPIError) -> None:
    """Keep the error visible; change status only when the token or permission is gone."""
    account.last_error = str(exc)[:2000]
    fields = ["last_error", "updated_at"]
    previous = account.status
    if exc.is_token_error:
        account.status = ConnectedAccount.Status.EXPIRED
        fields.append("status")
    elif exc.is_permission_error:
        account.status = ConnectedAccount.Status.ERROR
        fields.append("status")
    account.save(update_fields=fields)
    if account.status != previous:
        notices.instagram_needs_attention(account)


def _upsert_post(account: ConnectedAccount, item: dict) -> bool | None:
    """Create or update one post. Returns whether it was created, or None if skipped."""
    media_id = str(item.get("id") or "")
    if not media_id:
        return None
    _, created = Post.objects.update_or_create(
        account=account,
        external_media_id=media_id,
        defaults={
            "media_type": _choice(item.get("media_type"), Post.MediaType, Post.MediaType.IMAGE),
            "product_type": _choice(
                item.get("media_product_type"), Post.ProductType, Post.ProductType.FEED
            ),
            "caption": item.get("caption") or "",
            "permalink": _url(item.get("permalink")),
            # Videos have a thumbnail; images and carousels only a media URL.
            "thumbnail_url": _url(item.get("thumbnail_url") or item.get("media_url")),
            "published_at": _timestamp(item.get("timestamp")),
            "comments_count": int(item.get("comments_count") or 0),
            "like_count": int(item.get("like_count") or 0),
            "is_active": True,
        },
    )
    return created


def _apply_profile(account: ConnectedAccount, profile: dict) -> None:
    account.username = profile.get("username") or account.username
    account.display_name = (profile.get("name") or "")[:150]
    account.account_type = _choice(
        profile.get("account_type"), ConnectedAccount.AccountType, account.account_type
    )
    account.profile_picture_url = _url(profile.get("profile_picture_url"))
    if profile.get("followers_count") is not None:
        account.followers_count = int(profile["followers_count"])
    if profile.get("id"):
        account.app_scoped_user_id = str(profile["id"])


def _choice(value, choices, default):
    return value if value in choices.values else default


def _url(value) -> str:
    """URLs longer than the column are dropped - a truncated URL is a broken one."""
    value = str(value or "")
    return value if len(value) <= MAX_URL_LENGTH else ""


def _timestamp(value) -> datetime | None:
    try:
        return datetime.strptime(str(value), "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        return None
