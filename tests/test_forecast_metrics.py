"""Tests for forecast accuracy metrics (capability M2 — linchpin-forecast-metrics)."""

import math

import pytest

from src.forecast_metrics import ForecastMetrics, compute_metrics, mae, mape, rmsse, wape


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
