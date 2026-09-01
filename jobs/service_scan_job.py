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
    #: La declaracion de cobertura del adaptador, cuando publica una.
    coverage: ServiceCoverage | None = None
    #: Lo que hay que decirle al operador sobre lo que NO se pudo medir. Va al entregable.
    notes: tuple[str, ...] = field(default_factory=tuple)
    #: El desenlace never-unprotected. Siempre un HANDOFF: un scan MIDE, no arregla nada.
    outcome: GuidedOutcome | None = None


def _outcome(report_records, peores, source_name: str) -> GuidedOutcome:
    """El desenlace. Siempre HANDOFF, y eso es una afirmacion sobre lo que esta tool hace.

    El contrato never-unprotected del repo pide que ningun resultado consecuente quede sin
    salida (``nosilo_core.guided``). Para un scan de servicio la salida honesta es un paso
    HUMANO: esta tool cuenta quiebres, no los arregla, y devolverla como EXECUTED diria que
    el agente ya hizo algo con ellos.

    El primer paso es siempre el mismo y no es una formalidad: confirmar que registra la
    fecha de entrega del backend. De uno que solo ve la salida, "OTIF" significa despachar
    a tiempo, y entre esa lectura y la llegada real hay 6,2 puntos medidos.
    """
    pasos = [
        "Confirmar con el cliente que registra la fecha de entrega de su sistema: un "
        "despacho del deposito y una llegada al cliente no son el mismo numero, y el "
        "OTIF de arriba mide lo que su backend pueda ver.",
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


def prepare(data_path: str | None = None, params: dict | None = None) -> dict:
    """Resuelve la fuente. ``data_path`` no se usa: esta tool lee una cuenta, no un archivo.

    No lee nada todavia -- eso pasa en ``run`` -- porque una lectura contra Cin7 cuesta una
    llamada por venta y no tiene por que pagarse dos veces si el orquestador solo esta
    resolviendo la tool.
    """
    params = params or {}
    fuente, nombre = _resolve_source(params)
    return {"source": fuente, "source_name": nombre, "since": params.get("since")}


def run(payload: dict, *, since: str | None = None, stockout_days: dict | None = None):
    """Mide el servicio de la fuente resuelta. Delega en ``scan_service``, no lo repite."""
    ventana = since if since is not None else payload.get("since")
    scan = scan_service(payload["source"], since=ventana, stockout_days=stockout_days)
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
    return ServiceScanReport(
        source_name=payload["source_name"],
        records=tuple(scan.records),
        portfolio=scan.portfolio,
        n_orders=n_ordenes,
        n_lines=n_lineas,
        since=ventana,
        coverage=scan.coverage,
        notes=tuple(scan.notes),
        outcome=_outcome(scan.records, peores, payload["source_name"]),
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
    return problemas
