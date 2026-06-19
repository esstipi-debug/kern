"""Inventory policies (s,Q) and (R,S) — Vandeput (2020), Chapters 3, 5 & 6."""

from __future__ import annotations

from dataclasses import dataclass

from src.demand_variability import DistributionKind, safety_stock_risk_period
from src.eoq import EOQResult, compute_eoq
from src.risk_period import demand_over_risk_period
from src.safety_stock import SafetyStockResult


@dataclass(frozen=True)
class PolicyResult:
    """Parameters for a single-echelon inventory policy."""

    policy: str
    order_quantity: float | None
    reorder_point: float | None
    order_up_to_level: float | None
    review_period: float | None
    safety_stock: SafetyStockResult
    eoq: EOQResult | None
    expected_cycle_stock: float
    expected_in_transit: float
    mean_demand_risk_period: float
    demand_std_risk_period: float


def continuous_review_sq(
    annual_demand: float,
    mean_demand_per_period: float,
    demand_std_per_period: float,
    holding_cost_per_unit: float,
    fixed_order_cost: float,
    lead_time_periods: float,
    cycle_service_level: float,
    periods_per_year: float = 52.0,
    lead_time_std: float = 0.0,
    demand_distribution: DistributionKind = "normal",
    observed_skewness: float | None = None,
) -> PolicyResult:
    """
    Continuous review (s, Q) policy (Sections 5.1.1, 6.3.2).

    Q* from EOQ; Ss = z * sigma_x; s = mu_x + Ss.
    """
    eoq = compute_eoq(annual_demand, holding_cost_per_unit, fixed_order_cost)
    risk = demand_over_risk_period(
        mean_demand_per_period,
        demand_std_per_period,
        lead_time_periods,
        lead_time_std,
        review_period=0.0,
    )
    ss = safety_stock_risk_period(
        risk.mean_demand,
        risk.demand_std,
        cycle_service_level,
        risk.risk_periods,
        distribution=demand_distribution,
        observed_skewness=observed_skewness,
    )
    ss_value = ss.safety_stock

    reorder_point = risk.mean_demand + ss_value

    return PolicyResult(
        policy="(s, Q)",
        order_quantity=eoq.order_quantity,
        reorder_point=reorder_point,
        order_up_to_level=None,
        review_period=None,
        safety_stock=ss,
        eoq=eoq,
        expected_cycle_stock=eoq.order_quantity / 2,
        expected_in_transit=mean_demand_per_period * lead_time_periods,
        mean_demand_risk_period=risk.mean_demand,
        demand_std_risk_period=risk.demand_std,
    )


def periodic_review_rs(
    annual_demand: float,
    mean_demand_per_period: float,
    demand_std_per_period: float,
    holding_cost_per_unit: float,
    fixed_order_cost: float,
    lead_time_periods: float,
    review_period: float,
    cycle_service_level: float,
    lead_time_std: float = 0.0,
    demand_distribution: DistributionKind = "normal",
    observed_skewness: float | None = None,
) -> PolicyResult:
    """
    Periodic review (R, S) policy (Sections 5.1.2, 6.3.3).

    Ss = z * sigma_x; S = mu_x + Ss.
    """
    eoq = compute_eoq(annual_demand, holding_cost_per_unit, fixed_order_cost)
    risk = demand_over_risk_period(
        mean_demand_per_period,
        demand_std_per_period,
        lead_time_periods,
        lead_time_std,
        review_period=review_period,
    )
    ss = safety_stock_risk_period(
        risk.mean_demand,
        risk.demand_std,
        cycle_service_level,
        risk.risk_periods,
        distribution=demand_distribution,
        observed_skewness=observed_skewness,
    )
    ss_value = ss.safety_stock

    order_up_to = risk.mean_demand + ss_value

    return PolicyResult(
        policy="(R, S)",
        order_quantity=None,
        reorder_point=None,
        order_up_to_level=order_up_to,
        review_period=review_period,
        safety_stock=ss,
        eoq=eoq,
        expected_cycle_stock=mean_demand_per_period * review_period / 2,
        expected_in_transit=mean_demand_per_period * lead_time_periods,
        mean_demand_risk_period=risk.mean_demand,
        demand_std_risk_period=risk.demand_std,
    )
