"""Forecast accuracy metrics (capability M2 — linchpin-forecast-metrics).

Pure numpy implementations with correct zero-handling and scaling. These are the
metrics the modern-forecasting skills report through; kept dependency-free so the
base install needs no statsforecast/utilsforecast (those become an optional extra).
Reference: Hyndman & Athanasopoulos (MASE/RMSSE); Vandeput (WAPE/bias); Liu (2022,
eq. 8.16 - tracking signal, reported from Chopra & Meindl 2016).
"""

from __future__ import annotations

import math
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


#: Banda del tracking signal: fuera de +-6 el pronostico esta SESGADO. Liu (2022,
#: p.312, ec. 8.16) la publica y se la atribuye a Chopra & Meindl (2016); el numero
#: no es de Liu y no se le acredita a Liu.
TRACKING_SIGNAL_BAND = 6.0

#: Los cuatro veredictos de :func:`bias_verdict`. ``undefined`` NO es "sin sesgo":
#: es "no habia error con que medirlo" (MAD cero o serie vacia), y publicarlo como
#: 0 diria lo contrario de lo que se sabe.
BIAS_VERDICTS = ("in_band", "over", "under", "undefined")


def tracking_signal_series(actual, forecast) -> "np.ndarray":
    """``TS_t = CFE_t / MAD_t`` - el error acumulado contra su propia dispersion.

    La pregunta que contesta no es "cuanto me equivoque" (eso es MAE/RMSE) sino
    **"me equivoque siempre para el mismo lado?"**. ``bias()`` da el promedio con
    signo, y un promedio chico puede esconder un error enorme si los periodos se
    compensan; el TS lo normaliza contra la MAD acumulada, asi que el mismo sesgo
    absoluto pesa mas cuando los errores son chicos y consistentes. Dos SKUs con
    ``bias = +3`` no son el mismo problema si uno tiene MAD 1 y el otro MAD 40.

    **Convencion de signo, y no es la del libro.** Aca el error es ``f - a``
    (positivo = el metodo corre ALTO), la misma de :func:`bias`. Liu escribe
    ``E = A - F`` (ec. 8.11), asi que su TS tiene el signo opuesto al de este
    modulo: un TS positivo aca es sobre-pronostico, alla es sub-pronostico. Se
    elige la convencion local porque ``bias`` ya se publica con ella y dos
    superficies que dicen "+" con signos contrarios es exactamente el defecto que
    este repo paga una vez.

    **``NaN`` donde la MAD es cero**, no 0.0: un pronostico exacto no tiene error
    acumulado con que dividir, y el cociente no existe. Devolver 0 ahi seria
    publicar "perfectamente sin sesgo" sobre una serie donde no se midio nada.

    Devuelve un valor por periodo (el TS es una serie, no un numero: el veredicto
    es sobre el ultimo punto, pero la forma de la serie dice si el sesgo aparecio
    de golpe o se viene acumulando).
    """
    a, f = _arrays(actual, forecast)
    return tracking_signal_series_from_errors(f - a)


def tracking_signal_series_from_errors(errors) -> "np.ndarray":
    """:func:`tracking_signal_series` sobre una serie de errores YA calculada.

    Existe porque el motor no siempre tiene ``actual``/``forecast`` a mano: lo que
    guarda es el residuo (``ForecastResult.errors``, ``verify.backtest``). Pasar el
    error por aca en vez de reconstruir el par es lo que garantiza que ``bias`` y el
    tracking signal se lean de la MISMA serie, y evita una segunda definicion de
    "error" que con el tiempo se separa de la primera.
    """
    err = np.asarray(errors, dtype=float)
    if err.size == 0:
        return np.asarray([], dtype=float)
    cfe = np.cumsum(err)
    mad = np.cumsum(np.abs(err)) / np.arange(1, err.size + 1)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(mad > 0, cfe / mad, np.nan)


def tracking_signal_from_errors(errors) -> float:
    """:func:`tracking_signal` sobre una serie de errores ya calculada."""
    ts = tracking_signal_series_from_errors(errors)
    finite = ts[np.isfinite(ts)]
    return float(finite[-1]) if finite.size else float("nan")


def tracking_signal(actual, forecast) -> float:
    """:func:`tracking_signal_series` en su ultimo punto finito (``NaN`` si no hay).

    Se toma el ULTIMO finito y no el ultimo a secas: una cola de ``NaN`` (MAD que
    se vuelve cero porque el forecast paso a ser exacto) no invalida el veredicto
    que la serie ya habia ganado. Sin ningun punto finito, ``NaN``.
    """
    a, f = _arrays(actual, forecast)
    return tracking_signal_from_errors(f - a)


def bias_verdict(ts: float, band: float = TRACKING_SIGNAL_BAND, *, n_errors: int = 0) -> str:
    """Traduce un TS a ``in_band`` | ``over`` | ``under`` | ``undefined``.

    La banda es INCLUSIVA: ``|TS| <= band`` esta en banda. Liu (p.312) dice
    "outside the range of +-6", asi que exactamente 6 esta adentro; correr el
    borde un punto cambia el veredicto de un SKU con el TS justo en el limite.

    ``n_errors`` es la regla de alcanzabilidad, y sin ella el veredicto MIENTE:
    con ``n`` errores, ``|TS| <= n`` siempre (el CFE no puede superar ``n`` veces la
    MAD), asi que un veredicto "in_band" sobre 4 errores no dice "sin sesgo", dice
    "no habia forma de detectarlo". Cuando ``n <= band`` la banda es inalcanzable
    por construccion y la respuesta honesta es ``undefined``.

    ``over`` = el metodo corre alto de forma sostenida -> el inventario comprado
    con ese pronostico tiende a sobrar. ``under`` = corre bajo -> tiende a faltar.
    En los dos casos el remedio NO es mas buffer: ``sigma_e`` mide dispersion, y
    un z-factor mas grande no corrige un error de nivel.
    """
    if ts is None or not math.isfinite(ts):
        return "undefined"
    if n_errors and n_errors <= band:
        return "undefined"
    if abs(ts) <= band:
        return "in_band"
    return "over" if ts > 0 else "under"


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
    #: Ultimo tracking signal de la ventana evaluada, y su veredicto contra
    #: :data:`TRACKING_SIGNAL_BAND`. Viaja al lado de ``bias`` porque contestan dos
    #: preguntas distintas: ``bias`` dice cuanto y para que lado, el TS dice si ese
    #: signo se sostuvo periodo a periodo (sesgo) o fue ruido que se compenso.
    tracking_signal: float = float("nan")
    bias_verdict: str = "undefined"
    #: Sobre cuantos errores se calculo el TS. Viaja porque el veredicto no es
    #: auditable sin el: con n errores ``|TS| <= n``, asi que ``in_band`` sobre 4
    #: errores dice "no habia como detectarlo", no "sin sesgo".
    tracking_signal_periods: int = 0


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
    _a = np.asarray(actual, dtype=float)
    _ts = tracking_signal(actual, forecast)
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
        tracking_signal=_ts,
        bias_verdict=bias_verdict(_ts, n_errors=int(_a.size)),
        tracking_signal_periods=int(_a.size),
    )
