"""Compose a client-ready deliverable from an inventory-optimization JobReport.

Bridges the flagship engine output (`jobs.inventory_optimization.JobReport`) into
the deliverable composer (`src.deliverable`): turns SKU recommendations and
portfolio numbers into quantified findings, a KPI table with rationale, a
data-source map, and action recommendations. Additive — does not modify the
existing tool/deliverables path; a caller opts in.
"""
from __future__ import annotations

from collections.abc import Sequence

from src import order_fulfillment as of
from src.deliverable import DataSource, Deliverable, Finding, Kpi, money, pct

from .deliverables import amount, cost_is_synthesized
from .inventory_optimization import JobReport, unsized_note

#: How each provenance reads on a client page. The deck must never print a service
#: number without saying where it came from - that is the whole T10 defect: a
#: target the model set, presented where a client expects what they actually got.
#: Every PROVENANCE_* constant ``src/order_fulfillment.py`` declares is keyed here,
#: MODEL_IMPLIED included: that module's docstring lists it as one of the four
#: procedences a RealizedService can carry, so leaving it out did not make it
#: impossible - it made ``build()`` die with ``KeyError`` on the one record whose
#: label matters most, the model number wearing a measurement's name.
_ORIGIN_TEXT = {
    of.PROVENANCE_OBSERVED: "measured here from customer order lines",
    of.PROVENANCE_CLIENT_REPORTED: "fill rate reported by the client",
    of.PROVENANCE_MIXED: "part measured from order lines, part reported by the client",
    of.PROVENANCE_NOT_MEASURED: "no order lines and no fill rate reported by the client",
    of.PROVENANCE_MODEL_IMPLIED: (
        "NOT measured: the fill rate the sized policy implies, not service any "
        "customer received"
    ),
}

#: What ``lost_sales_value`` was valued AT, which is not the same money in every
#: case. ``of.measure_from_order_lines`` values the shortfall at the line's own
#: ``unit_price`` - revenue. ``of.from_reported_fill_rate`` values it at
#: ``unit_cost``, and says so itself in ``of.METHOD_REPORTED_FILL_RATE`` ("valued at
#: mean unit cost"); a cost printed as revenue overstates the loss by the whole
#: margin. ``of.aggregate`` can sum both into one total, so the mixed roll-up names
#: both bases rather than picking one and staying quiet about the other. Keyed by
#: provenance for the same reason ``_ORIGIN_TEXT`` is: the label follows where the
#: number came from, never the other way round.
_LOST_VALUE_BASIS = {
    of.PROVENANCE_OBSERVED: "of revenue not captured",
    of.PROVENANCE_CLIENT_REPORTED: "of unserved demand, valued at unit cost (not revenue)",
    of.PROVENANCE_MIXED: "of unserved demand, part valued at selling price and part at unit cost",
    of.PROVENANCE_NOT_MEASURED: "of unserved demand (no valuation basis: nothing was measured)",
    of.PROVENANCE_MODEL_IMPLIED: (
        "of demand the sized POLICY expects to miss - a model output, not a loss "
        "anyone observed"
    ),
}

#: What a provenance neither table knows reads as. A ``KeyError`` here suppresses
#: the whole deliverable over a label; a silent generic sentence would be worse -
#: it would put a claim in the client's hands that no code stands behind. So the
#: fallback names its own ignorance, in the deck, where it gets noticed.
_UNRECOGNIZED = "provenance {0!r}, which this deck carries no wording for"


def _reading_sources(report: JobReport) -> tuple[DataSource, ...]:
    """How Kern read the client's file, as source-map rows. Empty when untracked.

    Findings are problems of the client's business; this is where a number came
    from. Same placement as the delivery-date caveat: the Excel the client opens,
    not only the operator summary.
    """
    quality = report.intake_quality
    if quality is None or quality.reading is None:
        return ()
    reading = quality.reading
    rows: list[DataSource] = [
        DataSource("How Kern read the file", reading.location_text(), "per run"),
        DataSource("Column mapping", reading.mapping_text(), "per run"),
    ]
    if reading.noteworthy_hygiene():
        rows.append(DataSource(
            "What was taken off the spreadsheet", reading.hygiene_text(), "per run",
        ))
    if quality.n_dropped:
        rows.append(DataSource(
            "Rows dropped after mapping",
            f"{quality.n_dropped} of {quality.n_raw} source row(s) "
            f"({quality.dropped_fraction:.0%}): "
            f"{quality.n_dropped_bad_date} bad date, "
            f"{quality.n_dropped_bad_quantity} unreadable quantity, "
            f"{quality.n_dropped_negative_quantity} negative quantity",
            "per run",
        ))
    if quality.synthesized:
        filled = "; ".join(f"{name}: {n} cell(s)" for name, n in quality.synthesized)
        rows.append(DataSource(
            "Columns filled from defaults", filled, "per run",
        ))
    return tuple(rows)


