"""Tests for src/pricing_intel/events.py (Linchpin 3.0 PR-15).

Pure detection over two CompetitorOffer readings -- no EventLedger required
for most tests (ledger=None returns every candidate unfiltered, same
convention as scm_agent/monitors.py's own tests). A handful of tests use a
real in-memory EventLedger to prove the dedup path works identically to
every other event-emitting module in this repo.

Guarantees under test:
- a price change (previous != new) fires exactly one price_move event with
  a hand-verified delta_pct and severity (>=10% -> high, else medium);
- no price change -> no price_move;
- previous_offer=None (a pair's first-ever reading) -> [] (nothing to
  compare a "move" against yet);
- a transition InStock -> OutOfStock fires competitor_oos; OutOfStock ->
  OutOfStock (no transition) does NOT re-fire;
- a transition promo_flag False -> True fires promo_detected; True -> True
  does not re-fire;
- multiple simultaneous transitions (price move AND OOS) both fire, in one
  call;
- new_competitor_listing_event() always constructs the event (ledger=None)
  and is dedup'd through a real EventLedger like every other event type.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from scm_agent.events import EventLedger
from src.pricing_intel.events import detect_market_signal_events, new_competitor_listing_event
from src.pricing_intel.models import CompetitorOffer

NOW = datetime(2026, 7, 12, 12, 0, 0, tzinfo=timezone.utc)
EARLIER = datetime(2026, 7, 12, 8, 0, 0, tzinfo=timezone.utc)


def _offer(
    *,
    price_normalized: str = "100.00",
    availability: str = "InStock",
    promo_flag: bool = False,
    observed_at: datetime = NOW,
    site: str = "shop.example.com",
    competitor_sku_ref: str = "https://shop.example.com/p/1",
    matched_product_id: str | None = "SKU-100",
) -> CompetitorOffer:
    price = Decimal(price_normalized)
    return CompetitorOffer(
        observed_at=observed_at,
        site=site,
        competitor_sku_ref=competitor_sku_ref,
        matched_product_id=matched_product_id,
        match_confidence=1.0,
        price=price,
        currency="USD",
        price_normalized=price,
        shipping=None,
        availability=availability,
        promo_flag=promo_flag,
        list_price=None,
        acquisition_tier="L0",
        extractor="meli_api",
        extractor_version="1",
        extraction_confidence=1.0,
    )


# -- price_move -----------------------------------------------------------------


def test_price_change_fires_exactly_one_price_move_event_hand_verified_delta() -> None:
    previous = _offer(price_normalized="100.00", observed_at=EARLIER)
    current = _offer(price_normalized="92.00", observed_at=NOW)

    events = detect_market_signal_events(current, previous)

    assert len(events) == 1
    ev = events[0]
    assert ev.type == "price_move"
    assert ev.sku == "SKU-100"
    assert ev.payload["old_price_normalized"] == "100.00"
    assert ev.payload["new_price_normalized"] == "92.00"
    # (92 - 100) / 100 == -0.08
    assert Decimal(ev.payload["delta_pct"]) == Decimal("-0.08")
    assert ev.severity == "medium"  # |delta| < 0.10


def test_large_price_drop_is_high_severity() -> None:
    previous = _offer(price_normalized="100.00", observed_at=EARLIER)
    current = _offer(price_normalized="80.00", observed_at=NOW)  # -20%

    events = detect_market_signal_events(current, previous)
    assert events[0].severity == "high"


def test_unchanged_price_fires_no_price_move() -> None:
    previous = _offer(price_normalized="100.00")
    current = _offer(price_normalized="100.00")
    events = detect_market_signal_events(current, previous)
    assert events == []


def test_first_ever_reading_has_nothing_to_compare_against() -> None:
    current = _offer(price_normalized="100.00")
    events = detect_market_signal_events(current, None)
    assert events == []


# -- competitor_oos ---------------------------------------------------------------


def test_transition_into_out_of_stock_fires_competitor_oos() -> None:
    previous = _offer(availability="InStock", price_normalized="50.00", observed_at=EARLIER)
    current = _offer(availability="OutOfStock", price_normalized="50.00", observed_at=NOW)

    events = detect_market_signal_events(current, previous)
    assert len(events) == 1
    assert events[0].type == "competitor_oos"
    assert events[0].sku == "SKU-100"


def test_staying_out_of_stock_does_not_refire() -> None:
    previous = _offer(availability="OutOfStock", price_normalized="50.00")
    current = _offer(availability="OutOfStock", price_normalized="50.00")
    events = detect_market_signal_events(current, previous)
    assert events == []


def test_restock_transition_fires_no_competitor_oos() -> None:
    previous = _offer(availability="OutOfStock", price_normalized="50.00")
    current = _offer(availability="InStock", price_normalized="50.00")
    events = detect_market_signal_events(current, previous)
    assert all(e.type != "competitor_oos" for e in events)


# -- promo_detected -----------------------------------------------------------------


def test_transition_into_promo_fires_promo_detected() -> None:
    previous = _offer(promo_flag=False, price_normalized="50.00")
    current = _offer(promo_flag=True, price_normalized="50.00")
    events = detect_market_signal_events(current, previous)
    assert len(events) == 1
    assert events[0].type == "promo_detected"


def test_staying_on_promo_does_not_refire() -> None:
    previous = _offer(promo_flag=True, price_normalized="50.00")
    current = _offer(promo_flag=True, price_normalized="50.00")
    events = detect_market_signal_events(current, previous)
    assert events == []


# -- multiple simultaneous transitions -------------------------------------------


def test_price_move_and_competitor_oos_both_fire_together() -> None:
    previous = _offer(price_normalized="100.00", availability="InStock", observed_at=EARLIER)
    current = _offer(price_normalized="0.01", availability="OutOfStock", observed_at=NOW)

    events = detect_market_signal_events(current, previous)
    types = {e.type for e in events}
    assert types == {"price_move", "competitor_oos"}


# -- ledger dedup, same convention as every other event-emitting module ---------


def test_ledger_dedups_a_repeat_price_move_within_the_window(tmp_path) -> None:
    ledger = EventLedger(tmp_path / "events.sqlite3", dedup_window_seconds=3600.0)
    previous = _offer(price_normalized="100.00", observed_at=EARLIER)
    current = _offer(price_normalized="92.00", observed_at=NOW)

    first = detect_market_signal_events(current, previous, ledger=ledger)
    second = detect_market_signal_events(current, previous, ledger=ledger)

    assert len(first) == 1
    assert second == []  # deduped -- same dedup_key within the window
    ledger.close()


def test_new_competitor_listing_event_is_pure_without_a_ledger() -> None:
    ev = new_competitor_listing_event(
        site="shop.example.com", competitor_sku_ref="https://shop.example.com/p/new", matched_product_id="SKU-9",
        now=NOW,
    )
    assert ev is not None
    assert ev.type == "new_competitor_listing"
    assert ev.sku == "SKU-9"
    assert ev.payload["competitor_sku_ref"] == "https://shop.example.com/p/new"


def test_new_competitor_listing_event_dedups_through_a_real_ledger(tmp_path) -> None:
    ledger = EventLedger(tmp_path / "events.sqlite3", dedup_window_seconds=3600.0)
    kwargs = dict(site="shop.example.com", competitor_sku_ref="https://shop.example.com/p/new",
                  matched_product_id="SKU-9", now=NOW)
    first = new_competitor_listing_event(**kwargs, ledger=ledger)
    second = new_competitor_listing_event(**kwargs, ledger=ledger)
    assert first is not None
    assert second is None
    ledger.close()
