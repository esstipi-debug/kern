"""El empalme de un transportista dentro de ``scan_service``: el cable que faltaba.

La capa de transportistas existe desde el PR #313 y hasta este archivo no la llamaba nadie:
``with_arrivals()`` solo aparecia en su propio test, ``scan_service()`` no tomaba
transportista, y ``_notes()`` armaba las notas desde la declaracion de cobertura sin leer
nunca ``source.notes``. Tres adaptadores correctos, testeados, y conectados a nada.

Lo que este archivo fija es la unica afirmacion comercial de todo el hilo: **el OTIF de
despacho y el de llegada son numeros distintos, y la diferencia es lo que el ERP del cliente
no puede ver**. Medido sobre 98.666 pedidos reales de Olist son 6,2 puntos. Si el cableado
se rompe, el informe vuelve a decir "OTIF" queriendo decir "salio a tiempo del deposito", y
eso no se nota mirando el numero.
"""

from __future__ import annotations

import pytest

from src.connectors import (
    COVERAGE_MEASURED,
    COVERAGE_UNAVAILABLE,
    FieldCoverage,
    Order,
    OrderLine,
    ServiceCoverage,
)
from src.connectors.arrivals import Arrival
from src.connectors.service_scan import scan_service

# -- la cartera de prueba ------------------------------------------------------
#
# Dos ordenes SERVIDAS COMPLETAS y DESPACHADAS A TIEMPO. Por despacho, OTIF = 100%.
# El transportista dira otra cosa sobre una de las dos.

_PROMETIDA = "2026-01-10"
_DESPACHO = "2026-01-09"


def _orden(order_id: str, sku: str = "SKU-1") -> Order:
    return Order(
        order_id=order_id,
        created_at="2026-01-05",
        lines=(
            OrderLine(
                sku=sku,
                quantity=10.0,
                delivered_qty=10.0,
                promised_date=_PROMETIDA,
                delivered_date=_DESPACHO,
                price=20.0,
            ),
        ),
    )


_COBERTURA_ERP = ServiceCoverage(
    backend="ERP de prueba",
    delivered_qty=FieldCoverage(COVERAGE_MEASURED, "SalesShipmentLine.ShipmentQty",
                                "las unidades efectivamente despachadas"),
    promised_date=FieldCoverage(COVERAGE_MEASURED, "SalesOrder.RequiredDate",
                                "la fecha comprometida con el cliente"),
    delivered_date=FieldCoverage(COVERAGE_MEASURED, "SalesShipment.DispatchDate",
                                 "la fecha de DESPACHO del deposito, no la de llegada"),
)

_COBERTURA_TRANSPORTISTA = ServiceCoverage(
    backend="Transportista de prueba",
    delivered_qty=FieldCoverage(COVERAGE_UNAVAILABLE, "",
                                "un transportista mueve bultos, no lineas de pedido"),
    promised_date=FieldCoverage(COVERAGE_UNAVAILABLE, "",
                                "la promesa se pacta en la venta, no en el envio"),
    delivered_date=FieldCoverage(COVERAGE_MEASURED, "tracking_events[Delivered].scan_time",
                                 "la fecha en que el envio llego al cliente"),
)


class _Erp:
    """Una fuente minima. ``notes`` se llena AL LEER, como hace Cin7 de verdad."""

    SERVICE_COVERAGE = _COBERTURA_ERP

    def __init__(self, orders, *, nota: str | None = None) -> None:
        self._orders = list(orders)
        self._nota = nota
        self.lecturas = 0
        #: Vacia hasta que alguien lea. Es el orden real: Cin7 la escribe dentro de su
        #: propia lectura (``cin7.py:423``), asi que leerla antes da siempre ``()``.
        self.notes: tuple[str, ...] = ()

    def list_products(self):
        return []

    def inventory_levels(self):
        return []

    def orders(self, *, since: str | None = None):
        self.lecturas += 1
        if self._nota:
            self.notes = (self._nota,)
        return list(self._orders)


class _Transportista:
    """Un ``ArrivalSource``. Como los tres reales, ``notes`` se llena al consultar."""

    SERVICE_COVERAGE = _COBERTURA_TRANSPORTISTA

    def __init__(self, llegadas: dict[str, str], *, nota: str | None = None) -> None:
        self._llegadas = dict(llegadas)
        self._nota = nota
        self.notes: tuple[str, ...] = ()

    def arrivals(self, *, since: str | None = None):
        if self._nota:
            self.notes = (self._nota,)
        return [Arrival(oid, cuando) for oid, cuando in self._llegadas.items()]


# -- lo que el cable tiene que producir ----------------------------------------


