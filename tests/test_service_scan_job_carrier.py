"""La tool #51 con un transportista enchufado: que el OTIF de llegada llegue al cliente.

``scan_service`` ya sabe empalmar. Lo que fija este archivo es el ultimo tramo, que es donde
un cableado correcto se puede volver un informe que miente:

* el informe tiene que NOMBRAR al transportista (si no, dice "Unleashed" arriba de una fecha
  que Unleashed no tiene);
* el QA tiene que frenar un OTIF sin evidencia de puntualidad, que NO es lo mismo que un
  OTIF sin lineas puntuadas;
* y el paso humano tiene que cambiar: sin transportista hay que preguntarle al cliente que
  significa su fecha de entrega, y con transportista esa pregunta ya esta contestada.
"""

from __future__ import annotations

import pytest

from jobs import service_scan_job as job
from src.connectors import (
    COVERAGE_MEASURED,
    COVERAGE_UNAVAILABLE,
    FieldCoverage,
    Order,
    OrderLine,
    ServiceCoverage,
)
from src.connectors.arrivals import Arrival

_PROMETIDA = "2026-01-10"
_DESPACHO = "2026-01-09"


def _orden(order_id: str) -> Order:
    return Order(
        order_id=order_id,
        created_at="2026-01-05",
        lines=(OrderLine(sku="SKU-1", quantity=10.0, price=20.0, delivered_qty=10.0,
                         promised_date=_PROMETIDA, delivered_date=_DESPACHO),),
    )


_COBERTURA_ERP = ServiceCoverage(
    backend="ERP de prueba",
    delivered_qty=FieldCoverage(COVERAGE_MEASURED, "ShipmentQty", "unidades despachadas"),
    promised_date=FieldCoverage(COVERAGE_MEASURED, "RequiredDate", "la fecha comprometida"),
    delivered_date=FieldCoverage(COVERAGE_MEASURED, "DispatchDate", "el DESPACHO"),
)
_COBERTURA_CARRIER = ServiceCoverage(
    backend="Transportista de prueba",
    delivered_qty=FieldCoverage(COVERAGE_UNAVAILABLE, "", "mueve bultos"),
    promised_date=FieldCoverage(COVERAGE_UNAVAILABLE, "", "no conoce la promesa"),
    delivered_date=FieldCoverage(COVERAGE_MEASURED, "tracking", "llego al cliente"),
)


class _Erp:
    SERVICE_COVERAGE = _COBERTURA_ERP

    def __init__(self, orders):
        self._orders = list(orders)

    def list_products(self):
        return []

    def inventory_levels(self):
        return []

    def orders(self, *, since=None):
        return list(self._orders)


class _Carrier:
    SERVICE_COVERAGE = _COBERTURA_CARRIER

    def __init__(self, llegadas):
        self._llegadas = dict(llegadas)
        self.notes: tuple[str, ...] = ()
        self.ids_recibidos: tuple[str, ...] | None = None

    def arrivals(self, *, since=None):
        return [Arrival(o, c) for o, c in self._llegadas.items()]


def _correr(*, orders, carrier=None):
    params = {"source": _Erp(orders)}
    if carrier is not None:
        params["carrier"] = carrier
    payload = job.prepare(None, params)
    return job.run(payload)


# -- el transportista se nombra ------------------------------------------------


def test_sin_transportista_el_informe_nombra_solo_al_backend():
    reporte = _correr(orders=[_orden("SO-1")])

    assert "+" not in reporte.source_name
    assert reporte.arrival_coverage is None


def test_con_transportista_el_informe_lo_nombra():
    """El error que ``coverage_of_arrivals`` existe para impedir, en su ultima forma:
    componer bien la declaracion y despues publicar el informe bajo el nombre del ERP.
    El cliente leeria "Unleashed" en la fila de fuentes arriba de una fecha de llegada que
    Unleashed no registra."""
    reporte = _correr(
        orders=[_orden("SO-1")], carrier=_Carrier({"SO-1": "2026-01-15"})
    )

    assert "Transportista" in reporte.source_name or "transportista" in reporte.source_name
    assert reporte.arrival_coverage == pytest.approx(1.0)


def test_el_otif_del_reporte_es_el_de_llegada():
    """Despachado el 9 contra una promesa del 10 -- puntual. Llegado el 15 -- tarde."""
    sin = _correr(orders=[_orden("SO-1")])
    con = _correr(orders=[_orden("SO-1")], carrier=_Carrier({"SO-1": "2026-01-15"}))

    assert sin.portfolio.otif == pytest.approx(1.0)
    assert con.portfolio.otif == pytest.approx(0.0)


# -- el QA ---------------------------------------------------------------------


