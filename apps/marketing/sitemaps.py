"""sitemap.xml: every public page, with absolute URLs on APP_BASE_URL."""

from __future__ import annotations

from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.sitemaps import Sitemap
from django.urls import reverse

from .content import FEATURES, GUIDES, USE_CASES

#: (url name, kwargs, priority)
STATIC_PAGES = (
    ("core:landing", {}, 1.0),
    ("marketing:pricing", {}, 0.9),
    ("marketing:features", {}, 0.8),
    ("marketing:use-cases", {}, 0.7),
    ("marketing:alternative", {}, 0.7),
    ("marketing:faq", {}, 0.6),
    ("marketing:guides", {}, 0.6),
    ("marketing:help", {}, 0.5),
    ("marketing:about", {}, 0.5),
    ("core:contact", {}, 0.4),
    ("core:refunds", {}, 0.3),
    ("core:privacy", {}, 0.3),
    ("core:terms", {}, 0.3),
)


class PublicSitemap(Sitemap):
    changefreq = "weekly"

    def items(self):
        return [
            *STATIC_PAGES,
            *(("marketing:feature", {"slug": p.slug}, 0.8) for p in FEATURES),
            *(("marketing:use-case", {"slug": p.slug}, 0.7) for p in USE_CASES),
            *(("marketing:guide", {"slug": g.slug}, 0.6) for g in GUIDES),
        ]

    def location(self, item):
        name, kwargs, _ = item
        return reverse(name, kwargs=kwargs)

    def priority(self, item):
        return item[2]

    def get_urls(self, page=1, site=None, protocol=None):
        # Always advertise the public domain, whatever host the crawler used.
        base = urlsplit(settings.APP_BASE_URL)

        class _Site:
            domain = base.netloc
            name = base.netloc

        return super().get_urls(page=page, site=_Site(), protocol=base.scheme or "https")


SITEMAPS = {"pages": PublicSitemap}
