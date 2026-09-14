"""Shippit -- el segundo transportista del canal AU/NZ.

Lo que este archivo fija que el guard de cobertura no puede fijar: las trampas propias de
ESTA API. El guard verifica que el adaptador declare sus tres campos y que el que dice
medir efectivamente llegue; no puede saber que ``expected_delivery_date`` es una promesa
disfrazada de entrega, ni que el array ``track`` vive dentro de un envoltorio.
"""

from __future__ import annotations

import pytest

from src.connectors.shippit import (
    ShippitArrivals,
    ShippitCredentials,
    ShippitError,
    delivered_on,
)


def _cuerpo(track):
    """El envoltorio real de Shippit. La respuesta NO trae ``track`` en la raiz."""
    return {"response": {"tracking_number": "PP39TBECV7QSSPK", "success": True, "track": track}}


def test_la_llegada_sale_del_evento_completed():
    cuerpo = _cuerpo([
        {"status": "Order Placed", "date": "2024-04-18T10:00:00+10:00"},
        {"status": "In Transit", "date": "2024-04-20T08:00:00+10:00"},
        {"status": "Completed", "date": "2024-04-22T14:03:11+10:00"},
    ])
    assert delivered_on(cuerpo) == "2024-04-22"


def test_un_evento_posterior_no_corre_la_fecha_de_entrega():
    """La entrega ocurrio la primera vez. Un segundo evento es un duplicado del
    transportista o un reintento, y fecharla tarde inventa un incumplimiento."""
    cuerpo = _cuerpo([
        {"status": "Completed", "date": "2024-04-22T14:03:11+10:00"},
        {"status": "Completed", "date": "2024-04-30T08:00:00+10:00"},
    ])
    assert delivered_on(cuerpo) == "2024-04-22"


def test_el_orden_del_array_no_importa():
    """Shippit publica el ejemplo con el evento mas nuevo primero. Tomar ``track[0]`` o
    ``track[-1]`` es apostar a un orden que la doc no garantiza."""
    cuerpo = _cuerpo([
        {"status": "Completed", "date": "2024-04-22T14:03:11+10:00"},
        {"status": "In Transit", "date": "2024-04-20T08:00:00+10:00"},
    ])
    assert delivered_on(cuerpo) == "2024-04-22"


def test_el_estado_se_compara_sin_distinguir_mayusculas():
    """El endpoint de tracking devuelve ``"Completed"`` y el webhook ``"completed"``. Es el
    mismo evento y una comparacion exacta perderia la mitad de las entregas."""
    assert delivered_on(_cuerpo([{"status": "completed", "date": "2024-04-22T14:03:11Z"}]))


def test_expected_delivery_date_no_es_la_llegada():
    """LA TRAMPA DE SHIPPIT.

    ``expected_delivery_date`` es la ESTIMACION de Shippit, no la entrega. Usarlo como
    fecha de llegada daria un OTIF casi perfecto por construccion -- la estimacion tiende a
    cumplir la estimacion -- y el numero se leeria como una medicion.
    """
    cuerpo = _cuerpo([{"status": "Completed", "date": "2024-04-22T14:03:11+10:00"}])
    cuerpo["response"]["expected_delivery_date"] = "2024-04-10T09:00:00+10:00"

    assert delivered_on(cuerpo) == "2024-04-22"


def test_sin_evento_completed_no_hay_fecha():
    assert delivered_on(_cuerpo([{"status": "In Transit", "date": "2024-04-20T08:00:00Z"}])) is None


def test_el_array_track_no_esta_en_la_raiz():
    """El envoltorio ``response`` es la convencion de Shippit. Leer ``track`` de la raiz
    devolveria ``None`` siempre, y eso NO se lee como un bug: se lee como 'no llego'."""
    plano = {"track": [{"status": "Completed", "date": "2024-04-22T14:03:11Z"}]}
    assert delivered_on(plano) is None


class _Http:
    """Doble de httpx que registra QUE llave se consulto. Es lo que prueba el empalme."""

    class _R:
        def __init__(self, payload, status=200):
            self._payload, self.status_code = payload, status

        def json(self):
            return self._payload

    def __init__(self, por_llave: dict, status: int = 200):
        self.por_llave, self._status = por_llave, status
        self.consultadas: list[str] = []

    def get(self, path, *, params=None, headers=None):
        llave = path.rsplit("/", 2)[-2]
        self.consultadas.append(llave)
        if llave not in self.por_llave:
            return _Http._R({}, 404)
        return _Http._R(self.por_llave[llave], self._status)


_CREDS = ShippitCredentials(api_key="secreta")


def _entregado():
    return _cuerpo([{"status": "Completed", "date": "2024-04-22T14:03:11Z"}])


def test_se_consulta_por_la_llave_de_shippit_y_se_devuelve_la_del_erp():
    """La decision de diseño entera en un test.

    Entra el ``tracking_number`` de Shippit, sale el ``OrderNumber`` del ERP. Devolver la
    llave del transportista haria que ``with_arrivals`` no empalme con nada y que el OTIF
    de llegada saliera vacio -- sin error, sin nota, sin nada.
    """
    http = _Http({"PPxyz": _entregado()})
    fuente = ShippitArrivals(http, _CREDS, {"SO-1": "PPxyz"})

    llegadas = fuente.arrivals()

    assert http.consultadas == ["PPxyz"]
    assert [(a.order_id, a.arrived_on) for a in llegadas] == [("SO-1", "2024-04-22")]


def test_una_orden_sin_llave_se_declara_y_no_se_calla():
    """Dos silencios que se ven identicos.

    Sin esta nota, una orden que NUNCA se consulto sale del denominador de OTIF igual que
    una que el transportista dio por no entregada. El numero resultante seria el OTIF de
    'las ordenes que casualmente tenian tracking cargado', presentado como el de la cartera.
    """
    http = _Http({"PPxyz": _entregado()})
    fuente = ShippitArrivals(http, _CREDS, {"SO-1": "PPxyz"}, order_ids=["SO-1", "SO-2", "SO-3"])

    fuente.arrivals()

    assert http.consultadas == ["PPxyz"]
    assert any("2 de 3" in n for n in fuente.notes), fuente.notes


def test_sin_order_ids_no_se_inventa_una_nota():
    """Si solo te dieron un mapa, solo hablas del mapa. Una nota sobre ordenes que el
    llamador nunca menciono seria ruido presentado como cobertura."""
    fuente = ShippitArrivals(_Http({"PPxyz": _entregado()}), _CREDS, {"SO-1": "PPxyz"})

    fuente.arrivals()

    assert fuente.notes == ()


def test_un_404_es_una_respuesta_no_una_falla():
    """Shippit no conoce esa referencia: el pedido pudo haber salido por fuera de la
    plataforma. Es un dato ausente, no un error."""
    fuente = ShippitArrivals(_Http({}), _CREDS, {"SO-1": "PPxyz"})
    assert fuente.arrivals() == []


def test_un_error_de_red_no_se_degrada_a_no_llego():
    """Degradar un 500 a 'sin llegada' saca la linea del denominador de OTIF por un
    problema de red y no por falta de dato. El numero sale MEJOR y nadie se entera."""
    fuente = ShippitArrivals(_Http({"PPxyz": {}}, status=500), _CREDS, {"SO-1": "PPxyz"})

    with pytest.raises(ShippitError):
        fuente.arrivals()


def test_la_credencial_viaja_en_la_cabecera_y_no_en_la_url():
    from src.connectors.shippit import auth_headers

    assert auth_headers(_CREDS) == {"Authorization": "Bearer secreta"}
