"""Automated QA — verify a JobReport's numbers before it reaches a client.

The first gate of the human-in-the-loop model: catch any internal inconsistency
(bad investment math, infeasible-but-flagged-feasible, negative safety stock,
out-of-range allocation) so the human only reviews sound output.
"""

from __future__ import annotations

from .inventory_optimization import JobReport
from .leadership import DIMS, ChainProfile
from .pricing import PricingReport

TOL = 1e-6


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
