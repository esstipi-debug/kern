"""Probabilistic Quantile Forecasting and Asymmetric Loss (Pinball / WSPL).

Provides distribution-free quantile forecasting (P10, P50, P90) to size safety buffers
without assuming symmetric Gaussian demand errors. In supply chain environments, demand
distributions are frequently right-skewed, lumpy, or non-normal (Vandeput 2021; M5
Uncertainty Competition, Makridakis et al. 2022).

Features:
  - Vectorized Pinball Loss (Quantile Loss) calculation.
  - Weighted Scaled Pinball Loss (WSPL) from the M5 Uncertainty competition.
  - Quantile crossing prevention via monotonic sorting / isotonic projection.
  - Empirical and residual-quantile forecasting (built-in, zero dependencies).
  - Optional GBDT/LightGBM quantile regression with graceful degradation.
  - Asymmetric buffer sizing: upside reserve (P90 - P50) vs downside slack (P50 - P10).
"""

from __future__ import annotations

import importlib.util
import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np

from src.forecasting import _as_array, forecast_demand


def lightgbm_available() -> bool:
    """Return True when `lightgbm` is importable in the current environment."""
    return importlib.util.find_spec("lightgbm") is not None


def mlforecast_available() -> bool:
    """Return True when `mlforecast` is importable in the current environment."""
    return importlib.util.find_spec("mlforecast") is not None


def pinball_loss(
    actual: Sequence[float] | np.ndarray,
    predicted: Sequence[float] | np.ndarray | float,
    quantile: float,
) -> float:
    """Compute the asymmetric Pinball Loss (Quantile Loss) for a given quantile.

    Mathematical formulation:
        L_q(y, \\hat{y}_q) = max(q * (y - \\hat{y}_q), (1 - q) * (\\hat{y}_q - y))
                           = (y - \\hat{y}_q) * (q - I(y < \\hat{y}_q))

    Args:
        actual: Observed actual demand series y.
        predicted: Quantile predictions \\hat{y}_q (scalar or sequence).
        quantile: Quantile target q in (0, 1), e.g. 0.1, 0.5, 0.9.

    Returns:
        Mean pinball loss over all observations.

    Raises:
        ValueError: If quantile is not in (0, 1) or arrays have mismatched dimensions.
    """
    if not (0.0 < quantile < 1.0):
        raise ValueError(f"quantile must be strictly between 0 and 1, got {quantile!r}")

    y_true = np.asarray(actual, dtype=float)
    if y_true.size == 0:
        raise ValueError("actual series cannot be empty")

    y_pred = np.asarray(predicted, dtype=float)
    if y_pred.ndim == 0:
        y_pred = np.full_like(y_true, float(y_pred))
    elif y_pred.shape != y_true.shape:
        raise ValueError(
            f"shape mismatch: actual {y_true.shape} vs predicted {y_pred.shape}"
        )

    errors = y_true - y_pred
    # Vectorized piecewise loss
    loss = np.maximum(quantile * errors, (quantile - 1.0) * errors)
    return float(np.mean(loss))


def weighted_scaled_pinball_loss(
    actual: Sequence[float] | np.ndarray,
    predicted: Sequence[float] | np.ndarray | float,
    quantile: float,
    in_sample_history: Sequence[float] | np.ndarray,
    weight: float = 1.0,
) -> float:
    """Compute the Weighted Scaled Pinball Loss (WSPL) from the M5 Uncertainty benchmark.

    The scale s is the in-sample mean squared error of a one-step naive forecast:
        s = (1 / (n - 1)) * sum_{t=2}^n (y_t - y_{t-1})^2

    Scaled Loss:
        SPL = PinballLoss(actual, predicted, quantile) / sqrt(s)
        WSPL = weight * SPL

    Args:
        actual: Out-of-sample actual values.
        predicted: Quantile predictions for out-of-sample periods.
        quantile: Quantile q in (0, 1).
        in_sample_history: Training series used to calculate baseline volatility scale.
        weight: Aggregation weight (default 1.0).

    Returns:
        WSPL score.
    """
    if weight < 0:
        raise ValueError(f"weight must be non-negative, got {weight!r}")

    hist = _as_array(in_sample_history)
    if hist.size < 2:
        scale = 1.0
    else:
        diffs = np.diff(hist)
        scale = float(np.mean(diffs**2))
        scale = math.sqrt(scale) if scale > 0 else 1.0

    raw_loss = pinball_loss(actual, predicted, quantile)
    return float(weight * (raw_loss / scale))


