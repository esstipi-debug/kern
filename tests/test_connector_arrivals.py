"""La fecha de llegada, que no vive en el ERP y hay que ir a buscarla a otro lado.

Los tres backends del canal -- Unleashed, Cin7 Core, Odoo -- registran cuando la mercaderia
SALIO. Ninguno registra cuando llego. Medido sobre 98.666 pedidos reales en
``examples/run_service_report.py``, la diferencia entre las dos lecturas es de 6,2 puntos de
OTIF: pedidos que salieron del deposito a tiempo y llegaron tarde, y que el sistema del
cliente no puede ver.

El dato existe, pero en el transportista. De ahi este modulo: un ERP y un transportista
JUNTOS pueden medir el OTIF de llegada, y ninguno de los dos solo. El ERP sabe que se
pidio, cuanto y para cuando; el transportista sabe cuando llego.

Lo que se fija aca es el empalme, y sobre todo lo que pasa cuando el empalme NO encuentra
nada -- que en una cuenta real es la mayoria de los pedidos viejos.
"""

from __future__ import annotations

import pytest

from src.connectors import Order, OrderLine, realized_service
from src.connectors.arrivals import Arrival, ArrivalSource, coverage_of_arrivals, with_arrivals


class _Transportista:
    """Un ArrivalSource de mentira: sabe la llegada de las ordenes que le tocaron."""

    def __init__(self, llegadas: dict[str, str]) -> None:
        self._llegadas = dict(llegadas)

    def arrivals(self, *, since: str | None = None) -> list[Arrival]:
        return [Arrival(oid, fecha) for oid, fecha in sorted(self._llegadas.items())]


def _orden(oid: str, creada: str, *, prometida: str, salida: str | None, servidas=1.0) -> Order:
    return Order(
        oid, creada,
        (OrderLine("SKU-1", 1.0, 100.0, servidas, prometida, salida),),
    )


def test_el_protocolo_se_satisface_estructuralmente():
    assert isinstance(_Transportista({}), ArrivalSource)


def test_la_llegada_reemplaza_a_la_salida_en_la_linea():
    """El unico cambio que hace este modulo, y es todo el punto: la misma orden deja de
    decir "salio el 5" y pasa a decir "llego el 12"."""
    pedidos = [_orden("SO-1", "2026-01-01", prometida="2026-01-10", salida="2026-01-05")]

    unidos = with_arrivals(pedidos, _Transportista({"SO-1": "2026-01-12"}))

    assert unidos.orders[0].lines[0].delivered_date == "2026-01-12"
    # Lo pedido, lo servido y la promesa NO se tocan: el transportista no sabe nada de eso.
    assert unidos.orders[0].lines[0].delivered_qty == 1.0
    assert unidos.orders[0].lines[0].promised_date == "2026-01-10"


def test_una_orden_sin_llegada_conocida_pierde_la_fecha_en_vez_de_quedarse_con_la_salida():
    """El caso que decide si esto sirve o miente.

    Si una orden no aparece en el transportista, dejarle la fecha de DESPACHO la mezcla con
    las que si tienen llegada: el informe diria "OTIF de llegada" sobre un promedio de dos
    cosas distintas, y saldria mejor de lo que es -- porque el despacho siempre es mas
    temprano. Se vacia y la linea sale del denominador de OTIF, que es lo honesto: de esa
    orden no sabemos cuando llego.
    """
    pedidos = [
        _orden("SO-1", "2026-01-01", prometida="2026-01-10", salida="2026-01-05"),
        _orden("SO-2", "2026-01-02", prometida="2026-01-10", salida="2026-01-05"),
    ]

    unidos = with_arrivals(pedidos, _Transportista({"SO-1": "2026-01-12"}))

    por_id = {o.order_id: o for o in unidos.orders}
    assert por_id["SO-1"].lines[0].delivered_date == "2026-01-12"
    assert por_id["SO-2"].lines[0].delivered_date is None


