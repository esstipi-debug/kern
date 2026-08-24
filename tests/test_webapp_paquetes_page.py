"""Tests for webapp/paquetes_page.py: server-rendered HTML for /paquetes and /paquetes/{slug}."""

from __future__ import annotations

from html import escape

from webapp.offers import OFFERS, catalog_offers, get_offer
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
    for offer in catalog_offers():
        assert escape(offer.name) in html
        assert escape(offer.price) in html
        assert escape(offer.cadence) in html
        assert f"/paquetes/{offer.slug}" in html
    for offer in OFFERS:
        if offer.lane == "upgrade":
            assert f"/paquetes/{offer.slug}" not in html


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
    assert html.count("mailto:") == len(catalog_offers()) * 2  # agendar + pagar per visible offer


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


def test_index_shows_band_picker_widget_on_gmv_band_offers() -> None:
    """Starter/Growth/Scale have GMV-band pricing (webapp/pricing_quote.py)
    -- their cards get an additive band-picker widget keyed to the API's
    package_key vocabulary (starter/growth/scale). Retainer is deliberately
    excluded (see test_index_omits_band_picker_widget_on_non_gmv_band_offers):
    it is flat/unbanded and upgrade-only, not sold cold off a revenue figure."""
    html = render_index_html(OFFERS, _PROFILE)
    gmv_band = {
        "starter-fundamentos": "starter",
        "growth-operacion": "growth",
    }
    for slug, package_key in gmv_band.items():
        assert f'data-slug="{slug}"' in html
        assert f'data-package="{package_key}"' in html
    assert "/api/pricing-quote" in html
    assert "Calcular" in html
    # Scale is upgrade-lane: no card, so no band-picker on the cold index.
    assert 'data-slug="scale-red-sop"' not in html


def test_index_omits_band_picker_widget_on_non_gmv_band_offers() -> None:
    """diagnostico/starter_latam/proyecto_*/liquidacion/retainer have no GMV-band
    pricing -- /api/pricing-quote 400s for them, so they must NOT get the widget.
    Retainer is flat ($4,500/mo, UNBANDED in src/commercial_pricing.py) and
    upgrade-only (never sold cold), so a "calculate by revenue" widget on its
    card would be misleading even though it keeps its static price display."""
    html = render_index_html(OFFERS, _PROFILE)
    non_gmv_band_slugs = (
        "diagnostico-arranque",
        "starter-fundamentos-latam",
        "proyecto-red-almacen",
        "proyecto-sourcing",
        "sprint-liquidacion",
        "diagnostico-posicion-precios",
        "retainer-ejecutivo",
    )
    for slug in non_gmv_band_slugs:
        assert f'data-slug="{slug}"' not in html


def test_index_band_picker_is_additive_static_price_string_unchanged() -> None:
    """The band-picker widget must be purely additive: every offer's existing
    static escape(offer.price) anchor still renders, byte-for-byte, unchanged."""
    html = render_index_html(OFFERS, _PROFILE)
    for offer in catalog_offers():
        assert f'<div class="price">{escape(offer.price)}</div>' in html


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


def test_stocky_landing_uses_app_store_prices_not_operator_starter() -> None:
    from webapp.stocky_alternative_page import render_stocky_alternative_html

    alerts = get_offer("kern-alerts")
    check = get_offer("chequeo-stocky")
    html = render_stocky_alternative_html(alerts, check)
    assert "kern-alerts" in html
    assert "chequeo-stocky" in html
    assert "49" in html and "99" in html
    assert "350" in html
    assert "/paquetes/starter-fundamentos" not in html
    assert "USD 900" not in html
