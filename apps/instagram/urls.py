from django.urls import path

from . import views

app_name = "instagram"

urlpatterns = [
    path("start/", views.InstagramStartView.as_view(), name="start"),
    path("callback/", views.InstagramCallbackView.as_view(), name="callback"),
    path("accounts/", views.AccountListView.as_view(), name="accounts"),
    path("accounts/<int:pk>/", views.AccountDetailView.as_view(), name="account-detail"),
    path("accounts/<int:pk>/sync/", views.AccountSyncView.as_view(), name="account-sync"),
    path(
        "accounts/<int:pk>/disconnect/",
        views.AccountDisconnectView.as_view(),
        name="account-disconnect",
    ),
    # Registered in the Meta app dashboard.
    path("webhooks/", views.WebhookView.as_view(), name="webhooks"),
    # Meta POSTs a signed_request to each of these.
    path("deauthorize/", views.DeauthorizeCallbackView.as_view(), name="deauthorize"),
    path("data-deletion/", views.DataDeletionCallbackView.as_view(), name="data-deletion"),
]
