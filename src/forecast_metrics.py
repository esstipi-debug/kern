"""Forecast accuracy metrics (capability M2 — linchpin-forecast-metrics).

Pure numpy implementations with correct zero-handling and scaling. These are the
metrics the modern-forecasting skills report through; kept dependency-free so the
base install needs no statsforecast/utilsforecast (those become an optional extra).
Reference: Hyndman & Athanasopoulos (MASE/RMSSE); Vandeput (WAPE/bias).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def _arrays(actual, forecast):
    return np.asarray(actual, dtype=float), np.asarray(forecast, dtype=float)


def mae(actual, forecast) -> float:
    a, f = _arrays(actual, forecast)
    return float(np.mean(np.abs(f - a)))


def rmse(actual, forecast) -> float:
    a, f = _arrays(actual, forecast)
    return float(np.sqrt(np.mean((f - a) ** 2)))


def bias(actual, forecast) -> float:
    """Mean signed error (forecast - actual); positive = systematic over-forecast."""
    a, f = _arrays(actual, forecast)
    return float(np.mean(f - a))


def mape(actual, forecast) -> float:
    """Mean absolute percentage error, skipping zero actuals (undefined there)."""
    a, f = _arrays(actual, forecast)
    mask = a != 0
    if not mask.any():
        return float("inf")
    return float(np.mean(np.abs((f[mask] - a[mask]) / a[mask])))


def wape(actual, forecast) -> float:
    """Weighted APE = sum|error| / sum|actual| (a.k.a. ND). Robust to zeros."""
    a, f = _arrays(actual, forecast)
    denom = float(np.sum(np.abs(a)))
    if denom == 0:
        return float("inf")
    return float(np.sum(np.abs(f - a)) / denom)


def _naive_mae(train) -> float:
    t = np.asarray(train, dtype=float)
    return float(np.mean(np.abs(np.diff(t)))) if t.size > 1 else 0.0


def _naive_rmse(train) -> float:
    t = np.asarray(train, dtype=float)
    return float(np.sqrt(np.mean(np.diff(t) ** 2))) if t.size > 1 else 0.0


def mase(actual, forecast, train) -> float:
    """Mean absolute scaled error: MAE scaled by the in-sample naive MAE."""
    scale = _naive_mae(train)
    if scale == 0:
        return float("inf")
    return mae(actual, forecast) / scale


def rmsse(actual, forecast, train) -> float:
    """Root mean squared scaled error (the M5 scoring base)."""
    scale = _naive_rmse(train)
    if scale == 0:
        return float("inf")
    return rmse(actual, forecast) / scale


@dataclass(frozen=True)
class ForecastMetrics:
    mae: float
    rmse: float
    bias: float
    mape: float
    wape: float
    mase: float
    rmsse: float


def compute_metrics(actual, forecast, train=None) -> ForecastMetrics:
    """Bundle every metric. Scaled metrics (MASE/RMSSE) need ``train`` history."""
    scaled_mase = mase(actual, forecast, train) if train is not None else float("nan")
    scaled_rmsse = rmsse(actual, forecast, train) if train is not None else float("nan")
    return ForecastMetrics(
        mae=mae(actual, forecast),
        rmse=rmse(actual, forecast),
        bias=bias(actual, forecast),
        mape=mape(actual, forecast),
        wape=wape(actual, forecast),
        mase=scaled_mase,
        rmsse=scaled_rmsse,
    )
