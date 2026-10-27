"""Push Inventory Radar & Clearance Pricing Engine.

Identifies proactive push promotion opportunities from supply chain constraints:
1. Perishable spoilage risk based on effective shelf life (src/perishables.py).
2. Excess inventory and immobilized capital (DOH > 120 days).
3. Optimal clearance discount determination balancing required demand lift against
   spoilage and inventory carrying costs.

Pure, deterministic module.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional

from src.perishables import effective_shelf_life


@dataclass(frozen=True)
class InventoryItemProfile:
    """Product operational and financial characteristics."""
    sku: str
    on_hand: float
    daily_demand: float
    current_price: float
    unit_cost: float
    shelf_life_days: Optional[float] = None     # None if non-perishable
    channel_gating_pct: float = 0.0             # Fraction e.g. 0.33 (retailer rejects if <33% life remains)
    transit_lead_time_days: float = 0.0         # Lead time buffer
    disposal_cost_per_unit: float = 0.50        # Landfill/destruction fee
    annual_holding_rate: float = 0.25           # WACC + storage rate
    price_elasticity: float = 1.5               # Demand response: q ~ p^(-elasticity)


@dataclass(frozen=True)
class PushOpportunity:
    """Proactive recommendation pushed from Supply Chain to Commercial."""
    sku: str
    trigger: str                                # "PERISHABLE_SPOILAGE_RISK" | "EXCESS_CAPITAL_DOH"
    current_doh: float
    at_risk_units: float
    target_velocity_units_day: float
    recommended_discount_pct: float             # Fraction 0.0 .. 1.0
    recommended_clearance_price: float
    expected_units_cleared: float
    avoided_spoilage_units: float
    net_cash_benefit: float                     # Recovery vs letting spoil/hold
    rationale: str


def evaluate_push_opportunities(
    items: List[InventoryItemProfile],
    excess_doh_threshold: float = 120.0,
    target_healthy_doh: float = 60.0,
    max_allowed_discount_pct: float = 0.60,
) -> List[PushOpportunity]:
    """Scan inventory and generate actionable push opportunities for Marketing."""
    opportunities: List[PushOpportunity] = []

    for item in items:
        if item.daily_demand <= 0 or item.on_hand <= 0:
            continue

        doh = item.on_hand / item.daily_demand
        trigger: Optional[str] = None
        at_risk_units = 0.0
        effective_sl: Optional[float] = None
        horizon_days = 0.0

        # Check Trigger 1: Perishable Spoilage Risk
        if item.shelf_life_days is not None:
            effective_sl = effective_shelf_life(
                total_shelf_life_days=item.shelf_life_days,
                channel_gating_pct=item.channel_gating_pct,
                transit_lead_time_days=item.transit_lead_time_days,
            )
            if effective_sl > 0:
                max_sellable = item.daily_demand * effective_sl
                if item.on_hand > max_sellable:
                    trigger = "PERISHABLE_SPOILAGE_RISK"
                    at_risk_units = item.on_hand - max_sellable
                    horizon_days = effective_sl

        # Check Trigger 2: Excess Stock / Capital Inmovilizado (if not already perishable trigger)
        if trigger is None and doh > excess_doh_threshold:
            trigger = "EXCESS_CAPITAL_DOH"
            healthy_target_stock = item.daily_demand * target_healthy_doh
            at_risk_units = max(0.0, item.on_hand - healthy_target_stock)
            horizon_days = excess_doh_threshold

        if trigger is None or at_risk_units <= 0:
            continue

        # Solve required velocity to drain at-risk units over horizon
        target_velocity = item.on_hand / horizon_days if horizon_days > 0 else item.daily_demand
        velocity_ratio = target_velocity / item.daily_demand

        # Constant elasticity model: (p_new / p_old)^(-eps) = ratio
        # -> (1 - delta)^(-eps) = ratio -> 1 - delta = ratio^(-1/eps) -> delta = 1 - ratio^(-1/eps)
        if velocity_ratio > 1.0 and item.price_elasticity > 0:
            raw_discount = 1.0 - math.pow(velocity_ratio, -1.0 / item.price_elasticity)
        else:
            raw_discount = 0.10  # baseline conservative markdown

        discount = max(0.05, min(raw_discount, max_allowed_discount_pct))
        clearance_price = round(item.current_price * (1.0 - discount), 2)

        # Expected velocity under discount
        lift_multiplier = math.pow(1.0 - discount, -item.price_elasticity)
        new_daily_velocity = item.daily_demand * lift_multiplier
        units_cleared = min(item.on_hand, new_daily_velocity * horizon_days)
        avoided_spoilage = max(0.0, units_cleared - (item.daily_demand * horizon_days))

        # Financial reconciliation
        # Baseline cash if we do nothing (sales at regular price until expiry + loss of un-sellable units):
        baseline_units_sold = min(item.on_hand, item.daily_demand * horizon_days)
        baseline_spoilage_units = item.on_hand - baseline_units_sold
        baseline_cash = (baseline_units_sold * item.current_price) - (baseline_spoilage_units * (item.unit_cost + item.disposal_cost_per_unit))

        # Promo cash:
        promo_spoilage_units = max(0.0, item.on_hand - units_cleared)
        promo_cash = (units_cleared * clearance_price) - (promo_spoilage_units * (item.unit_cost + item.disposal_cost_per_unit))

        net_benefit = max(0.0, promo_cash - baseline_cash)

        if trigger == "PERISHABLE_SPOILAGE_RISK":
            rationale = (
                f"Effective shelf life is {effective_sl:.1f} days vs {doh:.1f} DOH. "
                f"{at_risk_units:.0f} units will spoil without a {discount*100:.1f}% discount."
            )
        else:
            rationale = (
                f"Coverage is {doh:.1f} days (threshold {excess_doh_threshold:.0f} days). "
                f"{at_risk_units:.0f} excess units tie up capital; {discount*100:.1f}% discount accelerates clearance."
            )

        opportunities.append(
            PushOpportunity(
                sku=item.sku,
                trigger=trigger,
                current_doh=doh,
                at_risk_units=at_risk_units,
                target_velocity_units_day=target_velocity,
                recommended_discount_pct=discount,
                recommended_clearance_price=clearance_price,
                expected_units_cleared=units_cleared,
                avoided_spoilage_units=avoided_spoilage,
                net_cash_benefit=net_benefit,
                rationale=rationale,
            )
        )

    # Sort opportunities by highest net cash benefit descending
    opportunities.sort(key=lambda x: x.net_cash_benefit, reverse=True)
    return opportunities
