from django.contrib import admin

from .models import (
    BillingWebhookEvent,
    Payment,
    Plan,
    Subscription,
    TrialClaim,
    TrialFingerprint,
    UsageRecord,
)


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = (
        "name", "slug", "monthly_price", "currency", "trial_days", "monthly_message_limit",
        "monthly_automation_limit", "instagram_account_limit", "is_active",
    )
    list_filter = ("is_active", "currency")
    search_fields = ("name", "slug", "provider_price_id")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = (
        "user", "plan", "provider", "status", "trial_ends_at",
        "current_period_end", "payment_method", "cancel_at_period_end",
    )
    list_filter = ("provider", "status", "cancel_at_period_end")
    search_fields = ("user__email", "provider_customer_id", "provider_subscription_id")
    readonly_fields = ("trial_signals", "trial_ip_hash", "created_at", "updated_at")


@admin.register(BillingWebhookEvent)
class BillingWebhookEventAdmin(admin.ModelAdmin):
    list_display = (
        "received_at", "provider", "event_type",
        "provider_event_id", "processing_status",
    )
    list_filter = ("provider", "processing_status", "received_at")
    search_fields = ("provider_event_id", "event_type")
    readonly_fields = (
        "provider", "provider_event_id", "event_type", "payload",
        "received_at", "processed_at",
    )

    def has_add_permission(self, request):
        return False


@admin.register(UsageRecord)
class UsageRecordAdmin(admin.ModelAdmin):
    list_display = (
        "user", "period_start", "period_end", "comments_processed",
        "private_replies_sent", "messages_sent", "unique_contacts", "link_clicks",
    )
    list_filter = ("period_start",)
    search_fields = ("user__email",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "amount_display", "currency", "status", "method")
    list_filter = ("status", "method", "provider")
    search_fields = ("user__email", "provider_payment_id")

    def has_add_permission(self, request):
        return False

    def get_readonly_fields(self, request, obj=None):
        return [field.name for field in Payment._meta.fields]


class TrialFingerprintInline(admin.TabularInline):
    model = TrialFingerprint
    extra = 0
    readonly_fields = ("kind", "value_hash", "created_at")
    can_delete = False


@admin.register(TrialClaim)
class TrialClaimAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "status", "subscription", "ip_hash")
    list_filter = ("status",)
    search_fields = ("user__email", "ip_hash")
    readonly_fields = ("user", "subscription", "ip_hash", "created_at", "updated_at")
    inlines = [TrialFingerprintInline]

