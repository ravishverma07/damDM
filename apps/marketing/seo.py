"""Search-engine helpers: absolute URLs and schema.org structured data (JSON-LD)."""

from __future__ import annotations

import json

from django.conf import settings
from django.templatetags.static import static
from django.utils.safestring import SafeString, mark_safe

_JSON_ESCAPES = {ord("<"): "\\u003C", ord(">"): "\\u003E", ord("&"): "\\u0026"}


def absolute(path: str) -> str:
    return f"{settings.APP_BASE_URL}{path}"


def logo_url() -> str:
    return absolute(static("img/icon-512.png"))


def to_json_ld(*items: dict) -> SafeString:
    """Serialise for a <script type="application/ld+json"> tag, safe against </script>."""
    data = {"@context": "https://schema.org", "@graph": list(items)}
    return mark_safe(json.dumps(data, ensure_ascii=False).translate(_JSON_ESCAPES))


def organization() -> dict:
    return {
        "@type": "Organization",
        "@id": absolute("/#organization"),
        "name": settings.APP_VENDOR,
        "legalName": settings.BUSINESS_LEGAL_NAME,
        "url": absolute("/"),
        "logo": logo_url(),
        "email": settings.SUPPORT_EMAIL,
    }


def website() -> dict:
    return {
        "@type": "WebSite",
        "@id": absolute("/#website"),
        "name": settings.APP_NAME,
        "url": absolute("/"),
        "publisher": {"@id": absolute("/#organization")},
        "inLanguage": "en-IN",
    }


def software_application() -> dict:
    return {
        "@type": "SoftwareApplication",
        "@id": absolute("/#app"),
        "name": settings.APP_NAME,
        "applicationCategory": "BusinessApplication",
        "applicationSubCategory": "Instagram DM automation",
        "operatingSystem": "Web, Android, iOS",
        "url": absolute("/"),
        "image": logo_url(),
        "description": (
            "Auto-reply to Instagram comments, DMs and stories, and send links by DM, "
            "using Meta's official API."
        ),
        "publisher": {"@id": absolute("/#organization")},
        "offers": {
            "@type": "Offer",
            "price": str(settings.PLAN_PRICE),
            "priceCurrency": "INR",
            "category": "subscription",
            "url": absolute("/pricing/"),
        },
    }


def breadcrumbs(trail: list[tuple[str, str]]) -> dict:
    return {
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": i, "name": name, "item": absolute(path)}
            for i, (name, path) in enumerate(trail, start=1)
        ],
    }


def faq_page(faqs) -> dict:
    return {
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": faq.question,
                "acceptedAnswer": {"@type": "Answer", "text": faq.answer},
            }
            for faq in faqs
        ],
    }


def article(guide, path: str) -> dict:
    return {
        "@type": "Article",
        "headline": guide.title,
        "description": guide.description,
        "datePublished": guide.published.isoformat(),
        "dateModified": guide.updated.isoformat(),
        "image": logo_url(),
        "mainEntityOfPage": absolute(path),
        "author": {"@id": absolute("/#organization")},
        "publisher": {"@id": absolute("/#organization")},
        "inLanguage": "en-IN",
    }
