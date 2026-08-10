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

El mapeo de columnas tampoco es propio: sale de ``jobs.intake.detect_columns``,
la misma tabla de alias que usa el resto de la ingesta. Un juego de tuplas
local habria sido el sexto normalizador del repo, y el sexto que se queda atras
cuando alguien agrega un alias en el que si se mantiene.

**La regla que hace honesta esta salida:** el plazo medido sale SIEMPRE de las
fechas de la recepcion. Una columna ``lead_time_days`` en un export es el campo
del ERP -- una promesa -- y usarla como observacion compara la promesa consigo
misma y devuelve un gap de cero sobre el defecto que veniamos a mostrar. Sin
fechas no se mide: se rechaza y se dice cuales faltan.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import pandas as pd

from jobs.intake import (
    RECEIPT_OPTIONAL,
    RECEIPT_REQUIRED,
    _parse_dates,
    detect_columns,
)
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

#: Como nombrar cada campo requerido cuando hay que decir cual falta. Un 400 que
#: dice `receipt_date` le sirve a quien escribio el CSV; uno que dice "fecha de
#: recepcion" le sirve a quien lo exporto del ERP, que es el visitante.
_ES = {
    "supplier": "proveedor",
    "order_date": "fecha de orden",
    "receipt_date": "fecha de recepcion",
}

REQUIRED_COLUMNS_HINT = (
    "proveedor, fecha de orden y fecha de recepcion (y opcional: "
    "plazo declarado en dias, product_id, po_id)"
)


@dataclass(frozen=True)
class LeadTimeScan:
    """Los scorecards comparables + el titular, con QA como una sola unidad."""

    scores: tuple[SupplierScore, ...]
    #: Recepciones descartadas y por que. Se reporta: un scan que analizo 40 de
    #: 200 filas y no lo dice es peor que uno que no corrio.
    dropped: dict[str, int]
    qa_issues: tuple[str, ...]
    findings: tuple[str, ...]
    #: Las recepciones que SI entraron, una por fila, con las dos fechas y el plazo
    #: que salio de restarlas. `to_deliveries` ya las calculaba y las tiraba: sin
    #: ellas el entregable pide creerle a un promedio, y 05-GTM-KERN §5 promete lo
    #: contrario -- "verificable en 5 min. Te ganas el derecho a seguir".
    #: Solo las de proveedores que califican, para que el total cuadre con
    #: ``headline["n_deliveries"]``.
    measured: tuple[dict, ...] = ()
    #: (proveedores, recepciones) que quedaron fuera por no llegar a
    #: ``MIN_DELIVERIES``. Es la cuarta causa de descarte, y la unica que
    #: ``dropped`` no veia: sin ella "total descartado + usadas" no suma las filas
    #: del archivo, y una reconciliacion que no cuadra se lee como un error.
    below_min: tuple[int, int] = (0, 0)

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


def relative_gap(score: SupplierScore) -> float | None:
    """|observado - declarado| / declarado. None cuando no hay con que comparar.

    El ``not score.declared_lead_time`` cubre None **y** cero, y esa segunda mitad
    es la que importa: un plazo declarado de 0 es el default de un ERP sin
    configurar, no una promesa de entrega inmediata, y dividir por el revienta.
    Publica para que el libro de Excel use ESTA definicion de "comparable" en vez
    de restatearla -- una segunda copia del guardia es como se reintroduce el bug.
    """
    if score.lead_time_gap is None or not score.declared_lead_time:
        return None
    return abs(score.lead_time_gap) / score.declared_lead_time


def signed_relative_gap(score: SupplierScore) -> float | None:
    """Igual, con signo: positivo = tarda mas de lo prometido. El signo ES el mensaje.

    Exige declarado **> 0**, y no solo distinto de cero como :func:`relative_gap`:
    ``to_deliveries`` acepta cualquier declarado finito, negativo incluido, y
    dividir por uno negativo invierte el signo -- o sea, muestra "llega antes"
    sobre un proveedor que llega tarde. Frente a un declarado roto el libro dice
    "no medido"; ``relative_gap`` conserva su semantica de siempre porque de ella
    dependen ``worst`` y los hallazgos del .md.
    """
    if score.lead_time_gap is None or not score.declared_lead_time:
        return None
    if score.declared_lead_time <= 0:
        return None
    return score.lead_time_gap / score.declared_lead_time


#: Alias interno historico. Los llamadores de este modulo lo usaban en privado.
_relative_gap = relative_gap


