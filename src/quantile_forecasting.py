"""Bandas de pronostico por cuantiles, sin supuesto normal (Pinball / WSPL).

La banda que ``forecast_job`` publicaba era ``punto +/- z(0,90) * sigma``: simetrica por
construccion. La demanda real no lo es -- sesgada a la derecha, con ceros estructurales en
los SKU lentos (Vandeput 2021; M5 Uncertainty, Makridakis et al. 2022, cuyo hallazgo es que
los metodos no parametricos le ganaron a los supuestos normal/log-normal en colas pesadas).

Dos estimadores, uno por forma de demanda:
  - **dos partes** (``two_part_quantiles``): para intermitentes. Separa CUANDO de CUANTO --
    la probabilidad de que haya venta, por la distribucion empirica de los tamanos cuando
    la hay. Es el modelo del ganador de M5 Uncertainty.
  - **residuos empiricos** (``empirical_quantile_forecast``): para demanda regular. Los
    cuantiles de los errores one-step del propio metodo, sumados al punto.

``quantile_band`` elige por la forma y se NIEGA (``None``) cuando no hay historia para
estimar: el llamador conserva la banda normal y lo dice. Nunca un cuantil inventado.

Este modulo NO dimensiona stock de seguridad: la banda se publica y se puntua. Llevarla al
buffer cambia los numeros que el cliente compra y se mide aparte, en el marcador.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np

from src.forecasting import ForecastResult, _as_array, forecast_demand

#: Cuantiles que publica la banda.
BAND_QUANTILES: tuple[float, float, float] = (0.1, 0.5, 0.9)

#: Metodos con que puede salir una banda. ``normal`` es la de antes (punto +/- z*sigma);
#: ``unavailable`` es que no hubo dispersion para dibujar ninguna.
BAND_METHODS: frozenset[str] = frozenset(
    {"two_part", "residual_empirical", "normal", "unavailable"}
)

#: Ventas minimas para tener una distribucion de tamanos. Con menos, el P90 de dos partes
#: seria casi siempre la venta mas grande vista, o sea un maximo disfrazado de cuantil.
MIN_NONZERO_FOR_TWO_PART = 4

#: Residuos one-step minimos para un cuantil empirico. Con menos, P10 y P90 son de hecho
#: el minimo y el maximo de la muestra.
MIN_RESIDUALS_FOR_EMPIRICAL = 8


def pinball_loss(
    actual: Sequence[float] | np.ndarray,
    predicted: Sequence[float] | np.ndarray | float,
    quantile: float,
) -> float:
    """Pinball (quantile) loss promedio para el cuantil ``quantile``.

        L_q(y, yhat) = max(q * (y - yhat), (q - 1) * (y - yhat))

    Subestimar se cobra por ``q``, sobrestimar por ``1 - q``; con ``q = 0,5`` es la mitad
    del error absoluto.
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
    loss = np.maximum(quantile * errors, (quantile - 1.0) * errors)
    return float(np.mean(loss))


