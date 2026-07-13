"""Tests for Control Tower monitors -- A1 "sense" layer (Linchpin 3.0 PR-5,
scm_agent/monitors.py + config/monitors.yaml).

Guarantees under test (plan S5's A1 row):
- rop_breach_monitor / stockout_projected_monitor split
  src.alerting.detect_events's "reorder_due"/"stockout_risk" kinds into two
  distinct, mutually exclusive event types, with hand-verified cover-days
  numbers on both sides of the critical floor;
- excess_growing_monitor is a real 2-snapshot TREND check -- excess alone
  (flat/shrinking cover) does not fire, only excess AND growing does, with
  hand-verified days-of-cover numbers and a severity boundary;
- forecast_error_out_of_band_monitor's documented forecast/outcomes
  convention, with hand-verified relative-error numbers at the medium/high
  severity boundary and inside the band;
- lead_time_drift_monitor's documented outcomes convention, with
  hand-verified drift-ratio numbers at the medium/high boundary and the
  shrinking-lead-time low-severity case;
- every monitor dedups via EventLedger: the same condition emitted twice
  with the same ledger only records once;
- load_monitor_config() parses the real config/monitors.yaml and rejects a
  malformed one;
- run_all_monitors() runs a full A1 cycle directly off src.state, degrades
  gracefully with no state at all, respects a disabled monitor, and a second
  full cycle over unchanged state emits nothing.
"""

from __future__ import annotations

import pandas as pd
import pytest

from scm_agent.events import EventLedger
from scm_agent.monitors import (
    DEFAULT_MONITORS_CONFIG_PATH,
    EVENT_EXCESS_GROWING,
    EVENT_FORECAST_ERROR_OUT_OF_BAND,
    EVENT_LEAD_TIME_DRIFT,
    EVENT_ROP_BREACH,
    EVENT_STOCKOUT_PROJECTED,
    MonitorConfigError,
    excess_growing_monitor,
    forecast_error_out_of_band_monitor,
    lead_time_drift_monitor,
    load_monitor_config,
    rop_breach_monitor,
    run_all_monitors,
    stockout_projected_monitor,
)
from src.state.store import StateStore
from src.state.system_state import history, latest, snapshot


def _store(tmp_path) -> StateStore:
    return StateStore(tmp_path / "state")


def _ledger() -> EventLedger:
    return EventLedger(":memory:")


def _stock_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _stock_row(*, product_id="SKU-A", on_hand, reorder_point=20.0, avg_daily_demand) -> dict:
    return {
        "product_id": product_id, "on_hand": on_hand,
        "reorder_point": reorder_point, "avg_daily_demand": avg_daily_demand,
    }


# -- (a) rop_breach_monitor ----------------------------------------------------


def test_rop_breach_monitor_fires_on_reorder_due(tmp_path):
    """on_hand=15 <= rop=20; cover = 15/1 = 15.0 days >= 7.0 -> reorder_due."""
    store = _store(tmp_path)
    snapshot("stock", _stock_df([_stock_row(on_hand=15.0, avg_daily_demand=1.0)]), "1", store=store)

    events = rop_breach_monitor(latest("stock", store=store))

    assert len(events) == 1
    e = events[0]
    assert e.type == EVENT_ROP_BREACH
    assert e.severity == "medium"
    assert e.sku == "SKU-A"
    assert e.source == "monitors"
    assert e.dedup_key == "SKU-A:rop_breach"
    assert e.payload["rows"] == [_stock_row(on_hand=15.0, avg_daily_demand=1.0)]


def test_rop_breach_monitor_does_not_fire_on_a_stockout_risk_sku(tmp_path):
    """on_hand=10 <= rop=20; cover = 10/5 = 2.0 days < 7.0 -> stockout_risk,
    not reorder_due -- rop_breach_monitor must NOT also fire (mutually
    exclusive, matching detect_events' "at most one state event per SKU")."""
    store = _store(tmp_path)
    snapshot("stock", _stock_df([_stock_row(on_hand=10.0, avg_daily_demand=5.0)]), "1", store=store)

    assert rop_breach_monitor(latest("stock", store=store)) == []


def test_rop_breach_monitor_does_not_fire_above_the_reorder_point(tmp_path):
    store = _store(tmp_path)
    snapshot("stock", _stock_df([_stock_row(on_hand=50.0, avg_daily_demand=1.0)]), "1", store=store)

    assert rop_breach_monitor(latest("stock", store=store)) == []


