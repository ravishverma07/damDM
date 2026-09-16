"""Public marketing pages. Each sets its own title, description and structured data."""

from __future__ import annotations

from django.conf import settings
from django.http import Http404, HttpResponse
from django.urls import reverse
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_GET
from django.views.generic import TemplateView

from . import seo
from .content import (
    ALL_FAQS,
    ALTERNATIVE_FAQS,
    FAQ_GROUPS,
    FEATURES,
    FEATURES_BY_SLUG,
    GUIDES,
    GUIDES_BY_SLUG,
    PRICING_FAQS,
    USE_CASES,
    USE_CASES_BY_SLUG,
)

HOME = ("Home", "/")

#: Paths search engines should skip: signed-in areas, callbacks and redirects.
PRIVATE_PATHS = (
    "/admin/", "/accounts/", "/dashboard/", "/instagram/", "/automations/", "/billing/",
    "/refer/", "/r/", "/l/", "/notifications/",
)


class MarketingPage(TemplateView):
    seo_title = ""
    seo_description = ""
    faqs: tuple = ()

    def trail(self) -> list[tuple[str, str]]:
        """Breadcrumbs after Home, as (name, path)."""
        return []

    def structured_data(self, trail) -> list[dict]:
        items = [seo.breadcrumbs(trail)]
        if self.get_faqs():
            items.append(seo.faq_page(self.get_faqs()))
        return items

    def get_faqs(self):
        return self.faqs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        trail = [HOME, *self.trail()]
        context.update(
            seo_title=self.get_seo_title(),
            seo_description=self.get_seo_description(),
            breadcrumbs=trail,
            faqs=self.get_faqs(),
            structured_data=seo.to_json_ld(*self.structured_data(trail)),
            features=FEATURES,
            use_cases=USE_CASES,
        )
        return context

    def get_seo_title(self) -> str:
        return self.seo_title

    def get_seo_description(self) -> str:
        return self.seo_description


# --- features and use cases ---------------------------------------------------

class FeatureIndexView(MarketingPage):
    template_name = "marketing/index.html"
    seo_title = "Instagram Automation Features"
    seo_description = (
        "Comment-to-DM, follow to unlock, DM keyword replies, story replies and more. Every "
        "Instagram automation feature in DamDM, all in one ₹99 plan."
    )
    extra_context = {
        "eyebrow": "Features",
        "heading": "Everything you need to automate Instagram DMs",
        "lead": "Every feature is included in one plan, with no limits on automations or messages.",
        "kind": "features",
    }

    def trail(self):
        return [("Features", reverse("marketing:features"))]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(cards=FEATURES, card_url="marketing:feature")
        return context


class UseCaseIndexView(MarketingPage):
    template_name = "marketing/index.html"
    seo_title = "Instagram DM Automation Use Cases"
    seo_description = (
        "See how creators, coaches, online stores and local businesses use DamDM to reply to "
        "Instagram comments and DMs automatically."
    )
    extra_context = {
        "eyebrow": "Use cases",
        "heading": "Instagram automation for every kind of business",
        "lead": "Pick your business to see the automations that work best for it.",
        "kind": "use_cases",
    }

    def trail(self):
        return [("Use cases", reverse("marketing:use-cases"))]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(cards=USE_CASES, card_url="marketing:use-case")
        return context


class _DetailView(MarketingPage):
    template_name = "marketing/detail.html"
    pages: dict = {}
    index_name = ""
    index_url = ""
    eyebrow = ""

    def get_page(self):
        page = self.pages.get(self.kwargs["slug"])
        if page is None:
            raise Http404
        return page

    def get_seo_title(self):
        return self.get_page().seo_title

    def get_seo_description(self):
        return self.get_page().description

    def get_faqs(self):
        return self.get_page().faqs

    def trail(self):
        page = self.get_page()
        return [
            (self.index_name, reverse(self.index_url)),
            (page.name, self.request.path),
        ]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        page = self.get_page()
        context.update(
            page=page,
            eyebrow=self.eyebrow,
            related=[FEATURES_BY_SLUG[slug] for slug in page.features],
        )
        return context


class FeatureDetailView(_DetailView):
    pages = FEATURES_BY_SLUG
    index_name = "Features"
    index_url = "marketing:features"
    eyebrow = "Feature"

    def structured_data(self, trail):
        return [*super().structured_data(trail), seo.software_application()]


