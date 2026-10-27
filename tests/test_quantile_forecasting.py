"""Unit tests for Quantile Forecasting and Pinball Loss (src/quantile_forecasting.py)."""

import numpy as np
import pytest

from src.quantile_forecasting import (
    empirical_quantile_forecast,
    lightgbm_available,
    lightgbm_quantile_forecast,
    pinball_loss,
    prevent_quantile_crossing,
    weighted_scaled_pinball_loss,
)


def test_pinball_loss_symmetry_at_median():
    """At q = 0.5, pinball loss equals half of Mean Absolute Error (MAE / 2)."""
    y_true = [10.0, 20.0, 30.0]
    y_pred = [12.0, 18.0, 35.0]  # errors: -2, +2, -5 => absolute: 2, 2, 5 => mean 3.0
    loss = pinball_loss(y_true, y_pred, quantile=0.5)
    assert loss == pytest.approx(1.5)


def test_pinball_loss_asymmetric_penalties():
    """Underestimation (actual > pred) is penalized by q; overestimation (actual < pred) by (1-q)."""
    # Case 1: Underestimation (actual=100, pred=80 => error=+20)
    loss_q90_under = pinball_loss([100.0], [80.0], quantile=0.9)
    assert loss_q90_under == pytest.approx(0.9 * 20.0)  # 18.0

    loss_q10_under = pinball_loss([100.0], [80.0], quantile=0.1)
    assert loss_q10_under == pytest.approx(0.1 * 20.0)  # 2.0

    # Case 2: Overestimation (actual=80, pred=100 => error=-20)
    loss_q90_over = pinball_loss([80.0], [100.0], quantile=0.9)
    assert loss_q90_over == pytest.approx((1.0 - 0.9) * 20.0)  # 2.0

    loss_q10_over = pinball_loss([80.0], [100.0], quantile=0.1)
    assert loss_q10_over == pytest.approx((1.0 - 0.1) * 20.0)  # 18.0


def test_pinball_loss_invalid_quantile_raises():
    with pytest.raises(ValueError, match="strictly between 0 and 1"):
        pinball_loss([10], [10], quantile=0.0)
    with pytest.raises(ValueError, match="strictly between 0 and 1"):
        pinball_loss([10], [10], quantile=1.0)
    with pytest.raises(ValueError, match="strictly between 0 and 1"):
        pinball_loss([10], [10], quantile=-0.2)


def test_weighted_scaled_pinball_loss_m5_formula():
    history = [10.0, 12.0, 11.0, 15.0]  # diffs: 2, -1, 4 => squares: 4, 1, 16 => mean 7.0 => sqrt(7)
    actual = [14.0]
    pred = [10.0]  # error 4.0
    scale = np.sqrt(np.mean([4, 1, 16]))
    expected_raw = 0.5 * 4.0
    expected_wspl = expected_raw / scale

    score = weighted_scaled_pinball_loss(actual, pred, quantile=0.5, in_sample_history=history, weight=1.0)
    assert score == pytest.approx(expected_wspl)


def test_prevent_quantile_crossing_rearranges_order():
    """Lower quantile must not exceed higher quantile."""
    quantiles = [0.1, 0.5, 0.9]
    unordered_preds = [50.0, 40.0, 30.0]  # Inverted!
    fixed = prevent_quantile_crossing(quantiles, unordered_preds)
    assert fixed[0] <= fixed[1] <= fixed[2]
    assert fixed == (50.0, 50.0, 50.0)


def test_empirical_quantile_forecast_structure_and_buffers():
    history = [100, 105, 95, 110, 90, 102, 98, 104, 96, 101]
    res = empirical_quantile_forecast(history, quantiles=(0.1, 0.5, 0.9))

    assert res.n_periods == len(history)
    assert res.lower_band <= res.median <= res.upper_band
    assert res.asymmetric_upside_buffer >= 0.0
    assert res.asymmetric_downside_slack >= 0.0

    buf = res.to_buffer_inputs()
    assert buf["median_demand"] == pytest.approx(res.median)
    assert buf["upside_safety_buffer"] == pytest.approx(res.upper_band - res.median)


def test_lightgbm_quantile_forecast_graceful_fallback():
    history = [20, 22, 25, 21, 23, 24, 28, 26, 25, 27]
    res = lightgbm_quantile_forecast(history, quantiles=(0.1, 0.5, 0.9))

    assert res.lower_band <= res.median <= res.upper_band
    if not lightgbm_available():
        assert res.fallback_used is True
        assert "fallback" in res.method
    else:
        assert res.fallback_used is False
