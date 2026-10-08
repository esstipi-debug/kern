"""F4 de la prueba del recorrido del 2026-09-19: Kern escribia decimales en Odoo.

187,6 rollos, 1,4 escaleras y 2,8 extintores en ordenes de compra borrador, y un punto
de reorden de 227,6. El plan redondeaba a UN decimal (``src/connectors/replenish.py``) y
el conector solo aplicaba el minimo del proveedor.

La unidad de Odoo sola no lo arregla: "Units" viene de fabrica con ``rounding=0.01``
(default del campo en uom/models/uom_uom.py de Odoo 17), asi que para Odoo 187,6 es una
cantidad legal. La regla: una unidad de la categoria "Unit" no se parte (paso 1); el
resto va a la precision de su unidad. Siempre hacia arriba, y lo que pidio el plan
queda visible al lado. Un producto que se compra en otra unidad (docena, caja) va a la
OC en esa unidad, que es en la que Odoo lee el minimo y el precio del proveedor.
"""

from __future__ import annotations

import csv
import math
import sys

import pytest

from jobs import odoo_job
from src.connectors import InventoryLevel, Order, OrderLine, Product
from src.connectors.odoo import InMemoryOdoo, OdooConnector, OdooError
from src.connectors.replenish import plan_replenishment
from src.constraints import purchasable_quantity

# -- la regla: src/constraints.py ----------------------------------------------


def test_una_pieza_sube_al_entero_y_conserva_lo_que_pidio_el_plan():
    lote = purchasable_quantity(187.6, unit_step=1.0)

    assert lote.quantity == 188.0
    assert lote.asked == 187.6
    assert lote.adjusted is True
    assert lote.rule == "multiple 1"


def test_el_ruido_de_coma_flotante_no_sube_un_paso_entero():
    """``round_up_to_multiple(0.1 * 3, 0.1)`` da 0.4: el cociente sale
    3.0000000000000004 y ``ceil`` lo sube. La regla mira primero si ya es legal."""
    assert purchasable_quantity(0.1 * 3, unit_step=0.1).quantity == 0.3
    assert purchasable_quantity(187.6, unit_step=0.01).quantity == 187.6
    assert purchasable_quantity(2.3456, unit_step=0.001).quantity == 2.346


def test_el_minimo_del_proveedor_manda_y_queda_sobre_el_paso_de_la_unidad():
    assert purchasable_quantity(12.8, unit_step=1.0, minimum_order_quantity=50.0).quantity == 50.0
    lote = purchasable_quantity(12.8, unit_step=1.0, minimum_order_quantity=49.5)

    assert lote.quantity == 50.0
    assert lote.rule == "moq 49.5, multiple 1"


def test_nada_que_comprar_no_se_sube_al_minimo():
    lote = purchasable_quantity(0.0, unit_step=1.0, minimum_order_quantity=50.0)

    assert lote.quantity == 0.0
    assert lote.adjusted is False


def test_sin_paso_ni_minimo_la_cantidad_pasa_intacta():
    lote = purchasable_quantity(187.6)

    assert lote.quantity == 187.6
    assert lote.adjusted is False
    assert lote.rule == ""


def test_una_cantidad_negativa_es_un_error_no_un_lote():
    with pytest.raises(ValueError):
        purchasable_quantity(-1.0, unit_step=1.0)


# -- la unidad de Odoo: src/connectors/odoo.py ------------------------------------

_DOCENA = 1 / 12  # uom.uom.factor de "Dozens" en Odoo 17: 1 unidad = 1/12 docena


