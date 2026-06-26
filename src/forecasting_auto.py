"""Modern demand forecasting via StatsForecast (optional ``[forecast]`` extra).

Wraps Nixtla's StatsForecast for AutoETS (dense/seasonal demand) and TSB
(intermittent demand). Returns the same :class:`~src.forecasting.ForecastResult`
as the built-in SES/Croston path so policies and safety stock need no changes.

Install: ``pip install -e ".[forecast]"``. When StatsForecast is absent or the
history is too short, :func:`forecast_modern` falls back to ``forecast_demand``.
"""

from __future__ import annotations

import importlib.util
from typing import Any

import numpy as np
import pandas as pd

from src.forecasting import ForecastResult, forecast_demand, is_intermittent

# StatsForecast needs a minimum history; below this we keep SES/Croston.
MIN_PERIODS_STATSFORECAST = 10

_MODERN_METHODS = frozenset({"auto_modern", "auto_ets", "tsb"})


def statsforecast_available() -> bool:
    """True when the optional ``statsforecast`` package is importable."""
    return importlib.util.find_spec("statsforecast") is not None


def history_to_frame(
    history: object,
    *,
    unique_id: str = "series",
    freq: str = "W",
) -> pd.DataFrame:
    """Convert a demand vector to Nixtla panel format (``unique_id``, ``ds``, ``y``)."""
    arr = np.asarray(list(history), dtype=float)
    if arr.size == 0:
        raise ValueError("history is empty")
    if np.any(arr < 0):
        raise ValueError("demand history cannot contain negative values")
    return pd.DataFrame(
        {
            "unique_id": unique_id,
            "ds": pd.date_range("2000-01-03", periods=arr.size, freq=freq),
            "y": arr,
        }
    )


def _season_length(n_periods: int, season_length: int | None) -> int:
    if season_length is not None:
        return max(1, season_length)
    return max(1, min(52, n_periods // 2))


def _resolve_route(method: str, intermittent: bool) -> str:
    if method in ("auto_modern", "auto"):
        return "tsb" if intermittent else "auto_ets"
    if method not in _MODERN_METHODS:
        raise ValueError(f"unknown modern method: {method!r}")
    return method


def _model_for_route(route: str, season_length: int):
    from statsforecast.models import AutoETS, TSB

    if route == "auto_ets":
        return "AutoETS", [AutoETS(season_length=season_length)]
    if route == "tsb":
        return "TSB", [TSB(alpha_d=0.2, alpha_p=0.2)]
    raise ValueError(f"unknown route: {route!r}")


def _error_stats_from_fitted(fitted: pd.DataFrame, model_col: str) -> tuple[float, float, float]:
    """Return (error_std, bias, mae) from in-sample one-step fitted values."""
    actual = fitted["y"].to_numpy(dtype=float)
    pred = fitted[model_col].to_numpy(dtype=float)
    if actual.size == 0:
        return 0.0, 0.0, 0.0
    errors = actual - pred
    bias = float(np.mean(errors))
    mae = float(np.mean(np.abs(errors)))
    error_std = float(np.std(errors, ddof=1)) if errors.size > 1 else 0.0
    return error_std, bias, mae


def _std(arr: np.ndarray) -> float:
    return float(np.std(arr, ddof=1)) if arr.size > 1 else 0.0


def _legacy_fallback(arr: np.ndarray, method: str) -> ForecastResult:
    legacy = "auto" if method in ("auto_modern", "auto") else "croston" if method == "tsb" else "ses"
    return forecast_demand(arr, method=legacy)


def forecast_modern(
    history: object,
    method: str = "auto_modern",
    *,
    season_length: int | None = None,
    freq: str = "W",
    unique_id: str = "series",
) -> ForecastResult:
    """
    Forecast with StatsForecast when available; otherwise delegate to SES/Croston.

    Methods:
      - ``auto_modern`` / ``auto``: TSB for intermittent demand (ADI >= 1.32),
        AutoETS otherwise.
      - ``auto_ets``: force AutoETS.
      - ``tsb``: force TSB (intermittent specialist).
    """
    arr = np.asarray(list(history), dtype=float)
    if arr.size == 0:
        raise ValueError("history is empty")
    if np.any(arr < 0):
        raise ValueError("demand history cannot contain negative values")

    intermittent = is_intermittent(arr)
    route = _resolve_route(method, intermittent)

    if not statsforecast_available() or arr.size < MIN_PERIODS_STATSFORECAST:
        return _legacy_fallback(arr, method)

    from statsforecast import StatsForecast

    slen = _season_length(arr.size, season_length)
    model_col, models = _model_for_route(route, slen)
    panel = history_to_frame(arr, unique_id=unique_id, freq=freq)

    sf = StatsForecast(models=models, freq=freq, n_jobs=1)
    fc = sf.forecast(df=panel, h=1, fitted=True)
    fitted = sf.forecast_fitted_values()

    point = float(fc[model_col].iloc[0])
    error_std, bias, mae = _error_stats_from_fitted(fitted, model_col)

    method_label = {
        "AutoETS": "auto_ets",
        "TSB": "tsb",
    }.get(model_col, model_col.lower())

    return ForecastResult(
        method=method_label,
        forecast=point,
        demand_mean=float(arr.mean()),
        demand_std=_std(arr),
        error_std=error_std,
        bias=bias,
        mae=mae,
        n_periods=arr.size,
        is_intermittent=intermittent,
    )


def forecast_portfolio(
    demand_df: pd.DataFrame,
    *,
    product_col: str = "product_id",
    date_col: str = "date",
    qty_col: str = "quantity",
    method: str = "auto_modern",
    **kwargs: Any,
) -> dict[str, ForecastResult]:
    """Forecast every SKU in a long-format demand table."""
    required = {product_col, date_col, qty_col}
    missing = required - set(demand_df.columns)
    if missing:
        raise ValueError(f"demand_df missing columns: {sorted(missing)}")

    out: dict[str, ForecastResult] = {}
    for product_id, group in demand_df.groupby(product_col, sort=True):
        series = (
            group.sort_values(date_col)[qty_col]
            .astype(float)
            .to_numpy()
        )
        out[str(product_id)] = forecast_modern(
            series,
            method=method,
            unique_id=str(product_id),
            **kwargs,
        )
    return out
