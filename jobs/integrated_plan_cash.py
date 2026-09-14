"""The cash axis of A5's integrated plan, as the client actually reads it: the
weekly disbursement rows, the purchase rows those disbursements are dated from,
and the deck findings/KPIs that state when the money leaves.

Split out of ``jobs/integrated_plan.py``, which composes the pipeline (forecast
-> demand shaping -> purchase plan -> coherence checks -> cash calendar) and had
grown past the repo's file ceiling. The cut is along the axis, not along an
arbitrary line count: everything here answers step 5's question -- WHEN does the
money leave and does that week fit -- and reads ``plan.cash_schedule`` plus the
purchase lines' payment columns and nothing else. ``jobs/sop_deliverable.py``
and ``jobs/inventory_deliverable.py`` already keep a job's presentation layer in
a sibling module the same way.

Pure rendering: every number here was computed by ``src.sop_engine``; nothing in
this module decides anything, so a reader auditing a cash claim lands on the
engine, not here.
"""

from __future__ import annotations

from src.deliverable import Finding, Kpi
from src.sop_engine.cash_plan import CashCommitmentSchedule
from src.sop_engine.engine import IntegratedPlanReport


def purchase_rows(plan: IntegratedPlanReport) -> list[dict]:
    """The purchase line as a spreadsheet row. ``reorder_buffer`` alone cannot be
    told apart from a buffer sized that small on purpose, so the buffer ASKED FOR
    and what the cut cost travel with it -- and so does the payment term, plus
    whether it was read or assumed."""
    return [
        {
            "product_id": line.product_id,
            "shaped_demand": round(line.shaped_demand, 2),
            "on_hand": round(line.on_hand, 2),
            "incoming_po": round(line.incoming_po, 2),
            "recommended_order": round(line.recommended_order, 2),
            "constraint_extra_units": round(line.constraint_extra_units, 2),
            "constraint_cost_delta": round(line.constraint_cost_delta, 2),
            "constraint_extra_cover_periods": round(line.constraint_extra_cover_periods, 2),
            "constraint_verdict": line.constraint_verdict,
            "reorder_buffer_requested": round(line.reorder_buffer_requested, 2),
            "reorder_buffer": round(line.reorder_buffer, 2),
            "units_at_risk": round(line.units_at_risk, 2),
            "cost_at_risk": round(line.cost_at_risk, 2),
            "projected_position": round(line.projected_position, 2),
            "order_value": round(line.order_value, 2),
            "payment_terms": line.payment_terms,
            "payment_terms_source": line.payment_terms_source,
            "payment_due_days": line.payment_due_days,
            "payment_due_week": line.payment_due_week,
        }
        for line in plan.purchase_plan
    ]


def cash_rows(plan: IntegratedPlanReport) -> list[dict]:
    """The same money on a weekly axis: its own schema, so its own file (the rule
    ``integrated_plan.write_operational`` already follows per step).

    ``cap``/``headroom``/``within_cap`` come out BLANK when no weekly cash cap was
    supplied -- the rule that module already applies to a coherence check with no
    verdict. A ``True`` there would read as a measured fit against a cap nobody
    gave, which is the same silent-zero defect in a different column.
    """
    return [
        {
            "week": w.week,
            "week_start": w.week_start,
            "disbursement": round(w.disbursement, 2),
            "cumulative": round(w.cumulative, 2),
            "cap": "" if w.cap is None else round(w.cap, 2),
            "headroom": "" if w.headroom is None else round(w.headroom, 2),
            "within_cap": "" if w.cap is None else w.within_cap,
            "n_lines": w.n_lines,
        }
        for w in plan.cash_schedule.weeks
    ]


def _over_cap_finding(plan: IntegratedPlanReport) -> Finding:
    """The weeks the plan does not fit, named with the worst one."""
    schedule = plan.cash_schedule
    peak = schedule.peak_week
    return Finding(
        "Weeks over the cash cap",
        f"{schedule.n_weeks_over_cap} week(s) exceed the {schedule.cap_per_week:,.0f} usd weekly "
        f"cap, first in week {schedule.first_week_over_cap}"
        + (f"; the peak is week {peak.week} at {peak.disbursement:,.0f} usd" if peak else ""),
        impact="move, split or renegotiate those lines' terms before the plan executes -- "
               "this tool never re-times a purchase order by itself",
    )


