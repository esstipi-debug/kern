"""El adaptador de Cin7 Core: auth, paginado, el empalme de dos saltos, y los ceros.

Cin7 Core (ex DEAR) es la OTRA plataforma del canal AU/NZ. Su modelo de datos es
sustancialmente distinto del de Unleashed y eso lo hace la primera prueba real de si el
guard de cobertura de servicio generaliza o si estaba hecho a la medida del primer
adaptador.

La diferencia que manda: la cantidad despachada NO sale de un endpoint. Cin7 empaca por
CAJA y despacha por CAJA, asi que hay que empalmar ``Pack.Lines[] (SKU -> Box -> Quantity)``
contra ``Ship.Lines[] (Boxes -> ShipmentDate, IsShipped)``. Dos saltos, y el segundo por un
campo de texto que puede llevar varias etiquetas. Eso se fija aca antes que nada, porque un
empalme por igualdad exacta pierde lineas en silencio y el sintoma es un fill rate que baja
sin motivo.

Verificado contra el blueprint oficial de la API el 2026-08-29
(``https://dearinventory.docs.apiary.io/api-description-document``).
"""

from __future__ import annotations

import pytest

from src.connectors.cin7 import (
    Cin7Credentials,
    Cin7Error,
    Cin7RateLimited,
    auth_headers,
    fetch_pages,
)

_CREDS = Cin7Credentials(account_id="the-account", application_key="the-key")


class FakeHttp:
    """Transporte httpx-style con respuestas preparadas; guarda lo que se pidio."""

    class _Response:
        def __init__(self, payload: dict, status: int = 200) -> None:
            self._payload = payload
            self.status_code = status

        def json(self) -> dict:
            return self._payload

    def __init__(self, pages: list[dict], status: int = 200) -> None:
        self._pages = list(pages)
        self._status = status
        self.calls: list[tuple[str, dict, dict]] = []

    def get(self, path, *, params=None, headers=None):
        self.calls.append((path, dict(params or {}), dict(headers or {})))
        payload = self._pages.pop(0) if self._pages else {"Total": 0, "Page": 1}
        return FakeHttp._Response(payload, self._status)


def _page(items: list[dict], *, total: int, page: int, key: str = "SaleList") -> dict:
    return {"Total": total, "Page": page, key: items}


# -- auth ----------------------------------------------------------------------


def test_las_cabeceras_llevan_la_cuenta_y_la_clave_de_aplicacion():
    """Cin7 NO firma nada: autentica con dos identificadores planos. Distinto de Unleashed,
    donde la clave privada firma la query string y nunca viaja."""
    headers = auth_headers(_CREDS)
    assert headers["api-auth-accountid"] == "the-account"
    assert headers["api-auth-applicationkey"] == "the-key"
    assert headers["Accept"] == "application/json"


# -- paginado ------------------------------------------------------------------


def test_el_paginado_camina_hasta_juntar_el_total_declarado():
    http = FakeHttp([
        _page([{"SaleID": "a"}, {"SaleID": "b"}], total=3, page=1),
        _page([{"SaleID": "c"}], total=3, page=2),
    ])
    items = fetch_pages(http, "saleList", {"Limit": 2}, _CREDS, list_key="SaleList")
    assert [i["SaleID"] for i in items] == ["a", "b", "c"]
    assert [c[1].get("Page") for c in http.calls] == [1, 2]


def test_una_sola_pagina_hace_una_sola_llamada():
    http = FakeHttp([_page([{"SaleID": "a"}], total=1, page=1)])
    assert len(fetch_pages(http, "saleList", {}, _CREDS, list_key="SaleList")) == 1
    assert len(http.calls) == 1


def test_una_pagina_vacia_corta_aunque_el_total_diga_otra_cosa():
    """Un ``Total`` que no baja y una pagina vacia es como un servidor mal implementado
    cuelga un job para siempre. La evidencia manda sobre el contador."""
    http = FakeHttp([
        _page([{"SaleID": "a"}], total=99, page=1),
        _page([], total=99, page=2),
    ])
    items = fetch_pages(http, "saleList", {}, _CREDS, list_key="SaleList")
    assert len(items) == 1
    assert len(http.calls) == 2


def test_el_tope_de_paginas_corta_en_vez_de_girar_para_siempre():
    http = FakeHttp([_page([{"SaleID": str(i)}], total=999, page=i) for i in range(1, 10)])
    items = fetch_pages(http, "saleList", {}, _CREDS, list_key="SaleList", max_pages=3)
    assert len(items) == 3


# -- errores que no se disfrazan -----------------------------------------------


