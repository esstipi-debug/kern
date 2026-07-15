"""Tests for old-method vs Linchpin comparison helpers."""

import pytest

from src.benchmarks import compare_forecast_methods


def test_compare_forecast_methods_naive_worse_than_smart():
    """Synthetic case: a flat naive forecast is clearly worse than a
    near-perfect smart forecast."""
    actuals = [10, 12, 8, 15, 9, 11]
    naive_forecast = [10, 10, 10, 10, 10, 10]
    smart_forecast = [10, 12, 8, 15, 9, 11]
    result = compare_forecast_methods(actuals, naive_forecast, smart_forecast)
    assert result.smart_mae < result.naive_mae
    assert result.smart_wape < result.naive_wape
    assert result.improvement_pct == pytest.approx(100.0, abs=0.01)


def test_compare_forecast_methods_identical_forecasts_zero_improvement():
    actuals = [10, 12, 8, 15, 9, 11]
    forecast = [9, 11, 9, 14, 10, 10]
    result = compare_forecast_methods(actuals, forecast, forecast)
    assert result.improvement_pct == pytest.approx(0.0, abs=0.01)
    assert result.naive_mae == result.smart_mae


def test_compare_forecast_methods_perfect_naive_stays_finite():
    """If the naive method happens to be a perfect forecast (naive_wape=0), the
    division-by-zero guard must keep improvement_pct finite rather than raising
    -- this exact case is untested by the other 3 tests, all of which have a
    strictly positive naive_wape."""
    actuals = [10, 20, 30]
    naive_forecast = [10, 20, 30]  # perfect -> naive_wape == 0
    smart_forecast = [11, 19, 31]  # imperfect but reasonable
    result = compare_forecast_methods(actuals, naive_forecast, smart_forecast)
    assert result.naive_wape == 0.0
    assert result.improvement_pct == 0.0  # guard fallback, not a crash


def test_compare_forecast_methods_hand_computed_mae():
    """Hand-computed MAE cross-check, independent of mae()/wape() themselves."""
    actuals = [10, 20, 30]
    naive_forecast = [12, 18, 33]  # |errors| = 2, 2, 3 -> MAE = 7/3
    smart_forecast = [10, 20, 30]  # perfect -> MAE = 0
    result = compare_forecast_methods(actuals, naive_forecast, smart_forecast)
    assert result.naive_mae == pytest.approx(7 / 3, abs=0.01)
    assert result.smart_mae == pytest.approx(0.0, abs=0.01)
