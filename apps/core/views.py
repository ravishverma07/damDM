import json

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.generic import DetailView, TemplateView

from . import push
from .models import DataDeletionRequest, Notification, PushSubscription
from .notify import deliver


class LandingView(TemplateView):
    template_name = "core/landing.html"

    def get(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("core:dashboard")
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        from apps.marketing import seo
        from apps.marketing.content import HOME_FAQS

        context = super().get_context_data(**kwargs)
        context.update(
            seo_title="Auto-reply to Instagram comments and DMs",
            seo_description=(
                "Auto-DM everyone who comments on your Instagram posts and Reels, reply to DM "
                "keywords and stories, and grow followers. Official API. 14 days free, then "
                "₹99/month."
            ),
            home_faqs=HOME_FAQS,
            structured_data=seo.to_json_ld(
                seo.organization(),
                seo.website(),
                seo.software_application(),
                seo.faq_page(HOME_FAQS),
            ),
        )
        return context


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "core/dashboard.html"

    def get_context_data(self, **kwargs):
        from apps.automations.models import Automation

        context = super().get_context_data(**kwargs)
        automations = self.request.user.automations.exclude(status=Automation.Status.ARCHIVED)
        context["automation_count"] = automations.count()
        context["active_automations"] = automations.filter(status=Automation.Status.ACTIVE).count()
        context["notifications"] = self.request.user.notifications.filter(read_at__isnull=True)[:5]
        return context


class NotificationDismissView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk):
        notification = get_object_or_404(Notification, pk=pk, user=request.user)
        if notification.read_at is None:
            notification.read_at = timezone.now()
            notification.save(update_fields=["read_at"])
        target = request.POST.get("next", "")
        if not url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
            target = "core:dashboard"
        return redirect(target)


class PrivacyView(TemplateView):
    template_name = "legal/privacy.html"


class TermsView(TemplateView):
    template_name = "legal/terms.html"


class RefundPolicyView(TemplateView):
    template_name = "legal/refunds.html"


class ContactView(TemplateView):
    template_name = "legal/contact.html"


class DataDeletionView(TemplateView):
    template_name = "legal/data_deletion.html"


class DataDeletionStatusView(DetailView):
    """Public status page for a platform data-deletion request, found by its code."""

    model = DataDeletionRequest
    template_name = "legal/data_deletion_status.html"
    slug_field = "confirmation_code"
    slug_url_kwarg = "code"
    context_object_name = "deletion"


class ManifestView(TemplateView):
    """Web app manifest: the name and icon used when the site is added to a home screen."""

    template_name = "manifest.webmanifest"
    content_type = "application/manifest+json"


def health(request):
    return JsonResponse({"status": "ok"})


# --- installable app and push notifications ---------------------------------

class ServiceWorkerView(TemplateView):
    """Served from the site root so it can control every page."""

    template_name = "pwa/sw.js"
    content_type = "application/javascript"

    def render_to_response(self, context, **kwargs):
        response = super().render_to_response(context, **kwargs)
        response["Service-Worker-Allowed"] = "/"
        response["Cache-Control"] = "no-cache"
        return response


class OfflineView(TemplateView):
    template_name = "pwa/offline.html"


class _PushView(LoginRequiredMixin, View):
    http_method_names = ["post"]
    raise_exception = True  # a JSON caller wants 403, not a login redirect

    def payload(self) -> dict:
        try:
            data = json.loads(self.request.body or b"{}")
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}


class PushSubscribeView(_PushView):
    def post(self, request):
        data = self.payload()
        keys = data.get("keys") if isinstance(data.get("keys"), dict) else {}
        endpoint = str(data.get("endpoint") or "")
        p256dh, auth = str(keys.get("p256dh") or ""), str(keys.get("auth") or "")
        if not push.is_configured():
            return JsonResponse({"error": "Push notifications aren't set up."}, status=503)
        valid_keys = 0 < len(p256dh) <= 200 and 0 < len(auth) <= 64
        if not (push.is_allowed_endpoint(endpoint) and valid_keys):
            return JsonResponse({"error": "That subscription isn't valid."}, status=400)
        PushSubscription.objects.update_or_create(
            endpoint=endpoint,
            defaults={
                "user": request.user,
                "p256dh": p256dh,
                "auth": auth,
                "user_agent": request.headers.get("User-Agent", "")[:300],
                "failure_count": 0,
            },
        )
        # Remembered so logging out stops this device getting this user's alerts.
        request.session["push_endpoint"] = endpoint
        return JsonResponse({"ok": True})


class PushUnsubscribeView(_PushView):
    def post(self, request):
        endpoint = str(self.payload().get("endpoint") or "")
        PushSubscription.objects.filter(user=request.user, endpoint=endpoint).delete()
        return JsonResponse({"ok": True})


class PushTestView(_PushView):
    def post(self, request):
        sent = deliver(request.user.pk, {
            "title": "Notifications are on 🎉",
            "body": "You'll hear from DamDM when something needs your attention.",
            "url": "/accounts/settings/",
            "tag": "test",
        })
        return JsonResponse({"sent": sent})
