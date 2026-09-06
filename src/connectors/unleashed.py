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

from src.connectors import (
    COVERAGE_DERIVED,
    COVERAGE_MEASURED,
    FieldCoverage,
    InventoryLevel,
    Order,
    OrderLine,
    Product,
    ServiceCoverage,
)

#: La base de la API v1. Se fija aca y no en cada llamada para que ``live_http`` sea el
#: unico lugar del proyecto que la conoce.
API_BASE = "https://api.unleashedsoftware.com"


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


#: El maximo que acepta Unleashed. Menos paginas es menos rate limit y menos latencia en
#: la primera corrida, que es la unica que un prospecto mira.
_PAGE_SIZE = 200

#: Los estados que SON demanda. ``Deleted`` no es una venta y ``Parked`` es un borrador
#: que nadie confirmo; contarlos mete quiebres que ningun cliente sufrio.
SALE_STATES_COUNTED = ("Placed", "Backordered", "Completed")


def _text(value: Any) -> str | None:
    """Texto no vacio, o ``None``. Unleashed manda ``null`` y tambien cadenas vacias."""
    if value is None:
        return None
    texto = str(value).strip()
    return texto or None


def _iso_date(value: Any) -> str | None:
    """La fecha ISO de un instante de Unleashed, o ``None`` si no vino ninguno.

    Unleashed manda ``2026-01-10T00:00:00``. Solo se acepta esa forma, y cualquier otra se
    rechaza en vez de adivinarse: una fecha mal parseada saca la linea de OTIF, que es
    mejor que ordenarla contra otra que no significa lo mismo.
    """
    texto = _text(value)
    if texto is None or len(texto) < 10:
        return None
    fecha = texto[:10]
    if fecha[4] != "-" or fecha[7] != "-":
        return None
    try:
        int(fecha[0:4]), int(fecha[5:7]), int(fecha[8:10])
    except ValueError:
        return None
    return fecha


