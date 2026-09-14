"""Sendle -- el tercer transportista del canal AU/NZ que puede fechar una llegada.

Mismo trabajo que ``starshipit.py`` y ``shippit.py``, y la misma declaracion: un
transportista sabe CUANDO llego un envio y nada mas.

Verificado contra la referencia oficial el 2026-09-01
(``https://developers.sendle.com/reference/trackparcel`` y ``.../authentication``):

    GET /api/tracking/{sendle_reference}
    ->  {"state": "Delivered",
         "tracking_events": [{"event_type", "scan_time", "description"}],
         "origin": {...}, "destination": {...}}

Base ``https://api.sendle.com``; sandbox ``https://sandbox.sendle.com``. Autenticacion
**HTTP Basic**: Sendle ID de usuario, API key de password. La credencial viaja en cada
request, como en Shippit y Cin7.

Y una advertencia que la doc de Sendle pone en negrita y este modulo repite: la API de
PRODUCCION crea envios reales y carga la tarjeta. Por eso ``SANDBOX_BASE`` esta a mano.

CUATRO DECISIONES.

1. **Se consulta por el ``sendle_reference`` y se devuelve la referencia del ERP.** El path
   toma el identificador que Sendle asigna al crear la orden, no el ``customer_reference``
   del comercio. Igual que en Shippit, el adaptador recibe un MAPA
   ``order_id -> sendle_reference`` y emite ``Arrival`` con la del ERP: devolver la del
   transportista haria que ``with_arrivals`` no empalme con nada, sin error y sin nota.

2. **Una orden sin referencia se declara.** ``with_arrivals`` saca del denominador de OTIF a
   toda orden sin llegada, y una orden que nunca se consulto produce el mismo silencio que
   una que el transportista dio por no entregada. ``order_ids`` es opcional y existe para
   contar la diferencia y decirla.

3. **``state`` NO se lee. La fecha sale del evento.** Esta es la trampa propia de Sendle y no
   se parece a las otras dos: Starshipit y Shippit publican un campo de fecha que no es la
   entrega, y Sendle publica un campo que dice CORRECTAMENTE que llego -- ``state ==
   "Delivered"`` -- y no trae marca de tiempo. Un estado no es una fecha. Si no hay evento
   ``Delivered`` con ``scan_time``, este adaptador devuelve ``None``: completar la fecha con
   cualquier otra cosa mete un numero inventado al OTIF, donde nadie lo vuelve a mirar.

4. **El PRIMER evento ``Delivered``.** Un segundo es duplicado o reintento; la entrega
   ocurrio la primera vez. Y el ``event_type`` se compara ENTERO: ``"Pickup Attempted"`` es
   un evento real de Sendle y una comparacion por subcadena lo confundiria con otra cosa.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from src.connectors import (
    COVERAGE_MEASURED,
    COVERAGE_UNAVAILABLE,
    WRITE_NONE,
    FieldCoverage,
    ServiceCoverage,
    WriteSurface,
)
from src.connectors.arrivals import Arrival

#: La base de produccion. Correr contra esta crea envios REALES y cobra.
API_BASE = "https://api.sendle.com"

#: La base de sandbox. Es donde se prueba, y por eso esta exportada y no escondida.
SANDBOX_BASE = "https://sandbox.sendle.com"

#: El ``event_type`` con el que Sendle da por entregado. En minuscula porque la comparacion
#: es case-insensitive. Los otros publicados: Pickup Attempted, Pickup, Info, In Transit.
DELIVERED = "delivered"

#: Sendle limita a 10 requests por segundo por IP. Con lecturas secuenciales no se roza; se
#: documenta para que nadie paralelice esto sin saber contra que.
RATE_LIMIT_PER_SECOND = 10

#: Cuantas referencias se consultan por defecto. Cada orden cuesta una llamada: el endpoint
#: toma una referencia por vez.
_MAX_ORDERS = 500

_NOTA_TOPE = (
    "Se consultaron {leidas} de {total} referencias (tope max_orders={tope}). "
    "/api/tracking/{{ref}} consulta una por vez y no admite lectura masiva, asi que la "
    "cobertura de llegada alcanza SOLO a esas ordenes."
)
_NOTA_SIN_LLAVE = (
    "{sin} de {total} ordenes no tienen referencia de Sendle y NO se consultaron. No es que "
    "el transportista las haya dado por no entregadas: nunca se pregunto por ellas. Sus "
    "lineas salen del denominador de OTIF por falta de mapeo, no por falta de entrega."
)


class SendleError(RuntimeError):
    """Sendle contesto algo que no se puede leer como una respuesta."""


@dataclass(frozen=True)
class SendleCredentials:
    """Las dos mitades del Basic auth. Se sacan de Settings > API en la cuenta de Sendle."""

    sendle_id: str
    api_key: str


def auth_headers(creds: SendleCredentials) -> dict[str, str]:
    """Las cabeceras de un request a Sendle. HTTP Basic: ID de usuario, API key de password.

    Se arman por request y no se guardan en el cliente, para que la credencial no quede
    pegada a un objeto de larga vida que despues alguien loguea entero.
    """
    par = f"{creds.sendle_id}:{creds.api_key}".encode()
    return {
        "Authorization": f"Basic {base64.b64encode(par).decode()}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _iso(valor: Any) -> str | None:
    """La fecha ISO ``YYYY-MM-DD`` de un ``scan_time``, o ``None`` si no la hay."""
    texto = str(valor or "").strip()
    return texto[:10] if len(texto) >= 10 else None


def delivered_on(payload: dict) -> str | None:
    """La fecha del PRIMER evento ``Delivered``, o ``None`` si no hay ninguno.

    **``state`` no se lee.** Ver la decision 3 del docstring del modulo: es un estado
    terminal y correcto, y no trae marca de tiempo. Un estado no es una fecha.
    """
    eventos = (payload or {}).get("tracking_events") or []
    fechas = [
        fecha
        for ev in eventos
        if str((ev or {}).get("event_type") or "").strip().lower() == DELIVERED
        and (fecha := _iso((ev or {}).get("scan_time"))) is not None
    ]
    return min(fechas) if fechas else None


class SendleArrivals:
    """Las llegadas que Sendle conoce, para las ordenes que tienen su referencia.

    ``refs`` es el mapa ``order_id del ERP -> sendle_reference``. ``order_ids`` es opcional y
    sirve para UNA cosa: contar lo que quedo afuera del mapa y decirlo en ``notes``.
    """

    def __init__(
        self,
        http: Any,
        creds: SendleCredentials,
        refs: Mapping[str, str],
        *,
        order_ids: Sequence[str] | None = None,
        max_orders: int = _MAX_ORDERS,
    ) -> None:
        self._http = http
        self._creds = creds
        self._refs = dict(refs)
        self._order_ids = list(order_ids) if order_ids is not None else None
        self._max_orders = max_orders
        #: Lo que hay que decirle al operador sobre lo que NO se consulto. Va al informe.
        self.notes: tuple[str, ...] = ()

    def _notas(self, consultadas: int) -> tuple[str, ...]:
        notas: list[str] = []
        if consultadas != len(self._refs):
            notas.append(
                _NOTA_TOPE.format(
                    leidas=consultadas, total=len(self._refs), tope=self._max_orders
                )
            )
        if self._order_ids is not None:
            sin_llave = sum(1 for o in self._order_ids if o not in self._refs)
            if sin_llave:
                notas.append(
                    _NOTA_SIN_LLAVE.format(sin=sin_llave, total=len(self._order_ids))
                )
        return tuple(notas)

    def arrivals(self, *, since: str | None = None) -> list[Arrival]:
        """Las llegadas conocidas. Solo las ordenes que Sendle dio por entregadas.

        ``since`` filtra el RESULTADO por fecha de llegada. No viaja a la API: el endpoint no
        acepta ventana de fechas, asi que filtrar antes seria mentir sobre lo consultado.
        """
        pares = list(self._refs.items())[: self._max_orders]
        self.notes = self._notas(len(pares))

        salida: list[Arrival] = []
        for order_id, ref in pares:
            resp = self._http.get(f"/api/tracking/{ref}", headers=auth_headers(self._creds))
            estado = getattr(resp, "status_code", 200)
            if estado == 404:
                # Sendle no conoce esa referencia. Es una respuesta, no una falla: ese
                # pedido pudo haberse enviado por fuera de la plataforma.
                continue
            if estado != 200:
                raise SendleError(
                    f"Sendle contesto {estado} consultando la referencia {ref!r}. Una "
                    "consulta fallida no se degrada a 'no llego': eso sacaria la linea del "
                    "denominador de OTIF por un problema de red, no por falta de dato."
                )
            cuando = delivered_on(resp.json() or {})
            if cuando is not None and (since is None or cuando >= since):
                salida.append(Arrival(order_id, cuando))
        return salida


SERVICE_COVERAGE = ServiceCoverage(
    backend="Sendle (transportista AU/NZ; NO es sistema de registro)",
    delivered_qty=FieldCoverage(
        COVERAGE_UNAVAILABLE,
        "",
        "un transportista mueve bultos, no lineas de pedido: la respuesta de tracking no "
        "trae ni SKU ni cantidad. Cuanto se pidio y cuanto se sirvio lo pone el ERP",
    ),
    promised_date=FieldCoverage(
        COVERAGE_UNAVAILABLE,
        "",
        "la fecha prometida al cliente se pacta en la venta, no en el envio. Sendle publica "
        "una estimacion de transito al cotizar, que no es lo que el vendedor le prometio al "
        "comprador, y por eso no se usa para nada",
    ),
    delivered_date=FieldCoverage(
        COVERAGE_MEASURED,
        "GET /api/tracking/{sendle_reference} -> tracking_events[] con event_type="
        "'Delivered' -> scan_time",
        "la fecha en que el transportista dio por ENTREGADO el envio: el pedido llego al "
        "cliente. Sale del evento y NO del campo state, que dice correctamente que llego "
        "pero no trae marca de tiempo -- un estado no es una fecha",
    ),
)


def live_http(*, timeout: float = 30.0, sandbox: bool = False) -> Any:
    """El cliente httpx apuntado a Sendle. **No lleva credenciales.**

    ``sandbox=True`` apunta a la base de pruebas. No es un detalle de comodidad: la API de
    produccion de Sendle crea envios reales y cobra la tarjeta de la cuenta.
    """
    import httpx

    return httpx.Client(base_url=SANDBOX_BASE if sandbox else API_BASE, timeout=timeout)


#: Que escribe este conector en el sistema del cliente (ver ``WriteSurface``).
WRITE_SURFACE = WriteSurface(
    WRITE_NONE,
    means=(
        'solo lectura: transportista; Kern lee llegadas y no crea ni modifica envios.'
    ),
)
