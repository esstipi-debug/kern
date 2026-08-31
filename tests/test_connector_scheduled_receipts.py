"""Lo que viene en camino: las recepciones pendientes, con sus DOS fechas.

Kern mide el plazo de proveedor mirando hacia atras (``OdooConnector.receipts()``:
``stock.move.date`` menos ``purchase.order.date_order``, y de ningun otro lado).
Lo que no tenia era la mirada hacia adelante -- que hay comprado y sin recibir --
y sin eso una promesa de fecha al cliente solo puede contar el stock en mano, o
sea prometer siempre mas tarde de lo real.

El disenio entero cuelga de una distincion, y es la misma que separa
``LEAD_TIME_MEASURED`` de ``LEAD_TIME_DECLARED`` en ``supplier_scorecard``, ahora
aplicada al futuro en vez de al pasado:

``promised_date``
    ``purchase.order.line.date_planned``. Es la PROMESA del ERP, y el docstring de
    ``receipts()`` ya explica que en Odoo vale ``date_order + seller.delay``: la
    ficha del proveedor, no su comportamiento. Sirve para decir que prometieron.

``expected_date``
    ``date_order`` mas el plazo MEDIDO de ese proveedor. Es la unica de las dos
    que se puede usar para comprometer una fecha con un cliente.

Sin plazo medido para el proveedor, ``expected_date`` es ``None`` -- nunca la
prometida con otro nombre. Un proveedor que promete 7 dias y entrega en 20 tiene
que producir una recepcion esperada vacia, no una a 7 dias: ese silencio es lo
que despues obliga a que la promesa al cliente salga HANDOFF en vez de una fecha
que se va a incumplir.
"""

import pytest

from src.connectors import ReceiptsSource, ScheduledReceipt
from src.connectors.odoo import InMemoryOdoo, OdooConnector

_PRODUCTS = {
    1: {"default_code": "SKU-1", "name": "Widget", "list_price": 20.0, "standard_price": 12.0},
    2: {"default_code": "SKU-2", "name": "Gadget", "list_price": 50.0, "standard_price": 30.0},
}


def _odoo_con_ocs(**extra) -> InMemoryOdoo:
    """Dos OC confirmadas: una a medio recibir, otra entera pendiente."""
    data = {
        "product.product": dict(_PRODUCTS),
        "res.partner": {
            70: {"name": "Acme", "commercial_partner_id": [70, "Acme"]},
            71: {"name": "Globex", "commercial_partner_id": [71, "Globex"]},
        },
        "purchase.order": {
            200: {
                "name": "PO-001",
                "date_order": "2026-01-05 09:00:00",
                "partner_id": [70, "Acme"],
                "state": "purchase",
                "order_line": [300, 301],
            },
            201: {
                "name": "PO-002",
                "date_order": "2026-01-08 09:00:00",
                "partner_id": [71, "Globex"],
                "state": "purchase",
                "order_line": [302],
            },
            # una OC en borrador no es una recepcion programada: nadie la confirmo
            202: {
                "name": "PO-BORRADOR",
                "date_order": "2026-01-09 09:00:00",
                "partner_id": [70, "Acme"],
                "state": "draft",
                "order_line": [303],
            },
        },
        "purchase.order.line": {
            # 40 pedidas, 15 ya recibidas -> quedan 25 en camino
            300: {"product_id": [1, "Widget"], "product_qty": 40.0, "qty_received": 15.0,
                  "date_planned": "2026-01-12 00:00:00", "order_id": [200, "PO-001"]},
            # completamente recibida: ya no viene nada
            301: {"product_id": [2, "Gadget"], "product_qty": 10.0, "qty_received": 10.0,
                  "date_planned": "2026-01-12 00:00:00", "order_id": [200, "PO-001"]},
            302: {"product_id": [1, "Widget"], "product_qty": 30.0, "qty_received": 0.0,
                  "date_planned": "2026-01-22 00:00:00", "order_id": [201, "PO-002"]},
            303: {"product_id": [1, "Widget"], "product_qty": 99.0, "qty_received": 0.0,
                  "date_planned": "2026-01-15 00:00:00", "order_id": [202, "PO-BORRADOR"]},
        },
        "stock.location": {},
        "stock.quant": {},
        "product.supplierinfo": {},
        "stock.warehouse.orderpoint": {},
    }
    data.update(extra)
    return InMemoryOdoo(data)


def _por_sku(recepciones) -> dict:
    salida: dict = {}
    for r in recepciones:
        salida.setdefault(r.sku, []).append(r)
    return salida