def test_rop_breach_monitor_dedups_across_two_identical_runs(tmp_path):
    store = _store(tmp_path)
    snapshot("stock", _stock_df([_stock_row(on_hand=15.0, avg_daily_demand=1.0)]), "1", store=store)
    stock = latest("stock", store=store)
    ledger = _ledger()

    first = rop_breach_monitor(stock, ledger=ledger)
    second = rop_breach_monitor(stock, ledger=ledger)

    assert len(first) == 1
    assert second == []


def test_rop_breach_monitor_without_a_ledger_is_not_deduped(tmp_path):
    """No ledger => every call returns the raw candidates, unfiltered -- the
    detection logic is testable in isolation from dedup storage."""
    store = _store(tmp_path)
    snapshot("stock", _stock_df([_stock_row(on_hand=15.0, avg_daily_demand=1.0)]), "1", store=store)
    stock = latest("stock", store=store)

    assert len(rop_breach_monitor(stock)) == 1
    assert len(rop_breach_monitor(stock)) == 1  # still fires -- nothing recorded that time


# -- (d) stockout_projected_monitor --------------------------------------------


def test_stockout_projected_monitor_fires_on_stockout_risk(tmp_path):
    """on_hand=10 <= rop=20; cover = 10/5 = 2.0 days < 7.0 -> stockout_risk."""
    store = _store(tmp_path)
    snapshot("stock", _stock_df([_stock_row(on_hand=10.0, avg_daily_demand=5.0)]), "1", store=store)

    events = stockout_projected_monitor(latest("stock", store=store))

    assert len(events) == 1
    e = events[0]
    assert e.type == EVENT_STOCKOUT_PROJECTED
    assert e.severity == "high"
    assert e.dedup_key == "SKU-A:stockout_projected"


def test_stockout_projected_monitor_does_not_fire_above_the_critical_floor(tmp_path):
    """on_hand=15 <= rop=20; cover = 15/1 = 15.0 days >= 7.0 -> reorder_due,
    not stockout_risk."""
    store = _store(tmp_path)
    snapshot("stock", _stock_df([_stock_row(on_hand=15.0, avg_daily_demand=1.0)]), "1", store=store)

    assert stockout_projected_monitor(latest("stock", store=store)) == []


def test_stockout_projected_monitor_dedups_across_two_identical_runs(tmp_path):
    store = _store(tmp_path)
    snapshot("stock", _stock_df([_stock_row(on_hand=10.0, avg_daily_demand=5.0)]), "1", store=store)
    stock = latest("stock", store=store)
    ledger = _ledger()

    assert len(stockout_projected_monitor(stock, ledger=ledger)) == 1
    assert stockout_projected_monitor(stock, ledger=ledger) == []


# -- (e) excess_growing_monitor -------------------------------------------------


def test_excess_growing_monitor_fires_when_cover_grows_past_the_ceiling(tmp_path):
    """prev: on_hand=200, adt=2 -> cover=100.0d. curr: on_hand=260, adt=2 ->
    cover=130.0d. 130.0 > 90.0 (excess) and grew by 30.0 >= 1.0 -> fires."""
    store = _store(tmp_path)
    snapshot("stock", _stock_df([_stock_row(on_hand=200.0, reorder_point=10.0, avg_daily_demand=2.0)]), "1", store=store)
    snapshot("stock", _stock_df([_stock_row(on_hand=260.0, reorder_point=10.0, avg_daily_demand=2.0)]), "2", store=store)

    events = excess_growing_monitor(history("stock", store=store))

    assert len(events) == 1
    e = events[0]
    assert e.type == EVENT_EXCESS_GROWING
    assert e.payload["days_of_cover_prev"] == pytest.approx(100.0)
    assert e.payload["days_of_cover_curr"] == pytest.approx(130.0)
    assert e.payload["growth_days"] == pytest.approx(30.0)
    assert e.severity == "medium"  # growth 30.0 >= 2 * min_growth_days (2.0)


