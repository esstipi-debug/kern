"""El plan de reposicion no vuelve a comprar lo que ya viene en camino (kern#251).

Las dos rutas que corre el cliente de autoservicio -- su planilla y el conector
del ERP -- dimensionaban la cantidad contra ``on_hand`` solo. Una OC ya emitida
no tenia por donde entrar, asi que se volvia a pedir entera.

La disciplina de estos tests es la del gate endurecido de la bateria: **que un
campo exista no acredita nada**. Cada caso corre DOS veces con insumos distintos
y afirma que el VALOR SE MUEVE, espejo de la sonda de ddmrp
(``[142.5, 142.5] -> [62.5, 62.5]`` con 80 unidades ya emitidas).
"""

from __future__ import annotations

import pytest
from openpyxl import Workbook

from jobs import excel_replenishment_job as job
from src.connectors.replenish import ReplenishmentLine, plan_replenishment
from src.ddmrp import net_flow_position

SHEET = "Stock Bodega"


def _planilla(path, *, en_transito=None, demand_column=False, header="En Transito"):
    """Planilla estilo cliente. ``en_transito`` None = la columna no existe."""
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET
    ws["A1"] = "INVENTARIO BODEGA CENTRAL"
    headers = ["Codigo", "Stock", "Punto Reorden"]
    if demand_column:
        headers.append("Demanda Semanal")
    if en_transito is not None:
        headers.append(header)
    for col, h in enumerate(headers, 1):
        ws.cell(row=3, column=col, value=h)

    # SKU-001 bajo su ROP; SKU-002 comodo. La demanda solo se usa en modo cobertura.
    filas = [("SKU-001", 42, 50, 10.0), ("SKU-002", 130, 80, 12.0)]
    for r, (code, stock, rop, demand) in enumerate(filas, 4):
        valores = [code, stock, rop]
        if demand_column:
            valores.append(demand)
        if en_transito is not None:
            valores.append(en_transito)
        for c, valor in enumerate(valores, 1):
            ws.cell(row=r, column=c, value=valor)
    wb.save(path)
    return path


def _qty(report, sku):
    return next(line.restock_qty for line in report.lines if line.sku == sku)


def _line(report, sku):
    return next(line for line in report.lines if line.sku == sku)


# -- la identidad que se reusa, no se reimplementa ----------------------------


def test_position_es_la_identidad_de_ddmrp_y_no_una_segunda_formula():
    """La resta vive en un solo lugar. Si alguien la copia, este test no lo ve --
    pero si alguien CAMBIA la identidad, las dos rutas cambian juntas."""
    assert net_flow_position(42.0, 80.0, 0.0) == 122.0
    assert net_flow_position(42.0, 0.0, 0.0) == 42.0


# -- planilla: modo punto de reorden ------------------------------------------


def test_planilla_rop_la_cantidad_baja_cuando_hay_stock_en_camino(tmp_path):
    """5 en camino sobre 42 en estante deja la posicion en 47, todavia bajo el
    ROP de 50: el disparador sigue activo y lo que cambia es la CANTIDAD."""
    sin = job.run(job.prepare(str(_planilla(tmp_path / "sin.xlsx", en_transito=0))))
    con = job.run(job.prepare(str(_planilla(tmp_path / "con.xlsx", en_transito=5))))

    # El valor SE MUEVE: no basta con que el campo exista.
    assert _qty(sin, "SKU-001") > _qty(con, "SKU-001") > 0
    assert _qty(con, "SKU-001") == pytest.approx(_qty(sin, "SKU-001") - 5)


def test_planilla_rop_no_pide_nada_cuando_lo_que_viene_ya_cubre_el_punto(tmp_path):
    """El neteo alcanza al DISPARADOR, no solo a la cantidad.

    SKU-001 tiene 42 en estante contra un ROP de 50: sin netear, dispara. Con 20
    unidades ya compradas la posicion es 62 y no hay nada que pedir. Sizing
    contra el estante compraba dos veces lo mismo."""
    con = job.run(job.prepare(str(_planilla(tmp_path / "cubierto.xlsx", en_transito=20))))
    assert _qty(con, "SKU-001") == 0.0
    assert _line(con, "SKU-001").position == 62.0


