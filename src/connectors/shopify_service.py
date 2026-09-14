"""El lado de SERVICIO de Shopify: lo pedido, lo servido, lo prometido, lo llegado.

``shopify_connect.py`` lee la demanda y la agrega derecho a ``SalesHistory``: filas
de (SKU, periodo, unidades), que es lo que comen el pronostico y ABC-XYZ. Nunca
produce un ``Order`` canonico, asi que ``src/order_fulfillment.py`` -- que mide
fill rate, OTIF (SCOR RL.1.1) y venta perdida a partir de lineas de pedido -- no
tenia de donde medirle el servicio a una tienda Shopify. Este modulo es el puente:
lee el mismo JSONL masivo y devuelve ``Order``/``OrderLine`` con las cinco claves
puestas, para que ``src.connectors.realized_service`` funcione sin cambios.

De donde sale cada numero (Admin GraphQL, verificado 2026-08-26):

===================== =========================================================
clave                 campo de Shopify
===================== =========================================================
ordered_qty           ``LineItem.currentQuantity`` -- pedidas menos reembolsadas
                      y removidas. ``quantity`` las incluye, y cobrarle al
                      almacen una unidad que el cliente devolvio es un quiebre
                      inventado.
delivered_qty         ``currentQuantity - unfulfilledQuantity``. Shopify NO
                      tiene un campo de cantidad entregada: se deduce.
unit_price            ``LineItem.originalUnitPriceSet.shopMoney.amount``
promised_date         ``FulfillmentOrder.deliveryMethod.maxDeliveryDateTime``,
                      la fecha de llegada mas tardia que se le mostro al
                      comprador EN EL CHECKOUT, junto a ``brandedPromise`` y
                      ``presentedName``.
delivered_date        ``Fulfillment.deliveredAt`` -- la fecha en que ese envio
                      se ENTREGO, no en que salio. Es mejor dato que el que da
                      Odoo, donde solo hay fecha de despacho.
===================== =========================================================

Cuatro decisiones, y ninguna es de comodidad:

1. **La fecha de llegada es por ORDEN, no por linea.** Una operacion masiva
   admite como maximo dos niveles de conexion anidada, asi que
   ``orders { fulfillments { fulfillmentLineItems } }`` es invalido y no hay
   forma de saber que linea sirvio cada envio. Se usa la fecha del ULTIMO envio
   entregado de la orden, que es cuando la orden termino de llegar. La
   granularidad no es una perdida: el pedido perfecto de SCOR se define sobre la
   ORDEN, no sobre la linea. La completitud si es por linea, y exacta.

2. **Sin ``deliveredAt`` la orden no tiene fecha de llegada.** Ese campo solo se
   llena si el carrier le reporta la entrega a Shopify. Si UN envio de la orden
   no la reporto, no se sabe cuando termino de llegar, y la orden sale del
   denominador de OTIF. Nunca se sustituye por ``Fulfillment.createdAt``: eso es
   cuando salio del deposito, y llamarlo entrega convierte cada demora de
   transporte en puntualidad.

3. **La promesa ausente por falta de permiso se NOMBRA.** ``order.fulfillmentOrders``
   lo filtra Shopify segun los scopes concedidos: sin
   ``read_merchant_managed_fulfillment_orders`` devuelve lista vacia con un 200,
   no un 403. "El comerciante no promete fechas" y "la app no tiene permiso para
   verlas" son dos hechos distintos que producen exactamente la misma salida, y
   uno de los dos se arregla con un click. Mismo tratamiento que
   ``ALL_ORDERS_SCOPE`` en ``shopify_connect.py``: opcional, y declarado cuando
   falta.

4. **Las fechas se comparan en UTC.** Shopify las entrega en UTC y aca se truncan
   a diez caracteres, asi que una entrega de las 21:00 en Santiago cae al dia
   siguiente. Afecta por igual a la promesa y a la llegada, que vienen de la
   misma fuente, pero puede correr un dia un caso de borde contra una promesa
   fijada a fin del dia local.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

from src.connectors import Order, OrderLine
from src.connectors.shopify_api_version import PINNED_VERSION

# ``_order_query`` es el mismo filtro de ventana que usa la lectura de demanda, y
# se comparte a proposito: dos filtros distintos darian un fill rate calculado
# sobre un universo de ordenes y una demanda sobre otro, y la diferencia no se
# veria en ninguna de las dos salidas por separado. ``run_bulk_read`` es la
# espera acotada y el manejo de estados terminales, incluida la regla de no tocar
# ``partialDataUrl``.
from src.connectors.shopify_connect import BulkStatus, _order_query, run_bulk_read

#: Sin este scope ``order.fulfillmentOrders`` viene VACIO -- con un 200, no con un
#: 403. No esta en ``REQUIRED_SCOPES`` porque todo lo demas funciona sin el; esta
#: aqui porque la salida tiene que poder decir que la promesa falta por permiso y
#: no porque el comerciante no prometa nada.
FULFILLMENT_ORDERS_SCOPE = "read_merchant_managed_fulfillment_orders"

#: El unico estado de ``Fulfillment`` que entrego algo. Los otros (CANCELLED,
#: ERROR, FAILURE, OPEN, PENDING) pueden traer fecha igual, y contarlos haria
#: llegar una orden que nunca llego.
_FULFILLMENT_OK = "SUCCESS"

NOTA_SIN_SCOPE = (
    "fecha prometida: AUSENTE POR PERMISO. Shopify filtra order.fulfillmentOrders segun los "
    f"scopes concedidos, y a esta app le falta {FULFILLMENT_ORDERS_SCOPE}: devuelve lista vacia "
    "con un 200, no un error. Sin la promesa, OTIF no se puede medir y queda reportado como no "
    "medido -- que NO es lo mismo que un comerciante que no promete fechas de entrega."
)


def bulk_service_query(since: str | None = None, until: str | None = None) -> str:
    """La consulta que corre ADENTRO de la operacion masiva.

    Sin un solo ``first``: una operacion masiva trae la conexion entera y Shopify
    rechaza la consulta si lleva argumentos de paginacion -- de paso, ninguna
    orden puede venir con las lineas truncadas.

    Cuatro conexiones y dos niveles de profundidad, contra los limites de una
    operacion masiva (cinco conexiones, dos niveles). Ese segundo limite es el que
    deja ``fulfillmentLineItems`` afuera y hace que la fecha de llegada sea por
    orden; quien agregue una quinta conexion aqui recalcula las dos cosas.
    """
    return (
        '{ orders(query: "%s") { edges { node { id createdAt cancelledAt test '
        "lineItems { edges { node { id sku quantity currentQuantity unfulfilledQuantity "
        "originalUnitPriceSet { shopMoney { amount } } } } } "
        "fulfillments { edges { node { id status createdAt deliveredAt } } } "
        "fulfillmentOrders { edges { node { id deliveryMethod { maxDeliveryDateTime "
        "minDeliveryDateTime brandedPromise presentedName } } } } "
        "} } } }" % _order_query(since, until)
    )


def _gid_type(gid: Any) -> str:
    """``gid://shopify/LineItem/1`` -> ``LineItem``.

    El tipo se saca del segmento completo y no por prefijo: ``FulfillmentOrder``
    empieza igual que ``Fulfillment``, y confundirlos mete la promesa en la lista
    de envios -- donde no tiene ``deliveredAt`` y deja la orden sin fecha.
    """
    partes = str(gid or "").split("/")
    return partes[3] if len(partes) > 3 else ""


def service_events(lineas: Iterable[Any]) -> Iterator[dict]:
    """El JSONL masivo -> un evento por orden, con sus tres clases de hijo.

    El formato aplana las conexiones: cada hijo viene como su propio objeto con
    ``__parentId`` apuntando a la orden, emitido despues de su padre, asi que se
    reconstruye en streaming. Un hijo cuyo padre no es la ultima orden vista se
    cuenta como huerfano en vez de adjudicarse a la orden equivocada.
    """
    actual: dict | None = None
    for linea in lineas:
        texto = linea.decode("utf-8") if isinstance(linea, bytes) else str(linea)
        texto = texto.strip()
        if not texto:
            continue
        obj = json.loads(texto)
        padre = obj.get("__parentId")
        if padre is None:
            if actual is not None:
                yield actual
            actual = {
                "id": obj.get("id"),
                "created_at": obj.get("createdAt"),
                "cancelled": bool(obj.get("cancelledAt")),
                "test": bool(obj.get("test")),
                "lines": [],
                "fulfillments": [],
                "promises": [],
                "orphans": 0,
            }
            continue
        if actual is None or padre != actual.get("id"):
            if actual is not None:
                actual["orphans"] += 1
            continue
        tipo = _gid_type(obj.get("id"))
        if tipo == "LineItem":
            actual["lines"].append(obj)
        elif tipo == "Fulfillment":
            actual["fulfillments"].append(obj)
        elif tipo == "FulfillmentOrder":
            actual["promises"].append(obj)
        else:
            actual["orphans"] += 1
    if actual is not None:
        yield actual


def _iso_date(value: Any) -> str | None:
    """La fecha ISO de un instante de Shopify, o ``None`` si no vino ninguno."""
    texto = str(value or "").strip()
    return texto[:10] if len(texto) >= 10 else None


def _numero(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _precio(linea: dict) -> float:
    monto = ((linea.get("originalUnitPriceSet") or {}).get("shopMoney") or {}).get("amount")
    return _numero(monto) or 0.0


def _fecha_de_llegada(envios: list[dict]) -> tuple[str | None, bool]:
    """Cuando termino de llegar la orden, y si algun envio no lo reporto.

    Devuelve ``(None, False)`` cuando no salio nada -- eso no es un dato faltante
    sino una orden sin despachar -- y ``(None, True)`` cuando salio y al menos un
    envio no reporto entrega, que si es la medicion que falta.
    """
    exitosos = [f for f in envios if str(f.get("status") or "").upper() == _FULFILLMENT_OK]
    if not exitosos:
        return None, False
    fechas = [_iso_date(f.get("deliveredAt")) for f in exitosos]
    if any(d is None for d in fechas):
        return None, True
    return max(d for d in fechas if d), False


def _fecha_prometida(promesas: list[dict]) -> str | None:
    """La mas tardia de las promesas de la orden.

    Una orden partida en varios fulfillment orders tiene una promesa por cada uno.
    Se toma la ultima por la misma razon por la que la llegada es la del ultimo
    envio: la orden no esta prometida entera hasta esa fecha, y aparear la promesa
    mas temprana con la llegada mas tardia mediria una carrera que nadie corrio.
    """
    fechas = [
        d
        for p in promesas
        if (d := _iso_date((p.get("deliveryMethod") or {}).get("maxDeliveryDateTime")))
    ]
    return max(fechas) if fechas else None


@dataclass(frozen=True)
class ServiceRead:
    """Las ordenes con su lado de servicio, mas lo que se dejo afuera y por que."""

    orders: tuple[Order, ...] = ()
    #: Que se descarto y por que motivo. Una salida que solo trae lo que sobrevivio
    #: es indistinguible de una lectura completa.
    dropped: dict = field(default_factory=dict)
    #: Lo que hay que decirle al operador sobre lo que NO se pudo medir.
    notes: tuple[str, ...] = ()


def read_service_orders(
    lineas: Iterable[Any], *, fulfillment_orders_scope: bool = True
) -> ServiceRead:
    """El JSONL masivo -> ``Order`` canonicos, listos para ``realized_service``.

    ``fulfillment_orders_scope`` es lo que el chequeo de conexion vio: en ``False``
    la lectura sabe que la promesa no falta porque el comerciante no prometa, sino
    porque Shopify no la dejo ver, y lo dice en ``notes``.
    """
    descartes = {
        "cancelada": 0,
        "de_prueba": 0,
        "sin_sku": 0,
        "sin_fecha": 0,
        "sin_fecha_de_entrega": 0,
        "linea_huerfana": 0,
    }
    pedidos: list[Order] = []
    for ev in service_events(lineas):
        if ev["cancelled"]:
            descartes["cancelada"] += 1
            continue
        if ev["test"]:
            descartes["de_prueba"] += 1
            continue
        descartes["linea_huerfana"] += ev["orphans"]
        creada = _iso_date(ev["created_at"])
        if creada is None:
            descartes["sin_fecha"] += 1
            continue
        llegada, sin_reportar = _fecha_de_llegada(ev["fulfillments"])
        if sin_reportar:
            descartes["sin_fecha_de_entrega"] += 1
        prometida = _fecha_prometida(ev["promises"])
        renglones: list[OrderLine] = []
        for ln in ev["lines"]:
            sku = str(ln.get("sku") or "").strip()
            if not sku:
                descartes["sin_sku"] += 1
                continue
            pedidas = _numero(ln.get("currentQuantity"))
            if pedidas is None:
                pedidas = _numero(ln.get("quantity"))
            sin_servir = _numero(ln.get("unfulfilledQuantity"))
            # None y no 0.0 cuando Shopify no dijo cuantas quedaron sin servir:
            # una ausencia contada como cero es un fill rate perfecto inventado.
            servidas = None if pedidas is None or sin_servir is None else max(pedidas - sin_servir, 0.0)
            renglones.append(
                OrderLine(
                    sku,
                    pedidas or 0.0,
                    _precio(ln),
                    delivered_qty=servidas,
                    promised_date=prometida,
                    delivered_date=llegada,
                )
            )
        if renglones:
            pedidos.append(Order(str(ev["id"]), creada, tuple(renglones)))
    notas = () if fulfillment_orders_scope else (NOTA_SIN_SCOPE,)
    return ServiceRead(orders=tuple(pedidos), dropped=descartes, notes=notas)


def read_service_orders_bulk(
    http: Any,
    *,
    since: str | None = None,
    until: str | None = None,
    api_version: str = PINNED_VERSION,
    access_token: str = "",
    bulk_id: str = "",
    max_wait: float = 120.0,
    poll_every: float = 3.0,
    sleep: Any = None,
    fetch_jsonl: Any = None,
    fulfillment_orders_scope: bool = True,
) -> tuple[ServiceRead | None, BulkStatus]:
    """El servicio de una tienda viva, por LECTURA MASIVA. ``(lectura, estado)``.

    ``lectura`` es ``None`` mientras la operacion sigue corriendo: el llamador
    vuelve mas tarde con ``bulk_id`` y no paga de nuevo el arranque. Misma
    maquinaria que la lectura de demanda (``run_bulk_read``), otra consulta.

    ``fulfillment_orders_scope`` sale de ``ConnectionCheck.fulfillment_orders``.
    Pasarlo importa: en ``False`` la salida distingue "no hay promesa" de "no
    tenemos permiso para verla", que Shopify entrega exactamente igual.
    """
    lineas, estado = run_bulk_read(
        http,
        query=bulk_service_query(since, until),
        api_version=api_version,
        access_token=access_token,
        bulk_id=bulk_id,
        max_wait=max_wait,
        poll_every=poll_every,
        sleep=sleep,
        fetch_jsonl=fetch_jsonl,
    )
    if lineas is None:
        return None, estado
    return read_service_orders(lineas, fulfillment_orders_scope=fulfillment_orders_scope), estado