def _odoo_con_unidades() -> InMemoryOdoo:
    """Cuatro productos como los deja Odoo 17: una pieza en "Units" (rounding 0.01 de
    fabrica), uno por kilo, uno sin unidad legible y un guante que se guarda por unidad
    y se compra por docena. ``uom_po_id`` repite ``uom_id`` cuando nadie lo cambio, como
    lo deja Odoo (product_template.py::_compute_uom_po_id)."""
    return InMemoryOdoo({
        "product.product": {
            1: {"default_code": "CINTA", "name": "Cinta", "list_price": 10.0, "standard_price": 5.0,
                "uom_id": [1, "Units"], "uom_po_id": [1, "Units"]},
            2: {"default_code": "GRANEL", "name": "Granel", "list_price": 3.0, "standard_price": 1.0,
                "uom_id": [2, "kg"], "uom_po_id": [2, "kg"]},
            3: {"default_code": "RARO", "name": "Raro", "list_price": 1.0, "standard_price": 1.0},
            4: {"default_code": "GUANTE", "name": "Guante", "list_price": 15.0, "standard_price": 10.0,
                "uom_id": [1, "Units"], "uom_po_id": [3, "Dozens"]},
        },
        "uom.uom": {
            1: {"name": "Units", "rounding": 0.01, "category_id": [1, "Unit"], "factor": 1.0},
            2: {"name": "kg", "rounding": 0.001, "category_id": [2, "Weight"], "factor": 1.0},
            3: {"name": "Dozens", "rounding": 0.01, "category_id": [1, "Unit"], "factor": _DOCENA},
        },
        "uom.category": {1: {"name": "Unit"}, 2: {"name": "Weight"}},
        "stock.location": {10: {"usage": "internal"}},
        "stock.quant": {
            100: {"product_id": [1, "Cinta"], "location_id": [10, "S"], "quantity": 40.0},
            101: {"product_id": [2, "Granel"], "location_id": [10, "S"], "quantity": 10.0},
        },
        "product.supplierinfo": {
            400: {"product_id": [1, "Cinta"], "partner_id": [70, "Acme"], "sequence": 1, "min_qty": 0.0},
            401: {"product_id": [2, "Granel"], "partner_id": [70, "Acme"], "sequence": 1, "min_qty": 0.0},
            402: {"product_id": [3, "Raro"], "partner_id": [70, "Acme"], "sequence": 1, "min_qty": 0.0},
            403: {"product_id": [4, "Guante"], "partner_id": [70, "Acme"], "sequence": 1, "min_qty": 0.0,
                  "price": 120.0},
        },
        "purchase.order": {},
        # Una linea cancelada alcanza para que el stand-in exponga el campo de unidad
        # de la linea de OC con el nombre de Odoo 17 (el stand-in deduce los campos
        # de los registros que tiene).
        "purchase.order.line": {900: {"product_uom": [1, "Units"], "state": "cancel"}},
        "stock.warehouse.orderpoint": {},
    })


class _SinUnidades:
    """Un Odoo que no deja leer ``uom.uom``: un usuario de API recortado, un modulo roto."""

    def __init__(self, inner: InMemoryOdoo) -> None:
        self._inner = inner

    def execute_kw(self, model, method, args, kwargs=None):
        if model == "uom.uom":
            raise OdooError("simulado: sin acceso a uom.uom")
        return self._inner.execute_kw(model, method, args, kwargs)

    def records(self, model):
        return self._inner.records(model)


def _unica_orden(odoo: InMemoryOdoo) -> dict:
    (orden,) = odoo.records("purchase.order").values()
    return orden


def test_una_pieza_tiene_paso_uno_aunque_odoo_la_deje_en_centesimos():
    pasos = OdooConnector(_odoo_con_unidades()).unit_step_by_sku()

    assert pasos.by_sku == {"CINTA": 1.0, "GRANEL": 0.001, "GUANTE": 1.0}
    assert pasos.missing == ("RARO",)
    assert pasos.category_readable is True


def test_la_unidad_de_compra_distinta_se_lee_con_su_conversion():
    pasos = OdooConnector(_odoo_con_unidades()).unit_step_by_sku()

    assert pasos.purchase_uom_differs == ("GUANTE",)
    docena = pasos.purchase["GUANTE"]
    assert (docena.uom_id, docena.name, docena.stock_per_unit, docena.step) == (3, "Dozens", 12.0, 1.0)


def test_sin_categoria_de_unidad_el_paso_es_la_precision_y_se_declara():
    """Una base sin ``uom.uom.category_id`` no puede reconocer una pieza: no se
    inventa un entero, se usa la precision de Odoo y se dice."""
    odoo = _odoo_con_unidades()
    for fila in odoo.records("uom.uom").values():
        del fila["category_id"]

    pasos = OdooConnector(odoo).unit_step_by_sku()

    assert pasos.by_sku["CINTA"] == 0.01
    assert pasos.category_readable is False


