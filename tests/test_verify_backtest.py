"""Tests for src/verify/backtest.py (Linchpin 3.0 PR-8, Control Tower A4 "verify").

Hand-verified reference numbers (matching the repo's numeric-reference-test
culture, see CLAUDE.md / plan rule 7):

SKU-A: actual=[100, 0, 200], forecast=[110, 5, 180]
  - reuses tests/test_forecast_metrics.py::test_mape_skips_zero_actuals verbatim:
    mape == (10/100 + 20/200) / 2 = (0.1 + 0.1) / 2 = 0.1 exactly
    (the middle actual=0 row is excluded from the MAPE ratio -> n_excluded=1)
  - wape = sum|error| / sum|actual| = (10 + 5 + 20) / (100 + 0 + 200) = 35/300
  - bias = mean(forecast - actual) = mean(10, 5, -20) = -5/3

SKU-B: actual=[100, 110, 90, 105], forecast=[90, 120, 100, 100]
  - reuses tests/test_forecast_metrics.py::test_wape verbatim: wape == 35/405
  - bias = mean(-10, 10, 10, -5) = 5/4 = 1.25
  - mape = mean(10/100, 10/110, 10/90, 5/105) [no zero actuals -> n_excluded=0]

SKU-C: actual=[0, 0], forecast=[3, 4] (degenerate: every actual is zero)
  - reuses tests/test_forecast_metrics.py::test_wape_zero_demand_is_inf verbatim:
    wape is +inf, and mape is +inf too (no nonzero actual to divide by)
  - bias = mean(3 - 0, 4 - 0) = 3.5 (bias is well-defined even at actual=0)
  - this is the "zero-actual edge case" the PR brief requires: it must not
    crash, and +inf is an honest "undefined", never a fabricated number.
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from src.safety_stock import service_level_factor, tune_service_level
from src.state.store import StateStore
from src.state.system_state import snapshot
from src.verify.backtest import (
    MatchedObservation,
    SkuBacktestResult,
    match_decision_actuals,
    match_forecast_actuals,
    per_sku_backtest,
    run_forecast_backtest,
    suggest_sigma_recalibration,
)

# -- per_sku_backtest: hand-verified reference numbers -----------------------


def _sku_a_obs() -> list[MatchedObservation]:
    actual = [100.0, 0.0, 200.0]
    forecast = [110.0, 5.0, 180.0]
    return [
        MatchedObservation("SKU-A", f"P{i}", predicted=f, actual=a)
        for i, (a, f) in enumerate(zip(actual, forecast))
    ]


def _sku_b_obs() -> list[MatchedObservation]:
    actual = [100.0, 110.0, 90.0, 105.0]
    forecast = [90.0, 120.0, 100.0, 100.0]
    return [
        MatchedObservation("SKU-B", f"P{i}", predicted=f, actual=a)
        for i, (a, f) in enumerate(zip(actual, forecast))
    ]


def _sku_c_obs() -> list[MatchedObservation]:
    actual = [0.0, 0.0]
    forecast = [3.0, 4.0]
    return [
        MatchedObservation("SKU-C", f"P{i}", predicted=f, actual=a)
        for i, (a, f) in enumerate(zip(actual, forecast))
    ]


def test_per_sku_backtest_sku_a_matches_hand_computed_mape_wape_bias():
    results = {r.product_id: r for r in per_sku_backtest(_sku_a_obs())}
    a = results["SKU-A"]

    assert a.n_matched == 3
    assert a.n_excluded_zero_actual == 1  # the actual=0 row
    assert a.mape == pytest.approx((0.1 + 0.1) / 2)  # == 0.1
    assert a.wape == pytest.approx(35 / 300)
    assert a.bias == pytest.approx(-5 / 3)
    assert a.errors == (10.0, 5.0, -20.0)


def test_per_sku_backtest_sku_b_matches_hand_computed_mape_wape_bias():
    results = {r.product_id: r for r in per_sku_backtest(_sku_b_obs())}
    b = results["SKU-B"]

    assert b.n_matched == 4
    assert b.n_excluded_zero_actual == 0
    assert b.wape == pytest.approx(35 / 405)
    assert b.bias == pytest.approx(5 / 4)
    expected_mape = (10 / 100 + 10 / 110 + 10 / 90 + 5 / 105) / 4
    assert b.mape == pytest.approx(expected_mape)


def test_per_sku_backtest_sku_c_zero_actual_is_honest_inf_not_a_crash_or_fabrication():
    """The whole-window-zero-actual edge case: no exception, and the "undefined"
    result is +inf, not a made-up 0% or 100% precision number."""
    results = {r.product_id: r for r in per_sku_backtest(_sku_c_obs())}
    c = results["SKU-C"]

    assert c.n_matched == 2
    assert c.n_excluded_zero_actual == 2
    assert math.isinf(c.mape)
    assert math.isinf(c.wape)
    assert c.bias == pytest.approx(3.5)  # bias IS well-defined even at actual=0


def test_per_sku_backtest_groups_multiple_skus_independently_and_sorts_by_product_id():
    all_obs = _sku_b_obs() + _sku_a_obs() + _sku_c_obs()
    results = per_sku_backtest(all_obs)

    assert [r.product_id for r in results] == ["SKU-A", "SKU-B", "SKU-C"]
    assert {r.n_matched for r in results} == {3, 4, 2}


def test_per_sku_backtest_empty_input_returns_empty_list():
    assert per_sku_backtest([]) == []


# -- joining src.state history into matched observations ---------------------


def test_match_forecast_actuals_joins_forecast_and_outcomes_domains(tmp_path):
    store = StateStore(tmp_path / "state")
    forecast_df = pd.DataFrame(
        {"product_id": ["SKU-A", "SKU-B"], "period": ["2026-01", "2026-01"], "forecast_qty": [110.0, 90.0]}
    )
    outcomes_df = pd.DataFrame(
        {
            "product_id": ["SKU-A", "SKU-B", "SKU-A"],
            "tool": ["forecast", "forecast", "forecast"],
            "metric": ["actual_qty", "actual_qty", "some_other_metric"],
            "value": [100.0, 95.0, 999.0],
            "period": ["2026-01", "2026-01", "2026-01"],
        }
    )
    snapshot("forecast", forecast_df, "1", store=store)
    snapshot("outcomes", outcomes_df, "1", store=store)

    from src.state.system_state import history

    matched = match_forecast_actuals(history("forecast", store=store), history("outcomes", store=store))

    assert {(m.product_id, m.predicted, m.actual) for m in matched} == {
        ("SKU-A", 110.0, 100.0),
        ("SKU-B", 90.0, 95.0),
    }


def test_match_forecast_actuals_returns_empty_list_when_outcomes_lack_period_column(tmp_path):
    """Extending the outcomes domain with 'period' is opt-in (strict=False allows
    extra columns, per src/state/system_state.py) -- a caller who hasn't added it
    yet gets an empty, honest result instead of a KeyError."""
    store = StateStore(tmp_path / "state")
    forecast_df = pd.DataFrame({"product_id": ["SKU-A"], "period": ["2026-01"], "forecast_qty": [110.0]})
    outcomes_df = pd.DataFrame(
        {"product_id": ["SKU-A"], "tool": ["forecast"], "metric": ["actual_qty"], "value": [100.0]}
    )
    snapshot("forecast", forecast_df, "1", store=store)
    snapshot("outcomes", outcomes_df, "1", store=store)

    from src.state.system_state import history

    matched = match_forecast_actuals(history("forecast", store=store), history("outcomes", store=store))
    assert matched == []


def test_match_forecast_actuals_empty_snapshots_returns_empty_list():
    assert match_forecast_actuals([], []) == []


def test_match_decision_actuals_joins_decisions_and_outcomes_domains(tmp_path):
    store = StateStore(tmp_path / "state")
    decisions_df = pd.DataFrame(
        {
            "product_id": ["SKU-A"],
            "tool": ["inventory_optimization"],
            "decision": ["reorder 50 units"],
            "period": ["2026-01"],
            "recommended_qty": [50.0],
        }
    )
    outcomes_df = pd.DataFrame(
        {
            "product_id": ["SKU-A"],
            "tool": ["inventory_optimization"],
            "metric": ["actual_qty"],
            "value": [48.0],
            "period": ["2026-01"],
        }
    )
    snapshot("decisions", decisions_df, "1", store=store)
    snapshot("outcomes", outcomes_df, "1", store=store)

    from src.state.system_state import history

    matched = match_decision_actuals(
        history("decisions", store=store), history("outcomes", store=store), decision_value_column="recommended_qty"
    )

    assert len(matched) == 1
    assert matched[0].predicted == 50.0
    assert matched[0].actual == 48.0
    assert matched[0].tool == "inventory_optimization"


def test_match_decision_actuals_missing_extra_column_returns_empty_list_not_a_crash(tmp_path):
    store = StateStore(tmp_path / "state")
    decisions_df = pd.DataFrame(
        {"product_id": ["SKU-A"], "tool": ["inventory_optimization"], "decision": ["reorder"]}
    )
    outcomes_df = pd.DataFrame(
        {"product_id": ["SKU-A"], "tool": ["inventory_optimization"], "metric": ["actual_qty"], "value": [48.0]}
    )
    snapshot("decisions", decisions_df, "1", store=store)
    snapshot("outcomes", outcomes_df, "1", store=store)

    from src.state.system_state import history

    matched = match_decision_actuals(
        history("decisions", store=store), history("outcomes", store=store), decision_value_column="recommended_qty"
    )
    assert matched == []


def test_run_forecast_backtest_end_to_end_through_src_state_history(tmp_path):
    store = StateStore(tmp_path / "state")
    snapshot(
        "forecast",
        pd.DataFrame({"product_id": ["SKU-A"], "period": ["2026-01"], "forecast_qty": [110.0]}),
        "1",
        store=store,
    )
    snapshot(
        "outcomes",
        pd.DataFrame(
            {"product_id": ["SKU-A"], "tool": ["forecast"], "metric": ["actual_qty"], "value": [100.0], "period": ["2026-01"]}
        ),
        "1",
        store=store,
    )

    results = run_forecast_backtest(store=store)

    assert len(results) == 1
    assert results[0].product_id == "SKU-A"
    assert results[0].bias == pytest.approx(10.0)


# -- recalibration suggestion -------------------------------------------------


def _result_with_errors(errors: tuple[float, ...], product_id: str = "SKU-X") -> "SkuBacktestResult":
    return SkuBacktestResult(
        product_id=product_id,
        n_matched=len(errors),
        n_excluded_zero_actual=0,
        mape=float("nan"),
        wape=float("nan"),
        bias=(sum(errors) / len(errors)) if errors else float("nan"),
        errors=errors,
    )


def test_suggest_sigma_recalibration_insufficient_data_below_two_observations():
    result = _result_with_errors((5.0,))  # n_matched == 1

    suggestion = suggest_sigma_recalibration(result, current_sigma_e=10.0)

    assert suggestion.observed_sigma_e is None
    assert suggestion.suggested_sigma_e == 10.0  # unchanged: no fabricated number
    assert suggestion.recommend_recalibration is False
    assert "need >= 2" in suggestion.rationale


def test_suggest_sigma_recalibration_material_change_is_recommended():
    """errors = [5, -5, 5, -5]: mean=0, sample variance = sum((e-0)^2)/(n-1)
    = (25+25+25+25)/3 = 100/3, so sigma = sqrt(100/3) ~= 5.7735.
    current_sigma_e=2.0 -> relative_change = |5.7735 - 2| / 2 ~= 1.887 > 0.20 threshold."""
    result = _result_with_errors((5.0, -5.0, 5.0, -5.0))

    suggestion = suggest_sigma_recalibration(result, current_sigma_e=2.0)

    expected_sigma = math.sqrt(100 / 3)
    assert suggestion.observed_sigma_e == pytest.approx(expected_sigma)
    assert suggestion.recommend_recalibration is True
    assert suggestion.suggested_sigma_e == pytest.approx(expected_sigma)
    assert suggestion.sigma_relative_change == pytest.approx(abs(expected_sigma - 2.0) / 2.0)


def test_suggest_sigma_recalibration_small_change_is_not_recommended():
    """errors = [1, -1, 1, -1]: sigma = sqrt(4/3) ~= 1.1547.
    current_sigma_e=1.0 -> relative_change ~= 0.1547, below the 0.20 default threshold."""
    result = _result_with_errors((1.0, -1.0, 1.0, -1.0))

    suggestion = suggest_sigma_recalibration(result, current_sigma_e=1.0)

    assert suggestion.recommend_recalibration is False
    assert suggestion.suggested_sigma_e == 1.0  # kept at current, not the noisy observed value


def test_suggest_sigma_recalibration_z_score_reuses_safety_stock_functions():
    """implied_fill_rate for errors=[5,-5,5,-5] = fraction with error >= 0 = 2/4 = 0.5.
    tune_service_level(0.90, 0.5, 0.95, step=0.5) = 0.90 + 0.5*(0.95-0.5) = 1.125,
    clamped to hi=0.999 (src.safety_stock.tune_service_level's own bound)."""
    result = _result_with_errors((5.0, -5.0, 5.0, -5.0))

    suggestion = suggest_sigma_recalibration(
        result, current_sigma_e=2.0, current_service_level=0.90, target_fill_rate=0.95, service_level_step=0.5
    )

    expected_suggested_level = tune_service_level(0.90, 0.5, 0.95, step=0.5)
    assert expected_suggested_level == pytest.approx(0.999)
    assert suggestion.suggested_service_level == pytest.approx(expected_suggested_level)
    assert suggestion.current_z == pytest.approx(service_level_factor(0.90))
    assert suggestion.suggested_z == pytest.approx(service_level_factor(0.999))


def test_suggest_sigma_recalibration_without_service_level_leaves_z_fields_none():
    result = _result_with_errors((5.0, -5.0, 5.0, -5.0))
    suggestion = suggest_sigma_recalibration(result, current_sigma_e=2.0)

    assert suggestion.current_z is None
    assert suggestion.suggested_z is None
    assert suggestion.suggested_service_level is None


def test_suggest_sigma_recalibration_rejects_negative_current_sigma():
    result = _result_with_errors((5.0, -5.0))
    with pytest.raises(ValueError):
        suggest_sigma_recalibration(result, current_sigma_e=-1.0)
