from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth import views as auth_views
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import IntegrityError
from django.shortcuts import redirect, render
from django.urls import reverse, reverse_lazy
from django.views.generic import FormView, TemplateView, View
from django_htmx.http import HttpResponseClientRedirect

from apps.core.models import AuditLog
from apps.core.services import audit
from apps.instagram.services.connect import peek_pending

from .forms import (
    DeleteAccountForm,
    EmailLoginForm,
    InstagramEmailForm,
    ProfileForm,
    RegistrationForm,
)
from .models import OAuthState, SocialLogin, User
from .services import google
from .services.auth import (
    SocialAuthError,
    begin_oauth,
    build_auth_panel,
    complete_login,
    finish_oauth,
    link_social_login,
    redirect_to_callback_host,
    safe_next,
    user_for_google,
)
from .services.deletion import close_account

PANEL_TEMPLATE = "accounts/partials/auth_panel.html"
PAGE_TEMPLATES = {
    "signin": "accounts/login.html",
    "signup": "accounts/register.html",
    "finish": "accounts/login.html",
}


def _redirect(request, url: str):
    """A normal redirect, or a full-page redirect when the form was posted by HTMX."""
    if request.htmx:
        return HttpResponseClientRedirect(url)
    return redirect(url)


def _render_panel(request, mode: str, **forms):
    """Re-render the sign-in panel (with errors) inside the popup or on its own page."""
    panel = build_auth_panel(
        request, mode=mode, next_url=request.POST.get("next") or request.GET.get("next"), **forms
    )
    if request.htmx:
        return render(request, PANEL_TEMPLATE, {"panel": panel})
    context = {"panel": panel, "hide_auth_dialog": True}
    return render(request, PAGE_TEMPLATES[panel["mode"]], context)


def _signin_url() -> str:
    return f"{reverse('core:landing')}?auth=signin"


class LoginView(auth_views.LoginView):
    template_name = "accounts/login.html"
    authentication_form = EmailLoginForm
    redirect_authenticated_user = True

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "auto_id": "signin_%s"}

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["panel"] = build_auth_panel(
            self.request, mode="signin", login_form=context["form"],
            next_url=self.get_redirect_url(),
        )
        context["hide_auth_dialog"] = True
        return context

    def form_valid(self, form):
        complete_login(self.request, form.get_user(), method="email")
        return _redirect(self.request, self.get_success_url())

    def form_invalid(self, form):
        return _render_panel(self.request, "signin", login_form=form)


class LogoutView(auth_views.LogoutView):
    next_page = reverse_lazy("core:landing")


class RegisterView(FormView):
    """Email sign-up: an email and the password twice. No email confirmation."""

    template_name = "accounts/register.html"
    form_class = RegistrationForm

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("core:dashboard")
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "auto_id": "signup_%s"}

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["panel"] = build_auth_panel(
            self.request, mode="signup", signup_form=context["form"],
            next_url=self.request.GET.get("next"),
        )
        context["hide_auth_dialog"] = True
        return context

    def form_valid(self, form):
        user = form.save()
        audit(AuditLog.Action.USER_REGISTERED, actor=user, request=self.request, method="email")
        complete_login(self.request, user, method="email")
        messages.success(self.request, f"Welcome to {settings.APP_NAME}!")
        target = safe_next(self.request, self.request.POST.get("next"))
        return _redirect(self.request, target or reverse("core:dashboard"))

    def form_invalid(self, form):
        return _render_panel(self.request, "signup", signup_form=form)


class InstagramFinishView(View):
    """Create an account for a first-time Instagram sign-in, from just an email."""

    http_method_names = ["post"]

    def post(self, request):
        identity = peek_pending(request)
        if identity is None:
            messages.error(request, "That Instagram sign-in expired. Please try again.")
            return _redirect(request, _signin_url())

        form = InstagramEmailForm(request.POST, auto_id="finish_%s")
        if not form.is_valid():
            return _render_panel(request, "finish", finish_form=form)
        try:
            user = User.objects.create_user(
                email=form.cleaned_data["email"], full_name=identity.name[:150]
            )
        except IntegrityError:
            form.add_error("email", InstagramEmailForm.EMAIL_TAKEN)
            return _render_panel(request, "finish", finish_form=form)

        link_social_login(
            user, provider=SocialLogin.Provider.INSTAGRAM, uid=identity.user_id,
            extra={"username": identity.username},
        )
        audit(AuditLog.Action.USER_REGISTERED, actor=user, request=request, method="instagram")
        complete_login(request, user, method="instagram")
        target = safe_next(request, request.POST.get("next"))
        return _redirect(request, target or reverse("core:dashboard"))


class GoogleStartView(View):
    def get(self, request):
        if not google.is_configured():
            messages.error(request, "Google sign-in isn't set up yet (GOOGLE_CLIENT_ID is empty).")
            return redirect(_signin_url())
        if not settings.GOOGLE_MOCK:
            moved = redirect_to_callback_host(request, settings.GOOGLE_REDIRECT_URI)
            if moved is not None:
                return moved
        state = begin_oauth(
            request,
            provider=SocialLogin.Provider.GOOGLE,
            purpose=OAuthState.Purpose.LOGIN,
            next_url=request.GET.get("next"),
        )
        return redirect(google.authorize_url(state.state))


class GoogleCallbackView(View):
    def get(self, request):
        if request.GET.get("error"):
            messages.info(request, "Google sign-in was cancelled.")
            return redirect(_signin_url())
        try:
            state = finish_oauth(request, SocialLogin.Provider.GOOGLE)
            identity = google.exchange_code(request.GET.get("code", ""))
            user, password_disabled = user_for_google(identity)
        except (SocialAuthError, google.GoogleOAuthError) as exc:
            messages.error(request, str(exc))
            return redirect(_signin_url())

        complete_login(request, user, method="google")
        if password_disabled:
            messages.warning(
                request,
                "Signed in with Google. Your email hadn't been confirmed, so its password was "
                "turned off. Keep using Google, or reset your password to set a new one.",
            )
        return redirect(safe_next(request, state.redirect_to) or reverse("core:dashboard"))


class ProfileView(LoginRequiredMixin, FormView):
    template_name = "accounts/profile.html"
    form_class = ProfileForm
    success_url = reverse_lazy("accounts:profile")

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "instance": self.request.user}

    def form_valid(self, form):
        form.save()
        messages.success(self.request, "Profile updated.")
        return super().form_valid(form)


class DeleteAccountView(LoginRequiredMixin, FormView):
    template_name = "accounts/delete_account.html"
    form_class = DeleteAccountForm
    success_url = reverse_lazy("core:landing")

    def get_form_kwargs(self):
        return {**super().get_form_kwargs(), "user": self.request.user}

    def form_valid(self, form):
        user = self.request.user
        close_account(user)
        audit(
            AuditLog.Action.ACCOUNT_DELETED,
            actor=user,
            target_type="User",
            target_id=str(user.pk),
            request=self.request,
        )
        logout(self.request)
        messages.success(self.request, "Your account has been deleted.")
        return super().form_valid(form)


class OnboardingView(LoginRequiredMixin, TemplateView):
    template_name = "accounts/onboarding.html"
