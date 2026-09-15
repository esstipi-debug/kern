"""Visual and discoverability checks for the GEO blog.

These import the page renderers directly -- not ``webapp.app`` -- so they run
without nosilo-core. Route-level tests live in ``test_blog_page.py``.
"""
from __future__ import annotations

from pathlib import Path

from webapp.blog_loader import load_posts, posts_for_lang
from webapp.blog_page import render_blog_article_html, render_blog_index_html
from webapp.offers import OFFERS, get_offer
from webapp.one_plan_page import render_one_plan_html
from webapp.operator_profile import OperatorProfile
from webapp.paquetes_page import render_index_html
from webapp.stocky_alternative_page import render_stocky_alternative_html

REPO = Path(__file__).resolve().parent.parent
INDEXED = [p for p in load_posts() if not p.noindex]


def test_index_is_a_card_list_with_language_switch() -> None:
    html = render_blog_index_html("es", posts_for_lang("es"))
    assert 'class="card"' in html
    assert 'class="lang"' in html
    assert 'aria-current="true">ES</a>' in html
    assert 'href="/blog/en"' in html
    assert html.count('class="card"') == sum(1 for p in INDEXED if p.lang == "es")


def test_english_index_marks_en_current() -> None:
    html = render_blog_index_html("en", posts_for_lang("en"))
    assert 'aria-current="true">EN</a>' in html
    assert 'href="/blog"' in html


def test_article_has_labelled_answer_breadcrumb_and_warm_cta() -> None:
    post = next(p for p in INDEXED if p.slug == "stock-muerto")
    html = render_blog_article_html(post)
    assert 'class="answer"' in html
    assert "La respuesta" in html
    assert 'class="crumb"' in html
    assert 'class="btn primary"' in html
    assert f"/demo?source=blog-{post.slug}" in html
    assert 'aria-current="true">ES</a>' in html


def test_answer_is_not_repeated_under_the_callout() -> None:
    post = next(p for p in INDEXED if p.slug == "stock-muerto")
    html = render_blog_article_html(post)
    assert html.count(post.answer.strip()) == 1
    assert "Qué se mide" in html


def test_article_tables_are_wrapped_for_overflow() -> None:
    post = next(p for p in INDEXED if p.slug == "stock-muerto")
    html = render_blog_article_html(post)
    assert "<table>" in html
    assert 'class="table-wrap"' in html


def test_stocky_spanish_lang_switch_points_at_the_landing() -> None:
    post = next(p for p in INDEXED if p.slug == "alternativa-stocky")
    html = render_blog_article_html(post)
    assert 'href="/stocky-alternative"' in html
    assert 'aria-current="true">ES</a>' in html


def test_sales_pages_link_to_the_blog() -> None:
    profile = OperatorProfile(name="T", bio="b", photo_url="", linkedin_url="", email="")
    paquetes = render_index_html(OFFERS, profile)
    starter = get_offer("starter-fundamentos")
    diagnostico = get_offer("diagnostico-arranque")
    assert starter is not None and diagnostico is not None
    stocky = render_stocky_alternative_html(starter, diagnostico)
    one_plan = render_one_plan_html(starter, diagnostico)
    demo = (REPO / "webapp/static/demo/index.html").read_text(encoding="utf-8")
    home = (REPO / "webapp/static/index.html").read_text(encoding="utf-8")
    for html, name in (
        (paquetes, "paquetes"),
        (stocky, "stocky"),
        (one_plan, "one-plan"),
        (demo, "demo"),
        (home, "home"),
    ):
        assert 'href="/blog"' in html, f"{name} is missing a Blog link"


def test_blog_shell_matches_the_rest_of_the_site() -> None:
    """Diamond brand + warm CTA, same as /demo and /stocky-alternative."""
    html = render_blog_index_html("es", posts_for_lang("es"))
    assert "&#9672;" in html
    assert "--accent-warm" in html
    assert 'class="iconrow"' in html
