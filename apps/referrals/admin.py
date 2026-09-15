from django.contrib import admin, messages

from .models import Payout, Referral, ReferralCode, ReferralReward
from .services import ReferralError, mark_payout_paid, reject_payout


@admin.register(ReferralCode)
class ReferralCodeAdmin(admin.ModelAdmin):
    list_display = ("code", "user", "created_at")
    search_fields = ("code", "user__email")
    readonly_fields = ("device_hashes", "created_at", "updated_at")


@admin.register(Referral)
class ReferralAdmin(admin.ModelAdmin):
    list_display = ("created_at", "referrer", "referred", "code", "status", "rejected_reason")
    list_filter = ("status",)
    search_fields = ("referrer__email", "referred__email", "code")
    readonly_fields = ("referrer", "referred", "code", "created_at", "updated_at")


@admin.register(ReferralReward)
class ReferralRewardAdmin(admin.ModelAdmin):
    list_display = ("created_at", "referrer", "amount_display", "status", "available_at", "payout")
    list_filter = ("status",)
    search_fields = ("referrer__email",)
    readonly_fields = (
        "referrer", "referral", "payment", "amount", "payout", "created_at", "updated_at",
    )


@admin.register(Payout)
class PayoutAdmin(admin.ModelAdmin):
    """Pay the UPI ID shown here, put the UTR in Reference, then use "Mark as paid"."""

    list_display = ("created_at", "user", "amount_display", "status", "reference", "processed_at")
    list_filter = ("status",)
    search_fields = ("user__email", "reference")
    readonly_fields = ("user", "amount", "upi_id", "processed_at", "created_at", "updated_at")
    exclude = ("encrypted_upi_id",)
    actions = ("mark_paid", "reject")

    @admin.display(description="UPI ID")
    def upi_id(self, obj):
        return obj.upi_id

    @admin.action(description="Mark as paid (fill in Reference first)")
    def mark_paid(self, request, queryset):
        done = 0
        for payout in queryset.filter(status=Payout.Status.REQUESTED):
            try:
                mark_payout_paid(payout, reference=payout.reference)
                done += 1
            except ReferralError as exc:
                self.message_user(request, f"Payout {payout.pk}: {exc}", messages.ERROR)
        if done:
            self.message_user(request, f"Marked {done} payout(s) as paid.")

    @admin.action(description="Reject and return earnings to the wallet")
    def reject(self, request, queryset):
        for payout in queryset.filter(status=Payout.Status.REQUESTED):
            reject_payout(payout, note=payout.note or "Rejected by admin")
        self.message_user(request, "Rejected. Earnings are back in the users' wallets.")
