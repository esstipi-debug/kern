"""El tool central de reposicion ve el estante y lo que viene en camino (N1d, H4).

``jobs/inventory_optimization.py`` dimensionaba ``s`` y ``Q`` y nunca miraba el
stock: decia cuanto pedir y jamas CUANDO, y una OC que transporte atraso dos
semanas era, para el, indistinguible de una OC que no existe -- se volvia a
pedir. La disciplina es la de ``tests/test_neteo_en_transito.py`` (kern#251),
que ya cubria la planilla y el conector de Odoo pero no este camino:

- que un campo exista no acredita nada: cada caso corre DOS veces con insumos
  distintos y afirma que el VALOR SE MUEVE;
- el neteo alcanza al DISPARADOR, no solo a la cantidad;
- la ausencia se declara, nunca se asume cero: sin columna de en-transito,
  ``on_order`` es ``None`` y el entregable dice que no pudo netear.
"""
from __future__ import annotations

import csv
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from jobs import deliverables, intake, inventory_deliverable, qa
from jobs import inventory_optimization as io
from nosilo_core.registry import JobRequest
from scm_agent import tool_options
from scm_agent.tools import inventory_tool
from src.ddmrp import net_flow_position

# -- fixtures -------------------------------------------------------------------


def _demand(sku: str = "A", mu: float = 20.0, cost: float = 5.0, periods: int = 30) -> pd.DataFrame:
    rng = np.random.default_rng(1)
    dates = pd.date_range("2025-01-06", periods=periods, freq="W-MON")
    return pd.DataFrame({
        "date": dates, "product_id": sku,
        "quantity": [max(0, round(rng.normal(mu, mu * 0.2))) for _ in dates],
        "unit_cost": cost, "lead_time_days": 14,
    })


def _intermittent(sku: str = "Z") -> pd.DataFrame:
    qty = [0, 0, 0, 9, 0, 0, 0, 0, 7, 0] * 4
    return pd.DataFrame({
        "date": pd.date_range("2025-01-06", periods=40, freq="W-MON"), "product_id": sku,
        "quantity": qty, "unit_cost": 12.0, "lead_time_days": 14,
    })


def _rec(report, sku):
    return next(r for r in report.recommendations if r.product_id == sku)


@pytest.fixture(scope="module")
def trigger() -> float:
    """El punto de reorden del SKU A sin posicion: el disparador que se prueba."""
    return _rec(io.run(_demand()), "A").reorder_point


# -- la identidad que se reusa, no se reimplementa ----------------------------


def test_la_posicion_es_la_identidad_de_ddmrp():
    report = io.run(_demand(), on_hand_by_sku={"A": 30.0}, on_order_by_sku={"A": 12.0})
    a = _rec(report, "A")
    assert a.inventory_position == net_flow_position(30.0, 12.0, 0.0) == 42.0


# -- el disparador se netea, no solo la cantidad -------------------------------


def test_el_valor_se_mueve_y_el_neteo_alcanza_al_disparador(trigger):
    """Un estante 1 unidad bajo ``s`` dispara; 50 en camino lo cubren y NO dispara.
    Si solo cambiara la cantidad, el en-transito se compraria igual."""
    sin = io.run(_demand(), on_hand_by_sku={"A": trigger - 1}, on_order_by_sku={"A": 0.0})
    con = io.run(_demand(), on_hand_by_sku={"A": trigger - 1}, on_order_by_sku={"A": 50.0})
    a_sin, a_con = _rec(sin, "A"), _rec(con, "A")

    assert a_sin.order_now is True and a_sin.order_qty_now == a_sin.order_quantity
    assert a_con.order_now is False and a_con.order_qty_now == 0.0
    assert a_con.inventory_position == pytest.approx(a_sin.inventory_position + 50.0)
    assert sin.n_order_now == 1 and con.n_order_now == 0
    assert sin.n_reorders_avoided_by_transit == 0
    assert con.n_reorders_avoided_by_transit == 1     # la doble compra que se evito
    assert con.units_on_order == 50.0
    assert qa.verify(sin) == [] and qa.verify(con) == []


def test_arriba_del_disparador_no_se_pide_nada(trigger):
    report = io.run(_demand(), on_hand_by_sku={"A": trigger + 10}, on_order_by_sku={"A": 0.0})
    a = _rec(report, "A")
    assert a.order_now is False and a.order_qty_now == 0.0
    assert report.n_order_now == 0


