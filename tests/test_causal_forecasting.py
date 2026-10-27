"""Tests for causal multivariate forecasting with AIC gating in src/causal_forecasting.py."""

import numpy as np
import pytest

from src.causal_forecasting import fit_causal_forecast


def test_promotional_covariate_accepted_by_aic():
    # 20 periods: Base demand is ~100.
    # On promo periods (t = 4, 9, 14, 19), demand jumps by ~150 to ~250.
    np.random.seed(42)
    n = 20
    promo = np.zeros(n)
    promo_indices = [4, 9, 14, 19]
    promo[promo_indices] = 1.0

    noise = np.random.normal(0, 3, n)
    y = 100.0 + 150.0 * promo + noise

    # Upcoming period (t=20) is a promo period!
    res = fit_causal_forecast(
        y=y,
        covariates={"promo": promo},
        future_covariates={"promo": 1.0},
        aic_tolerance=2.0,
    )

    assert res.verdict == "causal_accepted"
    assert res.method == "causal_multivariate_aic"
    assert "promo" in res.covariates_retained
    assert res.coefficients["promo"] > 140.0  # Captures the ~150 uplift
    assert res.forecast == pytest.approx(250.0, abs=10.0)  # Predicts ~250 for the promo
    assert res.delta_aic < -10.0  # Massive improvement in AIC
    assert res.fva_pct > 0.50  # Over 50% variance explained by promo


def test_spurious_noise_covariate_rejected_by_aic_gate():
    # Base demand is stable ~100 with small noise
    np.random.seed(123)
    n = 20
    y = 100.0 + np.random.normal(0, 2, n)

    # Completely unrelated random noise covariate
    fake_covariate = np.random.uniform(10, 50, n)

    res = fit_causal_forecast(
        y=y,
        covariates={"fake_feature": fake_covariate},
        future_covariates={"fake_feature": 30.0},
        aic_tolerance=2.0,
    )

    # Must refuse the covariate and fall back to univariate baseline!
    assert res.verdict == "univariate_fallback"
    assert res.method == "univariate_trend_baseline"
    assert res.covariates_retained == ()
    assert res.forecast == pytest.approx(100.0, abs=5.0)


def test_validation_short_history_raises():
    with pytest.raises(ValueError, match="at least 6 demand periods"):
        fit_causal_forecast(
            y=[10, 20, 30, 40, 50],  # only 5 periods
            covariates={"promo": [0, 1, 0, 1, 0]},
            future_covariates={"promo": 0.0},
        )