# -- planilla: modo cobertura de demanda --------------------------------------


def test_planilla_cobertura_tambien_netea(tmp_path):
    """La segunda rama del calculo. Cubrir solo una deja la mitad del defecto."""
    sin = job.run(job.prepare(str(
        _planilla(tmp_path / "sin.xlsx", en_transito=0, demand_column=True))))
    con = job.run(job.prepare(str(
        _planilla(tmp_path / "con.xlsx", en_transito=25, demand_column=True))))

    assert _qty(sin, "SKU-001") > _qty(con, "SKU-001")
    assert _qty(con, "SKU-001") == pytest.approx(_qty(sin, "SKU-001") - 25)


# -- planilla: rama de politica optimizada ------------------------------------


def test_planilla_politica_optimizada_tambien_netea(tmp_path):
    """La TERCERA rama. El ticket original decia que eran dos; son tres."""
    targets = {"SKU-001": {"reorder_point": 60.0, "order_up_to": 120.0}}
    sin = job.run(job.prepare(str(_planilla(tmp_path / "sin.xlsx", en_transito=0))),
                  optimized_targets=targets)
    con = job.run(job.prepare(str(_planilla(tmp_path / "con.xlsx", en_transito=10))),
                  optimized_targets=targets)

    assert _line(sin, "SKU-001").source == "kern_optimized"
    assert _qty(sin, "SKU-001") > _qty(con, "SKU-001") > 0
    assert _qty(con, "SKU-001") == pytest.approx(_qty(sin, "SKU-001") - 10)


# -- la ausencia se declara, nunca se asume cero ------------------------------


def test_planilla_sin_columna_no_inventa_un_cero(tmp_path):
    """``None`` y ``0.0`` son cosas distintas: la primera es "nadie nos dijo"."""
    rep = job.run(job.prepare(str(_planilla(tmp_path / "p.xlsx"))))
    assert rep.n_netted == 0
    for line in rep.lines:
        assert line.on_order is None, "sin columna, on_order tiene que ser None y no 0.0"
        assert line.position == line.on_hand


def test_planilla_con_columna_marca_cada_linea_neteada(tmp_path):
    rep = job.run(job.prepare(str(_planilla(tmp_path / "p.xlsx", en_transito=30))))
    assert rep.n_netted == len(rep.lines)
    for line in rep.lines:
        assert line.on_order == 30.0


def test_el_deck_dice_que_no_pudo_netear_cuando_falta_la_columna(tmp_path):
    rep = job.run(job.prepare(str(_planilla(tmp_path / "p.xlsx"))))
    residual = job.build_deck(rep, client="ACME").residual
    assert "NOT" in residual and "netted" in residual
    assert "manually" in residual


def test_el_deck_dice_cuantas_lineas_neteo_cuando_la_columna_existe(tmp_path):
    rep = job.run(job.prepare(str(_planilla(tmp_path / "p.xlsx", en_transito=30))))
    residual = job.build_deck(rep, client="ACME").residual
    assert "net of stock already on order" in residual
    assert "could NOT be" not in residual


# -- deteccion de la columna ---------------------------------------------------


@pytest.mark.parametrize("header", [
    "En Transito", "En Tránsito", "EN TRANSITO", "Por Recibir", "En Camino",
    "OC Pendiente", "On Order", "Incoming", "Open PO",
])
def test_detecta_los_nombres_reales_de_la_columna(tmp_path, header):
    """Los encabezados salen de exports reales, en los dos idiomas. ``_fold``
    saca los acentos, asi que 'En Tránsito' y 'En Transito' son el mismo."""
    rep = job.run(job.prepare(str(
        _planilla(tmp_path / f"{abs(hash(header))}.xlsx", en_transito=30, header=header))))
    assert rep.n_netted == len(rep.lines), f"no detecto el encabezado {header!r}"


def test_no_confunde_la_columna_de_salida_con_stock_en_camino(tmp_path):
    """``DEFAULT_ORDER_COLUMN`` es "Pedir (Kern)". Si un candidato como "pedido"
    la matcheara, el plan leeria su PROPIA salida como suministro entrante y la
    cantidad se netearia contra si misma hasta colapsar a cero en la segunda
    corrida. Por eso no hay candidato "pedido" pelado."""
    rep = job.run(job.prepare(str(
        _planilla(tmp_path / "p.xlsx", en_transito=30, header="Pedir (Kern)"))))
    assert rep.n_netted == 0
    assert all(line.on_order is None for line in rep.lines)