def test_rs_ordena_hasta_S_y_tambien_netea():
    """La segunda politica. Cubrir solo (s,Q) deja la mitad del defecto."""
    sin = io.run(_intermittent(), on_hand_by_sku={"Z": 3.0}, on_order_by_sku={"Z": 0.0})
    con = io.run(_intermittent(), on_hand_by_sku={"Z": 3.0}, on_order_by_sku={"Z": 4.0})
    z_sin, z_con = _rec(sin, "Z"), _rec(con, "Z")
    assert z_sin.policy_kind == "(R, S)"
    assert z_sin.order_qty_now == pytest.approx(z_sin.order_up_to - 3.0)
    assert z_con.order_qty_now == pytest.approx(z_sin.order_qty_now - 4.0)
    assert qa.verify(con) == []


# -- la ausencia se declara, nunca se asume cero ------------------------------


def test_sin_mapa_de_en_transito_no_inventa_un_cero(trigger):
    report = io.run(_demand(), on_hand_by_sku={"A": trigger - 1}, on_order_by_sku=None)
    a = _rec(report, "A")
    assert a.on_order is None, "sin columna, on_order tiene que ser None y no 0.0"
    assert a.inventory_position == a.on_hand
    assert a.order_now is True                        # se dimensiono contra el estante
    assert report.n_positioned == 1 and report.n_netted == 0

    out = tool_options.inventory_options(report)
    assert any("could NOT be netted" in r.description for r in out.residuals)
    deck = inventory_deliverable.build(report)
    assert any(f.title == "In-transit stock NOT counted" for f in deck.findings)
    assert any("Check open POs by hand" in r for r in deck.recommendations)


def test_sku_ausente_del_mapa_es_none_no_cero(trigger):
    """Un mapa vacio, o uno que nombra a otro SKU, no midio a este."""
    for mapa in ({}, {"OTRO": 5.0}):
        report = io.run(_demand(), on_hand_by_sku={"A": trigger - 1}, on_order_by_sku=mapa)
        assert _rec(report, "A").on_order is None
        assert report.n_netted == 0


def test_con_mapa_el_entregable_no_advierte_y_cuenta_lo_neteado(trigger):
    report = io.run(_demand(), on_hand_by_sku={"A": trigger - 1}, on_order_by_sku={"A": 0.0})
    out = tool_options.inventory_options(report)
    assert not any("could NOT be netted" in r.description for r in out.residuals)
    deck = inventory_deliverable.build(report)
    assert not any(f.title == "In-transit stock NOT counted" for f in deck.findings)
    assert report.n_netted == 1


def test_sin_posicion_todo_queda_en_blanco_como_siempre():
    """El control negativo: la corrida de siempre, sin stock en el archivo."""
    report = io.run(_demand())
    a = _rec(report, "A")
    assert (a.on_hand, a.on_order, a.inventory_position, a.order_now, a.order_qty_now) == (None,) * 5
    assert report.n_positioned == report.n_netted == report.n_order_now == 0
    out = tool_options.inventory_options(report)
    assert not any(o.label.startswith("Release") for o in out.options)
    assert not any("netted" in r.description for r in out.residuals)
    deck = inventory_deliverable.build(report)
    assert not any("due for an order" in f.title for f in deck.findings)
    assert qa.verify(report) == []


def test_un_sku_sin_politica_lleva_la_posicion_pero_no_un_disparo():
    """La posicion es un HECHO del SKU; el disparo necesita una politica."""
    thin = _demand(periods=3)
    report = io.run(thin, on_hand_by_sku={"A": 7.0}, on_order_by_sku={"A": 2.0})
    a = _rec(report, "A")
    assert a.policy_kind == "insufficient_history"
    assert (a.on_hand, a.on_order, a.inventory_position) == (7.0, 2.0, 9.0)
    assert a.order_now is None and a.order_qty_now is None
    assert qa.verify(report) == []


# -- las opciones ofrecen la terminal de ejecucion -----------------------------


