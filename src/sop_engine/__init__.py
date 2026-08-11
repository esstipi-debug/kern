"""A5 integrated planning v1 (Linchpin 3.0 PR-20, plan section 5 A5
``balance``) -- a STRICT SEQUENTIAL orchestration layer, not a rewrite of
``src/sop.py`` (the existing chase/level/hybrid aggregate-planning engine,
untouched by this package -- see ``engine.py``'s module docstring and
``jobs/integrated_plan.py``'s for the scope boundary).

Six modules, each its own step or decision:

1. ``demand_plan``  -- reconciled forecast + P2/P4 demand-shaping (steps 1-2).
2. ``purchase_plan`` -- ``src/constraints.py`` reused verbatim for MOQ/case-
   pack/budget (step 3).
3. ``budget_deferral`` -- WHICH line cedes its buffer when the cap binds, and
   what that cut costs in coverage (the decision the portfolio scale does not
   contain).
4. ``cash_plan``    -- WHEN the money leaves: payment terms -> a weekly
   disbursement calendar against the weekly cash cap (step 5).
5. ``coherence``    -- the >=3 citable cross-domain checks (step 4).
6. ``engine``       -- ``run_integrated_plan``, the top-level sequential glue.

No CP-SAT / OR-Tools anywhere in this package -- the joint solver is v2,
gated on A4 evidence this v1 pipeline does not have yet.
"""

from __future__ import annotations

from .budget_deferral import DeferredBuffer, defer_by_priority, deferral_order
from .cash_plan import (
    DEFAULT_PAYMENT_TERMS,
    TERMS_ASSUMED,
    TERMS_STATED,
    CashCommitmentSchedule,
    CashCommitmentWeek,
    build_cash_commitment_schedule,
    parse_payment_terms,
    payment_week,
    resolve_payment_terms,
)
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
    "DEFAULT_PAYMENT_TERMS",
    "LIQUIDATION_SOURCE",
    "NO_SHIFT_SOURCE",
    "PRICE_OPTIMIZER_SOURCE",
    "TERMS_ASSUMED",
    "TERMS_STATED",
    "CashCommitmentSchedule",
    "CashCommitmentWeek",
    "CoherenceResult",
    "DeferredBuffer",
    "DemandPlanLine",
    "IntegratedPlanReport",
    "PurchasePlanLine",
    "SkuPurchaseInputs",
    "build_cash_commitment_schedule",
    "build_demand_plan",
    "build_purchase_plan",
    "check_budget_feasibility",
    "check_promo_coverage",
    "check_reorder_point_service_level",
    "defer_by_priority",
    "deferral_order",
    "parse_payment_terms",
    "payment_week",
    "price_cut_lift_ratio",
    "resolve_payment_terms",
    "run_integrated_plan",
]
