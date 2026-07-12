"""Tests for POST /api/watch (Linchpin 3.0 PR-15) -- the L2 changedetection.io
webhook receiver wired into webapp/app.py.

Isolated ledgers/sku_map (same fixture idiom as tests/test_webapp_tower.py's
own ``isolated_ledgers``) so these tests never touch the real, gitignored
``data/`` directory or leak state between tests.

Guarantees under test:
- a realistic changedetection.io webhook payload (this module's own
  documented JSON contract) is accepted, sanity-gated, and durably appended
  to the SAME production PriceLedger the L0 scheduled cycle uses;
- a pair with no prior ledger reading fires a new_competitor_listing event;
- a second read with a different price fires a price_move event, both
  landing on the SAME EventLedger POST /api/events would read from;
- a sku_map CONFIRMED match resolves matched_product_id on the response and
  the recorded offer;
- a malformed payload (missing watch_url/price/currency) is a 400, never a
  silent 200;
- the endpoint is gated behind LINCHPIN_API_KEY (401 without/with a wrong
  key, matching every other mutating endpoint's convention).
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

pytest.importorskip("fastapi")
try:
    import python_multipart  # noqa: F401
except ImportError:
    pytest.importorskip("multipart")
from fastapi.testclient import TestClient  # noqa: E402

import webapp.app as appmod  # noqa: E402
from scm_agent.events import EventLedger  # noqa: E402
from src.pricing_intel.ledger import PriceLedger  # noqa: E402
from src.pricing_intel.match.sku_map import AUTO_CONFIRMED_BY, SkuMap  # noqa: E402
from src.pricing_intel.models import CompetitorOffer, MatchCandidate  # noqa: E402
from webapp import security  # noqa: E402
from webapp.app import app  # noqa: E402

client = TestClient(app)

NOW = datetime(2026, 7, 12, 12, 0, 0, tzinfo=timezone.utc)

_PAYLOAD = {
    "uuid": "a1b2c3d4-0000-0000-0000-000000000000",
    "watch_url": "https://competitor-shop.example.com/p/widget-9000",
    "watch_title": "Widget 9000",
    "price": "199.99",
    "previous_price": "219.99",
    "currency": "USD",
    "in_stock": True,
    "notification_timestamp": 1783814400,  # 2026-07-12T00:00:00Z
}


@pytest.fixture()
def isolated_watch_stores(tmp_path, monkeypatch):
    """Point the app's price ledger/sku_map/event ledger at throwaway files
    (same pattern as test_webapp_tower.py's isolated_ledgers) and disable
    rate limiting/API-key auth by default -- individual tests opt back into
    the API-key gate where they test it."""
    ledger_path = tmp_path / "pricing_intel_ledger"
    sku_map_path = tmp_path / "sku_map"
    events_path = tmp_path / "events.sqlite3"
    monkeypatch.setattr(appmod, "PRICE_LEDGER_PATH", str(ledger_path))
    monkeypatch.setattr(appmod, "SKU_MAP_PATH", str(sku_map_path))
    monkeypatch.setattr(appmod, "EVENTS_LEDGER_PATH", str(events_path))
    security.reset_rate_limit()
    monkeypatch.setattr(security, "RATE_LIMIT", 0)
    monkeypatch.setattr(security, "API_KEY", "")
    return ledger_path, sku_map_path, events_path


def test_first_ever_read_is_accepted_and_fires_new_competitor_listing(isolated_watch_stores):
    resp = client.post("/api/watch", json=_PAYLOAD)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "accepted"
    assert body["site"] == "competitor-shop.example.com"
    assert "new_competitor_listing" in body["events"]


def test_observation_is_durably_appended_to_the_production_ledger(isolated_watch_stores):
    ledger_path, _, _ = isolated_watch_stores
    client.post("/api/watch", json=_PAYLOAD)

    ledger = PriceLedger(ledger_path)
    record = ledger.latest_by_sku("competitor-shop.example.com", _PAYLOAD["watch_url"])
    assert record is not None
    assert str(record.offer.price) == "199.99"
    assert record.offer.acquisition_tier == "L2"
    ledger.close()


def test_second_read_with_different_price_fires_price_move(isolated_watch_stores):
    ledger_path, _, _ = isolated_watch_stores
    ledger = PriceLedger(ledger_path)
    earlier_price = CompetitorOffer(
        observed_at=NOW.replace(hour=8), site="competitor-shop.example.com",
        competitor_sku_ref=_PAYLOAD["watch_url"], matched_product_id=None, match_confidence=1.0,
        price=Decimal("219.99"), currency="USD",
        price_normalized=Decimal("219.99"), shipping=None,
        availability="InStock", promo_flag=False, list_price=None,
        acquisition_tier="L2", extractor="changedetection_io_webhook", extractor_version="1",
        extraction_confidence=0.85,
    )
    ledger.append([earlier_price], now=earlier_price.observed_at)
    ledger.close()

    resp = client.post("/api/watch", json=_PAYLOAD)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "accepted"
    assert "price_move" in body["events"]
    # a pair with a PRIOR reading is not "new" -- no listing event this time.
    assert "new_competitor_listing" not in body["events"]

    events = EventLedger(isolated_watch_stores[2])
    recorded = events.list_by_type("price_move")
    assert len(recorded) == 1
    assert recorded[0].payload["old_price_normalized"] == "219.99"
    events.close()


def test_confirmed_sku_map_match_resolves_matched_product_id(isolated_watch_stores):
    _, sku_map_path, _ = isolated_watch_stores
    sku_map = SkuMap(sku_map_path)
    sku_map.record(
        MatchCandidate(
            our_product_id="SKU-100", competitor_sku_ref=_PAYLOAD["watch_url"], site="competitor-shop.example.com",
            method="gtin", score=0.99, status="confirmed", reason="gtin_exact_match:hand-verified",
            confirmed_by=AUTO_CONFIRMED_BY, confirmed_at=NOW,
        ),
        now=NOW,
    )
    sku_map.close()

    resp = client.post("/api/watch", json=_PAYLOAD)
    body = resp.json()
    assert body["matched_product_id"] == "SKU-100"

    ledger_path = isolated_watch_stores[0]
    ledger = PriceLedger(ledger_path)
    record = ledger.latest_by_sku("competitor-shop.example.com", _PAYLOAD["watch_url"])
    assert record.offer.matched_product_id == "SKU-100"
    ledger.close()


def test_missing_watch_url_is_a_400_not_a_silent_200(isolated_watch_stores):
    payload = {k: v for k, v in _PAYLOAD.items() if k != "watch_url"}
    resp = client.post("/api/watch", json=payload)
    assert resp.status_code == 400
    assert "watch_url" in resp.json()["detail"]


def test_missing_price_is_a_400(isolated_watch_stores):
    payload = {k: v for k, v in _PAYLOAD.items() if k != "price"}
    resp = client.post("/api/watch", json=payload)
    assert resp.status_code == 400


def test_gated_behind_api_key_when_configured(isolated_watch_stores, monkeypatch):
    monkeypatch.setattr(security, "API_KEY", "s3cret")
    assert client.post("/api/watch", json=_PAYLOAD, headers={"X-API-Key": "nope"}).status_code == 401
    ok = client.post("/api/watch", json=_PAYLOAD, headers={"X-API-Key": "s3cret"})
    assert ok.status_code == 200
