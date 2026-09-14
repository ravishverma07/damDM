import pytest
from django.core.exceptions import PermissionDenied

from apps.core.mixins import require_owner

pytestmark = pytest.mark.django_db


def test_owner_passes(automation, user):
    require_owner(automation, user)


def test_non_owner_is_denied(automation, other_user):
    with pytest.raises(PermissionDenied):
        require_owner(automation, other_user)


def test_nested_owner_field(account, post, other_user, user):
    require_owner(post, user, owner_field="account__owner")
    with pytest.raises(PermissionDenied):
        require_owner(post, other_user, owner_field="account__owner")
