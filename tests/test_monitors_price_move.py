"""Tests for the Control Tower's competitor-price-move monitor (Discovery-
Assisted Price Intel plan, Task 7 / PR-7 -- R4 "sense" half,
``scm_agent/monitors.py::competitor_price_move_monitor`` + its
``run_all_monitors`` wiring + ``config/monitors.yaml`` entry).

Guarantees under test:
- a ``price_move`` (or ``competitor_oos``/``promo_detected``) pricing
  market-signal Event -- already emitted by
  ``src.pricing_intel.events.detect_market_signal_events`` inside
  ``jobs.price_monitor.accept_observation`` -- is PROMOTED into exactly one
  ``competitor_price_move`` Control Tower event, preserving severity/sku and
  carrying the original signal's payload plus its own type/id for
  traceability;
- dedup goes through the SAME ``EventLedger``/``_emit`` mechanism every
  other monitor in this module uses: the identical signal fed through twice
  with a real ledger only records once;
- an empty input list emits nothing;
- a NON-signal event (e.g. ``site_degraded``, a titan health event; or an
  inventory monitor's own ``rop_breach``) is NOT promoted -- this monitor
  adapts existing signals by ``event.type`` alone, it never re-derives "is
  this a move" from price/availability/promo data itself;
- ``run_all_monitors`` surfaces a competitor price move alongside inventory
  conditions when the caller passes ``price_signal_events`` in, respects the
  ``competitor_price_move`` enable/disable flag, and stays a no-op when no
  pricing events are passed (the pure "caller passes the data in"
  convention -- this module never reads a ``PriceLedger`` itself).
"""

from __future__ import annotations

from datetime import datetime, timezone

from scm_agent.events import Event, EventLedger
from scm_agent.monitors import (
    EVENT_COMPETITOR_PRICE_MOVE,
    SOURCE,
    competitor_price_move_monitor,
    load_monitor_config,
    run_all_monitors,
)
from src.state.store import StateStore

# -- fixtures -----------------------------------------------------------------

_PRICING_SOURCE = "pricing_intel.events"


def _price_signal_event(
    *,
    event_type: str = "price_move",
    sku: str | None = "SKU-X",
    site: str = "competitor.test",
    competitor_sku_ref: str = "ABC-123",
    severity: str = "medium",
    ts: datetime | None = None,
) -> Event:
    """Hand-built stand-in for what ``src.pricing_intel.events.detect_market_signal_events``
    actually emits -- same shape (type, dedup_key, payload keys), so this
    monitor is testable at the unit level without depending on PR-6's
    pricing acquisition pipeline at all (per the task brief: "mergeable
    independently of PR-6 at the unit level")."""
    return Event(
        type=event_type,
        severity=severity,
        source=_PRICING_SOURCE,
        dedup_key=f"{event_type}:{site}:{competitor_sku_ref}",
        sku=sku,
        payload={
            "site": site,
            "competitor_sku_ref": competitor_sku_ref,
            "matched_product_id": sku,
            "message": f"{sku or competitor_sku_ref} @ {site}: {event_type}",
        },
        ts=ts or datetime.now(timezone.utc),
    )


def _site_degraded_event() -> Event:
    """A real titan health event shape (``src/pricing_intel/acquire/base.py``'s
    ``CircuitBreaker.record_failure``) -- NOT a pricing market signal."""
    return Event(
        type="site_degraded",
        severity="warning",
        source="pricing_intel.acquire.base",
        dedup_key="site_degraded:competitor.test:2026-07-14T00:00:00+00:00",
        sku=None,
        payload={"domain": "competitor.test", "reason": "blocked"},
    )


def _ledger() -> EventLedger:
    return EventLedger(":memory:")


# -- competitor_price_move_monitor ---------------------------------------------


def test_promotes_price_move_event_to_control_tower():
    signal = _price_signal_event(event_type="price_move", severity="high")

    events = competitor_price_move_monitor([signal])

    assert len(events) == 1
    e = events[0]
    assert e.type == EVENT_COMPETITOR_PRICE_MOVE
    assert e.severity == "high"
    assert e.sku == "SKU-X"
    assert e.source == SOURCE
    assert e.dedup_key == "SKU-X:competitor_price_move:price_move"
    assert e.payload["site"] == "competitor.test"
    assert e.payload["competitor_sku_ref"] == "ABC-123"


def test_promotes_competitor_oos_and_promo_detected_too():
    """Not just price_move -- all three market-signal kinds the pricing
    titan emits (see module docstring / src/pricing_intel/events.py) are
    Control-Tower-worthy competitor price moves."""
    oos = _price_signal_event(event_type="competitor_oos", severity="medium")
    promo = _price_signal_event(event_type="promo_detected", severity="low", competitor_sku_ref="DEF-456")

    events = competitor_price_move_monitor([oos, promo])

    assert {e.type for e in events} == {EVENT_COMPETITOR_PRICE_MOVE}
    assert len(events) == 2


def test_dedup_collapses_repeat_move():
    signal = _price_signal_event()
    ledger = _ledger()

    first = competitor_price_move_monitor([signal], ledger=ledger)
    second = competitor_price_move_monitor([signal], ledger=ledger)

    assert len(first) == 1
    assert second == []


