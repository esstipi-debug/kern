"""Starshipit: de donde sale la fecha de llegada, y de donde NO hay que sacarla.

Verificado contra la referencia OpenAPI oficial el 2026-08-31. La respuesta de
``GET /api/track?order_number=X`` trae DOS cosas que parecen la fecha de entrega y solo una
lo es: ``last_updated_date`` (la ultima actualizacion de tracking, de cualquier tipo) y el
``event_datetime`` del evento cuyo ``status`` es ``Delivered``. Usar la primera fecha la
entrega tarde cada vez que el transportista manda un evento posterior.
"""

from __future__ import annotations

import pytest

from src.connectors.arrivals import ArrivalSource
from src.connectors.starshipit import (
    StarshipitArrivals,
    StarshipitCredentials,
    StarshipitError,
    auth_headers,
    delivered_on,
)

_CREDS = StarshipitCredentials(api_key="the-key", subscription_key="the-sub")


class FakeHttp:
    """Devuelve una respuesta por orden consultada; guarda lo que se pidio."""

    class _Response:
        def __init__(self, payload: dict, status: int = 200) -> None:
            self._payload = payload
            self.status_code = status

        def json(self) -> dict:
            return self._payload

    def __init__(self, por_orden: dict[str, dict], status: int = 200) -> None:
        self._por_orden = por_orden
        self._status = status
        self.calls: list[dict] = []

    def get(self, path, *, params=None, headers=None):
        params = dict(params or {})
        self.calls.append(params)
        oid = params.get("order_number")
        if self._status != 200:
            return FakeHttp._Response({}, self._status)
        if oid not in self._por_orden:
            return FakeHttp._Response({}, 404)
        return FakeHttp._Response({"results": self._por_orden[oid], "success": True})


def _seguimiento(eventos: list[dict], **extra) -> dict:
    return {"order_number": "SO-1", "tracking_number": "T1", "tracking_events": eventos, **extra}


# -- auth ----------------------------------------------------------------------


def test_las_cabeceras_llevan_las_dos_claves():
    """Starshipit no firma nada: las dos claves VIAJAN en cada request, como en Cin7 y a
    diferencia de Unleashed. Aparecen en cualquier proxy que registre cabeceras."""
    headers = auth_headers(_CREDS)
    assert headers["StarShipIT-Api-Key"] == "the-key"
    assert headers["Ocp-Apim-Subscription-Key"] == "the-sub"


# -- de donde sale la fecha ----------------------------------------------------


def test_la_fecha_sale_del_evento_entregado():
    seguimiento = _seguimiento([
        {"event_datetime": "2024-04-17T12:50:07", "status": "Printed", "details": "..."},
        {"event_datetime": "2024-04-19T09:15:00", "status": "InTransit", "details": "..."},
        {"event_datetime": "2024-04-22T14:03:11", "status": "Delivered", "details": "..."},
    ])
    assert delivered_on(seguimiento) == "2024-04-22"


def test_NO_sale_de_last_updated_date_aunque_el_estado_diga_entregado():
    """La trampa que hace falta evitar, y la razon por la que esto es una funcion aparte.

    ``last_updated_date`` es la ultima actualizacion de CUALQUIER tipo. Aca el paquete se
    entrego el 22 y el transportista mando un escaneo el 30; tomar el resumen fecharia la
    entrega ocho dias tarde y convertiria un pedido cumplido en uno incumplido.
    """
    seguimiento = _seguimiento(
        [{"event_datetime": "2024-04-22T14:03:11", "status": "Delivered", "details": "..."},
         {"event_datetime": "2024-04-30T08:00:00", "status": "Exception", "details": "..."}],
        tracking_status="Delivered",
        last_updated_date="2024-04-30T08:00:00",
    )
    assert delivered_on(seguimiento) == "2024-04-22"


def test_un_segundo_evento_entregado_no_corre_la_fecha():
    """Un duplicado del transportista o un reintento. La entrega ocurrio la primera vez."""
    seguimiento = _seguimiento([
        {"event_datetime": "2024-04-22T14:03:11", "status": "Delivered", "details": "..."},
        {"event_datetime": "2024-04-23T10:00:00", "status": "Delivered", "details": "dup"},
    ])
    assert delivered_on(seguimiento) == "2024-04-22"


