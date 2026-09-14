"""Shippit -- el segundo transportista del canal AU/NZ que puede fechar una llegada.

Mismo trabajo que ``starshipit.py`` y misma declaracion de cobertura: un transportista sabe
CUANDO llego un envio y nada mas. Lo que cambia -- y es lo unico que cambia -- es como se lo
consulta.

Verificado contra la referencia oficial el 2026-09-01
(``https://developer.shippit.com/api_guide/tracking.html`` y ``.../webhook.html``):

    GET /orders/{tracking_number}/tracking
    ->  {"response": {"tracking_number", "tracking_url", "success",
                      "track": [{"status", "date", "timestamp", "status_owner"}]}}

Base ``https://app.shippit.com/api/3``. Autenticacion ``Authorization: Bearer <api_key>``:
como en Cin7 y Starshipit y a diferencia de Unleashed (que firma), **la clave viaja en cada
request** y aparece en cualquier proxy que registre cabeceras.

CUATRO DECISIONES.

1. **Se consulta por la llave de Shippit y se devuelve la del ERP.** Starshipit acepta la
   referencia del ERP (``/api/track?order_number=``); Shippit NO. Su path toma el
   ``tracking_number`` que Shippit asigna -- formato ``PP39TBECV7QSSPK``, que no es el
   numero de guia del courier: ese viaja aparte como ``courier_job_id``. Asi que el
   adaptador recibe un MAPA ``order_id -> tracking_number`` y emite ``Arrival`` con la
   referencia del ERP. Devolver la del transportista haria que ``with_arrivals`` no empalme
   con nada y que el OTIF de llegada saliera vacio, sin error y sin nota.

   El mapa lo arma quien tiene el ERP delante: Cin7 Core guarda un ``TrackingNumber`` en la
   linea de envio, Unleashed no expone ninguno. Resolverlo desde aca no es posible -- el
   ``retailer_order_number`` aparece en el WEBHOOK de Shippit, no en la respuesta de
   tracking.

2. **Una orden sin llave se declara.** ``with_arrivals`` saca del denominador de OTIF a toda
   orden sin llegada. Para Starshipit eso es honesto: se pregunto y contesto 404. Aca una
   orden sin ``tracking_number`` produce EL MISMO silencio porque nunca se pregunto, y un
   OTIF calculado sobre "las que casualmente tenian tracking cargado" no es el de la
   cartera. Por eso ``order_ids`` es opcional: si te lo dan, se cuenta lo que falta y se
   dice en ``notes``.

3. **La llegada sale de ``track``, nunca de ``expected_delivery_date``.** El webhook publica
   ese campo y es la ESTIMACION de Shippit, no la entrega. Usarlo daria un OTIF casi
   perfecto por construccion -- una estimacion tiende a cumplir su propia estimacion -- y el
   numero se leeria como una medicion. Es la misma trampa que ``last_updated_date`` en
   Starshipit, con otro nombre.

4. **El PRIMER evento ``Completed``, y sin confiar en el orden del array.** Shippit publica
   su ejemplo con el evento mas nuevo primero, pero no lo garantiza; tomar ``track[0]`` o
   ``track[-1]`` es apostar. Y un segundo ``Completed`` es un duplicado o un reintento: la
   entrega ocurrio la primera vez.
"""

from __future__ import annotations

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

#: La base de la API. Se fija aca para que ``live_http`` sea el unico lugar que la sabe.
API_BASE = "https://app.shippit.com/api/3"

#: El estado con el que Shippit da por entregado. En minuscula porque la comparacion es
#: case-insensitive: el endpoint de tracking devuelve "Completed" y el webhook "completed".
DELIVERED = "completed"

#: Cuantas referencias se consultan por defecto. NO es una optimizacion: el path toma una
#: referencia por vez, asi que cada orden cuesta una llamada.
_MAX_ORDERS = 500

_NOTA_TOPE = (
    "Se consultaron {leidas} de {total} referencias (tope max_orders={tope}). "
    "/orders/{{ref}}/tracking consulta una por vez y no admite lectura masiva, asi que la "
    "cobertura de llegada alcanza SOLO a esas ordenes."
)
_NOTA_SIN_LLAVE = (
    "{sin} de {total} ordenes no tienen numero de seguimiento de Shippit y NO se "
    "consultaron. No es que el transportista las haya dado por no entregadas: nunca se "
    "pregunto por ellas. Sus lineas salen del denominador de OTIF por falta de mapeo, no "
    "por falta de entrega."
)


class ShippitError(RuntimeError):
    """Shippit contesto algo que no se puede leer como una respuesta."""


@dataclass(frozen=True)
class ShippitCredentials:
    """La clave de API. Una por cuenta, y una cuenta por deposito segun la doc de Shippit."""

    api_key: str


