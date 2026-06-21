"""Automated QA — verify a JobReport's numbers before it reaches a client.

The first gate of the human-in-the-loop model: catch any internal inconsistency
(bad investment math, infeasible-but-flagged-feasible, negative safety stock,
out-of-range allocation) so the human only reviews sound output.
"""

from __future__ import annotations

from .inventory_optimization import JobReport

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
