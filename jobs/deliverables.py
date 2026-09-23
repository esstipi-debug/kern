"""Client deliverables — turn a JobReport into an Excel workbook + a report.

These are what the client receives: a recommendations workbook for action and a
written report with findings, methodology and assumptions for credibility.
"""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from src.export import write_summary_csv
from src.sanitize import defuse_formula

from .inventory_optimization import JobReport, SkuRecommendation
from .pricing import PricingRec, PricingReport

STATUS_LABEL = {"ok": "On track", "high_bias": "High bias — review", "review": "Intermittent — review"}
ACTION_LABEL = {
    "raise": "Raise price",
    "lower": "Lower price",
    "hold": "Hold price",
    "inelastic": "Inelastic — test higher",
    "insufficient_data": "Insufficient price variation",
}


#: Un campo dimensionado que el motor no pudo calcular llega en ``None``
#: (``jobs/inventory_optimization.py``). Las dos funciones de abajo son la UNICA
#: forma en que este modulo lo renderiza: celda vacia en la planilla, raya en el
#: markdown. Nunca un cero -- un cero en una columna de disparador o de tasa de
#: demanda es un numero sobre el que un operador actua.
def _redondear(valor: float | None, digitos: int) -> float | None:
    return None if valor is None else round(valor, digitos)


def _celda(valor: float | None, digitos: int) -> str:
    return "—" if valor is None else f"{valor:,.{digitos}f}"


def _row(r: SkuRecommendation, *, units: bool = False) -> dict:
    # ``units=True`` cuando el costo unitario se invento (1,0): la columna de valor
    # cambia de nombre para que la planilla diga lo que contiene, no lo que quisiera.
    return {
        "product_id": r.product_id,
        "method": r.method,
        "policy": r.policy_kind,
        # Celda VACIA, no 0: el mismo criterio que `on_order`. Un 0 en una planilla
        # es una tasa de demanda, y quien la abra no tiene como distinguirlo de
        # "este SKU no vende".
        "forecast_per_period": _redondear(r.forecast, 2),
        "order_qty_Q": round(r.order_quantity, 2) if r.order_quantity is not None else "",
        # Supplier terms. `order_qty_Q` above is the lot the supplier ACCEPTS;
        # these say what the EOQ asked for, which rule moved it, what honouring
        # it costs per year, and the other legal lot with its own cost - the
        # second terminal, so a buyer reading the sheet always has two numbers
        # to choose from instead of one rounded one. Blank where no rule exists.
        "order_qty_unconstrained": (
            round(r.order_quantity_unconstrained, 2)
            if r.order_quantity_unconstrained is not None else ""
        ),
        "supplier_lot_rule": r.lot_rule,
        "lot_penalty_usd_yr": round(r.lot_penalty_usd, 2) if r.lot_rule else "",
        "lot_alternative_Q": (
            round(r.lot_alternative_qty, 2) if r.lot_alternative_qty is not None else ""
        ),
        "lot_alternative_penalty_usd_yr": (
            round(r.lot_alternative_penalty_usd, 2)
            if r.lot_alternative_penalty_usd is not None else ""
        ),
        "order_up_to_S": round(r.order_up_to, 2) if r.order_up_to is not None else "",
        "reorder_point_s": _redondear(r.reorder_point, 2),
        # Solo en una cobertura provisional: la media medida y los periodos sobre los
        # que se extendio. Van al lado del numero para que la planilla muestre la
        # cuenta -- cobertura = media x periodos -- en vez de un valor sin origen.
        "provisional_mean_per_period": _redondear(r.observed_mean_per_period, 2),
        "provisional_cover_periods": _redondear(r.cover_periods, 3),
        # Today's stock position and the execution signal. Blank, never 0, where
        # nothing was measured: a blank on_order says "nobody told us", a 0 would
        # say "measured, nothing inbound" - and the two lead to different POs.
        "on_hand": round(r.on_hand, 2) if r.on_hand is not None else "",
        "on_order": round(r.on_order, 2) if r.on_order is not None else "",
        "inventory_position": (
            round(r.inventory_position, 2) if r.inventory_position is not None else ""
        ),
        "order_now": ("yes" if r.order_now else "no") if r.order_now is not None else "",
        "order_qty_now": round(r.order_qty_now, 2) if r.order_qty_now is not None else "",
        # Fecha de fin de vida comercial y que le hizo a esta linea (src/horizon.py).
        # `horizon_verdict` viaja SIEMPRE, tambien cuando dice `expired` o `unusable`: el
        # comprador que abre la planilla tiene que poder ver que el archivo traia una
        # fecha que no se aplico, en la fila del SKU y no solo en un residual del resumen.
        # Los `*_before_horizon` van en blanco -- nunca en cero -- cuando ese numero no
        # se movio, por la misma razon que `on_order`: un cero afirmaria un cambio.
        "end_date": r.end_date or "",
        "horizon_verdict": r.horizon_verdict,
        "remaining_demand": _redondear(r.remaining_demand, 2),
        "reorder_point_s_before_horizon": _redondear(r.reorder_point_before_horizon, 2),
        "order_up_to_S_before_horizon": _redondear(r.order_up_to_before_horizon, 2),
        "order_qty_before_horizon": _redondear(r.order_quantity_before_horizon, 2),
        "safety_stock": _redondear(r.safety_stock, 2),
        "sigma_e": round(r.error_std, 2),
        # Which dispersion actually sized this row's buffer, and under which
        # distribution - so the CSV can be reconciled against the stated formula.
        "sigma_source": r.sigma_source,
        "buffer_distribution": r.buffer_distribution,
        "risk_periods_tau": round(r.risk_periods, 3),
        "lead_time_std": round(r.lead_time_std, 3),
        "lead_time_std_source": r.lead_time_std_source,
        "bias": round(r.bias, 2),
        "mae": round(r.mae, 2),
        "unit_cost": round(r.unit_cost, 2),
        ("inventory_units_no_cost" if units else "inventory_value"): round(r.investment, 2),
        "status": STATUS_LABEL[r.status],
        # Service, with the model's number and the client's kept in separate columns.
        # `service_level_target` is what the policy above is sized for; the
        # `observed_*` columns are what customers actually got, and
        # `service_provenance` says which of the two any given row rests on. A
        # spreadsheet is exactly where these get confused, so they never share a name.
        "service_level_target": round(r.service_level, 4),
        "model_implied_beta": (
            round(r.model_implied_beta, 4) if r.model_implied_beta is not None else ""
        ),
        **_service_columns(r.realized_service),
    }


