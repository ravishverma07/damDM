from django.urls import path

from . import views

app_name = "billing"

urlpatterns = [
    path("", views.BillingView.as_view(), name="overview"),
    path("start/", views.StartCheckoutView.as_view(), name="start"),
    path("confirm/", views.ConfirmCheckoutView.as_view(), name="confirm"),
    path("cancel/", views.CancelSubscriptionView.as_view(), name="cancel"),
    # Registered in the Razorpay dashboard.
    path("webhooks/razorpay/", views.RazorpayWebhookView.as_view(), name="razorpay-webhook"),
]
