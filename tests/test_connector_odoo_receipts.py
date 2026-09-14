"""`OdooConnector.receipts()`: el plazo OBSERVADO, leido de un Odoo.

El conector leia `product.supplierinfo.delay` y lo mapeaba a `lead_time_days`.
Ese campo es la **promesa** del ERP. `stock.picking` no aparecia en el repo, y
`date_done` solo como alias de columna CSV -- o sea que el codigo sabia donde
Odoo guarda la fecha real y nunca se la pedia. Sobre una conexion viva la brecha
daba cero por construccion, que es el defecto exacto que el producto muestra.

Lo que estos tests fijan, en orden de cuanto duele que se rompa:

1. El plazo sale de `stock.move.date - purchase.order.date_order` y de ningun
   otro lado, y `delay` viaja SOLO como declarado.
2. Los campos que serian la respuesta atractiva y equivocada no se leen:
   `effective_date` (sesga achicando la brecha), `date_planned` (es
   `date_order + delay`, o sea brecha ~0), `picking.date` (la siembra el propio
   `date_order`, o sea cero uniforme).
3. Lo que no es una compra queda afuera: devoluciones de cliente, ajustes,
   componentes de produccion.
4. Un camion de N lineas es UNA observacion, no N identicas.
5. Y el camino completo llega hasta un `LeadTimeScan` con brecha medida.
"""
from __future__ import annotations

import pytest

from src.connectors.odoo import InMemoryOdoo, OdooConnector, OdooError
from src.supplier_scorecard import LEAD_TIME_MEASURED
from webapp.demo_lead_time_scan import scan_from_deliveries


def _odoo(**extra) -> InMemoryOdoo:
    """Un Odoo con un proveedor que promete 7 dias y entrega en ~20."""
    data = {
        "product.product": {
            1: {"default_code": "SKU-1", "name": "Rodamiento", "list_price": 10.0, "standard_price": 6.0},
            2: {"default_code": "SKU-2", "name": "Sello", "list_price": 4.0, "standard_price": 2.0},
        },
        "res.partner": {
            70: {"name": "Acme Sucursal Santiago", "commercial_partner_id": [69, "Acme"]},
            69: {"name": "Acme", "commercial_partner_id": [69, "Acme"]},
        },
        "stock.picking.type": {10: {"code": "incoming"}, 11: {"code": "outgoing"}},
        "product.supplierinfo": {
            400: {"partner_id": [70, "Acme Sucursal Santiago"], "product_id": [1, "Rodamiento"],
                  "delay": 7.0, "sequence": 1},
        },
        "purchase.order": {},
        "purchase.order.line": {},
        "stock.move": {},
    }
    # Tres ordenes, cada una recibida ~20 dias despues: promesa 7, realidad 20.
    for n, (dia_orden, dia_recepcion) in enumerate(
        [("2026-01-05", "2026-01-25"), ("2026-02-02", "2026-02-23"), ("2026-03-02", "2026-03-21")], 1
    ):
        po, pol = 200 + n, 300 + n
        data["purchase.order"][po] = {
            "name": f"PO-{n:03d}", "date_order": f"{dia_orden} 09:00:00",
            "date_approve": f"{dia_orden} 11:00:00", "partner_id": [70, "Acme Sucursal Santiago"],
            "state": "purchase",
        }
        data["purchase.order.line"][pol] = {"order_id": [po, f"PO-{n:03d}"]}
        data["stock.move"][400 + n] = {
            "state": "done", "date": f"{dia_recepcion} 15:00:00", "product_id": [1, "Rodamiento"],
            "purchase_line_id": [pol, "linea"], "picking_id": [500 + n, f"WH/IN/{n}"],
            "picking_type_id": [10, "Recepciones"],
        }
    for modelo, filas in extra.items():
        data.setdefault(modelo, {}).update(filas)
    return InMemoryOdoo(data)


def _conn(odoo=None) -> OdooConnector:
    return OdooConnector(odoo or _odoo())


# --- 1. el plazo observado --------------------------------------------------


