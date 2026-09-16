from django.contrib import admin
from django.contrib.sitemaps.views import sitemap
from django.templatetags.static import static
from django.urls import include, path
from django.views.generic import RedirectView

from apps.marketing.sitemaps import SITEMAPS
from apps.marketing.views import robots_txt
from apps.referrals.views import ReferralLinkView

urlpatterns = [
    path("admin/", admin.site.urls),
    # Browsers ask for /favicon.ico on their own; point them at the PNG.
    path("favicon.ico", RedirectView.as_view(url=static("img/favicon-32.png"), permanent=True)),
    path("robots.txt", robots_txt, name="robots"),
    path("sitemap.xml", sitemap, {"sitemaps": SITEMAPS}, name="sitemap"),
    path("", include("apps.core.urls", namespace="core")),
    path("", include("apps.marketing.urls", namespace="marketing")),
    path("accounts/", include("apps.accounts.urls", namespace="accounts")),
    path("instagram/", include("apps.instagram.urls", namespace="instagram")),
    path("automations/", include("apps.automations.urls", namespace="automations")),
    path("billing/", include("apps.billing.urls", namespace="billing")),
    path("refer/", include("apps.referrals.urls", namespace="referrals")),
    path("r/<str:code>/", ReferralLinkView.as_view(), name="referral-link"),
    path("l/", include("apps.analytics.urls", namespace="analytics")),
]

admin.site.site_header = "DamDM administration"
admin.site.site_title = "DamDM"
admin.site.index_title = "DamDM by DammAI"
