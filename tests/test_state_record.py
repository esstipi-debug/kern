from __future__ import annotations

import pytest

from src.state import record
from src.state.store import StateStore
from src.state.system_state import SchemaValidationError, history


@pytest.fixture
def store(tmp_path):
    return StateStore(tmp_path / "state")


def test_record_stock_writes_a_snapshot_the_domain_accepts(store):
    rows = [
        {"product_id": "A1", "on_hand": 40.0, "reorder_point": 20.0, "avg_daily_demand": 5.0},
        {"product_id": "A2", "on_hand": 12.0, "reorder_point": 8.0, "avg_daily_demand": 2.0},
    ]
    snap = record.record_stock(store, "0", rows)
    assert snap.domain == "stock"
    assert snap.cycle_id == "0"
    assert set(snap.payload["product_id"]) == {"A1", "A2"}


def test_record_forecast_writes_product_period_forecast_qty(store):
    rows = [{"product_id": "A1", "period": "0", "forecast_qty": 5.0}]
    snap = record.record_forecast(store, "0", rows)
    assert list(snap.payload["forecast_qty"]) == [5.0]


def test_record_decision_writes_the_required_text_field_and_the_extra_order_qty(store):
    rows = [{"product_id": "A1", "tool": "inventory_optimization",
             "decision": "reponer", "period": "0", "order_qty": 30.0}]
    snap = record.record_decision(store, "0", rows)
    assert snap.payload.loc[0, "order_qty"] == 30.0
    assert snap.payload.loc[0, "decision"] == "reponer"


def test_record_outcome_writes_both_metrics_as_separate_rows(store):
    rows = [
        {"product_id": "A1", "tool": "inventory_optimization", "metric": "actual_qty",
         "value": 12.0, "period": "0"},
        {"product_id": "A1", "tool": "inventory_optimization", "metric": "sold_qty",
         "value": 9.0, "period": "0"},
    ]
    snap = record.record_outcome(store, "0", rows)
    assert set(snap.payload["metric"]) == {"actual_qty", "sold_qty"}


def test_a_missing_required_column_raises_the_domains_own_validation_error(store):
    with pytest.raises(SchemaValidationError):
        record.record_stock(store, "0", [{"product_id": "A1", "on_hand": 40.0}])


def test_sequential_integer_cycle_ids_beyond_nine_still_order_correctly(store):
    for week in range(11):
        record.record_stock(store, str(week), [
            {"product_id": "A1", "on_hand": float(week), "reorder_point": 1.0,
             "avg_daily_demand": 1.0},
        ])
    hist = history("stock", store=store)
    assert [s.cycle_id for s in hist] == [str(w) for w in range(11)]
