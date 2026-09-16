from django.urls import path

from . import views

app_name = "marketing"

urlpatterns = [
    path("features/", views.FeatureIndexView.as_view(), name="features"),
    path("features/<slug:slug>/", views.FeatureDetailView.as_view(), name="feature"),
    path("use-cases/", views.UseCaseIndexView.as_view(), name="use-cases"),
    path("use-cases/<slug:slug>/", views.UseCaseDetailView.as_view(), name="use-case"),
    path("pricing/", views.PricingView.as_view(), name="pricing"),
    path("faq/", views.FaqView.as_view(), name="faq"),
    path("about/", views.AboutView.as_view(), name="about"),
    path(
        "instagram-automation-alternative/",
        views.AlternativeView.as_view(),
        name="alternative",
    ),
    path("help/", views.HelpView.as_view(), name="help"),
    path("guides/", views.GuideIndexView.as_view(), name="guides"),
    path("guides/<slug:slug>/", views.GuideDetailView.as_view(), name="guide"),
]