def _origin_text(provenance: str) -> str:
    """Where this number came from, in a client's words. Never raises."""
    return _ORIGIN_TEXT.get(provenance, _UNRECOGNIZED.format(provenance))


def _lost_value_basis(provenance: str) -> str:
    """What the lost-sales money was valued AT. Never raises."""
    return _LOST_VALUE_BASIS.get(provenance, f"of unserved demand, valued under an unstated basis "
                                             f"({_UNRECOGNIZED.format(provenance)})")


def _otif_label(service: of.RealizedService) -> str:
    """El rotulo del KPI dice contra QUE vara se midio la puntualidad.

    Un OTIF contra la promesa que el cliente declaro es un numero real sobre una vara
    dicha: se publica, y no puede llegarle con el mismo rotulo que uno que su sistema
    registro. El rotulo es lo primero que se lee, asi que la distincion va ahi y no en
    una nota al pie.
    """
    if service.otif_basis == of.OTIF_BASIS_CLIENT_PROMISE:
        return "Realized OTIF (vs your declared promise)"
    if service.otif_basis == of.OTIF_BASIS_MIXED:
        return "Realized OTIF (partly vs your declared promise)"
    return "Realized OTIF (observed)"


def _otif_basis_clause(service: of.RealizedService) -> str:
    """La frase que explica la vara, cuando no fue la del sistema."""
    if not service.lines_scored_against_declared_promise:
        return ""
    total = service.lines_otif_scored or service.lines_scored_against_declared_promise
    return (
        f"; {service.lines_scored_against_declared_promise} of {total} scored lines carried no promised "
        f"date in the source system and were judged against the delivery promise you "
        f"declared, not against a date your system recorded"
    )


def _realized(report: JobReport) -> of.RealizedService:
    """Portfolio realized service, or a not-measured record when nothing came in.

    Records built by hand (older callers, tests) carry ``realized_service=None``;
    they contribute nothing rather than being counted as a zero.
    """
    measured = [r.realized_service for r in report.recommendations if r.realized_service]
    return of.aggregate(measured) if measured else of.not_measured("PORTFOLIO")


def _service_findings(service: of.RealizedService, broken: tuple[str, ...]) -> list[Finding]:
    """What the service gap cost, in units, money and named SKUs. Empty when unmeasured.

    The money carries its valuation basis (:data:`_LOST_VALUE_BASIS`) because the
    same field holds revenue on an order-line measurement and cost on a
    client-reported one. Calling both "revenue" hands the client a number labelled
    as something it is not.
    """
    if not service.lost_sales_units:
        return []
    detail = f"{service.lost_sales_units:,.0f} units were ordered and not delivered"
    if broken:
        detail += f"; SKUs with stockout days: {', '.join(broken[:5])}"
    return [Finding(
        "Demand the network did not serve",
        f"{detail} ({_origin_text(service.provenance)}).",
        impact=f"{money(service.lost_sales_value)} {_lost_value_basis(service.provenance)}",
    )]


def _penalty(report: JobReport, value: float) -> str:
    """`amount()` with cents: a lot penalty of $0.17/yr printed as "$0" reads as
    free, and a free rounding is not a renegotiation case."""
    return f"{value:,.2f} u." if cost_is_synthesized(report) else f"${value:,.2f}"


def _costliest_lots(report: JobReport, n: int) -> str:
    """'SKU-A +$12/yr (Q 40 -> 60, alt 30 at +$15/yr); ...' for the n costliest snaps."""
    moved = [
        r for r in report.recommendations
        if r.lot_rule and r.order_quantity_unconstrained is not None
        and r.order_quantity is not None and r.order_quantity != r.order_quantity_unconstrained
    ]
    moved.sort(key=lambda r: r.lot_penalty_usd, reverse=True)
    parts = []
    for r in moved[:n]:
        text = (f"{r.product_id} +{_penalty(report, r.lot_penalty_usd)}/yr "
                f"(Q {r.order_quantity_unconstrained:,.0f} -> {r.order_quantity:,.0f}")
        if r.lot_alternative_qty is not None and r.lot_alternative_penalty_usd is not None:
            text += (f", alt {r.lot_alternative_qty:,.0f} at "
                     f"+{_penalty(report, r.lot_alternative_penalty_usd)}/yr")
        parts.append(text + ")")
    return "; ".join(parts)


