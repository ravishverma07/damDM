from django.contrib import admin
from django.utils.html import format_html

from .models import ConnectedAccount, Post, WebhookEvent


class PostInline(admin.TabularInline):
    model = Post
    extra = 0
    fields = ("external_media_id", "product_type", "media_type", "published_at", "is_active")
    readonly_fields = fields
    can_delete = False
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(ConnectedAccount)
class ConnectedAccountAdmin(admin.ModelAdmin):
    """
    Access tokens are never rendered here - only whether one exists.
    `encrypted_access_token` is excluded from every form and list.
    """

    list_display = (
        "username", "platform", "external_account_id", "owner", "account_type",
        "status", "token_state", "last_synced_at",
    )
    list_filter = ("platform", "status", "account_type", "connected_at")
    search_fields = ("username", "external_account_id", "owner__email")
    readonly_fields = (
        "platform", "external_account_id", "username", "display_name", "account_type",
        "profile_picture_url", "followers_count", "token_state", "token_expires_at",
        "scopes", "webhooks_subscribed", "connected_at", "last_synced_at", "last_error",
        "created_at", "updated_at",
    )
    exclude = ("encrypted_access_token",)
    inlines = [PostInline]

    @admin.display(description="Token")
    def token_state(self, obj):
        if not obj.encrypted_access_token:
            return format_html('<span style="color:#b00">{}</span>', "no token stored")
        if obj.is_token_expired:
            return format_html('<span style="color:#b60">{}</span>', "encrypted (expired)")
        return format_html('<span style="color:#070">{}</span>', "encrypted (present)")

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("owner")


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = (
        "external_media_id", "account", "product_type",
        "media_type", "comments_count", "published_at", "is_active",
    )
    list_filter = ("product_type", "media_type", "is_active")
    search_fields = ("external_media_id", "caption", "account__username")
    readonly_fields = ("created_at", "updated_at")
    date_hierarchy = "published_at"


@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):
    list_display = (
        "external_event_id", "platform", "event_type", "external_account_id",
        "processing_status", "received_at", "processed_at",
    )
    list_filter = ("platform", "event_type", "processing_status", "received_at")
    search_fields = ("external_event_id", "external_account_id")
    readonly_fields = (
        "platform", "external_event_id", "event_type", "external_account_id", "account",
        "payload", "received_at", "processed_at",
    )
    date_hierarchy = "received_at"

    def has_add_permission(self, request):
        return False