def test_la_oc_borrador_sale_en_unidades_enteras_y_en_la_unidad_del_producto():
    odoo = _odoo_con_unidades()

    resultado = OdooConnector(odoo).create_draft_purchase_orders(
        {"CINTA": 187.6, "GRANEL": 2.3456}, approved_by="compras@cliente.com"
    )

    # Por producto y no por ``name``: la linea puede dejar de mandar ``name`` (F5).
    lineas = {cmd[2]["product_id"]: cmd[2] for cmd in _unica_orden(odoo)["order_line"]}
    assert lineas[1]["product_qty"] == 188.0
    assert lineas[2]["product_qty"] == 2.346
    # La cantidad esta en la unidad del PRODUCTO; sin nombrarla, Odoo pone la de
    # compra (purchase_order_line.py::_product_id_change).
    assert lineas[1]["product_uom"] == 1
    assert lineas[2]["product_uom"] == 2
    # Lo que pidio el plan no se esconde: viaja en el resultado, con la regla.
    assert resultado.lot_adjustments["CINTA"].asked == 187.6
    assert resultado.lot_adjustments["CINTA"].rule == "multiple 1"
    assert resultado.raised_to_min == {}
    assert resultado.purchase_units == {}


def test_con_unidad_de_compra_distinta_la_linea_va_en_la_unidad_de_compra():
    """24 guantes que el proveedor vende por docena son 2 docenas, no 24.

    Sin nombrar unidad, Odoo 17 pone la de compra y lee 24 docenas. Nombrando la de
    stock, el precio que completa Odoo sale por docena
    (``_compute_price_unit_and_date_planned_and_name``) sobre una cantidad en unidades:
    12 veces mas caro. La linea va entera en la unidad de compra: cantidad, unidad y
    precio.
    """
    odoo = _odoo_con_unidades()

    resultado = OdooConnector(odoo).create_draft_purchase_orders(
        {"GUANTE": 24.0}, approved_by="compras@cliente.com", prices={"GUANTE": 10.0}
    )

    ((_, _, linea),) = _unica_orden(odoo)["order_line"]
    assert linea["product_qty"] == 2.0
    assert linea["product_uom"] == 3
    assert linea["price_unit"] == 120.0
    assert resultado.purchase_units["GUANTE"].name == "Dozens"
    assert resultado.lot_adjustments["GUANTE"].quantity == 2.0


def test_el_minimo_del_proveedor_se_lee_en_la_unidad_de_compra():
    """``min_qty`` esta en la unidad del proveedor (``product.supplierinfo.product_uom``
    es un related a ``uom_po_id``): un minimo de 3 son 3 docenas, no 3 guantes."""
    odoo = _odoo_con_unidades()
    odoo.records("product.supplierinfo")[403]["min_qty"] = 3.0

    resultado = OdooConnector(odoo).create_draft_purchase_orders(
        {"GUANTE": 20.0}, approved_by="compras@cliente.com"
    )

    ((_, _, linea),) = _unica_orden(odoo)["order_line"]
    assert linea["product_qty"] == 3.0
    assert resultado.raised_to_min["GUANTE"] == pytest.approx((20 / 12, 3.0))


def test_en_odoo_19_la_unidad_de_la_linea_va_en_product_uom_id():
    """Odoo 19 renombro el campo (purchase_order_line.py: ``product_uom_id``). Mandar
    ``product_uom`` ahi haria fallar el create de la OC entera."""
    odoo = _odoo_con_unidades()
    odoo.records("purchase.order.line")[900] = {"product_uom_id": [1, "Units"], "state": "cancel"}

    OdooConnector(odoo).create_draft_purchase_orders({"CINTA": 187.6}, approved_by="compras@cliente.com")

    ((_, _, linea),) = _unica_orden(odoo)["order_line"]
    assert linea["product_uom_id"] == 1
    assert "product_uom" not in linea


def test_el_minimo_del_proveedor_sigue_subiendo_y_se_informa_aparte():
    odoo = _odoo_con_unidades()
    odoo.records("product.supplierinfo")[400]["min_qty"] = 50.0

    resultado = OdooConnector(odoo).create_draft_purchase_orders(
        {"CINTA": 12.8}, approved_by="compras@cliente.com"
    )

    assert resultado.raised_to_min == {"CINTA": (12.8, 50.0)}
    assert resultado.lot_adjustments["CINTA"].quantity == 50.0