def test_un_429_dice_que_es_el_limite_de_llamadas_y_no_un_fallo_generico():
    """Cin7 corta a 60 llamadas por minuto, y este adaptador pide una por venta. El 429 es
    el modo de falla ESPERADO en una cuenta grande, no una rareza: quien lo vea tiene que
    leer 'esperá y reintentá', no 'la integracion esta rota'."""
    http = FakeHttp([{}], status=429)
    with pytest.raises(Cin7RateLimited, match="60"):
        fetch_pages(http, "saleList", {}, _CREDS, list_key="SaleList")


def test_un_401_levanta_en_vez_de_devolver_una_lectura_vacia():
    """Una lectura fallida que vuelve como ``[]`` es indistinguible de una cuenta sin
    ventas, y produce un informe que le dice al cliente que no tiene quiebres."""
    http = FakeHttp([{}], status=401)
    with pytest.raises(Cin7Error, match="401"):
        fetch_pages(http, "saleList", {}, _CREDS, list_key="SaleList")


def test_el_rate_limited_es_un_cin7_error_para_que_un_solo_except_alcance():
    assert issubclass(Cin7RateLimited, Cin7Error)


# -- el empalme de dos saltos: SKU -> caja -> fecha de despacho ------------------

from src.connectors.cin7 import shipped_by_sku  # noqa: E402


def _venta(fulfilments: list[dict], *, shipping_status: str = "SHIPPED") -> dict:
    return {
        "ID": "sale-1",
        "CombinedShippingStatus": shipping_status,
        "Order": {"Lines": []},
        "Fulfilments": fulfilments,
    }


def _fulfilment(pack: list[dict], ship: list[dict]) -> dict:
    return {
        "TaskID": "task-1",
        "Pack": {"Status": "AUTHORISED", "Lines": pack},
        "Ship": {"Status": "AUTHORISED", "Lines": ship},
    }


def test_una_caja_empacada_y_enviada_da_cantidad_y_fecha():
    venta = _venta([
        _fulfilment(
            pack=[{"SKU": "SKU-1", "Box": "Box 1", "Quantity": 6}],
            ship=[{"Boxes": "Box 1", "ShipmentDate": "2026-01-12T00:00:00", "IsShipped": True}],
        )
    ])
    servido = shipped_by_sku(venta)
    assert servido["SKU-1"].qty == 6.0
    assert servido["SKU-1"].dispatched == "2026-01-12"


def test_empacar_no_es_despachar():
    """La caja esta armada y sigue en el deposito. Contarla adelanta el servicio a un
    momento en que el cliente todavia no recibio nada."""
    venta = _venta([
        _fulfilment(
            pack=[{"SKU": "SKU-1", "Box": "Box 1", "Quantity": 6}],
            ship=[{"Boxes": "Box 1", "ShipmentDate": "2026-01-12T00:00:00", "IsShipped": False}],
        )
    ])
    assert shipped_by_sku(venta) == {}


def test_una_caja_empacada_que_no_aparece_en_ningun_ship_queda_afuera():
    venta = _venta([
        _fulfilment(
            pack=[{"SKU": "SKU-1", "Box": "Box 9", "Quantity": 6}],
            ship=[{"Boxes": "Box 1", "ShipmentDate": "2026-01-12T00:00:00", "IsShipped": True}],
        )
    ])
    assert shipped_by_sku(venta) == {}


def test_dos_fulfilments_parciales_del_mismo_sku_se_suman():
    """El despacho multiple es la razon por la que el handoff eligio mirar Cin7. Quedarse
    con el ultimo fulfilment reporta como quiebre lo que si se sirvio."""
    venta = _venta([
        _fulfilment(
            pack=[{"SKU": "SKU-1", "Box": "Box 1", "Quantity": 6}],
            ship=[{"Boxes": "Box 1", "ShipmentDate": "2026-01-09T00:00:00", "IsShipped": True}],
        ),
        _fulfilment(
            pack=[{"SKU": "SKU-1", "Box": "Box 2", "Quantity": 2}],
            ship=[{"Boxes": "Box 2", "ShipmentDate": "2026-01-12T00:00:00", "IsShipped": True}],
        ),
    ])
    servido = shipped_by_sku(venta)
    assert servido["SKU-1"].qty == 8.0
    # La fecha es la del ULTIMO: la linea no termino de salir hasta ese camion, y usar la
    # del primero declara a tiempo una orden que se completo tres dias despues.
    assert servido["SKU-1"].dispatched == "2026-01-12"


