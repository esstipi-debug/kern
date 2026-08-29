"""Unleashed — ``InventorySource`` sobre el ERP del canal AU/NZ elegido.

Unleashed es una de las dos plataformas del beachhead (la otra es Cin7 Core). Este
adaptador lee su catalogo, su stock y sus ordenes de venta, y -- lo unico que importa --
llena las tres columnas de servicio que ``src/order_fulfillment.py`` necesita para medir
fill rate, OTIF saliente (SCOR RL.1.1) y venta perdida.

QUE PUEDE Y QUE NO PUEDE MEDIR UNLEASHED (verificado contra la doc del vendor el
2026-08-29; la declaracion EJECUTABLE esta al final del archivo, en ``SERVICE_COVERAGE``,
y ``tests/test_connector_service_contract.py`` la corre contra este adaptador):

===================== =========================================================
clave de OrderLine    campo de Unleashed
===================== =========================================================
ordered_qty           ``SalesOrderLine.OrderQuantity``
unit_price            ``SalesOrderLine.UnitPrice``
promised_date         ``SalesOrder.RequiredDate`` -- la fecha que el cliente
                      pidio, a nivel ORDEN. Vacia es lo normal, y una linea sin
                      promesa sale de OTIF en vez de medirse contra OrderDate.
delivered_qty         ``SalesShipmentLine.ShipmentQty``, sumada por linea de
                      orden y empalmada por (OrderNumber, SalesOrderLineNumber).
                      NO sale de /SalesOrders: ahi no existe. Sin despachos
                      leidos queda en None -- que es la respuesta correcta para
                      un cliente cuyos despachos no leimos, no un 0% de fill rate.
delivered_date        ``SalesShipment.DispatchDate`` del ULTIMO despacho de la
                      linea, o ``SalesOrder.CompletedDate`` cuando no se leyeron
                      despachos.
===================== =========================================================

Tres decisiones, y ninguna es de comodidad:

1. **Unleashed no tiene fecha de llegada. Ninguna.** Ni ``CompletedDate`` ni
   ``DispatchDate`` son la recepcion del cliente: las dos son el momento en que la
   mercaderia SALIO, y el indice de endpoints no tiene ningun recurso de entrega. Asi
   que el OTIF que sale de aca mide *despachar antes de la fecha pedida*, no *llegar*, y
   la declaracion lo dice con esas palabras. Llamar llegada a una salida convierte cada
   demora de transporte en puntualidad -- la misma regla que ``shopify_service.py`` fija
   en su decision 2, donde si hay ``deliveredAt`` y por eso se exige.

2. **La cantidad despachada se EMPALMA, no se deduce.** ``/SalesShipments`` trae
   ``ShipmentQty`` por linea con su ``SalesOrderLineNumber``, asi que un despacho parcial
   se suma y una orden servida en dos camiones da la suma real. Deducirla de
   ``OrderStatus`` (``Completed`` -> todo servido) seria inventar: una orden se puede
   cerrar con menos unidades de las pedidas.

3. **Las ordenes ``Deleted`` y ``Parked`` no cuentan.** Una borrada no es demanda y una
   estacionada no es un compromiso; contarlas mete quiebres que ningun cliente sufrio.

La clave privada FIRMA y no viaja: Unleashed autentica con HMAC-SHA256 sobre la query
string, en base64, en la cabecera ``api-auth-signature``. Mandar la clave en una cabecera
la deja en cada log de proxy del cliente.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from dataclasses import dataclass
from typing import Any


class UnleashedError(RuntimeError):
    """Unleashed contesto algo que no es una lectura. Nunca se degrada a una lista vacia.

    Un 401 que vuelve como ``[]`` es indistinguible de una tienda sin ventas, y produce un
    informe que le dice al cliente que no tiene quiebres.
    """


@dataclass(frozen=True)
class UnleashedCredentials:
    """El par de la API. ``api_key`` FIRMA y no se manda en ninguna cabecera."""

    api_id: str
    api_key: str
    #: Unleashed exige identificar la app: ``<cuenta>/<app>``, minusculas, sin espacios.
    client_type: str = "kern/kern"


def signature(query: str, api_key: str) -> str:
    """HMAC-SHA256 de la query string, en base64. ``query`` va SIN el ``?`` inicial.

    Sin parametros se firma la cadena vacia. Firmar el ``?`` o el path devuelve 401 solo
    en los endpoints que no llevan filtros, que es donde nadie mira primero.
    """
    digest = hmac.new(api_key.encode("utf-8"), query.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def _query_string(params: dict[str, Any]) -> str:
    """Los parametros como los va a mandar el transporte, para firmar exactamente eso.

    Ordenados: la firma cubre el TEXTO, asi que dos ordenes distintos del mismo dict dan
    dos firmas distintas y una de las dos es la que el servidor no espera.
    """
    return "&".join(f"{k}={v}" for k, v in sorted(params.items()) if v is not None)


def auth_headers(creds: UnleashedCredentials, query: str) -> dict[str, str]:
    """Las cinco cabeceras que Unleashed exige, con la firma de ``query``."""
    return {
        "api-auth-id": creds.api_id,
        "api-auth-signature": signature(query, creds.api_key),
        "client-type": creds.client_type,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def fetch_pages(
    http: Any,
    resource: str,
    params: dict[str, Any],
    creds: UnleashedCredentials,
    *,
    max_pages: int = 100,
) -> list[dict]:
    """Todas las paginas de un recurso, concatenadas. ``[]`` solo si de verdad no hay nada.

    Unleashed pagina por SEGMENTO DE RUTA (``/SalesOrders/2``), no por cursor, y la firma
    cubre solo los parametros -- que son los mismos en todas las paginas. Aun asi se
    recalcula por pagina: si alguien mueve el numero de pagina a los parametros, reusar la
    firma falla en la pagina 2 y nunca en la 1.

    ``max_pages`` es un tope y no una optimizacion: un servidor que devuelve
    ``NumberOfPages`` creciente colgaria el job para siempre.
    """
    limpios = {k: v for k, v in params.items() if v is not None}
    query = _query_string(limpios)
    headers = auth_headers(creds, query)
    items: list[dict] = []
    pagina = 1
    while pagina <= max_pages:
        resp = http.get(f"/{resource}/{pagina}", params=limpios, headers=headers)
        estado = getattr(resp, "status_code", 200)
        if estado != 200:
            raise UnleashedError(
                f"Unleashed contesto {estado} en /{resource}/{pagina}. Una lectura fallida "
                "no se degrada a vacio: una cuenta sin ventas y un token rechazado darian "
                "el mismo informe, y uno de los dos dice que no hay quiebres."
            )
        cuerpo = resp.json() or {}
        items.extend(cuerpo.get("Items") or [])
        total = int((cuerpo.get("Pagination") or {}).get("NumberOfPages") or 1)
        if pagina >= total:
            break
        pagina += 1
    return items