class UseCaseDetailView(_DetailView):
    pages = USE_CASES_BY_SLUG
    index_name = "Use cases"
    index_url = "marketing:use-cases"
    eyebrow = "Use case"


# --- single pages -------------------------------------------------------------

class PricingView(MarketingPage):
    template_name = "marketing/pricing.html"
    seo_title = "Pricing: ₹99/month, 14-Day Free Trial"
    seo_description = (
        "DamDM costs ₹99/month with unlimited Instagram automations, DMs and replies. Start "
        "with a 14-day free trial, pay by UPI AutoPay, and cancel any time."
    )
    faqs = PRICING_FAQS

    def trail(self):
        return [("Pricing", reverse("marketing:pricing"))]

    def structured_data(self, trail):
        return [*super().structured_data(trail), seo.software_application()]


class FaqView(MarketingPage):
    template_name = "marketing/faq.html"
    seo_title = "Instagram DM Automation FAQ"
    seo_description = (
        "Answers about DamDM: account requirements, safety, Instagram's messaging rules, "
        "features, the free trial, pricing and cancelling."
    )
    faqs = ALL_FAQS
    # The page lists the questions in groups itself.
    extra_context = {"faq_groups": FAQ_GROUPS, "hide_faq_section": True}

    def trail(self):
        return [("FAQ", reverse("marketing:faq"))]


class AboutView(MarketingPage):
    template_name = "marketing/about.html"
    seo_title = "About Us"
    seo_description = (
        "DamDM is built by DammAI to make Instagram DM automation simple and affordable for "
        "Indian creators and businesses."
    )

    def trail(self):
        return [("About", reverse("marketing:about"))]

    def structured_data(self, trail):
        return [*super().structured_data(trail), seo.organization()]


class AlternativeView(MarketingPage):
    template_name = "marketing/alternative.html"
    seo_title = "Affordable Instagram DM Automation Alternative"
    seo_description = (
        "Looking for a simpler, cheaper Instagram DM automation tool? DamDM gives you unlimited "
        "automations for ₹99/month with UPI AutoPay. Compare and switch."
    )
    faqs = ALTERNATIVE_FAQS

    def trail(self):
        return [("Alternative", reverse("marketing:alternative"))]


class HelpView(MarketingPage):
    template_name = "marketing/help.html"
    seo_title = "Getting Started With DamDM"
    seo_description = (
        "Connect your Instagram professional account, create your first automation and fix "
        "common problems. The DamDM getting-started guide."
    )

    def trail(self):
        return [("Help", reverse("marketing:help"))]


# --- guides -------------------------------------------------------------------

class GuideIndexView(MarketingPage):
    template_name = "marketing/guides.html"
    seo_title = "Instagram Automation Guides"
    seo_description = (
        "Practical guides to Instagram DM automation: comment-to-DM, growing followers, "
        "small-business replies and setting up a professional account."
    )
    extra_context = {"guides": GUIDES}

    def trail(self):
        return [("Guides", reverse("marketing:guides"))]


class GuideDetailView(MarketingPage):
    template_name = "marketing/guide.html"

    def get_guide(self):
        guide = GUIDES_BY_SLUG.get(self.kwargs["slug"])
        if guide is None:
            raise Http404
        return guide

    def get_seo_title(self):
        return self.get_guide().seo_title

    def get_seo_description(self):
        return self.get_guide().description

    def get_faqs(self):
        return self.get_guide().faqs

    def trail(self):
        return [
            ("Guides", reverse("marketing:guides")),
            (self.get_guide().title, self.request.path),
        ]

    def structured_data(self, trail):
        guide = self.get_guide()
        return [*super().structured_data(trail), seo.article(guide, self.request.path)]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        guide = self.get_guide()
        context.update(
            guide=guide,
            more_guides=[g for g in GUIDES if g.slug != guide.slug][:3],
        )
        return context


# --- crawler files ------------------------------------------------------------

@require_GET
@cache_control(max_age=60 * 60 * 24, public=True)
def robots_txt(request):
    lines = [
        "User-agent: *",
        *(f"Disallow: {path}" for path in PRIVATE_PATHS),
        "",
        f"Sitemap: {settings.APP_BASE_URL}{reverse('sitemap')}",
    ]
    return HttpResponse("\n".join(lines) + "\n", content_type="text/plain")
