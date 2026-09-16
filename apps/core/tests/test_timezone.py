from datetime import UTC, datetime

import pytest
from django.http import HttpResponse
from django.test import RequestFactory
from django.utils import timezone

from apps.core.middleware import TIMEZONE_COOKIE, BrowserTimezoneMiddleware, valid_timezone
from apps.core.utils import month_period

pytestmark = pytest.mark.django_db


def _zone_during_request(cookie=None, user=None):
    seen = {}

    def view(request):
        seen["zone"] = timezone.get_current_timezone_name()
        return HttpResponse()

    request = RequestFactory().get("/")
    if cookie is not None:
        request.COOKIES[TIMEZONE_COOKIE] = cookie
    if user is not None:
        request.user = user
    BrowserTimezoneMiddleware(view)(request)
    return seen["zone"]


def test_pages_render_in_the_browsers_timezone():
    assert _zone_during_request("America/New_York") == "America/New_York"
    assert timezone.get_current_timezone_name() == "UTC"  # reset afterwards


@pytest.mark.parametrize("cookie", [None, "", "Not/AZone", "../../etc/passwd", "Asia", "x" * 100])
def test_unknown_zones_fall_back_to_india(cookie):
    assert _zone_during_request(cookie) == "Asia/Kolkata"


def test_signed_in_users_remember_their_zone(user):
    assert _zone_during_request(user=user) == "Asia/Kolkata"  # default "UTC" means unknown
    _zone_during_request("Europe/London", user=user)
    user.refresh_from_db()
    assert user.timezone == "Europe/London"
    assert _zone_during_request(user=user) == "Europe/London"  # no cookie on this device yet


def test_valid_timezone():
    assert valid_timezone("Asia/Kolkata") == "Asia/Kolkata"
    assert valid_timezone("nope") == ""


def test_usage_month_is_calculated_in_utc_whatever_the_viewer_sees():
    # 31 Jan 20:00 UTC is already 1 Feb in India.
    moment = datetime(2026, 1, 31, 20, 0, tzinfo=UTC)
    with timezone.override("Asia/Kolkata"):
        assert month_period(moment)[0].month == 1


def test_base_page_reports_the_browser_zone(client):
    body = client.get("/").content.decode()
    assert "resolvedOptions().timeZone" in body and "damdm_tz=" in body
