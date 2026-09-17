"""El plan canal->ERP no vuelve a comprar lo que el ERP ya tiene en camino (C2).

``jobs/channel_replenishment_job.py`` dimensionaba ``qty = target - erp_on_hand``: el
estante, no la posicion. Una OC que el ERP ya emitio y que transporte atraso era, para
este job, indistinguible de una OC que no existe -- y el lector del ERP ya sabia leerlas
(``OdooConnector.open_purchase_qty_by_sku``, usado por ``jobs/odoo_job.py`` desde
kern#251). Faltaba el cable.

Misma disciplina de ``tests/test_neteo_en_transito.py``: dos corridas por caso, el VALOR
SE MUEVE, y la ausencia se declara -- un ERP que no expone OC abiertas lo dice, no
devuelve ceros.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from jobs import channel_replenishment_job as job  # noqa: E402
from src.connectors.odoo import InMemoryOdoo, OdooConnector, demo_odoo  # noqa: E402
from src.connectors.shopify import demo_shopify  # noqa: E402

#: Los ids de producto del Odoo demo, para colgarles lineas de OC abiertas.
_PRODUCT_IDS = {"SKU-1": 1, "SKU-2": 2, "SKU-3": 3}


def _odoo_con_oc(pendientes: dict[str, float] | None = None) -> InMemoryOdoo:
    """El Odoo demo (mismo catalogo y mismo stock) MAS lineas de OC abiertas.

    ``demo_odoo()`` no tiene modulo de Compras, asi que sobre el no hay nada que netear:
    ese es el caso de ausencia, y este es su contraparte medida.
    """
    base = demo_odoo()
    # El conector compara la unidad de la LINEA contra la del producto y, en la duda,
    # descarta: sin `uom_id` en el producto no hay con que comparar y la linea no suma.
    # Declararla es parte de armar el fixture, no un atajo alrededor de esa guarda.
    for rec in base.records("product.product").values():
        rec.setdefault("uom_id", [1, "Units"])
    lineas = base.records("purchase.order.line")
    for i, (sku, qty) in enumerate(sorted((pendientes or {}).items())):
        lineas[500 + i] = {
            "product_id": [_PRODUCT_IDS[sku], sku],
            "product_qty": float(qty),
            "qty_received": 0.0,
            "product_uom": [1, "Units"],
            "state": "purchase",
        }
    # Una unidad de medida para que el lector no descarte la linea por no poder
    # verificarla: el guard vive en el conector y este fixture no lo evade.
    base.records("uom.uom").setdefault(1, {"name": "Units"})
    return base


def _run(odoo: InMemoryOdoo | None = None, **params):
    return job.run(
        job.prepare(None, {"shopify_rpc": demo_shopify(), "odoo_rpc": odoo or demo_odoo(), **params})
    )


def _line(report, sku: str):
    return next(ln for ln in report.lines if ln.sku == sku)


# -- el valor se mueve ------------------------------------------------------------


def test_la_cantidad_baja_exactamente_lo_que_ya_viene_en_camino():
    """Dos corridas sobre el MISMO par canal/ERP, cambiando solo las OC abiertas.

    SKU-3 esta 20 unidades corto en el Odoo demo; con 8 ya compradas quedan 12.
    """
    sin = _run(_odoo_con_oc())
    con = _run(_odoo_con_oc({"SKU-3": 8.0}))

    assert sin.restock["SKU-3"] == 20.0
    assert con.restock["SKU-3"] == pytest.approx(sin.restock["SKU-3"] - 8.0)
    assert _line(con, "SKU-3").on_order == 8.0
    assert _line(con, "SKU-3").position == 28.0            # 20 en estante + 8 en camino
    assert con.n_netted == 1                                # solo SKU-3 tiene OC abierta


def test_lo_que_ya_viene_puede_sacar_al_sku_del_plan_entero():
    """El neteo alcanza al DISPARADOR, no solo a la cantidad."""
    con = _run(_odoo_con_oc({"SKU-3": 50.0}))
    assert "SKU-3" not in con.restock
    assert _line(con, "SKU-3").restock_qty == 0.0


def test_el_valor_del_plan_baja_con_la_cantidad():
    """La escalacion financiera se mide sobre este numero: si el neteo no lo mueve,
    un plan que ya esta comprado sigue empujando hacia el umbral."""
    sin = _run(_odoo_con_oc())
    con = _run(_odoo_con_oc({"SKU-3": 8.0}))
    # SKU-3 vale 5.00 en el ERP (el costo de libro, no el del canal)
    assert con.restock_value == pytest.approx(sin.restock_value - 8.0 * 5.0)


def test_una_oc_parcialmente_recibida_aporta_solo_lo_pendiente():
    """Sobre-estimar el en-transito es peor que ignorarlo: deja al cliente sin stock.
    La regla vive en el conector y este job la hereda sin re-implementarla."""
    odoo = _odoo_con_oc()
    odoo.records("purchase.order.line")[600] = {
        "product_id": [3, "SKU-3"], "product_qty": 50.0, "qty_received": 45.0,
        "product_uom": [1, "Units"], "state": "purchase",
    }
    report = _run(odoo)
    assert _line(report, "SKU-3").on_order == 5.0           # 50 pedidas - 45 recibidas
    assert report.restock["SKU-3"] == 15.0


# -- la ausencia se declara -------------------------------------------------------


def test_un_erp_sin_modulo_de_compras_lo_dice_y_no_finge_un_cero():
    report = _run(demo_odoo())                              # sin purchase.order.line

    assert report.n_netted == 0
    for ln in report.lines:
        assert ln.on_order is None, "sin lectura, None y nunca 0.0"
        assert ln.position == ln.erp_on_hand
    assert "NOT netted" in report.netting_note or "no se desconto" in report.netting_note
    assert report.netting_note in report.summary


def test_un_sku_sin_oc_abierta_queda_ausente_y_no_en_cero():
    """Ausente del mapa significa "sin OC abiertas", no "no medido": la distincion es
    del conector y este job la conserva en la linea."""
    report = _run(_odoo_con_oc({"SKU-3": 8.0}))
    assert _line(report, "SKU-1").on_order is None
    assert _line(report, "SKU-3").on_order == 8.0


def test_una_lectura_buena_sin_ninguna_oc_abierta_no_se_reporta_como_no_neteada():
    """El aviso se decide por si el ERP se pudo LEER, no por si alguna linea tenia
    algo en camino. Un ERP leido sin OC abiertas es una medicion, y decir "no se
    desconto" sobre el mandaria al comprador a restar a mano lo que ya esta restado."""
    # Una cotizacion (`draft`) deja el modulo de Compras presente y el mapa vacio: nadie
    # prometio nada todavia, que es justo lo que el conector no cuenta como suministro.
    odoo = _odoo_con_oc()
    odoo.records("purchase.order.line")[700] = {
        "product_id": [3, "SKU-3"], "product_qty": 99.0, "qty_received": 0.0,
        "product_uom": [1, "Units"], "state": "draft",
    }
    report = _run(odoo)
    assert report.on_order_by_sku == {} and report.n_netted == 0
    titulos = [f.title for f in job.build_deck(report, client="Demo", confidence=0.85).findings]
    assert "Quantities are net of stock already on order" in titulos
    assert "Quantities do NOT net stock already on order" not in titulos