def auth_headers(creds: ShippitCredentials) -> dict[str, str]:
    """La cabecera de autenticacion. Se arma por request y no se guarda en el cliente."""
    return {"Authorization": f"Bearer {creds.api_key}"}


def _iso(valor: Any) -> str | None:
    """La fecha ISO ``YYYY-MM-DD`` de un timestamp de Shippit, o ``None`` si no la hay."""
    texto = str(valor or "").strip()
    return texto[:10] if len(texto) >= 10 else None


def delivered_on(payload: dict) -> str | None:
    """La fecha del PRIMER evento ``Completed``, o ``None`` si el envio no llego.

    Lee ``response.track``, que es donde vive el array: Shippit envuelve toda respuesta en
    ``response`` y buscarlo en la raiz devolveria ``None`` siempre -- que no se lee como un
    bug, se lee como "no llego".
    """
    eventos = ((payload or {}).get("response") or {}).get("track") or []
    fechas = [
        fecha
        for ev in eventos
        if str((ev or {}).get("status") or "").strip().lower() == DELIVERED
        and (fecha := _iso((ev or {}).get("date"))) is not None
    ]
    return min(fechas) if fechas else None


class ShippitArrivals:
    """Las llegadas que Shippit conoce, para las ordenes que tienen su llave.

    ``refs`` es el mapa ``order_id del ERP -> tracking_number de Shippit``. ``order_ids`` es
    opcional y sirve para UNA cosa: contar lo que quedo afuera del mapa y decirlo. Ver la
    decision 2 del docstring del modulo.
    """

    def __init__(
        self,
        http: Any,
        creds: ShippitCredentials,
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
        """Las llegadas conocidas. Solo las ordenes que Shippit dio por entregadas.

        ``since`` filtra el RESULTADO por fecha de llegada. No viaja a la API: el endpoint
        no acepta ventana de fechas, asi que filtrar antes seria mentir sobre lo consultado.
        """
        pares = list(self._refs.items())[: self._max_orders]
        self.notes = self._notas(len(pares))

        salida: list[Arrival] = []
        for order_id, ref in pares:
            resp = self._http.get(f"/orders/{ref}/tracking", headers=auth_headers(self._creds))
            estado = getattr(resp, "status_code", 200)
            if estado == 404:
                # Shippit no conoce esa referencia. Es una respuesta, no una falla: ese
                # pedido pudo haberse enviado por fuera de la plataforma.
                continue
            if estado != 200:
                raise ShippitError(
                    f"Shippit contesto {estado} consultando la referencia {ref!r}. Una "
                    "consulta fallida no se degrada a 'no llego': eso sacaria la linea del "
                    "denominador de OTIF por un problema de red, no por falta de dato."
                )
            cuando = delivered_on(resp.json() or {})
            if cuando is not None and (since is None or cuando >= since):
                salida.append(Arrival(order_id, cuando))
        return salida


SERVICE_COVERAGE = ServiceCoverage(
    backend="Shippit (plataforma de envios AU/NZ; transportista, NO sistema de registro)",
    delivered_qty=FieldCoverage(
        COVERAGE_UNAVAILABLE,
        "",
        "un transportista mueve bultos, no lineas de pedido: la respuesta de tracking no "
        "trae ni SKU ni cantidad. Cuanto se pidio y cuanto se sirvio lo pone el ERP",
    ),
    promised_date=FieldCoverage(
        COVERAGE_UNAVAILABLE,
        "",
        "la fecha prometida al cliente se pacta en la venta, no en el envio. Shippit "
        "publica expected_delivery_date, que es SU estimacion de transito -- no lo que el "
        "vendedor le prometio al comprador -- y por eso no se usa para nada",
    ),
    delivered_date=FieldCoverage(
        COVERAGE_MEASURED,
        "GET /orders/{tracking_number}/tracking -> response.track[] con status='Completed' "
        "-> date",
        "la fecha en que el transportista dio por ENTREGADO el envio: el pedido llego al "
        "cliente. Sale del evento y NO de expected_delivery_date, que es la estimacion de "
        "Shippit y daria un OTIF perfecto por construccion",
    ),
)


def live_http(*, timeout: float = 30.0) -> Any:
    """El cliente httpx apuntado a Shippit. **No lleva credenciales.**

    Y la firma lo dice: no toma ``creds``. Las cabeceras se arman por request en
    ``auth_headers``, para que la clave no quede pegada a un objeto de larga vida que
    despues alguien loguea entero.
    """
    import httpx

    return httpx.Client(base_url=API_BASE, timeout=timeout)


#: Que escribe este conector en el sistema del cliente (ver ``WriteSurface``).
WRITE_SURFACE = WriteSurface(
    WRITE_NONE,
    means=(
        'solo lectura: transportista; Kern lee llegadas y no crea ni modifica envios.'
    ),
)
