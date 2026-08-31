"""Olist — el unico dato de este repo que sabe cuando el pedido LLEGO al cliente.

Olist es el dataset publico de un marketplace brasileno real: 99.441 ordenes con las tres
fechas que decide un OTIF -- la prometida al comprador en el checkout, la de despacho al
transportista, y la de ENTREGA al cliente.

Esa ultima es la que ningun ERP del canal AU/NZ registra. Verificado contra la doc de los
tres el 2026-08-29: Unleashed no tiene recurso de entrega; Cin7 Core tiene TrackingNumber y
TrackingURL pero ningun campo de entrega confirmada; Odoo llega hasta el movimiento de
stock. Los tres pueden decir "salio del deposito" y ninguno puede decir "llego".

POR QUE ESTO VIVE EN ``connectors/`` Y NO EN ``examples/``. No es un backend de cliente, y
su ``SERVICE_COVERAGE`` lo dice con esas palabras. Esta aca porque es la unica fuente con
la que se puede MEDIR cuanto se equivoca un OTIF de deposito contra uno de llegada, en vez
de afirmarlo -- y porque, estando aca, el guard de
``tests/test_connector_service_contract.py`` lo obliga a declarar su cobertura como a
cualquier otro. Un dato de referencia que se auto-declara es mas util que uno que hay que
creer. Mismo criterio que ``simulator.py``, que tambien se declara "NOT a system of record".

El mapeo, y sus dos decisiones:

===================== =========================================================
clave de OrderLine    campo de Olist
===================== =========================================================
ordered_qty           1.0 por fila de ``order_items``. El dataset no trae
                      cantidad: cada item es una unidad, y una orden de tres
                      unidades son tres filas.
unit_price            ``order_items.price`` (sin ``freight_value``: el flete no
                      es venta perdida cuando la unidad no sale).
promised_date         ``orders.order_estimated_delivery_date`` -- la fecha que
                      se le mostro al comprador.
delivered_qty         DERIVADA del estado de la orden. ``delivered`` -> 1.0.
                      ``canceled`` / ``unavailable`` -> 0.0 MEDIDO: son
                      terminales, esa orden ya no se sirve. Cualquier otro
                      estado (``shipped``, ``processing``, ``invoiced``...) ->
                      ``None``: salio o esta en curso y todavia puede llegar.
delivered_date        ``orders.order_delivered_customer_date``, la LLEGADA.
===================== =========================================================

1. **``canceled`` y ``unavailable`` son ceros medidos, y el resto no.** Es el mismo
   criterio que ``cin7.py`` aplica con ``CombinedShippingStatus == "SHIPPED"``: hace falta
   evidencia TERMINAL para afirmar un cero. ``shipped`` no la es -- ese pedido esta en un
   camion.

2. **``delivery="carrier"`` existe para medir la brecha, no para produccion.** Releer las
   MISMAS ordenes con la fecha de despacho es lo unico que permite cuantificar cuanto
   miente un OTIF de deposito. Sobre el dataset completo la diferencia es de 7,6 puntos:
   91,9% por llegada contra 99,5% por despacho, en 96.469 ordenes entregadas. Un ERP que
   solo ve la salida le informa al dueno un 99,5% mientras uno de cada doce pedidos llega
   tarde.
"""

from __future__ import annotations

from pathlib import Path

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

#: Los estados en que la orden LLEGO. Uno solo, y por eso la cantidad servida es binaria.
_DELIVERED = ("delivered",)
#: Los estados TERMINALES en que no llego y ya no va a llegar: un cero medido. El resto
#: (shipped, processing, invoiced, approved, created) sigue en curso y viaja como ``None``.
_TERMINAL_UNSERVED = ("canceled", "unavailable")

#: Cual de las dos fechas se usa como ``delivered_date``.
DELIVERY_CUSTOMER = "customer"
DELIVERY_CARRIER = "carrier"
_DELIVERY_COLUMN = {
    DELIVERY_CUSTOMER: "order_delivered_customer_date",
    DELIVERY_CARRIER: "order_delivered_carrier_date",
}


def _iso(value: object) -> str | None:
    """La fecha ISO de un timestamp de Olist (``2017-10-02 10:56:33``), o ``None``."""
    texto = "" if value is None else str(value).strip()
    if len(texto) < 10 or texto.lower() in ("nan", "nat"):
        return None
    fecha = texto[:10]
    if fecha[4] != "-" or fecha[7] != "-":
        return None
    try:
        int(fecha[0:4]), int(fecha[5:7]), int(fecha[8:10])
    except ValueError:
        return None
    return fecha


def _served(status: str) -> float | None:
    """Unidades servidas de una linea, deducidas del estado de su orden.

    ``None`` para todo lo que sigue en curso. La tentacion es mandar 0.0 -- "todavia no
    llego" -- y es exactamente el error que el resto de este paquete evita: un pedido en un
    camion no es un quiebre, y contarlo como tal inventa venta perdida sobre plata que el
    cliente todavia puede cobrar.
    """
    limpio = (status or "").strip().lower()
    if limpio in _DELIVERED:
        return 1.0
    if limpio in _TERMINAL_UNSERVED:
        return 0.0
    return None