def _horizon_findings(report: JobReport) -> list[Finding]:
    """Que hizo -- y que NO hizo -- la fecha de fin por SKU en este plan.

    Son dos hallazgos y no uno, porque dicen cosas opuestas y el cliente actua distinto
    con cada uno. El primero es plata que NO hay que gastar; el segundo es una fecha que
    el archivo trajo y este plan se nego a usar, que sin decirlo se lee como si el tope
    la hubiera cubierto.
    """
    out: list[Finding] = []
    n_cap = int(getattr(report, "n_horizon_capped", 0) or 0)
    if n_cap:
        liberadas = float(getattr(report, "horizon_units_freed", 0.0) or 0.0)
        capital = float(getattr(report, "horizon_capital_freed_usd", 0.0) or 0.0)
        pronto = sorted(
            (r for r in report.recommendations if r.horizon_verdict == "applied"),
            key=lambda r: r.periods_remaining or 0.0,
        )[:3]
        detalle = "; ".join(
            f"{r.product_id} ends {r.end_date} ({r.remaining_demand:,.0f} left to sell)"
            for r in pronto
        )
        out.append(Finding(
            f"{n_cap} SKU(s) capped by their end-of-life date",
            f"The policy would have stocked more than what is left to sell before the "
            f"SKU's end date; the cap removes {liberadas:,.0f} unit(s) of purchase and "
            f"${capital:,.0f} from the requested investment. {detalle}.",
            impact="less capital in stock that outlives its own demand",
        ))
    n_dec = int(getattr(report, "n_horizon_declarable", 0) or 0)
    if n_dec:
        out.append(Finding(
            f"{n_dec} end-of-life date(s) NOT applied",
            "The file carries an end date this run did not use - it falls on or before "
            "the last day of the history, or there is no measured mean behind it. Those "
            "SKUs were sized WITHOUT a cap: capping to zero on a mistyped date would "
            "silently stop replenishing a product that still sells, and stopping it is a "
            "discontinuation decision, not a rounding.",
            impact="check those dates before trusting the horizon on them",
        ))
    return out


def _position_findings(report: JobReport) -> list[Finding]:
    """What today's position says, and - louder - what it could not say.

    A plan that lists orders due now without saying whether stock already on order
    was counted hands the buyer the double buy H4 describes: the PO transport
    delayed gets bought again. So the "not netted" finding is a finding, not a
    footnote, and it comes right after the due-now count.
    """
    if not report.n_positioned:
        return []
    out = [Finding(
        f"{report.n_order_now} SKU(s) due for an order now",
        f"On today's inventory position (on hand + on order) {report.n_order_now} of "
        f"{report.n_positioned} positioned SKU(s) sit at or below their trigger; the quantity "
        "to order is on each row (order_qty_now)."
        + (f" Stock already on order was counted on {report.n_netted} SKU(s) "
           f"({report.units_on_order:,.0f} units), which avoided "
           f"{report.n_reorders_avoided_by_transit} re-order(s) of stock that is already inbound."
           if report.n_netted else ""),
        impact="cut these POs; the rest can wait for the next review",
    )]
    if not report.n_netted:
        out.append(Finding(
            "In-transit stock NOT counted",
            "The file carries stock on hand but no on-order column, so the due-now list "
            "could NOT be netted against open purchase orders: an order that transport "
            "delayed looks exactly like an order that does not exist.",
            impact="check open POs by hand before cutting any due-now order, or add an "
                   "on_order / en_transito column to the file",
        ))
    return out


def _supplier_terms_finding(report: JobReport) -> Finding:
    """The MOQ / case-pack rule, stated as a finding with its price tag. Every
    quantity in the plan is a lot the supplier accepts; what honouring the terms
    costs, and the second lot the buyer could take instead, are on each row."""
    detail = (
        f"{report.n_lot_constrained} (s,Q) SKU(s) carry a minimum order quantity or a "
        f"case-pack multiple on file; {report.n_lot_snapped} had their EOQ moved to the "
        "cheapest lot the supplier accepts, with the other feasible lot kept as an "
        f"alternative. Honouring the terms costs {_penalty(report, report.lot_penalty_total_usd)}"
        "/yr in ordering + holding cost over the unconstrained EOQ."
    )
    if report.n_lot_snapped:
        detail += f" Costliest: {_costliest_lots(report, 3)}."
    return Finding(
        "Order quantities honour the supplier's terms",
        detail,
        impact="every PO is one the supplier will accept; the penalty is the renegotiation case",
    )