# -- que cuenta como recepcion programada --------------------------------------


def test_el_conector_satisface_el_protocolo_de_recepciones():
    assert isinstance(OdooConnector(_odoo_con_ocs()), ReceiptsSource)


def test_solo_lo_confirmado_y_no_recibido_esta_en_camino():
    recepciones = OdooConnector(_odoo_con_ocs()).scheduled_receipts()

    por_sku = _por_sku(recepciones)
    # SKU-1: 25 pendientes de PO-001 + 30 de PO-002. La OC borrador no cuenta.
    assert sorted(r.quantity for r in por_sku["SKU-1"]) == [25.0, 30.0]
    # SKU-2 llego entero: no hay nada en camino
    assert "SKU-2" not in por_sku


def test_una_orden_en_borrador_no_es_una_recepcion_programada():
    """Nadie la confirmo: contarla es prometerle al cliente stock que no se compro."""
    recepciones = OdooConnector(_odoo_con_ocs()).scheduled_receipts()

    assert not [r for r in recepciones if r.order_id == "PO-BORRADOR"]
    assert 99.0 not in [r.quantity for r in recepciones]


def test_cada_recepcion_dice_de_que_proveedor_y_de_que_orden_viene():
    por_sku = _por_sku(OdooConnector(_odoo_con_ocs()).scheduled_receipts())

    proveedores = {r.order_id: r.supplier for r in por_sku["SKU-1"]}
    assert proveedores == {"PO-001": "Acme", "PO-002": "Globex"}


# -- las dos fechas ------------------------------------------------------------


def test_la_fecha_prometida_es_la_del_erp_y_se_llama_asi():
    por_sku = _por_sku(OdooConnector(_odoo_con_ocs()).scheduled_receipts())

    prometidas = {r.order_id: r.promised_date for r in por_sku["SKU-1"]}
    assert prometidas == {"PO-001": "2026-01-12", "PO-002": "2026-01-22"}


def test_sin_plazo_medido_no_hay_fecha_esperada():
    """La prometida NO se recicla con otro nombre. Ese silencio es el producto."""
    por_sku = _por_sku(OdooConnector(_odoo_con_ocs()).scheduled_receipts())

    for r in por_sku["SKU-1"]:
        assert r.expected_date is None
        assert r.expected_source is None


def test_con_plazo_medido_la_fecha_esperada_sale_de_la_fecha_de_orden():
    """date_order + plazo observado. NUNCA date_planned + nada."""
    conector = OdooConnector(_odoo_con_ocs())

    recepciones = conector.scheduled_receipts(measured_lead_times={"Acme": 20.0})

    acme = [r for r in recepciones if r.supplier == "Acme"][0]
    # OC del 5 de enero + 20 dias medidos = 25 de enero, no el 12 que prometieron
    assert acme.expected_date == "2026-01-25"
    assert acme.expected_source == "measured"
    assert acme.promised_date == "2026-01-12"  # y la promesa sigue ahi, aparte


def test_un_proveedor_sin_plazo_medido_no_hereda_el_de_otro():
    conector = OdooConnector(_odoo_con_ocs())

    recepciones = conector.scheduled_receipts(measured_lead_times={"Acme": 20.0})

    globex = [r for r in recepciones if r.supplier == "Globex"][0]
    assert globex.expected_date is None
    assert globex.expected_source is None


def test_la_brecha_entre_prometido_y_esperado_es_visible_por_recepcion():
    """El numero que justifica el producto: prometieron el 12, llega el 25."""
    recepciones = OdooConnector(_odoo_con_ocs()).scheduled_receipts(
        measured_lead_times={"Acme": 20.0}
    )

    acme = [r for r in recepciones if r.supplier == "Acme"][0]
    assert acme.slip_days == 13


def test_sin_fecha_esperada_no_hay_brecha_que_reportar():
    (globex,) = [
        r
        for r in OdooConnector(_odoo_con_ocs()).scheduled_receipts()
        if r.supplier == "Globex"
    ]

    assert globex.slip_days is None  # no cero: no se midio


# -- ausencias -----------------------------------------------------------------


def test_una_linea_sin_date_planned_no_promete_nada():
    odoo = _odoo_con_ocs()
    odoo.records("purchase.order.line")[302]["date_planned"] = False

    (linea,) = [
        r for r in OdooConnector(odoo).scheduled_receipts() if r.order_id == "PO-002"
    ]

    assert linea.promised_date is None


