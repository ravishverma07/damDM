import json
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.cache import cache
from django.db.models import Count
from django.http import (
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseForbidden,
    JsonResponse,
)
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import DetailView, ListView
from django.views.generic.detail import SingleObjectMixin

from apps.accounts.models import OAuthState, SocialLogin
from apps.accounts.services.auth import (
    SocialAuthError,
    begin_oauth,
    complete_login,
    finish_oauth,
    redirect_to_callback_host,
    safe_next,
    user_for_instagram,
)
from apps.core.mixins import OwnerQuerysetMixin
from apps.core.models import AuditLog, DataDeletionRequest
from apps.core.queue import enqueue
from apps.core.services import audit

from .models import ConnectedAccount, Platform
from .services import oauth, webhooks
from .services.connect import AccountAlreadyClaimed, connect_account, stash_pending
from .services.lifecycle import (
    InvalidSignedRequest,
    accounts_for_platform_user,
    disconnect_account,
    parse_signed_request,
)
from .tasks import process_data_deletion, process_webhook_event, sync_account_task

SYNC_THROTTLE_SECONDS = 60


# --- sign in / connect -------------------------------------------------------

class InstagramStartView(View):
    """
    Start the Instagram flow. Signed-out visitors are signing in; signed-in
    users are connecting an account.
    """

    def get(self, request):
        signed_in = request.user.is_authenticated
        if not oauth.is_configured():
            messages.error(request, "Instagram sign-in isn't set up yet (META_APP_ID is empty).")
            return redirect(
                reverse("instagram:accounts")
                if signed_in
                else f"{reverse('core:landing')}?auth=signin"
            )
        if not settings.META_MOCK:
            moved = redirect_to_callback_host(request, settings.META_REDIRECT_URI)
            if moved is not None:
                return moved
        state = begin_oauth(
            request,
            provider=SocialLogin.Provider.INSTAGRAM,
            purpose=OAuthState.Purpose.CONNECT if signed_in else OAuthState.Purpose.LOGIN,
            user=request.user if signed_in else None,
            next_url=request.GET.get("next"),
        )
        return redirect(oauth.authorize_url(state.state))


class InstagramCallbackView(View):
    def get(self, request):
        failure_url = (
            reverse("instagram:accounts")
            if request.user.is_authenticated
            else f"{reverse('core:landing')}?auth=signin"
        )
        if request.GET.get("error"):
            messages.info(request, "Instagram sign-in was cancelled.")
            return redirect(failure_url)
        try:
            state = finish_oauth(request, SocialLogin.Provider.INSTAGRAM)
            identity = oauth.exchange_code(request.GET.get("code", ""))
        except (SocialAuthError, oauth.InstagramOAuthError) as exc:
            # Keep Instagram's own reason where support can read it later.
            audit(
                AuditLog.Action.WEBHOOK_REJECTED,
                actor=request.user if request.user.is_authenticated else None,
                target_type="instagram_signin",
                request=request,
                reason=str(exc),
                detail=getattr(exc, "detail", "")[:400],
            )
            messages.error(request, str(exc))
            return redirect(failure_url)

        next_url = safe_next(request, state.redirect_to)
        if state.purpose == OAuthState.Purpose.CONNECT:
            return self._connect(request, state, identity, next_url, failure_url)

        try:
            user = user_for_instagram(identity)
        except SocialAuthError as exc:
            messages.error(request, str(exc))
            return redirect(failure_url)

        if user is None:
            # First time we've seen this account: ask for an email in the popup.
            stash_pending(request, identity)
            params = {"auth": "finish", **({"next": next_url} if next_url else {})}
            return redirect(f"{reverse('core:landing')}?{urlencode(params)}")

        complete_login(request, user, method="instagram")
        return redirect(next_url or reverse("core:dashboard"))

    def _connect(self, request, state, identity, next_url, failure_url):
        if not request.user.is_authenticated or state.user_id != request.user.pk:
            messages.error(request, "That connection wasn't started from this account.")
            return redirect(failure_url)
        try:
            account = connect_account(request.user, identity)
        except AccountAlreadyClaimed:
            messages.error(
                request,
                f"@{identity.username} is already connected to another "
                f"{settings.APP_NAME} account.",
            )
        else:
            messages.success(request, f"Connected Instagram account @{account.username}.")
        return redirect(next_url or reverse("instagram:accounts"))


# --- account management ------------------------------------------------------

class AccountListView(LoginRequiredMixin, ListView):
    template_name = "instagram/accounts.html"
    context_object_name = "accounts"

    def get_queryset(self):
        return (
            ConnectedAccount.objects.filter(owner=self.request.user)
            .annotate(post_count=Count("posts"))
            .order_by("-connected_at")
        )


class AccountDetailView(OwnerQuerysetMixin, DetailView):
    model = ConnectedAccount
    template_name = "instagram/account_detail.html"
    context_object_name = "account"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        posts = self.object.posts.filter(is_active=True)
        context["posts"] = posts[:60]
        context["post_count"] = posts.count()
        return context


