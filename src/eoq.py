"""Deterministic EOQ model — Vandeput (2020), Chapter 2."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class EOQResult:
    """Optimal order quantity and associated costs."""

    order_quantity: float
    optimal_total_cost: float
    holding_cost: float
    transaction_cost: float
    orders_per_year: float
    review_period: float


def compute_eoq(
    annual_demand: float,
    holding_cost_per_unit: float,
    fixed_order_cost: float,
) -> EOQResult:
    """
    Economic Order Quantity (eq. 2.2–2.3).

    Parameters
    ----------
    annual_demand:
        Yearly demand D (units/year).
    holding_cost_per_unit:
        Variable holding cost h (currency/unit/year).
    fixed_order_cost:
        Fixed transaction cost k per order (currency).
    """
    if annual_demand <= 0 or holding_cost_per_unit <= 0 or fixed_order_cost <= 0:
        raise ValueError("annual_demand, holding_cost_per_unit, and fixed_order_cost must be > 0")

    q_star = math.sqrt(2 * fixed_order_cost * annual_demand / holding_cost_per_unit)
    optimal_cost = math.sqrt(2 * fixed_order_cost * annual_demand * holding_cost_per_unit)
    holding = holding_cost_per_unit * q_star / 2
    transaction = fixed_order_cost * annual_demand / q_star
    orders_per_year = annual_demand / q_star
    review_period = q_star / annual_demand

    return EOQResult(
        order_quantity=q_star,
        optimal_total_cost=optimal_cost,
        holding_cost=holding,
        transaction_cost=transaction,
        orders_per_year=orders_per_year,
        review_period=review_period,
    )


def total_cost(
    order_quantity: float,
    annual_demand: float,
    holding_cost_per_unit: float,
    fixed_order_cost: float,
) -> float:
    """Total yearly supply chain cost for a given Q (eq. 2.1)."""
    if order_quantity <= 0:
        raise ValueError("order_quantity must be > 0")
    return fixed_order_cost * annual_demand / order_quantity + holding_cost_per_unit * order_quantity / 2


def cost_ratio_vs_optimal(order_quantity: float, optimal_quantity: float) -> float:
    """C(Q)/C(Q*) ratio from sensitivity analysis (Section 2.4)."""
    if order_quantity <= 0 or optimal_quantity <= 0:
        raise ValueError("quantities must be > 0")
    return 0.5 * (optimal_quantity / order_quantity + order_quantity / optimal_quantity)


@dataclass(frozen=True)
class PriceBreak:
    """Minimum order quantity for a unit price tier (Section 2.5.3)."""

    min_quantity: float
    unit_cost: float


@dataclass(frozen=True)
class EOQVolumeDiscountResult:
    """Best EOQ across price breaks."""

    order_quantity: float
    optimal_total_cost: float
    unit_cost: float
    price_break_index: int
    candidates: tuple[EOQResult, ...]


def compute_eoq_volume_discount(
    annual_demand: float,
    holding_cost_rate: float,
    fixed_order_cost: float,
    price_breaks: list[PriceBreak],
) -> EOQVolumeDiscountResult:
    """
    EOQ with all-units quantity discounts (Section 2.5.3).

    holding_cost_rate: h as fraction of unit cost (e.g. 0.25 = 25%/year).

    The decision cost compared ACROSS tiers must include the annual purchase cost
    (``annual_demand * unit_cost``): it is the whole reason a cheaper-per-unit tier
    can win despite its natural EOQ being infeasible (below the tier's minimum) and
    forcing a larger, holding-cost-heavier order. Omitting it (comparing only
    ordering + holding cost) is valid *within* one tier - where the unit cost is
    fixed and the purchase-cost term is a constant - but meaningless *across*
    tiers, and systematically under-favors taking a discount.
    """
    if not price_breaks:
        raise ValueError("price_breaks required")
    breaks = sorted(price_breaks, key=lambda b: b.min_quantity)

    best_q = 0.0
    best_cost = float("inf")
    best_idx = 0
    best_unit = breaks[0].unit_cost
    candidates: list[EOQResult] = []

    for idx, br in enumerate(breaks):
        h = holding_cost_rate * br.unit_cost
        if h <= 0:
            continue
        eoq = compute_eoq(annual_demand, h, fixed_order_cost)
        candidates.append(eoq)
        for q in {eoq.order_quantity, br.min_quantity}:
            if q <= 0 or q < br.min_quantity:
                continue
            cost = fixed_order_cost * annual_demand / q + h * q / 2 + annual_demand * br.unit_cost
            if cost < best_cost:
                best_cost = cost
                best_q = q
                best_idx = idx
                best_unit = br.unit_cost

    if best_q <= 0:
        raise ValueError("no feasible quantity across price breaks")

    return EOQVolumeDiscountResult(
        order_quantity=best_q,
        optimal_total_cost=best_cost,
        unit_cost=best_unit,
        price_break_index=best_idx,
        candidates=tuple(candidates),
    )


def round_review_period_power_of_two(
    optimal_period: float,
    base_period: float = 1.0,
) -> float:
    """
    Power-of-2 review period policy (Section 3.2.1).

    Returns R = 2^k * base_period with k minimal such that T*/sqrt(2) <= R <= sqrt(2)*T*.
    """
    if optimal_period <= 0 or base_period <= 0:
        raise ValueError("periods must be > 0")

    lower = optimal_period / math.sqrt(2)
    upper = optimal_period * math.sqrt(2)
    k = 0
    while True:
        candidate = (2**k) * base_period
        if lower <= candidate <= upper:
            return candidate
        if candidate > upper:
            return candidate
        k += 1
