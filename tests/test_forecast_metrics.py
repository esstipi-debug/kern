"""Tests for forecast accuracy metrics (capability M2 — linchpin-forecast-metrics)."""

import math

import pytest

from src.forecast_metrics import (
    ForecastMetrics,
    bias_verdict,
    compute_metrics,
    mae,
    mape,
    rmsse,
    tracking_signal,
    tracking_signal_series,
    wape,
)


def test_mae():
    assert mae([100, 110, 90, 105], [90, 120, 100, 100]) == pytest.approx(8.75)


def test_mape_skips_zero_actuals():
    assert mape([100, 0, 200], [110, 5, 180]) == pytest.approx((0.1 + 0.1) / 2)


def test_wape():
    assert wape([100, 110, 90, 105], [90, 120, 100, 100]) == pytest.approx(35 / 405)


def test_wape_zero_demand_is_inf():
    assert math.isinf(wape([0, 0], [3, 4]))


def test_rmsse_uses_naive_training_error():
    train = [90, 100, 110, 100, 120]
    val_actual = [100, 110, 90, 105]
    val_fcst = [90, 120, 100, 100]
    # rmse=sqrt(81.25); naive scale=sqrt(mean([10,10,10,20]^2))=sqrt(175)
    assert rmsse(val_actual, val_fcst, train) == pytest.approx(math.sqrt(81.25) / math.sqrt(175), rel=1e-6)


def test_compute_metrics_bundles_everything():
    m = compute_metrics([100, 110, 90, 105], [90, 120, 100, 100], train=[90, 100, 110, 100, 120])
    assert isinstance(m, ForecastMetrics)
    assert m.mae == pytest.approx(8.75)
    assert m.bias == pytest.approx(1.25)  # mean(forecast - actual)
    assert m.wape == pytest.approx(35 / 405)
    assert m.mase == pytest.approx(8.75 / 12.5)  # naive MAE = mean([10,10,10,20]) = 12.5


# -- tracking signal (Liu 2022, eq. 8.16; umbral +-6 de Chopra & Meindl) ------
#
# La pregunta es distinta de la de ``bias``: no cuanto, sino si el signo se
# sostuvo. Un sesgo de +10 con MAD 10 se detecta; el mismo +10 promedio sobre
# errores que se compensan, no.


def test_tracking_signal_es_error_acumulado_sobre_mad_acumulada():
    ts = tracking_signal_series([100, 100, 100], [110, 110, 110])

    assert list(ts) == pytest.approx([1.0, 2.0, 3.0])
    assert tracking_signal([100, 100, 100], [110, 110, 110]) == pytest.approx(3.0)


def test_tracking_signal_positivo_es_sobre_pronostico_como_bias():
    """Misma convencion que ``bias`` (f - a): + = el metodo corre ALTO.

    Liu escribe E = A - F, asi que su TS tiene el signo al reves. Si las dos
    superficies del repo no coinciden, el planner recorta compras en el SKU que se
    queda corto.
    """
    over = compute_metrics([100, 100, 100], [110, 110, 110])
    under = compute_metrics([100, 100, 100], [90, 90, 90])

    assert over.bias > 0 and over.tracking_signal > 0
    assert under.bias < 0 and under.tracking_signal < 0


def test_un_pronostico_exacto_deja_el_ts_indefinido_y_no_en_cero():
    """MAD 0 no es "perfectamente sin sesgo": es un cociente que no existe.

    Un 0.0 ahi se leeria como "en banda" y afirmaria algo que no se midio.
    """
    m = compute_metrics([100, 100, 100], [100, 100, 100])

    assert math.isnan(m.tracking_signal)
    assert m.bias_verdict == "undefined"


def test_el_mismo_bias_signo_promediado_puede_ser_ruido():
    """Dos SKUs con el mismo ``bias`` no son el mismo problema."""
    steady = compute_metrics([100, 100, 100, 100], [101, 101, 101, 101])
    alternating = compute_metrics([100, 100, 100, 100], [101, 99, 101, 99])

    assert steady.bias == pytest.approx(1.0) and steady.tracking_signal == pytest.approx(4.0)
    assert alternating.bias == pytest.approx(0.0)
    assert abs(alternating.tracking_signal) <= 1.0


def test_bias_verdict_banda_inclusiva_y_alcanzabilidad():
    """``|TS| <= 6`` esta en banda; pero con 6 errores o menos la banda no se alcanza.

    Con n errores, |TS| <= n siempre, asi que "in_band" sobre 4 errores no dice
    "sin sesgo" sino "no habia como detectarlo": la respuesta honesta es undefined.
    """
    assert bias_verdict(6.0, n_errors=12) == "in_band"
    assert bias_verdict(6.0001, n_errors=12) == "over"
    assert bias_verdict(-6.0001, n_errors=12) == "under"
    assert bias_verdict(6.0, n_errors=6) == "undefined"
    assert bias_verdict(1.0, n_errors=0) == "in_band"      # n desconocido: no gatea
    assert bias_verdict(float("nan")) == "undefined"


def test_compute_metrics_bundles_the_tracking_signal_with_its_period_count():
    m = compute_metrics([100] * 12, [110] * 12)

    assert m.tracking_signal == pytest.approx(12.0)
    assert m.tracking_signal_periods == 12                  # el veredicto no es auditable sin el n
    assert m.bias_verdict == "over"                        # 12 errores: la banda se alcanza
