"""El entregable de ``service_scan``: lo que el cliente recibio, y lo que su sistema no ve.

Compone un ``Deliverable`` desde un ``ServiceScanReport``. Sigue el mismo reparto que
``jobs/inventory_deliverable.py``, y la decision que mas importa es donde va cada cosa:

* Los **findings** son problemas del negocio del cliente: los SKU peor servidos, con la
  plata que costaron.
* La **declaracion de cobertura** va en ``data_sources``, no entre los hallazgos. Es un
  limite de la MEDICION, no un defecto de su operacion, y mezclarlos hace que el cliente
  lea "tu ERP no registra la llegada" como si fuera culpa suya.

Ese caveat no es un tecnicismo: Unleashed, Cin7 Core y Odoo llenan ``delivered_date`` con
una SALIDA del deposito, y medido sobre 98.666 pedidos reales la diferencia contra la
llegada real es de 6,2 puntos de OTIF (``examples/run_service_report.py``). Un cliente que
lee "OTIF 99%" y entiende "llego a tiempo" esta leyendo mal un numero bien calculado.
"""

from __future__ import annotations

import csv
from collections.abc import Sequence
from pathlib import Path

from src.deliverable import DataSource, Deliverable, Finding, Kpi, money, pct

from .service_scan_job import ServiceScanReport

#: Cuantos SKU malos entran al informe. Una lista de trescientos no se lee; las peores diez
#: por plata perdida son las que mueven una conversacion.
_TOP_FINDINGS = 10

_SIN_ORDENES = (
    "No orders were read from {source}. Nothing to measure yet -- this is an empty window, "
    "not a service failure."
)


def _resumen(report: ServiceScanReport) -> str:
    """El titular. Nunca un 0% donde no hubo medicion."""
    p = report.portfolio
    if not report.records:
        return _SIN_ORDENES.format(source=report.source_name)
    ventana = f" since {report.since}" if report.since else ""
    return (
        f"Read {report.n_orders:,} orders ({report.n_lines:,} lines) from "
        f"{report.source_name}{ventana}. Service customers actually received: "
        f"{pct(p.fill_rate)} fill rate, {pct(p.otif)} OTIF (perfect order), "
        f"{money(p.lost_sales_value)} of demand not served."
    )


def _findings(report: ServiceScanReport) -> list[Finding]:
    """Los SKU peor servidos, valorizados. Vacio cuando no hay nada medido."""
    con_perdida = [r for r in report.records if (r.lost_sales_value or 0) > 0]
    con_perdida.sort(key=lambda r: r.lost_sales_value or 0, reverse=True)
    return [
        Finding(
            f"{r.product_id}: {pct(r.fill_rate)} fill rate",
            f"{r.lost_sales_units:,.0f} units ordered and not delivered over "
            f"{r.lines_scored} scored line(s); OTIF {pct(r.otif)}.",
            impact=f"{money(r.lost_sales_value)} of revenue not captured",
        )
        for r in con_perdida[:_TOP_FINDINGS]
    ]


def _data_sources(report: ServiceScanReport) -> tuple[DataSource, ...]:
    """El mapa de fuentes, con la declaracion de cobertura del backend adentro.

    Una fila por campo de servicio, con lo que el adaptador declara que ES ese numero. Es
    la unica forma de que un cliente pueda mirar SU sistema y confirmar -- o descubrir que
    su "OTIF" mide otra cosa.
    """
    filas = [
        DataSource("Orders, lines and prices", report.source_name, "per run"),
        DataSource(
            "Measurement window",
            f"from {report.since}" if report.since else "all history available in the source",
            "per run",
        ),
    ]
    if report.coverage is not None:
        for campo, cobertura in report.coverage.fields().items():
            fuente = cobertura.source or "not available in this backend"
            filas.append(
                DataSource(f"{campo} ({cobertura.status})", f"{fuente} -- {cobertura.means}",
                           "per run")
            )
    if report.arrival_coverage is not None:
        filas.append(
            DataSource(
                "Arrival-date coverage",
                f"the carrier reported an arrival for {report.arrival_coverage:.0%} of the "
                "orders read; the rest leave the OTIF denominator",
                "per run",
            )
        )
    for nota in report.notes:
        filas.append(DataSource("What this backend cannot measure", nota, "per run"))
    return tuple(filas)


#: Como se enuncia la evidencia del fill rate cuando ES la de todo, y cuando no lo es. La
#: segunda version existe porque la primera se vuelve FALSA en cuanto un transportista
#: aporta la fecha de llegada de solo una parte del libro: el fill rate sigue descansando en
#: todas las lineas servidas y el OTIF pasa a descansar en las que ademas tienen llegada.
_POR_QUE_LINEAS = "how much evidence every number above rests on"
_POR_QUE_LINEAS_FILL = (
    "how much evidence the fill rate and lost demand rest on. OTIF rests on fewer lines -- "
    "see the row below"
)
_POR_QUE_LINEAS_OTIF = (
    "lines carrying BOTH a promise and a delivery date: the only ones punctuality can be "
    "judged on. Lower than the row above because the carrier did not report every order"
)

