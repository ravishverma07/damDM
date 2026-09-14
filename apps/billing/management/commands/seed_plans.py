"""Create or update the Pro plan and retire any older plans. Safe to re-run."""

from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.billing.models import Plan

PRO_SLUG = "pro"
PRO = {
    "name": "Pro",
    "monthly_price": Decimal("99.00"),
    "currency": "INR",
    "trial_days": 14,
    "monthly_message_limit": 0,  # unlimited
    "monthly_automation_limit": 0,  # unlimited
    "instagram_account_limit": 1,
    "analytics_enabled": True,
    "follow_gate_enabled": True,
    "display_order": 1,
    "is_active": True,
}


class Command(BaseCommand):
    help = "Create or update the ₹99/month Pro plan with a 14-day trial."

    def handle(self, *args, **options):
        defaults = dict(PRO)
        if settings.RAZORPAY_PLAN_ID:
            defaults["provider_price_id"] = settings.RAZORPAY_PLAN_ID
        plan, created = Plan.objects.update_or_create(slug=PRO_SLUG, defaults=defaults)
        retired = Plan.objects.exclude(slug=PRO_SLUG).filter(is_active=True).update(is_active=False)

        self.stdout.write(self.style.SUCCESS(f"{'Created' if created else 'Updated'} plan: {plan}"))
        if retired:
            self.stdout.write(f"Retired {retired} older plan(s).")
        if not plan.provider_price_id and not settings.RAZORPAY_MOCK:
            self.stdout.write(self.style.WARNING(
                "\nSet RAZORPAY_PLAN_ID (the ₹99 monthly plan from the Razorpay dashboard) "
                "and re-run before enabling checkout."
            ))
