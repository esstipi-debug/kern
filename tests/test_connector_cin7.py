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