def _deferral_finding(plan: IntegratedPlanReport) -> Finding:
    """Buffer given up so the portfolio fits its investment cap, worst hit first."""
    shown = ", ".join(
        f"{d.product_id} (-{d.units_at_risk:,.0f} u, {d.cost_at_risk:,.0f} usd)"
        for d in plan.deferrals[:5]
    )
    return Finding(
        "Reorder buffers deferred by the budget cap",
        f"{len(plan.deferrals)} line(s) gave up buffer so the portfolio fits its investment "
        f"cap, worst hit first: {shown}" + ("..." if len(plan.deferrals) > 5 else ""),
        impact="units at risk are protection REMOVED, not units forecast lost, and the value "
               "is at landed cost -- this plan carries no selling price",
    )


def _assumed_terms_finding(plan: IntegratedPlanReport) -> Finding:
    """The finding this deck must never omit: with no payment-terms column in the
    stock file every date on the calendar rests on the plan's assumed plazo, and
    a reader who is not told that will take the weeks for measured ones.

    That plazo is ``default_terms_in_effect``, the term the lines were ACTUALLY
    dated on, never the ``DEFAULT_PAYMENT_TERMS`` constant: a run with
    ``params['default_payment_terms'] = 'net_90'`` put its lines in week 12
    while this sentence said net 30, so the client read a plazo his own
    calendar contradicted.
    """
    schedule = plan.cash_schedule
    assumed = schedule.terms_assumed_for
    shown = ", ".join(assumed[:10])
    return Finding(
        "Payment terms ASSUMED, not read",
        f"{len(assumed)} line(s) carry no supplier payment condition and were "
        f"planned on {schedule.default_terms_in_effect}: {shown}"
        + ("..." if len(assumed) > 10 else ""),
        impact="supply payment_terms in the stock file and re-run -- until then these payment "
               "weeks are an assumption this deck is stating, not a contract it read",
    )


def cash_findings(plan: IntegratedPlanReport) -> list[Finding]:
    """The cash axis, as findings: when the money leaves, which weeks do not
    fit, which lines the investment cap deferred, and whose plazo was ASSUMED.

    Each conditional finding is built by its own helper above, so a reader
    checking one claim reads one function -- and so the list stays a list of
    what the plan actually has, never a fixed shape padded with empties.
    """
    schedule = plan.cash_schedule
    out = [Finding(
        "Cash commitment by week (step 5)",
        schedule.summary,
        impact=("this is when the purchase plan's money leaves, not just how much -- "
                "a total with no payment date cannot be checked against working capital"),
    )]
    if schedule.n_weeks_over_cap:
        out.append(_over_cap_finding(plan))
    if plan.deferrals:
        out.append(_deferral_finding(plan))
    if schedule.terms_assumed_for:
        out.append(_assumed_terms_finding(plan))
    return out


def _assumed_plazo(schedule: CashCommitmentSchedule) -> str:
    """How to NAME the assumed plazo in a KPI that is emitted even at count zero.

    With a line to speak for, the term those lines were actually dated on. With
    none, no term at all: this calendar has no assumed default in force, and
    reaching for ``DEFAULT_PAYMENT_TERMS`` there would print a plazo the run
    never used next to a count that is 0 -- the same false claim as the one
    ``default_terms_in_effect`` exists to close, just harder to notice.
    """
    return schedule.default_terms_in_effect or "an assumed default"


def cash_kpis(plan: IntegratedPlanReport) -> list[Kpi]:
    """The cash axis as KPIs. The empty TARGET on the two cap metrics is the
    point: with no weekly cap supplied there is nothing to be within, and a "0"
    target would imply a bar this plan was never measured against."""
    schedule = plan.cash_schedule
    peak = schedule.peak_week
    capped = schedule.cap_per_week is not None
    return [
        Kpi("Peak weekly cash commitment",
            f"{peak.disbursement:,.0f} (week {peak.week})" if peak else "0",
            target=f"<= {schedule.cap_per_week:,.0f}" if capped else "",
            rationale="Most money any single week of this plan asks of the bank account, from each "
                      "line's order_value dated by its supplier payment terms"),
        Kpi("Weeks over the cash cap", f"{schedule.n_weeks_over_cap}",
            target="0" if capped else "",
            rationale="Weeks whose disbursements exceed the weekly cash cap; no cap supplied means "
                      "no verdict was issued, not that every week fits"),
        Kpi("Lines on ASSUMED payment terms", f"{len(schedule.terms_assumed_for)}", target="0",
            rationale=f"Lines whose payment week rests on {_assumed_plazo(schedule)} because the "
                      f"stock file stated no supplier payment condition"),
    ]
