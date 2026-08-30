"""Cin7 Core — el segundo adaptador del canal AU/NZ, y la prueba de que el guard generaliza.

Cin7 Core (ex DEAR Systems) es la otra plataforma del beachhead. Este adaptador lee su
catalogo, su stock y sus ventas, y llena las tres columnas de servicio que
``src/order_fulfillment.py`` necesita para medir fill rate, OTIF saliente (SCOR RL.1.1) y
venta perdida.

Su modelo de datos es sustancialmente distinto del de Unleashed, y ahi esta el valor: es la
primera prueba real de si ``ServiceCoverage`` describe backends o describia al primero que
se escribio.

QUE PUEDE Y QUE NO PUEDE MEDIR CIN7 CORE (verificado contra el blueprint oficial de la API
el 2026-08-29; la declaracion EJECUTABLE esta al final del archivo):

===================== =========================================================
clave de OrderLine    campo de Cin7 Core
===================== =========================================================
ordered_qty           ``Sale.Order.Lines[].Quantity``
unit_price            ``Sale.Order.Lines[].Price``
promised_date         ``Sale.ShipBy`` -- "date when shipment is due", a nivel
                      ORDEN. Vacia significa que no se prometio fecha, y la
                      linea sale de OTIF en vez de medirse contra SaleOrderDate.
delivered_qty         EMPALME de dos saltos: ``Fulfilments[].Pack.Lines[]``
                      (SKU -> Box -> Quantity) contra
                      ``Fulfilments[].Ship.Lines[]`` (Boxes -> IsShipped).
delivered_date        ``Fulfilments[].Ship.Lines[].ShipmentDate`` de la ULTIMA
                      caja que llevo ese SKU.
===================== =========================================================

Cuatro decisiones, y ninguna es de comodidad:

1. **La cantidad servida no sale de ningun endpoint: se EMPALMA.** Cin7 empaca por caja y
   despacha por caja. ``/sale/fulfilment/ship`` no tiene producto ni cantidad -- sus lineas
   son cajas. El unico camino de SKU a fecha de despacho pasa por la etiqueta de la caja, y
   esa etiqueta es texto libre: ``Box`` (singular) en el pack, ``Boxes`` (plural, y puede
   llevar varias separadas por coma) en el ship. Un empalme por igualdad exacta pierde las
   lineas de todo envio multi-caja, en silencio, y el sintoma es un fill rate que baja sin
   motivo aparente.

2. **Empacar no es despachar.** Solo cuentan las cajas cuya linea de ship trae
   ``IsShipped: true``. Contar lo empacado adelanta el servicio a un momento en que la
   mercaderia sigue en el deposito.

3. **Aca -- y solo aca -- un cero es honesto.** ``CombinedShippingStatus == "SHIPPED"`` es
   un estado TERMINAL de la venta entera. Sobre una venta asi, una linea sin cantidad
   despachada es un CERO MEDIDO: la venta se cerro sin servirla. Unleashed no tiene con que
   afirmar eso y por eso alli todo es ``None``. Negarse a reportar un cero que SI se midio
   es el error simetrico del que persigue el resto de este codigo: esconde un quiebre total.
   Sobre una venta ``PARTIALLY SHIPPED`` o sin despachar, vuelve a ser ``None``.

4. **No hay lectura masiva de fulfilments.** Unleashed tiene ``/SalesShipments?startDate=``;
   Cin7 no tiene equivalente. Hay que pedir ``GET /sale?ID=`` una vez por venta, y la API
   corta a 60 llamadas por minuto. 500 ventas son ~9 minutos. Eso no es un detalle de
   implementacion: el tope de ventas leidas viaja en ``notes`` porque un corte silencioso se
   lee como cobertura completa.

Y lo que se repite por tercera vez: **Cin7 Core tampoco registra fecha de llegada.**
``ShipmentDate`` es el DESPACHO. Hay ``TrackingNumber`` y ``TrackingURL``, pero ningun campo
de entrega confirmada. Su OTIF, igual que el de Unleashed y el de Odoo, mide salir a tiempo
del deposito.

SEGURIDAD, declarada porque cambia donde hay que auditar: la clave de Cin7 VIAJA en una
cabecera en cada request. La de Unleashed firma la query string y nunca viaja. No es un
defecto de este adaptador -- es como autentica el vendor -- pero significa que la clave
aparece en cualquier proxy que registre cabeceras.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: La base de la API v2. Se fija aca para que ``live_http`` sea el unico lugar que la sabe.
API_BASE = "https://inventory.dearsystems.com/ExternalApi/v2"

#: Lo que Cin7 corta. Documentado en el blueprint: "You reached 60 calls per minute API
#: limit". Este adaptador pide una llamada por venta, asi que el 429 es el modo de falla
#: esperado en una cuenta grande y no una rareza.
RATE_LIMIT_PER_MINUTE = 60


class Cin7Error(RuntimeError):
    """Cin7 contesto algo que no es una lectura. Nunca se degrada a una lista vacia.

    Un 401 que vuelve como ``[]`` es indistinguible de una cuenta sin ventas, y produce un
    informe que le dice al cliente que no tiene quiebres.
    """


class Cin7RateLimited(Cin7Error):
    """Se alcanzo el limite de llamadas por minuto. Se reintenta, no se arregla.

    Subclase a proposito: un llamador que solo quiere saber "fallo la lectura" atrapa
    ``Cin7Error`` y ya; uno que sabe esperar distingue este.
    """


@dataclass(frozen=True)
class Cin7Credentials:
    """Los dos identificadores de la API. Los DOS viajan en cabeceras en cada request."""

    account_id: str
    application_key: str


def auth_headers(creds: Cin7Credentials) -> dict[str, str]:
    """Las cabeceras que Cin7 exige. Sin firma: autentica con identificadores planos."""
    return {
        "api-auth-accountid": creds.account_id,
        "api-auth-applicationkey": creds.application_key,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _check(resp: Any, resource: str, pagina: int) -> None:
    """Un estado que no es 200 levanta, y el 429 se nombra por lo que es."""
    estado = getattr(resp, "status_code", 200)
    if estado == 200:
        return
    if estado == 429:
        raise Cin7RateLimited(
            f"Cin7 corto la lectura de /{resource} en la pagina {pagina}: se alcanzo el "
            f"limite de {RATE_LIMIT_PER_MINUTE} llamadas por minuto. Esta lectura pide una "
            "llamada por venta, asi que en una cuenta grande esto se espera: hay que "
            "reintentar mas lento, no arreglar nada."
        )
    raise Cin7Error(
        f"Cin7 contesto {estado} en /{resource} (pagina {pagina}). Una lectura fallida no "
        "se degrada a vacio: una cuenta sin ventas y una credencial rechazada darian el "
        "mismo informe, y uno de los dos dice que no hay quiebres."
    )


def fetch_pages(
    http: Any,
    resource: str,
    params: dict[str, Any],
    creds: Cin7Credentials,
    *,
    list_key: str,
    max_pages: int = 100,
) -> list[dict]:
    """Todas las paginas de un recurso, concatenadas. ``[]`` solo si de verdad no hay nada.

    Cin7 pagina por parametro (``?Page=&Limit=``) y envuelve la respuesta en
    ``{"Total": N, "Page": n, "<list_key>": [...]}``. El nombre de la lista cambia por
    recurso -- ``SaleList``, ``Products``, ``ProductAvailabilityList`` -- por eso entra
    explicito en vez de adivinarse: adivinarlo mal devuelve ``[]`` con un 200, que es la
    forma exacta de una cuenta vacia.

    Se corta por TRES motivos, y el orden importa: una pagina vacia (la evidencia manda
    sobre el contador -- un ``Total`` que no baja colgaria el job), haber juntado el total
    declarado, o el tope de paginas.
    """
    limpios = {k: v for k, v in params.items() if v is not None}
    headers = auth_headers(creds)
    items: list[dict] = []
    pagina = 1
    while pagina <= max_pages:
        resp = http.get(f"/{resource}", params={**limpios, "Page": pagina}, headers=headers)
        _check(resp, resource, pagina)
        cuerpo = resp.json() or {}
        lote = cuerpo.get(list_key) or []
        if not lote:
            break
        items.extend(lote)
        total = cuerpo.get("Total")
        if total is not None and len(items) >= int(total):
            break
        pagina += 1
    return items
