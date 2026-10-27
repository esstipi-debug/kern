"""Campaign Stockout & Dual Shadow Price Expedite Simulator.

Evaluates marketing campaign feasibility against supply chain constraints:
1. Day-by-day deterministic inventory trajectory projection.
2. Exact stockout runout date (t_stockout) and delay duration in days until scheduled replenishment.
3. Linear Programming (LP) formulation solved via HiGHS to extract:
   - Optimal expedited bridge volume.
   - Total expedite premium cost.
   - Exact dual shadow price (pi) representing the marginal system cost of campaign-induced demand.

Pure, deterministic module using scipy.optimize.linprog.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
from scipy.optimize import linprog


@dataclass(frozen=True)
class ScheduledReplenishment:
    """Scheduled purchase order arrival."""
    arrival_day: int
    quantity: float


@dataclass(frozen=True)
class CampaignFeasibilityResult:
    """Complete campaign operational feasibility report."""
    sku: str
    is_feasible: bool
    stockout_day: Optional[int]
    next_replenishment_day: Optional[int]
    delay_days: int
    unmet_demand_units: float
    optimal_expedite_units: float
    total_expedite_cost: float
    max_shadow_price: float
    verdict: str  # "FEASIBLE" | "DELAY_WARNING" | "EXPEDITE_REQUIRED"
    daily_trajectory: List[Dict[str, float]]


def simulate_campaign_feasibility(
    sku: str,
    initial_on_hand: float,
    daily_base_demand: float,
    campaign_start_day: int,
    campaign_duration_days: int,
    campaign_lift_pct: float,
    scheduled_replenishments: List[ScheduledReplenishment],
    horizon_days: int = 60,
    regular_freight_unit_cost: float = 2.0,
    expedite_freight_unit_cost: float = 7.0,
    unserved_demand_penalty_unit: float = 15.0,
    daily_holding_cost_unit: float = 0.01,
) -> CampaignFeasibilityResult:
    """Simulate campaign demand over horizon and solve dual LP for expedite bridge."""
    if initial_on_hand < 0:
        raise ValueError("Initial on hand cannot be negative.")
    if daily_base_demand < 0:
        raise ValueError("Daily base demand cannot be negative.")
    if campaign_duration_days <= 0:
        raise ValueError("Campaign duration must be positive.")
    if campaign_start_day < 1:
        raise ValueError("Campaign start day must be at least 1.")
    if expedite_freight_unit_cost < regular_freight_unit_cost:
        raise ValueError("Expedite freight unit cost must be greater than or equal to regular freight.")

    unit_expedite_surcharge = expedite_freight_unit_cost - regular_freight_unit_cost

    # Build daily demand and replenishment vectors (1-indexed days 1..T)
    T = horizon_days
    campaign_end_day = campaign_start_day + campaign_duration_days - 1

    replenishments_by_day = {r.arrival_day: r.quantity for r in scheduled_replenishments}

    demands = np.zeros(T)
    for t in range(1, T + 1):
        if campaign_start_day <= t <= campaign_end_day:
            demands[t - 1] = daily_base_demand * (1.0 + campaign_lift_pct)
        else:
            demands[t - 1] = daily_base_demand

    # --- Phase 1: Passive Simulation (No expedite) ---
    stockout_day: Optional[int] = None
    first_replenishment_after_stockout: Optional[int] = None
    delay_count = 0
    unmet_units = 0.0
    current_inv = initial_on_hand
    trajectory = []

    for t in range(1, T + 1):
        rep = replenishments_by_day.get(t, 0.0)
        dem = demands[t - 1]
        start_inv = current_inv + rep
        end_inv = start_inv - dem

        is_stockout = False
        if end_inv < 0:
            is_stockout = True
            delay_count += 1
            unmet_units += abs(end_inv)
            if stockout_day is None:
                stockout_day = t
            end_inv_clamped = 0.0
        else:
            end_inv_clamped = end_inv

        if stockout_day is not None and t > stockout_day and first_replenishment_after_stockout is None:
            if rep > 0:
                first_replenishment_after_stockout = t

        trajectory.append({
            "day": float(t),
            "demand": float(dem),
            "replenishment": float(rep),
            "projected_inventory": float(end_inv),
            "is_stockout": 1.0 if is_stockout else 0.0,
        })
        current_inv = end_inv_clamped

    is_feasible = (stockout_day is None)

    # --- Phase 2: LP Optimization with Dual Shadow Price Extraction ---
    # Variables for each t in 1..T:
    # I_t: inventory at end of day t (len T)
    # x_t: expedited units received at day t (len T)
    # s_t: unserved shortage / lost sales at day t (len T)
    # Total variables = 3 * T
    # Objective: min sum_t [ h * I_t + c_expedite * x_t + p_penalty * s_t ]

    c_obj = np.zeros(3 * T)
    # I_t indices: 0 .. T-1
    c_obj[0:T] = daily_holding_cost_unit
    # x_t indices: T .. 2T-1
    c_obj[T:2*T] = unit_expedite_surcharge
    # s_t indices: 2T .. 3T-1
    c_obj[2*T:3*T] = unserved_demand_penalty_unit

    # Equality constraints:
    # For t=1: I_1 - x_1 - s_1 = initial_on_hand + r_1 - d_1
    # For t>1: I_t - I_{t-1} - x_t - s_t = r_t - d_t
    A_eq = np.zeros((T, 3 * T))
    b_eq = np.zeros(T)

    for t in range(1, T + 1):
        idx = t - 1
        rep = replenishments_by_day.get(t, 0.0)
        dem = demands[t - 1]

        # I_t
        A_eq[idx, idx] = 1.0
        if t > 1:
            # - I_{t-1}
            A_eq[idx, idx - 1] = -1.0

        # - x_t
        A_eq[idx, T + idx] = -1.0
        # - s_t
        A_eq[idx, 2 * T + idx] = -1.0

        # RHS
        if t == 1:
            b_eq[idx] = initial_on_hand + rep - dem
        else:
            b_eq[idx] = rep - dem

    bounds = [(0, None) for _ in range(3 * T)]

    res = linprog(c_obj, A_eq=A_eq, b_eq=b_eq, bounds=bounds, method="highs")

    if not res.success:
        raise RuntimeError(f"Linear program failed to solve: {res.message}")

    x_opt = res.x[T:2*T]
    optimal_expedite_units = float(np.sum(x_opt))
    total_expedite_cost = optimal_expedite_units * unit_expedite_surcharge

    # Dual marginals on equality constraints:
    # In scipy highs, res.eqlin.marginals contains the dual values.
    # The marginal on constraint t measures dCost/db_eq[t].
    # Since b_eq[t] = r_t - d_t, dCost/d(demand_t) = - marginal[t].
    marginals = res.eqlin.marginals if hasattr(res, "eqlin") and res.eqlin is not None else np.zeros(T)
    # The shadow price of demand is -marginal:
    shadow_prices = -np.array(marginals)
    max_shadow_price = float(np.max(shadow_prices)) if len(shadow_prices) > 0 else 0.0

    if is_feasible:
        verdict = "FEASIBLE"
    elif optimal_expedite_units > 0 and unserved_demand_penalty_unit > unit_expedite_surcharge:
        verdict = "EXPEDITE_REQUIRED"
    else:
        verdict = "DELAY_WARNING"

    return CampaignFeasibilityResult(
        sku=sku,
        is_feasible=is_feasible,
        stockout_day=stockout_day,
        next_replenishment_day=first_replenishment_after_stockout,
        delay_days=delay_count,
        unmet_demand_units=float(unmet_units),
        optimal_expedite_units=optimal_expedite_units,
        total_expedite_cost=float(total_expedite_cost),
        max_shadow_price=max_shadow_price,
        verdict=verdict,
        daily_trajectory=trajectory,
    )