_REC_CONFIRMAR_FECHA = (
    "Confirm with the operator what the backend's delivery date records: a warehouse "
    "dispatch and a customer arrival are not the same number."
)
_REC_COBERTURA = (
    "The OTIF above is measured on ARRIVAL, not dispatch, over the {cobertura:.0%} of "
    "orders the carrier reported. Review the unmatched orders before reading it as the "
    "whole book: they leave the punctuality denominator, not the fill-rate one."
)
_REC_SKUS = (
    "Work the worst-served SKUs above before raising any service-level target: a policy "
    "cannot fix a gap the policy did not cause."
)


def _kpis(report: ServiceScanReport) -> tuple[Kpi, ...]:
    """Los KPI de cartera. La explicacion va en ``rationale``, que es la columna que el
    renderizador titula "Why it matters" -- pasarla como tercer posicional la mandaba a
    "Target", donde se leia como una meta, y dejaba "Why it matters" vacia en las cuatro
    filas.

    La quinta fila solo aparece cuando los dos denominadores DIVERGEN. Agregarla siempre
    seria una fila que repite a la anterior y compite por la atencion con las cuatro que
    importan; no agregarla nunca deja al lector creyendo que el OTIF pesa lo mismo que el
    fill rate cuando puede descansar en la quinta parte de la evidencia.
    """
    p = report.portfolio
    divergen = p.lines_otif_scored != p.lines_scored
    filas = [
        Kpi("Fill rate", pct(p.fill_rate),
            rationale="units delivered / units ordered, over the lines that carried both"),
        Kpi("OTIF (perfect order)", pct(p.otif),
            rationale="lines delivered complete AND by the promised date (SCOR RL.1.1)"),
        Kpi("Demand not served", money(p.lost_sales_value),
            rationale="short units valued at the line's own price"),
        Kpi("Lines scored", f"{p.lines_scored:,}",
            rationale=_POR_QUE_LINEAS_FILL if divergen else _POR_QUE_LINEAS),
    ]
    if divergen:
        filas.append(
            Kpi("Lines scored for OTIF", f"{p.lines_otif_scored:,}",
                rationale=_POR_QUE_LINEAS_OTIF)
        )
    return tuple(filas)


def _recommendations(report: ServiceScanReport) -> tuple[str, ...]:
    """Que hacer. La primera cambia segun haya habido empalme, y no es cosmetica: sin
    transportista la pregunta abierta es QUE significa la fecha de entrega del backend, y
    con transportista esa ya esta contestada -- la que queda abierta es CUANTO del libro se
    pudo empalmar. Dejar la primera cuando el empalme ocurrio gasta la recomendacion que el
    cliente lee primero en una pregunta resuelta."""
    if report.arrival_coverage is None:
        return (_REC_CONFIRMAR_FECHA, _REC_SKUS)
    return (_REC_COBERTURA.format(cobertura=report.arrival_coverage), _REC_SKUS)


def build(
    report: ServiceScanReport,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: Sequence[str] = (),
    residual: str = "",
    confidence: float | None = None,
) -> Deliverable:
    """Arma el Deliverable del scan de servicio."""
    return Deliverable(
        title="Realized Customer Service",
        client=client,
        summary=_resumen(report),
        findings=tuple(_findings(report)),
        kpis=_kpis(report),
        data_sources=_data_sources(report),
        recommendations=_recommendations(report),
        citations=tuple(citations),
        confidence=confidence,
        residual=residual,
        prepared=prepared,
    )


def write_operational(report: ServiceScanReport, out_dir: str | Path, client: str = "Client") -> dict:
    """El CSV por SKU: la salida que un planificador abre y filtra.

    ``None`` se escribe como celda VACIA y nunca como 0. Un cero en una planilla se suma,
    se promedia y se grafica; una celda vacia no. Es la misma regla que el motor sostiene,
    en el unico formato donde el usuario puede romperla sin darse cuenta.
    """
    carpeta = Path(out_dir)
    carpeta.mkdir(parents=True, exist_ok=True)
    ruta = carpeta / "realized_service_by_sku.csv"
    columnas = ["product_id", "fill_rate", "otif", "lost_sales_units", "lost_sales_value",
                "days_out_of_stock", "lines_scored", "provenance", "method"]
    with ruta.open("w", newline="", encoding="utf-8") as f:
        escritor = csv.DictWriter(f, fieldnames=columnas)
        escritor.writeheader()
        for r in report.records:
            escritor.writerow(
                {c: ("" if getattr(r, c) is None else getattr(r, c)) for c in columnas}
            )
    return {"realized_service": ruta}
