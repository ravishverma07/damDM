from django.contrib import admin

from .models import Automation, AutomationKeyword, AutomationPost, AutomationRun, AutomationStep


class AutomationKeywordInline(admin.TabularInline):
    model = AutomationKeyword
    extra = 0


class AutomationPostInline(admin.TabularInline):
    model = AutomationPost
    extra = 0
    raw_id_fields = ("post",)


class AutomationStepInline(admin.StackedInline):
    model = AutomationStep
    extra = 0
    fields = ("order", "step_type", "config")


@admin.register(Automation)
class AutomationAdmin(admin.ModelAdmin):
    list_display = (
        "name", "owner", "account", "trigger_type", "post_scope",
        "status", "priority", "runs_count", "created_at",
    )
    list_filter = ("status", "trigger_type", "post_scope", "audience", "once_per_contact")
    search_fields = ("name", "owner__email", "account__username", "keywords__keyword")
    readonly_fields = ("runs_count", "completed_runs_count", "created_at", "updated_at")
    autocomplete_fields = ("account",)
    inlines = [AutomationKeywordInline, AutomationPostInline, AutomationStepInline]
    fieldsets = (
        (None, {"fields": ("owner", "account", "name", "status", "priority")}),
        ("Trigger", {"fields": ("trigger_type", "post_scope")}),
        ("Rules", {"fields": (
            "audience", "once_per_contact", "cooldown_minutes", "starts_at", "ends_at",
        )}),
        ("Stats", {"fields": ("runs_count", "completed_runs_count", "created_at", "updated_at")}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("owner", "account")


@admin.register(AutomationRun)
class AutomationRunAdmin(admin.ModelAdmin):
    list_display = (
        "started_at", "automation", "contact", "status", "current_step", "follow_check_count",
    )
    list_filter = ("status", "started_at")
    search_fields = ("automation__name", "contact__username", "contact__external_user_id")
    readonly_fields = (
        "automation", "contact", "trigger_comment", "trigger_message", "current_step",
        "follow_check_count", "context", "last_error", "started_at", "completed_at",
        "expires_at", "created_at", "updated_at",
    )
    date_hierarchy = "started_at"

    def has_add_permission(self, request):
        return False
