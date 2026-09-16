import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import TemplateView

from apps.core.models import AuditLog
from apps.core.services import audit
from apps.coupons.services import (
    CouponError,
    available_coupons,
    coupon_for_checkout,
    record_redemption,
)

from .models import BillingWebhookEvent, Plan, Subscription
from .providers.base import WebhookVerificationError
from .providers.registry import get_provider
from .services.access import current_subscription
from .services.subscriptions import (
    PLAN_SLUG,
    SubscriptionError,
    cancel_subscription,
    confirm_checkout,
    handle_webhook,
    start_checkout,
)
from .services.trials import check_eligibility

logger = logging.getLogger("damdm.billing")


class BillingView(LoginRequiredMixin, TemplateView):
    template_name = "billing/billing.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        subscription = current_subscription(user)
        context.update(
            plan=Plan.objects.filter(slug=PLAN_SLUG, is_active=True).first(),
            subscription=subscription,
            has_access=bool(subscription and subscription.has_access),
            eligibility=check_eligibility(self.request, user),
            payments=user.payments.all()[:12],
            coupons=available_coupons(user),
        )
        return context


class StartCheckoutView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request):
        coupon = None
        code = request.POST.get("coupon", "").strip()
        try:
            if code:
                coupon = coupon_for_checkout(request.user, code)
            checkout = start_checkout(request, request.user, coupon=coupon)
            if coupon is not None:
                record_redemption(coupon, request.user, checkout.subscription)
        except (SubscriptionError, CouponError) as exc:
            messages.error(request, str(exc))
            return redirect("billing:overview")

        subscription = checkout.subscription
        audit(AuditLog.Action.SUBSCRIPTION_CHANGED, actor=request.user, target=subscription,
              request=request, change="checkout_started", trial=checkout.trial)
        if checkout.completed:
            messages.success(request, _started_message(subscription))
            return redirect("billing:overview")

        return render(request, "billing/checkout.html", {
            "subscription": subscription,
            "trial": checkout.trial,
            "razorpay_key": settings.RAZORPAY_KEY_ID,
            "razorpay_theme_color": settings.RAZORPAY_THEME_COLOR,
            # Razorpay loads the logo itself, so it needs the full public URL.
            "razorpay_logo": request.build_absolute_uri(static("img/icon-512.png")),
            "confirm_url": reverse("billing:confirm"),
        })


class ConfirmCheckoutView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request):
        try:
            subscription = confirm_checkout(
                request.user,
                payment_id=request.POST.get("razorpay_payment_id", ""),
                subscription_id=request.POST.get("razorpay_subscription_id", ""),
                signature=request.POST.get("razorpay_signature", ""),
            )
        except SubscriptionError as exc:
            messages.error(request, str(exc))
            return redirect("billing:overview")

        audit(AuditLog.Action.SUBSCRIPTION_CHANGED, actor=request.user, target=subscription,
              request=request, change="checkout_confirmed", status=subscription.status)
        if subscription.has_access:
            messages.success(request, _started_message(subscription))
        else:
            messages.warning(
                request, "We're still confirming your AutoPay. This page updates in a minute."
            )
        return redirect("billing:overview")


class CancelSubscriptionView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request):
        try:
            subscription = cancel_subscription(request.user)
        except SubscriptionError as exc:
            messages.error(request, str(exc))
            return redirect("billing:overview")

        audit(AuditLog.Action.SUBSCRIPTION_CHANGED, actor=request.user, target=subscription,
              request=request, change="cancelled")
        ends = subscription.trial_ends_at if subscription.status == Subscription.Status.TRIALING \
            else subscription.current_period_end
        when = f" on {timezone.localtime(ends):%d %b %Y}" if ends else ""
        messages.success(request, f"Cancelled. You won't be charged again, and access ends{when}.")
        return redirect("billing:overview")


@method_decorator(csrf_exempt, name="dispatch")
class RazorpayWebhookView(View):
    """Razorpay subscription events. Stored idempotently, applied once."""

    http_method_names = ["post"]

    def post(self, request):
        provider = get_provider(Subscription.Provider.RAZORPAY)
        try:
            data = provider.verify_webhook(
                request.body,
                request.headers.get("X-Razorpay-Signature", ""),
                request.headers.get("X-Razorpay-Event-Id", ""),
            )
        except WebhookVerificationError:
            audit(AuditLog.Action.WEBHOOK_REJECTED, target_type="razorpay_webhook", request=request)
            return HttpResponseBadRequest()

        event, _ = BillingWebhookEvent.objects.get_or_create(
            provider=Subscription.Provider.RAZORPAY,
            provider_event_id=data.event_id[:191],
            defaults={"event_type": data.event_type[:100], "payload": data.payload},
        )
        if event.processing_status in (BillingWebhookEvent.Status.PROCESSED,
                                       BillingWebhookEvent.Status.IGNORED):
            return HttpResponse(status=200)

        try:
            outcome = handle_webhook(data)
        except Exception as exc:  # Razorpay retries non-2xx responses.
            logger.exception("Razorpay webhook %s failed", event.pk)
            event.processing_status = BillingWebhookEvent.Status.FAILED
            event.error_message = str(exc)[:2000]
            event.processed_at = timezone.now()
            event.save(update_fields=["processing_status", "error_message", "processed_at"])
            return HttpResponse(status=500)

        event.processing_status = outcome
        event.processed_at = timezone.now()
        event.save(update_fields=["processing_status", "processed_at"])
        return HttpResponse(status=200)


def _started_message(subscription: Subscription) -> str:
    if subscription.status == Subscription.Status.TRIALING and subscription.trial_ends_at:
        first_charge = timezone.localtime(subscription.trial_ends_at)
        return (
            f"You're all set. Your first ₹99 payment is on {first_charge:%d %b %Y}, "
            "unless you cancel before then."
        )
    return "You're subscribed. Automations can run now."