def test_no_signal_no_events():
    assert competitor_price_move_monitor([]) == []


def test_does_not_reimplement_detection():
    """Feeding a NON-signal event (a titan health event, not a pricing
    market signal) must NOT be promoted -- this monitor adapts existing
    signals by type, it never re-derives "is this a move" itself."""
    assert competitor_price_move_monitor([_site_degraded_event()]) == []


def test_does_not_promote_an_inventory_monitor_event():
    """An event from one of THIS module's own inventory monitors is also
    not a pricing signal and must not be promoted."""
    rop_event = Event(
        type="rop_breach", severity="medium", source=SOURCE,
        dedup_key="SKU-A:rop_breach", sku="SKU-A", payload={},
    )
    assert competitor_price_move_monitor([rop_event]) == []


def test_filters_signal_events_out_of_a_mixed_list():
    signal = _price_signal_event()
    events = competitor_price_move_monitor([_site_degraded_event(), signal])

    assert len(events) == 1
    assert events[0].sku == "SKU-X"


def test_without_a_ledger_is_not_deduped():
    """No ledger => every call returns the raw candidates, unfiltered --
    same convention as every other monitor in this module."""
    signal = _price_signal_event()

    assert len(competitor_price_move_monitor([signal])) == 1
    assert len(competitor_price_move_monitor([signal])) == 1  # still fires


def test_unmatched_product_falls_back_to_competitor_sku_ref_for_dedup():
    """matched_product_id=None is a documented, valid CompetitorOffer state
    (src/pricing_intel/models.py) -- an unmatched pair's signal must still
    dedup sanely instead of colliding with every other unmatched pair under
    one shared identifier."""
    signal = _price_signal_event(sku=None, competitor_sku_ref="UNMATCHED-1")

    events = competitor_price_move_monitor([signal])

    assert len(events) == 1
    assert events[0].sku is None
    assert events[0].dedup_key == "UNMATCHED-1:competitor_price_move:price_move"


def test_distinct_signal_kinds_for_same_sku_in_same_batch_are_all_promoted():
    """Regression test (task-7 review finding): THREE distinct signal kinds
    for the SAME SKU in the SAME observation batch -- e.g. a competitor
    closeout that is simultaneously a price_move AND a promo_detected -- must
    each surface as their OWN Control Tower event, not collapse into one.
    Prior to the fix, the dedup key ignored the source event's own ``type``
    and always deduped against the single constant EVENT_COMPETITOR_PRICE_MOVE,
    so the second and third signal kinds for the same SKU were silently
    dropped by the (real) EventLedger with no trace. Uses a REAL EventLedger
    (not ledger=None), since that's what let the collision slip through
    undetected before."""
    ledger = _ledger()
    price_move = _price_signal_event(event_type="price_move", severity="high")
    oos = _price_signal_event(event_type="competitor_oos", severity="medium")
    promo = _price_signal_event(event_type="promo_detected", severity="low")

    events = competitor_price_move_monitor([price_move, oos, promo], ledger=ledger)

    assert len(events) == 3
    assert {e.payload["signal_type"] for e in events} == {
        "price_move",
        "competitor_oos",
        "promo_detected",
    }
    assert all(e.sku == "SKU-X" for e in events)
    assert all(e.type == EVENT_COMPETITOR_PRICE_MOVE for e in events)
    # distinct dedup keys per signal kind -- but a REPEAT of the SAME kind
    # for the same SKU still collapses, per test_dedup_collapses_repeat_move.
    assert len({e.dedup_key for e in events}) == 3


# -- run_all_monitors() wiring --------------------------------------------------


def test_run_all_monitors_includes_price_move_when_enabled(tmp_path):
    store = StateStore(tmp_path / "state")
    signal = _price_signal_event()

    events = run_all_monitors(store=store, price_signal_events=[signal])

    assert {e.type for e in events} == {EVENT_COMPETITOR_PRICE_MOVE}


def test_run_all_monitors_without_price_signal_events_is_a_pure_noop(tmp_path):
    """The default (no pricing events passed) must not raise and must not
    fabricate any competitor_price_move event -- this module never reads a
    PriceLedger itself (the "caller passes the data in" convention)."""
    store = StateStore(tmp_path / "state")

    assert run_all_monitors(store=store) == []


def test_run_all_monitors_skips_a_disabled_competitor_price_move_monitor(tmp_path):
    store = StateStore(tmp_path / "state")
    signal = _price_signal_event()
    config = load_monitor_config()
    config = {**config, "competitor_price_move": {**config["competitor_price_move"], "enabled": False}}

    events = run_all_monitors(config=config, store=store, price_signal_events=[signal])

    assert events == []


def test_run_all_monitors_dedups_price_move_across_two_cycles(tmp_path):
    store = StateStore(tmp_path / "state")
    signal = _price_signal_event()
    ledger = _ledger()

    first = run_all_monitors(store=store, ledger=ledger, price_signal_events=[signal])
    second = run_all_monitors(store=store, ledger=ledger, price_signal_events=[signal])

    assert len(first) == 1
    assert second == []
