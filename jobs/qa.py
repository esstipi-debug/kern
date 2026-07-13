"""Automated QA — verify a JobReport's numbers before it reaches a client.

The first gate of the human-in-the-loop model: catch any internal inconsistency
(bad investment math, infeasible-but-flagged-feasible, negative safety stock,
out-of-range allocation) so the human only reviews sound output.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from src.guided import GuidedOutcome, verify_guided
from src.sop_engine.coherence import CHECK_BUDGET_FEASIBILITY, CHECK_PROMO_COVERAGE, CHECK_SERVICE_LEVEL

from . import forecast_job
from .inventory_optimization import JobReport
from .leadership import DIMS, ChainProfile
from .pricing import PricingReport

if TYPE_CHECKING:  # avoid a real circular import: jobs.digest_job -> scm_agent.events
    # -> scm_agent (package __init__) -> scm_agent.tools -> `from jobs import qa` -> here.
    # `from __future__ import annotations` above already makes the verify_digest type
    # hint a lazy string, so this import only ever runs for a type checker, never at
    # module load time.
    from .digest_job import DigestResult

    # Same hazard, same fix: jobs.integrated_plan -> scm_agent.knowledge/citation_gate ->
    # scm_agent (package __init__) -> scm_agent.tools -> `from jobs import qa` -> here.
    from .integrated_plan import IntegratedPlanBundle

    # Same hazard, same fix: jobs.price_intelligence -> src.pricing_intel.sanity ->
    # scm_agent.events -> scm_agent (package __init__) -> scm_agent.tools -> here.
    from .price_intelligence import PriceIntelReport

TOL = 1e-6
PRICE_INTEL_COVERAGE_MIN = 0.60  # plan section 6.9 item 2: ">=60% SKUs con >=1 competidor confirmado"
_REQUIRED_INTEGRATED_PLAN_CHECKS = {CHECK_PROMO_COVERAGE, CHECK_BUDGET_FEASIBILITY, CHECK_SERVICE_LEVEL}


def verify(report: JobReport) -> list[str]:
    """Return a list of QA issues. Empty list = passed."""
    issues: list[str] = []

    if not report.recommendations:
        issues.append("report has no SKU recommendations")

    for r in report.recommendations:
        if abs(r.investment - (r.cycle_investment + r.ss_investment)) > max(TOL, abs(r.investment) * 1e-9):
            issues.append(f"{r.product_id}: investment != cycle + safety")
        if r.safety_stock < -TOL:
            issues.append(f"{r.product_id}: negative safety stock")
        if r.reorder_point < -TOL:
            issues.append(f"{r.product_id}: negative reorder point")
        if r.policy_kind == "(s, Q)" and (r.order_quantity is None or r.order_quantity <= 0):
            issues.append(f"{r.product_id}: (s,Q) without a positive order quantity")
        if r.policy_kind == "(R, S)" and (r.order_up_to is None or r.order_up_to <= 0):
            issues.append(f"{r.product_id}: (R,S) without a positive order-up-to level")
        # the lead-only reorder must stay below order-up-to for (R,S)
        if r.policy_kind == "(R, S)" and r.order_up_to is not None and r.reorder_point > r.order_up_to + TOL:
            issues.append(f"{r.product_id}: reorder point exceeds order-up-to level")

    if not (0.0 - TOL <= report.safety_stock_scale <= 1.0 + TOL):
        issues.append(f"safety_stock_scale out of [0,1]: {report.safety_stock_scale}")
    if report.final_investment > report.requested_investment + max(TOL, report.requested_investment * 1e-9):
        issues.append("final investment exceeds requested")

    if report.budget is not None:
        if report.feasible and report.final_investment > report.budget + 1.0:
            issues.append("flagged feasible but final investment exceeds budget")
        if not report.feasible and report.cycle_floor <= report.budget + TOL:
            issues.append("flagged infeasible but cycle-stock floor fits the budget")

    sku_sum = sum(r.investment for r in report.recommendations)
    if report.budget is None and abs(sku_sum - report.requested_investment) > max(1.0, sku_sum * 1e-6):
        issues.append("requested investment != sum of SKU investments")

    return issues


def passed(report: JobReport) -> bool:
    return not verify(report)


def verify_pricing(report: PricingReport) -> list[str]:
    """Return a list of QA issues for a pricing report. Empty list = passed."""
    issues: list[str] = []
    if not report.recommendations:
        issues.append("pricing report has no recommendations")

    for r in report.recommendations:
        if r.current_price < -TOL:
            issues.append(f"{r.product_id}: negative current price")
        if r.action in {"raise", "lower"}:
            if r.optimal_price is None or r.optimal_price <= 0:
                issues.append(f"{r.product_id}: actionable but no positive optimal price")
            elif r.optimal_price <= r.unit_cost:
                issues.append(f"{r.product_id}: optimal price at or below unit cost")
            if r.elasticity >= -1:
                issues.append(f"{r.product_id}: actionable but demand is inelastic")
            if r.confident and r.profit_uplift_pct is not None and r.profit_uplift_pct < -0.5:
                issues.append(f"{r.product_id}: 'optimal' price lowers modeled profit")
        elif r.action == "inelastic" and r.elasticity < -1:
            issues.append(f"{r.product_id}: flagged inelastic but elasticity < -1")
        elif r.action == "insufficient_data" and r.optimal_price is not None:
            issues.append(f"{r.product_id}: insufficient data but an optimal price was set")

    return issues


def pricing_passed(report: PricingReport) -> bool:
    return not verify_pricing(report)


def verify_leadership(profile: ChainProfile) -> list[str]:
    """Return a list of QA issues for a CHAIN profile. Empty list = passed."""
    issues: list[str] = []
    codes = {code for code, _ in DIMS}

    if set(profile.scores) != codes:
        issues.append("profile is missing CHAIN dimensions")
    for code, val in profile.scores.items():
        if not 0 <= val <= 4:
            issues.append(f"{code}: score out of 0..4")

    expected_avg = sum(profile.scores.values()) / len(profile.scores) if profile.scores else 0.0
    if abs(profile.average - expected_avg) > 1e-9:
        issues.append("average does not match scores")

    if profile.scores:
        expected_gap = max(profile.scores.values()) - min(profile.scores.values())
        if profile.gap != expected_gap:
            issues.append("gap does not match scores")
        if profile.lever_level != min(profile.scores.values()):
            issues.append("priority lever is not the lowest-scoring dimension")

    if not profile.archetype:
        issues.append("missing archetype")
    return issues


def leadership_passed(profile: ChainProfile) -> bool:
    return not verify_leadership(profile)


def verify_digest(result: DigestResult) -> list[str]:
    """Return a list of QA issues for a daily digest (Linchpin 3.0 PR-3). Empty
    list = passed. The invariant that matters here is "no fabricated data"
    (plan rule 14): the per-type breakdown must actually sum to the reported
    total, an empty ledger must not be reported as having events, and a
    message that claims events happened must actually mention a count."""
    issues: list[str] = []

    if result.event_count < 0:
        issues.append("digest: negative event_count")
    if any(v < 0 for v in result.counts_by_type.values()):
        issues.append("digest: negative count in counts_by_type")

    counted = sum(result.counts_by_type.values())
    if counted != result.event_count:
        issues.append(f"digest: counts_by_type sums to {counted} but event_count is {result.event_count}")

    if not result.message.strip():
        issues.append("digest: empty message")
    elif result.event_count == 0 and "no events" not in result.message:
        issues.append("digest: zero events but message does not say so")
    elif result.event_count > 0 and str(result.event_count) not in result.message:
        issues.append("digest: message does not mention the actual event count")

    return issues


def digest_passed(result: DigestResult) -> bool:
    return not verify_digest(result)


def verify_price_intel(report: PriceIntelReport) -> list[str]:
    """QA invariants for the price-intelligence one-shot deliverable (plan
    section 6.9 item 2). Empty list = passed:

    - coverage: >=60% of products must have >=1 accepted competitor
      observation to ship at all (below that, the position matrix is too
      sparse to be a defensible deliverable).
    - zero quarantined/discarded rows leak into the accepted offers --
      they are reported in their own section (golden rule 14), never
      shipped as if trustworthy.
    - average freshness of the accepted observations stays within the
      report's own stated SLA.
    """
    issues: list[str] = []

    if report.n_products <= 0:
        issues.append("price_intel: no products in scope")
    if not (0.0 - TOL <= report.coverage_pct <= 1.0 + TOL):
        issues.append(f"price_intel: coverage_pct out of [0,1]: {report.coverage_pct}")
    elif report.coverage_pct < PRICE_INTEL_COVERAGE_MIN - TOL:
        issues.append(
            f"price_intel: coverage {report.coverage_pct * 100:.0f}% is below the "
            f"{PRICE_INTEL_COVERAGE_MIN * 100:.0f}% minimum required to ship"
        )

    accepted_refs = {(o.site, o.competitor_sku_ref) for o in report.offers}
    tainted_refs = {(r.site, r.competitor_url) for r in report.rows if r.status in ("quarantined", "discarded")}
    leaked = tainted_refs & accepted_refs
    if leaked:
        issues.append(f"price_intel: {len(leaked)} quarantined/discarded row(s) leaked into the accepted offers")

    if not (0.0 - TOL <= report.quarantine_rate <= 1.0 + TOL):
        issues.append(f"price_intel: quarantine_rate out of [0,1]: {report.quarantine_rate}")

    if report.avg_freshness_hours < -TOL:
        issues.append(f"price_intel: negative avg_freshness_hours: {report.avg_freshness_hours}")
    elif report.avg_freshness_hours > report.sla_hours + TOL:
        issues.append(
            f"price_intel: average freshness {report.avg_freshness_hours:.1f}h exceeds the "
            f"{report.sla_hours:.1f}h SLA"
        )

    return issues


def price_intel_passed(report: PriceIntelReport) -> bool:
    return not verify_price_intel(report)


def coverage_gate(outcome: GuidedOutcome) -> list[str]:
    """Deliverable coverage gate (plan §2.14). Empty list = the deliverable is covered.

    Extends the never-unprotected contract (``verify_guided``) with the residual-block
    requirements: a non-executed result must spell out the human residual - every
    handoff states the risk if skipped, and every escalation routes to a named human
    with an SLA. This is the QA-layer guard against a silent dead end.
    """
    issues = list(verify_guided(outcome))

    for h in outcome.handoffs:
        if not h.risk_if_skipped.strip():
            issues.append(f"handoff '{h.title}' does not state the risk if skipped")

    e = outcome.escalation
    if e is not None:
        if not e.route_to.strip():
            issues.append("escalation has no route_to (named human/role)")
        if not e.sla.strip():
            issues.append("escalation has no SLA")
        if not e.reason.strip():
            issues.append("escalation has no reason")

    return issues


def covered(outcome: GuidedOutcome) -> bool:
    return not coverage_gate(outcome)


def verify_integrated_plan(bundle: IntegratedPlanBundle) -> list[str]:
    """QA gate for A5's integrated plan (Linchpin 3.0 PR-20). Empty list =
    passed. Checks STRUCTURAL soundness (finite numbers, all 3 required
    coherence-check kinds present, internally-consistent counts) -- a
    FAILED coherence check is an intended FINDING this deliverable reports
    to a human (see ``jobs.integrated_plan``'s module docstring), never
    itself a QA failure that blocks the deliverable.
    """
    issues: list[str] = list(forecast_job.verify(bundle.forecast_report))
    plan = bundle.plan

    if plan.n_skus <= 0:
        issues.append("integrated_plan: no SKUs in the plan")
    if plan.n_checks != len(plan.checks):
        issues.append("integrated_plan: n_checks does not match len(checks)")
    if plan.n_checks_passed + plan.n_checks_failed != plan.n_checks:
        issues.append("integrated_plan: n_checks_passed + n_checks_failed != n_checks")

    present_kinds = {c.check for c in plan.checks}
    missing_kinds = _REQUIRED_INTEGRATED_PLAN_CHECKS - present_kinds
    if missing_kinds:
        issues.append(f"integrated_plan: missing required coherence check kind(s): {sorted(missing_kinds)}")

    for line in plan.demand_plan:
        if not math.isfinite(line.base_forecast) or line.base_forecast < 0:
            issues.append(f"integrated_plan: {line.product_id}: invalid base_forecast")
        if not math.isfinite(line.shaped_demand) or line.shaped_demand < 0:
            issues.append(f"integrated_plan: {line.product_id}: invalid shaped_demand")

    for line in plan.purchase_plan:
        if not math.isfinite(line.recommended_order) or line.recommended_order < 0:
            issues.append(f"integrated_plan: {line.product_id}: invalid recommended_order")
        if not math.isfinite(line.order_value) or line.order_value < 0:
            issues.append(f"integrated_plan: {line.product_id}: invalid order_value")

    return issues


def integrated_plan_passed(bundle: IntegratedPlanBundle) -> bool:
    return not verify_integrated_plan(bundle)
