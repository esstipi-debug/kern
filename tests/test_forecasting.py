"""Tests for the demand forecasting front-end."""

import numpy as np
import pytest

from src.forecasting import (
    ForecastResult,
    average_demand_interval,
    croston,
    forecast_demand,
    is_intermittent,
    moving_average,
    simple_exponential_smoothing,
)
from src.policies import continuous_review_sq

DENSE = [100, 102, 98, 101, 99, 103, 97, 100, 101, 99]
INTERMITTENT = [0, 0, 5, 0, 0, 0, 8, 0, 0, 6]


def test_moving_average_constant_series_has_zero_error():
    result = moving_average([50, 50, 50, 50, 50], window=3)
    assert result.forecast == pytest.approx(50)
    assert result.error_std == pytest.approx(0.0)
    assert result.bias == pytest.approx(0.0)


def test_ses_constant_series_converges_to_level():
    result = simple_exponential_smoothing([50, 50, 50, 50, 50], alpha=0.3)
    assert result.forecast == pytest.approx(50)
    assert result.error_std == pytest.approx(0.0)


def test_ses_recovers_mean_and_positive_error_on_noisy_demand():
    rng = np.random.default_rng(42)
    history = rng.normal(100, 10, size=200)
    history = np.clip(history, 0, None)
    result = simple_exponential_smoothing(history, alpha=0.2)
    assert result.forecast == pytest.approx(100, abs=8)
    assert result.error_std > 0


def test_average_demand_interval_and_intermittency():
    assert average_demand_interval(INTERMITTENT) == pytest.approx(10 / 3)
    assert is_intermittent(INTERMITTENT) is True
    assert is_intermittent(DENSE) is False


def test_average_demand_interval_all_zero_is_infinite():
    assert average_demand_interval([0, 0, 0]) == float("inf")


def test_croston_forecast_is_positive_rate_for_intermittent():
    result = croston(INTERMITTENT, alpha=0.1)
    assert result.forecast > 0
    # forecast is a per-period rate, bounded by the nonzero demand sizes
    assert result.forecast < max(INTERMITTENT)
    assert result.is_intermittent is True


def test_croston_all_zero_history_forecasts_zero():
    result = croston([0, 0, 0, 0])
    assert result.forecast == 0.0
    assert result.error_std == 0.0


def test_forecast_demand_auto_dispatch():
    assert forecast_demand(DENSE).method == "ses"
    assert forecast_demand(INTERMITTENT).method == "croston"


def test_forecast_demand_rejects_unknown_method():
    with pytest.raises(ValueError):
        forecast_demand(DENSE, method="prophet")


def test_invalid_history_raises():
    with pytest.raises(ValueError):
        moving_average([])
    with pytest.raises(ValueError):
        simple_exponential_smoothing([10, -5, 20])


def test_invalid_alpha_raises():
    with pytest.raises(ValueError):
        simple_exponential_smoothing(DENSE, alpha=0)
    with pytest.raises(ValueError):
        croston(INTERMITTENT, alpha=1.5)


def test_to_engine_inputs_uses_error_std_and_feeds_policy():
    """The forecast must plug straight into the inventory engine."""
    result = simple_exponential_smoothing(DENSE, alpha=0.3)
    inputs = result.to_engine_inputs(periods_per_year=52)
    assert inputs["mean_demand_per_period"] == pytest.approx(result.forecast)
    assert inputs["demand_std_per_period"] == pytest.approx(result.error_std)
    assert inputs["annual_demand"] == pytest.approx(result.forecast * 52)

    policy = continuous_review_sq(
        **inputs,
        holding_cost_per_unit=2.0,
        fixed_order_cost=50.0,
        lead_time_periods=2,
        cycle_service_level=0.95,
    )
    assert policy.policy == "(s, Q)"
    assert policy.reorder_point > 0
    assert policy.order_quantity > 0


def test_to_engine_inputs_falls_back_to_demand_std():
    """With a single error sample, error_std is 0 -> fall back to demand std."""
    result = ForecastResult(
        method="x", forecast=10.0, demand_mean=10.0, demand_std=3.0,
        error_std=0.0, bias=0.0, mae=0.0, n_periods=2, is_intermittent=False,
    )
    assert result.to_engine_inputs()["demand_std_per_period"] == 3.0
