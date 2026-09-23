from django.urls import path

from . import views

app_name = "core"

urlpatterns = [
    path("", views.LandingView.as_view(), name="landing"),
    path("dashboard/", views.DashboardView.as_view(), name="dashboard"),
    path(
        "notifications/<int:pk>/dismiss/",
        views.NotificationDismissView.as_view(),
        name="notification-dismiss",
    ),
    path("privacy/", views.PrivacyView.as_view(), name="privacy"),
    path("terms/", views.TermsView.as_view(), name="terms"),
    path("refunds/", views.RefundPolicyView.as_view(), name="refunds"),
    path("contact/", views.ContactView.as_view(), name="contact"),
    path("data-deletion/", views.DataDeletionView.as_view(), name="data-deletion"),
    path(
        "data-deletion/<str:code>/",
        views.DataDeletionStatusView.as_view(),
        name="data-deletion-status",
    ),
    path("manifest.webmanifest", views.ManifestView.as_view(), name="manifest"),
    path("sw.js", views.ServiceWorkerView.as_view(), name="service-worker"),
    path("offline/", views.OfflineView.as_view(), name="offline"),
    path("notifications/push/subscribe/", views.PushSubscribeView.as_view(), name="push-subscribe"),
    path(
        "notifications/push/unsubscribe/",
        views.PushUnsubscribeView.as_view(),
        name="push-unsubscribe",
    ),
    path("notifications/push/test/", views.PushTestView.as_view(), name="push-test"),
    path("healthz/", views.health, name="health"),
]