def test_las_opciones_ofrecen_soltar_las_ordenes_que_vencen_hoy(trigger):
    report = io.run(_demand(), on_hand_by_sku={"A": trigger - 1}, on_order_by_sku={"A": 0.0})
    out = tool_options.inventory_options(report)
    labels = [o.label for o in out.options]
    assert labels[0].startswith("Adopt")
    assert labels[1] == "Release the 1 replenishment order(s) due now"
    assert "A: position" in out.options[1].summary and "order_now = yes" in out.options[1].action


# -- QA rechaza un disparo inconsistente ----------------------------------------


def test_qa_rechaza_un_disparo_que_contradice_la_posicion(trigger):
    report = io.run(_demand(), on_hand_by_sku={"A": trigger - 1}, on_order_by_sku={"A": 0.0})
    a = _rec(report, "A")
    flipped = replace(a, order_now=False, order_qty_now=0.0)
    assert any("contradicts" in i for i in qa.verify(replace(report, recommendations=[flipped])))
    wrong_qty = replace(a, order_qty_now=a.order_qty_now + 1)
    assert any("what the policy orders" in i for i in qa.verify(replace(report, recommendations=[wrong_qty])))
    ghost = replace(_rec(io.run(_demand()), "A"), inventory_position=5.0)
    assert any("without stock on hand" in i for i in qa.verify(replace(report, recommendations=[ghost])))
    broken = replace(a, inventory_position=a.inventory_position + 1)
    assert any("on hand + on order" in i for i in qa.verify(replace(report, recommendations=[broken])))


# -- CSV, Excel y md ------------------------------------------------------------


def test_los_entregables_llevan_la_posicion_y_dejan_en_blanco_lo_no_medido(tmp_path, trigger):
    con = io.run(_demand(), on_hand_by_sku={"A": trigger - 1}, on_order_by_sku=None)
    path = deliverables.write_csv(con, tmp_path / "summary.csv")
    with open(path, newline="", encoding="utf-8") as fh:
        row = next(csv.DictReader(fh))
    assert float(row["on_hand"]) == pytest.approx(trigger - 1, abs=0.01)
    assert row["on_order"] == ""                       # no medido, no cero
    assert row["order_now"] == "yes" and float(row["order_qty_now"]) > 0

    md = deliverables.write_report_md(con, tmp_path / "report.md").read_text(encoding="utf-8")
    assert "## Orders due now" in md and "In-transit stock NOT counted" in md

    sin = io.run(_demand())
    path = deliverables.write_csv(sin, tmp_path / "plain.csv")
    with open(path, newline="", encoding="utf-8") as fh:
        row = next(csv.DictReader(fh))
    assert row["on_hand"] == "" and row["order_now"] == ""
    md = deliverables.write_report_md(sin, tmp_path / "plain.md").read_text(encoding="utf-8")
    assert "Orders due now" not in md


# -- el productor: la posicion desde el archivo del cliente --------------------


def _archivo(tmp_path: Path, name: str, *, en_transito: str | None = "En Tránsito",
             stock: str | None = "Stock", extra: dict | None = None) -> Path:
    """Export estilo LatAm, en orden INVERSO de fecha, con la celda de en-transito de
    B-2 vacia: la ultima fila por SKU tiene que ganar y una celda vacia no es cero."""
    rows = []
    for d in range(1, 13):
        base = {"Fecha": f"2026-01-{d:02d}", "Código": "A-1", "Cantidad": 10, "Costo": 20}
        if stock:
            base[stock] = 100 + d
        if en_transito:
            base[en_transito] = 5 * d
        rows.append({**base, **(extra or {})})
        other = {"Fecha": f"2026-01-{d:02d}", "Código": "B-2", "Cantidad": 3, "Costo": 8}
        if stock:
            other[stock] = 40
        if en_transito:
            other[en_transito] = None
        rows.append({**other, **(extra or {})})
    path = tmp_path / name
    pd.DataFrame(rows).iloc[::-1].to_csv(path, index=False)
    return path


