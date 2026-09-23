"""
Ending a connection: user disconnects, and Meta's deauthorize and
data-deletion callbacks.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

from django.db import transaction
from django.db.models import Q, QuerySet
from django.utils import timezone

from apps.accounts.models import SocialLogin
from apps.automations.models import Automation
from apps.core import notices
from apps.core.models import AuditLog, DataDeletionRequest
from apps.core.services import audit
from apps.messaging.models import Comment, Contact, Message

from ..models import ConnectedAccount, Platform, Post


class InvalidSignedRequest(Exception):
    """A Meta ``signed_request`` that is malformed or fails signature checks."""


def parse_signed_request(signed_request: str, secret: str) -> dict:
    """Verify and decode a Meta ``signed_request`` (HMAC-SHA256 over the payload)."""
    if not secret or not signed_request or signed_request.count(".") != 1:
        raise InvalidSignedRequest("Malformed signed request.")
    encoded_signature, encoded_payload = signed_request.split(".")
    try:
        signature = _b64decode(encoded_signature)
        payload = json.loads(_b64decode(encoded_payload))
    except ValueError as exc:
        raise InvalidSignedRequest("Undecodable signed request.") from exc

    expected = hmac.new(secret.encode(), encoded_payload.encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(signature, expected):
        raise InvalidSignedRequest("Signature mismatch.")
    if not isinstance(payload, dict) or str(payload.get("algorithm", "")).upper() != "HMAC-SHA256":
        raise InvalidSignedRequest("Unexpected signing algorithm.")
    return payload


def accounts_for_platform_user(user_id: str) -> QuerySet[ConnectedAccount]:
    """Instagram accounts identified by either their account id or app-scoped id."""
    if not user_id:
        return ConnectedAccount.objects.none()
    return ConnectedAccount.objects.filter(platform=Platform.INSTAGRAM).filter(
        Q(external_account_id=user_id) | Q(app_scoped_user_id=user_id)
    )


def disconnect_account(
    account: ConnectedAccount,
    *,
    status: str = ConnectedAccount.Status.DISCONNECTED,
    actor=None,
    reason: str = "user",
) -> int:
    """Drop the token and pause the account's automations. Returns how many were paused."""
    with transaction.atomic():
        account.disconnect(status=status)
        paused = Automation.objects.filter(
            account=account, status=Automation.Status.ACTIVE
        ).update(status=Automation.Status.PAUSED, updated_at=timezone.now())
    audit(
        AuditLog.Action.INSTAGRAM_DISCONNECTED,
        actor=actor,
        target=account,
        reason=reason,
        automations_paused=paused,
    )
    if reason != "user":
        notices.instagram_needs_attention(account)
    return paused


def delete_platform_user_data(deletion: DataDeletionRequest) -> DataDeletionRequest:
    """
    Delete everything held for the Instagram user named in ``deletion``.

    That is every connection to that account (and, by cascade, its posts,
    contacts, comments, messages and automations), the person's contact
    records on other accounts, and their Instagram sign-in identity. The
    DamDM user who connected the account keeps their DamDM login.
    """
    user_id = deletion.external_user_id
    accounts = accounts_for_platform_user(user_id)
    account_ids = list(accounts.values_list("pk", flat=True))
    identity_ids = {user_id, *accounts.values_list("external_account_id", flat=True)}
    as_contact = Contact.objects.filter(
        account__platform=Platform.INSTAGRAM, external_user_id=user_id
    )
    in_scope = Q(account_id__in=account_ids) | Q(contact__in=as_contact)

    counts = {
        "connected_accounts": len(account_ids),
        "posts": Post.objects.filter(account_id__in=account_ids).count(),
        "contacts": Contact.objects.filter(
            Q(account_id__in=account_ids) | Q(pk__in=as_contact)
        ).count(),
        "comments": Comment.objects.filter(in_scope).count(),
        "messages": Message.objects.filter(in_scope).count(),
    }

    with transaction.atomic():
        for account in ConnectedAccount.objects.filter(pk__in=account_ids):
            account.disconnect(status=ConnectedAccount.Status.REVOKED)
        as_contact.delete()
        ConnectedAccount.objects.filter(pk__in=account_ids).delete()
        SocialLogin.objects.filter(
            provider=SocialLogin.Provider.INSTAGRAM, provider_uid__in=identity_ids
        ).delete()
        deletion.mark_completed(**counts)

    audit(AuditLog.Action.DATA_DELETION_COMPLETED, target=deletion, **counts)
    return deletion


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
