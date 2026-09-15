from decimal import Decimal

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.urls import reverse
from django.views import View
from django.views.generic import TemplateView

from apps.billing.models import Plan
from apps.billing.services.subscriptions import PLAN_SLUG

from .models import Payout, Referral, ReferralReward
from .services import (
    HOLD_PERIOD,
    MIN_PAYOUT,
    REWARD_PERCENT,
    ReferralError,
    find_code,
    get_or_create_code,
    mask_email,
    referral_url,
    remember_device,
    request_payout,
    set_referral_cookie,
    wallet,
)


class ReferralLinkView(View):
    """Public invite link: remember the code, then send the visitor to sign up."""

    def get(self, request, code):
        if request.user.is_authenticated:
            return redirect("core:dashboard")
        response = redirect(f"{reverse('core:landing')}?auth=signup")
        referral_code = find_code(code)
        if referral_code is not None:
            set_referral_cookie(response, referral_code)
        return response


class ReferralsView(LoginRequiredMixin, TemplateView):
    template_name = "referrals/referrals.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        code = get_or_create_code(user)
        remember_device(self.request, code)

        referrals = list(Referral.objects.filter(referrer=user).select_related("referred")[:50])
        rewards = {
            reward.referral_id: reward
            for reward in ReferralReward.objects.filter(referral__in=referrals)
        }
        for referral in referrals:
            referral.masked_email = mask_email(referral.referred.email)
            referral.reward_or_none = rewards.get(referral.pk)

        totals = wallet(user)
        open_payout = user.payouts.filter(status=Payout.Status.REQUESTED).first()
        plan = Plan.objects.filter(slug=PLAN_SLUG, is_active=True).first()
        price = plan.amount_in_minor_units() if plan else 9900
        share_url = referral_url(code)
        share_text = (
            f"I automate my Instagram comments and DMs with {settings.APP_NAME}. "
            "Try it free for 14 days:"
        )
        context.update(
            code=code,
            share_url=share_url,
            share_text=share_text,
            share_message=f"{share_text} {share_url}",
            referrals=referrals,
            referrals_count=Referral.objects.filter(referrer=user).count(),
            rewarded_count=Referral.objects.filter(
                referrer=user, status=Referral.Status.REWARDED
            ).count(),
            amounts={key: Decimal(value) / 100 for key, value in totals.items()},
            reward_amount=Decimal(price * REWARD_PERCENT // 100) / 100,
            min_payout=MIN_PAYOUT // 100,
            hold_days=HOLD_PERIOD.days,
            open_payout=open_payout,
            can_withdraw=open_payout is None and totals["available"] >= MIN_PAYOUT,
            payouts=user.payouts.all()[:10],
        )
        return context

    def post(self, request):
        try:
            payout = request_payout(request.user, request.POST.get("upi_id", ""))
        except ReferralError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(
                request,
                f"Withdrawal of ₹{payout.amount_display:.2f} requested. "
                "It will show as paid here once it's sent to your UPI ID.",
            )
        return redirect("referrals:overview")
