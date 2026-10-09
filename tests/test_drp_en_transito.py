"""DRP planifica contra la posicion, no contra el estante (C3).

``src/drp.py`` calculaba ``available = prev_oh - forecast[t]``: la grilla no tenia el
termino de *scheduled receipts*, que en DRP clasico es insumo estandar (lo que ya se pidio
y llega en el periodo t). Consecuencia: una OC ya emitida a una sucursal se volvia a
planificar como liberacion.

Misma disciplina que ``tests/test_neteo_en_transito.py``: la instancia se verifica a mano
(numero por numero, no "es menor"), el valor SE MUEVE, y la ausencia se declara -- un
archivo sin la columna se planifica contra el estante y el entregable lo dice.
"""

from __future__ import annotations

import csv
import sys
from dataclasses import replace
from pathlib import Path

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from jobs import drp_job as dj  # noqa: E402
from src.drp import Branch, drp_plan  # noqa: E402


def _branch() -> Branch:
    """La MISMA sucursal que fija ``tests/test_drp.py``: demanda 10/periodo, 15 en
    estante, lead 1, stock de seguridad 5, lote a lote."""
    return Branch("B1", forecast=(10, 10, 10, 10), on_hand=15, lead_time=1, safety_stock=5)


# -- el motor: la instancia, numero por numero ------------------------------------


def test_una_recepcion_programada_desplaza_exactamente_la_recepcion_planificada():
    """Sin entradas: recibe [0,10,10,10] y libera [10,10,10,0]. Con 10 ya en camino en
    el periodo 1, el plan NO pide ese periodo: [0,0,10,10] y libera [0,10,10,0].

    El piso de stock de seguridad no se mueve: el proyectado sigue clavado en 5."""
    sin = drp_plan(_branch())
    con = drp_plan(replace(_branch(), scheduled_receipts=(0, 10, 0, 0)))

    assert [r.planned_receipt for r in sin] == [0, 10, 10, 10]
    assert [r.planned_receipt for r in con] == [0, 0, 10, 10]
    assert [r.planned_order_release for r in sin] == [10, 10, 10, 0]
    assert [r.planned_order_release for r in con] == [0, 10, 10, 0]
    assert [r.projected_on_hand for r in con] == [5, 5, 5, 5]
    assert [r.scheduled_receipt for r in con] == [0, 10, 0, 0]


def test_lo_ya_comprado_se_reporta_aparte_de_lo_que_el_plan_propone_pedir():
    """Dos columnas distintas en la fila: mezclarlas manda a comprar de nuevo lo que ya
    esta comprado."""
    fila = drp_plan(replace(_branch(), scheduled_receipts=(0, 10, 0, 0)))[1]
    assert fila.scheduled_receipt == 10
    assert fila.planned_receipt == 0


def test_sin_entradas_declaradas_la_grilla_es_exactamente_la_de_antes():
    """El control negativo: una tupla vacia no es una tupla de ceros con otro nombre --
    tiene que dar el mismo plan que daba el motor antes de existir el termino."""
    plan = drp_plan(_branch())
    assert [r.scheduled_receipt for r in plan] == [0, 0, 0, 0]
    assert [r.planned_receipt for r in plan] == [0, 10, 10, 10]


def test_una_entrada_en_el_periodo_cero_se_suma_al_estante_inicial():
    b = Branch("B", forecast=(20, 0), on_hand=0, lead_time=0, safety_stock=0,
               scheduled_receipts=(20, 0))
    plan = drp_plan(b)
    assert [r.planned_receipt for r in plan] == [0, 0]
    assert plan[0].projected_on_hand == 0


def test_una_serie_mas_corta_que_el_horizonte_se_rellena_con_ceros():
    """Y una mas larga no se cuela: un periodo fuera del horizonte no se planifica."""
    corta = drp_plan(replace(_branch(), scheduled_receipts=(0, 10)), n_periods=4)
    assert [r.scheduled_receipt for r in corta] == [0, 10, 0, 0]
    larga = drp_plan(replace(_branch(), scheduled_receipts=(0, 10, 0, 0, 99)), n_periods=4)
    assert [r.scheduled_receipt for r in larga] == [0, 10, 0, 0]


def test_el_lote_sigue_redondeando_sobre_lo_que_falta_despues_de_netear():
    """El lote se aplica al requerimiento NETO, o sea despues de contar lo que llega."""
    b = Branch("B", forecast=(7, 7), on_hand=0, lead_time=0, safety_stock=0, lot_size=10,
               scheduled_receipts=(7, 0))
    plan = drp_plan(b)
    assert [r.planned_receipt for r in plan] == [0, 10]


# -- el job: el valor se mueve y la ausencia se declara ---------------------------