def to_deliveries(
    df: pd.DataFrame,
    overrides: dict[str, str] | None = None,
    *,
    aliases: Mapping[str, Sequence[str]] | None = None,
) -> tuple[dict[str, list[dict]], dict[str, int]]:
    """Filas de recepcion -> las que ``score_supplier`` come, agrupadas por proveedor.

    Devuelve tambien el conteo de descartes por causa. El plazo medido sale de
    ``receipt_date - order_date`` y de ningun otro lugar (ver el docstring del
    modulo). Levanta ValueError cuando falta una columna estructural, que es lo
    que el endpoint mapea a un 400 accionable. ``overrides`` es el escape para
    cuando el auto-mapeo elige mal: ``{"receipt_date": "Fecha 2"}``.
    """
    # Mismo mapeo por alias que el resto de la ingesta, con acentos plegados y
    # cabeceras en espanol: un export de Defontana o Contpaqi entra sin tocar nada.
    # ``aliases`` llega desde el pack del rubro (VerticalProfile.merged_aliases):
    # `razon_social_proveedor` ya esta en la tabla global, pero `representante` e
    # `importador` solo existen en el pack de repuestos, y `casa_matriz` solo en
    # el de distribucion.
    detected = detect_columns(
        df, overrides, required=RECEIPT_REQUIRED, optional=RECEIPT_OPTIONAL,
        aliases=aliases,
    )
    if detected.unmatched_required:
        faltan = ", ".join(_ES[c] for c in detected.unmatched_required)
        raise ValueError(
            f"faltan columnas: {faltan}. Sin fecha de orden y de recepcion el "
            "plazo no se puede medir, y el campo de plazo del ERP es la promesa "
            "que veniamos a contrastar, no una medicion. "
            f"Columnas vistas: {list(df.columns)[:12]}"
        )

    cols = detected.mapping
    supplier_col = cols["supplier"]
    declared_col = cols.get("lead_time_days")
    product_col = cols.get("product_id")
    po_col = cols.get("po_id")

    # `_parse_dates` y no `pd.to_datetime` a secas: sin `format="mixed"` pandas se
    # queda con el formato que infiere de las primeras filas y convierte en NaT
    # toda fila que venga en otro -- un export que mezcla ISO con dd/mm/yyyy
    # perderia recepciones en silencio, y aqui una recepcion perdida es plazo
    # observado que no se mide.
    ordered = _parse_dates(df[cols["order_date"]])
    received = _parse_dates(df[cols["receipt_date"]])
    days = (received - ordered).dt.days

    # ponytail: bucle fila-a-fila con `df.at`, ~10 s sobre 200k recepciones (medido).
    # Un export tipico de 12 meses para 5k SKU son decenas de miles de filas, bajo 1 s.
    # Si un cliente sube algo cerca del tope de 25 MB y la espera se nota, vectorizar
    # con `itertuples` o construir los grupos con `groupby` -- el limite lo pone esto,
    # no `score_supplier`.

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
        # Las dos fechas de las que salio `lead`, para que el entregable pueda
        # mostrar la recepcion que produjo el numero en vez de pedir que le crean
        # al promedio. `score_supplier` lee por clave, asi que ignora estas dos.
        row["order_date"] = ordered.loc[pos]
        row["receipt_date"] = received.loc[pos]
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


def run_lead_time_scan(
    df: pd.DataFrame,
    overrides: dict[str, str] | None = None,
    *,
    aliases: Mapping[str, Sequence[str]] | None = None,
) -> LeadTimeScan:
    """El scan completo: mapeo de columnas, medicion, scorecards, QA, hallazgos.

    Levanta ValueError (desde ``to_deliveries``) cuando faltan las columnas
    estructurales; el endpoint lo mapea a un 400 accionable.

    ``aliases`` es la tabla del rubro cuando el visitante declaro uno; None deja
    la global, que es el caso del visitante anonimo del embudo.
    """
    return scan_from_deliveries(*to_deliveries(df, overrides, aliases=aliases))


def scan_from_deliveries(
    grouped: dict[str, list[dict]],
    dropped: dict[str, int],
    extra_issues: tuple[str, ...] = (),
) -> LeadTimeScan:
    """El scan a partir de recepciones YA agrupadas, venga de donde venga.

    Existe separado de :func:`run_lead_time_scan` porque las recepciones no
    siempre llegan en un DataFrame subido: ``src.connectors.odoo.receipts()`` las
    lee de un Odoo vivo y emite exactamente estas filas. Sin este corte, esa
    capacidad seria una funcion del conector que nadie puede consumir.

    ``extra_issues`` es para lo que sabe la fuente y el scorecard no puede saber
    -- por ejemplo, que un Odoo mide contra el clic de validar la recepcion.
    """
    qualifies = {s: rows for s, rows in sorted(grouped.items()) if len(rows) >= MIN_DELIVERIES}
    scores = [score_supplier(supplier, rows) for supplier, rows in qualifies.items()]
    declared_seen = any(s.declared_lead_time is not None for s in scores)

    # La cuarta causa de descarte. `dropped` cuenta filas que no se pudieron leer;
    # esta cuenta filas que se leyeron bien y aun asi no entraron en ningun numero,
    # porque su proveedor no junta MIN_DELIVERIES. Sin contarla, "descartadas +
    # usadas" no suma las filas del archivo y la hoja de honestidad miente.
    short = {s: rows for s, rows in grouped.items() if len(rows) < MIN_DELIVERIES}
    below_min = (len(short), sum(len(rows) for rows in short.values()))

    # Solo las recepciones que SI entraron, para que el detalle cuadre con el titular.
    measured = tuple(
        {**row, "supplier": supplier} for supplier, rows in qualifies.items() for row in rows
    )

    qa_issues = _scan_issues(scores, declared_seen)
    findings = _findings(scores, dropped) if not qa_issues else ()
    # Los avisos de la fuente van DESPUES: no descalifican el scan (`ok` mira
    # `qa_issues`), asi que solo se suman cuando ya hay algo que reportar.
    if extra_issues and not qa_issues:
        findings = (*findings, *extra_issues)
    return LeadTimeScan(
        scores=tuple(scores),
        dropped=dropped,
        qa_issues=tuple(qa_issues),
        findings=findings,
        measured=measured,
        below_min=below_min,
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
