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
