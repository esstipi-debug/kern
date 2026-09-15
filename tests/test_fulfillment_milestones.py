"""Pure engine tests for fulfillment-chain dwell."""

from __future__ import annotations

from nosilo_core.guided import EXECUTED
from src.connectors.milestones import MilestoneEvent
from src.fulfillment_milestones import scan_chain


def _e(oid, mid, when, src="wms"):
    return MilestoneEvent(oid, mid, when, src)


def test_both_ends_with_clock_report_hours():
    events = [
        _e("A", "pack_end", "2024-04-01T19:00:00"),
        _e("A", "load_end", "2024-04-01T21:00:00"),
    ]
    scan = scan_chain(events)
    hop = next(h for h in scan.hops if h.from_id == "pack_end")
    assert hop.unit == "hours"
    assert hop.p50 == 2.0
    assert hop.n_orders == 1


def test_date_only_reports_days():
    events = [
        _e("A", "pack_end", "2024-04-01"),
        _e("A", "load_end", "2024-04-03"),
    ]
    hop = next(h for h in scan_chain(events).hops if h.from_id == "pack_end")
    assert hop.unit == "days"
    assert hop.p50 == 2.0


def test_mixed_clock_falls_back_to_days_and_sets_caveat():
    events = [
        _e("A", "pack_end", "2024-04-01"),
        _e("A", "load_end", "2024-04-01T21:00:00"),
    ]
    scan = scan_chain(events)
    hop = next(h for h in scan.hops if h.from_id == "pack_end")
    assert hop.unit == "days"
    assert "mixed" in scan.mixed_clock_caveat


def test_missing_load_end_does_not_skip_to_pickup():
    events = [
        _e("A", "pack_end", "2024-04-01T16:00:00"),
        _e("A", "carrier_pickup", "2024-04-01T19:00:00"),
    ]
    scan = scan_chain(events)
    pack_load = next(h for h in scan.hops if h.from_id == "pack_end")
    load_pu = next(h for h in scan.hops if h.from_id == "load_end")
    assert pack_load.p50 is None
    assert load_pu.p50 is None
    skipped = [h for h in scan.hops if h.from_id == "pack_end" and h.to_id == "carrier_pickup"]
    assert skipped == []


def test_pack_after_load_is_a_sequence_violation():
    events = [
        _e("A", "pack_end", "2024-04-01T21:00:00"),
        _e("A", "load_end", "2024-04-01T19:00:00"),
    ]
    scan = scan_chain(events)
    assert scan.sequence_violations
    assert scan.sequence_violations[0].order_id == "A"


def test_late_with_hole_counts_as_uninstrumented():
    events = [
        _e("A", "order_created", "2024-04-01"),
        _e("A", "arrived", "2024-04-10"),
    ]
    scan = scan_chain(events, promised={"A": "2024-04-05"})
    assert scan.uninstrumented_late == 1
    assert scan.uninstrumented_late_scored == 1


def test_late_with_complete_chain_is_not_uninstrumented():
    events = [
        _e("A", "order_created", "2024-04-01T08:00:00"),
        _e("A", "released_to_warehouse", "2024-04-01T10:00:00"),
        _e("A", "pack_end", "2024-04-01T16:00:00"),
        _e("A", "load_end", "2024-04-01T18:00:00"),
        _e("A", "carrier_pickup", "2024-04-01T19:00:00"),
        _e("A", "arrived", "2024-04-10T11:00:00"),
    ]
    scan = scan_chain(events, promised={"A": "2024-04-05"})
    assert scan.uninstrumented_late == 0
    assert scan.complete_chain_n == 1
    assert scan.measurable_hops == 5
    assert scan.outcome.status == EXECUTED


def test_without_promise_uninstrumented_late_is_none():
    events = [
        _e("A", "arrived", "2024-04-10"),
        _e("A", "order_created", "2024-04-01"),
    ]
    scan = scan_chain(events)
    assert scan.uninstrumented_late is None


def test_empty_wide_equivalent_does_not_create_zero_dwell():
    """Only pack_end present: the hop touching it is missing, not 0."""
    scan = scan_chain([_e("A", "pack_end", "2024-04-01T16:00:00")])
    pack_load = next(h for h in scan.hops if h.from_id == "pack_end")
    assert pack_load.p50 is None
    assert pack_load.n_orders == 0
    assert scan.measurable_hops == 0
    assert scan.outcome.status != EXECUTED