def _service_columns(service) -> dict:
    """The realized-service columns, blank where nothing was measured.

    Blank, never 0: a zero in a fill-rate column is a total stockout, and printing
    one for a SKU whose service nobody measured is the same lie in a cell that
    ``src.deliverable.not_measured`` exists to prevent on a page.
    """
    if service is None:
        return {
            "observed_fill_rate": "", "observed_otif": "", "days_out_of_stock": "",
            "lost_sales_units": "", "lost_sales_value": "", "service_provenance": "",
        }
    number = lambda v, d=4: round(v, d) if v is not None else ""  # noqa: E731
    return {
        "observed_fill_rate": number(service.fill_rate),
        "observed_otif": number(service.otif),
        "days_out_of_stock": service.days_out_of_stock if service.days_out_of_stock is not None else "",
        "lost_sales_units": number(service.lost_sales_units, 2),
        "lost_sales_value": number(service.lost_sales_value, 2),
        "service_provenance": service.provenance,
    }


def write_csv(report: JobReport, path: str | Path) -> Path:
    """One row per SKU — the machine-readable deliverable."""
    return write_summary_csv([_row(r, units=cost_is_synthesized(report)) for r in report.recommendations], path)


def write_excel(report: JobReport, path: str | Path) -> Path:
    """A two-sheet workbook: Recommendations + Summary & Assumptions."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()

    ws = wb.active
    ws.title = "Recommendations"
    rows = [_row(r, units=cost_is_synthesized(report)) for r in report.recommendations]
    headers = list(rows[0].keys()) if rows else ["product_id"]
    header_fill = PatternFill("solid", fgColor="1F2A44")
    for c, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=c, value=h.replace("_", " "))
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
    for ri, row in enumerate(rows, 2):
        for ci, h in enumerate(headers, 1):
            ws.cell(row=ri, column=ci, value=defuse_formula(row[h]))
    for ci, h in enumerate(headers, 1):
        width = max(len(h), *(len(str(row[h])) for row in rows)) if rows else len(h)
        ws.column_dimensions[get_column_letter(ci)].width = min(max(width + 2, 10), 32)
    ws.freeze_panes = "A2"

    s = wb.create_sheet("Summary & Assumptions")
    p = report.params
    # Misma regla que el .md: sin costo cargado, la unidad de los montos es
    # "unidades", y el rotulo lo dice en la misma celda para que no haya que
    # leer una nota aparte.
    inv = " (units, no unit cost loaded)" if cost_is_synthesized(report) else " ($)"
    summary = [
        ("Inventory Optimization — Summary", ""),
        *([("Note", UNITS_NOTE)] if cost_is_synthesized(report) else []),
        ("SKUs analyzed", report.n_skus),
        ("SKUs flagged (high bias)", report.n_at_risk),
        ("Intermittent SKUs", report.n_intermittent),
        *([("SKUs on supplier terms (MOQ / case pack)",
            f"{report.n_lot_constrained} ({report.n_lot_snapped} moved off the EOQ)"),
           (f"Cost of honouring supplier terms{inv}/yr", round(report.lot_penalty_total_usd, 2))]
          if report.n_lot_constrained else []),
        *([("SKUs with a stock position on file",
            f"{report.n_positioned} ({report.n_netted} netted against stock on order)"),
           ("Orders due now (position at or below trigger)", report.n_order_now),
           ("Re-orders avoided by counting in-transit stock", report.n_reorders_avoided_by_transit)]
          if report.n_positioned else []),
        ("Forecast methods used", _methods_used(report) or "none"),
        (f"Requested investment{inv}", round(report.requested_investment, 2)),
        (f"Cycle-stock floor{inv}", round(report.cycle_floor, 2)),
        (f"Final investment{inv}", round(report.final_investment, 2)),
        ("Budget cap", report.budget if report.budget is not None else "none"),
        ("Safety-stock scale applied", f"{report.safety_stock_scale * 100:.0f}%"),
        ("Plan feasible", "yes" if report.feasible else "no"),
        ("", ""),
        ("Assumptions", ""),
        ("Cycle service level", _service_level_text(report)),
        ("Holding cost rate (of unit cost / yr)", f"{p['holding_rate'] * 100:.0f}%"),
        ("Order cost K", p["order_cost"]),
        ("Periods per year", p["periods_per_year"]),
    ]
    for ri, (k, v) in enumerate(summary, 1):
        kc = s.cell(row=ri, column=1, value=defuse_formula(k))
        if v == "" and k:
            kc.font = Font(bold=True)
        s.cell(row=ri, column=2, value=defuse_formula(v))
    s.column_dimensions["A"].width = 40
    s.column_dimensions["B"].width = 22

    _write_reading_sheet(wb, report)

    wb.save(out)
    return out


def _orders_due_now_lines(report: JobReport) -> list[str]:
    """The execution list: which SKUs sit at or below their trigger TODAY, sized
    against the position (on hand + on order). Empty when the file carried no stock
    on hand - the policy table above then says how much, never when."""
    if not report.n_positioned:
        return []
    lines = ["## Orders due now\n"]
    lines.append(
        f"{report.n_positioned} SKU(s) came with stock on hand; {report.n_netted} also with "
        f"stock already on order, which the position counts. **{report.n_order_now} SKU(s) "
        f"are at or below their trigger today**; {report.n_reorders_avoided_by_transit} "
        "would have been re-ordered on the shelf figure alone but are covered by stock "
        "already inbound."
    )
    if report.n_netted == 0:
        lines.append(
            "\n> ⚠️ **In-transit stock NOT counted.** The file has no on-order column, so an "
            "order transport delayed looks like one that does not exist. Check open POs by "
            "hand before cutting any order below, or add an `on_order` / `en_transito` column."
        )
    due = [r for r in report.recommendations if r.order_now]
    if due:
        lines.append("")
        lines.append("| SKU | Policy | On hand | On order | Position | Trigger | Order now |")
        lines.append("|---|---|---:|---:|---:|---:|---:|")
        for r in sorted(due, key=lambda x: (x.inventory_position or 0.0) - x.reorder_point)[:50]:
            trigger = r.reorder_point if r.policy_kind == "(s, Q)" else (r.order_up_to or 0.0)
            on_order = f"{r.on_order:,.0f}" if r.on_order is not None else "—"
            lines.append(
                f"| {r.product_id} | {r.policy_kind} | {r.on_hand:,.0f} | {on_order} | "
                f"{r.inventory_position:,.0f} | {trigger:,.0f} | {r.order_qty_now:,.0f} |"
            )
    if report.position_note:
        lines.append("")
        lines.append(f"_{report.position_note}_")
    lines.append("")
    return lines


def cost_is_synthesized(report: JobReport) -> bool:
    """True cuando NINGUNA fila trajo costo unitario y el motor corrio con 1,0 por defecto.

    Con costo 1,0, "inversion" es literalmente unidades: 244 unidades salian impresas
    como "$244" y un contador lo desarma en una pregunta. Es la grieta de honestidad
    #1 del diagnostico del 2026-09-05 (``jobs/intake.py`` rellena ``unit_cost`` y
    ``IntakeQuality.synthesized`` lo cuenta; el aviso interno moria antes del deck).
    Aca se decide con que unidad se escribe cada monto de inversion del entregable.
    """
    # getattr: solo el JobReport de inventario lleva intake_quality; un reporte de
    # otro job (precios) no tiene costo inventado que declarar.
    quality = getattr(report, "intake_quality", None)
    return quality is not None and "unit_cost" in quality.fully_synthesized_columns


def amount(report: JobReport, value: float) -> str:
    """``$1,234`` si el costo vino del archivo; ``1,234 u.`` si se invento (ver arriba)."""
    return f"{_fmt(value)} u." if cost_is_synthesized(report) else f"${_fmt(value)}"


UNITS_NOTE = (
    "Sin columna de costo unitario en el archivo: los montos de inversion de este "
    "informe estan en UNIDADES (el motor corrio con costo 1,0 por defecto), no en "
    "dinero. Carga `unit_cost` por SKU para verlos en plata."
)


def _fmt(n: float) -> str:
    return f"{n:,.0f}"


# How each forecast method is named to a client. The Methodology section lists the
# labels for the methods that *ran*; asserting a fixed pair here is what let the
# report claim "simple exponential smoothing (or Croston)" long after
# src/forecasting.py started routing to AutoETS/TSB.
METHOD_LABEL = {
    "ses": "simple exponential smoothing (SES)",
    "croston": "Croston's method",
    "moving_average": "moving average",
    "auto_ets": "AutoETS (StatsForecast)",
    "tsb": "TSB (Teunter-Syntetos-Babai)",
}

_SIGMA_LABEL = {
    "forecast_error": "the forecast-error standard deviation (sigma_e)",
    "demand_std_fallback": "the raw demand standard deviation, because sigma_e could not "
                           "be estimated for that SKU",
    "unavailable": "no dispersion estimate at all",
}


def _plural(items: list[str]) -> str:
    """Join labels for prose: 'a', 'a and b', 'a, b and c'."""
    if len(items) <= 1:
        return items[0] if items else ""
    return ", ".join(items[:-1]) + " and " + items[-1]


def _methods_used(report: JobReport) -> str:
    """The forecast methods that actually ran, named as a client would read them."""
    methods = sorted({r.method for r in report.recommendations})
    return _plural([METHOD_LABEL.get(m, m) for m in methods])


def _service_level_text(report: JobReport) -> str:
    """The cycle service level(s) the run actually applied, read off the SKUs."""
    levels = sorted({round(r.service_level, 4) for r in report.recommendations})
    if not levels:
        return f"{report.params['service_level'] * 100:.1f}%"
    if len(levels) == 1:
        return f"{levels[0] * 100:.1f}%"
    per_sku = ", ".join(f"{lv * 100:.1f}%" for lv in levels)
    return f"differentiated per SKU across {len(levels)} levels ({per_sku})"


def _service_level_clause(report: JobReport) -> str:
    """The service-level clause of the executive summary, read off the SKUs.

    Same rule as the Assumptions bullet, and for the same reason: quoting
    ``params['service_level']`` states the *default argument*, which
    ``service_levels=`` / ``differentiate_by_class=`` / ``observed_fill_rates=``
    each override per SKU. A run differentiated across 90% and 99% used to open
    with "at a 95% cycle service level" -- a figure no SKU ran at, contradicting
    the Assumptions section of the same document three sections below.
    """
    levels = sorted({round(r.service_level, 4) for r in report.recommendations})
    if len(levels) == 1:
        return f", at a **{levels[0] * 100:.1f}%** cycle service level. "
    if not levels:  # no SKU survived the run; the default is all there is to state
        return f", at a **{report.params['service_level'] * 100:.1f}%** cycle service level. "
    per_sku = ", ".join(f"{lv * 100:.1f}%" for lv in levels)
    return (
        f", at cycle service levels **differentiated per SKU** across "
        f"{len(levels)} levels ({per_sku}). "
    )


def _methodology_lines(report: JobReport) -> list[str]:
    """Describe the run that happened - read off the recommendations it produced."""
    recs = report.recommendations
    methods = sorted({r.method for r in recs})
    distributions = sorted({r.buffer_distribution for r in recs})
    policies = sorted({r.policy_kind for r in recs})

    method_text = _plural([METHOD_LABEL.get(m, m) for m in methods]) or "no SKU was forecast"
    # The split is only worth spelling out when more than one method actually ran.
    counts = (
        " (" + ", ".join(
            f"{METHOD_LABEL.get(m, m)}: {sum(1 for r in recs if r.method == m)} SKU(s)"
            for m in methods
        ) + ")"
        if len(methods) > 1 else ""
    )

    lines = ["## Methodology\n"]
    lines.append(
        f"- **Forecast:** per-SKU demand history was forecast with {method_text}{counts}. "
        "The method is selected per SKU from the demand pattern, not fixed in advance."
    )
    lines.append(
        "- **Dispersion:** safety stock is sized on the forecast-error standard deviation "
        "sigma_e where it could be estimated, and on the raw demand standard deviation "
        "where it could not (Vandeput 2021, Sec. 4.2.5). Per-SKU source in the "
        "`sigma_source` column of summary.csv."
    )
    lines.append(
        "- **Risk-period demand:** `sigma_x = sqrt(tau * sigma_d^2 + sigma_L^2 * mu_d^2)`, "
        "where `tau = L` for (s,Q) and `tau = L + R` for (R,S), `sigma_d` is the dispersion "
        "above and `sigma_L` the lead-time standard deviation (Vandeput 2020, eq. 6.4-6.5)."
    )
    if distributions == ["normal"]:
        lines.append(
            "- **Safety stock:** `SS = z * sigma_x` under a normal risk-period demand, with "
            "`z` from the target cycle service level."
        )
    elif distributions == ["gamma"]:
        lines.append(
            "- **Safety stock:** fitted to a **gamma** risk-period demand (Ch. 9), "
            "`SS = iota_gamma - mu_x`; `z` does not describe this buffer."
        )
    else:
        lines.append(
            "- **Safety stock:** `SS = z * sigma_x` under a normal risk-period demand for "
            f"{sum(1 for r in recs if r.buffer_distribution == 'normal')} SKU(s), and a fitted "
            f"**gamma** buffer `SS = iota_gamma - mu_x` for "
            f"{sum(1 for r in recs if r.buffer_distribution == 'gamma')} SKU(s) (Ch. 9)."
        )
    lines.append(
        "- **Order quantity:** Economic Order Quantity `Q* = sqrt(2*D*K/H)` for "
        "continuous-review (s,Q); periodic (R,S) order-up-to `S = mu_x + SS`."
        + (f" Policies used here: {_plural(policies)}." if policies else "")
    )
    lines.append(
        "- **Reorder point:** `s = mu_d * L + SS` - the lead-time mean plus the buffer. "
        "Note this uses `L` alone, not the `tau` above: for an (R,S) SKU the review period "
        "is covered by the order-up-to level `S`, not by `s`."
    )
    lines.append(
        "- **Budget:** when a cap is set, safety stock is scaled across the portfolio to fit "
        "while preserving cycle-stock economics."
    )
    lines.append("- Models from Vandeput (2020), *Inventory Optimization: Models and Simulations*.\n")
    return lines


def _assumption_lines(report: JobReport) -> list[str]:
    """State the inputs the run actually used, and which of them were defaults."""
    p = report.params
    recs = report.recommendations
    from_file = sorted({r.lead_time_std for r in recs if r.lead_time_std_source == "client_file"})
    defaulted = [r for r in recs if r.lead_time_std_source == "defaulted_zero"]

    lines = ["## Assumptions\n"]
    # `params['service_level']` is the *default argument*, not necessarily what ran:
    # service_levels= / differentiate_by_class= / observed_fill_rates= each override it
    # per SKU, and stating one number here would describe a run that did not happen.
    lines.append(f"- Cycle service level: **{_service_level_text(report)}**")
    lines.append(f"- Holding cost: **{p['holding_rate'] * 100:.0f}%** of unit cost per year")
    lines.append(f"- Fixed order cost (K): **${_fmt(p['order_cost'])}**")
    lines.append(
        f"- Demand bucketed into **{int(p['periods_per_year'])} periods/year**; lead time "
        "taken from the data (or a default where absent)."
    )

    # sigma_L is the assumption most likely to be wrong and least likely to be noticed:
    # a defaulted 0 asserts the supplier never varies, which shrinks every buffer.
    if defaulted and not from_file:
        lines.append(
            "- Lead-time standard deviation (sigma_L): **defaulted to 0** for all "
            f"{len(defaulted)} SKU(s) - this data carries no lead-time variability, so the "
            "buffers assume a perfectly reliable lead time and are a floor, not a forecast."
        )
    elif from_file and not defaulted:
        values = ", ".join(f"{v:g}" for v in from_file)
        lines.append(
            f"- Lead-time standard deviation (sigma_L): **from your file** ({values} periods)."
        )
    else:
        values = ", ".join(f"{v:g}" for v in from_file)
        lines.append(
            f"- Lead-time standard deviation (sigma_L): **from your file** ({values} periods) for "
            f"{len(recs) - len(defaulted)} SKU(s); **defaulted to 0** for the other "
            f"{len(defaulted)}, whose buffers assume a perfectly reliable lead time."
        )

    fallbacks = [r for r in recs if r.sigma_source == "demand_std_fallback"]
    unavailable = [r for r in recs if r.sigma_source == "unavailable"]
    if fallbacks:
        lines.append(
            f"- Dispersion source: {len(fallbacks)} of {len(recs)} SKU(s) were sized on "
            f"{_SIGMA_LABEL['demand_std_fallback']} "
            f"({', '.join(r.product_id for r in fallbacks[:5])}"
            + (", ..." if len(fallbacks) > 5 else "")
            + ") - a wider buffer than the theory calls for."
        )
    elif not unavailable:
        lines.append(
            f"- Dispersion source: every SKU was sized on {_SIGMA_LABEL['forecast_error']}."
        )
    if unavailable:
        lines.append(
            f"- Dispersion source: {len(unavailable)} SKU(s) had {_SIGMA_LABEL['unavailable']}; "
            "their buffers are zero by arithmetic, not by judgement."
        )
    lines.append("")
    return lines


def _reading_lines(report: JobReport) -> list[str]:
    """How Kern read the source file. Empty when the untracked path ran.

    Lives after Assumptions on purpose: those are the parameters the client
    chose; this is what we took their file to mean. A mapping that stays in
    the operator's head is how a TOTAL row becomes a sale.
    """
    quality = report.intake_quality
    if quality is None or quality.reading is None:
        return []
    reading = quality.reading
    lines = ["## How Kern read this file\n"]
    lines.append(f"- **Location:** {reading.location_text()}")
    lines.append(f"- **Column mapping:** {reading.mapping_text()}")
    if reading.noteworthy_hygiene():
        lines.append(f"- **Spreadsheet hygiene:** {reading.hygiene_text()}")
    if quality.n_dropped:
        lines.append(
            f"- **Rows dropped after mapping:** {quality.n_dropped} of {quality.n_raw} "
            f"({quality.dropped_fraction:.0%}) -- "
            f"{quality.n_dropped_bad_date} bad date, "
            f"{quality.n_dropped_bad_quantity} unreadable quantity, "
            f"{quality.n_dropped_negative_quantity} negative quantity."
        )
    if quality.synthesized:
        filled = "; ".join(f"{name}: {n} cell(s)" for name, n in quality.synthesized)
        lines.append(f"- **Columns filled from defaults:** {filled}")
    lines.append("")
    return lines


def _write_reading_sheet(wb: Workbook, report: JobReport) -> None:
    """A sheet the client can audit against their own file. Skipped if untracked."""
    quality = report.intake_quality
    if quality is None or quality.reading is None:
        return
    reading = quality.reading
    ws = wb.create_sheet("How Kern read this")
    header_fill = PatternFill("solid", fgColor="1F2A44")
    ws.cell(row=1, column=1, value="What").font = Font(bold=True, color="FFFFFF")
    ws.cell(row=1, column=2, value="Kern's reading").font = Font(bold=True, color="FFFFFF")
    ws.cell(row=1, column=1).fill = header_fill
    ws.cell(row=1, column=2).fill = header_fill
    rows = [
        ("Location", reading.location_text()),
        ("Column mapping", reading.mapping_text()),
    ]
    if reading.noteworthy_hygiene():
        rows.append(("Spreadsheet hygiene", reading.hygiene_text()))
    if quality.n_dropped:
        rows.append((
            "Rows dropped after mapping",
            f"{quality.n_dropped} of {quality.n_raw} ({quality.dropped_fraction:.0%}): "
            f"{quality.n_dropped_bad_date} bad date, "
            f"{quality.n_dropped_bad_quantity} unreadable quantity, "
            f"{quality.n_dropped_negative_quantity} negative quantity",
        ))
    if quality.synthesized:
        filled = "; ".join(f"{name}: {n} cell(s)" for name, n in quality.synthesized)
        rows.append(("Columns filled from defaults", filled))
    for ri, (k, v) in enumerate(rows, 2):
        ws.cell(row=ri, column=1, value=defuse_formula(k))
        ws.cell(row=ri, column=2, value=defuse_formula(v))
    ws.column_dimensions["A"].width = 32
    ws.column_dimensions["B"].width = 88
    ws.freeze_panes = "A2"


def write_report_md(report: JobReport, path: str | Path, *, client: str = "Client") -> Path:
    """A written report: summary, recommendations, findings, methodology."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    over = report.budget is not None and report.requested_investment > report.budget

    lines: list[str] = []
    lines.append(f"# Inventory Optimization — {client}\n")
    lines.append("## Executive summary\n")
    if cost_is_synthesized(report):
        lines.append(f"> {UNITS_NOTE}\n")
    lines.append(
        f"Analyzed **{report.n_skus} SKUs**. Recommended inventory investment is "
        f"**{amount(report, report.final_investment)}**"
        + (f" against a budget of **{amount(report, report.budget)}**" if report.budget is not None else "")
        + _service_level_clause(report)
        + (f"{report.n_at_risk} SKU(s) show high forecast bias and {report.n_intermittent} are intermittent (review recommended)."
           if (report.n_at_risk or report.n_intermittent) else "All SKUs are on track.")
    )
    lines.append("")
    if report.budget is not None:
        if not report.feasible:
            lines.append(f"> ⚠️ **Infeasible at this budget.** The cycle-stock floor alone is {amount(report, report.cycle_floor)}; raise the cap above it to fund any safety stock.\n")
        elif report.safety_stock_scale < 1:
            lines.append(f"> Safety stock scaled to **{report.safety_stock_scale * 100:.0f}%** to fit the budget. Raise the cap to {amount(report, report.requested_investment)} to fund it fully.\n")
        elif over:
            lines.append("")

    lines.append("## Recommended policy per SKU\n")
    lines.append("| SKU | Method | Policy | Forecast/period | Order qty (Q*) | Order-up-to (S) | Reorder point (s) | Safety stock | Inv. value | Status |")
    lines.append("|---|---|---|---:|---:|---:|---:|---:|---:|---|")
    for r in report.recommendations:
        q = f"{r.order_quantity:,.0f}" if r.order_quantity is not None else "—"
        s_up = f"{r.order_up_to:,.0f}" if r.order_up_to is not None else "—"
        lines.append(
            f"| {r.product_id} | {r.method} | {r.policy_kind} | {_celda(r.forecast, 1)} | {q} | {s_up} | "
            f"{_celda(r.reorder_point, 0)} | {_celda(r.safety_stock, 1)} | "
            f"{amount(report, r.investment)} | {STATUS_LABEL[r.status]} |"
        )
    lines.append("")
    if report.n_lot_constrained:
        lines.append(
            f"{report.n_lot_constrained} (s,Q) SKU(s) carry supplier terms (MOQ / case-pack "
            f"multiple): Q* above is the cheapest lot the supplier accepts, and "
            f"{report.n_lot_snapped} of them moved off the unconstrained EOQ at a cost of "
            f"{amount(report, report.lot_penalty_total_usd)}/yr in ordering + holding. The "
            "unconstrained Q, the rule, the penalty and the runner-up lot are on each row of "
            "the workbook (`order_qty_unconstrained`, `supplier_lot_rule`, "
            "`lot_penalty_usd_yr`, `lot_alternative_Q`).\n"
        )

    lines.extend(_orders_due_now_lines(report))

    flagged = [r for r in report.recommendations if r.status != "ok"]
    if flagged:
        lines.append("## Findings & flags\n")
        for r in flagged:
            if r.status == "high_bias":
                lines.append(f"- **{r.product_id}** — forecast bias {r.bias:+.1f} (|bias| ≥ 2). The forecast is consistently off; review the demand history or method before trusting the policy.")
            else:
                lines.append(
                    f"- **{r.product_id}** — intermittent demand, forecast via "
                    f"{METHOD_LABEL.get(r.method, r.method)}. Lumpy demand makes a periodic "
                    "(R,S) review more robust than a fixed reorder point."
                )
        lines.append("")

    lines.extend(_methodology_lines(report))
    lines.extend(_assumption_lines(report))
    lines.extend(_reading_lines(report))
    lines.append("_Generated from the client's demand data. Figures are decision support; validate cost and lead-time inputs against your systems before ordering._")

    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def write_all(report: JobReport, out_dir: str | Path, *, client: str = "Client") -> dict[str, Path]:
    """Write the full deliverable set into out_dir."""
    d = Path(out_dir)
    return {
        "excel": write_excel(report, d / "inventory_plan.xlsx"),
        "report": write_report_md(report, d / "report.md", client=client),
        "csv": write_csv(report, d / "summary.csv"),
    }