def test_sin_evento_de_entrega_no_hay_fecha():
    """Sigue en camino, o el transportista nunca reporto. Las dos cosas significan lo mismo
    para una medicion, y ninguna significa "llego hoy"."""
    seguimiento = _seguimiento([
        {"event_datetime": "2024-04-19T09:15:00", "status": "OutForDelivery", "details": "..."},
    ])
    assert delivered_on(seguimiento) is None


def test_un_seguimiento_sin_eventos_no_revienta():
    assert delivered_on({"order_number": "SO-1"}) is None
    assert delivered_on({}) is None


# -- la lectura ----------------------------------------------------------------


def test_satisface_el_protocolo_arrival_source():
    fuente = StarshipitArrivals(FakeHttp({}), _CREDS, [])
    assert isinstance(fuente, ArrivalSource)


def test_consulta_una_orden_por_vez_porque_la_api_no_tiene_lectura_masiva():
    entregado = [{"event_datetime": "2024-04-22T14:03:11", "status": "Delivered", "details": ""}]
    http = FakeHttp({"SO-1": _seguimiento(entregado), "SO-2": _seguimiento(entregado)})

    llegadas = StarshipitArrivals(http, _CREDS, ["SO-1", "SO-2"]).arrivals()

    assert [p["order_number"] for p in http.calls] == ["SO-1", "SO-2"]
    assert {a.order_id: a.arrived_on for a in llegadas} == {
        "SO-1": "2024-04-22", "SO-2": "2024-04-22"
    }


def test_una_orden_que_starshipit_no_conoce_se_omite_y_no_rompe():
    """Ese pedido pudo haberse enviado por fuera de la plataforma. Es una respuesta, no
    una falla."""
    entregado = [{"event_datetime": "2024-04-22T14:03:11", "status": "Delivered", "details": ""}]
    http = FakeHttp({"SO-1": _seguimiento(entregado)})

    llegadas = StarshipitArrivals(http, _CREDS, ["SO-1", "SO-DESCONOCIDA"]).arrivals()

    assert [a.order_id for a in llegadas] == ["SO-1"]


def test_un_error_de_verdad_levanta_en_vez_de_decir_que_no_llego():
    """La distincion que decide si una linea puntua OTIF o sale del denominador. Un 500
    tratado como "no llego" saca pedidos de la medicion por un problema de red."""
    http = FakeHttp({"SO-1": {}}, status=500)
    with pytest.raises(StarshipitError, match="500"):
        StarshipitArrivals(http, _CREDS, ["SO-1"]).arrivals()


def test_el_tope_de_ordenes_corta_y_lo_dice_en_las_notas():
    """A una llamada por orden, este adaptador VA a toparse en una cuenta real. Un corte
    silencioso se lee como cobertura de la cartera entera."""
    entregado = [{"event_datetime": "2024-04-22T14:03:11", "status": "Delivered", "details": ""}]
    http = FakeHttp({f"SO-{i}": _seguimiento(entregado) for i in range(5)})

    fuente = StarshipitArrivals(http, _CREDS, [f"SO-{i}" for i in range(5)], max_orders=2)
    fuente.arrivals()

    assert len(http.calls) == 2
    assert any("tope" in n.lower() for n in fuente.notes)


# -- lo que un transportista NO puede decir ------------------------------------


def test_declara_UNAVAILABLE_lo_que_un_transportista_no_sabe():
    """La primera fuente del repo que declara campos ausentes, y no por pobreza del
    adaptador: un transportista mueve bultos, no lineas de pedido. Esa declaracion es la
    que hace legible por que hay que empalmarlo con un ERP en vez de usarlo solo."""
    from src.connectors import COVERAGE_MEASURED, COVERAGE_UNAVAILABLE
    from src.connectors.starshipit import SERVICE_COVERAGE

    assert SERVICE_COVERAGE.delivered_qty.status == COVERAGE_UNAVAILABLE
    assert SERVICE_COVERAGE.promised_date.status == COVERAGE_UNAVAILABLE
    assert SERVICE_COVERAGE.delivered_date.status == COVERAGE_MEASURED
    # Y la unica que si mide es una LLEGADA, no una salida.
    assert "llego al cliente" in SERVICE_COVERAGE.delivered_date.means
