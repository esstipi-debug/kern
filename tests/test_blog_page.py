"""Renderer tests for the GEO blog, mirroring tests/test_stocky_alternative_page.py.

Covers section 10 of docs/superpowers/specs/2026-08-25-blog-geo-i18n-design.md:
routes, JSON-LD, hreflang, canonical host, the raw-markdown byte equality, both
llms feeds, and the sitemap.
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET

import pytest
from fastapi.testclient import TestClient

from src.seo.llms_txt import is_valid_llms_txt
from webapp.app import app
from webapp.blog_loader import BLOG_DIR, load_posts
from webapp.blog_page import public_base_url

client = TestClient(app)

INDEXED = [p for p in load_posts() if not p.noindex]


def _jsonld(html: str) -> dict:
    m = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    assert m, "article has no JSON-LD block"
    return json.loads(m.group(1).replace("<\\/", "</"))


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("path", ["/blog", "/blog/en"])
def test_indexes_render(path: str) -> None:
    r = client.get(path)
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


@pytest.mark.parametrize("post", INDEXED, ids=lambda p: f"{p.lang}-{p.slug}")
def test_every_post_renders(post) -> None:
    r = client.get(post.url_path)
    assert r.status_code == 200
    assert post.title.split(":")[0][:24] in r.text


def test_unknown_slug_is_404() -> None:
    assert client.get("/blog/does-not-exist").status_code == 404


@pytest.mark.parametrize(
    "attack",
    ["/blog/../../etc/passwd", "/blog/..%2f..%2fetc%2fpasswd", "/blog/....//secrets"],
)
def test_path_traversal_is_404(attack: str) -> None:
    """The slug is matched against the loaded set, never joined onto a path, so
    every one of these misses rather than escaping the blog directory."""
    assert client.get(attack).status_code == 404


def test_index_lists_only_indexed_posts() -> None:
    body = client.get("/blog").text
    for p in load_posts():
        if p.lang != "es":
            continue
        assert (p.url_path in body) is (not p.noindex)


# --------------------------------------------------------------------------- #
# Raw markdown for AI crawlers
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("post", INDEXED, ids=lambda p: f"{p.lang}-{p.slug}")
def test_markdown_route_is_byte_identical(post) -> None:
    r = client.get(f"{post.url_path}.md")
    assert r.status_code == 200
    assert "text/markdown" in r.headers["content-type"]
    assert r.content == post.path.read_bytes()


# --------------------------------------------------------------------------- #
# JSON-LD, hreflang, canonical
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("post", INDEXED, ids=lambda p: f"{p.lang}-{p.slug}")
def test_jsonld_has_the_three_types(post) -> None:
    graph = _jsonld(client.get(post.url_path).text)["@graph"]
    assert [n["@type"] for n in graph] == ["Article", "FAQPage", "BreadcrumbList"]


@pytest.mark.parametrize("post", INDEXED, ids=lambda p: f"{p.lang}-{p.slug}")
def test_jsonld_faq_matches_frontmatter(post) -> None:
    faq = _jsonld(client.get(post.url_path).text)["@graph"][1]["mainEntity"]
    assert [q["name"] for q in faq] == [f.q for f in post.faq]


@pytest.mark.parametrize("post", INDEXED, ids=lambda p: f"{p.lang}-{p.slug}")
def test_canonical_is_own_language_and_public_host(post) -> None:
    html = client.get(post.url_path).text
    canon = re.search(r'rel="canonical" href="([^"]+)"', html).group(1)
    assert canon == f"{public_base_url()}{post.url_path}"


@pytest.mark.parametrize("post", INDEXED, ids=lambda p: f"{p.lang}-{p.slug}")
def test_hreflang_pair_and_x_default_english(post) -> None:
    html = client.get(post.url_path).text
    alts = dict(re.findall(r'hreflang="([^"]+)" href="([^"]+)"', html))
    assert post.lang in alts
    if "en" in alts:
        assert alts.get("x-default") == alts["en"], "x-default must be the English URL"


def test_stocky_pair_points_at_the_existing_landing() -> None:
    """The Spanish Stocky article's English alternate is /stocky-alternative,
    which is not under /blog and is not one of the 11 markdown files."""
    html = client.get("/blog/alternativa-stocky").text
    alts = dict(re.findall(r'hreflang="([^"]+)" href="([^"]+)"', html))
    assert alts["en"] == f"{public_base_url()}/stocky-alternative"
    assert not (BLOG_DIR / "en" / "stocky-alternative.md").exists()
    assert not (BLOG_DIR / "en" / "alternativa-stocky.md").exists()


# --------------------------------------------------------------------------- #
# Machine surfaces
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("path", ["/llms.txt", "/llms.es.txt"])
def test_llms_feeds_are_valid(path: str) -> None:
    r = client.get(path)
    assert r.status_code == 200
    assert is_valid_llms_txt(r.text), f"{path} failed src.seo.llms_txt validation"


def test_llms_feeds_are_language_split() -> None:
    en, es = client.get("/llms.txt").text, client.get("/llms.es.txt").text
    for p in INDEXED:
        assert (p.url_path in en) is (p.lang == "en")
        assert (p.url_path in es) is (p.lang == "es")


def test_sitemap_lists_every_indexed_slug() -> None:
    r = client.get("/sitemap.xml")
    assert r.status_code == 200
    ET.fromstring(r.text)  # well-formed or raises
    for p in INDEXED:
        assert f"{public_base_url()}{p.url_path}" in r.text
    for path in ("/demo", "/how-it-works"):
        assert f"{public_base_url()}{path}" in r.text


def test_sitemap_no_anuncia_las_paginas_congeladas() -> None:
    """PR-A del plan del 2026-09-05: /paquetes, /stocky-alternative y /one-plan
    (ofertas y canales congelados hasta el primer pago) y /console y /tower (entrada
    del operador, no del cliente) siguen respondiendo pero no se anuncian."""
    from webapp.blog_seo import UNLISTED_PATHS

    body = client.get("/sitemap.xml").text
    for path in UNLISTED_PATHS:
        assert f"{public_base_url()}{path}<" not in body, path


def test_la_portada_no_enlaza_a_las_paginas_congeladas() -> None:
    html = client.get("/").text
    assert 'href="/console"' not in html
    assert 'href="/paquetes"' not in html
    assert 'href="/how-it-works"' in html and 'href="/demo"' in html


def test_sitemap_omits_noindex_posts() -> None:
    body = client.get("/sitemap.xml").text
    for p in load_posts():
        if p.noindex:
            assert p.url_path not in body


def test_robots_allows_ai_agents_and_points_at_sitemap() -> None:
    body = client.get("/robots.txt").text
    for agent in ("GPTBot", "PerplexityBot", "Google-Extended"):
        assert f"User-agent: {agent}" in body
    assert f"Sitemap: {public_base_url()}/sitemap.xml" in body


# --------------------------------------------------------------------------- #
# Safety
# --------------------------------------------------------------------------- #

def test_article_body_contains_no_raw_html() -> None:
    """The renderer escapes before re-introducing its own subset, so a body can
    never inject a tag. One script element is expected: the JSON-LD block."""
    for post in INDEXED:
        html = client.get(post.url_path).text
        assert html.lower().count("<script") == 1
        assert "<iframe" not in html.lower()


def test_cta_carries_the_blog_source() -> None:
    """Success is measured as leads whose source starts with 'blog-', so the
    demo link has to carry it."""
    for post in INDEXED:
        html = client.get(post.url_path).text
        if post.cta in ("demo", "both"):
            assert f"/demo?source=blog-{post.slug}" in html


# --------------------------------------------------------------------------- #
# Figures
# --------------------------------------------------------------------------- #

def test_figure_without_label_is_rejected() -> None:
    """A chart is the easiest place to smuggle an unlabelled number past
    section 10, so the label is enforced in code, not by review."""
    from webapp.blog_figures import FigureError, render_figure

    with pytest.raises(FigureError, match="label"):
        render_figure(json.dumps({"type": "buckets", "items": [{"label": "x", "value": 1}]}))


def test_figure_rejects_unknown_type_and_bad_json() -> None:
    from webapp.blog_figures import FigureError, render_figure

    with pytest.raises(FigureError):
        render_figure(json.dumps({"type": "piechart", "label": "x"}))
    with pytest.raises(FigureError):
        render_figure("{not json")


def test_figure_draws_only_the_numbers_it_was_given() -> None:
    """The renderer must not derive or round: every value in the spec appears in
    the SVG, and the caption comes through verbatim."""
    from webapp.blog_figures import render_figure

    spec = {
        "type": "bars",
        "bins": [["tau=3", 71], ["tau=5", 92]],
        "label": "Illustrative, not client data.",
    }
    svg = render_figure(json.dumps(spec))
    assert "tau=3" in svg and "tau=5" in svg
    assert "Illustrative, not client data." in svg
    assert "<script" not in svg.lower()


def test_figures_render_inside_real_articles() -> None:
    """The three articles that carry a kern-figure must show it, and the caption
    has to survive into the page."""
    seen = 0
    for post in INDEXED:
        if "kern-figure" not in post.body_md:
            continue
        seen += 1
        html = client.get(post.url_path).text
        assert 'class="fig"' in html, f"{post.slug} lost its figure"
        assert "<figcaption>" in html
        # the raw JSON must not leak into the page as a code block
        assert '"type":' not in html and '"type": ' not in html
    assert seen >= 3, f"expected at least 3 articles with figures, found {seen}"


def test_index_carries_the_icon_strip() -> None:
    for path in ("/blog", "/blog/en"):
        assert 'class="iconrow"' in client.get(path).text


def test_figures_respect_reduced_motion() -> None:
    """Animation is opt-in via the media query: a reduced-motion reader gets the
    finished figure, never a mid-animation one."""
    html = client.get("/blog/dead-stock").text
    assert "prefers-reduced-motion: no-preference" in html
