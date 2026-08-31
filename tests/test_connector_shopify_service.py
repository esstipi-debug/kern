"""El lado de servicio de Shopify: lo pedido, lo servido, lo prometido y lo llegado.

``shopify_connect.py`` lee la demanda y la agrega directo a ``SalesHistory``: no
produce un solo ``Order`` canonico, asi que ``src/order_fulfillment.py`` nunca
tuvo de donde medirle el servicio a una tienda Shopify. Estos tests cubren el
modulo que cierra esa brecha, y sobre todo las cuatro decisiones que la hacen
honesta en vez de comoda.

1. La promesa cuelga de ``order.fulfillmentOrders``, que Shopify FILTRA por los
   scopes concedidos: sin ``read_merchant_managed_fulfillment_orders`` devuelve
   lista vacia y un 200, no un 403. Sin nombrarlo, una tienda entera reportaria
   "nadie prometio una fecha" cuando la verdad es "la app no tiene permiso para
   verla". Son dos hechos distintos y el entregable tiene que decir cual es.
2. ``Fulfillment.deliveredAt`` solo se llena si el carrier le reporta la entrega
   a Shopify. Sin fecha, la linea queda FUERA del denominador de OTIF; nunca se
   sustituye por ``createdAt``, que es cuando salio y no cuando llego.
3. Shopify no tiene un campo "entregado" por linea: sale de restar
   ``currentQuantity - unfulfilledQuantity``.
4. Una operacion masiva admite como maximo DOS niveles de conexion anidada, asi
   que ``orders { fulfillments { fulfillmentLineItems } }`` es invalido. La fecha
   de llegada es por ORDEN, no por linea -- que es ademas la granularidad en la
   que SCOR define el pedido perfecto. Se documenta, no se disimula.
"""

import pytest

from src import order_fulfillment as of
from src.connectors import realized_service
from src.connectors.shopify_service import (
    FULFILLMENT_ORDERS_SCOPE,
    bulk_service_query,
    read_service_orders,
    service_events,
)

_ORDER = "gid://shopify/Order/1001"


def _o(**kw) -> dict:
    fila = {"id": _ORDER, "createdAt": "2026-01-05T10:00:00Z", "test": False}
    fila.update(kw)
    return fila


def _linea(i: int, sku: str, pedidas: float, sin_servir: float, precio: str = "20.00") -> dict:
    return {
        "id": f"gid://shopify/LineItem/{i}",
        "__parentId": _ORDER,
        "sku": sku,
        "quantity": pedidas,
        "currentQuantity": pedidas,
        "unfulfilledQuantity": sin_servir,
        "originalUnitPriceSet": {"shopMoney": {"amount": precio}},
    }


def _despacho(i: int, *, estado: str = "SUCCESS", entregado: str | None = None) -> dict:
    return {
        "id": f"gid://shopify/Fulfillment/{i}",
        "__parentId": _ORDER,
        "status": estado,
        "createdAt": "2026-01-07T09:00:00Z",
        "deliveredAt": entregado,
    }


def _promesa(i: int, cuando: str | None) -> dict:
    return {
        "id": f"gid://shopify/FulfillmentOrder/{i}",
        "__parentId": _ORDER,
        "deliveryMethod": None if cuando is None else {
            "maxDeliveryDateTime": cuando,
            "minDeliveryDateTime": cuando,
            "brandedPromise": None,
            "presentedName": "Envio estandar",
        },
    }


def _leer(filas: list[dict], **kw):
    import json

    return read_service_orders([json.dumps(f) for f in filas], **kw)


# -- la consulta ---------------------------------------------------------------


def test_la_consulta_masiva_no_lleva_argumentos_de_paginacion():
    """Shopify rechaza una consulta masiva que pagine, antes de correrla."""
    assert "first" not in bulk_service_query()
    assert "after" not in bulk_service_query()


def test_la_consulta_pide_los_campos_de_los_que_sale_cada_numero():
    q = bulk_service_query()

    for campo in (
        "currentQuantity",
        "unfulfilledQuantity",
        "originalUnitPriceSet",
        "deliveredAt",
        "maxDeliveryDateTime",
    ):
        assert campo in q, f"falta {campo}: el numero que depende de el saldria vacio"


def test_la_consulta_respeta_los_dos_limites_de_una_operacion_masiva():
    """Maximo dos niveles de conexion anidada y cinco conexiones en total."""
    q = bulk_service_query()
    conexiones = ("orders", "lineItems", "fulfillments", "fulfillmentOrders")

    assert sum(q.count(c + " {") + q.count(c + "(") for c in conexiones) <= 5
    # `fulfillmentLineItems` seria el tercer nivel, y por eso la fecha de llegada
    # es por orden: la consulta no puede pedirla por linea.
    assert "fulfillmentLineItems" not in q


