"""sigma_e must be measured against data the model did not see while fitting.

`z_alpha * sigma_e * sqrt(tau)` assumes sigma_e is the spread of ONE-STEP-AHEAD
FORECAST ERRORS. What the engine was handed instead was the spread of in-sample
residuals: what is left after the model has already bent itself toward those exact
points. For a model that fits parameters to the data, those two are not the same
number, and the in-sample one is the smaller -- so the band it sizes is narrower
than the errors the client actually lives through, and a "95% service level" is
not 95%.

The size of the gap is NOT uniform, and pretending otherwise would be its own
error. It scales with how many parameters the model estimated:

  - SES / Croston / moving average run on FIXED smoothing constants and estimate
    nothing, so their fitted values are already honest one-step-ahead predictions
    from prior data only. Measured here: in-sample within ~6% of rolling-origin,
    in both directions. No systematic optimism to correct.
  - AutoETS estimates its parameters. On a plain stationary or trending series it
    is still nearly unbiased (~4%); on a SEASONAL series -- many more fitted
    parameters -- it understated the true one-step error by ~20%. Seasonal demand
    is the normal case in retail.

So the fix is not "in-sample is always wrong". It is: measure it, per SKU, and
record which of the two produced the number.
"""

from __future__ import annotations

import numpy as np
import pytest

from src.forecasting import (
    DEFAULT_OOS_ORIGINS,
    MIN_PERIODS_FOR_POLICY,
    forecast_demand,
    forecast_with_oos_sigma,
    rolling_origin_error_std,
)


def _seasonal(n: int = 52, seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (10 + 4 * np.sin(np.arange(n) / 13 * 2 * np.pi) + rng.normal(0, 2, n)).clip(0)


def _stationary(n: int = 40, seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (10 + rng.normal(0, 3, n)).clip(0)


# ---------------------------------------------------------------------------
# the estimator itself
# ---------------------------------------------------------------------------


def test_every_error_is_predicted_from_prior_data_only():
    """The property that makes the estimate out-of-sample at all.

    Reproduces the walk by hand against a method with a closed form, so the test
    fails if the implementation ever quietly starts fitting on the full series.
    """
    series = _stationary(20)
    sigma, n = rolling_origin_error_std(series, method="ses", max_origins=5, min_train=6)

    expected = [
        float(series[t] - forecast_demand(series[:t], method="ses").forecast)
        for t in range(15, 20)
    ]
    assert n == 5
    assert sigma == pytest.approx(float(np.std(expected, ddof=1)))


def test_a_series_too_short_to_walk_reports_not_measured_never_zero():
    """A zero sigma is a claim ('demand is perfectly predictable'); NaN is an
    admission. Returning 0.0 here would size a zero safety buffer -- the exact
    failure mode the minimum-history gate exists to stop."""
    sigma, n = rolling_origin_error_std(np.array([5.0, 6.0, 7.0]), method="ses")

    assert np.isnan(sigma)
    assert n == 0


def test_walk_is_capped_at_max_origins():
    _, n = rolling_origin_error_std(_stationary(40), method="ses", max_origins=7)
    assert n == 7


# ---------------------------------------------------------------------------
# what the engine ends up using
# ---------------------------------------------------------------------------


def test_engine_is_handed_the_out_of_sample_sigma():
    result = forecast_with_oos_sigma(_stationary(30), method="ses")

    assert result.sigma_source == "forecast_error_oos"
    assert result.n_oos_errors == DEFAULT_OOS_ORIGINS
    assert result.to_engine_inputs()["demand_std_per_period"] == pytest.approx(
        result.error_std_oos
    )
    # the in-sample number is kept, not overwritten, so the two stay comparable
    assert result.error_std > 0
    assert result.error_std != result.error_std_oos


def test_opting_out_falls_back_to_the_in_sample_residual_and_says_so():
    """max_origins=0 is the latency escape hatch. It must degrade to the previous
    behaviour AND be visible in the record -- a silent downgrade would leave a
    reader unable to tell which SKUs carry a measured band."""
    result = forecast_with_oos_sigma(_stationary(30), method="ses", max_origins=0)

    assert result.sigma_source == "forecast_error"
    assert result.n_oos_errors == 0
    assert result.to_engine_inputs()["demand_std_per_period"] == pytest.approx(
        result.error_std
    )


def test_a_series_too_short_to_walk_still_produces_a_usable_forecast():
    """The walk failing must not take the forecast down with it."""
    result = forecast_with_oos_sigma(np.array([5.0, 6.0, 7.0, 6.0]), method="ses")

    assert result.sigma_source == "forecast_error"
    assert result.forecast > 0


# ---------------------------------------------------------------------------
# the finding this was built for, and its honest boundary
# ---------------------------------------------------------------------------


def test_autoets_on_seasonal_demand_understates_its_own_error_in_sample():
    """The case that justifies paying for the walk. AutoETS fits seasonal
    parameters, so its in-sample residuals are measured against points it already
    bent toward -- and a safety band sized off them is materially too narrow."""
    result = forecast_with_oos_sigma(_seasonal(), method="auto")

    if result.method != "auto_ets":            # statsforecast absent -> nothing to assert
        pytest.skip("AutoETS unavailable; the legacy path has no fitted parameters")

    assert result.error_std_oos > result.error_std * 1.10, (
        f"expected a material in-sample understatement, got "
        f"in-sample={result.error_std:.3f} oos={result.error_std_oos:.3f}"
    )


def test_fixed_parameter_methods_were_never_materially_optimistic():
    """The boundary of the finding, pinned so nobody later 'corrects' a bias that
    is not there. SES estimates no parameters: its fitted value for period t uses
    only data before t, so its residuals were already one-step-ahead errors."""
    result = forecast_with_oos_sigma(_stationary(40), method="ses")

    ratio = result.error_std_oos / result.error_std
    assert 0.85 < ratio < 1.15, f"in-sample and out-of-sample diverged by {ratio:.2f}x"


def test_min_train_respects_the_minimum_history_threshold():
    """The walk must not train on windows the engine would itself refuse to size a
    policy from -- otherwise the sigma it produces rests on fits this codebase has
    already declared unsupportable."""
    series = _stationary(30)
    _, n = rolling_origin_error_std(series, method="ses", max_origins=100)

    assert n == len(series) - MIN_PERIODS_FOR_POLICY
