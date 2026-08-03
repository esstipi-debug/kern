"""Tests for inventory event detection (capability M14, the pure core).

Deterministic detection over a per-SKU snapshot. The scheduler/notification
dispatch (APScheduler/Apprise) is a thin optional layer on top; detection itself
needs no external deps. Events feed the Guided Execution Layer so an alert always
arrives as an executable handoff, never a bare warning.
"""

from nosilo_core.guided import EXECUTED, HANDOFF, passed_guided

from src.alerting import InventoryEvent, alerts_outcome, detect_events


def test_low_cover_is_stockout_risk_high():
    events = detect_events([{"product_id": "A", "on_hand": 5, "reorder_point": 50, "avg_daily_demand": 10}])
    assert len(events) == 1
    assert events[0].kind == "stockout_risk"
    assert events[0].severity == "high"


def test_below_reorder_but_not_critical_is_reorder_due():
    events = detect_events([{"product_id": "B", "on_hand": 45, "reorder_point": 50, "avg_daily_demand": 1}])
    assert events[0].kind == "reorder_due"
    assert events[0].severity == "medium"


def test_high_cover_is_excess():
    events = detect_events([{"product_id": "C", "on_hand": 1000, "reorder_point": 50, "avg_daily_demand": 1}])
    assert events[0].kind == "excess"


def test_healthy_sku_raises_nothing():
    events = detect_events([{"product_id": "D", "on_hand": 100, "reorder_point": 50, "avg_daily_demand": 10}])
    assert events == []


def test_no_demand_with_stock_is_dead_stock():
    events = detect_events([{"product_id": "E", "on_hand": 20, "reorder_point": 0, "avg_daily_demand": 0}])
    assert events[0].kind == "dead_stock"


def test_no_demand_no_stock_raises_nothing():
    assert detect_events([{"product_id": "F", "on_hand": 0, "reorder_point": 0, "avg_daily_demand": 0}]) == []


def test_events_sorted_high_severity_first():
    events = detect_events([
        {"product_id": "C", "on_hand": 1000, "reorder_point": 50, "avg_daily_demand": 1},   # excess (low)
        {"product_id": "A", "on_hand": 5, "reorder_point": 50, "avg_daily_demand": 10},      # stockout (high)
    ])
    assert [e.severity for e in events] == ["high", "low"]
    assert all(isinstance(e, InventoryEvent) for e in events)


def test_empty_snapshot_returns_no_events():
    assert detect_events([]) == []


def test_alerts_outcome_with_events_is_a_protected_handoff():
    events = detect_events([{"product_id": "A", "on_hand": 5, "reorder_point": 50, "avg_daily_demand": 10}])
    outcome = alerts_outcome(events)
    assert outcome.status == HANDOFF
    assert passed_guided(outcome)


def test_alerts_outcome_with_no_events_is_executed():
    outcome = alerts_outcome([])
    assert outcome.status == EXECUTED
    assert passed_guided(outcome)
