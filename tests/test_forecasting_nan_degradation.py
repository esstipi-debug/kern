"""The forecast must never hand the inventory engine a silently-substituted sigma.

Regression cover for the degradation the 2026-07-26 autopsy measured by running the
code: TSB/Croston emit a warm-up NaN in their fitted values, ``np.mean``/``np.std``
propagate it into ``error_std``, and ``to_engine_inputs`` then took the
``demand_std`` fallback because ``nan > 0`` is False -- with no warning and no
record. Measured on the autopsy series: sigma 2,626 used where 2,753 was correct.

AutoETS does not emit that warm-up NaN, so the defect only showed on the
intermittent route. That asymmetry is what made it survive.
"""

import math

import numpy as np
import pandas as pd
import pytest

from src.forecasting import ForecastResult, forecast_demand, simple_exponential_smoothing
from src.forecasting_auto import _error_stats_from_fitted, history_to_frame


def _result(**overrides) -> ForecastResult:
    base = dict(
        method="tsb",
        forecast=10.0,
        demand_mean=10.0,
        demand_std=3.0,
        error_std=2.0,
        bias=0.0,
        mae=1.0,
        n_periods=30,
        is_intermittent=True,
    )
    base.update(overrides)
    return ForecastResult(**base)


# ---- the NaN itself ---------------------------------------------------------

def test_error_stats_drops_the_warmup_nan_instead_of_propagating_it():
    """A NaN fitted value is an unusable observation, not an unusable statistic."""
    fitted = pd.DataFrame({"y": [1.0, 2.0, 3.0, 4.0], "TSB": [float("nan"), 2.5, 2.5, 3.5]})

    error_std, _robusto, bias, mae, n_dropped = _error_stats_from_fitted(fitted, "TSB")

    assert n_dropped == 1
    assert math.isfinite(error_std) and math.isfinite(bias) and math.isfinite(mae)
    # Computed over the 3 surviving pairs: errors = [-0.5, 0.5, 0.5]
    assert bias == pytest.approx(1.0 / 6.0)
    assert mae == pytest.approx(0.5)
    assert error_std == pytest.approx(float(np.std([-0.5, 0.5, 0.5], ddof=1)))


def test_error_stats_reports_zero_dropped_when_every_pair_is_usable():
    fitted = pd.DataFrame({"y": [1.0, 2.0, 3.0], "AutoETS": [1.5, 2.5, 2.5]})

    _, _, _, _, n_dropped = _error_stats_from_fitted(fitted, "AutoETS")

    assert n_dropped == 0


def test_error_stats_on_all_nan_predictions_reports_everything_dropped():
    """Nothing survived: the caller must be able to tell that from the count."""
    fitted = pd.DataFrame({"y": [1.0, 2.0], "TSB": [float("nan"), float("nan")]})

    error_std, _robusto, bias, mae, n_dropped = _error_stats_from_fitted(fitted, "TSB")

    assert n_dropped == 2
    assert (error_std, bias, mae) == (0.0, 0.0, 0.0)


# ---- sigma_source: to_engine_inputs may not choose in silence ---------------

def test_sigma_source_is_forecast_error_when_sigma_e_is_usable():
    result = _result(error_std=2.0, demand_std=3.0)

    assert result.sigma_source == "forecast_error"
    assert result.to_engine_inputs()["demand_std_per_period"] == pytest.approx(2.0)


def test_sigma_source_names_the_fallback_instead_of_taking_it_silently():
    """error_std=0 means 'could not estimate', not 'estimated as zero'."""
    result = _result(error_std=0.0, demand_std=3.0)

    assert result.sigma_source == "demand_std_fallback"
    assert result.to_engine_inputs()["demand_std_per_period"] == pytest.approx(3.0)


def test_sigma_source_is_unavailable_when_neither_dispersion_is_usable():
    result = _result(error_std=0.0, demand_std=0.0)

    assert result.sigma_source == "unavailable"
    assert result.to_engine_inputs()["demand_std_per_period"] == pytest.approx(0.0)


def test_a_nan_sigma_e_is_a_fallback_on_the_record_not_a_silent_swap():
    """The exact shape of the measured defect: nan > 0 is False, so it fell through."""
    result = _result(error_std=float("nan"), demand_std=3.0)

    assert result.sigma_source == "demand_std_fallback"
    assert result.to_engine_inputs()["demand_std_per_period"] == pytest.approx(3.0)


def test_a_nan_demand_std_is_refused_rather_than_fed_to_safety_stock():
    """Substituting 0 here would print 'no buffer needed' as a confident fact."""
    result = _result(error_std=float("nan"), demand_std=float("nan"))

    assert result.sigma_source == "unavailable"
    with pytest.raises(ValueError, match="not finite"):
        result.to_engine_inputs()


def test_n_error_obs_dropped_defaults_to_zero_for_the_legacy_paths():
    assert simple_exponential_smoothing([10.0, 12.0, 11.0]).n_error_obs_dropped == 0


# ---- reject an impossible history at the door -------------------------------

@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_forecast_refuses_a_history_that_is_not_finite(bad):
    """A blank cell in the client's CSV is not a demand of zero, and not a number."""
    with pytest.raises(ValueError, match="finite"):
        forecast_demand([10.0, 12.0, bad, 11.0], method="ses")


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_history_to_frame_refuses_a_history_that_is_not_finite(bad):
    with pytest.raises(ValueError, match="finite"):
        history_to_frame([10.0, bad, 11.0])
