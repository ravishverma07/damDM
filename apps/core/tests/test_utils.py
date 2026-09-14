from datetime import date

from django.test import override_settings

from apps.core.logging import scrub
from apps.core.utils import hash_ip, month_period, random_slug


def test_hash_ip_is_stable_and_not_reversible():
    hashed = hash_ip("203.0.113.7")
    assert hashed == hash_ip("203.0.113.7")
    assert "203.0.113.7" not in hashed
    assert len(hashed) == 64


def test_hash_ip_depends_on_the_salt():
    with override_settings(IP_HASH_SALT="salt-a"):
        a = hash_ip("203.0.113.7")
    with override_settings(IP_HASH_SALT="salt-b"):
        b = hash_ip("203.0.113.7")
    assert a != b


def test_hash_ip_of_nothing_is_empty():
    assert hash_ip("") == ""
    assert hash_ip(None) == ""


def test_month_period_spans_the_calendar_month():
    start, end = month_period(date(2026, 3, 17))
    assert (start, end) == (date(2026, 3, 1), date(2026, 4, 1))


def test_month_period_rolls_over_the_year():
    start, end = month_period(date(2026, 12, 5))
    assert (start, end) == (date(2026, 12, 1), date(2027, 1, 1))


def test_random_slug_length_and_alphabet():
    slug = random_slug(10)
    assert len(slug) == 10
    assert slug.isalnum() and slug.islower() or slug.isdigit()


def test_log_scrubbing_removes_tokens():
    assert "IGQVJXaaaaaaaaaaaaaaaaaaaaaaa" not in scrub(
        "calling graph with IGQVJXaaaaaaaaaaaaaaaaaaaaaaa"
    )
    assert scrub("?access_token=abc123&x=1") == "?access_token=[REDACTED]&x=1"
    assert "sk_test_51abc" not in scrub("key sk_test_51abc used")


def test_log_filter_scrubs_tokens_inside_non_string_arguments():
    import logging

    import httpx

    from apps.core.logging import SensitiveDataFilter

    url = httpx.URL("https://graph.instagram.com/me?access_token=IGQVJsecret&fields=id")
    record = logging.LogRecord(
        "httpx", logging.INFO, __file__, 1, "HTTP Request: %s %s", ("GET", url), None
    )
    SensitiveDataFilter().filter(record)
    assert "IGQVJsecret" not in record.getMessage()
