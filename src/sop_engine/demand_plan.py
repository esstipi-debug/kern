"""Steps 1-2 of A5's integrated-planning pipeline (Linchpin 3.0 PR-20, plan
section 5 A5 ``balance``): a reconciled forecast, demand-shaped by any price-
cut signal already produced upstream.

**Step 1 -- forecast reconciliado.** v1's "reconciliation" is exactly what the
plan itself scopes it to: read the latest forecast run (``jobs.forecast_job``,
already a portfolio of per-SKU point forecasts). No hierarchical
reconciliation machinery is built here -- that is explicitly out of scope for
v1 (plan section 6, ``hierarchicalforecast`` is not a v1 dependency). The
caller (``jobs/integrated_plan.py``) hands this module a plain
``{product_id: forecast_quantity}`` mapping; this module never runs a
forecast itself (``src/`` stays pure, no jobs/ import -- see repo golden
rule 1).

**Step 2 -- plan de demanda.** A price CUT implies a demand LIFT under the
same constant-elasticity demand curve ``src/pricing.py`` already uses
(``q = scale * price**elasticity``): the ``scale`` term cancels in a ratio, so
the fractional demand change from moving price ``p0 -> p1`` at elasticity
``e`` is exactly ``(p1/p0)**e - 1`` -- :func:`price_cut_lift_ratio`, hand-
verifiable against ``src.pricing.recommend_price``'s own ``demand_change_pct``
(same formula, same model). Two upstream sources feed this shift, in
priority order:

1. **PR-16's P2 price optimizer** (:class:`~src.price_optimizer.PriceOptimizationResult`)
   -- the primary, plan-named source ("apply any demand-shaping suggestion
   from PR-16's P2 price optimizer"). Only a ``status == "ok"`` result with a
   real ``current_price``/``proposed_price``/``elasticity_used`` produces a
   shift; a ``needs_data`` result (no statistical signal) produces none --
   never a fabricated shift.
2. **PR-19's P4 liquidation report** (:class:`~src.liquidation.LiquidationLine`)
   -- the plan's own coherence-check example is a "promo/liquidation
   markdown (PR-19)"; a priced (non-salvage) liquidation line is also a price
   cut, so it can also imply a demand lift. v1 books the full
   ``units_to_clear`` into THIS SINGLE plan period rather than pacing it by
   ``weeks_to_clear`` -- the same single-period simplification step 1's own
   forecast already carries (no multi-period horizon in v1), documented here
   rather than silently assumed. This is an ABSOLUTE unit addition (not a
   ratio), because a clearance line's demand curve is priced directly off
   ``units_to_clear``/``clearance_price``, not off a separately observed
   elasticity-vs-baseline-demand relationship the way P2's result is.

A SKU present in both is resolved in priority order above (P2 first) -- a
SKU rarely carries both signals in practice (an actively-optimized, healthy
SKU vs. an excess/dead one), and when it does, the P2 signal is the more
statistically grounded of the two (a real fitted elasticity + CI, vs. a
liquidation line's often-heuristic default-discount/salvage price).

A SKU with neither signal keeps its unshaped forecast (``source ==
NO_SHIFT_SOURCE``, ``demand_shift_pct == 0.0``) -- never dropped, never
zeroed (Golden Rule 14).

Pure, no I/O, no jobs/ import (repo golden rule 1: ``src/`` is pure functions
only; ``src.price_optimizer``/``src.liquidation`` are both already ``src/``
modules, so importing their dataclasses here is same-layer, not a layering
violation).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from src.liquidation import LiquidationLine
from src.price_optimizer import PriceOptimizationResult

NO_SHIFT_SOURCE = "forecast_only"
PRICE_OPTIMIZER_SOURCE = "price_optimizer"
LIQUIDATION_SOURCE = "liquidation"


@dataclass(frozen=True)
class DemandPlanLine:
    """One SKU's demand plan: the reconciled forecast (step 1), any price-
    driven shift applied to it (step 2), and the resulting shaped demand fed
    to step 3's purchase plan."""

    product_id: str
    base_forecast: float          # step 1: the reconciled forecast, verbatim
    demand_shift_pct: float       # 0.0 when source == NO_SHIFT_SOURCE
    shaped_demand: float          # what step 3 must plan to cover
    source: str                   # NO_SHIFT_SOURCE | PRICE_OPTIMIZER_SOURCE | LIQUIDATION_SOURCE
    reason: str                   # citable: names the upstream numbers behind the shift


def price_cut_lift_ratio(current_price: float, proposed_price: float, elasticity: float) -> float:
    """Fractional demand change implied by moving price ``current_price ->
    proposed_price`` under a constant-elasticity demand curve
    ``q = scale * price**elasticity`` -- ``scale`` cancels in the ratio, so
    this needs only the two prices and the elasticity, not the fitted scale.
    Same model ``src.pricing.recommend_price`` already uses for its own
    ``demand_change_pct`` (``q_opt/q_cur - 1``); this is that identical ratio
    re-derived from two arbitrary prices instead of (current, optimal).
    """
    if current_price <= 0 or proposed_price <= 0:
        raise ValueError("current_price and proposed_price must be > 0")
    return (proposed_price / current_price) ** elasticity - 1.0


