"""Tests for the StatsForecast wrapper (src/forecasting_auto.py)."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.forecasting import forecast_demand
from src.forecasting_auto import (
    MIN_PERIODS_STATSFORECAST,
    forecast_modern,
    forecast_portfolio,
    history_to_frame,
    statsforecast_available,
)
from src.policies import continuous_review_sq

DENSE = [100, 102, 98, 101, 99, 103, 97, 100, 101, 99] * 5
INTERMITTENT = [0, 0, 5, 0, 0, 0, 8, 0, 0, 6] * 3
PORTFOLIO_CSV = Path(__file__).resolve().parents[1] / "data" / "sample_demand_portfolio.csv"

pytestmark = pytest.mark.skipif(
    not statsforecast_available(),
    reason="statsforecast not installed (pip install -e '.[forecast]')",
)


def test_history_to_frame_shape():
    frame = history_to_frame(DENSE, unique_id="SKU-A")
    assert list(frame.columns) == ["unique_id", "ds", "y"]
    assert len(frame) == len(DENSE)
    assert frame["unique_id"].iloc[0] == "SKU-A"


def test_history_to_frame_rejects_negative():
    with pytest.raises(ValueError):
        history_to_frame([1, -2, 3])


def test_auto_modern_dense_routes_to_auto_ets():
    result = forecast_modern(DENSE, method="auto_modern")
    assert result.method == "auto_ets"
    assert result.is_intermittent is False
    assert result.forecast == pytest.approx(100, abs=5)
    assert result.error_std >= 0


def test_auto_modern_intermittent_routes_to_tsb():
    result = forecast_modern(INTERMITTENT, method="auto_modern")
    assert result.method == "tsb"
    assert result.is_intermittent is True
    assert result.forecast > 0


def test_short_history_falls_back_to_legacy_ses():
  short = DENSE[: MIN_PERIODS_STATSFORECAST - 1]
  result = forecast_modern(short, method="auto_modern")
  assert result.method == "ses"


def test_short_intermittent_falls_back_to_croston():
    short = INTERMITTENT[: MIN_PERIODS_STATSFORECAST - 1]
    result = forecast_modern(short, method="auto_modern")
    assert result.method == "croston"


def test_forecast_demand_auto_modern_dispatch():
    result = forecast_demand(DENSE, method="auto_modern")
    assert result.method == "auto_ets"


def test_to_engine_inputs_plugs_into_policy():
    result = forecast_modern(DENSE, method="auto_ets")
    policy = continuous_review_sq(
        **result.to_engine_inputs(periods_per_year=52),
        holding_cost_per_unit=2.0,
        fixed_order_cost=50.0,
        lead_time_periods=2,
        cycle_service_level=0.95,
    )
    assert policy.reorder_point > 0
    assert policy.order_quantity > 0


def test_forecast_portfolio_sample_csv():
    if not PORTFOLIO_CSV.is_file():
        pytest.skip("sample_demand_portfolio.csv not generated")
    df = pd.read_csv(PORTFOLIO_CSV, parse_dates=["date"])
    results = forecast_portfolio(df, method="auto_modern")
    assert len(results) >= 4
    for sku, result in results.items():
        assert result.n_periods > 0
        assert result.forecast >= 0
        assert sku in df["product_id"].astype(str).values or sku in df["product_id"].values


def test_constant_dense_series_low_error():
    flat = [50.0] * 30
    result = forecast_modern(flat, method="auto_ets")
    assert result.forecast == pytest.approx(50, abs=1)
    assert result.mae == pytest.approx(0, abs=0.5)


@pytest.mark.parametrize("method", ["auto_modern", "auto_ets", "tsb"])
def test_modern_methods_return_forecast_result(method):
    history = INTERMITTENT if method == "tsb" else DENSE
    result = forecast_modern(history, method=method)
    assert result.n_periods == len(history)
    assert np.isfinite(result.forecast)
