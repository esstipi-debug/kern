"""A5 integrated planning v1 (Linchpin 3.0 PR-20, plan section 5 A5
``balance``) -- a STRICT SEQUENTIAL orchestration layer, not a rewrite of
``src/sop.py`` (the existing chase/level/hybrid aggregate-planning engine,
untouched by this package -- see ``engine.py``'s module docstring and
``jobs/integrated_plan.py``'s for the scope boundary).

Four steps, each its own module:

1. ``demand_plan``  -- reconciled forecast + P2/P4 demand-shaping (steps 1-2).
2. ``purchase_plan`` -- ``src/constraints.py`` reused verbatim for MOQ/case-
   pack/budget (step 3).
3. ``coherence``    -- the >=3 citable cross-domain checks (step 4).
4. ``engine``       -- ``run_integrated_plan``, the top-level sequential glue.

No CP-SAT / OR-Tools anywhere in this package -- the joint solver is v2,
gated on A4 evidence this v1 pipeline does not have yet.
"""

from __future__ import annotations

from .coherence import (
    CHECK_BUDGET_FEASIBILITY,
    CHECK_PROMO_COVERAGE,
    CHECK_SERVICE_LEVEL,
    CoherenceResult,
    check_budget_feasibility,
    check_promo_coverage,
    check_reorder_point_service_level,
)
from .demand_plan import (
    LIQUIDATION_SOURCE,
    NO_SHIFT_SOURCE,
    PRICE_OPTIMIZER_SOURCE,
    DemandPlanLine,
    build_demand_plan,
    price_cut_lift_ratio,
)
from .engine import IntegratedPlanReport, run_integrated_plan
from .purchase_plan import PurchasePlanLine, SkuPurchaseInputs, build_purchase_plan

__all__ = [
    "CHECK_BUDGET_FEASIBILITY",
    "CHECK_PROMO_COVERAGE",
    "CHECK_SERVICE_LEVEL",
    "LIQUIDATION_SOURCE",
    "NO_SHIFT_SOURCE",
    "PRICE_OPTIMIZER_SOURCE",
    "CoherenceResult",
    "DemandPlanLine",
    "IntegratedPlanReport",
    "PurchasePlanLine",
    "SkuPurchaseInputs",
    "build_demand_plan",
    "build_purchase_plan",
    "check_budget_feasibility",
    "check_promo_coverage",
    "check_reorder_point_service_level",
    "price_cut_lift_ratio",
    "run_integrated_plan",
]