def test_excess_growing_monitor_severity_is_low_just_above_the_growth_floor(tmp_path):
    """growth = 1.0 (== min_growth_days) fires, but below 2 * min_growth_days
    (2.0) -> low severity, not medium."""
    store = _store(tmp_path)
    snapshot("stock", _stock_df([_stock_row(on_hand=200.0, reorder_point=10.0, avg_daily_demand=2.0)]), "1", store=store)  # cover=100
    snapshot("stock", _stock_df([_stock_row(on_hand=202.0, reorder_point=10.0, avg_daily_demand=2.0)]), "2", store=store)  # cover=101

    events = excess_growing_monitor(history("stock", store=store))

    assert events[0].payload["growth_days"] == pytest.approx(1.0)
    assert events[0].severity == "low"


def test_excess_growing_monitor_does_not_fire_when_cover_is_flat(tmp_path):
    """Excess but NOT growing -- alerting.py's static "excess" territory, not
    this trend monitor's."""
    store = _store(tmp_path)
    snapshot("stock", _stock_df([_stock_row(on_hand=200.0, reorder_point=10.0, avg_daily_demand=2.0)]), "1", store=store)
    snapshot("stock", _stock_df([_stock_row(on_hand=200.0, reorder_point=10.0, avg_daily_demand=2.0)]), "2", store=store)

    assert excess_growing_monitor(history("stock", store=store)) == []


def test_excess_growing_monitor_does_not_fire_below_the_excess_ceiling(tmp_path):
    store = _store(tmp_path)
    snapshot("stock", _stock_df([_stock_row(on_hand=10.0, reorder_point=5.0, avg_daily_demand=2.0)]), "1", store=store)  # cover=5
    snapshot("stock", _stock_df([_stock_row(on_hand=20.0, reorder_point=5.0, avg_daily_demand=2.0)]), "2", store=store)  # cover=10, growing but not excess

    assert excess_growing_monitor(history("stock", store=store)) == []


def test_excess_growing_monitor_needs_at_least_two_snapshots(tmp_path):
    store = _store(tmp_path)
    snapshot("stock", _stock_df([_stock_row(on_hand=200.0, reorder_point=10.0, avg_daily_demand=2.0)]), "1", store=store)

    assert excess_growing_monitor(history("stock", store=store)) == []
    assert excess_growing_monitor([]) == []


def test_excess_growing_monitor_dedups_across_two_identical_cycles(tmp_path):
    store = _store(tmp_path)
    snapshot("stock", _stock_df([_stock_row(on_hand=200.0, reorder_point=10.0, avg_daily_demand=2.0)]), "1", store=store)
    snapshot("stock", _stock_df([_stock_row(on_hand=260.0, reorder_point=10.0, avg_daily_demand=2.0)]), "2", store=store)
    hist = history("stock", store=store)
    ledger = _ledger()

    assert len(excess_growing_monitor(hist, ledger=ledger)) == 1
    assert excess_growing_monitor(hist, ledger=ledger) == []


# -- (b) forecast_error_out_of_band_monitor ------------------------------------


def _forecast_snap(store, cycle_id, forecast_qty):
    snapshot("forecast", pd.DataFrame([{"product_id": "SKU-A", "period": "2026-W28", "forecast_qty": forecast_qty}]), cycle_id, store=store)
    return latest("forecast", store=store)


def _outcomes_actual_snap(store, cycle_id, actual_qty):
    snapshot("outcomes", pd.DataFrame([{"product_id": "SKU-A", "tool": "demand_actuals", "metric": "actual_qty", "value": actual_qty}]), cycle_id, store=store)
    return latest("outcomes", store=store)


def test_forecast_error_out_of_band_monitor_fires_medium_at_50pct_error(tmp_path):
    """forecast=100, actual=150 -> error=+50%; |0.50| > 0.30 but <= 0.60 -> medium."""
    store = _store(tmp_path)
    forecast_snap = _forecast_snap(store, "1", 100.0)
    outcomes_snap = _outcomes_actual_snap(store, "1", 150.0)

    events = forecast_error_out_of_band_monitor(forecast_snap, outcomes_snap)

    assert len(events) == 1
    e = events[0]
    assert e.type == EVENT_FORECAST_ERROR_OUT_OF_BAND
    assert e.payload["relative_error"] == pytest.approx(0.5)
    assert e.severity == "medium"
    assert e.dedup_key == "SKU-A:forecast_error_out_of_band"