class OlistSource:
    """``InventorySource`` sobre los CSV de Olist. Ver el docstring del modulo.

    Toma rutas y no un directorio fijo: ``data/`` esta gitignoreado, asi que la unica forma
    de que esto sea testeable en CI es que el llamador diga donde estan los archivos.
    """

    def __init__(
        self,
        orders_csv: str | Path,
        items_csv: str | Path,
        *,
        delivery: str = DELIVERY_CUSTOMER,
    ) -> None:
        if delivery not in _DELIVERY_COLUMN:
            raise ValueError(
                f"delivery debe ser {DELIVERY_CUSTOMER!r} (la llegada al cliente) o "
                f"{DELIVERY_CARRIER!r} (el despacho, para medir la brecha), no {delivery!r}"
            )
        self._orders_csv = Path(orders_csv)
        self._items_csv = Path(items_csv)
        self._delivery = delivery

    def _frames(self):
        import pandas as pd

        ordenes = pd.read_csv(self._orders_csv, dtype=str).fillna("")
        items = pd.read_csv(self._items_csv, dtype=str).fillna("")
        return ordenes, items

    # -- read side (InventorySource) ------------------------------------------

    def list_products(self) -> list[Product]:
        """El catalogo que aparece en las ordenes, con su ultimo precio visto.

        Sin costo: Olist no publica ninguno. Va en 0.0, que es como ya viaja en el resto
        del paquete cuando el backend no lo trae -- y significa que cualquier valorizacion
        de inventario sobre esta fuente es de venta, no de costo.
        """
        _, items = self._frames()
        vistos: dict[str, float] = {}
        for sku, precio in zip(items["product_id"], items["price"], strict=False):
            if sku:
                try:
                    vistos[sku] = float(precio)
                except (TypeError, ValueError):
                    vistos.setdefault(sku, 0.0)
        return [Product(sku, sku, precio, 0.0) for sku, precio in sorted(vistos.items())]

    def inventory_levels(self) -> list[InventoryLevel]:
        """Vacio, y a proposito: Olist publica ordenes, no inventario.

        Derivar niveles de lo vendido seria fabricar un almacen que nadie conto.
        """
        return []

    def orders(self, *, since: str | None = None) -> list[Order]:
        """Las ordenes con su lado de servicio, una linea por item.

        ``since`` filtra por fecha de COMPRA (``order_purchase_timestamp``), que es cuando
        el cliente pidio -- no por la de entrega, que sesgaria la ventana hacia lo que ya
        llego y dejaria afuera justamente los pedidos que fallaron.
        """
        ordenes, items = self._frames()
        columna = _DELIVERY_COLUMN[self._delivery]
        cabeceras: dict[str, tuple[str, float | None, str | None, str | None]] = {}
        for fila in ordenes.itertuples(index=False):
            comprada = _iso(getattr(fila, "order_purchase_timestamp", ""))
            oid = str(getattr(fila, "order_id", "")).strip()
            if not oid or comprada is None or (since is not None and comprada < since):
                continue
            cabeceras[oid] = (
                comprada,
                _served(str(getattr(fila, "order_status", ""))),
                _iso(getattr(fila, "order_estimated_delivery_date", "")),
                _iso(getattr(fila, columna, "")),
            )
        lineas: dict[str, list[OrderLine]] = {}
        for fila in items.itertuples(index=False):
            oid = str(getattr(fila, "order_id", "")).strip()
            sku = str(getattr(fila, "product_id", "")).strip()
            if oid not in cabeceras or not sku:
                continue
            _, servidas, prometida, llegada = cabeceras[oid]
            try:
                precio = float(getattr(fila, "price", 0.0) or 0.0)
            except (TypeError, ValueError):
                precio = 0.0
            lineas.setdefault(oid, []).append(
                OrderLine(
                    sku,
                    1.0,
                    precio,
                    delivered_qty=servidas,
                    promised_date=prometida,
                    # Solo hay fecha de entrega si algo se entrego. Una orden cancelada
                    # tiene un cero MEDIDO y ninguna fecha; una en transito no tiene
                    # ninguna de las dos.
                    #
                    # ``== 1.0`` y no ``if servidas``: bajo truthiness el cero medido de
                    # una cancelada cae en la misma rama que el None de una en transito,
                    # y da la respuesta correcta por accidente. Es el idiom que
                    # ``order_fulfillment.from_reported_fill_rate`` ya tuvo que corregir
                    # una vez, por lo mismo: 0.0 es un valor, no una ausencia.
                    delivered_date=llegada if servidas == 1.0 else None,
                )
            )
        return sorted(
            (Order(oid, cabeceras[oid][0], tuple(ls)) for oid, ls in lineas.items()),
            key=lambda o: o.created_at,
        )


#: Lo que Olist puede medir del servicio al cliente. Es la unica fuente de este repo, junto
#: con Shopify, cuya fecha de entrega es una LLEGADA y no una salida.
SERVICE_COVERAGE = ServiceCoverage(
    backend="Olist (dataset publico de un marketplace real; NO es un sistema de registro)",
    delivered_qty=FieldCoverage(
        COVERAGE_DERIVED,
        "orders.order_status (delivered -> 1; canceled/unavailable -> 0; el resto -> None)",
        "unidades entregadas de la linea, deducidas del estado. El dataset no trae cantidad: "
        "cada item es una unidad. Solo los estados TERMINALES producen un cero medido; una "
        "orden en transito viaja None porque todavia puede llegar",
    ),
    promised_date=FieldCoverage(
        COVERAGE_MEASURED,
        "orders.order_estimated_delivery_date",
        "la fecha de entrega que se le mostro al comprador al comprar",
    ),
    delivered_date=FieldCoverage(
        COVERAGE_MEASURED,
        "orders.order_delivered_customer_date (o order_delivered_carrier_date con "
        "delivery='carrier', solo para medir la brecha)",
        "la fecha en que el pedido LLEGO al cliente. Es la unica llegada real de este "
        "repo fuera de Shopify: Unleashed, Cin7 Core y Odoo solo registran la salida del "
        "deposito, y por eso su OTIF mide despacho y no entrega",
    ),
)
