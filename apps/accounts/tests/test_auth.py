import pytest
from django.contrib.auth import authenticate
from django.core import mail
from django.urls import reverse

pytestmark = pytest.mark.django_db

HTMX = {"HTTP_HX_REQUEST": "true"}
PASSWORD = "a-really-long-password"


def test_login_is_case_insensitive_on_email(user):
    assert authenticate(username="CREATOR@example.com", password="sup3r-secret-pass") == user


def test_wrong_password_does_not_authenticate(user):
    assert authenticate(username=user.email, password="wrong-password") is None


def test_unknown_email_does_not_authenticate(db):
    assert authenticate(username="nobody@example.com", password="whatever-pass") is None


def test_inactive_user_cannot_authenticate(user):
    user.is_active = False
    user.save(update_fields=["is_active"])
    assert authenticate(username=user.email, password="sup3r-secret-pass") is None


def test_dashboard_requires_login(client):
    response = client.get(reverse("core:dashboard"))
    assert response.status_code == 302
    assert reverse("accounts:login") in response["Location"]


# --- the popup -------------------------------------------------------

def test_landing_offers_every_way_in_from_one_popup(client):
    body = client.get(reverse("core:landing")).content.decode()
    assert 'id="auth-dialog"' in body
    for text in ("Continue with Google", "Continue with Instagram", "Sign in", "Sign up"):
        assert text in body


def test_popup_opens_on_load_in_the_requested_mode(client):
    body = client.get(reverse("core:landing") + "?auth=signup").content.decode()
    assert 'data-auto-open="true"' in body
    assert 'data-mode="signup"' in body


def test_popup_stays_closed_by_default(client):
    body = client.get(reverse("core:landing")).content.decode()
    assert 'data-auto-open="false"' in body
    assert 'data-mode="signin"' in body


def test_signed_in_users_get_no_popup(client, user):
    client.force_login(user)
    assert 'id="auth-dialog"' not in client.get(reverse("core:landing")).content.decode()


def test_login_page_shows_the_panel_inline_without_a_second_popup(client):
    body = client.get(reverse("accounts:login")).content.decode()
    assert 'id="auth-panel"' in body
    assert 'id="auth-dialog"' not in body


# --- email sign up ---------------------------------------------------

def test_sign_up_needs_only_an_email_and_the_password_twice(client):
    response = client.post(
        reverse("accounts:register"),
        {"email": "new@example.com", "password1": PASSWORD, "password2": PASSWORD},
    )
    assert response.status_code == 302
    assert client.get(reverse("core:dashboard")).status_code == 200
    assert mail.outbox == []  # no confirmation email


def test_sign_up_rejects_mismatched_passwords(client):
    response = client.post(
        reverse("accounts:register"),
        {"email": "new@example.com", "password1": PASSWORD, "password2": "a-different-password"},
    )
    assert response.status_code == 200
    body = response.content.decode()
    assert "don&#x27;t match" in body or "don't match" in body


def test_sign_up_error_stays_in_the_popup(client, user):
    response = client.post(
        reverse("accounts:register"),
        {"email": user.email, "password1": PASSWORD, "password2": PASSWORD},
        **HTMX,
    )
    body = response.content.decode()
    assert response.status_code == 200
    assert "HX-Redirect" not in response
    assert "already exists" in body
    assert 'data-mode="signup"' in body
    assert "<html" not in body


# --- email sign in ---------------------------------------------------

def test_htmx_sign_in_redirects_the_whole_page(client, user):
    response = client.post(
        reverse("accounts:login"),
        {"username": user.email, "password": "sup3r-secret-pass", "next": "/accounts/settings/"},
        **HTMX,
    )
    assert response.status_code == 200
    assert response["HX-Redirect"] == "/accounts/settings/"


def test_htmx_sign_in_failure_returns_the_panel_with_an_error(client, user):
    response = client.post(
        reverse("accounts:login"), {"username": user.email, "password": "wrong"}, **HTMX
    )
    body = response.content.decode()
    assert response.status_code == 200
    assert "HX-Redirect" not in response
    assert 'id="auth-panel"' in body
    assert "correct email" in body
    assert "<html" not in body


def test_sign_in_ignores_an_offsite_next(client, user):
    response = client.post(
        reverse("accounts:login"),
        {"username": user.email, "password": "sup3r-secret-pass", "next": "https://evil.example/"},
        **HTMX,
    )
    assert response["HX-Redirect"] == reverse("core:dashboard")


def test_dashboard_prompts_to_connect_instagram(client, user):
    client.force_login(user)
    assert "Connect Instagram" in client.get(reverse("core:dashboard")).content.decode()


def test_dashboard_lists_connected_accounts(client, user, account):
    client.force_login(user)
    body = client.get(reverse("core:dashboard")).content.decode()
    assert f"@{account.username}" in body
    assert "Connect Instagram" not in body
