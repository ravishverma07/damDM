from django.contrib import admin

from .models import AuditLog, DataDeletionRequest


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "action", "actor", "target_type", "target_id")
    list_filter = ("action", "created_at")
    search_fields = ("target_id", "actor__email")
    date_hierarchy = "created_at"
    readonly_fields = (
        "actor", "action", "target_type", "target_id", "context", "ip_hash", "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(DataDeletionRequest)
class DataDeletionRequestAdmin(admin.ModelAdmin):
    list_display = ("created_at", "platform", "external_user_id", "status", "completed_at")
    list_filter = ("platform", "status")
    search_fields = ("external_user_id", "confirmation_code")
    readonly_fields = (
        "platform", "external_user_id", "confirmation_code", "deleted_counts",
        "error_message", "completed_at", "created_at", "updated_at",
    )

    def has_add_permission(self, request):
        return False
