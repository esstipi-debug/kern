"""What a client reads of A5's demand and coherence axes: the two CSV row shapes
those steps export, and the deck's findings, KPIs and recommendations.

Split out of ``jobs/integrated_plan.py``, which composes the pipeline and had
grown past the repo's 800-line file ceiling. The cut is along the axis, not along
an arbitrary line count, and it copies the shape its older sibling
``jobs/integrated_plan_cash.py`` already has for step 5: rows first, then the
findings and KPIs built off the same numbers. ``jobs/sop_deliverable.py`` and
``jobs/inventory_deliverable.py`` keep a job's presentation layer in a sibling
the same way; ``src/sop_engine/`` is split by axis the same way again.

Pure rendering: every number here was computed by ``src.sop_engine`` and every
verdict by ``jobs.integrated_plan_coherence``. Nothing in this module decides
anything, so a reader auditing a claim lands on the engine or on the tally, never
here. The one rule this module does own is a presentation rule: a check with no
verdict must render as a BLANK, never as a False -- see :func:`check_rows`.

Every name that existed before travels UNCHANGED, underscore prefixes included,
so the move can be checked by diffing bodies instead of being re-read as a
rewrite. Only :func:`demand_rows` and :func:`check_rows` are newly named, because
they were local variables inside ``write_operational`` and had no name to keep --
they are named after ``purchase_rows``/``cash_rows`` next door.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from jobs.integrated_plan_cash import cash_findings, cash_kpis
from jobs.integrated_plan_coherence import CoherenceTally
from src.deliverable import DataSource, Finding, Kpi
from src.sop_engine.coherence import NOT_EVALUABLE, STATUS_FAILED, STATUS_PASSED, UnevaluableCheck
from src.sop_engine.demand_plan import DemandPlanLine
from src.sop_engine.engine import IntegratedPlanReport

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids a circular import:
    # jobs.integrated_plan imports this module for the deck pieces it composes,
    # so importing the bundle back for real would close the loop at load time.
    # `from __future__ import annotations` above already makes these hints lazy
    # strings, so the import only ever runs for a type checker. jobs/qa.py keeps
    # its own IntegratedPlanBundle hint the same way.
    from jobs.integrated_plan import IntegratedPlanBundle


def demand_rows(plan: IntegratedPlanReport) -> list[dict]:
    """Step 2's demand plan as a spreadsheet row: what the forecast said, what
    shaped it, and by how much.

    ``demand_shift_pct`` is written as the string ``"inf"`` rather than rounded:
    a lift off a zero base is a real, unbounded shift, and ``round(inf, 1)``
    would land a bare ``inf`` in a CSV cell that a spreadsheet reads as text
    anyway. Naming it keeps the reason legible.
    """
    return [
        {
            "product_id": line.product_id,
            "base_forecast": round(line.base_forecast, 2),
            "demand_shift_pct": ("inf" if math.isinf(line.demand_shift_pct) else round(line.demand_shift_pct, 1)),
            "shaped_demand": round(line.shaped_demand, 2),
            "source": line.source,
            "reason": line.reason,
        }
        for line in plan.demand_plan
    ]


def check_rows(
    plan: IntegratedPlanReport, gaps: dict[tuple[str, str | None], UnevaluableCheck]
) -> list[dict]:
    """Step 4's coherence checks as spreadsheet rows, with the gaps re-declared.

    A check with no verdict gets an EMPTY passed cell and says why in its message
    (jobs/cross_impact_job.write's rule): a spreadsheet reader scanning the column
    must see a blank, because True and False are both claims here and "False" would
    read as a measured incoherence nobody measured.

    ``gaps`` is keyed by ``(check, product_id)`` and comes from the bundle's tally,
    so this function never has to decide what is evaluable -- it only has to render
    the decision without flattening it back into a boolean.
    """
    return [
        {
            "check": c.check,
            "passed": "" if (c.check, c.product_id) in gaps else c.passed,
            "status": (
                NOT_EVALUABLE if (c.check, c.product_id) in gaps
                else (STATUS_PASSED if c.passed else STATUS_FAILED)
            ),
            "product_id": c.product_id or "",
            "message": (
                gaps[(c.check, c.product_id)].reason if (c.check, c.product_id) in gaps
                else c.message
            ),
        }
        for c in plan.checks
    ]


_DATA_SOURCES = (
    DataSource("Demand history (sku/period/quantity)", "ERP / sales history", "weekly"),
    DataSource("On-hand, unit cost, reorder point, incoming PO, MOQ/case pack", "WMS / procurement", "weekly"),
    DataSource("Supplier payment terms (net 30/60, cash) -- dates the disbursements",
               "AP master / supplier contracts", "on contract change"),
    DataSource("Price/quantity history (optional, P2 demand shaping)", "POS / order history", "weekly"),
    DataSource("Liquidation/E&O stock (optional, P4 demand shaping)", "WMS / E&O", "weekly"),
)

#: What this v1 does NOT do, stated in every deck it signs.
_RESIDUAL = (
    "This is A5 v1: a strictly SEQUENTIAL pipeline (forecast -> demand shaping -> purchase plan -> "
    "coherence checks), not a joint/global optimization -- no simultaneous multi-step solve "
    "reconciles the steps against each other beyond the citable checks above (plan section 5's own "
    "anti-pattern warning: the joint solver is v2, gated on evidence this v1 pipeline does not have "
    "yet). A FAILED coherence check is a finding for a human to resolve (adjust the purchase plan, "
    "the promo timing, or the budget), never an auto-corrected plan. Executing any purchase order, "
    "price change, or liquidation step stays a separate, human-approved action in its own tool -- "
    "this plan does not write back to any system."
)

_BASE_RECOMMENDATIONS = (
    "Resolve every FAILED coherence check before executing this plan -- each cites the exact SKU "
    "and shortfall.",
    "Place the recommended_order quantities for SKUs whose promo-coverage check failed first -- "
    "they carry the nearest-term stockout risk.",
    "Re-run after the next forecast/price/liquidation cycle -- this is a single-pass v1 pipeline, "
    "not a continuously reconciled plan.",
)


def _shaping_finding(shaped: list[DemandPlanLine]) -> Finding:
    """Step 2's signal, present or absent. The empty case is stated rather than
    omitted: a deck with no demand-shaping section reads as if the step never
    ran, when what happened is that it ran and found nothing."""
    if not shaped:
        return Finding(
            "SKUs with a demand-shaping signal (step 2)",
            "none this cycle -- every SKU's purchase plan is sized off its raw forecast.",
            impact="supply a price_history_path (P2) or a liquidation-shaped stock file (P4) to activate this step",
        )
    names = ", ".join(f"{line.product_id} ({line.source}, {line.demand_shift_pct:+.0f}%)" for line in shaped[:5])
    return Finding(
        "SKUs with a demand-shaping signal (step 2)",
        f"{len(shaped)} SKU(s) carry a price-cut/markdown-implied demand shift: {names}"
        + ("..." if len(shaped) > 5 else ""),
        impact="these SKUs' purchase quantities (step 3) are sized off the SHAPED demand, not the raw forecast",
    )


def _deck_findings(bundle: IntegratedPlanBundle, shaped: list[DemandPlanLine]) -> list[Finding]:
    """The narrative, step by step: forecast -> demand shaping -> coherence ->
    cash -> what was left out. Every coherence claim comes from ``bundle.tally``,
    never from ``plan.n_checks_passed`` (see :func:`build_deck`)."""
    tally = bundle.tally
    findings = [
        Finding(
            "Forecast (step 1)",
            bundle.forecast_report.summary,
            impact="the reconciled forecast this plan's purchase quantities are sized against",
        ),
        _shaping_finding(shaped),
    ]
    for c in tally.failed[:5]:
        findings.append(Finding(f"Coherence check FAILED: {c.check}", c.message,
                                 impact="needs a human decision before this plan executes"))
    # The gap, its motive, and the ask -- one finding per check that lost its
    # verdict, never folded into the pass/fail count above.
    for gap in tally.unevaluable[:5]:
        findings.append(Finding(
            f"Coherence check NOT EVALUABLE: {gap.check}",
            gap.reason,
            impact=f"supply {gap.column} for {gap.product_id} and re-run -- until then this "
                   f"plan carries no {gap.check} verdict for it, in either direction",
        ))
    if not tally.failed and not tally.unevaluable:
        findings.append(Finding("Coherence checks (step 4)", f"{tally.headline}.",
                                 impact="no cross-domain conflict detected this cycle"))
    findings.extend(cash_findings(bundle.plan))
    if bundle.dropped_no_purchase_data:
        shown = ", ".join(bundle.dropped_no_purchase_data[:10])
        findings.append(Finding(
            "SKUs excluded for lack of purchase data",
            f"{len(bundle.dropped_no_purchase_data)} forecasted SKU(s) have no matching stock-file row "
            f"and were excluded from the purchase/coherence steps: {shown}"
            + ("..." if len(bundle.dropped_no_purchase_data) > 10 else ""),
            impact="never silently dropped -- supply the missing SKUs' on_hand/unit_cost to include them",
        ))
    return findings


def _deck_kpis(bundle: IntegratedPlanBundle, shaped: list[DemandPlanLine]) -> list[Kpi]:
    """The scoreboard. "Checks passed" is counted over the EVALUATED ones and
    the unevaluable ones get their own number, so the two can never be read as
    one ratio -- that fusion is what let a 3/3 stand on inputs nobody stated."""
    plan = bundle.plan
    tally = bundle.tally
    total_order_value = sum(line.order_value for line in plan.purchase_plan)
    kpis = [
        Kpi("SKUs planned", f"{plan.n_skus}", rationale="Forecasted SKUs with matching purchase-plan data"),
        Kpi("Coherence checks passed", f"{tally.n_passed}/{tally.n_evaluated}",
            target=f"{tally.n_total}/{tally.n_total}",
            rationale="Cross-domain checks between the demand plan and the purchase plan, "
                      "counted over the ones that could be EVALUATED"),
        Kpi("Coherence checks NOT EVALUABLE", f"{tally.n_unevaluable}", target="0",
            rationale="Checks whose grounding purchase input the stock file never supplied: "
                      "no verdict was issued for them, in either direction"),
        Kpi("SKUs demand-shaped", f"{len(shaped)}", rationale="Carry a price-cut/markdown-implied demand shift"),
        Kpi("Recommended purchase value", f"{total_order_value:,.0f}",
            rationale="Sum of recommended_order * unit_cost across the portfolio"),
    ]
    if plan.budget is not None:
        kpis.append(Kpi("Budget cap", f"{plan.budget:,.0f}", target="fit", rationale="Portfolio inventory-investment cap"))
    kpis.extend(cash_kpis(plan))
    return kpis


def _deck_recommendations(tally: CoherenceTally) -> tuple[str, ...]:
    """The asks, with the missing-input one FIRST when there is one: a reader who
    acts on recommendation 1 must close the gap that suspended the verdict before
    acting on a verdict the plan never issued."""
    if not tally.unevaluable:
        return _BASE_RECOMMENDATIONS
    return (
        f"Supply {', '.join(tally.missing_columns)} in the stock file and re-run: "
        f"{tally.n_unevaluable} coherence check(s) have no verdict without it, and this plan "
        "cannot be called coherent or incoherent until they do.",
    ) + _BASE_RECOMMENDATIONS