# ---- pricing deliverables ----------------------------------------------------

def _price_row(r: PricingRec) -> dict:
    return {
        "product_id": r.product_id,
        "current_price": round(r.current_price, 2),
        "optimal_price": round(r.optimal_price, 2) if r.optimal_price is not None else "",
        "unit_cost": round(r.unit_cost, 2),
        "elasticity": round(r.elasticity, 2),
        "r_squared": round(r.r_squared, 2),
        "obs": r.n_points,
        "demand_change_pct": round(r.demand_change_pct, 1) if r.demand_change_pct is not None else "",
        "profit_uplift_pct": round(r.profit_uplift_pct, 1) if r.profit_uplift_pct is not None else "",
        "action": ACTION_LABEL[r.action],
        "confident": "yes" if r.confident else "no",
    }


def write_pricing_csv(report: PricingReport, path: str | Path) -> Path:
    return write_summary_csv([_price_row(r) for r in report.recommendations], path)


def write_pricing_report_md(report: PricingReport, path: str | Path, *, client: str = "Client") -> Path:
    """Written price-optimization report: summary, per-SKU table, methodology."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    lines.append(f"# Price Optimization — {client}\n")
    lines.append("## Executive summary\n")
    lines.append(
        f"Analyzed **{report.n_skus} SKUs**. **{report.n_actionable}** have a confident price move "
        f"(raise/lower); {report.n_inelastic} are inelastic and {report.n_insufficient} lack enough "
        "price variation to estimate. Recommendations maximize unit margin under a constant-elasticity "
        "demand model fitted to each SKU's price/quantity history."
    )
    lines.append("")
    lines.append("## Recommended price per SKU\n")
    lines.append("| SKU | Current | Optimal | Unit cost | Elasticity | R² | Obs | Δ demand | Profit uplift | Action | Confident |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|")
    for r in report.recommendations:
        opt = f"${r.optimal_price:,.2f}" if r.optimal_price is not None else "—"
        dchg = f"{r.demand_change_pct:+.0f}%" if r.demand_change_pct is not None else "—"
        upl = f"{r.profit_uplift_pct:+.0f}%" if r.profit_uplift_pct is not None else "—"
        lines.append(
            f"| {r.product_id} | ${r.current_price:,.2f} | {opt} | ${r.unit_cost:,.2f} | "
            f"{r.elasticity:.2f} | {r.r_squared:.2f} | {r.n_points} | {dchg} | {upl} | "
            f"{ACTION_LABEL[r.action]} | {'yes' if r.confident else 'no'} |"
        )
    lines.append("")

    lines.append("## Methodology\n")
    lines.append("- **Elasticity:** per-SKU log-log regression of quantity on price (ε = slope of ln q vs ln p), with R² as a fit-quality signal.")
    lines.append("- **Optimal price:** constant-elasticity profit maximum `p* = c · ε/(ε+1)`, valid when demand is elastic (ε < −1). Inelastic SKUs (ε ≥ −1) have no interior optimum — test a higher price.")
    lines.append("- **Profit uplift / demand change:** modeled against the fitted curve relative to the current (median) price.")
    lines.append(
        "- **Confidence:** flagged only when R² ≥ 0.5, ≥ 4 price observations, the recommended "
        "price stays within the observed price range (no more than 30% beyond the min/max prices "
        "actually seen), and the unit cost came from the data rather than being estimated.\n"
    )
    lines.append("## Assumptions & caveats\n")
    cr = report.params["cost_ratio"]
    lines.append(f"- Unit cost {'taken from the data' if report.params['has_cost_column'] else f'assumed at {cr * 100:.0f}% of current price'} (no cost column → margin is an estimate).")
    lines.append("- A single-product, constant-elasticity model: it ignores cross-product effects, competitor moves, and capacity. Validate before repricing, ideally with a live price test.")
    lines.append("")
    lines.append("_Decision support generated from the client's price/quantity history._")

    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def write_pricing_excel(report: PricingReport, path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "Price recommendations"
    rows = [_price_row(r) for r in report.recommendations]
    headers = list(rows[0].keys()) if rows else ["product_id"]
    fill = PatternFill("solid", fgColor="1F2A44")
    for c, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=c, value=h.replace("_", " "))
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = fill
    for ri, row in enumerate(rows, 2):
        for ci, h in enumerate(headers, 1):
            ws.cell(row=ri, column=ci, value=defuse_formula(row[h]))
    for ci, h in enumerate(headers, 1):
        width = max(len(h), *(len(str(row[h])) for row in rows)) if rows else len(h)
        ws.column_dimensions[get_column_letter(ci)].width = min(max(width + 2, 10), 28)
    ws.freeze_panes = "A2"
    wb.save(out)
    return out


def write_pricing_all(report: PricingReport, out_dir: str | Path, *, client: str = "Client") -> dict[str, Path]:
    d = Path(out_dir)
    return {
        "excel": write_pricing_excel(report, d / "price_recommendations.xlsx"),
        "report": write_pricing_report_md(report, d / "pricing_report.md", client=client),
        "csv": write_pricing_csv(report, d / "pricing_summary.csv"),
    }