def test_forecast_error_out_of_band_monitor_fires_high_at_110pct_error(tmp_path):
    """forecast=100, actual=210 -> error=+110%; > 0.60 -> high."""
    store = _store(tmp_path)
    forecast_snap = _forecast_snap(store, "1", 100.0)
    outcomes_snap = _outcomes_actual_snap(store, "1", 210.0)

    events = forecast_error_out_of_band_monitor(forecast_snap, outcomes_snap)

    assert events[0].severity == "high"


def test_forecast_error_out_of_band_monitor_does_not_fire_within_the_band(tmp_path):
    """forecast=100, actual=120 -> error=+20% <= 0.30 -> no event."""
    store = _store(tmp_path)
    forecast_snap = _forecast_snap(store, "1", 100.0)
    outcomes_snap = _outcomes_actual_snap(store, "1", 120.0)

    assert forecast_error_out_of_band_monitor(forecast_snap, outcomes_snap) == []


def test_forecast_error_out_of_band_monitor_skips_a_product_with_no_actual(tmp_path):
    store = _store(tmp_path)
    forecast_snap = _forecast_snap(store, "1", 100.0)
    snapshot("outcomes", pd.DataFrame([{"product_id": "SKU-B", "tool": "demand_actuals", "metric": "actual_qty", "value": 999.0}]), "1", store=store)
    outcomes_snap = latest("outcomes", store=store)

    assert forecast_error_out_of_band_monitor(forecast_snap, outcomes_snap) == []


def test_forecast_error_out_of_band_monitor_dedups_across_two_identical_runs(tmp_path):
    store = _store(tmp_path)
    forecast_snap = _forecast_snap(store, "1", 100.0)
    outcomes_snap = _outcomes_actual_snap(store, "1", 150.0)
    ledger = _ledger()

    assert len(forecast_error_out_of_band_monitor(forecast_snap, outcomes_snap, ledger=ledger)) == 1
    assert forecast_error_out_of_band_monitor(forecast_snap, outcomes_snap, ledger=ledger) == []


# -- (c) lead_time_drift_monitor ------------------------------------------------


def _lead_time_snap(store, cycle_id, lead_time_days):
    snapshot("outcomes", pd.DataFrame([{"product_id": "SKU-A", "tool": "po_receipt", "metric": "lead_time_days", "value": lead_time_days}]), cycle_id, store=store)


def test_lead_time_drift_monitor_fires_medium_at_30pct_growth(tmp_path):
    """baseline=10.0d, recent=13.0d -> drift=+30%; > 0.25 but <= 0.50 -> medium."""
    store = _store(tmp_path)
    _lead_time_snap(store, "1", 10.0)
    _lead_time_snap(store, "2", 13.0)

    events = lead_time_drift_monitor(history("outcomes", store=store))

    assert len(events) == 1
    e = events[0]
    assert e.type == EVENT_LEAD_TIME_DRIFT
    assert e.payload["drift_ratio"] == pytest.approx(0.3)
    assert e.severity == "medium"
    assert e.dedup_key == "SKU-A:lead_time_drift"


def test_lead_time_drift_monitor_fires_high_at_60pct_growth(tmp_path):
    """baseline=10.0d, recent=16.0d -> drift=+60% -> high."""
    store = _store(tmp_path)
    _lead_time_snap(store, "1", 10.0)
    _lead_time_snap(store, "2", 16.0)

    events = lead_time_drift_monitor(history("outcomes", store=store))

    assert events[0].severity == "high"


def test_lead_time_drift_monitor_fires_low_severity_when_lead_time_shrinks(tmp_path):
    """baseline=10.0d, recent=7.0d -> drift=-30% -- past the 0.25 threshold, so
    it still fires (worth noting), but always low severity, never
    medium/high, when shrinking."""
    store = _store(tmp_path)
    _lead_time_snap(store, "1", 10.0)
    _lead_time_snap(store, "2", 7.0)

    events = lead_time_drift_monitor(history("outcomes", store=store))

    assert len(events) == 1
    assert events[0].payload["drift_ratio"] == pytest.approx(-0.3)
    assert events[0].severity == "low"


def test_lead_time_drift_monitor_does_not_fire_within_the_threshold(tmp_path):
    store = _store(tmp_path)
    _lead_time_snap(store, "1", 10.0)
    _lead_time_snap(store, "2", 11.0)  # +10%, <= 0.25

    assert lead_time_drift_monitor(history("outcomes", store=store)) == []


def test_lead_time_drift_monitor_needs_at_least_two_snapshots(tmp_path):
    store = _store(tmp_path)
    _lead_time_snap(store, "1", 10.0)

    assert lead_time_drift_monitor(history("outcomes", store=store)) == []