def test_un_ship_con_varias_cajas_en_un_solo_texto_empalma_con_todas():
    """``Boxes`` es un STRING, no una lista, y puede llevar varias etiquetas separadas por
    coma. Un empalme por igualdad exacta pierde las lineas de todo envio multi-caja, en
    silencio, y el sintoma es un fill rate que baja sin motivo aparente."""
    venta = _venta([
        _fulfilment(
            pack=[
                {"SKU": "SKU-1", "Box": "Box 1", "Quantity": 6},
                {"SKU": "SKU-2", "Box": "Box 2", "Quantity": 3},
            ],
            ship=[{"Boxes": "Box 1, Box 2", "ShipmentDate": "2026-01-12T00:00:00",
                   "IsShipped": True}],
        )
    ])
    servido = shipped_by_sku(venta)
    assert servido["SKU-1"].qty == 6.0
    assert servido["SKU-2"].qty == 3.0


def test_dos_lineas_de_pack_del_mismo_sku_en_la_misma_caja_se_suman():
    """Un mismo SKU puede ir partido en dos lotes (batch/serie) dentro de una caja."""
    venta = _venta([
        _fulfilment(
            pack=[
                {"SKU": "SKU-1", "Box": "Box 1", "Quantity": 4, "BatchSN": "L1"},
                {"SKU": "SKU-1", "Box": "Box 1", "Quantity": 3, "BatchSN": "L2"},
            ],
            ship=[{"Boxes": "Box 1", "ShipmentDate": "2026-01-12T00:00:00", "IsShipped": True}],
        )
    ])
    assert shipped_by_sku(venta)["SKU-1"].qty == 7.0


def test_una_venta_sin_fulfilments_no_reporta_nada():
    assert shipped_by_sku(_venta([])) == {}


def test_una_cantidad_ausente_o_no_numerica_no_se_cuenta_como_cero():
    """Un ``null`` en la cantidad empacada es un dato que falta, no cero unidades."""
    venta = _venta([
        _fulfilment(
            pack=[{"SKU": "SKU-1", "Box": "Box 1", "Quantity": None},
                  {"SKU": "SKU-2", "Box": "Box 1", "Quantity": 2}],
            ship=[{"Boxes": "Box 1", "ShipmentDate": "2026-01-12T00:00:00", "IsShipped": True}],
        )
    ])
    servido = shipped_by_sku(venta)
    assert "SKU-1" not in servido
    assert servido["SKU-2"].qty == 2.0


# -- el conector: catalogo, stock y ordenes -------------------------------------

from src.connectors import InventorySource  # noqa: E402
from src.connectors.cin7 import Cin7Connector  # noqa: E402


class RoutedHttp:
    """Sirve payloads distintos por recurso; ``/sale`` responde segun el ID pedido."""

    class _Response:
        def __init__(self, payload: dict) -> None:
            self._payload = payload
            self.status_code = 200

        def json(self) -> dict:
            return self._payload

    def __init__(self, listas: dict[str, tuple[str, list[dict]]],
                 ventas: dict[str, dict] | None = None) -> None:
        self._listas = listas
        self._ventas = ventas or {}
        self.calls: list[tuple[str, dict]] = []

    def get(self, path, *, params=None, headers=None):
        params = dict(params or {})
        self.calls.append((path, params))
        recurso = path.strip("/")
        if recurso == "sale":
            return RoutedHttp._Response(self._ventas.get(params.get("ID"), {}))
        clave, items = self._listas.get(recurso, ("X", []))
        return RoutedHttp._Response({"Total": len(items), "Page": params.get("Page", 1),
                                     clave: items})


_CIN7_PRODUCTS = [
    {"SKU": "SKU-1", "Name": "Widget", "PriceTier1": 20.0, "AverageCost": 12.0},
    {"SKU": "SKU-2", "Name": "Gadget", "PriceTier1": 50.0, "AverageCost": 30.0},
]

_CIN7_STOCK = [
    {"SKU": "SKU-1", "Location": "Main", "OnHand": 40.0, "Allocated": 5.0, "Available": 35.0},
]

