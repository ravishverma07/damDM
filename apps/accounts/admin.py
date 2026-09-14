from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.utils.translation import gettext_lazy as _

from .models import OAuthState, SocialLogin, User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    ordering = ("-created_at",)
    list_display = ("email", "full_name", "email_verified", "is_active", "is_staff", "created_at")
    list_filter = ("is_active", "is_staff", "is_superuser", "email_verified", "onboarding_state")
    search_fields = ("email", "full_name")
    readonly_fields = ("created_at", "updated_at", "last_login")

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (_("Profile"), {
            "fields": ("full_name", "avatar_url", "timezone", "email_verified", "onboarding_state"),
        }),
        (_("Permissions"), {
            "fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions"),
        }),
        (_("Dates"), {"fields": ("last_login", "created_at", "updated_at")}),
    )
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("email", "full_name", "password1", "password2"),
        }),
    )


@admin.register(SocialLogin)
class SocialLoginAdmin(admin.ModelAdmin):
    list_display = ("user", "provider", "email", "last_login_at", "created_at")
    list_filter = ("provider",)
    search_fields = ("user__email", "email", "provider_uid")
    readonly_fields = ("provider", "provider_uid", "extra_data", "created_at", "updated_at")


@admin.register(OAuthState)
class OAuthStateAdmin(admin.ModelAdmin):
    list_display = ("provider", "purpose", "user", "expires_at", "consumed_at", "created_at")
    list_filter = ("provider", "purpose", "consumed_at")
    search_fields = ("user__email",)
    readonly_fields = ("state", "created_at")

    def has_add_permission(self, request):
        return False
