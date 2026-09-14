import pytest
from django.urls import reverse

from apps.analytics.models import LinkClick, TrackedLink
from apps.core.utils import hash_ip

pytestmark = pytest.mark.django_db


@pytest.fixture
def link(automation):
    return TrackedLink.objects.create(
        automation=automation, destination_url="https://example.com/guide"
    )


def test_slug_is_generated_and_unique(automation, link):
    second = TrackedLink.objects.create(
        automation=automation, destination_url="https://example.com/other"
    )
    assert link.slug and second.slug and link.slug != second.slug


def test_redirect_goes_to_the_destination(client, link):
    response = client.get(reverse("analytics:link-redirect", kwargs={"slug": link.slug}))
    assert response.status_code == 302
    assert response["Location"] == "https://example.com/guide"


def test_click_is_recorded_and_counted(client, link):
    client.get(reverse("analytics:link-redirect", kwargs={"slug": link.slug}))
    client.get(reverse("analytics:link-redirect", kwargs={"slug": link.slug}))
    link.refresh_from_db()
    assert link.click_count == 2
    assert LinkClick.objects.filter(tracked_link=link).count() == 2


def test_raw_ip_is_never_stored(client, link):
    client.get(
        reverse("analytics:link-redirect", kwargs={"slug": link.slug}),
        REMOTE_ADDR="203.0.113.7",
    )
    click = LinkClick.objects.get()
    assert click.ip_hash == hash_ip("203.0.113.7")
    assert "203.0.113.7" not in click.ip_hash


def test_recipient_is_captured_from_the_query_string(client, link):
    client.get(reverse("analytics:link-redirect", kwargs={"slug": link.slug}) + "?u=99001")
    assert LinkClick.objects.get().recipient_scoped_id == "99001"


def test_unknown_slug_is_a_404(client):
    url = reverse("analytics:link-redirect", kwargs={"slug": "nope1234"})
    assert client.get(url).status_code == 404
