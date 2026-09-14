from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import redirect
from django.views.generic import DetailView, TemplateView

from .models import DataDeletionRequest


class LandingView(TemplateView):
    template_name = "core/landing.html"

    def get(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("core:dashboard")
        return super().get(request, *args, **kwargs)


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "core/dashboard.html"

    def get_context_data(self, **kwargs):
        from apps.automations.models import Automation

        context = super().get_context_data(**kwargs)
        automations = self.request.user.automations.exclude(status=Automation.Status.ARCHIVED)
        context["automation_count"] = automations.count()
        context["active_automations"] = automations.filter(status=Automation.Status.ACTIVE).count()
        return context


class PrivacyView(TemplateView):
    template_name = "legal/privacy.html"


class TermsView(TemplateView):
    template_name = "legal/terms.html"


class DataDeletionView(TemplateView):
    template_name = "legal/data_deletion.html"


class DataDeletionStatusView(DetailView):
    """Public status page for a platform data-deletion request, found by its code."""

    model = DataDeletionRequest
    template_name = "legal/data_deletion_status.html"
    slug_field = "confirmation_code"
    slug_url_kwarg = "code"
    context_object_name = "deletion"


def health(request):
    return JsonResponse({"status": "ok"})
