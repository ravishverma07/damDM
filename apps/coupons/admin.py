from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.utils import timezone

from .models import Coupon, CouponRedemption
from .services import (
    archive_coupon,
    generate_code,
    notify_recipients_of,
    parse_usernames,
    sync_recipients,
)


class CouponAdminForm(forms.ModelForm):
    usernames = forms.CharField(
        label="Instagram usernames",
        widget=forms.Textarea(attrs={"rows": 5, "placeholder": "damdm_fan\n@another.creator"}),
        help_text="Only these exact Instagram accounts can use the coupon. One per line; "
                  "@ is optional.",
    )

    class Meta:
        model = Coupon
        fields = (
            "name", "code", "discount_percent", "discount_months", "razorpay_offer_id",
            "expires_at", "notify_on_connect",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["usernames"].initial = "\n".join(
                self.instance.recipients.values_list("instagram_username", flat=True)
            )

    def clean_code(self):
        return (self.cleaned_data.get("code") or "").strip().upper()

    def clean_usernames(self):
        valid, invalid = parse_usernames(self.cleaned_data.get("usernames"))
        if invalid:
            raise forms.ValidationError(
                f"These aren't valid Instagram usernames: {', '.join(invalid)}"
            )
        if not valid:
            raise forms.ValidationError("Add at least one Instagram username.")
        return valid

    def clean(self):
        data = super().clean()
        percent = data.get("discount_percent")
        needs_offer = percent and percent < 100 and not settings.RAZORPAY_MOCK
        if needs_offer and not data.get("razorpay_offer_id"):
            self.add_error(
                "razorpay_offer_id",
                "Discounts under 100% need a Razorpay Offer. Create one in the Razorpay Dashboard "
                f"({percent}% off, limited to {data.get('discount_months') or 1} cycle(s)) and "
                "paste its offer_… id here.",
            )
        expires_at = data.get("expires_at")
        if expires_at and expires_at <= timezone.now() and not self.instance.pk:
            self.add_error("expires_at", "Pick a date in the future.")
        return data


class CouponRedemptionInline(admin.TabularInline):
    model = CouponRedemption
    extra = 0
    can_delete = False
    fields = ("user", "recipient", "subscription", "created_at", "cancelled_at")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    form = CouponAdminForm
    list_display = (
        "code", "name", "summary", "usernames_display", "redeemed", "expires_at", "status",
    )
    list_filter = ("status",)
    search_fields = ("code", "name", "recipients__instagram_username")
    readonly_fields = ("status", "archived_at", "created_at", "updated_at")
    actions = ("archive",)
    inlines = [CouponRedemptionInline]
    fieldsets = (
        (None, {"fields": ("name", "code", "usernames")}),
        ("Discount", {
            "fields": ("discount_percent", "discount_months", "razorpay_offer_id"),
            "description": "100% gives free months after the free trial; nothing to set up in "
                           "Razorpay. Under 100% needs a Razorpay Offer with the same percentage "
                           "and number of cycles.",
        }),
        ("Availability", {
            "fields": ("expires_at", "notify_on_connect", "status", "archived_at"),
        }),
    )

    @admin.display(description="Discount")
    def summary(self, obj):
        return obj.summary

    @admin.display(description="Usernames")
    def usernames_display(self, obj):
        names = list(obj.recipients.values_list("instagram_username", flat=True)[:4])
        more = obj.recipients.count() - len(names)
        return ", ".join(f"@{name}" for name in names) + (f" +{more}" if more > 0 else "")

    @admin.display(description="Used")
    def redeemed(self, obj):
        return obj.redemptions.count()

    def save_model(self, request, obj, form, change):
        if not obj.code:
            obj.code = generate_code()
        if obj.created_by_id is None:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        sync_recipients(form.instance, form.cleaned_data["usernames"])
        notified = notify_recipients_of(form.instance)
        if notified:
            self.message_user(request, f"Notified {notified} connected account(s).")

    # "%%" because Django formats action descriptions with the % operator.
    @admin.action(description="Archive (100%% coupons also cancel the plans they started)")
    def archive(self, request, queryset):
        cancelled = sum(archive_coupon(coupon) for coupon in queryset)
        self.message_user(
            request,
            f"Archived {queryset.count()} coupon(s); cancelled {cancelled} free subscription(s).",
            messages.SUCCESS,
        )
