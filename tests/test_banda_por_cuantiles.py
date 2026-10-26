"""La banda P10/P50/P90 sale de la distribucion medida, no de un supuesto normal.

Hasta esta rama ``forecast_job`` dibujaba la banda como ``punto +/- z(0,90) * sigma``:
simetrica por construccion. Sobre demanda intermitente eso es falso dos veces -- el P10
de un SKU que no vende la mitad de los periodos es CERO, y la cola de arriba es mas larga
que la de abajo -- y la banda normal no puede decir ninguna de las dos cosas.

Dos estimadores, uno por forma de demanda (Syntetos-Boylan ya separa las formas):
  - intermitente: modelo de DOS PARTES (ganador de M5 Uncertainty): probabilidad de que
    haya venta por el cuantil de los tamanos cuando la hay;
  - regular: cuantiles empiricos de los residuos one-step del propio metodo.
Sin historia para ninguno de los dos, la banda normal de antes queda IGUAL, y lo dice.

Y no se publica a ciegas: el holdout la puntua con pinball contra la banda normal, la
misma idea que MASE contra el naive.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from jobs import forecast_job as fj
from src.forecasting import forecast_demand
from src.quantile_forecasting import (
    BAND_METHODS,
    MIN_NONZERO_FOR_TWO_PART,
    MIN_RESIDUALS_FOR_EMPIRICAL,
    QuantileBand,
    band_score,
    quantile_band,
    two_part_quantiles,
)

# 60% de periodos sin venta; tamanos 10..60 cuando la hay.
_INTERMITENTE = [0, 0, 10, 0, 20, 0, 0, 30, 0, 40, 0, 0, 50, 0, 60, 0, 0, 20, 0, 30]
# Ruido sesgado a la derecha alrededor de 100: picos grandes, valles chicos.
_SESGADA = [100, 98, 140, 99, 101, 97, 150, 100, 98, 102, 160, 99, 100, 101, 135, 98]
_CORTA = [100.0, 104.0, 98.0, 101.0]


# ---- motor: modelo de dos partes ---------------------------------------------


def test_dos_partes_reproduce_la_aritmetica_de_la_mezcla():
    """P(D <= x) = (1-p) + p * F_tamano(x): con 60% de ceros, P10 y P50 son cero y el
    P90 es el cuantil (0,9-0,4)/0,6 de los tamanos medidos."""
    q = two_part_quantiles(_INTERMITENTE, (0.1, 0.5, 0.9))
    sizes = [v for v in _INTERMITENTE if v > 0]
    p = len(sizes) / len(_INTERMITENTE)
    assert p == pytest.approx(0.4)
    assert q[0.1] == 0.0
    assert q[0.5] == 0.0
    assert q[0.9] == pytest.approx(float(np.quantile(sizes, (0.9 - 0.6) / 0.4)))


def test_dos_partes_es_monotona_por_construccion():
    q = two_part_quantiles(_INTERMITENTE, (0.05, 0.1, 0.5, 0.7, 0.9, 0.95))
    valores = [q[k] for k in sorted(q)]
    assert valores == sorted(valores)


def test_dos_partes_se_niega_con_muy_pocas_ventas():
    """Con menos de MIN_NONZERO_FOR_TWO_PART ventas no hay distribucion de tamanos que
    citar: devuelve None, nunca un cuantil inventado."""
    pocas = [0] * 10 + [5] * (MIN_NONZERO_FOR_TWO_PART - 1)
    assert two_part_quantiles(pocas, (0.1, 0.5, 0.9)) is None


# ---- motor: residuos empiricos y la eleccion ---------------------------------


def test_banda_empirica_muestra_la_asimetria_que_la_normal_no_puede():
    """Picos grandes y valles chicos: el tramo P50->P90 tiene que ser mas largo que el
    P10->P50. La banda normal es simetrica por construccion."""
    banda = quantile_band(_SESGADA, forecast_demand(_SESGADA, method="ses"))
    assert banda is not None
    assert banda.method == "residual_empirical"
    assert banda.p10 <= banda.p50 <= banda.p90
    assert (banda.p90 - banda.p50) > (banda.p50 - banda.p10)


def test_banda_empirica_se_niega_sin_residuos_suficientes():
    fc = forecast_demand(_CORTA, method="ses")
    assert len(fc.errors) < MIN_RESIDUALS_FOR_EMPIRICAL
    assert quantile_band(_CORTA, fc) is None


def test_intermitente_elige_dos_partes():
    fc = forecast_demand(_INTERMITENTE, method="croston")
    assert fc.is_intermittent
    banda = quantile_band(_INTERMITENTE, fc)
    assert banda is not None
    assert banda.method == "two_part"
    assert banda.p10 == 0.0


def test_band_score_es_pinball_promedio_y_cobertura():
    banda = QuantileBand(p10=10.0, p50=20.0, p90=30.0, method="normal")
    actual = [5.0, 20.0, 40.0]
    pinball, cobertura = band_score(actual, banda)
    # q10: (5-10)*(.1-1)=4.5, (20-10)*.1=1, (40-10)*.1=3 -> 8.5/3
    # q50: 7.5, 0, 10 -> 17.5/3 ; q90: (5-30)*(.9-1)=2.5, (20-30)*-.1=1, 10*.9=9 -> 12.5/3
    esperado = (8.5 / 3 + 17.5 / 3 + 12.5 / 3) / 3
    assert pinball == pytest.approx(esperado)
    assert cobertura == pytest.approx(1 / 3)


# ---- job: la banda que llega al cliente ---------------------------------------


def _sku(serie: list[float], nombre: str = "A") -> fj.SkuForecast:
    return fj.run({nombre: [float(v) for v in serie]}).skus[0]


def test_el_job_publica_la_banda_de_dos_partes_para_un_intermitente():
    s = _sku(_INTERMITENTE)
    assert s.band_method == "two_part"
    assert s.p10 == 0.0
    assert s.p10 <= s.p50 <= s.p90


def test_el_job_publica_la_banda_empirica_para_una_serie_regular():
    s = _sku(_SESGADA)
    assert s.band_method == "residual_empirical"
    assert (s.p90 - s.p50) > (s.p50 - s.p10)


def test_sin_historia_la_banda_normal_queda_identica_y_lo_dice():
    """La red de seguridad: con historia corta el numero es EXACTAMENTE el de antes."""
    s = _sku(_CORTA)
    fc = forecast_demand(_CORTA, method=s.method)
    sigma = fj._dispersion(fc)
    assert s.band_method == "normal"
    assert s.p10 == pytest.approx(max(0.0, fc.forecast - fj._Z80 * sigma))
    assert s.p90 == pytest.approx(fc.forecast + fj._Z80 * sigma)
    assert s.p50 == pytest.approx(fc.forecast)


def test_el_holdout_puntua_la_banda_contra_la_normal():
    s = _sku(_SESGADA)
    assert s.holdout_pinball is not None and math.isfinite(s.holdout_pinball)
    assert s.holdout_pinball_normal is not None and math.isfinite(s.holdout_pinball_normal)
    assert 0.0 <= s.holdout_coverage <= 1.0


def test_sin_holdout_el_puntaje_es_none_nunca_cero():
    s = _sku([5.0, 6.0])
    assert s.holdout_pinball is None
    assert s.holdout_pinball_normal is None
    assert s.holdout_coverage is None


def test_el_reporte_cuenta_donde_la_banda_le_gano_a_la_normal():
    r = fj.run({"I": [float(v) for v in _INTERMITENTE], "S": [float(v) for v in _SESGADA]})
    comparables = [
        s for s in r.skus
        if s.band_method != "normal" and s.holdout_pinball is not None
    ]
    ganan = sum(1 for s in comparables if s.holdout_pinball < s.holdout_pinball_normal)
    assert r.n_band_compared == len(comparables)
    assert r.n_band_beats_normal == ganan


def test_la_banda_llega_al_csv_y_al_deck_en_ascii(tmp_path):
    r = fj.run({"I": [float(v) for v in _INTERMITENTE], "S": [float(v) for v in _SESGADA]})
    csv = fj.write_operational(r, tmp_path)["csv"].read_text(encoding="utf-8")
    cabecera = csv.splitlines()[0].split(",")
    for col in ("p10", "p50", "p90", "band_method", "holdout_pinball",
                "holdout_pinball_normal", "holdout_coverage"):
        assert col in cabecera
    deck = fj.build_deck(r)
    texto = " ".join(f.title + " " + f.detail for f in deck.findings)
    assert "two_part" in texto and "residual_empirical" in texto
    texto.encode("ascii")


def test_verify_rechaza_una_banda_cruzada_o_un_metodo_desconocido():
    from dataclasses import replace

    r = fj.run({"S": [float(v) for v in _SESGADA]})
    s = r.skus[0]
    cruzada = replace(r, skus=(replace(s, p10=s.p50 + 1.0),))
    assert any("band" in i for i in fj.verify(cruzada))
    rara = replace(r, skus=(replace(s, band_method="magia"),))
    assert any("band method" in i for i in fj.verify(rara))
    assert "magia" not in BAND_METHODS