def test_sin_transportista_el_otif_es_de_despacho():
    """La linea base, y el regresor: sin transportista nada cambia.

    Las dos ordenes salieron el 9 con una promesa del 10. Por despacho, servicio perfecto.
    """
    scan = scan_service(_Erp([_orden("SO-1"), _orden("SO-2")]))

    assert scan.portfolio.otif == pytest.approx(1.0)
    assert scan.portfolio.fill_rate == pytest.approx(1.0)


def test_con_transportista_el_otif_pasa_a_ser_de_llegada():
    """LA AFIRMACION COMERCIAL DE TODO EL HILO.

    Las mismas dos ordenes, los mismos despachos a tiempo, y el transportista dice que
    llegaron el 15 -- cinco dias despues de lo prometido. El OTIF cae de 100% a 0%.

    Esa distancia es lo que ningun ERP del canal AU/NZ registra, y es la unica razon por la
    que existen los adaptadores de transportista. Si este test cae, el producto vuelve a
    decir "OTIF" queriendo decir "salio a tiempo del deposito".
    """
    transportista = _Transportista({"SO-1": "2026-01-15", "SO-2": "2026-01-15"})

    scan = scan_service(_Erp([_orden("SO-1"), _orden("SO-2")]), carrier=transportista)

    assert scan.portfolio.otif == pytest.approx(0.0)
    # Y el fill rate NO se toca: cuanto se sirvio lo sabe el ERP, no el transportista.
    assert scan.portfolio.fill_rate == pytest.approx(1.0)


def test_la_cobertura_publicada_dice_que_la_fecha_es_del_transportista():
    """Publicar la declaracion del ERP a secas despues de empalmar afirmaria una llegada
    que el ERP no puede dar -- el error exacto que todo el mecanismo existe para impedir."""
    scan = scan_service(
        _Erp([_orden("SO-1")]), carrier=_Transportista({"SO-1": "2026-01-15"})
    )

    assert scan.coverage is not None
    assert "ERP de prueba" in scan.coverage.backend
    assert "Transportista de prueba" in scan.coverage.backend
    # Lo servido y la promesa siguen siendo del ERP; la entrega pasa a ser del transportista.
    assert scan.coverage.delivered_qty == _COBERTURA_ERP.delivered_qty
    assert scan.coverage.promised_date == _COBERTURA_ERP.promised_date
    assert scan.coverage.delivered_date == _COBERTURA_TRANSPORTISTA.delivered_date


def test_la_nota_de_la_fecha_de_entrega_se_recalcula_sobre_la_cobertura_compuesta():
    """La nota es lo que el cliente LEE. Si se calcula antes de componer, el informe dice
    'la fecha de DESPACHO del deposito' arriba de un OTIF que ya es de llegada."""
    scan = scan_service(
        _Erp([_orden("SO-1")]), carrier=_Transportista({"SO-1": "2026-01-15"})
    )

    notas = " ".join(scan.notes)
    assert "llego al cliente" in notas
    assert "no la de llegada" not in notas, "la nota quedo con el means del ERP"


def test_una_orden_que_el_transportista_no_reporta_sale_del_otif_pero_no_del_fill_rate():
    """La degradacion honesta.

    SO-2 no la reporto nadie. Dejarle el despacho la mezclaria con las que si tienen
    llegada, y como el despacho siempre es mas temprano el OTIF de llegada saldria mejor de
    lo que es: un promedio de dos cosas distintas presentado como una.
    """
    scan = scan_service(
        _Erp([_orden("SO-1"), _orden("SO-2")]),
        carrier=_Transportista({"SO-1": "2026-01-15"}),
    )

    # Solo SO-1 se puede juzgar contra la promesa: llego el 15, tarde.
    assert scan.portfolio.lines_scored == 2, "las dos siguen contando para el fill rate"
    assert scan.portfolio.otif == pytest.approx(0.0)
    assert scan.portfolio.fill_rate == pytest.approx(1.0)


def test_la_nota_del_empalme_dice_cuanto_cubrio_el_transportista():
    """Un OTIF de llegada calculado sobre cobertura parcial NO es el de la cartera, y el
    operador tiene que leer esa fraccion sin ir a buscarla."""
    scan = scan_service(
        _Erp([_orden("SO-1"), _orden("SO-2")]),
        carrier=_Transportista({"SO-1": "2026-01-15"}),
    )

    notas = " ".join(scan.notes)
    assert "1 de 2" in notas
    assert scan.arrival_coverage == pytest.approx(0.5)


def test_arrival_coverage_es_none_sin_transportista():
    """``None`` y ``0.0`` no son lo mismo: uno es 'no se empalmo nada' y el otro es 'se
    empalmo y el transportista no conocia ninguna orden'."""
    assert scan_service(_Erp([_orden("SO-1")])).arrival_coverage is None