def prevent_quantile_crossing(
    quantiles: Sequence[float],
    forecast_values: Sequence[float],
) -> tuple[float, ...]:
    """Ensure monotonicity of quantile predictions (q_i < q_j => y_i <= y_j).

    Sorts predictions to guarantee that higher quantiles never fall below lower
    quantiles (rearrangement operator / isotonic projection).
    """
    pairs = sorted(zip(quantiles, forecast_values), key=lambda p: p[0])
    ordered_vals = [p[1] for p in pairs]
    # Enforce non-decreasing values
    monotone_vals = []
    current_max = -float("inf")
    for v in ordered_vals:
        current_max = max(current_max, v)
        monotone_vals.append(current_max)

    # Map back to original quantile order
    mapping = {p[0]: monotone_vals[i] for i, p in enumerate(pairs)}
    return tuple(mapping[q] for q in quantiles)


@dataclass(frozen=True)
class QuantileForecastResult:
    """Structured result of multi-quantile probabilistic demand forecasting."""

    quantiles: tuple[float, ...]
    forecasts: dict[float, float]
    median: float
    lower_band: float
    upper_band: float
    asymmetric_upside_buffer: float
    asymmetric_downside_slack: float
    pinball_losses: dict[float, float]
    method: str
    n_periods: int
    fallback_used: bool = False

    def to_buffer_inputs(self) -> dict[str, float]:
        """Convert quantile estimates directly into inventory buffer dimensions.

        Avoids assuming Gaussian symmetry (z * sigma):
          - `median_demand`: P50 point forecast
          - `upside_safety_buffer`: P90 - P50 (protection against demand surges)
          - `downside_slack`: P50 - P10 (overstock risk exposure)
        """
        return {
            "median_demand": self.median,
            "upside_safety_buffer": self.asymmetric_upside_buffer,
            "downside_slack": self.asymmetric_downside_slack,
            "p10": self.lower_band,
            "p50": self.median,
            "p90": self.upper_band,
        }


def empirical_quantile_forecast(
    history: Sequence[float] | np.ndarray,
    quantiles: Sequence[float] = (0.1, 0.5, 0.9),
    *,
    base_method: str = "auto",
) -> QuantileForecastResult:
    """Compute quantile forecasts via residual-empirical distribution.

    Algorithm:
      1. Generates one-step base forecast using `forecast_demand(method=base_method)`.
      2. Extracts one-step-ahead residuals: e_t = y_t - \\hat{y}_t.
      3. Calculates empirical percentiles of the residuals: Q_q(e).
      4. Forecasts each quantile: \\hat{y}_q = max(0, \\hat{y} + Q_q(e)).
      5. Enforces monotonic sorting to eliminate quantile crossing.

    Zero external dependencies, exact, fully deterministic.
    """
    arr = _as_array(history)
    n = arr.size
    q_tuple = tuple(sorted(float(q) for q in quantiles))
    for q in q_tuple:
        if not (0.0 < q < 1.0):
            raise ValueError(f"quantiles must be in (0, 1), got {q!r}")

    # Generate baseline point forecast and errors
    base_res = forecast_demand(arr, method=base_method)
    errors = np.asarray(base_res.errors, dtype=float)

    raw_preds: list[float] = []
    if errors.size >= 3:
        # Residual empirical percentiles
        # In Kern, errors = fitted - actual => actual = fitted - errors.
        # So actual - fitted = -errors.
        residual_spreads = -errors
        for q in q_tuple:
            q_val = float(np.percentile(residual_spreads, q * 100.0))
            pred = max(0.0, float(base_res.forecast + q_val))
            raw_preds.append(pred)
    else:
        # Sparse history: fallback to direct empirical percentiles of raw demand
        for q in q_tuple:
            pred = max(0.0, float(np.percentile(arr, q * 100.0)))
            raw_preds.append(pred)

    # Monotonicity projection
    adjusted_preds = prevent_quantile_crossing(q_tuple, raw_preds)
    forecast_dict = dict(zip(q_tuple, adjusted_preds))

    # Evaluate in-sample pinball losses
    losses: dict[float, float] = {}
    for q in q_tuple:
        losses[q] = pinball_loss(arr, forecast_dict[q], q)

    p10 = forecast_dict.get(0.1, adjusted_preds[0])
    p50 = forecast_dict.get(0.5, float(np.median(adjusted_preds)))
    p90 = forecast_dict.get(0.9, adjusted_preds[-1])

    return QuantileForecastResult(
        quantiles=q_tuple,
        forecasts=forecast_dict,
        median=p50,
        lower_band=p10,
        upper_band=p90,
        asymmetric_upside_buffer=max(0.0, p90 - p50),
        asymmetric_downside_slack=max(0.0, p50 - p10),
        pinball_losses=losses,
        method=f"residual_empirical({base_res.method})",
        n_periods=n,
        fallback_used=False,
    )


