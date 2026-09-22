"""La compuerta de suciedad acusa por FIDELIDAD, no por el ordinal del nivel.

`degrada_con_la_suciedad` afirmaba: si un nivel mas sucio captura mejor, el nivel
esta entrando en la decision por una via no disenada. La observacion sigue siendo
correcta; la INFERENCIA dejo de valer, y esta medido por que.

Los niveles de `src/simworld/lens.py` son monotonos en CANTIDAD DE DEFECTOS y no en
DISTORSION. Medido sobre 25 mundos: el archivo de nivel 3 le entrega al motor ~1,26
de la demanda verdadera y el de nivel 2 ~0,59, porque los inyectores del 3 INFLAN
(`inject_duplicate_rows`, `inject_unmarked_promo`) y los del 2 DESINFLAN
(`inject_mixed_units`, `inject_calendar_gaps`). En distancia a la verdad el archivo
del 3 es el menos malo, asi que Kern comprando mas al leerlo es la respuesta CORRECTA
a un archivo mas fiel -- no una fuga.

Lo que estos tests fijan es que el cambio no le sacara los dientes a la alarma: sigue
acusando cuando la captura mejora con un archivo igual de fiel o peor, que es
exactamente la forma del defecto que atrapo en 2026-09-18 (el signo de la funcion de
costo, donde las fidelidades eran identicas porque el defecto no estaba en la lente).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.simworld.lens import DIRT_BY_LEVEL, fidelidad_del_nivel, history_frame
from src.simworld.world import advance, generate_world, initial_state
from src.verify.barrido import (
    Distribucion,
    degrada_con_la_suciedad,
    inversiones_explicadas,
)


def _d(nivel: int, media: float, *, fidelidad: float | None, n: int = 25,
       sd: float = 0.05) -> Distribucion:
    return Distribucion(
        carril="forced", nivel=nivel, n_mundos=n, n_medibles=n,
        media=media, desviacion=sd, p10=media, mediana=media, p90=media,
        peor=media, mejor=media, fidelidad_media=fidelidad,
    )


# ---------------------------------------------------------------------------
# La alarma conserva sus dientes
# ---------------------------------------------------------------------------

def test_acusa_cuando_mejora_con_un_archivo_IGUAL_de_fiel():
    """La forma del defecto real: la lente no cambio y aun asi ensuciar mejoro."""
    dists = [_d(0, -0.50, fidelidad=1.0), _d(1, -0.10, fidelidad=1.0)]
    assert degrada_con_la_suciedad(dists, "forced")


def test_acusa_cuando_mejora_con_un_archivo_PEOR():
    dists = [_d(0, -0.50, fidelidad=1.0), _d(1, -0.10, fidelidad=0.40)]
    assert degrada_con_la_suciedad(dists, "forced")


def test_acusa_cuando_NO_hay_fidelidad_medida():
    """Una linea base vieja, o un archivo que el intake no pudo leer. Exculpar con
    una medicion ausente convertiria la alarma en un adorno."""
    dists = [_d(0, -0.50, fidelidad=None), _d(1, -0.10, fidelidad=None)]
    assert degrada_con_la_suciedad(dists, "forced")
    # Y tampoco alcanza con que la tenga UNO de los dos.
    assert degrada_con_la_suciedad([_d(0, -0.50, fidelidad=1.0),
                                    _d(1, -0.10, fidelidad=None)], "forced")


def test_no_acusa_cuando_la_mejora_cabe_en_el_ruido():
    """Lo de siempre: el margen escala con el error estandar, y no se toco."""
    dists = [_d(0, -0.50, fidelidad=1.0), _d(1, -0.49, fidelidad=1.0)]
    assert degrada_con_la_suciedad(dists, "forced") == []


# ---------------------------------------------------------------------------
# La exencion, y que sea VISIBLE
# ---------------------------------------------------------------------------

def test_no_acusa_cuando_el_nivel_mas_sucio_leyo_un_archivo_mas_fiel():
    dists = [_d(2, -0.90, fidelidad=0.59), _d(3, -0.30, fidelidad=1.26)]
    assert degrada_con_la_suciedad(dists, "forced") == []


def test_la_exencion_sale_por_escrito_y_nombra_las_dos_fidelidades():
    """Un barrido que pase en verde sin decir que dos de sus filas no estan ordenadas
    como su ordinal sugiere le esconde eso a quien lee la tabla."""
    dists = [_d(2, -0.90, fidelidad=0.59), _d(3, -0.30, fidelidad=1.26)]
    fuera = inversiones_explicadas(dists, "forced")
    assert len(fuera) == 1
    assert "0.59" in fuera[0] and "1.26" in fuera[0]
    assert "explicado" in fuera[0]


def test_una_anomalia_de_verdad_no_sale_como_explicada():
    """Las dos listas son disjuntas: lo que acusa no se exime y viceversa."""
    dists = [_d(0, -0.50, fidelidad=1.0), _d(1, -0.10, fidelidad=1.0)]
    assert degrada_con_la_suciedad(dists, "forced")
    assert inversiones_explicadas(dists, "forced") == []


def test_inflar_y_desinflar_lo_mismo_son_igual_de_infieles():
    """La razon cruda no ordena dos niveles; su DISTANCIA a 1 si. Sin esto, un archivo
    que inventa el 30% de la demanda contaria como mas fiel que uno intacto."""
    assert _d(1, -0.1, fidelidad=1.30).error_de_fidelidad == pytest.approx(0.30)
    assert _d(1, -0.1, fidelidad=0.70).error_de_fidelidad == pytest.approx(0.30)
    assert _d(1, -0.1, fidelidad=None).error_de_fidelidad is None
    # Y por eso inflar mucho NO exime.
    dists = [_d(2, -0.90, fidelidad=0.95), _d(3, -0.30, fidelidad=1.80)]
    assert degrada_con_la_suciedad(dists, "forced")


def test_una_linea_base_vieja_sin_el_campo_sigue_cargando():
    """`Distribucion(**d)` sobre un JSON grabado antes de que el campo existiera."""
    viejo = {"carril": "forced", "nivel": 0, "n_mundos": 25, "n_medibles": 25,
             "media": -0.4, "desviacion": 0.14, "p10": -0.6, "mediana": -0.36,
             "p90": -0.26, "peor": -0.72, "mejor": -0.16, "metrica": "capture"}
    d = Distribucion(**viejo)
    assert d.fidelidad_media is None and d.error_de_fidelidad is None
    assert "captura -40.0%" in d.frase(), "la frase vendible no puede haber cambiado"


# ---------------------------------------------------------------------------
# La medida misma
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def mundo_y_salidas():
    w = generate_world(seed=1000, n_skus=8, n_suppliers=2, weeks=12,
                       regime="seasonal_trend")
    st = initial_state(w)
    outs = []
    for _ in range(w.weeks):
        st, o = advance(w, st, {})
        outs.append(o)
    return w, outs


def test_el_nivel_limpio_es_fiel_por_definicion(mundo_y_salidas):
    w, outs = mundo_y_salidas
    assert fidelidad_del_nivel(w, outs, level=0, seed=1000) == pytest.approx(1.0)


def test_el_nivel_cosmetico_tambien(mundo_y_salidas):
    """Es el invariante del arreglo de intake, visto desde la otra punta: si el nivel
    1 no fuera fiel, la compuerta lo estaria exculpando o acusando por un defecto de
    lectura en vez de por suciedad."""
    w, outs = mundo_y_salidas
    assert fidelidad_del_nivel(w, outs, level=1, seed=1000) == pytest.approx(1.0, abs=0.01)


def test_la_medida_distingue_inflar_de_desinflar(mundo_y_salidas, tmp_path):
    """Sobre los inyectores reales, no sobre un mock: uno que duplica filas tiene que
    dar > 1 y uno que convierte unidades a cajas < 1."""
    w, outs = mundo_y_salidas
    limpio = history_frame(w, outs)
    por_nombre = {f.__name__: f for nivel in (2, 3) for f in DIRT_BY_LEVEL[nivel]}

    def total(fn, tmp: Path) -> float:
        from jobs import intake

        ruta = tmp / f"{fn.__name__}.csv"
        fn(limpio.copy(), np.random.default_rng(1000)).to_csv(ruta, index=False)
        return float(pd.to_numeric(intake.prepare_tracked(ruta)[0]["quantity"]).sum())

    base = float(pd.to_numeric(limpio["quantity"]).sum())
    assert total(por_nombre["inject_duplicate_rows"], tmp_path) > base
    assert total(por_nombre["inject_mixed_units"], tmp_path) < base
