"""Step 4 of A5's integrated-planning pipeline (Linchpin 3.0 PR-20, plan
section 5 A5 ``balance``): coherence checks BETWEEN the demand plan (steps
1-2) and the purchase plan (step 3) -- the actual point of A5 v1. Each check
returns one or more :class:`CoherenceResult`, and every message is CITABLE:
it names a specific number from a specific upstream step (a shaped-demand
figure, an on-hand quantity, a ``BudgetAllocation`` field) rather than a
vague "looks risky" warning -- the plan's own requirement.

Three checks (the plan's stated floor):

1. :func:`check_promo_coverage` -- the plan's own literal example: a SKU
   with a planned promo/liquidation markdown (a demand-plan line whose
   ``shaped_demand`` exceeds its ``base_forecast`` -- see
   ``src.sop_engine.demand_plan``) has no incoming purchase order
   (``PurchasePlanLine.incoming_po``, plus on-hand) to cover the expected
   lift. This reads the ALREADY-COMMITTED position only (never this same
   plan's own freshly-computed ``recommended_order`` -- see
   ``purchase_plan.py``'s module docstring for why that distinction is what
   makes this check able to fail at all).
2. :func:`check_budget_feasibility` -- surfaces ``BudgetAllocation``'s OWN
   ``feasible``/``final_investment`` fields as a named coherence failure
   (never silently truncates the plan and says nothing).
3. :func:`check_reorder_point_service_level` -- does the purchase plan
   actually keep every committed SKU's projected inventory position at or
   above its reorder point, given the reconciled (shaped) forecast.

**A check reads a number it did not put there, so it can be asked to compare
against a number nobody supplied.** ``SkuPurchaseInputs`` types every optional
purchase input as a plain float defaulting to ``0.0``, and a reorder buffer of
``0.0`` is byte-identical whether the client set that policy or never mentioned
the column: ``projected_position >= 0.0`` then passes for free, and the most
favourable statement this module can make gets issued from nothing. This engine
cannot tell the two apart -- it never saw the CSV cell -- so it does not try.
What it owns is the VOCABULARY for the answer: :class:`CoherenceResult` is a
real verdict and :class:`UnevaluableCheck` is the declared absence of one. The
caller that read the cell (``jobs/integrated_plan.py``) is the layer that knows
which of the two a given input deserves, exactly as ``jobs/risk_job.py``'s
``RegisterRiskFactor`` keeps TTR/TTS provenance out of the shared ``src/risk.py``
scorer.

Pure, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.constraints import BudgetAllocation
from src.sop_engine.demand_plan import NO_SHIFT_SOURCE, DemandPlanLine
from src.sop_engine.purchase_plan import PurchasePlanLine

CHECK_PROMO_COVERAGE = "promo_markdown_po_coverage"
CHECK_BUDGET_FEASIBILITY = "budget_allocation_feasibility"
CHECK_SERVICE_LEVEL = "reorder_point_service_level"

# What one check's outcome is called once absence is a first-class outcome.
# ``NOT_EVALUABLE`` is not an error and not a failure: it is the third answer,
# and it outranks the clean one wherever the two are summarised together
# (jobs/cross_impact_job.py's rule for its own unevaluable areas).
STATUS_PASSED = "passed"
STATUS_FAILED = "failed"
NOT_EVALUABLE = "not_evaluable"
CHECK_STATUSES = (STATUS_PASSED, STATUS_FAILED, NOT_EVALUABLE)

_TOL = 1e-6


@dataclass(frozen=True)
class CoherenceResult:
    """One coherence check's VERDICT for one SKU (or the whole portfolio,
    when ``product_id`` is ``None`` -- the budget check, which is portfolio-
    level by construction).

    ``passed`` is a judgement about numbers that were actually supplied. When
    the number a check compares against was never supplied, there is no verdict
    to record and building one of these -- with either boolean -- is the defect:
    use :class:`UnevaluableCheck` instead.
    """

    check: str
    passed: bool
    product_id: str | None
    message: str


@dataclass(frozen=True)
class UnevaluableCheck:
    """A coherence check that HAS NO VERDICT, and the input whose absence cost it one.

    The engine never builds one of these: it cannot see a CSV cell, so it cannot know
    that the ``0.0`` it compared against was a placeholder rather than a policy. The
    caller that read the cell builds it, and this type is what makes that answer
    sayable at all -- without it, a caller discovering a missing input has only
    ``passed=True`` or ``passed=False`` to say it with, and both are lies.

    ``__post_init__`` refuses the empty-handed version: an absence with no column and
    no reason is indistinguishable from a shrug, and the reason IS the value (the same
    bar ``jobs/risk_job.py``'s ``ResilienceGap`` holds its own note to).
    """

    check: str
    product_id: str | None
    column: str
    reason: str

    def __post_init__(self) -> None:
        if not self.check.strip():
            raise ValueError("an unevaluable check must name the check that lost its verdict")
        if not self.column.strip():
            raise ValueError(
                f"{self.check}: an unevaluable check must name the input that was not "
                "supplied -- 'something was missing' is not a declared absence")
        if not self.reason.strip():
            raise ValueError(
                f"{self.check}: an unevaluable check must carry a legible reason: it is "
                "what a reader gets in place of the verdict")


def check_promo_coverage(
    demand_plan: tuple[DemandPlanLine, ...],
    purchase_plan: tuple[PurchasePlanLine, ...],
) -> tuple[CoherenceResult, ...]:
    """Every demand-plan line with a real markdown-implied LIFT (``source !=
    NO_SHIFT_SOURCE`` and ``shaped_demand > base_forecast``) must be covered
    by on-hand stock plus an ALREADY-COMMITTED incoming purchase order
    (``PurchasePlanLine.incoming_po`` -- never this same plan's own freshly-
    computed ``recommended_order``, or this check could never fail);
    otherwise the plan is incoherent -- marketing/liquidation expects a
    demand lift that procurement has not actually placed a PO to cover yet.
    A failing result cites ``recommended_order`` as the actionable fix.

    A SKU with no lift at all makes this check inapplicable (documented as a
    single passing result, never silently empty -- Golden Rule 14).
    """
    purchase_by_id = {p.product_id: p for p in purchase_plan}
    lifted = [
        d for d in demand_plan
        if d.source != NO_SHIFT_SOURCE and d.shaped_demand > d.base_forecast + _TOL
    ]
    if not lifted:
        return (CoherenceResult(
            CHECK_PROMO_COVERAGE, True, None,
            "no SKU in this plan carries a price-cut/markdown demand lift -- check not applicable.",
        ),)

    results: list[CoherenceResult] = []
    for d in lifted:
        p = purchase_by_id.get(d.product_id)
        if p is None:
            results.append(CoherenceResult(
                CHECK_PROMO_COVERAGE, False, d.product_id,
                f"{d.product_id}: {d.source} implies a demand lift to {d.shaped_demand:.1f} units "
                f"(base {d.base_forecast:.1f}) but this SKU has no purchase-plan line at all -- cannot "
                "confirm coverage.",
            ))
            continue
        committed = p.on_hand + p.incoming_po
        if committed + _TOL >= d.shaped_demand:
            results.append(CoherenceResult(
                CHECK_PROMO_COVERAGE, True, d.product_id,
                f"{d.product_id}: {d.source} implies {d.demand_shift_pct:+.1f}% demand shift to "
                f"{d.shaped_demand:.1f} units (base {d.base_forecast:.1f}); on-hand {p.on_hand:.1f} + "
                f"already-committed incoming PO {p.incoming_po:.1f} = {committed:.1f} covers it.",
            ))
        else:
            gap = d.shaped_demand - committed
            results.append(CoherenceResult(
                CHECK_PROMO_COVERAGE, False, d.product_id,
                f"{d.product_id}: {d.source} implies {d.demand_shift_pct:+.1f}% demand shift, base "
                f"forecast {d.base_forecast:.1f} -> shaped demand {d.shaped_demand:.1f} unit(s), but "
                f"on-hand {p.on_hand:.1f} + already-committed incoming PO {p.incoming_po:.1f} = "
                f"{committed:.1f} falls short by {gap:.1f} unit(s) -- no purchase order covers the "
                f"expected demand lift (this plan recommends ordering {p.recommended_order:.1f} more "
                "to close the gap).",
            ))
    return tuple(results)


def check_budget_feasibility(
    allocation: BudgetAllocation | None,
    budget: float | None,
) -> tuple[CoherenceResult, ...]:
    """Surface ``BudgetAllocation.feasible`` as a named coherence failure
    with its OWN real numbers (``final_investment``, never a recomputed or
    fabricated shortfall) -- ``allocate_under_budget`` never raises on an
    infeasible portfolio, it flags it, and this is the check that turns that
    flag into a citable finding instead of a silent truncation.

    **What the passing message may say about ``safety_stock_scale``.** It used
    to read "safety-stock scale S applied to R requested", and both halves of
    that had stopped being true. ``S`` is not applied to ``requested_investment``
    -- it is the fraction of the requested SAFETY-stock investment the cap
    funded, while ``requested_investment`` is that safety total PLUS the
    preserved cycle floor. And since ``purchase_plan._allocate`` puts
    ``budget_deferral.defer_by_priority`` in the middle, ``S`` is no longer
    applied per line at all: the distribution is by priority -- the cheapest
    cover is kept whole and the expensive buffers are deferred by name. A
    message a client can cite must not assert a mechanism that stopped running,
    so it now states what the number IS (a portfolio fraction) and stops there.
    WHICH line kept its buffer is ``budget_deferral.deferral_order``'s answer,
    and this check never had the inputs to see it.
    """
    if allocation is None or budget is None:
        return (CoherenceResult(
            CHECK_BUDGET_FEASIBILITY, True, None, "no budget cap supplied -- check not applicable.",
        ),)
    if allocation.feasible:
        return (CoherenceResult(
            CHECK_BUDGET_FEASIBILITY, True, None,
            f"portfolio investment {allocation.final_investment:,.2f} fits the {budget:,.2f} budget "
            f"cap (of {allocation.requested_investment:,.2f} requested, the cap funded "
            f"{allocation.safety_stock_scale:.0%} of the requested safety-stock investment -- a "
            "portfolio total, not a per-line multiplier; which lines kept their buffer is a "
            "separate decision this check does not make).",
        ),)
    shortfall = allocation.final_investment - budget
    return (CoherenceResult(
        CHECK_BUDGET_FEASIBILITY, False, None,
        f"budget-exceeded: even after zeroing every SKU's reorder buffer, the economic order floor "
        f"(cycle purchases alone) requires {allocation.final_investment:,.2f}, {shortfall:,.2f} over "
        f"the {budget:,.2f} budget cap (BudgetAllocation.feasible=False).",
    ),)


def check_reorder_point_service_level(
    purchase_plan: tuple[PurchasePlanLine, ...],
) -> tuple[CoherenceResult, ...]:
    """Does the purchase plan actually keep this SKU's PROJECTED inventory
    position (on-hand + already-committed PO + this plan's recommended
    top-up order - shaped demand) at or above its reorder buffer, given the
    reconciled (shaped) forecast -- stockout risk before the next
    replenishment cycle when it does not. Unlike :func:`check_promo_coverage`
    (which reads only the ALREADY-COMMITTED position), this check evaluates
    the plan's own recommendation too -- "if procurement follows this plan,
    does the SKU stay covered".
    """
    results: list[CoherenceResult] = []
    for p in purchase_plan:
        incoming_total = p.incoming_po + p.recommended_order
        if p.projected_position + _TOL >= p.reorder_buffer:
            results.append(CoherenceResult(
                CHECK_SERVICE_LEVEL, True, p.product_id,
                f"{p.product_id}: projected position {p.on_hand:.1f} on-hand + {incoming_total:.1f} "
                f"incoming (committed {p.incoming_po:.1f} + recommended {p.recommended_order:.1f}) - "
                f"{p.shaped_demand:.1f} shaped demand = {p.projected_position:.1f}, at or above its "
                f"{p.reorder_buffer:.1f}-unit reorder buffer.",
            ))
        else:
            shortfall = p.reorder_buffer - p.projected_position
            results.append(CoherenceResult(
                CHECK_SERVICE_LEVEL, False, p.product_id,
                f"{p.product_id}: projected position {p.on_hand:.1f} on-hand + {incoming_total:.1f} "
                f"incoming (committed {p.incoming_po:.1f} + recommended {p.recommended_order:.1f}) - "
                f"{p.shaped_demand:.1f} shaped demand = {p.projected_position:.1f}, below its "
                f"{p.reorder_buffer:.1f}-unit reorder buffer by {shortfall:.1f} unit(s) -- stockout "
                "risk before the next replenishment cycle.",
            ))
    return tuple(results)
