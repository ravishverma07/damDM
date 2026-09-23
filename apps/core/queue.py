"""Enqueue Celery work without letting a missing broker break the request."""

from __future__ import annotations

import logging

from kombu.exceptions import OperationalError

logger = logging.getLogger("damdm.queue")


def enqueue(task, *args, **kwargs) -> bool:
    """
    ``task.delay(...)``, returning False instead of raising when the broker is
    unreachable. Periodic jobs pick up anything that could not be queued.
    """
    try:
        task.delay(*args, **kwargs)
    except OperationalError:
        logger.exception("Could not enqueue %s", task.name)
        return False
    return True


def enqueue_later(task, countdown: int, *args, **kwargs) -> bool:
    """``enqueue`` for work that should run after ``countdown`` seconds."""
    try:
        task.apply_async(args=args, kwargs=kwargs, countdown=countdown)
    except OperationalError:
        logger.exception("Could not schedule %s", task.name)
        return False
    return True