def test_sin_unidad_legible_la_linea_no_nombra_unidad_ni_inventa_un_entero():
    odoo = _odoo_con_unidades()

    resultado = OdooConnector(odoo).create_draft_purchase_orders(
        {"RARO": 7.5}, approved_by="compras@cliente.com"
    )

    ((_, _, linea),) = _unica_orden(odoo)["order_line"]
    assert linea["product_qty"] == 7.5
    assert "product_uom" not in linea
    assert resultado.lot_adjustments == {}


def test_un_sku_sin_proveedor_no_aparece_como_ajustado():
    """Un SKU en ``unsourced`` no tiene linea de OC: no hay cantidad que la OC le haya
    cambiado, aunque se compre por docena."""
    odoo = _odoo_con_unidades()
    del odoo.records("product.supplierinfo")[403]

    resultado = OdooConnector(odoo).create_draft_purchase_orders(
        {"GUANTE": 20.0, "CINTA": 187.6}, approved_by="compras@cliente.com"
    )

    assert resultado.unsourced == ("GUANTE",)
    assert "GUANTE" not in resultado.lot_adjustments
    assert resultado.purchase_units == {}


def test_sin_poder_leer_la_unidad_no_se_crea_la_oc():
    """La OC es irreversible: sin saber en que unidad va la cantidad no se escribe."""
    odoo = _odoo_con_unidades()

    with pytest.raises(OdooError):
        OdooConnector(_SinUnidades(odoo)).create_draft_purchase_orders(
            {"CINTA": 187.6}, approved_by="compras@cliente.com"
        )

    assert odoo.records("purchase.order") == {}


def test_el_punto_de_reorden_se_escribe_hacia_arriba_en_la_unidad():
    cambios = OdooConnector(_odoo_con_unidades()).stage_restock(
        {"CINTA": 187.6, "GRANEL": 2.3456}, idempotency_key="f4"
    )

    minimos = {c.entity_id: c.after for c in cambios.changes}
    assert minimos == {"CINTA": 228.0, "GRANEL": 12.346}  # 40 + 187.6 ; 10 + 2.3456


def test_sin_poder_leer_la_unidad_el_punto_de_reorden_sale_como_antes_y_lo_dice():
    """Reversible y ya declarado en el entregable: el minimo sale sin redondear y el
    motivo viaja en el changeset."""
    cambios = OdooConnector(_SinUnidades(_odoo_con_unidades())).stage_restock(
        {"CINTA": 187.6}, idempotency_key="f4"
    )

    assert cambios.changes[0].after == 227.6
    assert "sin redondear a la unidad de Odoo" in cambios.reason


# -- el plan: src/connectors/replenish.py -----------------------------------------


class _Fuente:
    """Un SKU con demanda irregular: el pronostico SES sale con decimales."""

    def list_products(self):
        return [Product(sku="CINTA", title="Cinta", price=10.0, cost=5.0)]

    def inventory_levels(self):
        return [InventoryLevel(sku="CINTA", available=5.0)]

    def orders(self):
        cantidades = (4.0, 2.0, 3.0, 5.0, 2.0, 3.0, 4.0, 2.0, 3.0, 5.0, 2.0, 3.0)
        return [
            Order(order_id=f"O{i}", created_at=f"2026-01-{i + 1:02d}",
                  lines=(OrderLine(sku="CINTA", quantity=q, price=10.0),))
            for i, q in enumerate(cantidades)
        ]


def test_el_plan_redondea_hacia_arriba_y_deja_visible_lo_que_pidio():
    plan = plan_replenishment(_Fuente(), cover_periods=8.0, method="ses",
                              unit_step_by_sku={"CINTA": 1.0})

    (linea,) = plan.lines
    assert linea.restock_unrounded == pytest.approx(19.7)
    assert linea.restock_qty == 20.0
    assert linea.unit_step == 1.0
    # Lo que va al conector es lo redondeado: el CSV, la PO y el minimo dicen lo mismo.
    assert plan.restock == {"CINTA": 20.0}


def test_el_plan_sin_unidad_declarada_no_inventa_un_redondeo():
    plan = plan_replenishment(_Fuente(), cover_periods=8.0, method="ses")

    (linea,) = plan.lines
    assert linea.unit_step is None
    assert linea.restock_qty == linea.restock_unrounded == pytest.approx(19.7)


# -- el entregable: jobs/odoo_job.py y examples/run_odoo.py -----------------------