# -- el JSONL de tres hijos ----------------------------------------------------


def test_reconstruye_la_orden_con_sus_tres_clases_de_hijo():
    filas = [
        _o(),
        _linea(1, "SKU-1", 10.0, 2.0),
        _despacho(1, entregado="2026-01-09T12:00:00Z"),
        _promesa(1, "2026-01-10T23:59:00Z"),
    ]

    (evento,) = list(service_events(json_lines(filas)))

    assert evento["id"] == _ORDER
    assert len(evento["lines"]) == 1
    assert len(evento["fulfillments"]) == 1
    assert len(evento["promises"]) == 1
    assert evento["orphans"] == 0


def test_un_hijo_de_otra_orden_es_huerfano_y_no_se_le_adjudica_a_esta():
    ajena = dict(_linea(9, "SKU-9", 5.0, 0.0), __parentId="gid://shopify/Order/9999")
    filas = [_o(), _linea(1, "SKU-1", 10.0, 0.0), ajena]

    (evento,) = list(service_events(json_lines(filas)))

    assert len(evento["lines"]) == 1
    assert evento["orphans"] == 1


# -- lo servido ----------------------------------------------------------------


def test_lo_entregado_sale_de_restar_lo_no_servido():
    """Shopify no tiene campo de cantidad entregada; se deduce."""
    lectura = _leer([_o(), _linea(1, "SKU-1", 10.0, 3.0), _despacho(1, entregado="2026-01-09T12:00:00Z"),
                     _promesa(1, "2026-01-10T23:59:00Z")])

    (linea,) = lectura.orders[0].lines
    assert linea.quantity == 10.0
    assert linea.delivered_qty == 7.0


def test_una_orden_sin_un_solo_despacho_entrego_cero_no_desconocido():
    """El pedido existe y no salio nada: eso es una medicion, no una ausencia."""
    lectura = _leer([_o(), _linea(1, "SKU-1", 10.0, 10.0), _promesa(1, "2026-01-10T23:59:00Z")])

    (linea,) = lectura.orders[0].lines
    assert linea.delivered_qty == 0.0


# -- la fecha de llegada -------------------------------------------------------


def test_la_fecha_de_llegada_es_la_del_ultimo_despacho_entregado():
    filas = [
        _o(),
        _linea(1, "SKU-1", 10.0, 0.0),
        dict(_despacho(1, entregado="2026-01-09T12:00:00Z")),
        dict(_despacho(2, entregado="2026-01-13T12:00:00Z")),
        _promesa(1, "2026-01-10T23:59:00Z"),
    ]

    lectura = _leer(filas)

    (linea,) = lectura.orders[0].lines
    # la orden no termino de llegar hasta el segundo camion
    assert linea.delivered_date == "2026-01-13"


def test_un_despacho_sin_fecha_de_entrega_deja_la_orden_sin_fecha():
    """Estricto: si un envio nunca reporto entrega, no se sabe cuando llego."""
    filas = [
        _o(),
        _linea(1, "SKU-1", 10.0, 0.0),
        _despacho(1, entregado="2026-01-09T12:00:00Z"),
        _despacho(2, entregado=None),
        _promesa(1, "2026-01-10T23:59:00Z"),
    ]

    lectura = _leer(filas)

    (linea,) = lectura.orders[0].lines
    assert linea.delivered_date is None
    assert lectura.dropped["sin_fecha_de_entrega"] == 1


def test_nunca_se_sustituye_la_fecha_de_entrega_por_la_de_despacho():
    filas = [_o(), _linea(1, "SKU-1", 10.0, 0.0), _despacho(1, entregado=None),
             _promesa(1, "2026-01-10T23:59:00Z")]

    (linea,) = _leer(filas).orders[0].lines

    assert linea.delivered_date is None  # createdAt del despacho es 2026-01-07


def test_un_despacho_cancelado_no_entrego_nada():
    filas = [
        _o(),
        _linea(1, "SKU-1", 10.0, 0.0),
        _despacho(1, estado="CANCELLED", entregado="2026-01-30T12:00:00Z"),
        _despacho(2, estado="SUCCESS", entregado="2026-01-09T12:00:00Z"),
        _promesa(1, "2026-01-10T23:59:00Z"),
    ]

    (linea,) = _leer(filas).orders[0].lines

    assert linea.delivered_date == "2026-01-09"


# -- la promesa ----------------------------------------------------------------


def test_la_promesa_es_la_fecha_de_entrega_que_vio_el_comprador():
    lectura = _leer([_o(), _linea(1, "SKU-1", 10.0, 0.0),
                     _despacho(1, entregado="2026-01-09T12:00:00Z"),
                     _promesa(1, "2026-01-10T23:59:00Z")])

    (linea,) = lectura.orders[0].lines
    assert linea.promised_date == "2026-01-10"


