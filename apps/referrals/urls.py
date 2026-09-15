from django.urls import path

from . import views

app_name = "referrals"

urlpatterns = [
    path("", views.ReferralsView.as_view(), name="overview"),
]