def _df(receipts: list[float] | None = None, columna: str = "en_transito") -> pd.DataFrame:
    rows = []
    for branch, oh, lt, ss in (("East", 15, 1, 5), ("West", 0, 1, 0)):
        for period in range(1, 5):
            rows.append({"branch": branch, "period": period, "demand": 10,
                         "on_hand": oh, "lead_time": lt, "safety_stock": ss})
    df = pd.DataFrame(rows)
    if receipts is not None:
        df[columna] = receipts
    return df


def _east(report):
    return next(bp for bp in report.branch_plans if bp.name == "East")


def test_el_job_lee_la_columna_y_la_liberacion_baja_exactamente_lo_programado():
    sin = dj.run(dj.prepare_records(_df()))
    con = dj.run(dj.prepare_records(_df([0, 10, 0, 0, 0, 0, 0, 0])))

    assert list(_east(sin).releases) == [10, 10, 10, 0]
    assert list(_east(con).releases) == [0, 10, 10, 0]
    assert con.total_branch_releases == sin.total_branch_releases - 10
    assert con.n_with_receipts == 2 and con.receipt_column == "en_transito"
    assert dj.verify(con) == []


def test_sin_la_columna_el_job_no_inventa_un_cronograma_vacio():
    report = dj.run(dj.prepare_records(_df()))
    assert report.n_with_receipts == 0 and report.receipt_column == ""
    for bp in report.branch_plans:
        assert all(r.scheduled_receipt == 0.0 for r in bp.plan)


def test_los_nombres_reales_de_la_columna_se_detectan():
    for columna in ("scheduled_receipt", "in_transit", "on_order", "incoming",
                    "en_transito", "por_recibir", "oc_pendiente"):
        payload = dj.prepare_records(_df([0, 10, 0, 0, 0, 0, 0, 0], columna=columna))
        assert payload["receipt_column"] == columna, columna


def test_una_columna_nombrada_a_mano_gana():
    df = _df([0, 10, 0, 0, 0, 0, 0, 0], columna="Llega Semana")
    assert dj.prepare_records(df)["receipt_column"] == ""
    payload = dj.prepare_records(df, {"receipt_col": "Llega Semana"})
    assert payload["receipt_column"] == "Llega Semana"
    assert payload["branches"][0].scheduled_receipts == (0.0, 10.0, 0.0, 0.0)


def test_el_centro_de_distribucion_tambien_netea_por_params():
    """El CD no tiene filas propias: sus entradas programadas llegan por params, y
    tampoco se fabrican en cero."""
    sin = dj.run(dj.prepare_records(_df()))
    con = dj.run(dj.prepare_records(_df(), {"dc_scheduled_receipts": [30, 0, 0, 0]}))
    assert con.dc_release_total < sin.dc_release_total
    assert [r.scheduled_receipt for r in con.dc_plan] == [30, 0, 0, 0]


def test_el_deck_y_el_csv_dicen_si_se_neteo(tmp_path):
    sin = dj.run(dj.prepare_records(_df()))
    con = dj.run(dj.prepare_records(_df([0, 10, 0, 0, 0, 0, 0, 0])))

    titulos_sin = [f.title for f in dj.build_deck(sin, confidence=0.85).findings]
    assert titulos_sin[0] == "Releases do NOT net stock already on order"
    fuente = next(s for s in dj.build_deck(sin, confidence=0.85).data_sources
                  if s.field == "Stock already on order per period")
    assert fuente.cadence == "absent"

    titulos_con = [f.title for f in dj.build_deck(con, confidence=0.85).findings]
    assert titulos_con[0] == "Releases are net of stock already on order"
    assert "Releases do NOT net stock already on order" not in titulos_con

    ruta = dj.write_operational(con, tmp_path)["csv"]
    with open(ruta, newline="", encoding="utf-8") as fh:
        filas = list(csv.DictReader(fh))
    east_p1 = next(r for r in filas if r["node"] == "East" and r["period"] == "1")
    assert float(east_p1["scheduled_receipt"]) == 10.0

    ruta = dj.write_operational(sin, tmp_path / "sin")["csv"]
    with open(ruta, newline="", encoding="utf-8") as fh:
        fila = next(csv.DictReader(fh))
    assert fila["scheduled_receipt"] == "", "sin columna declarada, en blanco y no 0"


def test_el_deck_sigue_siendo_ascii():
    """Regla del repo: las superficies de consola y los entregables md no llevan
    caracteres fuera de ASCII."""
    report = dj.run(dj.prepare_records(_df([0, 10, 0, 0, 0, 0, 0, 0])))
    assert dj.build_deck(report, confidence=0.85).to_markdown().isascii()
