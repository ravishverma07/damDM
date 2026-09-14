from django.contrib import admin

from .models import DailyStat, LinkClick, TrackedLink


class LinkClickInline(admin.TabularInline):
    model = LinkClick
    extra = 0
    fields = ("clicked_at", "recipient_scoped_id", "user_agent")
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(TrackedLink)
class TrackedLinkAdmin(admin.ModelAdmin):
    list_display = ("slug", "automation", "destination_url", "click_count", "created_at")
    search_fields = ("slug", "destination_url", "automation__name")
    readonly_fields = ("click_count", "created_at", "updated_at")
    inlines = [LinkClickInline]


@admin.register(LinkClick)
class LinkClickAdmin(admin.ModelAdmin):
    list_display = ("clicked_at", "tracked_link", "recipient_scoped_id")
    list_filter = ("clicked_at",)
    search_fields = ("tracked_link__slug", "recipient_scoped_id")
    readonly_fields = (
        "tracked_link", "recipient_scoped_id", "contact", "automation_run",
        "ip_hash", "user_agent", "clicked_at",
    )
    date_hierarchy = "clicked_at"

    def has_add_permission(self, request):
        return False


@admin.register(DailyStat)
class DailyStatAdmin(admin.ModelAdmin):
    list_display = (
        "date", "account", "automation", "comments_received", "keyword_matches",
        "runs_started", "dms_sent", "link_clicks",
    )
    list_filter = ("date",)
    search_fields = ("account__username", "automation__name")
    date_hierarchy = "date"