def test_con_varias_promesas_manda_la_mas_tardia():
    """La orden no esta completa hasta la ultima, igual que su fecha de llegada."""
    filas = [
        _o(), _linea(1, "SKU-1", 10.0, 0.0), _despacho(1, entregado="2026-01-09T12:00:00Z"),
        _promesa(1, "2026-01-10T23:59:00Z"), _promesa(2, "2026-01-14T23:59:00Z"),
    ]

    (linea,) = _leer(filas).orders[0].lines

    assert linea.promised_date == "2026-01-14"


def test_sin_scope_de_fulfillment_orders_lo_dice_en_vez_de_callarselo():
    """Shopify devuelve lista vacia y un 200: sin la nota son dos hechos iguales."""
    filas = [_o(), _linea(1, "SKU-1", 10.0, 0.0), _despacho(1, entregado="2026-01-09T12:00:00Z")]

    lectura = _leer(filas, fulfillment_orders_scope=False)

    (linea,) = lectura.orders[0].lines
    assert linea.promised_date is None
    assert any(FULFILLMENT_ORDERS_SCOPE in n for n in lectura.notes)


def test_con_el_scope_puesto_y_sin_promesa_no_se_culpa_al_permiso():
    """El comerciante simplemente no vende con fecha prometida. Otro hecho."""
    filas = [_o(), _linea(1, "SKU-1", 10.0, 0.0), _despacho(1, entregado="2026-01-09T12:00:00Z"),
             _promesa(1, None)]

    lectura = _leer(filas, fulfillment_orders_scope=True)

    assert lectura.orders[0].lines[0].promised_date is None
    assert not any(FULFILLMENT_ORDERS_SCOPE in n for n in lectura.notes)


# -- que cuenta como venta -----------------------------------------------------


def test_una_orden_cancelada_o_de_prueba_no_entra():
    filas = [
        _o(cancelledAt="2026-01-06T10:00:00Z"), _linea(1, "SKU-1", 10.0, 0.0),
    ]
    lectura = _leer(filas)
    assert lectura.orders == () and lectura.dropped["cancelada"] == 1

    filas = [_o(test=True), _linea(1, "SKU-1", 10.0, 0.0)]
    lectura = _leer(filas)
    assert lectura.orders == () and lectura.dropped["de_prueba"] == 1


def test_una_linea_sin_sku_no_se_puede_atribuir_a_ningun_producto():
    filas = [_o(), _linea(1, "SKU-1", 10.0, 0.0), dict(_linea(2, "", 3.0, 0.0), sku=None),
             _despacho(1, entregado="2026-01-09T12:00:00Z"), _promesa(1, "2026-01-10T23:59:00Z")]

    lectura = _leer(filas)

    assert len(lectura.orders[0].lines) == 1
    assert lectura.dropped["sin_sku"] == 1


# -- de punta a punta ----------------------------------------------------------


def test_de_jsonl_de_shopify_a_un_otif_medido():
    """La prueba que importa: el formato crudo de Shopify hasta el numero."""
    filas = [
        _o(),
        _linea(1, "SKU-1", 10.0, 2.0),
        _linea(2, "SKU-2", 4.0, 0.0, precio="50.00"),
        _despacho(1, entregado="2026-01-12T12:00:00Z"),
        _promesa(1, "2026-01-10T23:59:00Z"),
    ]

    registros = {r.product_id: r for r in realized_service(_leer(filas).orders)}

    assert registros["SKU-1"].provenance == of.PROVENANCE_OBSERVED
    assert registros["SKU-1"].fill_rate == pytest.approx(0.8)
    assert registros["SKU-1"].lost_sales_value == pytest.approx(40.0)
    # corta Y tarde: las dos mitades de on-time-in-full fallan
    assert registros["SKU-1"].otif == pytest.approx(0.0)
    # completa, pero llego el 12 contra una promesa del 10
    assert registros["SKU-2"].fill_rate == pytest.approx(1.0)
    assert registros["SKU-2"].otif == pytest.approx(0.0)


def json_lines(filas: list[dict]) -> list[str]:
    import json

    return [json.dumps(f) for f in filas]


# -- la lectura masiva contra el doble -----------------------------------------


