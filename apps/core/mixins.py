"""Ownership enforcement shared by every dashboard view."""

from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import QuerySet


class OwnerQuerysetMixin(LoginRequiredMixin):
    """
    Restrict a view's queryset to objects owned by ``request.user``.

    ``owner_field`` may traverse relations, e.g. ``"account__owner"``.
    Every object-level view in the dashboard must go through this so a
    guessed primary key can never leak another creator's data.
    """

    owner_field: str = "owner"

    def get_queryset(self) -> QuerySet:
        queryset = super().get_queryset()
        return queryset.filter(**{self.owner_field: self.request.user})


def require_owner(obj, user, owner_field: str = "owner") -> None:
    """Raise PermissionDenied unless ``user`` owns ``obj``."""
    owner = obj
    for part in owner_field.split("__"):
        owner = getattr(owner, part, None)
    if owner != user:
        raise PermissionDenied("You do not have access to this object.")
