"""Sendle -- el tercer transportista del canal AU/NZ.

La trampa de Sendle no se parece a las otras dos. Starshipit y Shippit publican un campo de
fecha que NO es la entrega (``last_updated_date``, ``expected_delivery_date``); Sendle
publica un campo que dice correctamente que llego -- ``state == "Delivered"`` -- y no trae
marca de tiempo. Un estado no es una fecha, y la tentacion de completarla es mayor
justamente porque el estado no miente.
"""

from __future__ import annotations

import pytest

from src.connectors.sendle import (
    SendleArrivals,
    SendleCredentials,
    SendleError,
    delivered_on,
)


def test_la_llegada_sale_del_evento_delivered():
    payload = {
        "state": "Delivered",
        "tracking_events": [
            {"event_type": "Pickup", "scan_time": "2024-04-18T10:00:00+10:00"},
            {"event_type": "In Transit", "scan_time": "2024-04-20T08:00:00+10:00"},
            {"event_type": "Delivered", "scan_time": "2024-04-22T14:03:11+10:00"},
        ],
    }
    assert delivered_on(payload) == "2024-04-22"


def test_un_estado_entregado_sin_evento_no_da_fecha():
    """LA TRAMPA DE SENDLE.

    ``state`` es un estado terminal y correcto, y NO trae marca de tiempo. Completarla con
    cualquier otra cosa -- la fecha de hoy, el ultimo evento, la de despacho -- mete un
    numero inventado al OTIF, donde nadie lo vuelve a mirar. Antes 'no se cuando llego'.
    """
    assert delivered_on({"state": "Delivered", "tracking_events": []}) is None


def test_un_evento_posterior_no_corre_la_fecha_de_entrega():
    """La entrega ocurrio la primera vez. Un segundo evento es duplicado o reintento."""
    payload = {
        "state": "Delivered",
        "tracking_events": [
            {"event_type": "Delivered", "scan_time": "2024-04-22T14:03:11+10:00"},
            {"event_type": "Delivered", "scan_time": "2024-04-30T08:00:00+10:00"},
        ],
    }
    assert delivered_on(payload) == "2024-04-22"


def test_un_intento_de_retiro_no_es_una_entrega():
    """``Pickup Attempted`` es un evento real de Sendle. Comparar por subcadena o por
    prefijo lo confundiria con algo; se compara el ``event_type`` entero."""
    payload = {"tracking_events": [
        {"event_type": "Pickup Attempted", "scan_time": "2024-04-18T10:00:00Z"},
    ]}
    assert delivered_on(payload) is None


def test_sin_eventos_no_hay_fecha():
    assert delivered_on({}) is None


class _Http:
    """Doble de httpx que registra QUE referencia se consulto."""

    class _R:
        def __init__(self, payload, status=200):
            self._payload, self.status_code = payload, status

        def json(self):
            return self._payload

    def __init__(self, por_llave: dict, status: int = 200):
        self.por_llave, self._status = por_llave, status
        self.consultadas: list[str] = []

    def get(self, path, *, params=None, headers=None):
        llave = path.rsplit("/", 1)[-1]
        self.consultadas.append(llave)
        if llave not in self.por_llave:
            return _Http._R({}, 404)
        return _Http._R(self.por_llave[llave], self._status)


_CREDS = SendleCredentials(sendle_id="mi-id", api_key="secreta")
_ENTREGADO = {
    "state": "Delivered",
    "tracking_events": [{"event_type": "Delivered", "scan_time": "2024-04-22T14:03:11Z"}],
}


def test_se_consulta_por_la_referencia_de_sendle_y_se_devuelve_la_del_erp():
    """Entra el ``sendle_reference``, sale el ``OrderNumber`` del ERP. Devolver la llave
    del transportista haria que ``with_arrivals`` no empalme con nada."""
    http = _Http({"S3ND1E": _ENTREGADO})
    fuente = SendleArrivals(http, _CREDS, {"SO-1": "S3ND1E"})

    llegadas = fuente.arrivals()

    assert http.consultadas == ["S3ND1E"]
    assert [(a.order_id, a.arrived_on) for a in llegadas] == [("SO-1", "2024-04-22")]


def test_una_orden_sin_referencia_se_declara_y_no_se_calla():
    """El ``customer_reference`` del comercio NO sirve para consultar tracking: hace falta
    el ``sendle_reference``. Lo que no este mapeado nunca se consulta, y eso se dice."""
    http = _Http({"S3ND1E": _ENTREGADO})
    fuente = SendleArrivals(http, _CREDS, {"SO-1": "S3ND1E"}, order_ids=["SO-1", "SO-2", "SO-3"])

    fuente.arrivals()

    assert http.consultadas == ["S3ND1E"]
    assert any("2 de 3" in n for n in fuente.notes), fuente.notes


def test_sin_order_ids_no_se_inventa_una_nota():
    fuente = SendleArrivals(_Http({"S3ND1E": _ENTREGADO}), _CREDS, {"SO-1": "S3ND1E"})

    fuente.arrivals()

    assert fuente.notes == ()


def test_un_404_es_una_respuesta_no_una_falla():
    fuente = SendleArrivals(_Http({}), _CREDS, {"SO-1": "S3ND1E"})
    assert fuente.arrivals() == []


def test_un_error_de_red_no_se_degrada_a_no_llego():
    """Degradar un 500 a 'sin llegada' saca la linea del denominador de OTIF por un
    problema de red. El numero sale MEJOR y nadie se entera."""
    fuente = SendleArrivals(_Http({"S3ND1E": {}}, status=500), _CREDS, {"SO-1": "S3ND1E"})

    with pytest.raises(SendleError):
        fuente.arrivals()


def test_la_credencial_va_en_basic_auth_y_no_en_la_url():
    """Sendle usa HTTP Basic: Sendle ID de usuario, API key de password."""
    import base64

    from src.connectors.sendle import auth_headers

    cabeceras = auth_headers(_CREDS)
    esperado = base64.b64encode(b"mi-id:secreta").decode()

    assert cabeceras["Authorization"] == f"Basic {esperado}"
    assert cabeceras["Accept"] == "application/json"


def test_el_sandbox_es_una_base_distinta():
    """Sendle COBRA: usar la API de produccion crea envios reales y carga la tarjeta. La
    base de sandbox existe para que probar no cueste plata, y tiene que estar a mano."""
    from src.connectors.sendle import API_BASE, SANDBOX_BASE

    assert SANDBOX_BASE != API_BASE
    assert "sandbox" in SANDBOX_BASE
