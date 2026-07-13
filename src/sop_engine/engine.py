"""A5 v1's top-level sequential pipeline (Linchpin 3.0 PR-20, plan section 5
A5 ``balance``): forecast -> demand shaping -> purchase plan -> coherence
checks, each step's OUTPUT feeding the next step's INPUT, ONE pass.

This is deliberately a STRICT SEQUENTIAL pipeline, never a joint/global
optimization: no OR-Tools, no CP-SAT, nothing global-optimization-shaped.
Plan section 5's own anti-pattern warning: "no intentar el solver global
primero... el solver conjunto llega cuando A4 acredite el pipeline
secuencial" -- a joint solver is v2, gated on A4 evidence this v1 pipeline
does not have yet (A4, ``src/verify/``, backtests DECISIONS after the fact;
it has not yet accumulated a track record for THIS pipeline, which doesn't
exist before this PR). ``run_integrated_plan`` below calls exactly three
already-independent pure functions in order and stops -- there is no
feedback loop back into an earlier step and no simultaneous multi-step
solve.

Pure, no I/O (the jobs/ layer resolves every real-world input -- forecast
run, price-optimizer results, liquidation report, purchase inputs, budget --
before calling this).
"""

from __future__ import annotations

from dataclasses import dataclass

from src.constraints import BudgetAllocation
from src.liquidation import LiquidationLine
from src.price_optimizer import PriceOptimizationResult
from src.sop_engine.coherence import (
    CoherenceResult,
    check_budget_feasibility,
    check_promo_coverage,
    check_reorder_point_service_level,
)
from src.sop_engine.demand_plan import DemandPlanLine, build_demand_plan
from src.sop_engine.purchase_plan import PurchasePlanLine, SkuPurchaseInputs, build_purchase_plan


@dataclass(frozen=True)
class IntegratedPlanReport:
    """The full A5 v1 result: every intermediate step's output (never
    hidden -- a client can inspect the demand plan and purchase plan
    directly, not just the pass/fail checks) plus the coherence verdicts."""

    demand_plan: tuple[DemandPlanLine, ...]
    purchase_plan: tuple[PurchasePlanLine, ...]
    allocation: BudgetAllocation | None
    checks: tuple[CoherenceResult, ...]
    n_skus: int
    n_checks: int
    n_checks_passed: int
    n_checks_failed: int
    budget: float | None
    summary: str


def run_integrated_plan(
    forecast: dict[str, float],
    sku_inputs: dict[str, SkuPurchaseInputs],
    *,
    price_shifts: dict[str, PriceOptimizationResult] | None = None,
    liquidation_lines: dict[str, LiquidationLine] | None = None,
    budget: float | None = None,
) -> IntegratedPlanReport:
    """Run the strictly sequential A5 v1 pipeline once. Raises ``ValueError``
    (via :func:`~src.sop_engine.purchase_plan.build_purchase_plan`) if
    ``sku_inputs`` is missing an entry for a SKU in ``forecast`` -- the
    caller (``jobs/integrated_plan.py``) is responsible for aligning the two
    before calling this.
    """
    demand_plan = build_demand_plan(forecast, price_shifts=price_shifts, liquidation_lines=liquidation_lines)
    purchase_plan, allocation = build_purchase_plan(demand_plan, sku_inputs, budget=budget)
    checks = (
        check_promo_coverage(demand_plan, purchase_plan)
        + check_budget_feasibility(allocation, budget)
        + check_reorder_point_service_level(purchase_plan)
    )
    n_passed = sum(1 for c in checks if c.passed)
    n_failed = len(checks) - n_passed
    summary = (
        f"Integrated plan over {len(demand_plan)} SKU(s): {n_passed}/{len(checks)} coherence check(s) "
        f"passed" + (f", {n_failed} FAILED" if n_failed else "") + "."
    )
    return IntegratedPlanReport(
        demand_plan=demand_plan, purchase_plan=purchase_plan, allocation=allocation, checks=checks,
        n_skus=len(demand_plan), n_checks=len(checks), n_checks_passed=n_passed,
        n_checks_failed=n_failed, budget=budget, summary=summary,
    )
