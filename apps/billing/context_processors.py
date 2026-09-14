from .services.access import current_subscription

TRIAL_REMINDER_DAYS = 3


def plan_banner(request):
    """A reminder strip for signed-in users without a plan, or near the end of a trial."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    match = getattr(request, "resolver_match", None)
    if match and match.namespace == "billing":
        return {}

    subscription = current_subscription(user)
    if subscription is None or not subscription.has_access:
        return {"plan_banner": {
            "kind": "start",
            "text": "Start your 14-day free trial to turn on automations.",
            "action": "Start free trial",
        }}
    if subscription.status == subscription.Status.PAST_DUE:
        return {"plan_banner": {
            "kind": "warning",
            "text": "Your last payment didn't go through. Razorpay will retry it.",
            "action": "View billing",
        }}
    days_left = subscription.trial_days_left
    if subscription.status == subscription.Status.TRIALING and days_left <= TRIAL_REMINDER_DAYS:
        text = f"Your free trial ends in {days_left} day{'s' if days_left != 1 else ''}."
        if subscription.cancel_at_period_end:
            text += " You've cancelled, so automations will pause."
        return {"plan_banner": {"kind": "info", "text": text, "action": "Manage plan"}}
    return {}