def test_un_erp_que_no_expone_ordenes_de_compra_lo_dice_por_su_nombre():
    """Unleashed no lee OC abiertas hoy. El job no puede quedarse callado por eso."""

    class _ErpSinCompras:
        pass

    on_order, nota = job.read_erp_on_order(_ErpSinCompras())
    assert on_order is None
    assert "_ErpSinCompras" in nota and "NOT netted" in nota


def test_el_odoo_si_se_lee_con_el_lector_del_job_de_odoo():
    """El mismo lector, no una copia: la regla de estado, la de pendiente y la guarda
    de unidad de medida viven en un solo lugar."""
    on_order, nota = job.read_erp_on_order(OdooConnector(_odoo_con_oc({"SKU-1": 9.0})))
    assert on_order == {"SKU-1": 9.0}
    assert "descontado" in nota


# -- lo que el operador ve --------------------------------------------------------


def test_sin_neteo_el_deck_y_las_opciones_lo_advierten():
    report = _run(demo_odoo())

    deck = job.build_deck(report, client="Demo", confidence=0.85)
    titulos = [f.title for f in deck.findings]
    assert "Quantities do NOT net stock already on order" in titulos
    fuente = next(s for s in deck.data_sources if s.field == "Stock already on order")
    assert fuente.cadence == "absent"

    assert any("bought a second time" in r.risk_if_skipped for r in report.outcome.residuals)


def test_con_neteo_el_deck_dice_que_ya_esta_descontado_y_no_repite_el_aviso():
    report = _run(_odoo_con_oc({"SKU-3": 8.0}))

    deck = job.build_deck(report, client="Demo", confidence=0.85)
    titulos = [f.title for f in deck.findings]
    assert "Quantities are net of stock already on order" in titulos
    assert "Quantities do NOT net stock already on order" not in titulos
    fuente = next(s for s in deck.data_sources if s.field == "Stock already on order")
    assert fuente.cadence == "live"
    assert not any("bought a second time" in r.risk_if_skipped for r in report.outcome.residuals)


def test_el_csv_lleva_la_posicion_y_deja_en_blanco_lo_que_no_midio(tmp_path):
    con = _run(_odoo_con_oc({"SKU-3": 8.0}))
    ruta = job.write_operational(con, tmp_path)["csv"]
    with open(ruta, newline="", encoding="utf-8") as fh:
        filas = {r["sku"]: r for r in csv.DictReader(fh)}
    assert float(filas["SKU-3"]["on_order"]) == 8.0
    assert float(filas["SKU-3"]["position"]) == 28.0

    sin = _run(demo_odoo())
    ruta = job.write_operational(sin, tmp_path / "sin")["csv"]
    with open(ruta, newline="", encoding="utf-8") as fh:
        fila = next(csv.DictReader(fh))
    assert fila["on_order"] == ""
    assert float(fila["position"]) == float(fila["erp_on_hand"])


def test_la_compuerta_de_qa_sigue_pasando_en_los_dos_caminos():
    assert job.verify(_run(demo_odoo())) == []
    assert job.verify(_run(_odoo_con_oc({"SKU-3": 8.0}))) == []
