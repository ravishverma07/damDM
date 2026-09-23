"""Push every new dashboard notification to the user's devices."""

from __future__ import annotations

from django.contrib.auth.signals import user_logged_out
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Notification, PushSubscription


@receiver(post_save, sender=Notification, dispatch_uid="push-new-notification")
def push_new_notification(sender, instance: Notification, created: bool, **kwargs) -> None:
    if not created:
        return
    from .notify import push_only

    push_only(
        instance.user,
        title=instance.title,
        body=instance.body,
        url=instance.action_url or "/dashboard/",
        tag=instance.kind,
    )


@receiver(user_logged_out, dispatch_uid="forget-push-on-logout")
def forget_push_on_logout(sender, request, user, **kwargs) -> None:
    """A shared phone shouldn't keep getting the previous user's notifications."""
    endpoint = request.session.get("push_endpoint") if request is not None else None
    if user is not None and endpoint:
        PushSubscription.objects.filter(user=user, endpoint=endpoint).delete()
