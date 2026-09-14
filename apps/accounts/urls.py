from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.LoginView.as_view(), name="login"),
    path("logout/", views.LogoutView.as_view(), name="logout"),
    path("register/", views.RegisterView.as_view(), name="register"),
    path("google/start/", views.GoogleStartView.as_view(), name="google-start"),
    path("google/callback/", views.GoogleCallbackView.as_view(), name="google-callback"),
    path("instagram/finish/", views.InstagramFinishView.as_view(), name="instagram-finish"),
    path(
        "password/reset/",
        auth_views.PasswordResetView.as_view(
            template_name="accounts/password_reset.html",
            email_template_name="accounts/email/password_reset.txt",
            success_url="/accounts/password/reset/sent/",
        ),
        name="password-reset",
    ),
    path(
        "password/reset/sent/",
        auth_views.PasswordResetDoneView.as_view(
            template_name="accounts/password_reset_sent.html"
        ),
        name="password-reset-sent",
    ),
    path(
        "password/reset/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(
            template_name="accounts/password_reset_confirm.html",
            success_url="/accounts/password/reset/done/",
        ),
        name="password-reset-confirm",
    ),
    path(
        "password/reset/done/",
        auth_views.PasswordResetCompleteView.as_view(
            template_name="accounts/password_reset_done.html"
        ),
        name="password-reset-done",
    ),
    path("settings/", views.ProfileView.as_view(), name="profile"),
    path("delete/", views.DeleteAccountView.as_view(), name="delete-account"),
]
