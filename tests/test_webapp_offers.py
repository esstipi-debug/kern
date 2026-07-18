"""Tests for webapp/offers.py: package data integrity + CTA resolution/degradation."""

from __future__ import annotations

from pathlib import Path

from webapp.offers import (
    OFFERS,
    get_offer,
    is_safe_external_url,
    is_safe_same_origin_or_external_url,
    resolve_agendar_cta,
    resolve_pagar_cta,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_ten_official_packages_present() -> None:
    assert len(OFFERS) == 10
    assert len({offer.slug for offer in OFFERS}) == 10  # slugs unique


def test_get_offer_returns_starter_fundamentos_latam() -> None:
    """Stage 1.5 (A1): LatAm reduced-scope Starter-equivalent, USD 250-300/mes."""
    offer = get_offer("starter-fundamentos-latam")
    assert offer is not None
    assert "250" in offer.price and "300" in offer.price
    assert offer.md_file == "starter-fundamentos-latam.md"


def test_get_offer_returns_diagnostico_posicion_precios() -> None:
    offer = get_offer("diagnostico-posicion-precios")
    assert offer is not None
    assert offer.name.startswith("Diagnostico de Posicion de Precios")
    assert offer.md_file == "diagnostico-posicion-precios.md"


def test_every_offer_has_a_real_one_pager_file() -> None:
    for offer in OFFERS:
        md_path = REPO_ROOT / "documentation" / "paquetes" / offer.md_file
        assert md_path.is_file(), f"missing one-pager for {offer.slug}: {md_path}"


def test_get_offer_returns_offer_for_known_slug() -> None:
    offer = get_offer("starter-fundamentos")
    assert offer is not None
    assert offer.name.startswith("Starter")


def test_get_offer_returns_none_for_unknown_slug() -> None:
    assert get_offer("no-existe") is None


def test_stripe_env_var_naming() -> None:
    offer = get_offer("proyecto-red-almacen")
    assert offer.stripe_env_var == "STRIPE_LINK_PROYECTO_RED_ALMACEN"


def test_agendar_cta_uses_calendly_when_configured(monkeypatch) -> None:
    monkeypatch.setenv("CALENDLY_URL", "https://calendly.com/linchpin/intro")
    offer = get_offer("growth-operacion")
    cta = resolve_agendar_cta(offer)
    assert cta.kind == "calendly"
    assert cta.href == "https://calendly.com/linchpin/intro"


def test_agendar_cta_degrades_to_mailto_without_calendly(monkeypatch) -> None:
    monkeypatch.delenv("CALENDLY_URL", raising=False)
    monkeypatch.delenv("OPERATOR_EMAIL", raising=False)
    offer = get_offer("growth-operacion")
    cta = resolve_agendar_cta(offer)
    assert cta.kind == "mailto"
    assert cta.href.startswith("mailto:?subject=")


def test_pagar_cta_uses_stripe_link_when_configured(monkeypatch) -> None:
    offer = get_offer("starter-fundamentos")
    monkeypatch.setenv(offer.stripe_env_var, "https://buy.stripe.com/test_abc123")
    cta = resolve_pagar_cta(offer)
    assert cta.kind == "stripe"
    assert cta.href == "https://buy.stripe.com/test_abc123"


def test_pagar_cta_degrades_to_mailto_without_stripe_link(monkeypatch) -> None:
    offer = get_offer("starter-fundamentos")
    monkeypatch.delenv(offer.stripe_env_var, raising=False)
    monkeypatch.setenv("OPERATOR_EMAIL", "ventas@linchpin.example")
    cta = resolve_pagar_cta(offer)
    assert cta.kind == "mailto"
    assert cta.href.startswith("mailto:ventas@linchpin.example?subject=")


def test_pagar_cta_for_one_package_never_uses_another_packages_stripe_link(monkeypatch) -> None:
    monkeypatch.delenv("STRIPE_LINK_STARTER_FUNDAMENTOS", raising=False)
    monkeypatch.setenv("STRIPE_LINK_GROWTH_OPERACION", "https://buy.stripe.com/growth")
    cta = resolve_pagar_cta(get_offer("starter-fundamentos"))
    assert cta.kind == "mailto"


def test_pagar_cta_rejects_javascript_uri_and_degrades_to_mailto(monkeypatch) -> None:
    offer = get_offer("starter-fundamentos")
    monkeypatch.setenv(offer.stripe_env_var, "javascript:alert(1)")
    cta = resolve_pagar_cta(offer)
    assert cta.kind == "mailto"
    assert "javascript:" not in cta.href


def test_agendar_cta_rejects_javascript_uri_and_degrades_to_mailto(monkeypatch) -> None:
    monkeypatch.setenv("CALENDLY_URL", "javascript:alert(1)")
    cta = resolve_agendar_cta(get_offer("starter-fundamentos"))
    assert cta.kind == "mailto"
    assert "javascript:" not in cta.href


def test_is_safe_external_url_allows_only_http_https() -> None:
    assert is_safe_external_url("https://buy.stripe.com/abc")
    assert is_safe_external_url("http://example.com")
    assert not is_safe_external_url("javascript:alert(1)")
    assert not is_safe_external_url("data:text/html,<script>alert(1)</script>")
    assert not is_safe_external_url("/relative/path")


def test_is_safe_same_origin_or_external_url_allows_relative_paths() -> None:
    assert is_safe_same_origin_or_external_url("/static/operator/foto.jpg")
    assert is_safe_same_origin_or_external_url("https://cdn.example.com/foto.jpg")
    assert not is_safe_same_origin_or_external_url("//evil.com/foto.jpg")  # protocol-relative
    assert not is_safe_same_origin_or_external_url("javascript:alert(1)")
