"""Tests for webapp/billing.py — checkout session resolution."""

from __future__ import annotations

import pytest

from webapp.billing import create_checkout_session
from webapp.offers import Offer


def test_create_checkout_session_valid_slug_returns_stripe(monkeypatch) -> None:
    monkeypatch.setenv("STRIPE_LINK_STARTER_MASS", "https://buy.stripe.com/test_79")
    result = create_checkout_session("starter-mass", sku_count=300)
    assert result["checkout_kind"] == "stripe"
    assert result["checkout_url"] == "https://buy.stripe.com/test_79"
    assert result["sku_count"] == 300
    assert result["ceiling_hit"] is False
    assert isinstance(result["offer"], Offer)


def test_create_checkout_session_invalid_slug_raises() -> None:
    with pytest.raises(ValueError, match="unknown offer slug"):
        create_checkout_session("no-existe")


def test_create_checkout_session_degrades_to_mailto_without_stripe(monkeypatch) -> None:
    monkeypatch.delenv("STRIPE_LINK_STARTER_MASS", raising=False)
    monkeypatch.setenv("OPERATOR_EMAIL", "sales@linchpin.example")
    result = create_checkout_session("starter-mass", sku_count=600)
    assert result["checkout_kind"] == "mailto"
    assert result["ceiling_hit"] is True  # 600 > 500


def test_gratis_offer_has_zero_price_string() -> None:
    result = create_checkout_session("gratis")
    assert "USD 0" in result["offer"].price
    assert "watermark" in result["offer"].price
