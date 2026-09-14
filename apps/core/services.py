"""Core service helpers."""

from __future__ import annotations

from .models import AuditLog
from .utils import client_ip, hash_ip


def audit(
    action: str,
    *,
    actor=None,
    target=None,
    target_type: str = "",
    target_id: str = "",
    request=None,
    **context,
) -> AuditLog:
    """Write an audit entry. Never pass tokens or payloads in ``context``."""
    if target is not None:
        target_type = target_type or target.__class__.__name__
        target_id = target_id or str(getattr(target, "pk", ""))
    return AuditLog.objects.create(
        actor=actor,
        action=action,
        target_type=target_type,
        target_id=str(target_id),
        context=context,
        ip_hash=hash_ip(client_ip(request)) if request is not None else "",
    )