def weighted_scaled_pinball_loss(
    actual: Sequence[float] | np.ndarray,
    predicted: Sequence[float] | np.ndarray | float,
    quantile: float,
    in_sample_history: Sequence[float] | np.ndarray,
    weight: float = 1.0,
) -> float:
    """Weighted Scaled Pinball Loss (M5 Uncertainty).

    La escala es la raiz del error cuadratico medio del naive one-step dentro de la
    muestra, ``sqrt(mean(diff(history)^2))``; ``WSPL = weight * pinball / escala``.
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
    """Fuerza ``q_i < q_j => y_i <= y_j`` (reordenamiento monotono), en el orden dado."""
    pairs = sorted(zip(quantiles, forecast_values), key=lambda p: p[0])
    monotone_vals = []
    current_max = -float("inf")
    for _, v in pairs:
        current_max = max(current_max, v)
        monotone_vals.append(current_max)
    mapping = {p[0]: monotone_vals[i] for i, p in enumerate(pairs)}
    return tuple(mapping[q] for q in quantiles)


@dataclass(frozen=True)
class QuantileForecastResult:
    """Pronostico multi-cuantil por residuos empiricos."""

    quantiles: tuple[float, ...]
    forecasts: dict[float, float]
    median: float
    lower_band: float
    upper_band: float
    method: str
    n_periods: int


def empirical_quantile_forecast(
    history: Sequence[float] | np.ndarray,
    quantiles: Sequence[float] = BAND_QUANTILES,
    *,
    base_method: str = "auto",
) -> QuantileForecastResult:
    """Cuantiles por la distribucion empirica de los residuos one-step.

    1. Pronostico puntual con ``forecast_demand(method=base_method)``.
    2. Residuos ``actual - fitted``: en este repo ``errors`` es ``fitted - actual``
       (``forecasting._error_stats``), asi que el residuo es ``-errors``.
    3. ``yhat_q = max(0, punto + percentil_q(residuo))``, reordenado monotono.

    Con menos de tres residuos cae a los percentiles de la demanda cruda, y el ``method``
    lo dice (``demand_empirical``): es la dispersion del nivel, no del error.
    """
    arr = _as_array(history)
    q_tuple = tuple(sorted(float(q) for q in quantiles))
    for q in q_tuple:
        if not (0.0 < q < 1.0):
            raise ValueError(f"quantiles must be in (0, 1), got {q!r}")

    base_res = forecast_demand(arr, method=base_method)
    errors = np.asarray(base_res.errors, dtype=float)

    if errors.size >= 3:
        residuals = -errors
        raw = [max(0.0, float(base_res.forecast + np.percentile(residuals, q * 100.0)))
               for q in q_tuple]
        method = f"residual_empirical({base_res.method})"
    else:
        raw = [max(0.0, float(np.percentile(arr, q * 100.0))) for q in q_tuple]
        method = "demand_empirical"

    adjusted = prevent_quantile_crossing(q_tuple, raw)
    forecast_dict = dict(zip(q_tuple, adjusted))
    return QuantileForecastResult(
        quantiles=q_tuple,
        forecasts=forecast_dict,
        median=forecast_dict.get(0.5, float(np.median(adjusted))),
        lower_band=adjusted[0],
        upper_band=adjusted[-1],
        method=method,
        n_periods=int(arr.size),
    )


def two_part_quantiles(
    history: Sequence[float] | np.ndarray,
    quantiles: Sequence[float] = BAND_QUANTILES,
) -> dict[float, float] | None:
    """Cuantiles de la mezcla "hay venta o no" x "cuanto, cuando hay".

    Con ``p`` = fraccion de periodos con venta y ``F`` la distribucion empirica de los
    tamanos, ``P(D <= x) = (1 - p) + p * F(x)`` para ``x >= 0``. Invertida:

      - ``q <= 1 - p``  ->  0 (el cuantil cae en la masa de ceros);
      - si no           ->  ``F^-1((q - (1 - p)) / p)``.

    Monotona por construccion. ``None`` con menos de :data:`MIN_NONZERO_FOR_TWO_PART`
    ventas: no hay distribucion de tamanos que citar.
    """
    arr = _as_array(history)
    sizes = arr[arr > 0]
    if sizes.size < MIN_NONZERO_FOR_TWO_PART:
        return None
    p = sizes.size / arr.size
    out: dict[float, float] = {}
    for q in sorted(float(v) for v in quantiles):
        if not (0.0 < q < 1.0):
            raise ValueError(f"quantiles must be in (0, 1), got {q!r}")
        zero_mass = 1.0 - p
        out[q] = 0.0 if q <= zero_mass else float(np.quantile(sizes, (q - zero_mass) / p))
    return out


@dataclass(frozen=True)
class QuantileBand:
    """Una banda P10/P50/P90 y con que metodo se dibujo (uno de :data:`BAND_METHODS`)."""

    p10: float
    p50: float
    p90: float
    method: str


def quantile_band(history: Sequence[float] | np.ndarray, fc: ForecastResult) -> QuantileBand | None:
    """La banda sin supuesto normal para esta serie, o ``None`` si no hay como estimarla.

    ``fc`` es el pronostico YA ajustado sobre ``history`` (no se vuelve a ajustar: con
    AutoETS eso es lo caro). Su ``is_intermittent`` elige la forma -- la misma regla que
    ya separa Croston/TSB de la suavizacion --: intermitente -> dos partes; regular ->
    cuantiles de sus residuos one-step sumados al punto.
    """
    lo, mid, hi = BAND_QUANTILES
    if fc.is_intermittent:
        q = two_part_quantiles(history, BAND_QUANTILES)
        if q is None:
            return None
        return QuantileBand(p10=q[lo], p50=q[mid], p90=q[hi], method="two_part")

    errors = np.asarray(fc.errors, dtype=float)
    if errors.size < MIN_RESIDUALS_FOR_EMPIRICAL:
        return None
    raw = [max(0.0, float(fc.forecast + np.percentile(-errors, q * 100.0)))
           for q in BAND_QUANTILES]
    p10, p50, p90 = prevent_quantile_crossing(BAND_QUANTILES, raw)
    return QuantileBand(p10=p10, p50=p50, p90=p90, method="residual_empirical")


def band_score(actual: Sequence[float], band: QuantileBand) -> tuple[float, float]:
    """(pinball promedio sobre P10/P50/P90, fraccion de ``actual`` dentro de [P10, P90]).

    En las unidades del SKU: sirve para comparar dos bandas del MISMO SKU, no para
    promediar SKUs de escalas distintas.
    """
    y = np.asarray(actual, dtype=float)
    lo, mid, hi = BAND_QUANTILES
    pinball = float(np.mean([
        pinball_loss(y, band.p10, lo),
        pinball_loss(y, band.p50, mid),
        pinball_loss(y, band.p90, hi),
    ]))
    coverage = float(np.mean((y >= band.p10) & (y <= band.p90)))
    return pinball, coverage
