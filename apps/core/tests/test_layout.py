import re

import pytest
from django.conf import settings
from django.urls import reverse

pytestmark = pytest.mark.django_db


def test_landing_shows_the_animated_demo(client):
    body = client.get(reverse("core:landing")).content.decode()
    assert "data-demo" in body
    assert "landing-demo.js" in body
    assert "by DammAI" not in body


def test_public_pages_have_no_app_navigation(client):
    body = client.get(reverse("core:landing")).content.decode()
    assert 'class="tabbar"' not in body
    assert 'class="sidebar"' not in body


def test_signed_in_visitors_skip_the_landing_page(client, user):
    client.force_login(user)
    assert client.get(reverse("core:landing"))["Location"] == reverse("core:dashboard")


@pytest.mark.parametrize(
    ("url_name", "section"),
    [
        ("core:dashboard", "Home"),
        ("automations:list", "Automations"),
        ("instagram:accounts", "Instagram"),
        ("accounts:profile", "Settings"),
    ],
)
def test_navigation_marks_the_current_section(client, user, url_name, section):
    client.force_login(user)
    body = client.get(reverse(url_name)).content.decode()
    active = re.findall(r'is-active" aria-current="page">.*?<span>(\w+)</span>', body, re.S)
    assert active == [section, section]  # sidebar and tab bar


def test_tab_bar_has_a_create_button(client, user):
    client.force_login(user)
    body = client.get(reverse("core:dashboard")).content.decode()
    assert 'class="tabbar"' in body
    assert f'href="{reverse("automations:new")}" class="tabbar__create"' in body


def test_settings_page_offers_log_out(client, user):
    client.force_login(user)
    body = client.get(reverse("accounts:profile")).content.decode()
    assert "Log out" in body
    assert reverse("accounts:delete-account") in body


def test_logo_and_favicons_use_the_brand_image(client):
    body = client.get(reverse("core:landing")).content.decode()
    assert 'rel="icon"' in body and "img/favicon-32.png" in body
    assert 'rel="apple-touch-icon"' in body
    assert 'class="logo-mark" src="/static/img/logo.png"' in body
    assert "demo__badge" in body and "Sent by DamDM" in body


def test_favicon_ico_redirects_to_the_png(client):
    response = client.get("/favicon.ico")
    assert response.status_code == 301
    assert response["Location"].endswith("img/favicon-32.png")


@pytest.mark.parametrize(
    ("url_name", "heading"),
    [("core:refunds", "Refund &amp; Cancellation Policy"), ("core:contact", "Contact Us")],
)
def test_payment_policy_pages_are_public_and_linked(client, url_name, heading):
    body = client.get(reverse(url_name)).content.decode()
    assert heading in body
    assert settings.SUPPORT_EMAIL in body
    assert reverse("core:refunds") in body and reverse("core:contact") in body  # footer


def test_pricing_shows_the_launch_discount(client, settings):
    body = client.get(reverse("marketing:pricing")).content.decode()
    assert "<s class=\"pricing-card__was\"" in body and "₹199" in body and "50% off" in body
    settings.PLAN_LIST_PRICE = 0
    body = client.get(reverse("marketing:pricing")).content.decode()
    assert "% off" not in body and "₹99" in body


def test_home_screen_uses_the_app_name_and_logo(client):
    body = client.get(reverse("core:landing")).content.decode()
    assert '<meta name="apple-mobile-web-app-title" content="DamDM">' in body
    assert f'rel="manifest" href="{reverse("core:manifest")}"' in body
    response = client.get(reverse("core:manifest"))
    assert response["Content-Type"] == "application/manifest+json"
    manifest = response.json()
    assert manifest["name"] == manifest["short_name"] == "DamDM"
    assert {icon["sizes"] for icon in manifest["icons"]} == {"192x192", "512x512"}
