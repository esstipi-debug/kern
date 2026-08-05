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


def _seasonal_diff(train, season_length: int) -> np.ndarray:
    """In-sample naive errors: ``y_t - y_{t-m}``. ``m=1`` is the plain lag-1 naive.

    On seasonal demand the lag-1 naive is a straw man, so scaling against it
    flatters every model and inflates the reported value-add (Vandeput 2021,
    sec 27.2). Passing the real season length makes the benchmark the seasonal
    naive - "same period last cycle" - which is the honest bar to clear.
    """
    t = np.asarray(train, dtype=float)
    m = int(season_length)
    if m < 1:
        raise ValueError(f"season_length must be >= 1, got {season_length!r}")
    if t.size <= m:
        return np.empty(0, dtype=float)
    return t[m:] - t[:-m]


def _naive_mae(train, season_length: int = 1) -> float:
    d = _seasonal_diff(train, season_length)
    return float(np.mean(np.abs(d))) if d.size else 0.0


def _naive_rmse(train, season_length: int = 1) -> float:
    d = _seasonal_diff(train, season_length)
    return float(np.sqrt(np.mean(d**2))) if d.size else 0.0


def mase(actual, forecast, train, season_length: int = 1) -> float:
    """Mean absolute scaled error: MAE scaled by the in-sample (seasonal) naive MAE.

    MASE is MAE-based, so *optimising* it targets the demand **median** - which is
    0 for intermittent demand (Vandeput 2021, sec 2.6). Report it; gate on
    :func:`rmsse` too. See ``jobs/forecast_job.py`` for how the two are combined.
    """
    scale = _naive_mae(train, season_length)
    if scale == 0:
        return float("inf")
    return mae(actual, forecast) / scale


def rmsse(actual, forecast, train, season_length: int = 1) -> float:
    """Root mean squared scaled error (the M5 scoring base), vs the (seasonal) naive.

    RMSE-based, so it targets the demand **mean** and punishes the large misses a
    median-seeking metric forgives. This is the one to optimise/gate on.
    """
    scale = _naive_rmse(train, season_length)
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
    #: Season length the naive benchmark used: 1 = plain lag-1 naive, >1 = seasonal
    #: naive. On the record because it changes what MASE/RMSSE (and any value-add
    #: derived from them) actually mean.
    season_length: int = 1
    #: How many in-sample naive errors the scale was measured over. ``0`` means the train
    #: slice was shorter than the season: there is **no benchmark**, and MASE/RMSSE come
    #: back as inf for want of one.
    naive_obs: int = 0
    #: The naive benchmark's own error. ``0.0`` with ``naive_obs > 0`` is not a missing
    #: measurement - it means the naive forecast is *exact* on this series, so nothing can
    #: beat it. Distinguishing that from "no benchmark" is the caller's job and it matters:
    #: one is a verdict, the other is an absence of one.
    naive_mae: float = float("nan")
    naive_rmse: float = float("nan")


def compute_metrics(actual, forecast, train=None, season_length: int = 1) -> ForecastMetrics:
    """Bundle every metric. Scaled metrics (MASE/RMSSE) need ``train`` history.

    ``season_length`` picks the naive benchmark: 1 (default) keeps the plain lag-1
    naive; pass the real season length for seasonal demand, or the reported
    value-add is measured against a benchmark nobody should be proud of beating.
    """
    scaled_mase = mase(actual, forecast, train, season_length) if train is not None else float("nan")
    scaled_rmsse = (
        rmsse(actual, forecast, train, season_length) if train is not None else float("nan")
    )
    return ForecastMetrics(
        mae=mae(actual, forecast),
        rmse=rmse(actual, forecast),
        bias=bias(actual, forecast),
        mape=mape(actual, forecast),
        wape=wape(actual, forecast),
        mase=scaled_mase,
        rmsse=scaled_rmsse,
        season_length=int(season_length),
        naive_obs=int(_seasonal_diff(train, season_length).size) if train is not None else 0,
        naive_mae=_naive_mae(train, season_length) if train is not None else float("nan"),
        naive_rmse=_naive_rmse(train, season_length) if train is not None else float("nan"),
    )