def test_un_odoo_sin_modulo_de_compras_no_inventa_recepciones():
    """Sin purchase.order no hay nada en camino que leer, y eso no es un error."""
    vacio = InMemoryOdoo(
        {
            "product.product": dict(_PRODUCTS),
            "stock.location": {},
            "stock.quant": {},
            "product.supplierinfo": {},
            "stock.warehouse.orderpoint": {},
        }
    )

    assert OdooConnector(vacio).scheduled_receipts() == []


def test_una_linea_sobre_recibida_no_deja_una_cantidad_negativa_en_camino():
    """Recibir de mas pasa (sobre-entrega del proveedor). No son unidades por venir."""
    odoo = _odoo_con_ocs()
    odoo.records("purchase.order.line")[302]["qty_received"] = 35.0  # pidio 30

    assert not [r for r in OdooConnector(odoo).scheduled_receipts() if r.order_id == "PO-002"]


def test_el_horizonte_recorta_lo_que_llega_demasiado_tarde():
    """Una recepcion a 90 dias no sirve para prometerle a un pedido de esta semana."""
    conector = OdooConnector(_odoo_con_ocs())

    cerca = conector.scheduled_receipts(until="2026-01-15")

    assert [r.order_id for r in cerca] == ["PO-001"]


# -- el agregado que consume ATP -----------------------------------------------


def test_las_recepciones_se_agrupan_por_sku_y_fecha_para_el_motor():
    from src.connectors import incoming_by_sku

    recepciones = OdooConnector(_odoo_con_ocs()).scheduled_receipts()

    incoming = incoming_by_sku(recepciones)

    assert set(incoming) == {"SKU-1"}
    assert sorted(incoming["SKU-1"]) == [("2026-01-12", 25.0), ("2026-01-22", 30.0)]


def test_el_agregado_usa_la_fecha_esperada_cuando_existe():
    """Lo que ATP tiene que consumir es lo que va a pasar, no lo que prometieron."""
    from src.connectors import incoming_by_sku

    recepciones = OdooConnector(_odoo_con_ocs()).scheduled_receipts(
        measured_lead_times={"Acme": 20.0}
    )

    incoming = incoming_by_sku(recepciones)

    # Acme corrida al 25; Globex, sin plazo medido, se queda con su prometida
    assert sorted(incoming["SKU-1"]) == [("2026-01-22", 30.0), ("2026-01-25", 25.0)]


def test_el_agregado_puede_exigir_solo_fechas_medidas():
    """El modo estricto que ATP usa para no comprometer sobre una promesa ajena."""
    from src.connectors import incoming_by_sku

    recepciones = OdooConnector(_odoo_con_ocs()).scheduled_receipts(
        measured_lead_times={"Acme": 20.0}
    )

    incoming = incoming_by_sku(recepciones, measured_only=True)

    assert sorted(incoming["SKU-1"]) == [("2026-01-25", 25.0)]  # Globex queda afuera


def test_una_recepcion_sin_ninguna_fecha_no_entra_al_agregado():
    from src.connectors import incoming_by_sku

    sin_fecha = ScheduledReceipt(
        sku="SKU-9", quantity=5.0, promised_date=None, expected_date=None,
        expected_source=None, supplier="X", order_id="PO-X",
    )

    assert incoming_by_sku([sin_fecha]) == {}


def test_la_brecha_se_calcula_sola_desde_las_dos_fechas():
    r = ScheduledReceipt(
        sku="SKU-1", quantity=1.0, promised_date="2026-01-12",
        expected_date="2026-01-25", expected_source="measured",
        supplier="Acme", order_id="PO-001",
    )

    assert r.slip_days == 13


def test_una_recepcion_que_llega_antes_de_lo_prometido_da_brecha_negativa():
    r = ScheduledReceipt(
        sku="SKU-1", quantity=1.0, promised_date="2026-01-25",
        expected_date="2026-01-20", expected_source="measured",
        supplier="Acme", order_id="PO-001",
    )

    assert r.slip_days == -5


@pytest.mark.parametrize("prometida,esperada", [(None, "2026-01-25"), ("2026-01-12", None), (None, None)])
def test_sin_las_dos_fechas_no_hay_brecha(prometida, esperada):
    r = ScheduledReceipt(
        sku="SKU-1", quantity=1.0, promised_date=prometida, expected_date=esperada,
        expected_source=None, supplier="Acme", order_id="PO-001",
    )

    assert r.slip_days is None
