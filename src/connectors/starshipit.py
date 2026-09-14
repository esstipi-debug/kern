"""Starshipit — de donde sale la fecha de llegada en el canal AU/NZ.

Starshipit es la plataforma de envios de AU/NZ que se sienta entre el ERP y el
transportista. Este adaptador lee UNA cosa: cuando el transportista dio por entregado cada
pedido. Es lo que ni Unleashed, ni Cin7 Core, ni Odoo registran, y lo que separa un OTIF de
"salio a tiempo del deposito" de uno de "llego a tiempo al cliente".

Verificado contra la referencia OpenAPI oficial el 2026-08-31
(``https://support.starshipit.com/developers/api-reference/starshipit/reference.json``):

    GET /api/track?order_number={ref}
    ->  {"results": {"order_number", "tracking_number", "tracking_status",
                     "shipment_date", "last_updated_date",
                     "tracking_events": [{"event_datetime", "status", "details"}]},
         "success": true}

Cabeceras: ``StarShipIT-Api-Key`` y ``Ocp-Apim-Subscription-Key``, las dos de Settings >
API. Como en Cin7 y a diferencia de Unleashed, las credenciales VIAJAN en cada request: no
hay firma. Aparecen en cualquier proxy que registre cabeceras.

Cuatro decisiones:

1. **La llegada sale de ``tracking_events``, no de ``last_updated_date``.** El campo de
   resumen es "la ultima actualizacion de tracking", no la entrega: cualquier evento
   posterior -- un escaneo de devolucion, un refresco de estado -- lo corre hacia adelante y
   la entrega se fecha tarde. El evento con ``status == "Delivered"`` trae su propio
   ``event_datetime``, que es la fecha real.

2. **Se toma el PRIMER evento entregado, no el ultimo.** Un segundo "Delivered" es un
   duplicado del transportista o un reintento; la entrega ocurrio la primera vez.

3. **La llave es ``order_number``, no el numero de seguimiento.** El tracking identifica al
   PAQUETE y una orden puede viajar en varios: empalmar por tracking contaria una entrega
   por bulto. ``order_number`` es la referencia del ERP -- el mismo ``OrderNumber`` que
   leen ``unleashed.py`` y ``cin7.py``.

4. **Una consulta por orden.** ``/api/track`` filtra por una referencia, no por ventana de
   fechas: no hay lectura masiva. Mismo perfil de costo que Cin7, y por eso hay un tope de
   ordenes que se declara en ``notes`` en vez de recortar en silencio.

Y lo que este adaptador NO puede decir, que es la mitad de su declaracion: un transportista
no sabe que se pidio. No tiene cantidades, ni promesas, ni precios. ``SERVICE_COVERAGE`` lo
declara ``UNAVAILABLE`` en dos de los tres campos -- es la primera fuente de este repo que
lo hace, y por eso es la que finalmente ejerce esa rama del guard contra un adaptador real
en vez de contra un doble de test.
"""

from __future__ import annotations

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
API_BASE = "https://api.starshipit.com"

#: El estado con el que el transportista da por entregado. Uno de los diecisiete que
#: Starshipit publica; los otros (InTransit, OutForDelivery, AttemptedDelivery...) son
#: camino, no llegada.
DELIVERED = "delivered"

#: Cuantas ordenes se consultan por defecto. NO es una optimizacion: ``/api/track`` filtra
#: por una referencia por vez, asi que cada orden cuesta una llamada.
_MAX_ORDERS = 500

_NOTA_TOPE = (
    "Se consultaron {leidas} de {total} ordenes (tope max_orders={tope}). /api/track filtra "
    "por una referencia por vez y no admite lectura masiva, asi que la cobertura de llegada "
    "alcanza SOLO a esas ordenes."
)


class StarshipitError(RuntimeError):
    """Starshipit contesto algo que no es una lectura. Nunca se degrada a "no llego".

    Una consulta fallida que vuelve vacia es indistinguible de un envio que sigue en
    camino, y la diferencia decide si una orden puntua OTIF o sale del denominador.
    """


@dataclass(frozen=True)
class StarshipitCredentials:
    """Las dos claves de Settings > API. Las dos viajan en cabeceras en cada request."""

    api_key: str
    subscription_key: str


def auth_headers(creds: StarshipitCredentials) -> dict[str, str]:
    """Las cabeceras que Starshipit exige. Sin firma: autentica con las claves planas."""
    return {
        "StarShipIT-Api-Key": creds.api_key,
        "Ocp-Apim-Subscription-Key": creds.subscription_key,
        "Content-Type": "application/json",
    }


def _iso(value: Any) -> str | None:
    """La fecha ISO de un instante (``2024-04-17T13:20:18``), o ``None``."""
    texto = "" if value is None else str(value).strip()
    if len(texto) < 10:
        return None
    fecha = texto[:10]
    if fecha[4] != "-" or fecha[7] != "-":
        return None
    try:
        int(fecha[0:4]), int(fecha[5:7]), int(fecha[8:10])
    except ValueError:
        return None
    return fecha


def delivered_on(results: dict) -> str | None:
    """La fecha en que el transportista entrego, de ``tracking_events``. Funcion pura.

    ``None`` cuando no hay evento de entrega: ese envio sigue en camino, o el transportista
    nunca reporto la entrega. Las dos cosas significan lo mismo para una medicion -- no
    sabemos cuando llego -- y ninguna de las dos es "llego hoy".

    Se ignora ``last_updated_date`` a proposito, aunque el estado de resumen diga
    ``Delivered``: ese campo es la ultima actualizacion de CUALQUIER tipo, y un escaneo
    posterior corre la entrega hacia adelante. La fecha buena es la del evento.
    """
    eventos = (results or {}).get("tracking_events") or []
    fechas = [
        fecha
        for ev in eventos
        if str((ev or {}).get("status") or "").strip().lower() == DELIVERED
        and (fecha := _iso((ev or {}).get("event_datetime"))) is not None
    ]
    # La PRIMERA: un segundo "Delivered" es un duplicado o un reintento del transportista,
    # y la entrega ocurrio la primera vez.
    return min(fechas) if fechas else None


