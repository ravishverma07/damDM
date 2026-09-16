import json
import re

import pytest
from django.conf import settings
from django.urls import reverse

from apps.marketing.content import ALL_FAQS, FEATURES, GUIDES, USE_CASES
from apps.marketing.sitemaps import PublicSitemap

pytestmark = pytest.mark.django_db

SINGLE_PAGES = [
    "marketing:features", "marketing:use-cases", "marketing:pricing", "marketing:faq",
    "marketing:about", "marketing:alternative", "marketing:help", "marketing:guides",
]
DETAIL_PAGES = [
    *(("marketing:feature", page.slug) for page in FEATURES),
    *(("marketing:use-case", page.slug) for page in USE_CASES),
    *(("marketing:guide", guide.slug) for guide in GUIDES),
]
ALL_URLS = [
    *(reverse(name) for name in SINGLE_PAGES),
    *(reverse(name, kwargs={"slug": slug}) for name, slug in DETAIL_PAGES),
]
#: Competitor names must never appear on our pages.
COMPETITORS = re.compile(r"manychat|chatfuel|zorcha|inro|linkdm|superprofile|spur", re.I)


def _structured_data(body: str) -> dict:
    match = re.search(r'<script type="application/ld\+json">(.*?)</script>', body, re.S)
    assert match, "page has no structured data"
    return json.loads(match.group(1))


@pytest.mark.parametrize("url", ALL_URLS)
def test_every_marketing_page_is_public_and_described(client, url):
    response = client.get(url)
    assert response.status_code == 200
    body = response.content.decode()
    assert body.count("<h1") == 1
    assert '<meta name="description" content="' in body
    assert f'<link rel="canonical" href="{settings.APP_BASE_URL}{url}">' in body
    assert 'property="og:title"' in body
    assert "noindex" not in body
    graph = _structured_data(body)["@graph"]
    assert graph[0]["@type"] == "BreadcrumbList"
    assert not COMPETITORS.search(body)


@pytest.mark.parametrize("url", [reverse("core:landing"), *ALL_URLS])
def test_no_page_names_a_competitor(client, url):
    assert not COMPETITORS.search(client.get(url).content.decode())


def test_titles_and_descriptions_are_unique_and_search_sized(client):
    titles, descriptions = set(), set()
    for url in ALL_URLS:
        body = client.get(url).content.decode()
        title = re.search(r"<title>(.*?)</title>", body, re.S).group(1).strip()
        description = re.search(r'<meta name="description" content="(.*?)">', body).group(1)
        assert len(title) <= 70, title
        assert 50 <= len(description) <= 170, description
        titles.add(title)
        descriptions.add(description)
    assert len(titles) == len(descriptions) == len(ALL_URLS)


def test_unknown_slugs_are_404(client):
    for name in ("marketing:feature", "marketing:use-case", "marketing:guide"):
        assert client.get(reverse(name, kwargs={"slug": "nope"})).status_code == 404


def test_faq_page_marks_up_every_question_once(client):
    body = client.get(reverse("marketing:faq")).content.decode()
    faq = next(i for i in _structured_data(body)["@graph"] if i["@type"] == "FAQPage")
    assert len(faq["mainEntity"]) == len(ALL_FAQS)
    assert body.count('class="faq-item"') == len(ALL_FAQS)


def test_pricing_page_offers_the_plan_in_rupees(client):
    body = client.get(reverse("marketing:pricing")).content.decode()
    app = next(i for i in _structured_data(body)["@graph"] if i["@type"] == "SoftwareApplication")
    assert app["offers"] == {**app["offers"], "price": "99", "priceCurrency": "INR"}
    assert "aggregateRating" not in app  # never invent ratings


def test_guides_are_articles(client):
    guide = GUIDES[0]
    body = client.get(reverse("marketing:guide", kwargs={"slug": guide.slug})).content.decode()
    article = next(i for i in _structured_data(body)["@graph"] if i["@type"] == "Article")
    assert article["headline"] == guide.title
    assert '<meta property="og:type" content="article">' in body


def test_landing_page_has_structured_data_and_links_to_features(client):
    body = client.get(reverse("core:landing")).content.decode()
    types = {item["@type"] for item in _structured_data(body)["@graph"]}
    assert {"Organization", "WebSite", "SoftwareApplication", "FAQPage"} <= types
    assert reverse("marketing:feature", kwargs={"slug": "comment-to-dm"}) in body
    assert reverse("marketing:pricing") in body  # header and footer


def test_signed_in_pages_are_not_indexed(client, user):
    client.force_login(user)
    body = client.get(reverse("core:dashboard")).content.decode()
    assert '<meta name="robots" content="noindex">' in body


def test_robots_txt_hides_private_areas_and_points_to_the_sitemap(client):
    response = client.get("/robots.txt")
    assert response["Content-Type"].startswith("text/plain")
    text = response.content.decode()
    for path in ("/dashboard/", "/billing/", "/admin/", "/accounts/"):
        assert f"Disallow: {path}" in text
    assert f"Sitemap: {settings.APP_BASE_URL}/sitemap.xml" in text
    assert "Disallow: /\n" not in text


def test_sitemap_lists_every_public_page_on_the_public_domain(client):
    response = client.get("/sitemap.xml", HTTP_HOST="testserver")
    assert response.status_code == 200
    body = response.content.decode()
    locations = re.findall(r"<loc>(.*?)</loc>", body)
    assert len(locations) == len(PublicSitemap().items())
    assert all(loc.startswith(f"{settings.APP_BASE_URL}/") for loc in locations)
    for url in ALL_URLS:
        assert f"<loc>{settings.APP_BASE_URL}{url}</loc>" in body
    assert "/dashboard/" not in body
