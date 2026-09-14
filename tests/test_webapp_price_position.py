"""Tests for GET /api/price-position/{sku} -- the last unbuilt route of the
Linchpin 3.0 plan's section 9, flagged open by the 2026-07-13 gap audit and
still missing when the audits were re-verified on 2026-09-12.

Isolated ledger (same fixture idiom as tests/test_webapp_watch.py) so these
never touch the real, gitignored ``data/`` directory.

Guarantees under test:
- the route reads the SAME production PriceLedger POST /api/watch writes to,
  and returns every observed competitor offer with its provenance and age;
- WITHOUT ``our_price`` the competitor panel still comes back in full, and
  the index is null/insufficient_signal -- never a fabricated 1.0;
- WITH ``our_price`` the index is exactly what
  ``jobs.price_intelligence._position_index`` returns for the same inputs,
  so this route can never drift from price_position_matrix.xlsx's own math;
- a SKU with no observations is a 200 that DECLARES the gap, not a 404 and
  not a zero;
- a non-positive our_price is rejected loudly, not coerced;
- the route is gated behind LINCHPIN_API_KEY like every other endpoint that
  serves a client's own commercial data.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

pytest.importorskip("fastapi")
try:
    import python_multipart  # noqa: F401
except ImportError:
    pytest.importorskip("multipart")
from fastapi.testclient import TestClient  # noqa: E402

import webapp.app as appmod  # noqa: E402
from jobs.price_intelligence import _position_index  # noqa: E402
from src.pricing_intel.ledger import PriceLedger  # noqa: E402
from src.pricing_intel.models import CompetitorOffer  # noqa: E402
from webapp import security  # noqa: E402
from webapp.app import app  # noqa: E402

client = TestClient(app)

_SKU = "SKU-WIDGET-9000"


def _offer(site: str, price: str, *, observed_at: datetime, product_id: str | None = _SKU) -> CompetitorOffer:
    return CompetitorOffer(
        observed_at=observed_at, site=site,
        competitor_sku_ref=f"https://{site}/p/widget-9000", matched_product_id=product_id,
        match_confidence=0.97,
        price=Decimal(price), currency="USD",
        price_normalized=Decimal(price), shipping=None,
        availability="InStock", promo_flag=False, list_price=None,
        acquisition_tier="L2", extractor="changedetection_io_webhook", extractor_version="1",
        extraction_confidence=0.85,
    )


@pytest.fixture()
def isolated_ledger(tmp_path, monkeypatch):
    ledger_path = tmp_path / "pricing_intel_ledger"
    monkeypatch.setattr(appmod, "PRICE_LEDGER_PATH", str(ledger_path))
    security.reset_rate_limit()
    monkeypatch.setattr(security, "RATE_LIMIT", 0)
    monkeypatch.setattr(security, "API_KEY", "")
    return ledger_path


@pytest.fixture()
def three_offers(isolated_ledger):
    """100 / 200 / 300 -- average exactly 200, so a position index is
    hand-checkable rather than an artifact of the fixture."""
    now = datetime.now(timezone.utc)
    ledger = PriceLedger(isolated_ledger)
    for site, price, hours in (("a.example.com", "100", 1), ("b.example.com", "200", 5), ("c.example.com", "300", 9)):
        observed = now - timedelta(hours=hours)
        ledger.append([_offer(site, price, observed_at=observed)], now=observed)
    ledger.close()
    return isolated_ledger


def test_unobserved_sku_declares_the_gap_instead_of_returning_a_zero(isolated_ledger):
    """The ledger holds no catalogue of valid SKUs, so it cannot tell "never
    watched" from "typo" -- answering 404 would assert a distinction the data
    does not support. It says so instead."""
    resp = client.get("/api/price-position/NOPE-404")
    assert resp.status_code == 200
    body = resp.json()
    assert body["observed_offers"] == 0
    assert body["competitor_panel"] is None
    assert body["position_index"] is None
    assert body["competitor_read"] == "insufficient_signal"
    assert body["offers"] == []


def test_panel_comes_back_in_full_without_our_price(three_offers):
    resp = client.get(f"/api/price-position/{_SKU}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["observed_offers"] == 3
    assert body["competitor_panel"] == {"min": 100.0, "avg": 200.0, "max": 300.0, "observations": 3}
    # the panel is real persisted data; the index is not derivable without our price
    assert body["our_price"] is None
    assert body["position_index"] is None
    assert body["competitor_read"] == "insufficient_signal"


def test_index_matches_price_intelligence_verbatim(three_offers):
    """Mutation guard on golden rule 1: if this route ever grows its own copy
    of the our_price/avg(competitor) math, this assert is what catches it."""
    resp = client.get(f"/api/price-position/{_SKU}", params={"our_price": 240})
    body = resp.json()
    expected = _position_index(Decimal("240"), [Decimal("100"), Decimal("200"), Decimal("300")])
    assert body["position_index"] == pytest.approx(float(expected))
    assert body["position_index"] == pytest.approx(1.2)  # 240 / 200, pricier than the field
    assert body["competitor_read"] == "confirmed"
    assert body["our_price"] == 240.0


def test_cheaper_than_the_field_reads_below_one(three_offers):
    body = client.get(f"/api/price-position/{_SKU}", params={"our_price": 150}).json()
    assert body["position_index"] == pytest.approx(0.75)
    assert body["competitor_read"] == "confirmed"


def test_our_price_is_useless_without_observations_and_says_so(isolated_ledger):
    body = client.get("/api/price-position/NOPE-404", params={"our_price": 150}).json()
    assert body["our_price"] == 150.0
    assert body["position_index"] is None
    assert body["competitor_read"] == "insufficient_signal"


def test_offers_carry_provenance_and_age(three_offers):
    body = client.get(f"/api/price-position/{_SKU}").json()
    sites = {o["site"] for o in body["offers"]}
    assert sites == {"a.example.com", "b.example.com", "c.example.com"}
    for offer in body["offers"]:
        assert offer["acquisition_tier"] == "L2"
        assert offer["extractor"] == "changedetection_io_webhook"
        assert offer["availability"] == "InStock"
        assert offer["age_hours"] > 0
    # freshest/stalest bracket the per-offer ages, so staleness is visible
    # without the caller re-deriving it.
    ages = [o["age_hours"] for o in body["offers"]]
    assert body["freshest_age_hours"] == pytest.approx(min(ages))
    assert body["stalest_age_hours"] == pytest.approx(max(ages))
    assert body["stalest_age_hours"] > body["freshest_age_hours"]


def test_a_non_positive_our_price_is_rejected_not_coerced(three_offers):
    assert client.get(f"/api/price-position/{_SKU}", params={"our_price": 0}).status_code == 422
    assert client.get(f"/api/price-position/{_SKU}", params={"our_price": -5}).status_code == 422


def test_another_products_offers_never_leak_into_this_sku(isolated_ledger):
    now = datetime.now(timezone.utc)
    ledger = PriceLedger(isolated_ledger)
    ledger.append([_offer("a.example.com", "100", observed_at=now, product_id="OTHER-SKU")], now=now)
    ledger.close()

    body = client.get(f"/api/price-position/{_SKU}").json()
    assert body["observed_offers"] == 0
    assert client.get("/api/price-position/OTHER-SKU").json()["observed_offers"] == 1


def test_gated_behind_api_key_when_configured(three_offers, monkeypatch):
    monkeypatch.setattr(security, "API_KEY", "s3cret")
    assert client.get(f"/api/price-position/{_SKU}", headers={"X-API-Key": "nope"}).status_code == 401
    assert client.get(f"/api/price-position/{_SKU}").status_code == 401
    ok = client.get(f"/api/price-position/{_SKU}", headers={"X-API-Key": "s3cret"})
    assert ok.status_code == 200
