"""Inventory Optimization playbook.

Given canonical demand (from intake), runs the engine end to end for every SKU
— forecast (sigma_e) -> (s,Q)/(R,S) policy -> portfolio budget allocation — and
returns a structured report ready to turn into client deliverables.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from src.classification import classify_portfolio
from src.classification import service_levels as class_service_levels
from src.constraints import InventoryItem, allocate_under_budget
from src.forecasting import forecast_demand
from src.policies import continuous_review_sq, periodic_review_rs
from src.safety_stock import tune_service_level
from src.sources import DataFrameDemandSource

PERIODS_PER_YEAR = 52.0


@dataclass(frozen=True)
class SkuRecommendation:
    product_id: str
    method: str
    intermittent: bool
    forecast: float
    error_std: float
    bias: float
    mae: float
    policy_kind: str
    order_quantity: float | None
    order_up_to: float | None
    reorder_point: float
    safety_stock: float
    z_factor: float
    service_level: float       # cycle service level actually used (per-SKU, may be differentiated)
    unit_cost: float
    lead_periods: float
    cycle_investment: float
    ss_investment: float
    investment: float
    status: str  # "ok" | "high_bias" | "review"


@dataclass(frozen=True)
class JobReport:
    recommendations: list[SkuRecommendation]
    params: dict
    requested_investment: float
    cycle_floor: float
    final_investment: float
    safety_stock_scale: float
    feasible: bool
    budget: float | None
    n_skus: int
    n_at_risk: int
    n_intermittent: int


def _status(intermittent: bool, bias: float) -> str:
    if intermittent:
        return "review"
    if abs(bias) >= 2:
        return "high_bias"
    return "ok"


def run(
    demand: pd.DataFrame,
    *,
    service_level: float = 0.95,
    holding_rate: float = 0.25,
    order_cost: float = 75.0,
    budget: float | None = None,
    periods_per_year: float = PERIODS_PER_YEAR,
    service_levels: dict[str, float] | None = None,
    differentiate_by_class: bool = False,
    lead_times: dict[str, float] | None = None,
    observed_fill_rates: dict[str, float] | None = None,
    target_fill_rate: float = 0.95,
) -> JobReport:
    """Run the inventory-optimization analysis over canonical demand.

    Safety stock is differentiated per SKU: ``service_levels`` overrides the cycle service
    level per product (or set ``differentiate_by_class`` to derive it from the ABC-XYZ class);
    ``lead_times`` overrides the per-SKU risk period (e.g. observed supplier lead times); and
    ``observed_fill_rates`` closes the loop, nudging each SKU's service level toward
    ``target_fill_rate`` so chronic stockouts raise the buffer and over-service relaxes it.
    """
    if not 0 < service_level < 1:
        raise ValueError("service_level must be in (0, 1)")
    if holding_rate <= 0 or order_cost <= 0:
        raise ValueError("holding_rate and order_cost must be > 0")

    source = DataFrameDemandSource(demand, periods_per_year=periods_per_year)
    base_levels: dict[str, float] = dict(service_levels or {})
    if not base_levels and differentiate_by_class:
        class_items = [
            {"product_id": p, "unit_cost": source.metadata(p).mean_unit_cost,
             "demand": list(source.demand_series(p))}
            for p in source.list_products()
        ]
        base_levels = class_service_levels(classify_portfolio(class_items))
    recs: list[SkuRecommendation] = []
    items: list[InventoryItem] = []

    for pid in source.list_products():
        series = source.demand_series(pid)
        meta = source.metadata(pid)
        fc = forecast_demand(series)
        inputs = fc.to_engine_inputs(periods_per_year=periods_per_year)
        holding_cost = max(holding_rate * meta.mean_unit_cost, 1e-6)
        lead = lead_times.get(pid, meta.lead_time_periods) if lead_times else meta.lead_time_periods
        sl = base_levels.get(pid, service_level)
        if observed_fill_rates and pid in observed_fill_rates:
            sl = tune_service_level(sl, observed_fill_rates[pid], target_fill_rate)

        if fc.is_intermittent:
            pol = periodic_review_rs(
                annual_demand=inputs["annual_demand"],
                mean_demand_per_period=inputs["mean_demand_per_period"],
                demand_std_per_period=inputs["demand_std_per_period"],
                holding_cost_per_unit=holding_cost,
                fixed_order_cost=order_cost,
                lead_time_periods=lead,
                review_period=1.0,
                cycle_service_level=sl,
            )
            kind, order_quantity = "(R, S)", None
        else:
            pol = continuous_review_sq(
                annual_demand=inputs["annual_demand"],
                mean_demand_per_period=inputs["mean_demand_per_period"],
                demand_std_per_period=inputs["demand_std_per_period"],
                holding_cost_per_unit=holding_cost,
                fixed_order_cost=order_cost,
                lead_time_periods=lead,
                cycle_service_level=sl,
            )
            kind, order_quantity = "(s, Q)", pol.order_quantity

        ss = pol.safety_stock.safety_stock
        cycle_units = pol.expected_cycle_stock
        cycle_inv = cycle_units * meta.mean_unit_cost
        ss_inv = ss * meta.mean_unit_cost
        reorder = inputs["mean_demand_per_period"] * lead + ss  # mu*L + safety (lead-only)

        recs.append(
            SkuRecommendation(
                product_id=pid, method=fc.method, intermittent=fc.is_intermittent,
                forecast=fc.forecast, error_std=fc.error_std, bias=fc.bias, mae=fc.mae,
                policy_kind=kind, order_quantity=order_quantity, order_up_to=pol.order_up_to_level,
                reorder_point=reorder, safety_stock=ss, z_factor=pol.safety_stock.service_level_factor,
                service_level=sl,
                unit_cost=meta.mean_unit_cost, lead_periods=lead,
                cycle_investment=cycle_inv, ss_investment=ss_inv, investment=cycle_inv + ss_inv,
                status=_status(fc.is_intermittent, fc.bias),
            )
        )
        items.append(InventoryItem(product_id=pid, order_quantity=2.0 * cycle_units, safety_stock=ss, unit_cost=meta.mean_unit_cost))

    cycle_floor = sum(it.cycle_investment for it in items)
    if budget is not None:
        plan = allocate_under_budget(items, budget)
        requested, final, scale, feasible = plan.requested_investment, plan.final_investment, plan.safety_stock_scale, plan.feasible
    else:
        requested = sum(it.investment for it in items)
        final, scale, feasible = requested, 1.0, True

    return JobReport(
        recommendations=recs,
        params={"service_level": service_level, "holding_rate": holding_rate, "order_cost": order_cost, "periods_per_year": periods_per_year},
        requested_investment=requested, cycle_floor=cycle_floor, final_investment=final,
        safety_stock_scale=scale, feasible=feasible, budget=budget,
        n_skus=len(recs), n_at_risk=sum(1 for r in recs if r.status == "high_bias"),
        n_intermittent=sum(1 for r in recs if r.intermittent),
    )
