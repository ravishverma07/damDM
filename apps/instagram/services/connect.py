"""
Attach an Instagram account to a DamDM user, and hold one across sign-up.

When someone signs in with an Instagram account DamDM has never seen, there
is no user to attach it to yet. The identity is parked in the session (token
encrypted) until they finish creating or signing in to an account.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta

from django.contrib import messages
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.core.crypto import TokenDecryptionError, decrypt, encrypt
from apps.core.models import AuditLog
from apps.core.services import audit

from ..models import ConnectedAccount, Platform
from .oauth import InstagramIdentity

PENDING_SESSION_KEY = "pending_instagram"
PENDING_TTL = timedelta(minutes=15)


class AccountAlreadyClaimed(Exception):
    """The Instagram account is already connected to a different DamDM user."""


def claimed_account(external_account_id: str) -> ConnectedAccount | None:
    return (
        ConnectedAccount.objects.select_related("owner")
        .filter(
            platform=Platform.INSTAGRAM,
            external_account_id=external_account_id,
            status__in=ConnectedAccount.CLAIMED_STATUSES,
        )
        .first()
    )


def connect_account(user: User, identity: InstagramIdentity) -> ConnectedAccount:
    """
    Create or refresh ``user``'s connection for ``identity``.

    An Instagram account belongs to at most one DamDM user at a time; one that
    someone else holds raises ``AccountAlreadyClaimed``.
    """
    account = claimed_account(identity.user_id)
    if account is not None and account.owner_id != user.pk:
        raise AccountAlreadyClaimed(identity.username)
    if account is None:
        # Reconnecting keeps the posts and contacts of this user's earlier connection.
        account = (
            ConnectedAccount.objects.filter(
                owner=user, platform=Platform.INSTAGRAM, external_account_id=identity.user_id
            )
            .order_by("-updated_at")
            .first()
        )

    newly_connected = account is None or not account.is_claimed
    if account is None:
        account = ConnectedAccount(
            owner=user, platform=Platform.INSTAGRAM, external_account_id=identity.user_id
        )
    if newly_connected:
        account.connected_at = timezone.now()
    account.app_scoped_user_id = identity.app_scoped_id or account.app_scoped_user_id
    account.username = identity.username
    account.display_name = identity.name[:150]
    account.account_type = (
        identity.account_type
        if identity.account_type in ConnectedAccount.AccountType.values
        else ConnectedAccount.AccountType.UNKNOWN
    )
    picture = identity.profile_picture_url
    account.profile_picture_url = picture if len(picture) <= 1000 else ""
    account.followers_count = identity.followers_count
    account.scopes = list(identity.scopes)
    account.status = ConnectedAccount.Status.ACTIVE
    account.last_error = ""
    account.set_access_token(identity.access_token, identity.expires_at)
    try:
        with transaction.atomic():
            account.save()
    except IntegrityError as exc:  # someone else claimed it between our check and save
        raise AccountAlreadyClaimed(identity.username) from exc

    action = AuditLog.Action.INSTAGRAM_CONNECTED
    if not newly_connected:
        action = AuditLog.Action.INSTAGRAM_TOKEN_REFRESHED
    audit(action, actor=user, target=account)
    user.advance_onboarding(User.Onboarding.INSTAGRAM_CONNECTED)

    if newly_connected:
        from apps.core.queue import enqueue

        from ..tasks import sync_account_task

        transaction.on_commit(lambda: enqueue(sync_account_task, account.pk))
    return account


# --- pending identity held in the session ------------------------------

def stash_pending(request, identity: InstagramIdentity) -> None:
    data = asdict(identity)
    data["access_token"] = encrypt(identity.access_token)
    data["expires_at"] = identity.expires_at.isoformat() if identity.expires_at else None
    data["scopes"] = list(identity.scopes)
    request.session[PENDING_SESSION_KEY] = {
        "identity": data,
        "stashed_at": timezone.now().isoformat(),
    }


def pending_username(request) -> str:
    """Username of the parked Instagram account, without decrypting its token."""
    session = getattr(request, "session", None)
    pending = session.get(PENDING_SESSION_KEY) if session is not None else None
    if not pending or _expired(pending):
        return ""
    return pending.get("identity", {}).get("username", "")


def peek_pending(request) -> InstagramIdentity | None:
    pending = request.session.get(PENDING_SESSION_KEY)
    if not pending:
        return None
    if _expired(pending):
        discard_pending(request)
        return None
    data = dict(pending.get("identity", {}))
    try:
        data["access_token"] = decrypt(data["access_token"])
        data["expires_at"] = (
            datetime.fromisoformat(data["expires_at"]) if data.get("expires_at") else None
        )
        data["scopes"] = tuple(data.get("scopes") or ())
        return InstagramIdentity(**data)
    except (KeyError, TypeError, ValueError, TokenDecryptionError):
        discard_pending(request)
        return None


def discard_pending(request) -> None:
    request.session.pop(PENDING_SESSION_KEY, None)


def attach_pending(request, user: User) -> ConnectedAccount | None:
    """Connect the parked Instagram account to ``user``, if there is one."""
    identity = peek_pending(request)
    if identity is None:
        return None
    discard_pending(request)
    try:
        account = connect_account(user, identity)
    except AccountAlreadyClaimed:
        messages.error(
            request,
            f"@{identity.username} is already connected to another account, so it wasn't added.",
        )
        return None
    messages.success(request, f"Connected Instagram account @{account.username}.")
    return account


def _expired(pending: dict) -> bool:
    try:
        stashed_at = datetime.fromisoformat(pending["stashed_at"])
    except (KeyError, TypeError, ValueError):
        return True
    return timezone.now() - stashed_at > PENDING_TTL
