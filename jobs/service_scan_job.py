"""Servicio al cliente realmente entregado, leido de una cuenta viva. Tool #51.

Cierra el limite que declaro el PR #308: hasta ahora, medir el servicio de un CONECTOR
exigia escribir Python a mano. El motor (``src/order_fulfillment.py``), la lectura
(``src/connectors/service_scan.py``) y los adaptadores (Unleashed, Cin7 Core) ya existian y
ya estaban testeados; lo que faltaba era el envoltorio de agente.

Este modulo no reimplementa nada de eso. Hace tres cosas:

1. **Resuelve la fuente**: fuente inyectada -> Unleashed -> Cin7 Core -> el simulador
   offline. Mismo orden y mismo patron que ``jobs/odoo_job.py::_make_rpc``, que es el
   precedente del repo para una tool sobre un backend vivo.
2. **Mide**, delegando en ``scan_service``.
3. **Frena lo que no se puede publicar** (``verify``).

DOS INVARIANTES, y las dos existen porque su violacion no se ve hasta que ya llego a un
cliente:

* **Ninguna credencial sale de aca.** Ni al nombre de la fuente, ni a las notas, ni al
  entregable. El nombre identifica al BACKEND ("Unleashed (cuenta viva)"), nunca a la
  cuenta: el impulso natural al escribir esto es meter el api-id "para saber cual es", y
  eso lo imprime en el informe del cliente y en cada log que lo toque.
* **El simulador se anuncia.** Una demo que no se declara demo se lee como una cuenta real,
  y ese es el peor entregable posible: numeros inventados con cara de medicion.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from nosilo_core.guided import GuidedOutcome, HandoffPacket, Residual, as_handoff

from src import order_fulfillment as of
from src.connectors import ServiceCoverage
from src.connectors.service_scan import scan_service

#: Como se anuncia cada backend. Identifican al SISTEMA, no a la cuenta -- ver la invariante
#: de credenciales en el docstring del modulo.
_NAME_UNLEASHED = "Unleashed (cuenta viva, API v1)"
_NAME_CIN7 = "Cin7 Core (cuenta viva, API v2)"
_NAME_INJECTED = "fuente inyectada (simulador o doble de test)"
_NAME_DEMO = (
    "simulador offline -- datos de DEMOSTRACION, NO es una cuenta real ni un sistema de "
    "registro. No se encontraron credenciales de Unleashed ni de Cin7 Core"
)

#: El unico transportista que se puede resolver con solo sus credenciales. Starshipit se
#: consulta por la referencia del ERP, que es exactamente lo que la lectura acaba de
#: producir. Shippit y Sendle tienen adaptador construido y testeado y NO se pueden
#: encender: los dos se consultan por su llave interna y exigen un mapa
#: ``order_id -> llave`` que ninguna linea de este repo produce -- Unleashed no expone
#: numero de seguimiento y Cin7 lo menciona en prosa sin parsearlo. Enchufarlos igual
#: empalmaria contra un mapa vacio, y un OTIF de llegada sobre cero llegadas se lee igual
#: que uno sobre mil.
_NAME_STARSHIPIT = "Starshipit (transportista, cuenta viva)"
_NAME_CARRIER_INJECTED = "transportista inyectado (simulador o doble de test)"

_PROBLEMA_OTIF_SIN_PUNTUALIDAD = (
    "el reporte publica un OTIF de {otif:.0%} sobre CERO lineas con evidencia de "
    "puntualidad. No es lo mismo que un fill rate sin lineas: una cartera puede tener cien "
    "lineas servidas y ninguna con fecha de entrega, y entonces el fill rate descansa en "
    "cien y el OTIF en nada. No se entrega."
)
_PROBLEMA_COBERTURA_CERO = (
    "se empalmo un transportista y no reporto la llegada de NINGUNA orden. El informe diria "
    "'OTIF de llegada' sobre cero llegadas conocidas: no es un OTIF parcial, es ninguno. "
    "Revisar credenciales, ventana y mapeo de referencias antes de entregar."
)

_PROBLEMA_SIN_EVIDENCIA = (
    "el reporte publica un fill rate de {fill:.0%} y no tiene ni una linea puntuada: un "
    "numero medido sobre cero evidencia se lee igual que uno medido sobre mil. No se "
    "entrega."
)
_PROBLEMA_OTIF_SIN_EVIDENCIA = (
    "el reporte publica un OTIF de {otif:.0%} sin lineas puntuadas: misma evidencia vacia "
    "que el fill rate, mismo motivo para no entregarlo."
)


@dataclass(frozen=True)
class ServiceScanReport:
    """Lo que una cuenta entrego, y lo que su backend NO puede decir del asunto."""

    #: Que backend se leyo. Nunca que cuenta: ver la invariante de credenciales.
    source_name: str
    records: tuple[of.RealizedService, ...]
    portfolio: of.RealizedService
    #: Cuantas ordenes y lineas sostienen los numeros de arriba.
    n_orders: int = 0
    n_lines: int = 0
    #: Desde cuando se leyo. ``None`` es "todo el historico", y no es lo mismo.
    since: str | None = None
    #: La declaracion de cobertura del adaptador, cuando publica una. Con transportista
    #: empalmado es la COMPUESTA: lo servido y la promesa siguen siendo del sistema de
    #: registro, la fecha de entrega pasa a ser del transportista.
    coverage: ServiceCoverage | None = None
    #: Fraccion de ordenes con fecha de llegada. ``None`` = no se empalmo transportista, y
    #: no es lo mismo que ``0.0``, que es "se empalmo y no conocia ninguna".
    arrival_coverage: float | None = None
    #: Lo que hay que decirle al operador sobre lo que NO se pudo medir. Va al entregable.
    notes: tuple[str, ...] = field(default_factory=tuple)
    #: El desenlace never-unprotected. Siempre un HANDOFF: un scan MIDE, no arregla nada.
    outcome: GuidedOutcome | None = None


def _outcome(
    report_records, peores, source_name: str, arrival_coverage: float | None = None
) -> GuidedOutcome:
    """El desenlace. Siempre HANDOFF, y eso es una afirmacion sobre lo que esta tool hace.

    El contrato never-unprotected del repo pide que ningun resultado consecuente quede sin
    salida (``nosilo_core.guided``). Para un scan de servicio la salida honesta es un paso
    HUMANO: esta tool cuenta quiebres, no los arregla, y devolverla como EXECUTED diria que
    el agente ya hizo algo con ellos.

    El primer paso es siempre el mismo y no es una formalidad: confirmar que registra la
    fecha de entrega del backend. De uno que solo ve la salida, "OTIF" significa despachar
    a tiempo, y entre esa lectura y la llegada real hay 6,2 puntos medidos.
    """
    # El primer paso depende de si hubo transportista, y no es cosmetica. Sin empalme la
    # pregunta abierta es QUE significa la fecha; con empalme ya esta contestada -- sale del
    # transportista -- y la que queda abierta es CUANTO del libro se pudo empalmar. Dejar la
    # primera cuando el empalme ocurrio gasta el unico paso humano que alguien va a leer en
    # una pregunta resuelta, y esconde la que si importa.
    if arrival_coverage is None:
        pasos = [
            "Confirmar con el cliente que registra la fecha de entrega de su sistema: un "
            "despacho del deposito y una llegada al cliente no son el mismo numero, y el "
            "OTIF de arriba mide lo que su backend pueda ver.",
        ]
    else:
        pasos = [
            f"El OTIF de arriba es de LLEGADA, no de despacho, y descansa en una cobertura "
            f"de empalme del {arrival_coverage:.0%} de las ordenes. Revisar con el cliente "
            "las ordenes que quedaron sin empalmar antes de leer el numero como el de la "
            "cartera entera: salen del denominador de puntualidad, no del de fill rate.",
        ]
    if peores:
        nombres = ", ".join(r.product_id for r in peores[:5])
        pasos.append(
            f"Revisar los SKU peor servidos antes de mover ninguna politica: {nombres}. "
            "Una politica no arregla un quiebre que la politica no causo."
        )
    if not report_records:
        pasos.append(
            "No hubo ordenes en la ventana leida: conectar la cuenta o ampliar el rango "
            "antes de concluir nada sobre el servicio."
        )
    paquete = HandoffPacket(
        title="Revisar el servicio medido y confirmar que significa la fecha de entrega",
        steps=pasos,
        risk_if_skipped=(
            "Un OTIF de despacho leido como si fuera de llegada le muestra al cliente un "
            "servicio mejor que el que sus compradores vivieron."
        ),
    )
    return as_handoff(
        f"Servicio medido sobre {source_name}. Requiere revision humana antes de actuar.",
        [paquete],
        residuals=[
            Residual(
                description="Decidir que hacer con los SKU peor servidos.",
                risk_if_skipped="Los quiebres siguen, medidos y sin corregir.",
            )
        ],
    )


def _resolve_source(params: dict) -> tuple[Any, str]:
    """La fuente a leer, y como se la anuncia. Inyectada -> Unleashed -> Cin7 -> simulador.

    El orden no es arbitrario: una fuente inyectada es siempre un test o una demo dirigida,
    y las credenciales de un backend vivo le ganan al stand-in porque quien las puso quiere
    leer SU cuenta. El simulador es el ultimo recurso y se declara como tal.
    """
    inyectada = params.get("source")
    if inyectada is not None:
        return inyectada, _NAME_INJECTED

    api_id = params.get("unleashed_api_id") or os.environ.get("UNLEASHED_API_ID")
    api_key = params.get("unleashed_api_key") or os.environ.get("UNLEASHED_API_KEY")
    if api_id and api_key:
        from src.connectors.unleashed import UnleashedConnector, UnleashedCredentials, live_http

        creds = UnleashedCredentials(api_id=api_id, api_key=api_key)
        http = params.get("http") or live_http(creds)
        return UnleashedConnector(http, creds), _NAME_UNLEASHED

    account = params.get("cin7_account_id") or os.environ.get("CIN7_ACCOUNT_ID")
    app_key = params.get("cin7_application_key") or os.environ.get("CIN7_APPLICATION_KEY")
    if account and app_key:
        from src.connectors.cin7 import Cin7Connector, Cin7Credentials, live_http

        creds = Cin7Credentials(account_id=account, application_key=app_key)
        http = params.get("http") or live_http()
        return Cin7Connector(http, creds), _NAME_CIN7

    from src.connectors.simulator import demo_store

    return demo_store(), _NAME_DEMO


def _resolve_carrier(params: dict) -> tuple[object | None, str | None]:
    """El transportista a empalmar, y como se lo anuncia. ``(None, None)`` = ninguno.

    Devuelve una FABRICA y no un conector armado, y el motivo es de costo: los tres
    adaptadores de transportista se construyen sabiendo que ordenes van a consultar, y
    ``prepare`` todavia no leyo la fuente -- a proposito, porque contra Cin7 una lectura son
    60 llamadas por minuto. ``scan_service`` llama a la fabrica con los identificadores de
    la lectura que ya hizo. Ver ``src.connectors.service_scan.CarrierFactory``.

    **Sin credenciales no se arma nada, y eso es deliberado**: el camino offline de esta
    tool tiene que quedar byte-identico al de antes de existir este resolvedor.
    """
    inyectado = params.get("carrier")
    if inyectado is not None:
        return inyectado, _NAME_CARRIER_INJECTED

    api_key = params.get("starshipit_api_key") or os.environ.get("STARSHIPIT_API_KEY")
    sub_key = (
        params.get("starshipit_subscription_key")
        or os.environ.get("STARSHIPIT_SUBSCRIPTION_KEY")
    )
    if api_key and sub_key:
        from src.connectors.starshipit import (
            StarshipitArrivals,
            StarshipitCredentials,
            live_http,
        )

        creds = StarshipitCredentials(api_key=api_key, subscription_key=sub_key)
        http = params.get("carrier_http") or live_http()
        return (lambda ids: StarshipitArrivals(http, creds, list(ids))), _NAME_STARSHIPIT

    return None, None


def prepare(data_path: str | None = None, params: dict | None = None) -> dict:
    """Resuelve la fuente. ``data_path`` no se usa: esta tool lee una cuenta, no un archivo.

    No lee nada todavia -- eso pasa en ``run`` -- porque una lectura contra Cin7 cuesta una
    llamada por venta y no tiene por que pagarse dos veces si el orquestador solo esta
    resolviendo la tool.
    """
    params = params or {}
    fuente, nombre = _resolve_source(params)
    transportista, nombre_transportista = _resolve_carrier(params)
    return {
        "source": fuente,
        "source_name": nombre,
        "since": params.get("since"),
        "carrier": transportista,
        "carrier_name": nombre_transportista,
    }


def run(payload: dict, *, since: str | None = None, stockout_days: dict | None = None):
    """Mide el servicio de la fuente resuelta. Delega en ``scan_service``, no lo repite."""
    ventana = since if since is not None else payload.get("since")
    transportista = payload.get("carrier")
    # UNA SOLA LLAMADA, con o sin transportista. La alternativa -- intentar el empalme y
    # recaer en un scan sin transportista si el transportista falla -- exige releer la
    # fuente entera, que es exactamente lo que el comentario de abajo documenta haber
    # pagado por evitar. Y la degradacion tampoco seria gratis: un OTIF de despacho
    # entregado bajo un pedido de OTIF de llegada es el numero equivocado con cara de
    # correcto. Un transportista caido revienta la corrida, a proposito y declarado.
    scan = scan_service(
        payload["source"],
        since=ventana,
        stockout_days=stockout_days,
        carrier=transportista,
    )
    # ``scan.n_orders`` y no un segundo ``orders()``: la primera version releia la fuente
    # para contar pedidos, y contra Cin7 eso es una pasada entera de mas a 60 llamadas por
    # minuto. Lo detecto un test que espiaba cuantas veces se llamaba al adaptador.
    n_ordenes = scan.n_orders
    n_lineas = int(len(scan.order_lines))
    peores = sorted(
        (r for r in scan.records if (r.lost_sales_value or 0) > 0),
        key=lambda r: r.lost_sales_value or 0,
        reverse=True,
    )
    # El nombre COMPUESTO cuando hubo empalme. Publicar solo el del sistema de registro
    # arriba de una fecha que salio del transportista es la ultima forma del error que
    # ``coverage_of_arrivals`` existe para impedir: el cliente lee "Unleashed" en la fila de
    # fuentes de un informe cuya fecha de entrega Unleashed no registra. No se usa
    # ``scan.source_name`` porque perderia la invariante de que el simulador se anuncia.
    nombre = payload["source_name"]
    if transportista is not None and payload.get("carrier_name"):
        nombre = f"{nombre} + {payload['carrier_name']}"
    return ServiceScanReport(
        source_name=nombre,
        records=tuple(scan.records),
        portfolio=scan.portfolio,
        n_orders=n_ordenes,
        n_lines=n_lineas,
        since=ventana,
        coverage=scan.coverage,
        arrival_coverage=scan.arrival_coverage,
        notes=tuple(scan.notes),
        outcome=_outcome(scan.records, peores, nombre, scan.arrival_coverage),
    )


def verify(report: ServiceScanReport) -> list[str]:
    """El QA. Lista vacia = paso, como en las 50 tools registradas.

    Una sola condicion, y es la que este hilo entero persigue: **un numero de servicio sin
    evidencia no se entrega**. Todo lo demas que podria estar mal -- una cuenta sin ordenes,
    un backend que no publica cantidades servidas -- ya viaja declarado como ``None`` o como
    una nota, que es la respuesta correcta y no un fallo de QA.
    """
    problemas: list[str] = []
    p = report.portfolio
    if p.lines_scored <= 0:
        if p.fill_rate is not None:
            problemas.append(_PROBLEMA_SIN_EVIDENCIA.format(fill=p.fill_rate))
        if p.otif is not None:
            problemas.append(_PROBLEMA_OTIF_SIN_EVIDENCIA.format(otif=p.otif))
    # El gate de arriba mira ``lines_scored``, que es el denominador del FILL RATE. Mientras
    # el sistema de registro fecha todo lo que despacha los dos coinciden; un transportista
    # con cobertura parcial los divorcia, y entonces ese gate deja pasar un OTIF construido
    # sobre cero evidencia de puntualidad.
    elif p.otif is not None and p.lines_otif_scored <= 0:
        problemas.append(_PROBLEMA_OTIF_SIN_PUNTUALIDAD.format(otif=p.otif))
    if report.arrival_coverage == 0.0:
        problemas.append(_PROBLEMA_COBERTURA_CERO)
    return problemas