def test_columna_nombrada_explicitamente_gana(tmp_path):
    path = str(_planilla(tmp_path / "p.xlsx", en_transito=30, header="Comprometido Proveedor"))
    sin_param = job.run(job.prepare(path))
    con_param = job.run(job.prepare(path, {"on_order_column": "Comprometido Proveedor"}))
    assert sin_param.n_netted == 0
    assert con_param.n_netted == len(con_param.lines)


# -- ruta del conector ---------------------------------------------------------


class _Fuente:
    """InventorySource minima: dos SKU con historial suficiente para pronosticar."""

    def __init__(self, on_hand: float):
        self._on_hand = on_hand

    def list_products(self):
        from src.connectors import Product
        return [Product(sku="SKU-001", title="Tornillo", price=10.0, cost=5.0)]

    def inventory_levels(self):
        from src.connectors import InventoryLevel
        return [InventoryLevel(sku="SKU-001", available=self._on_hand)]

    def orders(self):
        from src.connectors import Order, OrderLine
        return [
            Order(order_id=f"O{i}", created_at=f"2026-01-{i:02d}",
                  lines=(OrderLine(sku="SKU-001", quantity=10.0, price=10.0),))
            for i in range(1, 13)
        ]


def test_conector_netea_cuando_le_pasan_el_mapa():
    sin = plan_replenishment(_Fuente(20.0), cover_periods=8.0)
    con = plan_replenishment(_Fuente(20.0), cover_periods=8.0,
                             on_order_by_sku={"SKU-001": 35.0})

    q_sin = sin.lines[0].restock_qty
    q_con = con.lines[0].restock_qty
    assert q_sin > q_con, "el valor tiene que moverse, no solo existir el campo"
    assert q_con == pytest.approx(max(0.0, q_sin - 35.0))


def test_conector_sin_mapa_declara_la_ausencia():
    plan = plan_replenishment(_Fuente(20.0), cover_periods=8.0)
    assert plan.lines[0].on_order is None
    assert plan.lines[0].position == 20.0


def test_conector_sku_ausente_del_mapa_no_se_lee_como_cero_medido():
    """Un SKU que no esta en el mapa no fue medido: ``None``, no ``0.0``."""
    plan = plan_replenishment(_Fuente(20.0), cover_periods=8.0,
                              on_order_by_sku={"OTRO-SKU": 99.0})
    assert plan.lines[0].on_order is None


def test_linea_rechazada_por_historial_no_finge_una_posicion():
    """``insufficient_history`` nunca se dimensiono: su posicion es None."""
    line = ReplenishmentLine("SKU-X", 5.0, 0.0, 0.0, 0.0, status="insufficient_history")
    assert line.position is None
    assert line.on_order is None


# -- productor real: Odoo ------------------------------------------------------


def _odoo_con_ocs(lineas):
    """Fake de Odoo con dos productos en la unidad 1 y las lineas de OC dadas."""
    from src.connectors.odoo import InMemoryOdoo, OdooConnector
    rpc = InMemoryOdoo({
        "product.product": {
            1: {"default_code": "SKU-001", "name": "A", "list_price": 10.0,
                "standard_price": 6.0, "uom_id": [1, "Units"]},
            2: {"default_code": "SKU-002", "name": "B", "list_price": 20.0,
                "standard_price": 12.0, "uom_id": [1, "Units"]},
        },
        "purchase.order.line": {i: linea for i, linea in enumerate(lineas, 500)},
    })
    return OdooConnector(rpc)


def test_odoo_cuenta_lo_PENDIENTE_y_no_lo_ordenado():
    """Una OC de 500 con 480 ya recibidas aporta 20, no 500.

    Sobre-estimar el en-transito es peor que ignorarlo: ignorarlo compra de mas,
    sobre-estimarlo deja al cliente sin stock."""
    con = _odoo_con_ocs([
        {"product_id": [1, "A"], "product_qty": 500.0, "qty_received": 480.0,
         "product_uom": [1, "Units"], "state": "purchase"},
    ])
    abiertas = con.open_purchase_qty_by_sku()
    assert abiertas.by_sku == {"SKU-001": 20.0}


