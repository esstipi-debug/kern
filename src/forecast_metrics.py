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


def _naive_errors(train, season_length: int) -> "np.ndarray":
    """Errores del naive de referencia: cada punto contra el de ``season_length`` atras.

    ``season_length=1`` es el naive lag-1 de siempre (``np.diff``). Con un m > 1 el
    referente pasa a ser "el mismo periodo del ciclo anterior", que es lo que MASE y
    RMSSE tienen que escalar cuando la demanda es estacional: contra un lag-1, un
    pronostico plano sobre una serie que alterna parece excelente, porque el
    benchmark esta oscilando de punta a punta todos los periodos. Vandeput (2021,
    sec 27.2) es explicito: en demanda estacional el benchmark debe ser estacional.
    """
    t = np.asarray(train, dtype=float)
    m = max(1, int(season_length))
    if t.size <= m:
        return np.asarray([], dtype=float)
    return t[m:] - t[:-m]


def _naive_mae(train, season_length: int = 1) -> float:
    e = _naive_errors(train, season_length)
    return float(np.mean(np.abs(e))) if e.size else 0.0


def _naive_rmse(train, season_length: int = 1) -> float:
    e = _naive_errors(train, season_length)
    return float(np.sqrt(np.mean(e ** 2))) if e.size else 0.0


def mase(actual, forecast, train, season_length: int = 1) -> float:
    """Mean absolute scaled error: MAE scaled by the in-sample naive MAE.

    ``season_length`` elige el naive contra el que se escala (1 = lag-1).
    """
    scale = _naive_mae(train, season_length)
    if scale == 0:
        return float("inf")
    return mae(actual, forecast) / scale


def rmsse(actual, forecast, train, season_length: int = 1) -> float:
    """Root mean squared scaled error (the M5 scoring base)."""
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
    #: El naive de referencia, expuesto para que el consumidor pueda distinguir
    #: "el naive es perfecto aca" (escala 0 -> MASE inf, y es un VEREDICTO) de
    #: "no hubo con que medir" (sin observaciones, que es una AUSENCIA). Las dos
    #: producen MASE infinito y significan cosas opuestas.
    naive_mae: float = float("nan")
    naive_rmse: float = float("nan")
    naive_obs: int = 0


def compute_metrics(actual, forecast, train=None, season_length: int = 1) -> ForecastMetrics:
    """Bundle every metric. Scaled metrics (MASE/RMSSE) need ``train`` history.

    ``season_length`` viaja hasta el naive de referencia. El default 1 conserva
    exactamente el comportamiento anterior; quien detecte estacionalidad
    (``src.seasonality.detect_season_length``) pasa el m y las escaladas dejan de
    compararse contra un hombre de paja.
    """
    scaled_mase = (
        mase(actual, forecast, train, season_length) if train is not None else float("nan")
    )
    scaled_rmsse = (
        rmsse(actual, forecast, train, season_length) if train is not None else float("nan")
    )
    _naive_e = _naive_errors(train, season_length) if train is not None else None
    return ForecastMetrics(
        mae=mae(actual, forecast),
        rmse=rmse(actual, forecast),
        bias=bias(actual, forecast),
        mape=mape(actual, forecast),
        wape=wape(actual, forecast),
        mase=scaled_mase,
        rmsse=scaled_rmsse,
        naive_mae=(_naive_mae(train, season_length) if train is not None else float("nan")),
        naive_rmse=(_naive_rmse(train, season_length) if train is not None else float("nan")),
        naive_obs=(0 if _naive_e is None else int(_naive_e.size)),
    )