def test_stock_position_toma_la_ultima_fila_por_sku_en_orden_de_fecha(tmp_path):
    pos = intake.stock_position(_archivo(tmp_path, "pos.csv"))
    by = pos.set_index("product_id")
    assert by.loc["A-1", "on_hand"] == 112 and by.loc["A-1", "on_order"] == 60
    assert by.loc["B-2", "on_hand"] == 40 and np.isnan(by.loc["B-2", "on_order"])
    assert pos.attrs["position_headers"] == {"on_hand": "Stock", "on_order": "En Tránsito"}
    assert pos.attrs["as_of"] == "2026-01-12"
    assert "not bought twice" in pos.attrs["position_note"]

    on_hand, on_order = intake.position_maps(pos)
    assert on_hand == {"A-1": 112.0, "B-2": 40.0}
    assert on_order == {"A-1": 60.0}                   # B-2 no esta: no medido


def test_stock_position_sin_en_transito_lo_dice_y_devuelve_none_en_el_mapa(tmp_path):
    pos = intake.stock_position(_archivo(tmp_path, "sin.csv", en_transito=None))
    assert "on_order" not in pos.attrs["position_headers"]
    assert "could NOT be netted" in pos.attrs["position_note"]
    on_hand, on_order = intake.position_maps(pos)
    assert on_hand == {"A-1": 112.0, "B-2": 40.0} and on_order is None


def test_stock_position_sin_stock_es_none(tmp_path):
    assert intake.stock_position(_archivo(tmp_path, "none.csv", stock=None, en_transito=None)) is None
    assert intake.position_maps(None) == (None, None)


@pytest.mark.parametrize("header", [
    "En Transito", "En Tránsito", "EN TRANSITO", "Por Recibir", "En Camino",
    "OC Pendiente", "On Order", "Incoming", "Open PO", "In Transit",
])
def test_detecta_los_nombres_reales_de_la_columna_de_en_transito(tmp_path, header):
    pos = intake.stock_position(_archivo(tmp_path, f"{abs(hash(header))}.csv", en_transito=header))
    assert pos.attrs["position_headers"]["on_order"] == header


def test_no_confunde_la_columna_de_salida_con_stock_en_camino(tmp_path):
    """``Pedir (Kern)`` es la SALIDA de la planilla de reposicion; leerla como
    suministro entrante netearia el plan contra si mismo."""
    pos = intake.stock_position(_archivo(tmp_path, "pedir.csv", en_transito=None,
                                         extra={"Pedir (Kern)": 30}))
    assert "on_order" not in pos.attrs["position_headers"]


def test_un_en_transito_negativo_no_es_una_medicion(tmp_path):
    pos = intake.stock_position(_archivo(tmp_path, "neg.csv", en_transito=None,
                                         extra={"En Transito": -5}))
    _, on_order = intake.position_maps(pos)
    assert on_order == {}


# -- el camino COMPLETO del agente: prepare -> run ---------------------------------


def _correr(path: Path):
    tool = inventory_tool()
    prepared = tool.prepare(
        JobRequest(brief="politica de inventario", data_path=str(path), params={"period": "D"}),
        None,
    )
    assert prepared.status == "ok", prepared.messages
    return tool.run(prepared.payload, {"period": "D"})


def test_el_agente_netea_desde_el_csv_y_lo_dice_en_el_resumen(tmp_path):
    produced = _correr(_archivo(tmp_path, "agente.csv"))
    a = next(r for r in produced.report.recommendations if r.product_id == "A-1")
    assert (a.on_hand, a.on_order, a.inventory_position) == (112.0, 60.0, 172.0)
    assert a.order_now is not None
    assert produced.report.n_netted == 1 and produced.report.n_positioned == 2
    assert "on_order <- 'En Tránsito'" in produced.summary
    assert "due for an order now" in produced.summary
    assert inventory_tool().qa(produced.report) == []


def test_el_agente_dice_que_no_pudo_netear_cuando_falta_la_columna(tmp_path):
    produced = _correr(_archivo(tmp_path, "sin.csv", en_transito=None))
    a = next(r for r in produced.report.recommendations if r.product_id == "A-1")
    assert a.on_hand == 112.0 and a.on_order is None
    assert "could NOT be netted" in produced.summary


def test_el_mismo_archivo_sin_stock_sigue_sin_posicion(tmp_path):
    produced = _correr(_archivo(tmp_path, "plain.csv", stock=None, en_transito=None))
    assert produced.report.n_positioned == 0
    assert "position" not in produced.summary.lower()


def test_la_tool_declara_las_columnas():
    assert {"on_hand", "on_order"} <= set(inventory_tool().optional_columns)