#: SO-1 cerrada y despachada entera menos dos unidades de SKU-2, que nunca salieron.
#: SO-2 a medio despachar. SO-3 anulada. SO-4 con la orden sin autorizar.
_SALE_LIST = [
    {"SaleID": "s1", "OrderNumber": "SO-1", "OrderDate": "2026-01-05T00:00:00",
     "ShipBy": "2026-01-10T00:00:00", "Status": "COMPLETED", "OrderStatus": "AUTHORISED",
     "CombinedShippingStatus": "SHIPPED"},
    {"SaleID": "s2", "OrderNumber": "SO-2", "OrderDate": "2026-01-06T00:00:00",
     "ShipBy": None, "Status": "BACKORDERED", "OrderStatus": "AUTHORISED",
     "CombinedShippingStatus": "PARTIALLY SHIPPED"},
    {"SaleID": "s3", "OrderNumber": "SO-3", "OrderDate": "2026-01-07T00:00:00",
     "Status": "VOIDED", "OrderStatus": "AUTHORISED", "CombinedShippingStatus": "VOIDED"},
    {"SaleID": "s4", "OrderNumber": "SO-4", "OrderDate": "2026-01-08T00:00:00",
     "Status": "ORDERING", "OrderStatus": "DRAFT", "CombinedShippingStatus": "NOT SHIPPED"},
]

_SALES = {
    "s1": {
        "ID": "s1", "SaleOrderNumber": "SO-1", "SaleOrderDate": "2026-01-05T00:00:00",
        "ShipBy": "2026-01-10T00:00:00", "Status": "COMPLETED",
        "CombinedShippingStatus": "SHIPPED",
        "Order": {"Status": "AUTHORISED", "Lines": [
            {"SKU": "SKU-1", "Name": "Widget", "Quantity": 10, "Price": 20.0},
            {"SKU": "SKU-2", "Name": "Gadget", "Quantity": 2, "Price": 50.0},
        ]},
        "Fulfilments": [{
            "TaskID": "t1",
            "Pack": {"Status": "AUTHORISED",
                     "Lines": [{"SKU": "SKU-1", "Box": "Box 1", "Quantity": 8}]},
            "Ship": {"Status": "AUTHORISED",
                     "Lines": [{"Boxes": "Box 1", "ShipmentDate": "2026-01-12T00:00:00",
                                "IsShipped": True}]},
        }],
    },
    "s2": {
        "ID": "s2", "SaleOrderNumber": "SO-2", "SaleOrderDate": "2026-01-06T00:00:00",
        "ShipBy": None, "Status": "BACKORDERED",
        "CombinedShippingStatus": "PARTIALLY SHIPPED",
        "Order": {"Status": "AUTHORISED", "Lines": [
            {"SKU": "SKU-1", "Name": "Widget", "Quantity": 5, "Price": 20.0},
            {"SKU": "SKU-2", "Name": "Gadget", "Quantity": 4, "Price": 50.0},
        ]},
        "Fulfilments": [{
            "TaskID": "t2",
            "Pack": {"Status": "AUTHORISED",
                     "Lines": [{"SKU": "SKU-1", "Box": "Box 7", "Quantity": 5}]},
            "Ship": {"Status": "AUTHORISED",
                     "Lines": [{"Boxes": "Box 7", "ShipmentDate": "2026-01-09T00:00:00",
                                "IsShipped": True}]},
        }],
    },
}


def _conector(**kw) -> Cin7Connector:
    http = RoutedHttp(
        {"product": ("Products", _CIN7_PRODUCTS),
         "ref/productavailability": ("ProductAvailabilityList", _CIN7_STOCK),
         "saleList": ("SaleList", _SALE_LIST)},
        _SALES,
    )
    return Cin7Connector(http, _CREDS, **kw)


def test_el_conector_satisface_el_protocolo_inventory_source():
    assert isinstance(_conector(), InventorySource)


def test_los_productos_mapean_sku_nombre_precio_y_costo():
    productos = {p.sku: p for p in _conector().list_products()}
    assert productos["SKU-1"].title == "Widget"
    assert productos["SKU-1"].price == 20.0
    assert productos["SKU-1"].cost == 12.0


def test_el_stock_usa_available_y_no_onhand():
    """``OnHand`` incluye lo ya asignado a otras ordenes (``Allocated``). Planificar sobre
    el fisico promete unidades que ya tienen dueno."""
    niveles = {lvl.sku: lvl for lvl in _conector().inventory_levels()}
    assert niveles["SKU-1"].available == 35.0
    assert niveles["SKU-1"].location == "Main"


def test_una_venta_anulada_o_sin_orden_autorizada_no_es_demanda():
    """SO-3 esta anulada y SO-4 tiene la orden en borrador. Ninguna de las dos es un
    compromiso con un cliente, y contarlas mete quiebres que nadie sufrio."""
    pedidos = _conector().orders()
    assert [o.order_id for o in pedidos] == ["SO-1", "SO-2"]