def _shift_from_price_optimizer(result: PriceOptimizationResult) -> tuple[float, str] | None:
    """``None`` when the optimizer had no statistical signal for this SKU
    (``status != "ok"``) or is missing a field the ratio needs -- never a
    fabricated shift (mirrors ``src.price_optimizer``'s own "needs_data,
    never a fabricated number" contract)."""
    if result.status != "ok":
        return None
    if result.current_price is None or result.current_price <= 0:
        return None
    if result.proposed_price is None or result.elasticity_used is None:
        return None
    ratio = price_cut_lift_ratio(result.current_price, result.proposed_price, result.elasticity_used)
    reason = (
        f"P2 price optimizer: price {result.current_price:.2f} -> {result.proposed_price:.2f} at "
        f"elasticity {result.elasticity_used:.3f} implies a {ratio * 100:+.1f}% demand shift "
        f"((p1/p0)**e - 1)."
    )
    return ratio, reason


def _shift_from_liquidation(line: LiquidationLine) -> tuple[float, str] | None:
    """``None`` for a salvage line (``clearance_price is None`` -- no price
    is announced, so no discount/demand-lift signal exists; mirrors
    ``src.liquidation_calendar``'s own "no price -> no discount" rule) or a
    line with nothing left to clear."""
    if line.clearance_price is None or line.units_to_clear <= 0:
        return None
    weeks = "inf" if math.isinf(line.weeks_to_clear) else f"{line.weeks_to_clear:.1f}"
    reason = (
        f"P4 liquidation: {line.method} clearance at {line.clearance_price:.2f} expects "
        f"{line.units_to_clear:.1f} unit(s) to clear (nominal {weeks} week horizon; v1 books the full "
        "disposition volume into this single plan period rather than pacing it -- same single-period "
        "simplification step 1's own forecast already carries)."
    )
    return line.units_to_clear, reason  # absolute units, not a ratio -- see module docstring


def _shape_from_liquidation(base: float, lift_units: float, reason: str, product_id: str) -> DemandPlanLine:
    shaped = base + lift_units
    if base > 0:
        shift_pct = (shaped / base - 1.0) * 100.0
    else:
        # Undefined percentage change from a zero baseline -- report the true
        # (infinite) growth rather than a fabricated finite number (Golden
        # Rule 14); shaped_demand itself still carries the real magnitude.
        shift_pct = math.inf if shaped > 0 else 0.0
    return DemandPlanLine(
        product_id=product_id, base_forecast=base, demand_shift_pct=shift_pct,
        shaped_demand=shaped, source=LIQUIDATION_SOURCE, reason=reason,
    )


def build_demand_plan(
    forecast: dict[str, float],
    *,
    price_shifts: dict[str, PriceOptimizationResult] | None = None,
    liquidation_lines: dict[str, LiquidationLine] | None = None,
) -> tuple[DemandPlanLine, ...]:
    """Shape ``forecast`` (step 1's reconciled per-SKU forecast) by any price-
    cut signal in ``price_shifts`` (P2, checked first) or ``liquidation_lines``
    (P4, checked second) -- see module docstring for the priority rule and
    the exact formulas. Returns one line per key in ``forecast``, sorted by
    ``product_id`` for a deterministic, diffable plan. A SKU only present in
    ``price_shifts``/``liquidation_lines`` but absent from ``forecast`` is
    NOT included (this function shapes a forecast, it does not invent one --
    the caller is responsible for making sure every SKU it cares about has a
    forecast entry, even if that entry is ``0.0``).
    """
    price_shifts = price_shifts or {}
    liquidation_lines = liquidation_lines or {}
    lines: list[DemandPlanLine] = []

    for product_id, raw_base in forecast.items():
        base = float(raw_base)

        opt = price_shifts.get(product_id)
        opt_shift = _shift_from_price_optimizer(opt) if opt is not None else None
        if opt_shift is not None:
            ratio, reason = opt_shift
            shaped = base * (1.0 + ratio)
            lines.append(DemandPlanLine(
                product_id=product_id, base_forecast=base, demand_shift_pct=ratio * 100.0,
                shaped_demand=shaped, source=PRICE_OPTIMIZER_SOURCE, reason=reason,
            ))
            continue

        liq = liquidation_lines.get(product_id)
        liq_shift = _shift_from_liquidation(liq) if liq is not None else None
        if liq_shift is not None:
            lift_units, reason = liq_shift
            lines.append(_shape_from_liquidation(base, lift_units, reason, product_id))
            continue

        lines.append(DemandPlanLine(
            product_id=product_id, base_forecast=base, demand_shift_pct=0.0, shaped_demand=base,
            source=NO_SHIFT_SOURCE, reason="no demand-shaping signal for this SKU -- forecast used as-is.",
        ))

    return tuple(sorted(lines, key=lambda line: line.product_id))
