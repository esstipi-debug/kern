"""Simulation-based optimization — Vandeput (2020), Chapter 13."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import optimize

from src.cost_optimization import optimize_rs_policy
from src.simulation import _sample_demand


@dataclass(frozen=True)
class SimulationCostResult:
    periods: int
    total_cost: float
    holding_cost: float
    ordering_cost: float
    backorder_cost: float
    mean_on_hand: float
    fill_rate: float
    safety_stock: float
    order_up_to_level: float


def simulate_rs_cost(
    order_up_to_level: float,
    lead_time_periods: int,
    review_period: int,
    *,
    mean_demand: float,
    std_demand: float,
    holding_cost_per_period: float,
    fixed_order_cost: float,
    backorder_cost: float,
    periods: int = 10_000,
    historical_demand: np.ndarray | None = None,
    seed: int | None = 42,
) -> SimulationCostResult:
    """
    (R,S) simulation with cost accounting (Ch. 8.4, 13.1).

    Physical inventory method: holding on on-hand only; backorders penalized.
    """
    rng = np.random.default_rng(seed)
    demand = _sample_demand(rng, periods, mean_demand, std_demand, historical_demand)

    on_hand = order_up_to_level
    pipeline: list[tuple[int, float]] = []
    backorders = 0.0
    on_hand_sum = 0.0
    total_demand = 0.0
    units_short = 0.0
    order_count = 0

    for t in range(periods):
        arrivals = [qty for due, qty in pipeline if due == t]
        if arrivals:
            received = sum(arrivals)
            on_hand += received
            if backorders > 0:
                fulfilled = min(on_hand, backorders)
                on_hand -= fulfilled
                backorders -= fulfilled
            pipeline = [(due, qty) for due, qty in pipeline if due != t]

        on_hand_sum += on_hand
        d = demand[t]
        total_demand += d
        if on_hand >= d:
            on_hand -= d
        else:
            short = d - on_hand
            units_short += short
            backorders += short
            on_hand = 0.0

        if t % review_period == 0:
            net_inventory = on_hand + sum(q for _, q in pipeline) - backorders
            order_qty = max(order_up_to_level - net_inventory, 0.0)
            if order_qty > 0:
                pipeline.append((t + lead_time_periods, order_qty))
                order_count += 1

    holding = holding_cost_per_period * on_hand_sum / periods
    ordering = fixed_order_cost * order_count / periods
    backorder = backorder_cost * units_short / periods
    fill_rate = 1.0 - units_short / total_demand if total_demand > 0 else 1.0
    mu_cycle = mean_demand * (review_period + lead_time_periods)
    ss = order_up_to_level - mu_cycle

    return SimulationCostResult(
        periods=periods,
        total_cost=holding + ordering + backorder,
        holding_cost=holding,
        ordering_cost=ordering,
        backorder_cost=backorder,
        mean_on_hand=on_hand_sum / periods,
        fill_rate=fill_rate,
        safety_stock=ss,
        order_up_to_level=order_up_to_level,
    )


def find_best_safety_stock(
    mean_demand: float,
    std_demand: float,
    lead_time_periods: int,
    review_period: int,
    holding_cost_per_period: float,
    fixed_order_cost: float,
    backorder_cost: float,
    *,
    step_size: float = 5.0,
    start_ss: float | None = None,
    search_radius: float = 50.0,
    periods: int = 5_000,
    seed: int = 42,
    max_evaluations: int = 200,
) -> SimulationCostResult:
    """
    Grid search for safety stock minimizing simulated cost (Ch. 13.1-13.2).

    Each grid point runs a full ``periods``-step simulation, so the grid is
    bounded: a non-positive ``step_size`` is rejected, and a grid wider than
    ``max_evaluations`` points fails fast instead of silently grinding. To search
    a bigger space, widen ``step_size``, narrow ``search_radius``, or raise
    ``max_evaluations``.
    """
    if step_size <= 0:
        raise ValueError(f"step_size must be positive, got {step_size}")
    mu_cycle = mean_demand * (review_period + lead_time_periods)
    if start_ss is None:
        start_ss = 0.5 * std_demand * (review_period + lead_time_periods) ** 0.5

    low = max(0.0, start_ss - search_radius)
    high = start_ss + search_radius
    candidates = np.arange(low, high + step_size, step_size)
    if len(candidates) > max_evaluations:
        raise ValueError(
            f"grid search would evaluate {len(candidates)} points, above the "
            f"max_evaluations cap of {max_evaluations}; increase step_size, reduce "
            f"search_radius, or raise max_evaluations"
        )

    best: SimulationCostResult | None = None
    for ss in candidates:
        s_level = mu_cycle + ss
        result = simulate_rs_cost(
            s_level,
            lead_time_periods,
            review_period,
            mean_demand=mean_demand,
            std_demand=std_demand,
            holding_cost_per_period=holding_cost_per_period,
            fixed_order_cost=fixed_order_cost,
            backorder_cost=backorder_cost,
            periods=periods,
            seed=seed,
        )
        if best is None or result.total_cost < best.total_cost:
            best = result

    if best is None:
        raise ValueError("no candidates evaluated")
    return best


def find_best_safety_stock_smart_start(
    mean_demand: float,
    std_demand: float,
    lead_time_periods: int,
    review_period: int,
    holding_cost_per_period: float,
    fixed_order_cost: float,
    backorder_cost: float,
    *,
    step_size: float = 5.0,
    search_radius: float = 50.0,
    periods: int = 5_000,
    seed: int = 42,
    max_evaluations: int = 200,
) -> tuple[SimulationCostResult, float]:
    """
    Smart start from analytical (R,S) optimum, then local grid search (Ch. 13.2).
    """
    analytical = optimize_rs_policy(
        mean_demand_per_period=mean_demand,
        demand_std_per_period=std_demand,
        mean_lead_time=float(lead_time_periods),
        holding_cost_per_period=holding_cost_per_period,
        fixed_order_cost=fixed_order_cost,
        backorder_cost=backorder_cost,
        review_periods=[float(review_period)],
    )
    start_ss = analytical.cost.safety_stock
    sim_best = find_best_safety_stock(
        mean_demand,
        std_demand,
        lead_time_periods,
        review_period,
        holding_cost_per_period,
        fixed_order_cost,
        backorder_cost,
        step_size=step_size,
        start_ss=start_ss,
        search_radius=search_radius,
        periods=periods,
        seed=seed,
        max_evaluations=max_evaluations,
    )
    return sim_best, start_ss


def optimize_rs_simulation(
    mean_demand: float,
    std_demand: float,
    lead_time_periods: int,
    review_period: int,
    holding_cost_per_period: float,
    fixed_order_cost: float,
    backorder_cost: float,
    *,
    bounds_ss: tuple[float, float] = (0.0, 200.0),
    periods: int = 3_000,
    seed: int = 42,
) -> SimulationCostResult:
    """Continuous search on safety stock via scipy (Ch. 13.3)."""
    mu_cycle = mean_demand * (review_period + lead_time_periods)

    def objective(ss: float) -> float:
        s_level = mu_cycle + ss
        return simulate_rs_cost(
            s_level,
            lead_time_periods,
            review_period,
            mean_demand=mean_demand,
            std_demand=std_demand,
            holding_cost_per_period=holding_cost_per_period,
            fixed_order_cost=fixed_order_cost,
            backorder_cost=backorder_cost,
            periods=periods,
            seed=seed,
        ).total_cost

    result = optimize.minimize_scalar(objective, bounds=bounds_ss, method="bounded")
    ss = float(result.x)
    return simulate_rs_cost(
        mu_cycle + ss,
        lead_time_periods,
        review_period,
        mean_demand=mean_demand,
        std_demand=std_demand,
        holding_cost_per_period=holding_cost_per_period,
        fixed_order_cost=fixed_order_cost,
        backorder_cost=backorder_cost,
        periods=periods,
        seed=seed,
    )


def optimize_rs_simulation_grid(
    mean_demand: float,
    std_demand: float,
    lead_time_periods: int,
    holding_cost_per_period: float,
    fixed_order_cost: float,
    backorder_cost: float,
    *,
    review_periods: list[int] | None = None,
    bounds_ss: tuple[float, float] = (0.0, 200.0),
    periods: int = 2_000,
    seed: int = 42,
) -> tuple[SimulationCostResult, int, float]:
    """Grid over R and Ss (Ch. 13.3 multi-parameter search)."""
    if review_periods is None:
        review_periods = [1, 2, 4]

    best: SimulationCostResult | None = None
    best_r = review_periods[0]
    best_ss = 0.0

    for r in review_periods:
        result = optimize_rs_simulation(
            mean_demand,
            std_demand,
            lead_time_periods,
            r,
            holding_cost_per_period,
            fixed_order_cost,
            backorder_cost,
            bounds_ss=bounds_ss,
            periods=periods,
            seed=seed,
        )
        if best is None or result.total_cost < best.total_cost:
            best = result
            best_r = r
            best_ss = result.safety_stock

    if best is None:
        raise ValueError("no feasible simulation optimization")
    return best, best_r, best_ss