def test_el_qa_frena_un_otif_sin_evidencia_de_puntualidad():
    """``lines_scored`` NO es el denominador del OTIF.

    Una cartera puede tener cien lineas servidas y cero con fecha de llegada: el fill rate
    descansa en cien y el OTIF en ninguna. El unico gate que existia miraba ``lines_scored``
    y hubiera dejado pasar un OTIF construido sobre nada.
    """
    from src import order_fulfillment as of

    reporte = _correr(orders=[_orden("SO-1")])
    roto = type(reporte)(
        source_name=reporte.source_name,
        records=reporte.records,
        portfolio=of.RealizedService(
            product_id="portfolio", fill_rate=0.9, otif=0.99, days_out_of_stock=None,
            lost_sales_units=None, lost_sales_value=None,
            provenance="observed", method="test",
            lines_scored=100, lines_otif_scored=0,
        ),
    )

    problemas = job.verify(roto)

    assert problemas, "un OTIF sobre cero lineas puntuadas no se entrega"
    assert any("puntualidad" in p.lower() or "otif" in p.lower() for p in problemas)


def test_el_qa_frena_un_empalme_con_cobertura_cero():
    """Se enchufo un transportista y no conocia ni una orden. El informe diria "OTIF de
    llegada" sobre cero llegadas conocidas: no es un OTIF parcial, es ninguno."""
    reporte = _correr(orders=[_orden("SO-1")], carrier=_Carrier({}))

    assert reporte.arrival_coverage == pytest.approx(0.0)
    assert job.verify(reporte), "cobertura de llegada cero no se entrega como OTIF de llegada"


def test_un_scan_normal_sigue_pasando_el_qa():
    """El regresor del gate: lo que hoy pasa tiene que seguir pasando."""
    assert job.verify(_correr(orders=[_orden("SO-1")])) == []
    assert job.verify(
        _correr(orders=[_orden("SO-1")], carrier=_Carrier({"SO-1": "2026-01-15"}))
    ) == []


# -- el paso humano ------------------------------------------------------------


def test_sin_transportista_el_paso_humano_pregunta_que_significa_la_fecha():
    reporte = _correr(orders=[_orden("SO-1")])

    pasos = " ".join(p for pk in reporte.outcome.handoffs for p in pk.steps)
    assert "fecha de entrega" in pasos


def test_con_transportista_esa_pregunta_ya_esta_contestada():
    """Pedirle al cliente que confirme que significa su fecha de entrega, cuando el informe
    ya la tomo del transportista, gasta el unico paso humano que va a leer en una pregunta
    resuelta. El residuo con transportista es otro: cuanto del libro quedo sin empalmar."""
    reporte = _correr(
        orders=[_orden("SO-1"), _orden("SO-2")], carrier=_Carrier({"SO-1": "2026-01-15"})
    )

    pasos = " ".join(p for pk in reporte.outcome.handoffs for p in pk.steps)
    assert "cobertura" in pasos.lower() or "empalm" in pasos.lower()


# -- la resolucion por credenciales --------------------------------------------


def test_sin_credenciales_no_se_arma_ningun_transportista():
    """El camino offline tiene que quedar identico: el smoke harness fija esta tool en PASS
    sin credenciales, y un transportista resuelto por defecto moveria ese bucket."""
    import os

    for var in ("STARSHIPIT_API_KEY", "STARSHIPIT_SUBSCRIPTION_KEY"):
        assert var not in os.environ or not os.environ[var]

    fabrica, nombre = job._resolve_carrier({})

    assert fabrica is None
    assert nombre is None


def test_con_credenciales_de_starshipit_se_arma_una_fabrica_que_recibe_los_ids(monkeypatch):
    """Los tres adaptadores de transportista se construyen sabiendo que ordenes consultar, y
    ``prepare`` no leyo la fuente todavia -- a proposito, porque contra Cin7 cada lectura
    cuesta una pasada a 60 llamadas por minuto. Por eso se resuelve una FABRICA."""
    monkeypatch.setenv("STARSHIPIT_API_KEY", "k")
    monkeypatch.setenv("STARSHIPIT_SUBSCRIPTION_KEY", "s")

    fabrica, nombre = job._resolve_carrier({"carrier_http": object()})

    assert callable(fabrica)
    assert "Starshipit" in nombre
    fuente = fabrica(("SO-1", "SO-2"))
    assert hasattr(fuente, "arrivals")


def test_la_credencial_del_transportista_no_viaja_al_nombre(monkeypatch):
    """Misma invariante que el resolvedor de ERP: el nombre identifica al SISTEMA, nunca a
    la cuenta. Meter la clave "para saber cual es" la imprime en el informe del cliente."""
    monkeypatch.setenv("STARSHIPIT_API_KEY", "clave-secreta")
    monkeypatch.setenv("STARSHIPIT_SUBSCRIPTION_KEY", "otra-secreta")

    _, nombre = job._resolve_carrier({"carrier_http": object()})

    assert "clave-secreta" not in nombre
    assert "otra-secreta" not in nombre