def test_lead_time_drift_monitor_dedups_across_two_identical_runs(tmp_path):
    store = _store(tmp_path)
    _lead_time_snap(store, "1", 10.0)
    _lead_time_snap(store, "2", 13.0)
    hist = history("outcomes", store=store)
    ledger = _ledger()

    assert len(lead_time_drift_monitor(hist, ledger=ledger)) == 1
    assert lead_time_drift_monitor(hist, ledger=ledger) == []


# -- load_monitor_config() ------------------------------------------------------


def test_load_monitor_config_parses_the_real_config_file():
    config = load_monitor_config(DEFAULT_MONITORS_CONFIG_PATH)

    assert set(config) == {
        "rop_breach", "stockout_projected", "excess_growing",
        "forecast_error_out_of_band", "lead_time_drift",
    }
    assert config["rop_breach"]["critical_cover_days"] == 7.0
    assert config["excess_growing"]["excess_cover_days"] == 90.0
    for spec in config.values():
        assert spec["enabled"] is True
        assert spec["cadence_minutes"] > 0


def test_load_monitor_config_rejects_missing_monitors_key(tmp_path):
    path = tmp_path / "monitors.yaml"
    path.write_text("version: 1\n", encoding="utf-8")

    with pytest.raises(MonitorConfigError, match="monitors"):
        load_monitor_config(path)


def test_load_monitor_config_rejects_a_monitor_entry_that_is_not_a_mapping(tmp_path):
    path = tmp_path / "monitors.yaml"
    path.write_text("monitors:\n  rop_breach: just_a_string\n", encoding="utf-8")

    with pytest.raises(MonitorConfigError, match="mapping"):
        load_monitor_config(path)


def test_load_monitor_config_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_monitor_config(tmp_path / "does_not_exist.yaml")


# -- run_all_monitors(): one full A1 cycle directly off src.state --------------


def test_run_all_monitors_with_no_state_returns_no_events_without_raising(tmp_path):
    store = _store(tmp_path)
    assert run_all_monitors(store=store) == []


def test_run_all_monitors_runs_the_stock_monitors_against_real_state(tmp_path):
    store = _store(tmp_path)
    snapshot("stock", _stock_df([_stock_row(on_hand=10.0, avg_daily_demand=5.0)]), "1", store=store)  # stockout_risk

    events = run_all_monitors(store=store)

    assert {e.type for e in events} == {EVENT_STOCKOUT_PROJECTED}


def test_run_all_monitors_second_cycle_over_unchanged_state_emits_nothing(tmp_path):
    store = _store(tmp_path)
    snapshot("stock", _stock_df([_stock_row(on_hand=10.0, avg_daily_demand=5.0)]), "1", store=store)
    ledger = _ledger()

    first = run_all_monitors(store=store, ledger=ledger)
    second = run_all_monitors(store=store, ledger=ledger)

    assert len(first) == 1
    assert second == []


def test_run_all_monitors_skips_a_disabled_monitor(tmp_path):
    store = _store(tmp_path)
    snapshot("stock", _stock_df([_stock_row(on_hand=10.0, avg_daily_demand=5.0)]), "1", store=store)

    config = load_monitor_config(DEFAULT_MONITORS_CONFIG_PATH)
    config = {**config, "stockout_projected": {**config["stockout_projected"], "enabled": False}}

    assert run_all_monitors(config=config, store=store) == []


def test_run_all_monitors_runs_the_forecast_error_monitor_too(tmp_path):
    store = _store(tmp_path)
    _forecast_snap(store, "1", 100.0)
    snapshot("outcomes", pd.DataFrame([
        {"product_id": "SKU-A", "tool": "demand_actuals", "metric": "actual_qty", "value": 150.0},
    ]), "1", store=store)

    events = run_all_monitors(store=store)

    assert {e.type for e in events} == {EVENT_FORECAST_ERROR_OUT_OF_BAND}


def test_run_all_monitors_runs_the_lead_time_drift_monitor_too(tmp_path):
    store = _store(tmp_path)
    _lead_time_snap(store, "1", 10.0)
    _lead_time_snap(store, "2", 13.0)

    events = run_all_monitors(store=store)

    assert {e.type for e in events} == {EVENT_LEAD_TIME_DRIFT}