def _odoo_con_estante_fraccionado() -> InMemoryOdoo:
    """Doce ventas diarias de CINTA y de GUANTE, con 5,5 cintas en el estante: el
    faltante de CINTA sale con decimales sea cual sea el metodo que elija el
    pronostico, y el GUANTE se repone por docena."""
    odoo = _odoo_con_unidades()
    odoo.records("stock.quant")[100]["quantity"] = 5.5
    ventas, lineas = odoo.records("sale.order"), odoo.records("sale.order.line")
    for i, q in enumerate((4.0, 2.0, 3.0, 5.0, 2.0, 3.0, 4.0, 2.0, 3.0, 5.0, 2.0, 3.0)):
        ventas[200 + i] = {"name": f"S{i:03d}", "date_order": f"2026-01-{i + 1:02d} 10:00:00",
                           "state": "sale", "order_line": [300 + i, 320 + i]}
        lineas[300 + i] = {"product_id": [1, "Cinta"], "product_uom_qty": q, "price_unit": 10.0}
        lineas[320 + i] = {"product_id": [4, "Guante"], "product_uom_qty": q, "price_unit": 15.0}
    return odoo


def test_el_entregable_muestra_las_dos_cantidades_y_declara_el_redondeo(tmp_path):
    reporte = odoo_job.run(
        odoo_job.prepare(None, {"odoo_rpc": _odoo_con_estante_fraccionado()}), cover_periods=8.0
    )

    (cinta,) = [ln for ln in reporte.lines if ln.sku == "CINTA"]
    assert cinta.restock_unrounded % 1 != 0, "el fixture tiene que pedir con decimales"
    assert cinta.restock_qty == math.ceil(cinta.restock_unrounded)
    assert reporte.restock["CINTA"] == cinta.restock_qty
    assert "RARO" in reporte.rounding_note  # sin unidad legible: se nombra
    assert "GUANTE" in reporte.rounding_note  # se compra por docena: se nombra

    ruta = odoo_job.write_operational(reporte, tmp_path)["csv"]
    with open(ruta, newline="", encoding="utf-8") as fh:
        filas = {f["sku"]: f for f in csv.DictReader(fh)}
    assert float(filas["CINTA"]["restock_qty"]) == cinta.restock_qty
    assert float(filas["CINTA"]["restock_unrounded"]) == pytest.approx(cinta.restock_unrounded)
    assert float(filas["CINTA"]["unit_step"]) == 1.0
    assert filas["RARO"]["unit_step"] == ""  # en blanco, no 0

    md = odoo_job.build_deck(reporte, client="Acme").to_markdown()
    assert "redondeadas hacia arriba" in md
    assert md.isascii()


def test_si_no_se_puede_leer_la_unidad_el_entregable_lo_dice_y_no_redondea():
    reporte = odoo_job.run(
        odoo_job.prepare(None, {"odoo_rpc": _SinUnidades(_odoo_con_estante_fraccionado())}),
        cover_periods=8.0,
    )

    (cinta,) = [ln for ln in reporte.lines if ln.sku == "CINTA"]
    assert cinta.unit_step is None
    assert cinta.restock_qty == cinta.restock_unrounded
    assert reporte.rounding_note.startswith("cantidades SIN redondear a la unidad de Odoo")


def test_la_consola_muestra_las_dos_cifras_y_la_oc_sale_entera(monkeypatch, tmp_path, capsys):
    from examples import run_odoo

    rpc = _odoo_con_estante_fraccionado()
    monkeypatch.setattr(run_odoo, "_rpc", lambda: rpc)
    monkeypatch.setenv("KERN_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["run_odoo.py", "--draft-po", "--approved-by", "ana"])

    run_odoo.main()

    salida = capsys.readouterr().out
    assert "plan sin redondear" in salida
    assert "Dozens (= " in salida  # el guante va por docena, y se dice cuanto es en unidades
    assert "multiplo 1" in salida  # la regla sale en espanol: lot_rule dice "multiple 1"
    assert salida.isascii()
    lineas = {cmd[2]["product_id"]: cmd[2] for cmd in _unica_orden(rpc)["order_line"]}
    assert lineas[1]["product_qty"] == math.floor(lineas[1]["product_qty"])
    assert lineas[1]["product_uom"] == 1
    assert lineas[4]["product_qty"] == math.floor(lineas[4]["product_qty"])
    assert lineas[4]["product_uom"] == 3