def test_odoo_suma_varias_lineas_del_mismo_sku():
    con = _odoo_con_ocs([
        {"product_id": [1, "A"], "product_qty": 100.0, "qty_received": 0.0,
         "product_uom": [1, "Units"], "state": "purchase"},
        {"product_id": [1, "A"], "product_qty": 40.0, "qty_received": 15.0,
         "product_uom": [1, "Units"], "state": "done"},
    ])
    assert _odoo_con_ocs([])  # sanity: el fake vacio se construye
    assert con.open_purchase_qty_by_sku().by_sku == {"SKU-001": 125.0}


def test_odoo_no_cuenta_cotizaciones_como_suministro():
    """`draft`/`sent` son RFQ: ningun proveedor las prometio todavia."""
    con = _odoo_con_ocs([
        {"product_id": [1, "A"], "product_qty": 90.0, "qty_received": 0.0,
         "product_uom": [1, "Units"], "state": "draft"},
        {"product_id": [1, "A"], "product_qty": 10.0, "qty_received": 0.0,
         "product_uom": [1, "Units"], "state": "purchase"},
    ])
    assert con.open_purchase_qty_by_sku().by_sku == {"SKU-001": 10.0}


def test_odoo_descarta_la_linea_en_otra_unidad_en_vez_de_sumar_cajas_como_unidades():
    """El modo de falla mas caro: una conversion inventada infla el en-transito,
    el plan compra de menos, y el numero sigue pareciendo razonable."""
    con = _odoo_con_ocs([
        {"product_id": [1, "A"], "product_qty": 10.0, "qty_received": 0.0,
         "product_uom": [7, "Dozens"], "state": "purchase"},
        {"product_id": [2, "B"], "product_qty": 5.0, "qty_received": 0.0,
         "product_uom": [1, "Units"], "state": "purchase"},
    ])
    abiertas = con.open_purchase_qty_by_sku()
    assert abiertas.by_sku == {"SKU-002": 5.0}, "la linea en docenas no debe sumarse"
    assert abiertas.dropped["uom_distinta"] == 1


def test_odoo_una_linea_sin_unidad_legible_se_descarta_no_se_asume():
    con = _odoo_con_ocs([
        {"product_id": [1, "A"], "product_qty": 10.0, "qty_received": 0.0,
         "product_uom": False, "state": "purchase"},
    ])
    abiertas = con.open_purchase_qty_by_sku()
    assert abiertas.by_sku == {}
    assert abiertas.dropped["uom_distinta"] == 1


def test_odoo_sku_sin_oc_abierta_queda_AUSENTE_y_no_en_cero():
    """Ausente significa "sin OC abiertas". Un 0.0 seria una medicion afirmada,
    y el consumidor tiene que poder distinguirlas."""
    con = _odoo_con_ocs([
        {"product_id": [1, "A"], "product_qty": 10.0, "qty_received": 0.0,
         "product_uom": [1, "Units"], "state": "purchase"},
    ])
    by_sku = con.open_purchase_qty_by_sku().by_sku
    assert "SKU-002" not in by_sku


def test_odoo_el_mapa_enchufa_directo_en_el_plan_de_reposicion():
    """El productor y el consumidor hablan el mismo idioma: si esto se rompe,
    el parametro queda existiendo y sin nadie que lo llene -- el patron que este
    ticket existe para no repetir."""
    con = _odoo_con_ocs([
        {"product_id": [1, "A"], "product_qty": 35.0, "qty_received": 0.0,
         "product_uom": [1, "Units"], "state": "purchase"},
    ])
    mapa = con.open_purchase_qty_by_sku().by_sku
    sin = plan_replenishment(_Fuente(20.0), cover_periods=8.0)
    conn = plan_replenishment(_Fuente(20.0), cover_periods=8.0, on_order_by_sku=mapa)
    assert conn.lines[0].restock_qty == pytest.approx(
        max(0.0, sin.lines[0].restock_qty - 35.0))