class AccountSyncView(OwnerQuerysetMixin, SingleObjectMixin, View):
    model = ConnectedAccount
    http_method_names = ["post"]

    def post(self, request, pk):
        account = self.get_object()
        target = reverse("instagram:account-detail", args=[account.pk])
        if not account.is_connected:
            messages.error(request, "Reconnect this account before syncing.")
        elif not cache.add(f"instagram:sync:{account.pk}", True, SYNC_THROTTLE_SECONDS):
            messages.info(request, "A sync started less than a minute ago.")
        elif enqueue(sync_account_task, account.pk):
            messages.success(request, "Syncing posts. New posts appear within a minute.")
        else:
            messages.error(request, "Background jobs aren't running, so the sync couldn't start.")
        return redirect(target)


class AccountDisconnectView(OwnerQuerysetMixin, SingleObjectMixin, View):
    model = ConnectedAccount
    http_method_names = ["post"]

    def post(self, request, pk):
        account = self.get_object()
        if account.is_claimed:
            paused = disconnect_account(account, actor=request.user, reason="user")
            note = f" {paused} automation{'s' if paused != 1 else ''} paused." if paused else ""
            messages.success(request, f"Disconnected @{account.username}.{note}")
        return redirect("instagram:accounts")


# --- Meta callbacks ----------------------------------------------------------

def _verified_payload(request, callback: str) -> dict | None:
    try:
        return parse_signed_request(
            request.POST.get("signed_request", ""), settings.META_APP_SECRET
        )
    except InvalidSignedRequest:
        audit(
            AuditLog.Action.WEBHOOK_REJECTED,
            target_type="meta_callback",
            target_id=callback,
            request=request,
        )
        return None


@method_decorator(csrf_exempt, name="dispatch")
class WebhookView(View):
    """
    Instagram webhooks: comments, DMs, story replies and mentions.

    GET answers Meta's one-time subscription check. POST verifies the
    signature, stores each event and returns 200 straight away; the work
    happens in Celery, because Meta retries anything slow or failed.
    """

    http_method_names = ["get", "post"]

    def get(self, request):
        challenge = webhooks.verify_subscription(request.GET)
        if challenge is None:
            audit(
                AuditLog.Action.WEBHOOK_REJECTED,
                target_type="instagram_webhook",
                target_id="verify",
                request=request,
                **webhooks.token_report(request.GET),
            )
            return HttpResponseForbidden()
        return HttpResponse(challenge, content_type="text/plain")

    def post(self, request):
        signature = request.headers.get("X-Hub-Signature-256", "")
        try:
            webhooks.verify_signature(request.body, signature)
        except webhooks.InvalidSignature:
            audit(
                AuditLog.Action.WEBHOOK_REJECTED,
                target_type="instagram_webhook",
                request=request,
                **webhooks.signature_report(request.body, signature),
            )
            return HttpResponseForbidden()
        try:
            payload = json.loads(request.body or b"{}")
        except ValueError:
            return HttpResponseBadRequest()
        if not isinstance(payload, dict):
            return HttpResponseBadRequest()

        for event in webhooks.store(payload):
            enqueue(process_webhook_event, event.pk)
        return HttpResponse(status=200)


@method_decorator(csrf_exempt, name="dispatch")
class DeauthorizeCallbackView(View):
    """Meta calls this when someone removes the app from their Instagram account."""

    http_method_names = ["post"]

    def post(self, request):
        payload = _verified_payload(request, "deauthorize")
        if payload is None:
            return HttpResponseBadRequest()
        user_id = str(payload.get("user_id") or "")
        for account in accounts_for_platform_user(user_id).filter(
            status__in=ConnectedAccount.CLAIMED_STATUSES
        ):
            disconnect_account(
                account, status=ConnectedAccount.Status.REVOKED, reason="deauthorized"
            )
        return HttpResponse(status=200)


@method_decorator(csrf_exempt, name="dispatch")
class DataDeletionCallbackView(View):
    """
    Meta calls this when someone asks for their data to be deleted. It must
    answer with a status URL and a confirmation code; the work runs in Celery.
    """

    http_method_names = ["post"]

    def post(self, request):
        payload = _verified_payload(request, "data_deletion")
        user_id = str(payload.get("user_id") or "") if payload else ""
        if not user_id:
            return HttpResponseBadRequest()

        deletion = DataDeletionRequest.issue(platform=Platform.INSTAGRAM, external_user_id=user_id)
        audit(AuditLog.Action.DATA_DELETION_REQUESTED, target=deletion)
        enqueue(process_data_deletion, deletion.pk)

        status_path = reverse("core:data-deletion-status", args=[deletion.confirmation_code])
        return JsonResponse(
            {
                "url": f"{settings.APP_BASE_URL}{status_path}",
                "confirmation_code": deletion.confirmation_code,
            }
        )
