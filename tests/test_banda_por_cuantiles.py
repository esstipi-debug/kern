"""Una banda P10/P50/P90 medida sin supuesto normal, como RETADORA de la normal.

``forecast_job`` publica la banda como ``punto +/- z(0,90) * sigma``: simetrica por
construccion. Dos estimadores la desafian, uno por forma de demanda:
  - intermitente: modelo de DOS PARTES (ganador de M5 Uncertainty): probabilidad de que
    haya venta por el cuantil de los tamanos cuando la hay;
  - regular: cuantiles empiricos de los residuos one-step del propio metodo.

Sobre 1000 SKUs del M5 real ninguno le gano a la normal a UN paso (``scripts/banda_m5_*``),
asi que la publicada sigue siendo la normal, al numero, y la retadora se calcula y se
puntua en cada corrida sobre los datos del cliente. Promoverla es una decision humana.
"""
from __future__ import annotations

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




def _normal_esperada(serie: list[float], metodo: str) -> tuple[float, float, float]:
    fc = forecast_demand(serie, method=metodo)
    sigma = fj._dispersion(fc)
    return max(0.0, fc.forecast - fj._Z80 * sigma), fc.forecast, fc.forecast + fj._Z80 * sigma


@pytest.mark.parametrize("serie", [_INTERMITENTE, _SESGADA, _CORTA])
def test_la_banda_publicada_sigue_siendo_la_normal_al_numero(serie):
    """La garantia de esta rama: para TODO SKU, intermitente o no, la banda que el
    cliente ve es exactamente la de antes. La retadora no la toca."""
    s = _sku(serie)
    p10, p50, p90 = _normal_esperada([float(v) for v in serie], s.method)
    assert s.band_method == "normal"
    assert s.p10 == pytest.approx(p10)
    assert s.p50 == pytest.approx(p50)
    assert s.p90 == pytest.approx(p90)


def test_la_retadora_de_un_intermitente_es_de_dos_partes():
    s = _sku(_INTERMITENTE)
    assert s.challenger_method == "two_part"
    assert s.challenger_p10 == 0.0
    assert s.challenger_p10 <= s.challenger_p50 <= s.challenger_p90


def test_la_retadora_de_una_serie_regular_es_empirica_y_asimetrica():
    s = _sku(_SESGADA)
    assert s.challenger_method == "residual_empirical"
    assert (s.challenger_p90 - s.challenger_p50) > (s.challenger_p50 - s.challenger_p10)


def test_sin_historia_no_hay_retadora_y_lo_dice():
    s = _sku(_CORTA)
    assert s.challenger_method == "unavailable"
    assert (s.challenger_p10, s.challenger_p50, s.challenger_p90) == (None, None, None)
    assert s.challenger_pinball is None and s.challenger_scored_origins == 0


def test_el_puntaje_es_a_un_paso_y_se_reproduce_a_mano():
    """Cada origen t ajusta sobre serie[:t] y se puntua contra serie[t], que no vio."""
    serie = [float(v) for v in _SESGADA]
    s = fj.run({"S": serie}, challenger_origins=3).skus[0]
    ch, no, cc, nc = [], [], [], []
    for t in range(len(serie) - 3, len(serie)):
        fc = forecast_demand(serie[:t], method=s.method)
        b, n = quantile_band(serie[:t], fc), fj._normal_band(fc)
        if b is None or b.method != s.challenger_method:
            continue
        a, c = band_score([serie[t]], b)
        z, w = band_score([serie[t]], n)
        ch.append(a), cc.append(c), no.append(z), nc.append(w)
    assert s.challenger_scored_origins == len(ch) >= 2
    assert s.challenger_pinball == pytest.approx(float(np.mean(ch)))
    assert s.normal_pinball == pytest.approx(float(np.mean(no)))
    assert s.challenger_coverage == pytest.approx(float(np.mean(cc)))
    assert s.normal_coverage == pytest.approx(float(np.mean(nc)))


def test_con_cero_origenes_no_se_puntua_y_la_publicada_no_cambia():
    serie = [float(v) for v in _SESGADA]
    con = fj.run({"S": serie}).skus[0]
    sin = fj.run({"S": serie}, challenger_origins=0).skus[0]
    assert sin.challenger_pinball is None and sin.challenger_scored_origins == 0
    assert (sin.p10, sin.p50, sin.p90) == (con.p10, con.p50, con.p90)
    assert sin.challenger_method == con.challenger_method


def test_el_reporte_cuenta_donde_la_retadora_le_gano_a_la_normal():
    r = fj.run({"I": [float(v) for v in _INTERMITENTE], "S": [float(v) for v in _SESGADA]})
    puntuados = [s for s in r.skus if s.challenger_scored_origins > 0]
    assert puntuados
    assert r.n_challenger_scored == len(puntuados)
    assert r.n_challenger_beats_normal == sum(
        1 for s in puntuados if s.challenger_pinball < s.normal_pinball
    )


def test_la_retadora_llega_al_csv_y_al_deck_en_ascii(tmp_path):
    r = fj.run({"I": [float(v) for v in _INTERMITENTE], "S": [float(v) for v in _SESGADA]})
    csv = fj.write_operational(r, tmp_path)["csv"].read_text(encoding="utf-8")
    cabecera = csv.splitlines()[0].split(",")
    for col in ("p10", "p50", "p90", "band_method", "challenger_method", "challenger_p10",
                "challenger_p50", "challenger_p90", "challenger_pinball", "normal_pinball",
                "challenger_coverage", "normal_coverage", "challenger_scored_origins"):
        assert col in cabecera
    deck = fj.build_deck(r)
    texto = " ".join(f.title + " " + f.detail + " " + f.impact for f in deck.findings)
    assert "two_part" in texto and "residual_empirical" in texto
    assert "not acted on" in texto
    texto.encode("ascii")


def test_verify_frena_que_una_retadora_se_cuele_como_publicada():
    from dataclasses import replace

    r = fj.run({"S": [float(v) for v in _SESGADA]})
    s = r.skus[0]
    colada = replace(r, skus=(replace(s, band_method="residual_empirical"),))
    assert any("unknown band method" in i for i in fj.verify(colada))
    cruzada = replace(r, skus=(replace(s, challenger_p10=s.challenger_p50 + 1.0),))
    assert any("crossed challenger band" in i for i in fj.verify(cruzada))
    vacia = replace(r, skus=(replace(s, challenger_p90=None),))
    assert any("challenger band" in i for i in fj.verify(vacia))
    assert fj.verify(r) == []
    assert "residual_empirical" in BAND_METHODS
