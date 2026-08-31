"""La fecha de LLEGADA: el dato que no vive en el ERP y hay que ir a buscarlo al transportista.

Los tres backends del canal AU/NZ registran cuando la mercaderia SALIO y ninguno cuando
llego. Verificado contra la doc de los tres el 2026-08-29: Unleashed no tiene recurso de
entrega, Cin7 Core tiene ``TrackingNumber`` y ``TrackingURL`` pero ningun campo de entrega
confirmada, y Odoo llega hasta el movimiento de stock.

Cuanto importa esa diferencia esta medido, no supuesto: sobre 98.666 pedidos reales
(``examples/run_service_report.py``), el OTIF por llegada es 92,9% y por despacho 99,2%.
Seis coma dos puntos de pedidos que salieron a tiempo y llegaron tarde, invisibles para el
sistema del cliente.

LA ARQUITECTURA, Y POR QUE NO ES OTRO ``InventorySource``. Un transportista no sabe que se
pidio: no tiene productos, ni cantidades, ni precios, ni promesas. Solo sabe cuando llego un
envio. Forzarlo dentro de ``InventorySource`` obligaria a inventar las otras tres cosas.

Por eso ``ArrivalSource`` es un Protocol aparte -- misma razon por la que ``ReceiptsSource``
lo es -- y por eso un ERP y un transportista JUNTOS pueden medir el OTIF de llegada mientras
ninguno de los dos solo puede. ``coverage_of_arrivals`` compone las dos declaraciones para
que el informe diga de donde sale cada campo.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Protocol, runtime_checkable

from src.connectors import Order, ServiceCoverage

_NOTA_COBERTURA = (
    "El transportista reporto la llegada de {matched} de {total} ordenes; {unmatched} de "
    "{total} quedaron sin fecha de llegada y salen del denominador de OTIF. Un OTIF de "
    "llegada calculado sobre una cobertura parcial NO es el de la cartera entera."
)
_NOTA_COMPLETA = "El transportista reporto la llegada de las {total} ordenes leidas."
_NOTA_VACIA = "No hay ordenes que empalmar."


@dataclass(frozen=True)
class Arrival:
    """Cuando llego un envio, y a que orden del ERP corresponde.

    ``order_id`` es la referencia del ERP -- ``OrderNumber`` en Unleashed y en Cin7 -- y no
    el numero de seguimiento: el tracking identifica al paquete y una orden puede viajar en
    varios. La llave tiene que ser la orden o el empalme cuenta una entrega por bulto.
    """

    order_id: str
    #: Fecha ISO ``YYYY-MM-DD`` en que el transportista la dio por entregada.
    arrived_on: str


@runtime_checkable
class ArrivalSource(Protocol):
    """Un backend que sabe cuando llegaron los envios. Y nada mas que eso.

    Deliberadamente NO se dobla dentro de ``InventorySource``: ese Protocol es
    ``runtime_checkable`` y se satisface estructuralmente, asi que agregarle un metodo
    des-satisfaria en silencio a todos los adaptadores existentes en cuanto algo corriera un
    ``isinstance``. Y ademas seria mentira: un transportista no tiene catalogo ni stock.
    """

    def arrivals(self, *, since: str | None = None) -> list[Arrival]: ...


@dataclass(frozen=True)
class JoinedOrders:
    """Las ordenes con la llegada puesta, y cuanto del empalme quedo sin cubrir."""

    orders: tuple[Order, ...]
    #: Ordenes del ERP para las que el transportista reporto una llegada.
    matched: int
    #: Ordenes del ERP sin fecha de llegada. Sus lineas salen del denominador de OTIF.
    unmatched: int
    #: Lo que hay que decirle al operador. Va al informe, no a un log.
    note: str

    @property
    def coverage(self) -> float | None:
        """Fraccion de ordenes con llegada conocida. ``None`` si no habia ordenes."""
        total = self.matched + self.unmatched
        return self.matched / total if total else None


def with_arrivals(orders: Iterable[Order], source: ArrivalSource, *, since: str | None = None):
    """Las ordenes del ERP, con la fecha de despacho reemplazada por la de llegada.

    Solo se toca ``delivered_date``. Lo pedido, lo servido y la promesa siguen siendo del
    ERP, que es el unico que los sabe.

    UNA ORDEN QUE EL TRANSPORTISTA NO REPORTA QUEDA SIN FECHA, no con la del ERP. Es la
    decision que separa esto de un informe que miente: dejarle el despacho la mezclaria con
    las que si tienen llegada, y como el despacho siempre es mas temprano, el "OTIF de
    llegada" saldria mejor de lo que es -- un promedio de dos cosas distintas presentado
    como una. Sin fecha, la linea sale del denominador de OTIF, que es lo que efectivamente
    pasa: de esa orden no sabemos cuando llego.

    Una linea sin cantidad servida tampoco recibe fecha: si el ERP no la dio por entregada,
    no hay entrega que fechar aunque el transportista tenga un evento suelto.
    """
    pedidos = list(orders)
    llegadas = {a.order_id: a.arrived_on for a in source.arrivals(since=since) if a.arrived_on}
    salida: list[Order] = []
    emparejadas = 0
    for pedido in pedidos:
        cuando = llegadas.get(pedido.order_id)
        if cuando is not None:
            emparejadas += 1
        salida.append(
            replace(
                pedido,
                lines=tuple(
                    replace(
                        linea,
                        delivered_date=(cuando if linea.delivered_qty else None),
                    )
                    for linea in pedido.lines
                ),
            )
        )
    sin_emparejar = len(pedidos) - emparejadas
    if not pedidos:
        nota = _NOTA_VACIA
    elif sin_emparejar:
        nota = _NOTA_COBERTURA.format(
            matched=emparejadas, unmatched=sin_emparejar, total=len(pedidos)
        )
    else:
        nota = _NOTA_COMPLETA.format(total=len(pedidos))
    return JoinedOrders(tuple(salida), emparejadas, sin_emparejar, nota)


def coverage_of_arrivals(erp: ServiceCoverage, carrier: ServiceCoverage) -> ServiceCoverage:
    """La declaracion de un ERP MAS un transportista: una fuente nueva, y lo dice.

    Publicar la del ERP a secas despues de empalmar afirmaria una llegada que el ERP no
    puede dar -- exactamente el error que todo este mecanismo existe para impedir. La
    promesa y lo servido siguen siendo del ERP porque el transportista no los conoce; la
    entrega pasa a ser del transportista porque el ERP no la tiene.
    """
    return ServiceCoverage(
        backend=f"{erp.backend} + {carrier.backend}",
        delivered_qty=erp.delivered_qty,
        promised_date=erp.promised_date,
        delivered_date=carrier.delivered_date,
    )
