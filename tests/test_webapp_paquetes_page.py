"""Tests for webapp/paquetes_page.py: server-rendered HTML for /paquetes and /paquetes/{slug}."""

from __future__ import annotations

from html import escape

from webapp.offers import OFFERS, get_offer
from webapp.operator_profile import OperatorProfile
from webapp.paquetes_page import render_index_html, render_offer_html

_PROFILE = OperatorProfile(
    name="Jane Doe",
    bio="Bio de prueba.",
    photo_url="",
    linkedin_url="",
    email="",
)


def test_index_lists_all_seven_offers_with_price_and_cadence() -> None:
    html = render_index_html(OFFERS, _PROFILE)
    for offer in OFFERS:
        assert escape(offer.name) in html
        assert escape(offer.price) in html
        assert escape(offer.cadence) in html
        assert f"/paquetes/{offer.slug}" in html


def test_index_shows_operator_name_and_bio() -> None:
    html = render_index_html(OFFERS, _PROFILE)
    assert "Jane Doe" in html
    assert "Bio de prueba." in html


def test_index_falls_back_to_avatar_initial_without_photo() -> None:
    html = render_index_html(OFFERS, _PROFILE)
    assert 'class="avatar-fallback"' in html
    assert ">J<" in html


def test_index_rejects_javascript_uri_in_photo_and_linkedin() -> None:
    hostile = OperatorProfile(
        name="Jane",
        bio="ok",
        photo_url="javascript:alert(1)",
        linkedin_url="javascript:alert(1)",
        email="",
    )
    html = render_index_html(OFFERS, hostile)
    assert "javascript:" not in html
    assert 'class="avatar-fallback"' in html  # falls back since photo_url was rejected


def test_index_accepts_same_origin_photo_path() -> None:
    same_origin = OperatorProfile(
        name="Jane", bio="ok", photo_url="/static/operator/foto.jpg", linkedin_url="", email=""
    )
    html = render_index_html(OFFERS, same_origin)
    assert '<img src="/static/operator/foto.jpg"' in html


def test_index_escapes_operator_bio_html() -> None:
    hostile = OperatorProfile(
        name="<script>alert(1)</script>", bio="ok", photo_url="", linkedin_url="", email=""
    )
    html = render_index_html(OFFERS, hostile)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_index_cta_degrades_to_mailto_without_env_vars(monkeypatch) -> None:
    for offer in OFFERS:
        monkeypatch.delenv(offer.stripe_env_var, raising=False)
    monkeypatch.delenv("CALENDLY_URL", raising=False)
    html = render_index_html(OFFERS, _PROFILE)
    assert html.count("mailto:") == len(OFFERS) * 2  # agendar + pagar per offer


def test_offer_page_embeds_fetch_of_its_own_md_file() -> None:
    offer = get_offer("growth-operacion")
    html = render_offer_html(offer, _PROFILE)
    assert escape(offer.name) in html
    assert "/paquetes-docs/growth-operacion.md" in html
    assert "marked.min.js" in html


def test_offer_page_cta_uses_stripe_link_when_configured(monkeypatch) -> None:
    offer = get_offer("diagnostico-arranque")
    monkeypatch.setenv(offer.stripe_env_var, "https://buy.stripe.com/diag123")
    html = render_offer_html(offer, _PROFILE)
    assert "https://buy.stripe.com/diag123" in html


def test_offer_page_inline_script_has_balanced_braces() -> None:
    """Regression test: an f-string brace-escaping slip (`}}` instead of `}` on a
    plain, non-f-string line) previously produced invalid JS that silently broke
    the fetch().then() chain, leaving the page stuck on "Cargando...". """
    offer = get_offer("growth-operacion")
    html = render_offer_html(offer, _PROFILE)
    # the second <script> block is the inline one (the first is the marked.min.js src tag)
    inline_script = html.split("<script>", 1)[1].split("</script>", 1)[0]
    assert inline_script.count("{") == inline_script.count("}")
    assert ".then(function(md){document" in inline_script
