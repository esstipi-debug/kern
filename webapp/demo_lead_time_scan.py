"""Demo funnel, salida #1: un CSV de recepciones -> plazo real vs. plazo declarado.

La salida que 05-GTM-KERN §5 pone primera ("si solo pudieras entregar una,
entrega la #1") y la unica que se gana el derecho a seguir hablando, porque es un
hecho sobre los datos del visitante y no una opinion: el ERP dice 21 dias, las
ultimas 40 recepciones promedian 34 con desviacion de 11.

El motor ya existia entero en ``src/supplier_scorecard.py`` -- media, desviacion,
p90, declarado, gap firmado y los SKU cuyo SS/ROP quedo dimensionado contra la
promesa. Este modulo no calcula nada: arma las filas que ``score_supplier`` come
y compone el titular. Misma forma que ``webapp/demo_scan.py`` y
``webapp/demo_price_scan.py``: puro, sin I/O, con compuerta de QA -- si nada es
comparable no hay artefacto, y el issue dice que columna falta.

**La regla que hace honesta esta salida:** el plazo medido sale SIEMPRE de las
fechas de la recepcion. Una columna ``lead_time_days`` en un export es el campo
del ERP -- una promesa -- y usarla como observacion compara la promesa consigo
misma y devuelve un gap de cero sobre el defecto que veniamos a mostrar. Sin
fechas no se mide: se rechaza y se dice cuales faltan.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from src.supplier_scorecard import (
    LEAD_TIME_GAP_MATERIAL,
    LEAD_TIME_MEASURED,
    SupplierScore,
    score_supplier,
)

#: Menos de esto y la desviacion no es una medicion, es ruido: `_std` de dos
#: recepciones existe pero no describe a un proveedor.
MIN_DELIVERIES = 3

CTA_PATH = "/paquetes/diagnostico-arranque"

_ORDER_DATE_COLS = (
    "order_date", "po_date", "issue_date", "ordered_at", "date_order",
    "fecha_orden", "fecha_emision", "fecha_pedido",
)
_RECEIPT_DATE_COLS = (
    "receipt_date", "received_date", "delivery_date", "actual_delivery_date",
    "date_done", "fecha_recepcion", "fecha_entrega", "fecha_recibido",
)
_DECLARED_COLS = (
    "declared_lead_time_days", "promised_lead_time_days", "lead_time_days",
    "lead_time", "planned_lead_time", "delay", "plazo_dias", "plazo_declarado",
)
_SUPPLIER_COLS = (
    "supplier", "Supplier", "vendor", "Vendor", "supplier_name", "supplier_id",
    "partner_id", "proveedor",
)
_PRODUCT_COLS = ("product_id", "sku", "item", "codigo", "StockCode")
_PO_COLS = ("po_id", "po", "order_id", "purchase_order", "name", "oc")

REQUIRED_COLUMNS_HINT = (
    "proveedor, fecha de orden y fecha de recepcion (y opcional: "
    "plazo declarado en dias, product_id, po_id)"
)


def _pick(df: pd.DataFrame, candidates: tuple[str, ...]) -> str | None:
    return next((c for c in candidates if c in df.columns), None)


@dataclass(frozen=True)
class LeadTimeScan:
    """Los scorecards comparables + el titular, con QA como una sola unidad."""

    scores: tuple[SupplierScore, ...]
    #: Recepciones descartadas y por que. Se reporta: un scan que analizo 40 de
    #: 200 filas y no lo dice es peor que uno que no corrio.
    dropped: dict[str, int]
    qa_issues: tuple[str, ...]
    findings: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.qa_issues

    @property
    def worst(self) -> SupplierScore | None:
        """El proveedor cuyo gap relativo es mayor. None si ninguno es comparable."""
        comparables = [s for s in self.scores if _relative_gap(s) is not None]
        if not comparables:
            return None
        return max(comparables, key=lambda s: _relative_gap(s) or 0.0)

    @property
    def headline(self) -> dict:
        """Numeros JSON-safe para la UI. Solo significan algo cuando ok."""
        w = self.worst
        return {
            "n_suppliers": len(self.scores),
            "n_deliveries": sum(s.deliveries for s in self.scores),
            "n_stale": sum(1 for s in self.scores if s.skus_to_recalculate),
            "n_skus_to_recalculate": len(
                {sku for s in self.scores for sku in s.skus_to_recalculate}
            ),
            "worst_supplier": w.supplier if w else None,
            "worst_declared": round(w.declared_lead_time, 1) if w else None,
            "worst_observed": round(w.avg_lead_time, 1) if w else None,
            "worst_std": round(w.lead_time_std, 1) if w and w.lead_time_std else None,
            "worst_p90": round(w.lead_time_p90, 1) if w and w.lead_time_p90 else None,
        }


def _relative_gap(score: SupplierScore) -> float | None:
    """|observado - declarado| / declarado. None cuando no hay con que comparar."""
    if score.lead_time_gap is None or not score.declared_lead_time:
        return None
    return abs(score.lead_time_gap) / score.declared_lead_time


def to_deliveries(df: pd.DataFrame) -> tuple[dict[str, list[dict]], dict[str, int]]:
    """Filas de recepcion -> las que ``score_supplier`` come, agrupadas por proveedor.

    Devuelve tambien el conteo de descartes por causa. El plazo medido sale de
    ``receipt_date - order_date`` y de ningun otro lugar (ver el docstring del
    modulo). Levanta ValueError cuando falta una columna estructural, que es lo
    que el endpoint mapea a un 400 accionable.
    """
    supplier_col = _pick(df, _SUPPLIER_COLS)
    order_col = _pick(df, _ORDER_DATE_COLS)
    receipt_col = _pick(df, _RECEIPT_DATE_COLS)

    missing = [
        name
        for name, col in (
            ("proveedor", supplier_col),
            ("fecha de orden", order_col),
            ("fecha de recepcion", receipt_col),
        )
        if col is None
    ]
    if missing:
        raise ValueError(
            f"faltan columnas: {', '.join(missing)}. Sin fecha de orden y de "
            "recepcion el plazo no se puede medir, y el campo de plazo del ERP "
            "es la promesa que veniamos a contrastar, no una medicion. "
            f"Columnas vistas: {list(df.columns)[:12]}"
        )

    declared_col = _pick(df, _DECLARED_COLS)
    product_col = _pick(df, _PRODUCT_COLS)
    po_col = _pick(df, _PO_COLS)

    ordered = pd.to_datetime(df[order_col], errors="coerce")
    received = pd.to_datetime(df[receipt_col], errors="coerce")
    days = (received - ordered).dt.days

    # ponytail: bucle fila-a-fila con `df.at`, ~10 s sobre 200k recepciones (medido).
    # Un export tipico de 12 meses para 5k SKU son decenas de miles de filas, bajo 1 s.
    # Si un cliente sube algo cerca del tope de 25 MB y la espera se nota, vectorizar
    # con `itertuples` o construir los grupos con `groupby` -- el limite lo pone esto,
    # no `score_supplier`. Las fechas en formato mixto tambien caen a `dateutil` por
    # elemento (mismo aviso que jobs/sourcing_job.py:250).

    grouped: dict[str, list[dict]] = {}
    dropped = {"sin_fecha": 0, "plazo_negativo": 0, "sin_proveedor": 0}

    for pos in df.index:
        supplier = str(df.at[pos, supplier_col]).strip()
        if not supplier or supplier.lower() in {"nan", "none", "false"}:
            dropped["sin_proveedor"] += 1
            continue
        lead = days.loc[pos]
        if pd.isna(lead):
            dropped["sin_fecha"] += 1
            continue
        lead = float(lead)
        # Una recepcion anterior a su propia orden es un dato roto, no un plazo de
        # cero dias: promediarla baja el observado y disimula justo el gap.
        if lead < 0:
            dropped["plazo_negativo"] += 1
            continue

        row: dict = {"lead_time_days": lead, "lead_time_source": LEAD_TIME_MEASURED}
        if declared_col is not None:
            declared = pd.to_numeric(df.at[pos, declared_col], errors="coerce")
            if pd.notna(declared) and math.isfinite(float(declared)):
                row["declared_lead_time_days"] = float(declared)
        if product_col is not None and pd.notna(df.at[pos, product_col]):
            row["product_id"] = str(df.at[pos, product_col])
        if po_col is not None and pd.notna(df.at[pos, po_col]):
            row["po_id"] = str(df.at[pos, po_col])

        grouped.setdefault(supplier, []).append(row)

    return grouped, dropped


def _scan_issues(scores: list[SupplierScore], declared_seen: bool) -> list[str]:
    """Nunca un 'no' a secas: cada issue dice que falta y quien lo tiene."""
    issues: list[str] = []
    if not scores:
        issues.append(
            f"ningun proveedor llega a {MIN_DELIVERIES} recepciones con fechas "
            "validas: con menos, la desviacion del plazo es ruido y no describe "
            "al proveedor. Pedir a compras el export de recepciones de 12 meses."
        )
        return issues
    if not declared_seen:
        issues.append(
            "el export no trae el plazo declarado, asi que hay plazo observado "
            "pero nada contra que compararlo. Esta en la ficha del proveedor o "
            "en el maestro de articulos del ERP (campo 'lead time' / 'delay'); "
            "lo exporta el administrador de compras."
        )
    if not any(_relative_gap(s) is not None for s in scores):
        issues.append(
            "ningun proveedor tiene plazo declarado y observado a la vez: sin "
            "los dos no hay brecha que mostrar."
        )
    return issues


def _findings(scan_scores: list[SupplierScore], dropped: dict[str, int]) -> tuple[str, ...]:
    """Los hallazgos ejecutivos, en el orden del discurso de 05-GTM-KERN §5."""
    comparables = sorted(
        (s for s in scan_scores if _relative_gap(s) is not None),
        key=lambda s: _relative_gap(s) or 0.0,
        reverse=True,
    )
    findings: list[str] = []

    w = comparables[0]
    desviacion = (
        f", con desviacion de {w.lead_time_std:.0f}" if w.lead_time_std is not None else ""
    )
    findings.append(
        f"Tu sistema dice que {w.supplier} entrega en "
        f"{w.declared_lead_time:.0f} dias. Las ultimas {w.deliveries} recepciones "
        f"promedian {w.avg_lead_time:.0f}{desviacion}. "
        f"{'Tarda' if w.lead_time_gap > 0 else 'Llega'} "
        f"{abs(w.lead_time_gap):.0f} dias {'mas' if w.lead_time_gap > 0 else 'antes'} "
        "de lo que dice la ficha."
    )

    stale = [s for s in comparables if s.skus_to_recalculate]
    skus = {sku for s in stale for sku in s.skus_to_recalculate}
    if stale:
        findings.append(
            f"{len(stale)} de {len(comparables)} proveedores se desvian mas de "
            f"{LEAD_TIME_GAP_MATERIAL * 100:.0f}% de su plazo declarado. "
            f"{len(skus)} SKU(s) tienen stock de seguridad y punto de reorden "
            "dimensionados contra ese numero: hoy estan mal, en un sentido o en el otro."
        )
    else:
        findings.append(
            f"Ningun proveedor se desvia mas de {LEAD_TIME_GAP_MATERIAL * 100:.0f}% "
            "de su plazo declarado: los plazos del maestro se sostienen. El buffer "
            "mal puesto, si lo hay, viene de la variabilidad y no del promedio."
        )

    variables = [s for s in comparables if s.lead_time_std]
    if variables:
        v = max(variables, key=lambda s: s.lead_time_std or 0.0)
        findings.append(
            f"La mayor variabilidad es {v.supplier}: promedio {v.avg_lead_time:.0f} "
            f"dias, desviacion {v.lead_time_std:.0f}, y 1 de cada 10 recepciones "
            f"tarda {v.lead_time_p90:.0f} o mas. El stock de seguridad se dimensiona "
            "contra esa desviacion, no contra el promedio -- y el campo del ERP no la tiene."
        )

    descartes = sum(dropped.values())
    if descartes:
        detalle = ", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in dropped.items() if v)
        verbo = "Se descarto 1 fila" if descartes == 1 else f"Se descartaron {descartes} filas"
        findings.append(
            f"{verbo} del export ({detalle}). No entraron en ningun numero de arriba."
        )
    return tuple(findings)


def run_lead_time_scan(df: pd.DataFrame) -> LeadTimeScan:
    """El scan completo: sniff de columnas, medicion, scorecards, QA, hallazgos.

    Levanta ValueError (desde ``to_deliveries``) cuando faltan las columnas
    estructurales; el endpoint lo mapea a un 400 accionable.
    """
    grouped, dropped = to_deliveries(df)

    scores = [
        score_supplier(supplier, rows)
        for supplier, rows in sorted(grouped.items())
        if len(rows) >= MIN_DELIVERIES
    ]
    declared_seen = any(s.declared_lead_time is not None for s in scores)

    qa_issues = _scan_issues(scores, declared_seen)
    findings = _findings(scores, dropped) if not qa_issues else ()
    return LeadTimeScan(
        scores=tuple(scores),
        dropped=dropped,
        qa_issues=tuple(qa_issues),
        findings=findings,
    )


def render_mini_report(scan: LeadTimeScan, *, dataset_label: str, ts: str) -> str:
    """El mini-reporte persistido (markdown), misma forma que ``demo_scan``."""
    h = scan.headline
    lines = [
        "# Plazo de entrega real vs. el que dice tu sistema",
        "",
        f"- **Dataset:** {dataset_label}",
        f"- **Fecha:** {ts}",
        f"- **Proveedores medidos:** {h['n_suppliers']} "
        f"({h['n_deliveries']} recepciones)",
        "",
        "## Titular",
        "",
        f"**{h['worst_supplier']}: la ficha dice {h['worst_declared']:.0f} dias, "
        f"las recepciones dicen {h['worst_observed']:.0f}.** "
        f"{h['n_skus_to_recalculate']} SKU(s) tienen su stock de seguridad "
        "dimensionado contra el numero equivocado.",
        "",
        "## Plazo por proveedor",
        "",
        "| Proveedor | Recepciones | Declarado | Observado | Desviacion | p90 | Brecha |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for s in sorted(scan.scores, key=lambda s: _relative_gap(s) or -1.0, reverse=True):
        def _n(v: float | None, suffix: str = "") -> str:
            return f"{v:.0f}{suffix}" if v is not None else "no medido"

        lines.append(
            f"| {s.supplier} | {s.deliveries} | {_n(s.declared_lead_time)} | "
            f"{_n(s.avg_lead_time)} | {_n(s.lead_time_std)} | {_n(s.lead_time_p90)} | "
            f"{_n(s.lead_time_gap, ' d')} |"
        )
    lines += ["", "## Hallazgos", ""]
    lines += [f"{i}. {f}" for i, f in enumerate(scan.findings, start=1)]
    lines += [
        "",
        "## Siguiente paso",
        "",
        f"Diagnostico de Arranque: {CTA_PATH}",
        "",
        "*Medicion sobre las fechas del propio export de recepciones. "
        "'No medido' significa que el dato no venia, nunca que el valor sea cero.*",
    ]
    return "\n".join(lines) + "\n"


def render_followup_email(
    scan: LeadTimeScan, *, email: str, dataset_label: str
) -> str:
    """Borrador de seguimiento para el operador. Nunca se envia automaticamente."""
    h = scan.headline
    return "\n".join(
        [
            f"Para: {email}",
            f"Asunto: {h['worst_supplier']} entrega en {h['worst_observed']:.0f} dias, "
            f"no en {h['worst_declared']:.0f}",
            "",
            "Hola,",
            "",
            f"Corri la medicion de plazos sobre {dataset_label} "
            f"({h['n_deliveries']} recepciones, {h['n_suppliers']} proveedores). "
            "Tres numeros:",
            "",
            f"- {h['worst_supplier']}: la ficha dice {h['worst_declared']:.0f} dias; "
            f"las recepciones promedian {h['worst_observed']:.0f}"
            + (f", con desviacion de {h['worst_std']:.0f}" if h["worst_std"] else "")
            + ".",
            f"- {h['n_stale']} proveedor(es) se desvian lo suficiente como para que "
            f"los parametros armados sobre su plazo esten mal hoy.",
            f"- {h['n_skus_to_recalculate']} SKU(s) tienen stock de seguridad y punto "
            "de reorden dimensionados contra ese numero.",
            "",
            "No es un problema del proveedor: es que el sistema dimensiona contra su "
            "promesa en vez de contra su comportamiento. La desviacion del plazo ni "
            "siquiera existe como campo en el ERP, y en repuestos suele explicar la "
            "mayor parte del buffer que necesitas.",
            "",
            f"Detalle y alcance: {CTA_PATH}",
            "",
            "Si te sirve, respondeme y coordinamos 20 minutos para revisar los SKU "
            "afectados uno por uno.",
            "",
            "[FIRMA-OPERADOR]",
            "",
            "--",
            "BORRADOR generado por la medicion de plazos. Revisar y enviar a mano; "
            "Kern nunca envia correo automaticamente.",
        ]
    ) + "\n"