class StarshipitArrivals:
    """``ArrivalSource`` sobre Starshipit. Ver el docstring del modulo.

    ``order_ids`` son las referencias del ERP a consultar -- el llamador las saca de las
    ordenes que ya leyo. El adaptador no las descubre solo porque ``/api/track`` no tiene
    forma de listar: filtra por una referencia por vez.
    """

    def __init__(
        self,
        http: Any,
        creds: StarshipitCredentials,
        order_ids: list[str],
        *,
        max_orders: int = _MAX_ORDERS,
    ) -> None:
        self._http = http
        self._creds = creds
        self._order_ids = list(order_ids)
        self._max_orders = max_orders
        #: Lo que el operador tiene que saber de ESTA lectura. Hoy: si se corto por el tope.
        self.notes: tuple[str, ...] = ()

    def arrivals(self, *, since: str | None = None) -> list[Arrival]:
        """Las llegadas conocidas. Solo las ordenes que el transportista dio por entregadas.

        ``since`` filtra el RESULTADO por fecha de llegada. No viaja a la API: ``/api/track``
        no acepta ventana de fechas, asi que filtrar antes seria mentir sobre lo que se
        consulto.
        """
        pedidas = self._order_ids[: self._max_orders]
        self.notes = (
            ()
            if len(pedidas) == len(self._order_ids)
            else (
                _NOTA_TOPE.format(
                    leidas=len(pedidas), total=len(self._order_ids), tope=self._max_orders
                ),
            )
        )
        salida: list[Arrival] = []
        for oid in pedidas:
            resp = self._http.get(
                "/api/track",
                params={"order_number": oid},
                headers=auth_headers(self._creds),
            )
            estado = getattr(resp, "status_code", 200)
            if estado == 404:
                # Starshipit no conoce esa referencia. Es una respuesta, no una falla: ese
                # pedido pudo haberse enviado por fuera de la plataforma.
                continue
            if estado != 200:
                raise StarshipitError(
                    f"Starshipit contesto {estado} consultando la orden {oid!r}. Una "
                    "consulta fallida no se degrada a 'no llego': eso sacaria la linea del "
                    "denominador de OTIF por un problema de red, no por falta de dato."
                )
            cuerpo = resp.json() or {}
            cuando = delivered_on(cuerpo.get("results") or {})
            if cuando is not None and (since is None or cuando >= since):
                salida.append(Arrival(oid, cuando))
        return salida


def live_http(*, timeout: float = 30.0) -> Any:
    """El cliente httpx apuntado a Starshipit. **No lleva credenciales.**

    Y la firma lo dice: no toma ``creds``. Las cabeceras se arman por request, porque
    pegarlas al cliente las dejaria viajando en cualquier llamada que alguien le haga
    despues a ese mismo objeto. Mismo criterio que ``cin7.live_http``.
    """
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - depende del extra instalado
        raise StarshipitError(
            "httpx es necesario para hablar con Starshipit -- instala el extra que lo trae, "
            "o inyecta un transporte con http= (los tests lo hacen)"
        ) from exc
    return httpx.Client(base_url=API_BASE, timeout=timeout)


#: Lo que un transportista puede y no puede decir del servicio al cliente.
#:
#: Es la primera fuente de este repo que declara campos UNAVAILABLE, y no por pobreza del
#: adaptador: un transportista NO SABE que se pidio. Esa declaracion es la que hace legible
#: por que hace falta empalmarlo con un ERP (``arrivals.coverage_of_arrivals``) en vez de
#: usarlo solo.
SERVICE_COVERAGE = ServiceCoverage(
    backend="Starshipit (plataforma de envios AU/NZ; transportista, NO sistema de registro)",
    delivered_qty=FieldCoverage(
        COVERAGE_UNAVAILABLE,
        "",
        "un transportista no sabe cuantas unidades se pidieron ni cuantas se sirvieron: "
        "mueve bultos, no lineas de pedido. Este dato lo tiene que poner el ERP",
    ),
    promised_date=FieldCoverage(
        COVERAGE_UNAVAILABLE,
        "",
        "la fecha prometida al cliente se pacta en la venta, no en el envio. Starshipit "
        "conoce la fecha de la etiqueta (shipment_date), que es cuando el vendedor decidio "
        "despachar -- no lo que le prometio al comprador",
    ),
    delivered_date=FieldCoverage(
        COVERAGE_MEASURED,
        "GET /api/track -> results.tracking_events[] con status='Delivered' -> event_datetime",
        "la fecha en que el transportista dio por ENTREGADO el envio: el pedido llego al "
        "cliente. Es el dato que ningun ERP del canal registra, y sale del evento y no de "
        "last_updated_date, que es la ultima actualizacion de cualquier tipo",
    ),
)


#: Que escribe este conector en el sistema del cliente (ver ``WriteSurface``).
WRITE_SURFACE = WriteSurface(
    WRITE_NONE,
    means=(
        'solo lectura: un transportista sabe cuando llego un envio y nada mas; Kern lee llegadas para medir OTIF de llegada y no escribe envios.'
    ),
)