def test_el_plazo_sale_de_las_dos_fechas_y_no_del_campo_del_erp():
    pull = _conn().receipts()
    filas = pull.grouped["Acme"]
    assert len(filas) == 3
    # 2026-01-05 09:00 -> 2026-01-25 15:00 = 20,25 dias. Con horas, no truncado:
    # el corte a diez caracteres es un error de un dia entero.
    assert filas[0]["lead_time_days"] == pytest.approx(20.25, abs=0.01)
    assert all(f["lead_time_source"] == LEAD_TIME_MEASURED for f in filas)


def test_el_delay_del_erp_viaja_solo_como_declarado():
    filas = _conn().receipts().grouped["Acme"]
    assert all(f["declared_lead_time_days"] == 7.0 for f in filas)
    # Y nunca como la observacion.
    assert all(f["lead_time_days"] != 7.0 for f in filas)


def test_un_delay_de_uno_no_es_una_promesa():
    """Es el default de Odoo. Sin piso, observado 20 contra declarado 1 da 1.900%
    de brecha relativa, gana `worst`, y el titular pasa a ser un artefacto."""
    odoo = _odoo()
    odoo.records("product.supplierinfo")[400]["delay"] = 1.0
    filas = _conn(odoo).receipts().grouped["Acme"]
    assert all("declared_lead_time_days" not in f for f in filas)


def test_las_dos_fechas_viajan_para_que_el_numero_se_pueda_verificar():
    fila = _conn().receipts().grouped["Acme"][0]
    assert fila["order_date"].date().isoformat() == "2026-01-05"
    assert fila["receipt_date"].date().isoformat() == "2026-01-25"
    assert fila["po_id"] == "PO-001"


def test_la_demora_de_aprobacion_se_reporta_aparte():
    """Una OC que espero tres semanas la aprobacion no se le cobra al proveedor."""
    fila = _conn().receipts().grouped["Acme"][0]
    assert fila["approval_latency_days"] == pytest.approx(2 / 24, abs=0.01)


# --- 2. los campos que NO se leen -------------------------------------------


@pytest.mark.parametrize(
    "campo,modelo,por_que",
    [
        ("effective_date", "purchase.order", "es el min() de las recepciones: sesga achicando la brecha"),
        ("date_planned", "purchase.order", "es date_order + delay: medir contra el reconstruye el propio delay"),
        ("scheduled_date", "stock.picking", "es una promesa, no una llegada"),
    ],
)
def test_los_campos_promesa_no_se_leen(campo, modelo, por_que):
    """Si alguno se leyera, el numero saldria mal sin que nada falle."""
    import inspect

    from src.connectors import odoo as mod

    fuente = inspect.getsource(mod.OdooConnector.receipts)
    cuerpo = fuente.split('"""', 2)[-1]  # el docstring SI los nombra, para explicarlos
    assert campo not in cuerpo, f"{campo} ({modelo}): {por_que}"


def test_sin_el_modulo_de_compras_falla_nombrandolo_en_vez_de_usar_la_promesa():
    odoo = _odoo()
    for m in odoo.records("stock.move").values():
        m.pop("purchase_line_id")
    with pytest.raises(OdooError, match="modulo de compras"):
        _conn(odoo).receipts()


# --- 3. lo que no es una compra ---------------------------------------------


def test_una_devolucion_de_cliente_no_es_una_recepcion_de_compra():
    """Odoo la registra como picking de entrada hecho, con fecha real y todo.

    Lo unico que la distingue es que su movimiento no tiene linea de OC.
    """
    odoo = _odoo(**{"stock.move": {
        900: {"state": "done", "date": "2026-01-10 10:00:00", "product_id": [1, "Rodamiento"],
              "purchase_line_id": False, "picking_id": [900, "WH/IN/R"],
              "picking_type_id": [10, "Recepciones"]},
    }})
    assert len(_conn(odoo).receipts().grouped["Acme"]) == 3


def test_un_movimiento_cancelado_dentro_de_una_recepcion_hecha_no_cuenta():
    odoo = _odoo()
    odoo.records("stock.move")[401]["state"] = "cancel"
    assert len(_conn(odoo).receipts().grouped["Acme"]) == 2