def _number(value: Any) -> float | None:
    """Un numero reportado, o ``None`` cuando el backend no reporto ninguno."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class UnleashedConnector:
    """``InventorySource`` sobre Unleashed. Ver el docstring del modulo para el mapeo.

    ``http`` es cualquier objeto con ``.get(path, params=..., headers=...)`` estilo httpx:
    ``live_http(creds)`` arma el real y los tests inyectan uno de mentira. El adaptador no
    arma su propio transporte a proposito -- un secreto que se ensambla en la capa HTTP hay
    que auditarlo en cada ruta que lo use.
    """

    def __init__(self, http: Any, creds: UnleashedCredentials, *, max_pages: int = 100) -> None:
        self._http = http
        self._creds = creds
        self._max_pages = max_pages

    def _read(self, resource: str, params: dict[str, Any] | None = None) -> list[dict]:
        return fetch_pages(
            self._http, resource, params or {}, self._creds, max_pages=self._max_pages
        )

    # -- read side (InventorySource) ------------------------------------------

    def list_products(self) -> list[Product]:
        productos: list[Product] = []
        for row in self._read("Products", {"pageSize": _PAGE_SIZE}):
            sku = _text(row.get("ProductCode"))
            if sku is None:
                continue
            productos.append(
                Product(
                    sku,
                    _text(row.get("ProductDescription")) or sku,
                    _number(row.get("DefaultSellPrice")) or 0.0,
                    _number(row.get("LastCost")) or 0.0,
                )
            )
        return productos

    def inventory_levels(self) -> list[InventoryLevel]:
        """El DISPONIBLE, no el fisico.

        ``AvailableQty`` descuenta lo ya comprometido a otras ordenes; ``QtyOnHand`` no.
        Planificar sobre el fisico promete unidades que ya tienen dueno. Sin
        ``AvailableQty`` se cae al fisico, que es lo unico que queda y se sabe optimista.
        """
        niveles: list[InventoryLevel] = []
        for row in self._read("StockOnHand", {"pageSize": _PAGE_SIZE}):
            sku = _text(row.get("ProductCode"))
            if sku is None:
                continue
            disponible = _number(row.get("AvailableQty"))
            if disponible is None:
                disponible = _number(row.get("QtyOnHand")) or 0.0
            niveles.append(
                InventoryLevel(sku, disponible, _text(row.get("WarehouseCode")) or "default")
            )
        return niveles

    def orders(self, *, since: str | None = None) -> list[Order]:
        """Las ordenes que son demanda, con lo pedido en cada linea.

        ``since`` se filtra en el SERVIDOR (``startDate``): traer el historico entero para
        descartarlo en memoria es lo que hace que la primera corrida contra una cuenta viva
        tarde minutos y pague rate limit por nada.

        La promesa es ``RequiredDate``, que es de la ORDEN, y por eso todas sus lineas
        llevan la misma. Vacia es lo habitual y significa que no se prometio fecha: la
        linea sale de OTIF en vez de medirse contra ``OrderDate``, que es cuando el cliente
        compro y no cuando le dijimos que llegaba.

        ``delivered_qty`` queda ``None`` aca SIEMPRE: ``/SalesOrders`` no trae cantidad
        despachada. La llena ``orders_with_shipments()``, empalmando ``/SalesShipments``.
        """
        return self._to_orders(self._read("SalesOrders", self._order_params(since)))

    def orders_with_shipments(self, *, since: str | None = None) -> list[Order]:
        """Las ordenes CON lo que realmente salio: la lectura completa del servicio.

        Dos llamadas en vez de una, y es la unica forma: ``/SalesOrders`` no tiene cantidad
        despachada y ``/SalesShipments`` no tiene lo pedido. Con las dos, este adaptador
        mide fill rate y OTIF de salida -- que es exactamente lo que la competencia del
        anillo 2 verificada el 2026-08-29 (Netstock, StockTrim) no puede medir.

        La MISMA ventana ``since`` filtra las dos lecturas, a proposito: dos ventanas
        distintas dan un fill rate calculado sobre un universo de ordenes y unos despachos
        sobre otro, y esa diferencia no se ve en ninguna de las dos salidas por separado.

        Una linea sin despacho queda en ``None``, no en 0.0, incluso dentro de una lectura
        donde otras si lo tienen: esa orden puede salir manana.
        """
        envios: dict[str, Any] = {"pageSize": _PAGE_SIZE}
        if since is not None:
            envios["startDate"] = since
        despachos = shipments_by_line(self._read("SalesShipments", envios))
        return self._to_orders(
            self._read("SalesOrders", self._order_params(since)), despachos=despachos
        )

    def _order_params(self, since: str | None) -> dict[str, Any]:
        params: dict[str, Any] = {
            "pageSize": _PAGE_SIZE,
            "orderStatus": ",".join(SALE_STATES_COUNTED),
        }
        if since is not None:
            params["startDate"] = since
        return params

    def _to_orders(
        self, filas: list[dict], despachos: dict[tuple[str, int], _Shipped] | None = None
    ) -> list[Order]:
        """Las filas crudas -> ``Order`` canonicos, ordenados por fecha.

        ``despachos`` es lo que ``/SalesShipments`` reporto, indexado por
        ``(OrderNumber, SalesOrderLineNumber)``. En ``None`` -- el camino de ``orders()`` --
        la cantidad servida queda sin llenar, y la fecha de salida es ``CompletedDate``,
        que es lo unico que ``/SalesOrders`` sabe del cierre.
        """
        salida: list[Order] = []
        for fila in filas:
            estado = _text(fila.get("OrderStatus")) or ""
            if estado not in SALE_STATES_COUNTED:
                continue
            numero = _text(fila.get("OrderNumber"))
            creada = _iso_date(fila.get("OrderDate"))
            if numero is None or creada is None:
                continue
            prometida = _iso_date(fila.get("RequiredDate"))
            completada = _iso_date(fila.get("CompletedDate"))
            lineas: list[OrderLine] = []
            for ln in fila.get("SalesOrderLines") or []:
                sku = _text((ln.get("Product") or {}).get("ProductCode"))
                pedidas = _number(ln.get("OrderQuantity"))
                if sku is None or pedidas is None:
                    continue
                enviado = (
                    despachos.get((numero, int(ln.get("LineNumber") or 0)))
                    if despachos is not None
                    else None
                )
                if enviado is not None:
                    servidas, salio = enviado.qty, enviado.dispatched
                elif despachos is not None:
                    # Se leyeron despachos y ESTA linea no tuvo ninguno. None y no 0.0: la
                    # orden puede salir manana, y un cero aca es un quiebre total inventado
                    # sobre una linea que todavia no vencio.
                    servidas, salio = None, None
                else:
                    # Sin leer despachos: la cantidad no existe en /SalesOrders, y lo unico
                    # que se sabe del cierre es CompletedDate, de la orden entera.
                    servidas, salio = None, completada
                lineas.append(
                    OrderLine(
                        sku,
                        pedidas,
                        _number(ln.get("UnitPrice")) or 0.0,
                        delivered_qty=servidas,
                        promised_date=prometida,
                        delivered_date=salio,
                    )
                )
            if lineas:
                salida.append(Order(numero, creada, tuple(lineas)))
        return sorted(salida, key=lambda o: o.created_at)


#: Los estados de despacho que efectivamente sacaron mercaderia. Uno borrado trae cantidad
#: igual, y sumarlo da un fill rate mayor a uno sobre unidades que nunca salieron.
_SHIPPED_STATUSES = ("Completed", "Dispatched", "Shipped", "Placed")


@dataclass(frozen=True)
class _Shipped:
    """Lo que salio de UNA linea de orden, sumado sobre todos sus despachos."""

    qty: float
    #: La fecha del ULTIMO despacho que la sirvio. Es DESPACHO, no llegada.
    dispatched: str | None


def shipments_by_line(filas: list[dict]) -> dict[tuple[str, int], _Shipped]:
    """``/SalesShipments`` -> ``{(OrderNumber, SalesOrderLineNumber): _Shipped}``.

    Se SUMA por linea porque Unleashed admite despachos parciales: una linea servida en dos
    camiones tiene dos ``SalesShipmentLine``, y quedarse con el ultimo reporta como quiebre
    lo que si se sirvio. La fecha, en cambio, es la del ULTIMO: la linea no termino de
    salir hasta ese camion, y usar la del primero declara a tiempo una orden que se
    completo tres dias despues.

    El empalme es por ``SalesOrderLineNumber`` y no por ``ProductCode``: una orden puede
    llevar el mismo SKU en dos lineas (precios o depositos distintos), y empalmar por
    producto le adjudicaria a una lo que salio de la otra.
    """
    acumulado: dict[tuple[str, int], _Shipped] = {}
    for envio in filas:
        if (_text(envio.get("ShipmentStatus")) or "") not in _SHIPPED_STATUSES:
            continue
        numero = _text(envio.get("OrderNumber"))
        if numero is None:
            continue
        salida = _iso_date(envio.get("DispatchDate"))
        for ln in envio.get("SalesShipmentLines") or []:
            linea = ln.get("SalesOrderLineNumber")
            cantidad = _number(ln.get("ShipmentQty"))
            if linea is None or cantidad is None:
                continue
            clave = (numero, int(linea))
            previo = acumulado.get(clave)
            if previo is None:
                acumulado[clave] = _Shipped(cantidad, salida)
                continue
            acumulado[clave] = _Shipped(
                previo.qty + cantidad,
                # ``max`` sobre las que existen: un despacho sin fecha no borra la del otro
                # camion, y tampoco se le inventa una al que no la trajo.
                max([d for d in (previo.dispatched, salida) if d], default=None),
            )
    return acumulado


def live_http(creds: UnleashedCredentials, *, timeout: float = 30.0) -> Any:
    """El cliente httpx apuntado a la API de Unleashed.

    ``creds`` no se consume aca: la firma se calcula por request en ``fetch_pages``, porque
    depende de la query string de ESE request. Aqui solo se fija la base y el timeout --
    acotado y no infinito, porque esta lectura puede correr dentro de un request web y una
    cuenta que no contesta tiene que soltar el hilo en vez de quedarselo.
    """
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - depende del extra instalado
        raise UnleashedError(
            "httpx es necesario para hablar con Unleashed -- instala el extra que lo trae, "
            "o inyecta un transporte con http= (los tests lo hacen)"
        ) from exc
    return httpx.Client(base_url=API_BASE, timeout=timeout, headers={"client-type": creds.client_type})


#: Lo que Unleashed puede y no puede medir del servicio al cliente. Verificado contra la
#: doc del vendor el 2026-08-29; ``tests/test_connector_service_contract.py`` lo CORRE
#: contra este adaptador en vez de leerlo.
SERVICE_COVERAGE = ServiceCoverage(
    backend="Unleashed (API v1: /SalesOrders + /SalesShipments)",
    delivered_qty=FieldCoverage(
        COVERAGE_DERIVED,
        "SalesShipmentLine.ShipmentQty, sumada por (OrderNumber, SalesOrderLineNumber)",
        "unidades que SALIERON de la linea, sumando despachos parciales. No esta en "
        "/SalesOrders: sin leer /SalesShipments queda en None, que es 'no lo medimos' y no "
        "un 0% de fill rate",
    ),
    promised_date=FieldCoverage(
        COVERAGE_MEASURED,
        "SalesOrder.RequiredDate",
        "la fecha que el cliente pidio, a nivel ORDEN y replicada a sus lineas. Vacia es lo "
        "habitual, y saca la linea de OTIF en vez de medirla contra OrderDate",
    ),
    delivered_date=FieldCoverage(
        COVERAGE_DERIVED,
        "SalesShipment.DispatchDate del ultimo despacho de la linea "
        "(SalesOrder.CompletedDate cuando no se leyeron despachos)",
        "la fecha de DESPACHO, no de llegada. Unleashed no registra ninguna llegada: no "
        "tiene recurso de entrega y sus dos fechas son salidas. El OTIF que sale de aca "
        "mide despachar antes de la fecha pedida, no llegar antes de ella",
    ),
)
