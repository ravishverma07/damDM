from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Case, IntegerField, Value, When
from django.http import Http404
from django.shortcuts import redirect
from django.views import View
from django.views.generic import FormView, ListView, TemplateView
from django.views.generic.detail import SingleObjectMixin

from apps.accounts.models import User
from apps.billing.services.access import has_access
from apps.core.mixins import OwnerQuerysetMixin
from apps.core.models import AuditLog
from apps.core.services import audit
from apps.instagram.models import ConnectedAccount

from .forms import AUDIENCE_OPTIONS, SCOPE_OPTIONS, TRIGGER_OPTIONS, AutomationBuilderForm
from .models import Automation
from .services.builder import (
    default_initial,
    describe_automation,
    initial_from_automation,
    save_automation,
)
from .services.recipes import RECIPES, RECIPES_BY_SLUG


def _claimed_accounts(user):
    return ConnectedAccount.objects.filter(
        owner=user, status__in=ConnectedAccount.CLAIMED_STATUSES
    ).order_by("-connected_at")


def _needs_account(request):
    messages.info(request, "Connect an Instagram account first. Automations run on it.")
    return redirect("instagram:accounts")


class AutomationListView(LoginRequiredMixin, ListView):
    template_name = "automations/list.html"
    context_object_name = "automations"

    def get_queryset(self):
        live_first = Case(
            When(status=Automation.Status.ACTIVE, then=Value(0)),
            default=Value(1),
            output_field=IntegerField(),
        )
        return (
            Automation.objects.filter(owner=self.request.user)
            .exclude(status=Automation.Status.ARCHIVED)
            .select_related("account")
            .prefetch_related("keywords", "posts", "steps")
            .order_by(live_first, "-updated_at")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        for automation in context["automations"]:
            automation.summary = describe_automation(automation)
        context["recipes"] = RECIPES
        context["has_accounts"] = _claimed_accounts(self.request.user).exists()
        return context


class AutomationNewView(LoginRequiredMixin, TemplateView):
    """Pick a starting point."""

    template_name = "automations/new.html"

    def get(self, request, *args, **kwargs):
        if not _claimed_accounts(request.user).exists():
            return _needs_account(request)
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        return {**super().get_context_data(**kwargs), "recipes": RECIPES}


class BuilderMixin:
    form_class = AutomationBuilderForm
    template_name = "automations/builder.html"
    automation: Automation | None = None

    def get_form_kwargs(self):
        return {
            **super().get_form_kwargs(),
            "user": self.request.user,
            "automation": self.automation,
        }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        form = context["form"]
        accounts = list(form.fields["account"].queryset)
        chosen = str(form["account"].value() or "")
        fallback = accounts[0] if accounts else None
        account = next((a for a in accounts if str(a.pk) == chosen), fallback)
        context.update(
            automation=self.automation,
            trigger_options=TRIGGER_OPTIONS,
            scope_options=SCOPE_OPTIONS,
            audience_options=AUDIENCE_OPTIONS,
            posts=form.fields["posts"].queryset.order_by("-published_at", "-created_at")[:60],
            selected_posts=[str(getattr(post, "pk", post)) for post in form["posts"].value() or []],
            account_username=account.username if account else "",
        )
        return context

    def form_valid(self, form):
        user = self.request.user
        account = form.cleaned_data["account"]
        is_new = self.automation is None
        go_live = form.cleaned_data["go_live"]
        if go_live and not has_access(user):
            go_live = False
            messages.warning(
                self.request,
                "Start your free trial to turn this automation on. It's saved for now.",
            )
        elif go_live and not account.is_connected:
            go_live = False
            messages.warning(
                self.request,
                f"@{account.username} needs reconnecting, so this automation wasn't turned on.",
            )

        automation = save_automation(
            user, form.cleaned_data, automation=self.automation, go_live=go_live
        )
        audit(
            AuditLog.Action.AUTOMATION_CREATED if is_new else AuditLog.Action.AUTOMATION_UPDATED,
            actor=user,
            target=automation,
            request=self.request,
        )
        if is_new:
            user.advance_onboarding(User.Onboarding.AUTOMATION_CREATED)

        if automation.is_active:
            state = "It's live."
        elif automation.status == Automation.Status.PAUSED:
            state = "It's paused."
        else:
            state = "Saved as a draft."
        verb = "created" if is_new else "saved"
        messages.success(self.request, f"“{automation.name}” {verb}. {state}")
        return redirect("automations:list")


class AutomationCreateView(LoginRequiredMixin, BuilderMixin, FormView):
    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.recipe = RECIPES_BY_SLUG.get(kwargs.get("recipe", ""))
        if self.recipe is None:
            raise Http404("Unknown automation template.")

    def get(self, request, *args, **kwargs):
        if not _claimed_accounts(request.user).exists():
            return _needs_account(request)
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        if not _claimed_accounts(request.user).exists():
            return _needs_account(request)
        return super().post(request, *args, **kwargs)

    def get_initial(self):
        first = _claimed_accounts(self.request.user).first()
        return {
            **default_initial(),
            "name": self.recipe.title,
            **self.recipe.initial,
            "account": first.pk if first else None,
        }


class AutomationUpdateView(OwnerQuerysetMixin, BuilderMixin, SingleObjectMixin, FormView):
    model = Automation

    def get(self, request, *args, **kwargs):
        self.object = self.automation = self.get_object()
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        self.object = self.automation = self.get_object()
        return super().post(request, *args, **kwargs)

    def get_initial(self):
        return initial_from_automation(self.automation)


class AutomationStatusView(OwnerQuerysetMixin, SingleObjectMixin, View):
    model = Automation
    http_method_names = ["post"]

    def post(self, request, pk):
        automation = self.get_object()
        if request.POST.get("action") == "activate":
            if not has_access(request.user):
                messages.error(request, "Start your free trial to turn automations on.")
            elif not automation.account.is_connected:
                messages.error(
                    request,
                    f"Reconnect @{automation.account.username} to turn this automation on.",
                )
            elif not automation.steps.exists():
                messages.error(request, "Add a message before turning this automation on.")
            else:
                automation.activate()
                audit(AuditLog.Action.AUTOMATION_UPDATED, actor=request.user, target=automation,
                      request=request, status="active")
                messages.success(request, f"“{automation.name}” is live.")
        elif automation.is_active:
            automation.pause()
            audit(AuditLog.Action.AUTOMATION_UPDATED, actor=request.user, target=automation,
                  request=request, status="paused")
            messages.success(request, f"“{automation.name}” is paused.")
        return redirect("automations:list")


class AutomationDeleteView(OwnerQuerysetMixin, SingleObjectMixin, View):
    model = Automation
    http_method_names = ["post"]

    def post(self, request, pk):
        automation = self.get_object()
        name = automation.name
        audit(
            AuditLog.Action.AUTOMATION_DELETED,
            actor=request.user,
            target=automation,
            request=request,
        )
        automation.delete()
        messages.success(request, f"“{name}” deleted.")
        return redirect("automations:list")
