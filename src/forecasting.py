"""Demand forecasting front-end — the missing fuel intake for the engine.

The inventory models in this package consume an *expected demand per period*
and a *dispersion*. They never forecast: historically the dispersion was the
raw sample std of demand. That is only correct for stationary demand.

This module turns a raw demand history into:
  - ``forecast``   : the next-period point forecast (expected demand/period)
  - ``error_std``  : sigma_e, the std of one-step-ahead forecast errors

sigma_e — not the raw demand std — is the theoretically correct dispersion for
safety stock (Vandeput 2021, *Data Science for Supply Chain Forecasting*,
Sec. 4.2.5). ``ForecastResult.to_engine_inputs`` maps a forecast straight onto
the keyword arguments of ``policies.continuous_review_sq`` / ``periodic_review_rs``.

Methods:
  - moving_average            : stable, stationary demand
  - simple_exponential_smoothing (SES) : stationary demand, recency-weighted
  - croston                   : intermittent / lumpy demand (spare parts)
  - forecast_demand(method="auto") : AutoETS/TSB when [forecast] is installed, else SES/Croston
  - forecast_demand(method="auto_modern") : same modern path, explicit
  - forecast_demand(method="ses"|"croston") : legacy built-ins (always available)
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

# Syntetos-Boylan: demand is "intermittent" above this average demand interval.
INTERMITTENT_ADI_THRESHOLD = 1.32


def _is_usable_dispersion(value: float) -> bool:
    """A dispersion estimate is usable only when it is a real, positive number.

    ``nan > 0`` is False, so a plain ``> 0`` test silently routes a NaN down the
    fallback branch. Every dispersion choice in this module goes through here.
    """
    return math.isfinite(value) and value > 0


@dataclass(frozen=True)
class ForecastResult:
    """Forecast summary plus the statistics the inventory engine needs."""

    method: str
    forecast: float
    demand_mean: float
    demand_std: float
    error_std: float
    bias: float
    mae: float
    n_periods: int
    is_intermittent: bool
    # One-step error pairs discarded before computing error_std/bias/mae, e.g. the
    # warm-up NaN that TSB and Croston emit in their fitted values. 0 on the
    # closed-form legacy paths, which have no warm-up row to discard.
    n_error_obs_dropped: int = 0

    @property
    def sigma_source(self) -> str:
        """Which dispersion ``to_engine_inputs`` hands the engine, and why.

        - ``forecast_error``: sigma_e was estimable and positive — the correct
          input for safety stock (Vandeput 2021, §4.2.5).
        - ``demand_std_fallback``: sigma_e was *not* estimable (too few periods,
          or NaN fitted values), so the raw demand std stands in. A wider sigma
          than the theory calls for, and the caller can now see that it happened.
        - ``unavailable``: neither is a usable dispersion estimate.

        Derived, never stored, so it cannot drift from the numbers it describes.
        """
        if _is_usable_dispersion(self.error_std):
            return "forecast_error"
        if _is_usable_dispersion(self.demand_std):
            return "demand_std_fallback"
        return "unavailable"

    def to_engine_inputs(self, periods_per_year: float = 52.0) -> dict[str, float]:
        """
        Map the forecast onto inventory-engine keyword arguments.

        Uses sigma_e (``error_std``) as ``demand_std_per_period`` — the correct
        dispersion for safety stock — falling back to the raw demand std when
        too few periods exist to estimate the forecast error. Which of the two
        was used is on the record in :attr:`sigma_source`.

        Raises when neither dispersion is a real number: substituting 0 there
        would print "no buffer needed" as a confident fact.
        """
        source = self.sigma_source
        if source == "forecast_error":
            sigma = self.error_std
        elif source == "demand_std_fallback":
            sigma = self.demand_std
        else:
            if not (math.isfinite(self.error_std) and math.isfinite(self.demand_std)):
                raise ValueError(
                    f"cannot size safety stock: dispersion is not finite "
                    f"(error_std={self.error_std!r}, demand_std={self.demand_std!r}) - "
                    "the demand history this forecast came from is not usable"
                )
            sigma = 0.0  # a genuinely flat history: measured, not missing
        return {
            "annual_demand": self.forecast * periods_per_year,
            "mean_demand_per_period": self.forecast,
            "demand_std_per_period": sigma,
        }


def _as_array(history: object) -> np.ndarray:
    arr = np.asarray(list(history), dtype=float)
    if arr.size == 0:
        raise ValueError("history is empty")
    # Before the sign check: a blank cell in the client's CSV arrives as NaN, and
    # `nan < 0` is False, so it used to sail through and poison every statistic
    # downstream. A missing period is not a demand of zero.
    if not np.all(np.isfinite(arr)):
        bad = int(np.count_nonzero(~np.isfinite(arr)))
        raise ValueError(
            f"demand history must be finite: {bad} of {arr.size} value(s) are NaN or infinite "
            "(a blank period is not a demand of zero - drop it or fill it deliberately)"
        )
    if np.any(arr < 0):
        raise ValueError("demand history cannot contain negative values")
    return arr


def _std(arr: np.ndarray) -> float:
    return float(np.std(arr, ddof=1)) if arr.size > 1 else 0.0


def _error_stats(actual: np.ndarray, fitted: np.ndarray) -> tuple[float, float, float]:
    """Return (error_std, bias, mae) for one-step-ahead forecasts."""
    errors = actual - fitted
    if errors.size == 0:
        return 0.0, 0.0, 0.0
    bias = float(np.mean(errors))
    mae = float(np.mean(np.abs(errors)))
    error_std = float(np.std(errors, ddof=1)) if errors.size > 1 else 0.0
    return error_std, bias, mae


def average_demand_interval(history: object) -> float:
    """ADI = total periods / number of periods with demand (inf if all zero)."""
    arr = _as_array(history)
    nonzero = int(np.count_nonzero(arr))
    if nonzero == 0:
        return float("inf")
    return arr.size / nonzero


def is_intermittent(history: object, threshold: float = INTERMITTENT_ADI_THRESHOLD) -> bool:
    """True when demand is lumpy enough to warrant Croston over smoothing."""
    return average_demand_interval(history) >= threshold


def moving_average(history: object, window: int = 3) -> ForecastResult:
    """Simple moving-average forecast over the last ``window`` periods."""
    arr = _as_array(history)
    if window < 1:
        raise ValueError("window must be >= 1")
    window = min(window, arr.size)

    preds = np.array([arr[t - window : t].mean() for t in range(window, arr.size)])
    error_std, bias, mae = _error_stats(arr[window:], preds)
    forecast = float(arr[-window:].mean())

    return ForecastResult(
        method="moving_average",
        forecast=forecast,
        demand_mean=float(arr.mean()),
        demand_std=_std(arr),
        error_std=error_std,
        bias=bias,
        mae=mae,
        n_periods=arr.size,
        is_intermittent=is_intermittent(arr),
    )


def simple_exponential_smoothing(history: object, alpha: float = 0.3) -> ForecastResult:
    """SES: level_t = alpha*y_t + (1-alpha)*level_{t-1}; forecast = last level."""
    arr = _as_array(history)
    if not 0 < alpha <= 1:
        raise ValueError("alpha must be in (0, 1]")

    level = float(arr[0])
    preds = []
    for t in range(1, arr.size):
        preds.append(level)  # one-step forecast for period t
        level = alpha * arr[t] + (1 - alpha) * level

    error_std, bias, mae = _error_stats(arr[1:], np.array(preds))

    return ForecastResult(
        method="ses",
        forecast=float(level),
        demand_mean=float(arr.mean()),
        demand_std=_std(arr),
        error_std=error_std,
        bias=bias,
        mae=mae,
        n_periods=arr.size,
        is_intermittent=is_intermittent(arr),
    )


def croston(history: object, alpha: float = 0.1) -> ForecastResult:
    """Croston's method for intermittent demand: forecast = size/interval."""
    arr = _as_array(history)
    if not 0 < alpha <= 1:
        raise ValueError("alpha must be in (0, 1]")

    nonzero_idx = np.flatnonzero(arr)
    if nonzero_idx.size == 0:
        return ForecastResult(
            method="croston",
            forecast=0.0,
            demand_mean=0.0,
            demand_std=0.0,
            error_std=0.0,
            bias=0.0,
            mae=0.0,
            n_periods=arr.size,
            is_intermittent=True,
        )

    size = float(arr[nonzero_idx[0]])  # demand-size estimate z
    interval = float(nonzero_idx[0] + 1)  # inter-demand interval estimate p
    since_demand = 1
    forecasts = np.empty(arr.size)

    for t in range(arr.size):
        forecasts[t] = size / interval
        if t == 0:
            continue
        if arr[t] > 0:
            size = alpha * arr[t] + (1 - alpha) * size
            interval = alpha * since_demand + (1 - alpha) * interval
            since_demand = 1
        else:
            since_demand += 1

    error_std, bias, mae = _error_stats(arr[1:], forecasts[1:])

    return ForecastResult(
        method="croston",
        forecast=float(size / interval),
        demand_mean=float(arr.mean()),
        demand_std=_std(arr),
        error_std=error_std,
        bias=bias,
        mae=mae,
        n_periods=arr.size,
        is_intermittent=True,
    )


def forecast_demand(history: object, method: str = "auto", **kwargs: float) -> ForecastResult:
    """
    Forecast demand, dispatching by method.

    method='auto' uses Croston for intermittent demand (ADI >= 1.32) and SES
    otherwise. Extra keyword args (``window``, ``alpha``) pass through.
    """
    arr = _as_array(history)
    if method == "auto":
        from src.forecasting_auto import MIN_PERIODS_STATSFORECAST, forecast_modern, statsforecast_available

        if statsforecast_available() and arr.size >= MIN_PERIODS_STATSFORECAST:
            return forecast_modern(arr, method="auto_modern")
        method = "croston" if is_intermittent(arr) else "ses"

    if method in ("auto_modern", "auto_ets", "tsb"):
        from src.forecasting_auto import forecast_modern

        return forecast_modern(arr, method=method, **kwargs)

    dispatch = {
        "moving_average": moving_average,
        "ses": simple_exponential_smoothing,
        "croston": croston,
    }
    if method not in dispatch:
        raise ValueError(f"unknown method: {method!r} (choose from {sorted(dispatch)})")
    return dispatch[method](arr, **kwargs)