def lightgbm_quantile_forecast(
    history: Sequence[float] | np.ndarray,
    quantiles: Sequence[float] = (0.1, 0.5, 0.9),
    *,
    lags: Sequence[int] = (1, 2, 3),
) -> QuantileForecastResult:
    """Quantile regression using LightGBM if installed, with automatic empirical fallback.

    When `lightgbm` is available:
      Trains separate models minimizing pinball loss (`objective='quantile'`, `alpha=q`)
      over lagged autoregressive features.
    When absent:
      Degrades gracefully to `empirical_quantile_forecast`, marking `fallback_used=True`.
    """
    arr = _as_array(history)
    n = arr.size
    q_tuple = tuple(sorted(float(q) for q in quantiles))

    if not lightgbm_available() or n < (max(lags) + 5):
        base_res = empirical_quantile_forecast(arr, quantiles=q_tuple)
        return QuantileForecastResult(
            quantiles=base_res.quantiles,
            forecasts=base_res.forecasts,
            median=base_res.median,
            lower_band=base_res.lower_band,
            upper_band=base_res.upper_band,
            asymmetric_upside_buffer=base_res.asymmetric_upside_buffer,
            asymmetric_downside_slack=base_res.asymmetric_downside_slack,
            pinball_losses=base_res.pinball_losses,
            method="empirical_fallback(no_lightgbm_or_short_series)",
            n_periods=n,
            fallback_used=True,
        )

    import lightgbm as lgb  # type: ignore

    max_lag = max(lags)
    X_rows = []
    y_vals = []
    for t in range(max_lag, n):
        X_rows.append([arr[t - lag] for lag in lags])
        y_vals.append(arr[t])

    X = np.asarray(X_rows, dtype=float)
    y = np.asarray(y_vals, dtype=float)
    x_next = np.asarray([[arr[n - lag] for lag in lags]], dtype=float)

    raw_preds: list[float] = []
    losses: dict[float, float] = {}

    for q in q_tuple:
        params = {
            "objective": "quantile",
            "alpha": q,
            "verbosity": -1,
            "min_child_samples": 2,
            "n_estimators": 50,
            "learning_rate": 0.05,
            "random_state": 42,
        }
        model = lgb.LGBMRegressor(**params)
        model.fit(X, y)
        pred_val = max(0.0, float(model.predict(x_next)[0]))
        raw_preds.append(pred_val)

        # In-sample fitted loss
        fitted = model.predict(X)
        losses[q] = pinball_loss(y, fitted, q)

    adjusted_preds = prevent_quantile_crossing(q_tuple, raw_preds)
    forecast_dict = dict(zip(q_tuple, adjusted_preds))

    p10 = forecast_dict.get(0.1, adjusted_preds[0])
    p50 = forecast_dict.get(0.5, float(np.median(adjusted_preds)))
    p90 = forecast_dict.get(0.9, adjusted_preds[-1])

    return QuantileForecastResult(
        quantiles=q_tuple,
        forecasts=forecast_dict,
        median=p50,
        lower_band=p10,
        upper_band=p90,
        asymmetric_upside_buffer=max(0.0, p90 - p50),
        asymmetric_downside_slack=max(0.0, p50 - p10),
        pinball_losses=losses,
        method="lightgbm_quantile",
        n_periods=n,
        fallback_used=False,
    )