def test_se_reporta_cuantas_ordenes_quedaron_sin_empalmar():
    """Una cobertura parcial silenciosa es un OTIF calculado sobre la mitad de la cartera
    que se lee como si fuera de toda. El numero tiene que viajar con el informe."""
    pedidos = [
        _orden("SO-1", "2026-01-01", prometida="2026-01-10", salida="2026-01-05"),
        _orden("SO-2", "2026-01-02", prometida="2026-01-10", salida="2026-01-05"),
        _orden("SO-3", "2026-01-03", prometida="2026-01-10", salida="2026-01-05"),
    ]

    unidos = with_arrivals(pedidos, _Transportista({"SO-1": "2026-01-12"}))

    assert unidos.matched == 1
    assert unidos.unmatched == 2
    assert unidos.coverage == pytest.approx(1 / 3)
    assert "2 de 3" in unidos.note


def test_una_llegada_de_una_orden_que_el_erp_no_tiene_no_inventa_una_orden():
    """El transportista puede llevar envios que no son ventas -- muestras, devoluciones,
    transferencias. Ninguno de esos es un pedido de cliente."""
    pedidos = [_orden("SO-1", "2026-01-01", prometida="2026-01-10", salida="2026-01-05")]

    unidos = with_arrivals(pedidos, _Transportista({"SO-1": "2026-01-12", "DEVOL-9": "2026-01-13"}))

    assert [o.order_id for o in unidos.orders] == ["SO-1"]
    assert unidos.matched == 1


def test_una_linea_sin_cantidad_servida_no_recibe_fecha_de_llegada():
    """Una orden que el ERP todavia no dio por servida no "llego", aunque el transportista
    tenga un evento suelto. Sin cantidad no hay entrega que fechar."""
    pedidos = [_orden("SO-1", "2026-01-01", prometida="2026-01-10", salida=None, servidas=None)]

    unidos = with_arrivals(pedidos, _Transportista({"SO-1": "2026-01-12"}))

    assert unidos.orders[0].lines[0].delivered_date is None


def test_la_cadena_entera_cambia_el_otif_de_cumplido_a_incumplido():
    """La prueba de que esto sirve: la MISMA orden, mismo pedido, misma promesa.

    Con la fecha del ERP salio el 5 contra una promesa del 10 -> a tiempo.
    Con la del transportista llego el 12 -> tarde. El servicio no cambio; cambio lo que se
    podia ver de el.
    """
    pedidos = [_orden("SO-1", "2026-01-01", prometida="2026-01-10", salida="2026-01-05")]
    cierre = "2026-02-01"

    (erp,) = realized_service(pedidos, as_of=cierre)
    (real,) = realized_service(with_arrivals(pedidos, _Transportista({"SO-1": "2026-01-12"})).orders,
                               as_of=cierre)

    assert erp.otif == 1.0
    assert real.otif == 0.0


def test_la_cobertura_compuesta_dice_de_donde_sale_cada_campo():
    """Un ERP mas un transportista es una fuente nueva, y su declaracion tiene que serlo
    tambien: la promesa sigue siendo del ERP y la entrega pasa a ser del transportista.
    Publicar la del ERP a secas afirmaria una llegada que el ERP no puede dar."""
    from src.connectors.starshipit import SERVICE_COVERAGE as SSI
    from src.connectors.unleashed import SERVICE_COVERAGE as UNL

    compuesta = coverage_of_arrivals(UNL, SSI)

    assert "Unleashed" in compuesta.backend and "Starshipit" in compuesta.backend
    # La promesa y lo servido siguen viniendo del ERP.
    assert compuesta.promised_date == UNL.promised_date
    assert compuesta.delivered_qty == UNL.delivered_qty
    # La entrega ya no es un despacho.
    assert compuesta.delivered_date == SSI.delivered_date
    assert "llego" in compuesta.delivered_date.means.lower()
