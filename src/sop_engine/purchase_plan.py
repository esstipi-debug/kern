"""Step 3 of A5's integrated-planning pipeline (Linchpin 3.0 PR-20, plan
section 5 A5 ``balance``): turn the demand plan (steps 1-2) into a per-SKU
purchase/inventory plan by reusing ``src/constraints.py`` VERBATIM -- the
plan's own words, "constraints.py generalizado". No new order-quantity or
budget math is invented here; this module only maps demand-plan lines onto
``constraints.py``'s existing ``InventoryItem``/``allocate_under_budget``
machinery.

**Two purchase-quantity numbers are kept deliberately distinct** (this is
what makes step 4's promo-coverage check meaningful rather than tautological):

- ``incoming_po`` (:class:`SkuPurchaseInputs`) is an EXTERNAL FACT the caller
  supplies -- whatever procurement has ALREADY committed to before this plan
  ran (an ERP number, or ``0.0`` when nothing has been placed yet). This
  module never invents or adjusts it.
- ``recommended_order`` (:class:`PurchasePlanLine`) is what THIS plan
  computes: the top-up needed to close ``shaped_demand - on_hand -
  incoming_po`` (floored at zero), run through
  :func:`~src.constraints.apply_order_rules` (MOQ, case-pack rounding). This
  becomes ``InventoryItem.order_quantity`` -- the preserved economic floor
  ``allocate_under_budget`` never trims (see that module's own docstring):
  a budget crunch should never silently cancel the units needed to cover
  already-shaped demand.

Coherence step 4's promo-coverage check compares the ALREADY-COMMITTED
position (``on_hand + incoming_po``) against ``shaped_demand`` -- exactly the
plan's literal example, "a SKU with a planned promo/liquidation markdown has
no incoming purchase order to cover the expected demand lift". If this
module instead auto-sized ``incoming_po`` itself to always cover the gap,
that check could never fail; keeping the two numbers separate is what makes
the check a genuine coherence finding instead of a tautology.

The caller-supplied ``reorder_point`` becomes ``InventoryItem.safety_stock``
-- the desired buffer held ON TOP of what covers this cycle's demand. This
IS the field ``allocate_under_budget`` scales down first when the budget is
tight (verbatim reuse, not a new trimming rule).

``PurchasePlanLine.recommended_order`` is the real number of PHYSICAL UNITS
this plan proposes ordering (used by step 4's service-level check);
``constraints.py``'s ``InventoryItem.cycle_investment`` divides this by 2
internally (the classic EOQ average-cycle-stock convention,
``examples/run_constrained_plan.py`` already relies on this same reading) --
a WORKING-CAPITAL view used only inside ``allocate_under_budget``'s own
feasibility math, never surfaced here where units are what matters.

Pure, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.constraints import BudgetAllocation, InventoryItem, allocate_under_budget, apply_order_rules
from src.sop_engine.demand_plan import DemandPlanLine


@dataclass(frozen=True)
class SkuPurchaseInputs:
    """One SKU's inventory-position + ordering-rule inputs -- everything
    :func:`build_purchase_plan` needs beyond the demand plan itself.
    ``incoming_po`` is an external fact (already committed before this plan
    ran), not something this module computes -- see module docstring."""

    product_id: str
    on_hand: float
    unit_cost: float
    reorder_point: float = 0.0
    incoming_po: float = 0.0
    minimum_order_quantity: float = 0.0
    order_multiple: float = 0.0

    def __post_init__(self) -> None:
        if self.on_hand < 0:
            raise ValueError(f"{self.product_id}: on_hand must be >= 0")
        if self.unit_cost <= 0:
            raise ValueError(f"{self.product_id}: unit_cost must be > 0")
        if self.reorder_point < 0:
            raise ValueError(f"{self.product_id}: reorder_point must be >= 0")
        if self.incoming_po < 0:
            raise ValueError(f"{self.product_id}: incoming_po must be >= 0")
        if self.minimum_order_quantity < 0:
            raise ValueError(f"{self.product_id}: minimum_order_quantity must be >= 0")
        if self.order_multiple < 0:
            raise ValueError(f"{self.product_id}: order_multiple must be >= 0")


#: The supplier's terms cost nothing extra on this line - there was nothing to round.
UNCONSTRAINED = "unconstrained"
#: The rounding buys leftover the demand plan absorbs inside the horizon: take the terms.
WORTH_IT = "worth_it"
#: The rounding buys more cover than the horizon allows. The lever is not the plan, it is
#: the terms: renegotiate the MOQ, split the order, or source it elsewhere.
NOT_WORTH_IT = "not_worth_it"
#: There is leftover but no demand to measure it against, so no cover can be computed and
#: no verdict is honest - ``constraint_extra_cover_periods`` reports 0.0 there, which reads
#: as "the terms cost nothing". ``build_purchase_plan`` can no longer reach this (it does
#: not order against a zero gap at all), so this is what the public function answers to a
#: caller that hands it that pair rather than a plausible-looking WORTH_IT.
UNMEASURED = "unmeasured"

#: How much extra cover the rounding may add before it stops being worth it, in the periods
#: the demand plan is stated in. One full extra period of demand bought on top of what the
#: cycle needed is the point where a case-pack stops being a rounding and starts being a
#: stocking decision. Per-plan, because the tolerable horizon is a business call.
DEFAULT_CONSTRAINT_COVER_HORIZON = 1.0


def constraint_verdict(
    extra_units: float, shaped_demand: float, cover_horizon: float
) -> str:
    """Is taking the supplier's terms worth it on this line?

    Split from ``build_purchase_plan`` so the judgement is testable without a plan, and
    so the zero-demand case answers UNMEASURED instead of a plausible-looking verdict.
    """
    if extra_units <= 0:
        return UNCONSTRAINED
    if shaped_demand <= 0:
        return UNMEASURED
    return WORTH_IT if extra_units / shaped_demand <= cover_horizon else NOT_WORTH_IT


@dataclass(frozen=True)
class PurchasePlanLine:
    """One SKU's purchase/inventory plan: what's on hand, what's already
    incoming, what this plan additionally recommends ordering, the
    (possibly budget-trimmed) reorder buffer, and the projected inventory
    position after fulfilling the shaped demand with all three."""

    product_id: str
    shaped_demand: float
    on_hand: float
    incoming_po: float           # already-committed PO quantity (external fact, unchanged by this module)
    unit_cost: float
    recommended_order: float     # NEW top-up this plan proposes this cycle; NEVER trimmed by the budget allocator
    reorder_buffer: float        # desired buffer beyond on_hand + incoming_po + recommended_order; MAY be budget-trimmed
    projected_position: float    # on_hand + incoming_po + recommended_order - shaped_demand
    order_value: float           # recommended_order * unit_cost (full purchase cost, not the Q/2 EOQ convention)
    # What the SUPPLIER'S RULES added on top of the units this cycle actually needs:
    # recommended_order minus the raw gap, i.e. everything MOQ and case-pack rounding
    # bought beyond the requirement. Rounding up without pricing the leftover is how a
    # plan quietly buys months of stock it did not ask for, so the three numbers travel
    # with the line: units, money, and how much cover they add.
    constraint_extra_units: float = 0.0
    constraint_cost_delta: float = 0.0
    constraint_extra_cover_periods: float = 0.0
    #: Whether taking the supplier's terms is worth it, on those three numbers. Three
    #: numbers and no verdict leaves the buyer to eyeball it, which is the decision this
    #: plan exists to make. One of the CONSTRAINT_* tokens below.
    constraint_verdict: str = UNCONSTRAINED


def build_purchase_plan(
    demand_plan: tuple[DemandPlanLine, ...],
    sku_inputs: dict[str, SkuPurchaseInputs],
    *,
    budget: float | None = None,
    cover_horizon: float = DEFAULT_CONSTRAINT_COVER_HORIZON,
) -> tuple[tuple[PurchasePlanLine, ...], BudgetAllocation | None]:
    """Build the purchase/inventory plan for every SKU in ``demand_plan``.

    Every ``demand_plan`` line MUST have a matching ``sku_inputs`` entry --
    raises ``ValueError`` naming the missing SKU(s) rather than silently
    defaulting on-hand/unit_cost to zero (fail fast at a system boundary,
    per repo convention). ``budget`` is optional; when omitted, no portfolio
    trimming happens at all (every SKU keeps its full ``reorder_point`` as
    ``reorder_buffer``) and the returned ``BudgetAllocation`` is ``None``
    (never a fabricated allocation against a budget that was never given).
    """
    missing = [d.product_id for d in demand_plan if d.product_id not in sku_inputs]
    if missing:
        raise ValueError(f"no purchase inputs (on_hand/unit_cost) for: {', '.join(missing)}")

    items: list[InventoryItem] = []
    extra_by_sku: dict[str, float] = {}
    for d in demand_plan:
        inp = sku_inputs[d.product_id]
        gap = max(0.0, d.shaped_demand - inp.on_hand - inp.incoming_po)
        # The supplier's terms shape an order; they do not create one. `apply_order_rules`
        # floors at the MOQ, so handing it a zero gap ordered a full MOQ for a SKU that
        # needed nothing - 250 units of pure excess, priced and stocked as if requested.
        # The floor is correct where it is used on a real quantity (and on a price floor,
        # in price_optimizer); the decision of WHETHER to order belongs here.
        order_qty = apply_order_rules(
            gap, minimum_order_quantity=inp.minimum_order_quantity, order_multiple=inp.order_multiple,
        ) if gap > 0 else 0.0
        extra_by_sku[d.product_id] = order_qty - gap
        items.append(InventoryItem(
            product_id=d.product_id, order_quantity=order_qty,
            safety_stock=inp.reorder_point, unit_cost=inp.unit_cost,
        ))

    allocation = allocate_under_budget(items, budget) if budget is not None else None
    final_items = allocation.items if allocation is not None else items

    lines: list[PurchasePlanLine] = []
    for d, item in zip(demand_plan, final_items):
        inp = sku_inputs[d.product_id]
        projected = inp.on_hand + inp.incoming_po + item.order_quantity - d.shaped_demand
        lines.append(PurchasePlanLine(
            product_id=d.product_id, shaped_demand=d.shaped_demand, on_hand=inp.on_hand,
            incoming_po=inp.incoming_po, unit_cost=inp.unit_cost, recommended_order=item.order_quantity,
            reorder_buffer=item.safety_stock, projected_position=projected,
            order_value=item.order_quantity * inp.unit_cost,
            constraint_extra_units=extra_by_sku[d.product_id],
            constraint_cost_delta=extra_by_sku[d.product_id] * inp.unit_cost,
            # Cover the leftover adds, in the same period the demand plan is stated in.
            # Zero demand means no cover to speak of, not infinite cover.
            constraint_extra_cover_periods=(
                extra_by_sku[d.product_id] / d.shaped_demand if d.shaped_demand > 0 else 0.0
            ),
            constraint_verdict=constraint_verdict(
                extra_by_sku[d.product_id], d.shaped_demand, cover_horizon
            ),
        ))
    return tuple(lines), allocation
