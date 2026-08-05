"""Tests for forecast accuracy metrics (capability M2 — linchpin-forecast-metrics)."""

import math

import pytest

from src.forecast_metrics import (
    ForecastMetrics,
    _naive_mae,
    _naive_rmse,
    compute_metrics,
    mae,
    mape,
    mase,
    rmsse,
    wape,
)


def test_mae():
    assert mae([100, 110, 90, 105], [90, 120, 100, 100]) == pytest.approx(8.75)


def test_mape_skips_zero_actuals():
    assert mape([100, 0, 200], [110, 5, 180]) == pytest.approx((0.1 + 0.1) / 2)


def test_wape():
    assert wape([100, 110, 90, 105], [90, 120, 100, 100]) == pytest.approx(35 / 405)


def test_wape_zero_demand_is_inf():
    assert math.isinf(wape([0, 0], [3, 4]))


def test_rmsse_uses_naive_training_error():
    train = [90, 100, 110, 100, 120]
    val_actual = [100, 110, 90, 105]
    val_fcst = [90, 120, 100, 100]
    # rmse=sqrt(81.25); naive scale=sqrt(mean([10,10,10,20]^2))=sqrt(175)
    assert rmsse(val_actual, val_fcst, train) == pytest.approx(math.sqrt(81.25) / math.sqrt(175), rel=1e-6)


def test_compute_metrics_bundles_everything():
    m = compute_metrics([100, 110, 90, 105], [90, 120, 100, 100], train=[90, 100, 110, 100, 120])
    assert isinstance(m, ForecastMetrics)
    assert m.mae == pytest.approx(8.75)
    assert m.bias == pytest.approx(1.25)  # mean(forecast - actual)
    assert m.wape == pytest.approx(35 / 405)
    assert m.mase == pytest.approx(8.75 / 12.5)  # naive MAE = mean([10,10,10,20]) = 12.5
    assert m.season_length == 1  # default benchmark is the plain lag-1 naive


def test_seasonal_naive_is_a_harder_benchmark_than_lag_1_on_seasonal_demand():
    """The whole point of the season_length knob: a lag-1 naive flatters the model."""
    train = [10, 40, 20, 5] * 5
    actual, forecast = [10, 40, 20, 5], [12, 38, 22, 6]
    lag1 = mase(actual, forecast, train, 1)
    seasonal = mase(actual, forecast, train, 4)
    # A perfect 4-cycle: the seasonal naive is exact, so nothing can beat it.
    assert math.isinf(seasonal)
    assert lag1 < 1.0  # ... whereas lag-1 would have called this a win


def test_seasonal_naive_scales_by_the_same_period_last_cycle():
    train = [10.0, 20.0, 12.0, 26.0, 14.0, 32.0]  # season length 2, drifting up
    # naive errors at m=2: [2, 6, 2, 6] -> MAE 4.0 ; at m=1: [10,-8,14,-12,18] -> MAE 12.4
    assert _naive_mae(train, 2) == pytest.approx(4.0)
    assert _naive_mae(train, 1) == pytest.approx(62 / 5)
    assert _naive_rmse(train, 2) == pytest.approx(math.sqrt(20.0))


def test_train_shorter_than_the_season_gives_an_infinite_scaled_error():
    """No usable benchmark must never look like a great score."""
    assert math.isinf(mase([1, 2], [1, 2], [5.0, 6.0], 4))
    assert math.isinf(rmsse([1, 2], [1, 2], [5.0, 6.0], 4))


def test_season_length_below_one_is_rejected():
    with pytest.raises(ValueError):
        mase([1, 2], [1, 2], [1, 2, 3], 0)
