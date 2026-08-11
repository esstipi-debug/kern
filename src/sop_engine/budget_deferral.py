"""WHICH line cedes its reorder buffer when the money does not reach, and what
that cut costs in coverage.

``src.constraints.allocate_under_budget`` answers a portfolio question -- how
much safety-stock investment fits under the cap -- and then spends the answer by
multiplying EVERY SKU's buffer by the same ``safety_stock_scale``. That is a
total with no decision inside it. At a 0.10 scale nobody chose what to defer:
all thirty SKUs come out with a tenth of the protection they were sized for, so
none of them is actually protected, and the plan cannot tell a buyer which
supplier to call. The APICS/ASCM formulation of the constrained S&OP step is the
opposite instinct (Vollmann et al., *Manufacturing Planning and Control for
Supply Chain Management*, on resolving the supply plan against a binding
resource): when the constraint binds you decide what NOT to do, and you say so
by name.

**The ordering rule.** Every dollar of safety investment released from a line
costs ``1 / unit_cost`` units of demand coverage. To free the dollars the cap
demands while surrendering the FEWEST units of protection, the buffer to drain
first is the most expensive one per unit -- equivalently, the money still
available is spent buying cover from the cheapest units up. This is the classic
budget-constrained safety-stock result (expensive items carry proportionally
less buffer under an investment cap; Silver, Pyke & Peterson, *Inventory
Management and Production Planning and Scheduling*, on the exchange-curve /
Lagrangian treatment of an aggregate investment constraint).

**What the rule assumes, stated so it can be argued with.** Every unit of cover
counts the same across SKUs, because this plan carries no per-SKU service target
and no shortage cost -- the two inputs that would make the ordering economic
rather than merely cost-based. Where two lines cost the same, the order falls
back to ``product_id`` so the answer is reproducible; that tie-break is
arbitrary and is not a business judgement.

This module does NOT modify ``src/constraints.py``: the portfolio arithmetic
there (what fits, whether it is feasible at all) is reused verbatim and only its
per-line DISTRIBUTION is re-decided here. ``allocate_under_budget`` has other
callers (``jobs/inventory_optimization.py``, ``webapp/app.py``) whose contract is
that scale.

Pure, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Iterable, Sequence

from src.constraints import BudgetAllocation, InventoryItem, total_investment

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids a circular import
    from src.sop_engine.purchase_plan import PurchasePlanLine

#: How far below its request a FULLY FUNDED buffer may land and still count as
#: whole -- as a FRACTION of the request, because the error this absorbs is a
#: relative one. ``_funded_buffers`` puts a funded line through
#: ``(safety_stock * unit_cost) / unit_cost``: one multiply and one divide, each
#: correctly rounded to half an ULP, so the residual is bounded by about
#: ``2.3e-16 * safety_stock``. This leaves four orders of magnitude of headroom
#: over that bound and still sits far below any cut a buyer would call a cut.
#:
#: Deliberately NOT the absolute ``1e-6`` this started as, and not the shared
#: value in ``src.sop_engine.coherence`` either -- that one compares MONEY totals
#: and this one compares one line's UNITS, so they were never the same
#: measurement even when they were the same number. A fixed width on a quantity
#: whose error scales is wrong at both ends: see :func:`deferral_order`.
_ROUND_TRIP_REL_TOL = 1e-12


@dataclass(frozen=True)
class DeferredBuffer:
    """One line's share of the cut, in the two units a buyer argues in.

    ``units_at_risk`` is the demand this plan can no longer protect against
    variability -- the exposure the budget cut ADDED. It is deliberately NOT
    called a forecast of lost sales: turning removed buffer into a probability
    of stocking out needs the demand-variability distribution that
    ``src/inventory.py``'s ``sigma_L`` carries, which this plan does not
    receive. ``cost_at_risk`` values those units at LANDED COST for the same
    reason -- the purchase plan has no selling price, so this is what the
    unprotected units cost to own, never the revenue they would have earned.
    """

    product_id: str
    requested_buffer: float
    granted_buffer: float
    units_at_risk: float
    cost_at_risk: float


def _funded_buffers(requested: Sequence[InventoryItem], affordable: float) -> list[float]:
    """Buy cover from the cheapest units up until the money runs out."""
    order = sorted(range(len(requested)), key=lambda i: (requested[i].unit_cost, requested[i].product_id))
    granted = [0.0] * len(requested)
    remaining = affordable
    for i in order:
        item = requested[i]
        if item.unit_cost <= 0:
            # A zero-cost buffer consumes no budget, so the cap has no claim on
            # it: it is KEPT WHOLE and the money left for everyone else is
            # untouched. Skipping the line instead left ``granted[i]`` at 0.0 and
            # deferred the one buffer the budget never had a reason to cut --
            # a line that costs nothing cannot be part of what does not fit.
            granted[i] = item.safety_stock
            continue
        spend = min(item.safety_investment, remaining)
        granted[i] = spend / item.unit_cost
        remaining = max(0.0, remaining - spend)
    return granted


def defer_by_priority(
    allocation: BudgetAllocation, requested: Sequence[InventoryItem]
) -> BudgetAllocation:
    """Re-spend the safety investment ``allocation`` approved, by priority.

    Same money, same feasibility, same ``safety_stock_scale`` (which stays what
    it always was: the PORTFOLIO fraction of the requested safety investment
    that survived the cap) -- only the per-line distribution changes, from "every
    line loses the same fraction" to "the lines whose protection is cheapest are
    kept whole and the expensive ones are deferred first".

    Returns ``allocation`` untouched when nothing was trimmed, so the
    unconstrained path stays bit-for-bit what ``allocate_under_budget`` produced.
    """
    if allocation.safety_stock_scale >= 1.0:
        return allocation
    affordable = sum(item.safety_investment for item in allocation.items)
    granted = _funded_buffers(requested, affordable)
    items = [replace(item, safety_stock=granted[i]) for i, item in enumerate(requested)]
    return replace(allocation, items=items, final_investment=total_investment(items))


def deferral_order(lines: Iterable[PurchasePlanLine]) -> tuple[DeferredBuffer, ...]:
    """The lines this budget deferred, WORST HIT FIRST, and what each cut costs.

    This is the list a buyer works down -- "these are the buffers the cap could
    not fund, and this is the protection each one takes with it". Lines that
    kept their full buffer are left out: a cut list containing the lines nobody
    cut is not a cut list. Read off the plan lines themselves rather than
    recomputed, so the ranking and the CSV can never disagree.

    "Kept its full buffer" is judged against :data:`_ROUND_TRIP_REL_TOL` TIMES
    THAT LINE'S REQUEST, not against a bare ``> 0`` and not against a fixed
    number of units. A funded line's granted buffer is ``spend / unit_cost``
    where ``spend`` was itself ``safety_stock * unit_cost``, and that round trip
    lands a few parts in 1e16 below the request: without any tolerance a line the
    cap financed ENTIRELY came out with ``units_at_risk`` of 3e-14 and was
    reported to the buyer as deferred, inflating "N line(s) gave up buffer" by
    lines nobody cut.

    The tolerance was first written as an absolute ``1e-6``, eight orders of
    magnitude wider than the 3e-14 it cited as its cause, and that mismatch is
    wrong in BOTH directions -- because the cause is a relative error and the
    threshold was not:

    * too wide at the bottom -- a line requesting 1.0 unit of an item costing
      1e7 apiece, left 5 currency short by the cap, is deferred by 5e-7 units.
      That is real money and a real decision, and it vanished from the list the
      buyer works down.
    * too narrow at the top -- on a request near 1e11 units the same two
      operations leave a residual of 1.5e-5, past the constant, so the fully
      funded line reappears in the cut list. That is the original bug back
      again, at a scale the fixed number never covered.

    Scaling the threshold with the request answers both at once, because the
    error being screened out scales with the request. What this does NOT claim to
    absorb is the one line where ``remaining`` runs out mid-allocation: its
    shortfall is the cap biting, not rounding, and it belongs in the list.
    """
    cuts = (
        DeferredBuffer(
            product_id=line.product_id,
            requested_buffer=line.reorder_buffer_requested,
            granted_buffer=line.reorder_buffer,
            units_at_risk=line.units_at_risk,
            cost_at_risk=line.cost_at_risk,
        )
        for line in lines
    )
    return tuple(sorted(
        (c for c in cuts if c.units_at_risk > _ROUND_TRIP_REL_TOL * c.requested_buffer),
        key=lambda c: (-c.cost_at_risk, -c.units_at_risk, c.product_id),
    ))
