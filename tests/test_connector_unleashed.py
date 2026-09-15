"""El adaptador de Unleashed: firma, paginado, mapeo y el empalme de despachos.

Unleashed es una de las dos plataformas del canal AU/NZ elegido. Su API firma cada
request con HMAC-SHA256 sobre la QUERY STRING -- no sobre la URL ni el cuerpo -- y pagina
por segmento de ruta (``/SalesOrders/2``) en vez de por cursor. Las dos cosas son faciles
de escribir mal de una forma que anda igual en el primer pedido y falla en el segundo,
asi que se fijan aca antes que nada.

El resto del archivo es sobre los ``None``. Eso ES el producto: la handoff del 2026-08-29
verifico contra el swagger publico que StockTrim no puede medir el servicio saliente y que
Netstock mide solo el espejo entrante. Lo unico que sostiene esa diferencia es que este
adaptador no invente los numeros que Unleashed no da -- ni un 0.0 que fabrica un quiebre,
ni la cantidad pedida que fabrica un 100%.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

import pytest

from src.connectors.unleashed import (
    UnleashedCredentials,
    UnleashedError,
    auth_headers,
    fetch_pages,
    signature,
)


class FakeHttp:
    """Un transporte httpx-style que devuelve respuestas preparadas y guarda lo pedido."""

    class _Response:
        def __init__(self, payload: dict, status: int = 200) -> None:
            self._payload = payload
            self.status_code = status
            self.text = str(payload)

        def json(self) -> dict:
            return self._payload

    def __init__(self, pages: list[dict], status: int = 200) -> None:
        self._pages = list(pages)
        self._status = status
        self.calls: list[tuple[str, dict, dict]] = []

    def get(self, path, *, params=None, headers=None):
        self.calls.append((path, dict(params or {}), dict(headers or {})))
        payload = self._pages.pop(0) if self._pages else {"Pagination": {}, "Items": []}
        return FakeHttp._Response(payload, self._status)


_CREDS = UnleashedCredentials(api_id="the-id", api_key="the-key")


def _page(items: list[dict], page: int, pages: int) -> dict:
    return {
        "Pagination": {"NumberOfItems": len(items), "PageSize": 200,
                       "PageNumber": page, "NumberOfPages": pages},
        "Items": items,
    }


def test_the_signature_is_hmac_sha256_of_the_query_string_in_base64():
    """La referencia de Unleashed (AuthenticationHelp) firma los parametros SIN el '?'.
    Se recalcula aca a mano en vez de pegar un valor esperado: un valor pegado sigue
    pasando si el algoritmo cambia y alguien actualiza la constante."""
    query = "pageSize=200&startDate=2026-01-01"
    esperado = base64.b64encode(
        hmac.new(b"the-key", query.encode("utf-8"), hashlib.sha256).digest()
    ).decode("ascii")
    assert signature(query, "the-key") == esperado


def test_a_request_with_no_parameters_signs_the_empty_string():
    """El caso que rompe en produccion y no en la demo: firmar el '?' o el path devuelve
    401 solo en los endpoints que no llevan filtros."""
    esperado = base64.b64encode(
        hmac.new(b"the-key", b"", hashlib.sha256).digest()
    ).decode("ascii")
    assert signature("", "the-key") == esperado


def test_the_headers_carry_the_id_the_signature_and_the_client_type():
    headers = auth_headers(_CREDS, "pageSize=200")
    assert headers["api-auth-id"] == "the-id"
    assert headers["api-auth-signature"] == signature("pageSize=200", "the-key")
    assert headers["client-type"] == "kern/kern"
    assert headers["Accept"] == "application/json"


def test_the_api_key_never_appears_in_a_header_or_a_url():
    """La clave privada FIRMA y no viaja. Un adaptador que la mande en una cabecera la
    deja en cada log de proxy del cliente."""
    headers = auth_headers(_CREDS, "pageSize=200")
    assert "the-key" not in "".join(f"{k}{v}" for k, v in headers.items())


def test_paging_walks_every_page_and_stops_at_the_last():
    http = FakeHttp([
        _page([{"OrderNumber": "SO-1"}], page=1, pages=2),
        _page([{"OrderNumber": "SO-2"}], page=2, pages=2),
    ])
    items = fetch_pages(http, "SalesOrders", {"pageSize": 200}, _CREDS)
    assert [i["OrderNumber"] for i in items] == ["SO-1", "SO-2"]
    assert [call[0] for call in http.calls] == ["/SalesOrders/1", "/SalesOrders/2"]


def test_every_page_is_signed_with_its_own_query_string():
    """La firma cubre los parametros y el numero de pagina va en la RUTA. Si alguien lo
    mueve a los parametros, reusar la firma de la primera pagina da 401 en la segunda y
    nunca en la primera -- que es el bug que no se ve en una demo de una pagina."""
    http = FakeHttp([
        _page([{"OrderNumber": "SO-1"}], page=1, pages=2),
        _page([{"OrderNumber": "SO-2"}], page=2, pages=2),
    ])
    fetch_pages(http, "SalesOrders", {"pageSize": 200, "startDate": "2026-01-01"}, _CREDS)
    for _path, params, headers in http.calls:
        query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        assert headers["api-auth-signature"] == signature(query, "the-key")


def test_a_single_page_response_makes_exactly_one_call():
    http = FakeHttp([_page([{"OrderNumber": "SO-1"}], page=1, pages=1)])
    assert len(fetch_pages(http, "SalesOrders", {}, _CREDS)) == 1
    assert len(http.calls) == 1


def test_a_non_200_raises_instead_of_returning_an_empty_read():
    """Un 401 que devuelve ``[]`` es indistinguible de una tienda sin ventas: fill rate
    no medido, cero hallazgos, y un cliente al que se le informa que no tiene quiebres."""
    http = FakeHttp([_page([], page=1, pages=1)], status=401)
    with pytest.raises(UnleashedError, match="401"):
        fetch_pages(http, "SalesOrders", {}, _CREDS)


def test_paging_stops_at_the_cap_rather_than_looping_forever():
    """Un servidor que devuelve NumberOfPages creciente no puede colgar un job."""
    http = FakeHttp([_page([{"OrderNumber": f"SO-{i}"}], page=i, pages=999)
                     for i in range(1, 10)])
    items = fetch_pages(http, "SalesOrders", {}, _CREDS, max_pages=3)
    assert len(items) == 3
    assert len(http.calls) == 3


# -- catalogo, stock y el lado de demanda ---------------------------------------

from src.connectors import InventorySource  # noqa: E402
from src.connectors.unleashed import UnleashedConnector  # noqa: E402


class RoutedHttp:
    """Un transporte que sirve payloads distintos segun el recurso pedido."""

    class _Response:
        def __init__(self, payload: dict) -> None:
            self._payload = payload
            self.status_code = 200

        def json(self) -> dict:
            return self._payload

    def __init__(self, by_resource: dict[str, list[dict]]) -> None:
        self._by_resource = by_resource
        self.calls: list[tuple[str, dict]] = []

    def get(self, path, *, params=None, headers=None):
        self.calls.append((path, dict(params or {})))
        recurso = path.strip("/").split("/")[0]
        items = self._by_resource.get(recurso, [])
        return RoutedHttp._Response(
            {"Pagination": {"NumberOfPages": 1, "PageNumber": 1}, "Items": items}
        )


_PRODUCTS = [
    {"ProductCode": "SKU-1", "ProductDescription": "Widget",
     "DefaultSellPrice": 20.0, "LastCost": 12.0},
    {"ProductCode": "SKU-2", "ProductDescription": "Gadget",
     "DefaultSellPrice": 50.0, "LastCost": 30.0},
]

_STOCK = [
    {"ProductCode": "SKU-1", "QtyOnHand": 40.0, "AvailableQty": 35.0, "WarehouseCode": "MAIN"},
    {"ProductCode": "SKU-2", "QtyOnHand": 5.0, "AvailableQty": 5.0, "WarehouseCode": "MAIN"},
]

#: Una orden pedida el 5, prometida para el 10, cerrada el 12. Una sin promesa. Y una
#: borrada con 999 unidades, que no es demanda.
_ORDERS = [
    {
        "OrderNumber": "SO-1", "OrderDate": "2026-01-05T00:00:00",
        "RequiredDate": "2026-01-10T00:00:00", "CompletedDate": "2026-01-12T00:00:00",
        "OrderStatus": "Completed",
        "SalesOrderLines": [
            {"LineNumber": 1, "Product": {"ProductCode": "SKU-1"},
             "OrderQuantity": 10.0, "UnitPrice": 20.0},
            {"LineNumber": 2, "Product": {"ProductCode": "SKU-2"},
             "OrderQuantity": 4.0, "UnitPrice": 50.0},
        ],
    },
    {
        "OrderNumber": "SO-2", "OrderDate": "2026-01-06T00:00:00",
        "RequiredDate": None, "CompletedDate": None, "OrderStatus": "Backordered",
        "SalesOrderLines": [
            {"LineNumber": 1, "Product": {"ProductCode": "SKU-1"},
             "OrderQuantity": 5.0, "UnitPrice": 20.0},
        ],
    },
    {
        "OrderNumber": "SO-3", "OrderDate": "2026-01-07T00:00:00",
        "OrderStatus": "Deleted",
        "SalesOrderLines": [
            {"LineNumber": 1, "Product": {"ProductCode": "SKU-1"},
             "OrderQuantity": 999.0, "UnitPrice": 20.0},
        ],
    },
]


def _connector(**extra) -> UnleashedConnector:
    http = RoutedHttp({"Products": _PRODUCTS, "StockOnHand": _STOCK,
                       "SalesOrders": _ORDERS, **extra})
    return UnleashedConnector(http, _CREDS)


def test_the_connector_satisfies_the_inventory_source_protocol():
    assert isinstance(_connector(), InventorySource)


def test_products_map_code_description_price_and_cost():
    productos = {p.sku: p for p in _connector().list_products()}
    assert productos["SKU-1"].title == "Widget"
    assert productos["SKU-1"].price == 20.0
    assert productos["SKU-1"].cost == 12.0


def test_stock_uses_available_not_on_hand():
    """``QtyOnHand`` incluye lo ya comprometido a otras ordenes. Planificar sobre el
    fisico promete unidades que ya tienen dueno."""
    niveles = {lvl.sku: lvl for lvl in _connector().inventory_levels()}
    assert niveles["SKU-1"].available == 35.0
    assert niveles["SKU-1"].location == "MAIN"


def test_orders_carry_the_demand_side_and_sort_by_date():
    pedidos = _connector().orders()
    assert [o.order_id for o in pedidos] == ["SO-1", "SO-2"]
    primera = pedidos[0]
    assert primera.created_at == "2026-01-05"
    assert [(ln.sku, ln.quantity, ln.price) for ln in primera.lines] == [
        ("SKU-1", 10.0, 20.0), ("SKU-2", 4.0, 50.0),
    ]


def test_a_deleted_order_is_not_demand():
    """Una orden borrada con 999 unidades deformaria el pronostico de SKU-1 sola."""
    cantidades = [ln.quantity for o in _connector().orders() for ln in o.lines]
    assert 999.0 not in cantidades


def test_since_travels_as_the_start_date_filter():
    """Se filtra en el SERVIDOR. Traer el historico entero para descartarlo en memoria es
    lo que hace que la primera corrida contra una cuenta viva tarde minutos y pague rate
    limit por nada."""
    conector = _connector()
    conector.orders(since="2026-01-06")
    llamadas = [params for path, params in conector._http.calls if "SalesOrders" in path]
    assert llamadas and llamadas[0].get("startDate") == "2026-01-06"


def test_an_order_with_no_required_date_leaves_the_promise_empty():
    """Ausente, y no ``OrderDate``: la fecha en que el cliente compro no es una promesa, y
    medir contra ella declara tarde a todo lo que no salio el mismo dia."""
    so2 = next(o for o in _connector().orders() if o.order_id == "SO-2")
    assert all(ln.promised_date is None for ln in so2.lines)


def test_the_promised_date_is_the_required_date_truncated_to_a_day():
    so1 = next(o for o in _connector().orders() if o.order_id == "SO-1")
    assert all(ln.promised_date == "2026-01-10" for ln in so1.lines)


def test_without_shipments_the_delivered_quantity_is_none_not_zero():
    """El ``None`` que sostiene el producto entero. ``/SalesOrders`` no trae cantidad
    despachada: rellenarla con 0.0 le regala al cliente un 0% de fill rate sobre lineas
    que nadie midio, y rellenarla con lo pedido le regala un 100%."""
    lineas = [ln for o in _connector().orders() for ln in o.lines]
    assert lineas and all(ln.delivered_qty is None for ln in lineas)


# -- el empalme de despachos ----------------------------------------------------

from src.connectors import realized_service  # noqa: E402
from src.connectors.unleashed import shipments_by_line  # noqa: E402

#: SO-1 linea 1 salio en DOS camiones (6 + 2 de 10 pedidas), el ultimo el 12.
#: SO-1 linea 2 salio entera el 9. SO-2 no tiene ningun despacho.
#: SH-3 esta BORRADA y trae 99 unidades que nunca salieron.
_SHIPMENTS = [
    {
        "ShipmentNumber": "SH-1", "OrderNumber": "SO-1", "ShipmentStatus": "Completed",
        "DispatchDate": "2026-01-09T00:00:00",
        "SalesShipmentLines": [
            {"LineNumber": 1, "SalesOrderLineNumber": 1,
             "Product": {"ProductCode": "SKU-1"}, "ShipmentQty": 6.0},
            {"LineNumber": 2, "SalesOrderLineNumber": 2,
             "Product": {"ProductCode": "SKU-2"}, "ShipmentQty": 4.0},
        ],
    },
    {
        "ShipmentNumber": "SH-2", "OrderNumber": "SO-1", "ShipmentStatus": "Completed",
        "DispatchDate": "2026-01-12T00:00:00",
        "SalesShipmentLines": [
            {"LineNumber": 1, "SalesOrderLineNumber": 1,
             "Product": {"ProductCode": "SKU-1"}, "ShipmentQty": 2.0},
        ],
    },
    {
        "ShipmentNumber": "SH-3", "OrderNumber": "SO-1", "ShipmentStatus": "Deleted",
        "DispatchDate": "2026-01-08T00:00:00",
        "SalesShipmentLines": [
            {"LineNumber": 1, "SalesOrderLineNumber": 1,
             "Product": {"ProductCode": "SKU-1"}, "ShipmentQty": 99.0},
        ],
    },
]


def _connector_con_despachos() -> UnleashedConnector:
    return _connector(SalesShipments=_SHIPMENTS)


def test_partial_shipments_of_one_line_are_summed():
    """Seis mas dos, no dos. Quedarse con el ultimo despacho reporta un quiebre de ocho
    unidades sobre una linea que se sirvio en dos camiones."""
    porlinea = shipments_by_line(_SHIPMENTS)
    assert porlinea[("SO-1", 1)].qty == 8.0


def test_the_dispatch_date_of_a_line_is_the_last_one_that_served_it():
    """Con la del PRIMER camion, una orden partida declara a tiempo lo que termino de
    salir tres dias despues."""
    assert shipments_by_line(_SHIPMENTS)[("SO-1", 1)].dispatched == "2026-01-12"


def test_a_deleted_shipment_never_shipped_anything():
    """SH-3 trae 99 unidades y esta borrada. Sumarla da un fill rate mayor a uno."""
    assert shipments_by_line(_SHIPMENTS)[("SO-1", 1)].qty == 8.0


def test_the_join_is_by_order_line_number_not_by_product_code():
    """Una orden puede llevar el mismo SKU en dos lineas (precios o depositos distintos).
    Empalmar por producto le adjudica a una lo que salio de la otra."""
    porlinea = shipments_by_line(_SHIPMENTS)
    assert set(porlinea) == {("SO-1", 1), ("SO-1", 2)}
    assert porlinea[("SO-1", 2)].qty == 4.0


def test_the_delivered_quantity_arrives_on_the_line():
    so1 = next(o for o in _connector_con_despachos().orders_with_shipments()
               if o.order_id == "SO-1")
    porsku = {ln.sku: ln for ln in so1.lines}
    assert porsku["SKU-1"].delivered_qty == 8.0
    assert porsku["SKU-2"].delivered_qty == 4.0


def test_a_line_with_no_shipment_stays_none_even_when_others_shipped():
    """El caso que separa este adaptador de uno que miente: SO-2 no tiene despacho, y
    dentro de la MISMA lectura donde SO-1 si lo tiene. Un 0.0 aca es un quiebre total
    inventado sobre una orden que quiza salga manana."""
    so2 = next(o for o in _connector_con_despachos().orders_with_shipments()
               if o.order_id == "SO-2")
    assert all(ln.delivered_qty is None for ln in so2.lines)
    assert all(ln.delivered_date is None for ln in so2.lines)


def test_the_delivered_date_is_the_dispatch_not_the_order_completion():
    """``CompletedDate`` cierra la ORDEN entera; el despacho es por linea. Con despachos
    leidos gana el despacho, o una linea que salio el 9 hereda el 12 de su vecina."""
    so1 = next(o for o in _connector_con_despachos().orders_with_shipments()
               if o.order_id == "SO-1")
    porsku = {ln.sku: ln for ln in so1.lines}
    assert porsku["SKU-1"].delivered_date == "2026-01-12"
    assert porsku["SKU-2"].delivered_date == "2026-01-09"


def test_the_shipment_read_is_filtered_by_the_same_window_as_the_orders():
    """Dos ventanas distintas dan un fill rate calculado sobre un universo de ordenes y
    unos despachos sobre otro, y esa diferencia no se ve en ninguna de las dos salidas."""
    conector = _connector_con_despachos()
    conector.orders_with_shipments(since="2026-01-06")
    despachos = [params for path, params in conector._http.calls if "SalesShipments" in path]
    assert despachos and despachos[0].get("startDate") == "2026-01-06"


def test_the_whole_chain_produces_a_measured_fill_rate_and_otif():
    """La prueba de que esto sirve para algo: adaptador -> realized_service -> numeros.

    SKU-1 aparece en dos ordenes: 10 pedidas en SO-1 (8 servidas, salidas el 12 contra
    una promesa del 10) y 5 en SO-2 (sin despacho, y por eso FUERA del denominador). El
    fill rate es 8/10, no 8/15: la linea que nadie midio no puntua cero.
    SKU-2 sirvio 4 de 4 y salio el 9, antes del 10: OTIF 1.
    """
    registros = {r.product_id: r
                 for r in realized_service(_connector_con_despachos().orders_with_shipments())}
    assert registros["SKU-1"].fill_rate == pytest.approx(0.8)
    assert registros["SKU-1"].lines_scored == 1
    assert registros["SKU-1"].otif == 0.0
    assert registros["SKU-2"].fill_rate == 1.0
    assert registros["SKU-2"].otif == 1.0
    assert registros["SKU-1"].provenance == "observed"