def test_una_orden_sin_confirmar_no_cuenta_y_se_reporta():
    odoo = _odoo()
    odoo.records("purchase.order")[201]["state"] = "draft"
    pull = _conn(odoo).receipts()
    assert len(pull.grouped["Acme"]) == 2
    assert pull.dropped["po_no_confirmada"] == 1


def test_una_recepcion_anterior_a_su_orden_es_un_dato_roto():
    odoo = _odoo()
    odoo.records("stock.move")[401]["date"] = "2025-12-01 10:00:00"
    pull = _conn(odoo).receipts()
    assert pull.dropped["plazo_negativo"] == 1
    assert len(pull.grouped["Acme"]) == 2


# --- 4. una recepcion es un evento ------------------------------------------


def test_un_camion_de_varias_lineas_es_una_observacion_no_varias():
    """N valores identicos no tienen dispersion interna, y esa desviacion
    falsamente apretada viaja despues al stock de seguridad."""
    odoo = _odoo()
    odoo.records("stock.move")[404] = {
        "state": "done", "date": "2026-01-25 15:00:00", "product_id": [2, "Sello"],
        "purchase_line_id": [301, "linea"], "picking_id": [501, "WH/IN/1"],
        "picking_type_id": [10, "Recepciones"],
    }
    por_envio = _conn(odoo).receipts()
    assert len(por_envio.grouped["Acme"]) == 3
    fila = next(f for f in por_envio.grouped["Acme"] if f.get("product_ids"))
    # Un envio de dos SKU no inventa un representante: los lleva todos aparte.
    assert set(fila["product_ids"]) == {"SKU-1", "SKU-2"}
    assert "product_id" not in fila

    por_linea = _conn(odoo).receipts(unit="line")
    assert len(por_linea.grouped["Acme"]) == 4


def test_las_sucursales_de_un_proveedor_son_un_proveedor():
    """Sin plegar a la empresa comercial, cada sucursal puede caer bajo el minimo
    de recepciones y desaparecer del entregable."""
    odoo = _odoo()
    odoo.records("res.partner")[71] = {"name": "Acme Sucursal Norte",
                                       "commercial_partner_id": [69, "Acme"]}
    odoo.records("purchase.order")[202]["partner_id"] = [71, "Acme Sucursal Norte"]
    pull = _conn(odoo).receipts()
    assert list(pull.grouped) == ["Acme"]
    assert len(pull.grouped["Acme"]) == 3


def test_un_modo_invalido_falla_temprano():
    with pytest.raises(OdooError, match="shipment"):
        _conn().receipts(unit="camion")


def test_el_doble_de_test_ya_no_acepta_un_operador_que_no_soporta():
    """Sin el raise, un `<=` no matcheaba ningun branch y el registro PASABA: la
    consulta devolvia TODAS las filas y la suite quedaba verde sobre un numero malo."""
    odoo = InMemoryOdoo({"m": {1: {"x": 5}}})
    with pytest.raises(OdooError, match="operador"):
        odoo.execute_kw("m", "search", [[["x", "<=", 3]]])


# --- 5. el camino completo --------------------------------------------------


def test_de_un_odoo_vivo_a_un_scan_con_brecha_medida():
    """LO QUE HACE QUE ESTO NO SEA OTRA CAPACIDAD SIN ENCHUFAR.

    `receipts()` emite exactamente las filas que come el scorecard, asi que un
    Odoo entra al mismo scan que un CSV subido -- y devuelve la brecha entre lo
    que el ERP promete y lo que sus propias recepciones muestran.
    """
    pull = _conn().receipts()
    scan = scan_from_deliveries(pull.grouped, pull.dropped, pull.qa_issues)

    assert scan.ok, scan.qa_issues
    peor = scan.worst
    assert peor.supplier == "Acme"
    assert peor.declared_lead_time == 7.0            # lo que promete la ficha
    assert peor.avg_lead_time > 19                   # lo que dicen sus recepciones
    assert peor.lead_time_gap > 12                   # la brecha, que era cero antes
    assert "SKU-1" in peor.skus_to_recalculate
    # Y el aviso de la fuente viaja: el plazo se mide contra el clic de validar.
    assert any("valid" in f for f in scan.findings)