def _orden_shopify(oid: str) -> dict:
    """Una orden con la forma exacta que emite la Admin API."""
    return {
        "id": f"gid://shopify/Order/{oid}",
        "createdAt": "2026-01-05T10:00:00Z",
        "cancelledAt": None,
        "test": False,
        "lineItems": {"edges": [{"node": {
            "sku": "SKU-1", "quantity": 10, "currentQuantity": 10, "unfulfilledQuantity": 2,
            "originalUnitPriceSet": {"shopMoney": {"amount": "20.00"}},
        }}]},
        "fulfillments": {"edges": [{"node": {
            "status": "SUCCESS", "createdAt": "2026-01-07T09:00:00Z",
            "deliveredAt": "2026-01-12T12:00:00Z",
        }}]},
        "fulfillmentOrders": {"edges": [{"node": {
            "deliveryMethod": {"maxDeliveryDateTime": "2026-01-10T23:59:00Z"},
        }}]},
    }


def test_la_lectura_masiva_entrega_servicio_medido():
    from src.connectors.shopify_connect import InMemoryShopifyAdmin
    from src.connectors.shopify_service import read_service_orders_bulk

    admin = InMemoryShopifyAdmin([_orden_shopify("1001")])
    lectura, estado = read_service_orders_bulk(admin, sleep=lambda _s: None,
                                               fetch_jsonl=lambda _u: admin.jsonl())

    assert estado.ok
    (registro,) = realized_service(lectura.orders)
    assert registro.provenance == of.PROVENANCE_OBSERVED
    assert registro.fill_rate == pytest.approx(0.8)
    assert registro.otif == pytest.approx(0.0)


def test_la_mutacion_masiva_no_lleva_ninguna_escritura():
    """Lo prohibido es escribir en la tienda, no invocar un campo que es mutacion."""
    from src.connectors.shopify_connect import InMemoryShopifyAdmin
    from src.connectors.shopify_service import read_service_orders_bulk

    admin = InMemoryShopifyAdmin([_orden_shopify("1001")])
    read_service_orders_bulk(admin, sleep=lambda _s: None, fetch_jsonl=lambda _u: admin.jsonl())

    for prohibido in ("Update", "Create", "Delete", "Set(", "Adjust"):
        assert prohibido not in admin.bulk_query


def test_el_chequeo_de_conexion_reporta_si_la_promesa_es_legible():
    """Quien lee el servicio necesita saberlo ANTES de interpretar un OTIF vacio."""
    from src.connectors.shopify_connect import (
        FULFILLMENT_ORDERS_SCOPE as SCOPE_EN_CONNECT,
    )
    from src.connectors.shopify_connect import (
        REQUIRED_SCOPES,
        InMemoryShopifyAdmin,
        check_connection,
    )

    assert SCOPE_EN_CONNECT == FULFILLMENT_ORDERS_SCOPE

    from src.connectors.shopify_connect import ShopifyCredentials

    creds = ShopifyCredentials("mi-tienda.myshopify.com", "shpat_" + "0" * 32)
    sin = check_connection(creds, http=InMemoryShopifyAdmin([]))
    con = check_connection(
        creds,
        http=InMemoryShopifyAdmin([], scopes=REQUIRED_SCOPES + (FULFILLMENT_ORDERS_SCOPE,)),
    )

    assert not sin.fulfillment_orders
    assert con.fulfillment_orders


def test_una_tienda_sin_datos_de_entrega_sale_no_medida_no_en_cero():
    """El caso mas comun de todos: la orden existe, nadie reporto entregas."""
    from src.connectors.shopify_connect import InMemoryShopifyAdmin
    from src.connectors.shopify_service import read_service_orders_bulk

    cruda = _orden_shopify("1001")
    cruda["lineItems"]["edges"][0]["node"].pop("unfulfilledQuantity")
    cruda["fulfillments"] = {"edges": []}
    cruda["fulfillmentOrders"] = {"edges": []}
    admin = InMemoryShopifyAdmin([cruda])

    lectura, _ = read_service_orders_bulk(admin, sleep=lambda _s: None,
                                          fetch_jsonl=lambda _u: admin.jsonl(),
                                          fulfillment_orders_scope=False)

    (registro,) = realized_service(lectura.orders)
    assert registro.provenance == of.PROVENANCE_NOT_MEASURED
    assert registro.fill_rate is None


def test_el_copy_de_instalacion_nombra_el_permiso_que_desbloquea_otif():
    """El comerciante decide los permisos UNA vez, en esa pantalla y no despues."""
    from src.connectors.shopify_connect import ALL_ORDERS_SCOPE, setup_copy

    recomendados = {p["handle"]: p for p in setup_copy()["permisos"]["recomendado"]}

    # el que ya estaba no se corre de lugar
    assert list(recomendados)[0] == ALL_ORDERS_SCOPE
    entrada = recomendados[FULFILLMENT_ORDERS_SCOPE]
    assert "OTIF" in entrada["si_falta"]
    # y dice lo que lo vuelve invisible: Shopify contesta bien igual
    assert "EXITOSA" in entrada["si_falta"].upper()
