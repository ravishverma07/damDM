"""Background jobs shared across apps."""

from __future__ import annotations

from celery import shared_task

from .notify import deliver


@shared_task(ignore_result=True)
def send_push(user_id: int, payload: dict) -> None:
    deliver(user_id, payload)
