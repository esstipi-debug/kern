"""Tests for jobs/seo_priority.py (Linchpin 3.0 PR-21, S4 inventory-aware SEO priority).

Two layers:
  1. Pure `run()`/`verify()` tests on a small HAND-CONSTRUCTED portfolio (ABC-XYZ +
     E&O + forecast dataclasses built directly, numbers verified by hand below) --
     the primary coverage, per the repo's reference-number testing culture.
  2. A CSV round-trip test for `prepare()` wiring (real files -> the same joined
     report), including the required `params['stock_path']` validation.
"""

from __future__ import annotations

import pandas as pd
import pytest

from jobs import seo_priority as sp
from jobs.forecast_job import SkuForecast
from src.classification import SkuClassification
from src.excess_obsolete import SkuStock


def _classification(**overrides) -> SkuClassification:
    defaults = dict(
        product_id="X", annual_value=1000.0, cumulative_share=0.5, abc="B",
        mean_demand=10.0, cv=0.4, xyz="X", cell="BX", service_level=0.95,
        policy="(s, Q)", buffer_distribution="normal",
    )
    defaults.update(overrides)
    return SkuClassification(**defaults)


def _stock(**overrides) -> SkuStock:
    defaults = dict(product_id="X", on_hand=100.0, daily_demand=5.0, unit_cost=2.0,
                     days_since_last_sale=1.0)
    defaults.update(overrides)
    return SkuStock(**defaults)


def _forecast(**overrides) -> SkuForecast:
    defaults = dict(
        name="X", quadrant="smooth", adi=1.0, cv2=0.1, method="ses", forecast=10.0,
        mae=0.0, mape=0.0, mase=0.5, fva=0.5, beats_naive=True, n_periods=12,
    )
    defaults.update(overrides)
    return SkuForecast(**defaults)


# ---- hand-constructed portfolio ------------------------------------------------
# PUSH1: A-class, mean_demand=10 -> E&O cover = 200/10 = 20 <= 90 -> healthy;
#        forecast=15 -> pct_change = (15-10)/10 = +50% >= 10% threshold -> up.
#        => push (A + healthy + up).
# CUT1:  daily_demand=1 (>0, so not the zero-demand trigger), days_since_last_sale=250
#        >= dead_threshold_days=180 -> dead (time trigger). => cut regardless of ABC/trend.
# HOLD_EXCESS: on_hand=1000, daily_demand=5 -> cover = 200 > 90 -> excess (not dead,
#        not healthy) -> hold even though the forecast trend reads "up" (10 vs
#        mean_demand 5 -> +100%) -- deliberately picked "up" to prove an excess (not
#        healthy) E&O status blocks a push regardless of ABC class or trend.
# HOLD_NOFORECAST: A-class, healthy (cover = 100/8 = 12.5 <= 90), but missing from the
#        forecast input -> trend=insufficient_signal -> hold (push needs an up trend).
# MISSING_EO: only in classifications -> excluded.
# MISSING_ABC: only in stocks -> excluded.

def _portfolio_payload() -> dict:
    classifications = [
        _classification(product_id="PUSH1", abc="A", mean_demand=10.0, cumulative_share=0.3,
                         annual_value=20000.0, xyz="X", cell="AX", service_level=0.98,
                         policy="(R, S)"),
        _classification(product_id="CUT1", abc="C", mean_demand=1.0, cumulative_share=0.99,
                         annual_value=50.0, xyz="Z", cell="CZ", service_level=0.90,
                         policy="make-to-order / review for discontinuation",
                         buffer_distribution="gamma"),
        _classification(product_id="HOLD_EXCESS", abc="B", mean_demand=5.0, cumulative_share=0.85,
                         annual_value=2000.0, xyz="Y", cell="BY", service_level=0.95),
        _classification(product_id="HOLD_NOFORECAST", abc="A", mean_demand=8.0, cumulative_share=0.4,
                         annual_value=8000.0, xyz="X", cell="AX", service_level=0.98,
                         policy="(R, S)"),
        _classification(product_id="MISSING_EO", abc="C", mean_demand=1.0, cumulative_share=0.95,
                         annual_value=100.0, xyz="X", cell="CX", service_level=0.90),
    ]
    stocks = [
        _stock(product_id="PUSH1", on_hand=200.0, daily_demand=10.0, unit_cost=5.0,
               days_since_last_sale=2.0),
        _stock(product_id="CUT1", on_hand=40.0, daily_demand=1.0, unit_cost=2.0,
               days_since_last_sale=250.0),
        _stock(product_id="HOLD_EXCESS", on_hand=1000.0, daily_demand=5.0, unit_cost=3.0,
               days_since_last_sale=10.0),
        _stock(product_id="HOLD_NOFORECAST", on_hand=100.0, daily_demand=8.0, unit_cost=4.0,
               days_since_last_sale=1.0),
        _stock(product_id="MISSING_ABC", on_hand=10.0, daily_demand=1.0, unit_cost=1.0,
               days_since_last_sale=1.0),
    ]
    forecasts = [
        _forecast(name="PUSH1", forecast=15.0, n_periods=12),
        _forecast(name="CUT1", forecast=0.5, n_periods=12),
        _forecast(name="HOLD_EXCESS", forecast=10.0, n_periods=12),
        # HOLD_NOFORECAST deliberately has no matching forecast record.
    ]
    return {"classifications": classifications, "stocks": stocks, "forecasts": forecasts}


