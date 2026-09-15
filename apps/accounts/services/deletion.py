"""
Deleting an account from the app only flags it.

Nothing is removed or changed and no outside service is called: the user row,
email, connected Instagram accounts, automations and history all stay. The
account is marked deleted and can no longer sign in.
"""

from __future__ import annotations

from django.utils import timezone

from ..models import User


def close_account(user: User) -> None:
    user.account_status = User.AccountStatus.DELETED
    user.deleted_at = timezone.now()
    user.is_active = False  # blocks sign-in and ends any other open sessions
    user.save(update_fields=["account_status", "deleted_at", "is_active", "updated_at"])