def build(
    report: JobReport,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: Sequence[str] = (),
    residual: str = "",
    confidence: float | None = None,
    data_source: str = "demand history (intake)",
) -> Deliverable:
    """Build a Deliverable from an inventory-optimization report."""
    sl = report.params.get("service_level", 0.95)
    service = _realized(report)
    broken = of.skus_out_of_stock(
        [r.realized_service for r in report.recommendations if r.realized_service]
    )
    # El deck lleva su PROPIO encabezado, distinto del resumen que arma
    # `scm_agent/tools.py`, y es el que el cliente lee. Las dos frases salen de la
    # MISMA funcion del productor para que no puedan divergir otra vez.
    titular, cola = unsized_note(report)
    servicio = f" Service actually delivered to customers: {pct(service.fill_rate)} fill rate."
    if titular is not None:
        summary = titular + servicio
    else:
        summary = (
            f"Analyzed {report.n_skus} SKUs; recommended ${report.final_investment:,.0f} "
            f"inventory investment at a {sl * 100:.0f}% cycle service level target."
            + cola + servicio
        )

    findings: list[Finding] = list(_service_findings(service, broken))
    if report.n_at_risk:
        findings.append(Finding(
            f"{report.n_at_risk} SKUs with high forecast bias",
            "Forecast persistently diverges from actuals, so the sized policy may be off.",
            impact="review before ordering",
        ))
    if report.n_intermittent:
        findings.append(Finding(
            f"{report.n_intermittent} intermittent-demand SKUs",
            "Sporadic demand; placed on periodic (R, S) review instead of (s, Q).",
            impact="avoids over-stocking slow movers",
        ))
    if report.n_lot_constrained:
        findings.append(_supplier_terms_finding(report))
    findings.extend(_position_findings(report))
    findings.extend(_horizon_findings(report))
    if report.budget is not None and report.safety_stock_scale < 1.0:
        findings.append(Finding(
            "Budget-constrained safety stock",
            f"Safety stock scaled to {report.safety_stock_scale * 100:.0f}% to fit the "
            f"${report.budget:,.0f} budget.",
            impact="service drops below target on the tail",
        ))
    # A skipped-zero-demand SKU always carries investment=0.0 (see
    # jobs/inventory_optimization.py's run()) - exclude it so it never displaces or
    # clutters a real finding on a small portfolio.
    investable = [r for r in report.recommendations if r.investment > 0]
    top = sorted(investable, key=lambda r: r.investment, reverse=True)[:3]
    if top:
        names = ", ".join(f"{r.product_id} (${r.investment:,.0f})" for r in top)
        findings.append(Finding(
            "Inventory concentration",
            f"Largest investments: {names}.",
            impact="focus cycle counts and review cadence here",
        ))

    origin = _origin_text(service.provenance)
    kpis = (
        Kpi("SKUs analyzed", str(report.n_skus), rationale="Catalog coverage of the analysis"),
        Kpi(
            "Recommended investment" + (" (units, no unit cost loaded)" if cost_is_synthesized(report) else ""),
            amount(report, report.final_investment),
            target=(f"<= ${report.budget:,.0f}" if report.budget is not None else ""),
            rationale="Capital tied up in cycle + safety stock"),
        # The two realized KPIs come FIRST among the service numbers, and the model's
        # target is labelled as one. A client checking our deck against their own
        # numbers has to land on the measured line, not on the goal.
        Kpi("Realized fill rate (observed)", pct(service.fill_rate), target="95%+",
            rationale=f"Units delivered over units ordered on real customer orders - "
                      f"what was actually served ({origin})"),
        Kpi(_otif_label(service), pct(service.otif), target="95%+",
            rationale=f"Order lines delivered complete AND by the promised date, SCOR "
                      f"RL.1.1 ({origin}{_otif_basis_clause(service)})"),
        Kpi("Cycle service level (model target)", f"{sl * 100:.0f}%", target="95%+",
            rationale="The probability of not stocking out that the POLICY is sized for. "
                      "A target, not an outcome - read it against the realized fill rate above"),
        Kpi("High-bias SKUs", str(report.n_at_risk), target="0",
            rationale="Forecast-quality risk that distorts policy sizing"),
        Kpi("Intermittent SKUs", str(report.n_intermittent),
            rationale="Slow / sporadic movers managed on periodic review"),
    )
    if report.n_positioned:
        kpis += (
            Kpi("Orders due now", str(report.n_order_now),
                rationale=f"SKUs at or below their trigger on today's inventory position "
                          f"(on hand + on order), out of {report.n_positioned} with a position on file"),
        )
        if report.n_netted:
            kpis += (
                Kpi("Re-orders avoided by in-transit stock", str(report.n_reorders_avoided_by_transit),
                    rationale=f"SKUs the shelf figure alone would have re-ordered; "
                              f"{report.units_on_order:,.0f} units already on order were counted"),
            )
    if report.n_lot_constrained:
        kpis += (
            Kpi("SKUs on supplier terms (MOQ / case pack)",
                f"{report.n_lot_constrained} ({report.n_lot_snapped} moved off the EOQ)",
                rationale="Every (s,Q) quantity is a lot the supplier accepts; the runner-up "
                          "lot and both penalties are on each row of the workbook"),
            Kpi("Cost of honouring supplier terms",
                _penalty(report, report.lot_penalty_total_usd) + "/yr", target="as low as the terms allow",
                rationale="Ordering + holding cost of the chosen lots minus the unconstrained "
                          "EOQ's - what a renegotiation of the terms could recover"),
        )

    data_sources = (
        DataSource("Demand history & unit cost", data_source, "per run"),
        DataSource("Service level / holding rate / order cost", "engagement parameters", "per run"),
        DataSource("Realized fill rate / OTIF / lost sales",
                   f"{origin}; method: {service.method}", "per run"),
        DataSource("Days out of stock",
                   "customer order lines with dates, or the client's stock-zero history "
                   "- a scalar fill rate cannot produce it", "per run"),
        DataSource("Cycle service level (model target)",
                   "engagement parameter, tuned toward the observed fill rate - a policy "
                   "setting, never a measurement of service", "per run"),
    )
    # El caveat de la fecha de entrega, cuando la fuente lo declaro. Va en el mapa de
    # FUENTES y no entre los hallazgos: un finding es un problema del negocio del cliente, y
    # esto es un limite de la medicion. Meterlo entre los hallazgos lo haria leer como un
    # defecto de su operacion.
    #
    # Y va al entregable y no solo al resumen del agente porque el resumen lo lee el
    # operador y el Excel lo abre el cliente. El OTIF es el numero mas delicado del informe
    # -- de un archivo no se puede saber si su fecha es el despacho o la llegada, y entre
    # las dos lecturas hay 6,2 puntos medidos -- asi que llegarle sin el caveat es darle un
    # numero que no puede reconciliar contra su propio sistema.
    if report.service_note:
        data_sources += (
            DataSource("Where the service columns came from", report.service_note, "per run"),
        )
    if report.position_note:
        data_sources += (
            DataSource("Stock position (on hand / on order)", report.position_note, "per run"),
        )

    data_sources += _reading_sources(report)

    recs: list[str] = []
    if report.n_at_risk:
        recs.append("Review the high-bias SKUs' forecasts before issuing their POs.")
    if report.n_positioned:
        recs.append(
            f"Cut the {report.n_order_now} PO(s) due now (rows with order_now = yes, for "
            "order_qty_now units); every other positioned SKU is above its trigger today."
            + ("" if report.n_netted else " Check open POs by hand first: the file carried no "
               "on-order column, so nothing was netted.")
        )
    else:
        recs.append("Issue replenishment POs to the computed reorder points and order quantities.")
    if report.n_lot_snapped:
        worst = _costliest_lots(report, 3)
        recs.append(
            "Take the lot penalties to the supplier where rounding costs most "
            f"({worst}), or order the alternative lot listed on the row."
        )
    if report.n_lot_rule_unapplied:
        recs.append(
            f"{report.n_lot_rule_unapplied} periodic-review (R,S) SKU(s) carry supplier terms "
            "this plan does not apply: round each order-up-to quantity to the MOQ / case pack "
            "by hand when the PO is cut."
        )
    if report.budget is not None and not report.feasible:
        recs.append(
            f"Budget ${report.budget:,.0f} is below the cycle-stock floor "
            f"(${report.cycle_floor:,.0f}) - raise the budget or trim SKUs."
        )

    return Deliverable(
        title="Inventory Optimization",
        client=client,
        summary=summary,
        findings=tuple(findings),
        kpis=kpis,
        data_sources=data_sources,
        recommendations=tuple(recs),
        citations=tuple(citations),
        confidence=confidence,
        residual=residual,
        prepared=prepared,
    )