def test_push_cut_hold_assignment_by_hand():
    report = sp.run(_portfolio_payload())
    by_id = {a.product_id: a for a in report.actions}

    assert set(by_id) == {"PUSH1", "CUT1", "HOLD_EXCESS", "HOLD_NOFORECAST"}

    push = by_id["PUSH1"]
    assert push.action == sp.PUSH
    assert push.trend == sp.TREND_UP
    assert push.eo_classification == "healthy"
    assert not push.requires_human_signoff
    assert "ABC class=A" in push.reason and "E&O status=healthy" in push.reason

    cut = by_id["CUT1"]
    assert cut.action == sp.CUT
    assert cut.eo_classification == "dead"
    assert cut.requires_human_signoff
    assert "days_since_last_sale=250" in cut.reason
    assert "dead_threshold_days=180" in cut.reason
    assert "RECOMMENDATION" in cut.reason and "human sign-off" in cut.reason
    assert "no sale in 250+ days" in cut.reason  # time trigger, not zero-demand

    hold_excess = by_id["HOLD_EXCESS"]
    assert hold_excess.action == sp.HOLD
    assert hold_excess.eo_classification == "excess"  # never pushed even though B-class + up trend

    hold_noforecast = by_id["HOLD_NOFORECAST"]
    assert hold_noforecast.action == sp.HOLD
    assert hold_noforecast.trend == sp.TREND_INSUFFICIENT
    assert hold_noforecast.eo_classification == "healthy"  # A + healthy but no trend -> still hold

    assert report.n_push == 1 and report.n_cut == 1 and report.n_hold == 2


def test_missing_from_either_input_is_excluded_not_dropped():
    report = sp.run(_portfolio_payload())
    excluded_by_id = {e.product_id: e for e in report.excluded}

    assert set(excluded_by_id) == {"MISSING_EO", "MISSING_ABC"}
    assert "missing from the E&O" in excluded_by_id["MISSING_EO"].reason
    assert "missing from ABC-XYZ" in excluded_by_id["MISSING_ABC"].reason
    assert report.n_excluded == 2
    assert "MISSING_EO" not in {a.product_id for a in report.actions}
    assert "MISSING_ABC" not in {a.product_id for a in report.actions}


def test_verify_passes_on_the_hand_built_portfolio():
    report = sp.run(_portfolio_payload())
    assert sp.verify(report) == []
    assert sp.seo_priority_passed(report) is True


def test_zero_demand_dead_trigger_is_cited_distinctly():
    payload = {
        "classifications": [_classification(product_id="Z1", abc="A", mean_demand=5.0)],
        "stocks": [_stock(product_id="Z1", daily_demand=0.0, on_hand=30.0, days_since_last_sale=5.0)],
        "forecasts": [],
    }
    report = sp.run(payload)
    line = report.actions[0]
    assert line.action == sp.CUT
    assert "zero/negative demand" in line.reason
    assert "days_since_last_sale=5" in line.reason  # still cited even though not the trigger


# ---- forecast trend: never a fabricated up/down --------------------------------

def test_trend_up_and_down_thresholds():
    up, _ = sp._forecast_trend(_forecast(forecast=12.0), 10.0, min_periods=4, trend_threshold_pct=0.10)
    down, _ = sp._forecast_trend(_forecast(forecast=8.0), 10.0, min_periods=4, trend_threshold_pct=0.10)
    flat, _ = sp._forecast_trend(_forecast(forecast=10.5), 10.0, min_periods=4, trend_threshold_pct=0.10)
    assert up == sp.TREND_UP
    assert down == sp.TREND_DOWN
    assert flat == sp.TREND_FLAT


def test_trend_insufficient_signal_cases():
    missing, reason = sp._forecast_trend(None, 10.0, min_periods=4, trend_threshold_pct=0.10)
    short, _ = sp._forecast_trend(_forecast(n_periods=2), 10.0, min_periods=4, trend_threshold_pct=0.10)
    zero_mean, _ = sp._forecast_trend(_forecast(), 0.0, min_periods=4, trend_threshold_pct=0.10)
    non_finite, _ = sp._forecast_trend(
        _forecast(forecast=float("nan")), 10.0, min_periods=4, trend_threshold_pct=0.10
    )
    assert missing == sp.TREND_INSUFFICIENT and "no forecast tool output" in reason
    assert short == sp.TREND_INSUFFICIENT
    assert zero_mean == sp.TREND_INSUFFICIENT
    assert non_finite == sp.TREND_INSUFFICIENT


# ---- verify() catches a fabricated / malformed report ---------------------------