def test_las_notas_del_transportista_llegan_al_scan():
    """Codigo que hasta hoy estaba muerto.

    ``StarshipitArrivals.notes`` avisa cuando se toco el tope de consultas, y
    ``ShippitArrivals``/``SendleArrivals`` avisan cuando faltan llaves. Ninguna llegaba a
    ningun informe porque ``_notes()`` solo miraba la declaracion de cobertura.
    """
    scan = scan_service(
        _Erp([_orden("SO-1")]),
        carrier=_Transportista({"SO-1": "2026-01-15"}, nota="se consultaron 500 de 900"),
    )

    assert any("500 de 900" in n for n in scan.notes), scan.notes


def test_las_notas_de_la_fuente_llegan_al_scan():
    """La otra mitad del mismo codigo muerto: ``Cin7Connector.notes`` tampoco se leia."""
    scan = scan_service(_Erp([_orden("SO-1")], nota="se leyeron 200 de 4000 ventas"))

    assert any("200 de 4000" in n for n in scan.notes), scan.notes


def test_las_notas_se_leen_despues_de_la_lectura_y_no_antes():
    """La trampa de orden.

    ``notes`` de un adaptador esta vacia hasta que el adaptador LEE: Cin7 la escribe dentro
    de su propia paginacion. Un cableado que la copie al empezar se lleva ``()`` siempre, y
    el sintoma es que el aviso de truncamiento nunca aparece -- sin error y sin fallo.
    """
    fuente = _Erp([_orden("SO-1")], nota="aviso que solo existe despues de leer")
    assert fuente.notes == (), "precondicion: la nota no existe antes de leer"

    scan = scan_service(fuente)

    assert fuente.lecturas == 1, "no se puede releer la fuente para conseguir la nota"
    assert any("despues de leer" in n for n in scan.notes), scan.notes


def test_el_transportista_puede_pasarse_como_fabrica_y_recibe_los_ids_leidos():
    """Los tres adaptadores reales se construyen con las ordenes a consultar (Starshipit
    toma ``order_ids``, Shippit y Sendle un mapa), y quien llama a ``scan_service`` todavia
    no leyo la fuente. Releer para conseguir los ids es una pasada entera de mas: contra
    Cin7, 60 llamadas por minuto. La fabrica recibe los ids de la lectura que ya se hizo."""
    vistos: list[tuple[str, ...]] = []

    def fabrica(order_ids):
        vistos.append(tuple(order_ids))
        return _Transportista({"SO-1": "2026-01-15"})

    fuente = _Erp([_orden("SO-1"), _orden("SO-2")])
    scan = scan_service(fuente, carrier=fabrica)

    assert vistos == [("SO-1", "SO-2")]
    assert fuente.lecturas == 1
    assert scan.portfolio.otif == pytest.approx(0.0)


def test_una_llegada_tardia_en_cobertura_parcial_no_esconde_un_stockout():
    """Confirmado por revision adversarial contra este mismo cambio, severidad bloqueante.

    Cobertura PARCIAL: SO-1 se sirvio completa y el transportista la reporta LLEGADA
    tarde (el 15, contra una promesa del 10) -- esa fecha prueba que el reloj del mundo
    real ya paso el 15. SO-2 es un stockout TOTAL con promesa el 12, y el transportista
    nunca la reporta.

    Pinnear la ventana SOLO con hechos pre-empalme (lo que ``scan_service`` hacia hasta
    este test) la deja en el 09 -- el despacho de SO-1, unico dato que el ERP conocia.
    Con esa ventana, SO-2 no vencio todavia segun el reloj pinneado y cae en la guarda
    "en curso": un stockout total se leeria como que no paso nada.
    """
    so1 = Order("SO-1", "2026-01-01",
               (OrderLine(sku="SKU-1", quantity=10.0, price=20.0, delivered_qty=10.0,
                          promised_date="2026-01-10", delivered_date="2026-01-09"),))
    so2 = Order("SO-2", "2026-01-01",
               (OrderLine(sku="SKU-1", quantity=10.0, price=20.0, delivered_qty=0.0,
                          promised_date="2026-01-12", delivered_date=None),))

    scan = scan_service(
        _Erp([so1, so2]), carrier=_Transportista({"SO-1": "2026-01-15"})
    )

    assert scan.portfolio.fill_rate == pytest.approx(0.5), "el stockout de SO-2 tiene que contar"
    assert scan.portfolio.lost_sales_value == pytest.approx(200.0)


def test_un_transportista_sin_declaracion_no_rompe_el_empalme():
    """Se empalma igual -- la fecha es un dato, no una declaracion -- pero la cobertura no
    se puede componer, y eso se dice en vez de publicar la del ERP como si nada."""

    class _SinDeclarar:
        def arrivals(self, *, since=None):
            return [Arrival("SO-1", "2026-01-15")]

    scan = scan_service(_Erp([_orden("SO-1")]), carrier=_SinDeclarar())

    assert scan.portfolio.otif == pytest.approx(0.0)
    assert any("no declara" in n.lower() for n in scan.notes), scan.notes
