import pytest

from apps.core.models import DataDeletionRequest

pytestmark = pytest.mark.django_db


def test_deletion_request_gets_an_unguessable_code_and_completes():
    first = DataDeletionRequest.issue(platform="instagram", external_user_id="99001")
    second = DataDeletionRequest.issue(platform="instagram", external_user_id="99001")
    assert len(first.confirmation_code) >= 32
    assert first.confirmation_code != second.confirmation_code

    first.mark_completed(contacts=1, messages=4)
    first.refresh_from_db()
    assert first.status == DataDeletionRequest.Status.COMPLETED
    assert first.deleted_counts == {"contacts": 1, "messages": 4}