def test_since_viaja_como_createdsince_al_servidor():
    conector = _conector()
    conector.orders(since="2026-01-06")
    listas = [p for path, p in conector._http.calls if path.strip("/") == "saleList"]
    assert listas and listas[0].get("CreatedSince") == "2026-01-06"


def test_cada_venta_se_pide_entera_porque_la_lista_no_trae_lineas():
    """``/saleList`` no tiene lineas de pedido: hay que pedir ``/sale?ID=`` por venta. Es el
    costo que Cin7 impone y que Unleashed no, y se paga a 60 llamadas por minuto."""
    conector = _conector()
    conector.orders()
    ids = [p.get("ID") for path, p in conector._http.calls if path.strip("/") == "sale"]
    assert ids == ["s1", "s2"]


def test_orders_solo_ya_trae_las_tres_columnas_de_servicio():
    """La diferencia estructural con Unleashed, y la prueba de que ``scan_service`` no fue
    escrito a la medida del primer adaptador: Cin7 trae el fulfilment INLINE en la venta,
    asi que no necesita un ``orders_with_shipments()`` y ``orders()`` sola mide todo."""
    conector = _conector()
    assert not hasattr(conector, "orders_with_shipments")
    so1 = next(o for o in conector.orders() if o.order_id == "SO-1")
    porsku = {ln.sku: ln for ln in so1.lines}
    assert porsku["SKU-1"].delivered_qty == 8.0
    assert porsku["SKU-1"].promised_date == "2026-01-10"
    assert porsku["SKU-1"].delivered_date == "2026-01-12"


def test_sobre_una_venta_ya_cerrada_una_linea_sin_despacho_es_un_cero_MEDIDO():
    """El unico lugar de los dos adaptadores donde un 0.0 es honesto.
    ``CombinedShippingStatus == "SHIPPED"`` es terminal: la venta se cerro, y SKU-2 no salio.
    Eso es un quiebre total que SI se midio, y devolverlo como ``None`` lo escondaria --
    el error simetrico del que persigue el resto de este codigo."""
    so1 = next(o for o in _conector().orders() if o.order_id == "SO-1")
    porsku = {ln.sku: ln for ln in so1.lines}
    assert porsku["SKU-2"].delivered_qty == 0.0


def test_sobre_una_venta_a_medio_despachar_la_linea_sin_servir_sigue_en_none():
    """SO-2 esta PARTIALLY SHIPPED: SKU-2 puede salir manana. Un 0.0 aca inventa un quiebre
    sobre una orden que todavia no vencio."""
    so2 = next(o for o in _conector().orders() if o.order_id == "SO-2")
    porsku = {ln.sku: ln for ln in so2.lines}
    assert porsku["SKU-2"].delivered_qty is None
    assert porsku["SKU-2"].delivered_date is None


def test_una_venta_sin_shipby_deja_la_promesa_vacia():
    """Ausente, y no ``SaleOrderDate``: la fecha en que el cliente compro no es una promesa,
    y medir contra ella declara tarde a todo lo que no salio el mismo dia."""
    so2 = next(o for o in _conector().orders() if o.order_id == "SO-2")
    assert all(ln.promised_date is None for ln in so2.lines)


def test_el_tope_de_ventas_corta_y_lo_dice_en_las_notas():
    """Un tope silencioso se lee como cobertura completa. A 60 llamadas por minuto este
    adaptador VA a toparse en una cuenta real, y el operador tiene que ver cuanto quedo
    afuera antes de leer el fill rate como si fuera de toda la cartera."""
    conector = _conector(max_sales=1)
    pedidos = conector.orders()
    assert len(pedidos) == 1
    assert any("1" in n and "tope" in n.lower() for n in conector.notes)


def test_sin_tope_alcanzado_no_hay_ruido_en_las_notas():
    conector = _conector()
    conector.orders()
    assert conector.notes == ()


def test_la_cadena_entera_da_fill_rate_y_otif_medidos():
    """adaptador -> realized_service -> numeros.

    SKU-1: 8 de 10 en SO-1 (salio el 12 contra una promesa del 10) y 5 de 5 en SO-2 (sin
    promesa, o sea fuera de OTIF). Fill rate 13/15.
    SKU-2: 0 de 2 medido en SO-1; la linea de SO-2 no se midio y queda fuera del denominador.
    """
    from src.connectors import realized_service

    registros = {r.product_id: r for r in realized_service(_conector().orders())}
    assert registros["SKU-1"].fill_rate == pytest.approx(13 / 15)
    assert registros["SKU-1"].otif == 0.0
    assert registros["SKU-2"].fill_rate == 0.0
    assert registros["SKU-2"].lines_scored == 1
