from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.generic import DetailView, TemplateView

from .models import DataDeletionRequest, Notification


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