def test_verify_flags_a_cut_without_a_citable_reason():
    bad = sp.SeoPriorityReport(
        actions=(sp.SkuSeoAction(
            product_id="BAD", action=sp.CUT, abc="C", xyz="Z", eo_classification="dead",
            trend=sp.TREND_INSUFFICIENT, reason="deindex it", requires_human_signoff=True,
        ),),
        excluded=(), n_push=0, n_cut=1, n_hold=0, n_excluded=0,
        dead_threshold_days=180.0, trend_threshold_pct=0.10, summary="",
    )
    issues = sp.verify(bad)
    assert any("citable E&O reason" in i for i in issues)


def test_verify_flags_push_without_up_trend():
    bad = sp.SeoPriorityReport(
        actions=(sp.SkuSeoAction(
            product_id="BAD", action=sp.PUSH, abc="A", xyz="X", eo_classification="healthy",
            trend=sp.TREND_FLAT, reason="looks good", requires_human_signoff=False,
        ),),
        excluded=(), n_push=1, n_cut=0, n_hold=0, n_excluded=0,
        dead_threshold_days=180.0, trend_threshold_pct=0.10, summary="",
    )
    issues = sp.verify(bad)
    assert any("without an up trend" in i for i in issues)


def test_verify_flags_cut_without_human_signoff_flag():
    bad = sp.SeoPriorityReport(
        actions=(sp.SkuSeoAction(
            product_id="BAD", action=sp.CUT, abc="C", xyz="Z", eo_classification="dead",
            trend=sp.TREND_INSUFFICIENT,
            reason="E&O status=dead (days_since_last_sale=200, dead_threshold_days=180).",
            requires_human_signoff=False,
        ),),
        excluded=(), n_push=0, n_cut=1, n_hold=0, n_excluded=0,
        dead_threshold_days=180.0, trend_threshold_pct=0.10, summary="",
    )
    issues = sp.verify(bad)
    assert any("must require human sign-off" in i for i in issues)


def test_verify_flags_unreported_exclusion():
    bad = sp.SeoPriorityReport(
        actions=(), excluded=(sp.ExcludedSku(product_id="Q", reason=""),),
        n_push=0, n_cut=0, n_hold=0, n_excluded=1,
        dead_threshold_days=180.0, trend_threshold_pct=0.10, summary="",
    )
    issues = sp.verify(bad)
    assert any("excluded without a reason" in i for i in issues)


# ---- write_operational -----------------------------------------------------------

def test_write_operational_emits_action_and_excluded_csvs(tmp_path):
    report = sp.run(_portfolio_payload())
    out = sp.write_operational(report, tmp_path)
    action_df = pd.read_csv(out["csv"])
    excluded_df = pd.read_csv(out["excluded_csv"])
    assert set(action_df["product_id"]) == {"PUSH1", "CUT1", "HOLD_EXCESS", "HOLD_NOFORECAST"}
    assert set(excluded_df["product_id"]) == {"MISSING_EO", "MISSING_ABC"}
    assert list(action_df.columns) == list(sp._ACTION_CSV_COLUMNS)


def test_write_operational_on_empty_report_writes_header_only(tmp_path):
    empty = sp.SeoPriorityReport(
        actions=(), excluded=(), n_push=0, n_cut=0, n_hold=0, n_excluded=0,
        dead_threshold_days=180.0, trend_threshold_pct=0.10, summary="",
    )
    out = sp.write_operational(empty, tmp_path)
    action_df = pd.read_csv(out["csv"])
    assert list(action_df.columns) == list(sp._ACTION_CSV_COLUMNS)
    assert len(action_df) == 0


# ---- prepare(): CSV wiring + params['stock_path'] validation --------------------

def _write_demand_csv(path) -> str:
    rows = []
    for pid, qty, cost in (("A", 20.0, 5.0), ("B", 2.0, 1.0)):
        for period in range(6):
            rows.append({"product_id": pid, "period": period, "quantity": qty, "unit_cost": cost})
    pd.DataFrame(rows).to_csv(path, index=False)
    return str(path)


def _write_stock_csv(path) -> str:
    pd.DataFrame([
        {"product_id": "A", "on_hand": 40.0, "daily_demand": 20.0, "unit_cost": 5.0,
         "days_since_last_sale": 1.0},
        {"product_id": "B", "on_hand": 500.0, "daily_demand": 0.0, "unit_cost": 1.0,
         "days_since_last_sale": 300.0},
    ]).to_csv(path, index=False)
    return str(path)


def test_prepare_requires_stock_path(tmp_path):
    demand = _write_demand_csv(tmp_path / "demand.csv")
    with pytest.raises(ValueError, match="stock_path"):
        sp.prepare(demand, {})


def test_prepare_and_run_end_to_end(tmp_path):
    demand = _write_demand_csv(tmp_path / "demand.csv")
    stock = _write_stock_csv(tmp_path / "stock.csv")
    payload = sp.prepare(demand, {"stock_path": stock})
    report = sp.run(payload)

    by_id = {a.product_id: a for a in report.actions}
    assert set(by_id) == {"A", "B"}
    # B: daily_demand=0 -> dead regardless of ABC/XYZ.
    assert by_id["B"].action == sp.CUT
    assert by_id["B"].eo_classification == "dead"
    assert sp.verify(report) == []
