import pytest
from django.contrib import admin

from apps.instagram.models import ConnectedAccount

pytestmark = pytest.mark.django_db


def test_admin_never_exposes_the_access_token(account, rf, django_user_model):
    site_admin = admin.site._registry[ConnectedAccount]
    staff = django_user_model.objects.create_superuser(
        email="staff@example.com", password="pw-long-enough"
    )
    request = rf.get("/admin/")
    request.user = staff

    fields = site_admin.get_fields(request, account)
    assert "encrypted_access_token" not in fields

    form = site_admin.get_form(request, account)
    assert "encrypted_access_token" not in form.base_fields

    rendered = str(site_admin.token_state(account))
    assert "IGQVJtest-token-value" not in rendered
    assert "encrypted" in rendered
