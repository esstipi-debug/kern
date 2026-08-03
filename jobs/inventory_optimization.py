"""Inventory Optimization playbook.

Given canonical demand (from intake), runs the engine end to end for every SKU
— forecast (sigma_e) -> (s,Q)/(R,S) policy -> portfolio budget allocation — and
returns a structured report ready to turn into client deliverables.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats as _sps

from jobs.intake import IntakeQuality
from src.classification import classify_portfolio
from src.classification import service_levels as class_service_levels
from src.constraints import InventoryItem, allocate_under_budget
from src.forecasting import forecast_demand
from src.policies import continuous_review_sq, periodic_review_rs
from src.safety_stock import tune_service_level
from src.sources import DataFrameDemandSource

PERIODS_PER_YEAR = 52.0
# Service level gained per point of fill-rate gap in the closed loop. The engine's
# own default (0.5) drives a 15-point shortfall straight past 1.0 into its hi=0.999
# clamp, which turns every chronically short SKU into a 99.9% target regardless of
# how big the gap was.
SERVICE_LEVEL_TUNING_STEP = 0.1


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
    # What the run actually did, so the deliverable can describe it instead of
    # asserting a template methodology the engine stopped using.
    sigma_source: str = "forecast_error"      # see ForecastResult.sigma_source
    buffer_distribution: str = "normal"       # the distribution that sized the buffer
    lead_time_std: float = 0.0                # sigma_L fed to the risk period
    lead_time_std_source: str = "defaulted_zero"   # "client_file" | "defaulted_zero"
    risk_periods: float = 0.0                 # tau actually used (L, or L+R for (R,S))


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
    # How much of the source file survived intake, and why the rest didn't
    # (jobs/intake.py) — None when the caller used the untracked intake.prepare()
    # path (e.g. examples/run_inventory_job.py). scm_agent/tool_options.py's
    # inventory_options() reads this to decide whether to escalate.
    intake_quality: IntakeQuality | None = None
    intake_quality_threshold: float = 0.20
    # Share of source rows that may be negative before the negative-quantity gate
    # (jobs/quantity_audit.py) blocks the deliverable. Distinct from
    # intake_quality_threshold above, which covers ALL drop reasons together.
    negative_quantity_threshold: float = 0.02
    # SKUs skipped because their aggregate demand was zero (see run()'s per-SKU
    # loop) - a real new/seasonal SKU with no activity, or a data-quality split
    # that zeroed one spelling's demand. Isolated, not fatal to the batch.
    n_skipped_zero_demand: int = 0
    # SKUs skipped because no row carried a usable (>0) unit_cost (see run()'s
    # per-SKU loop) - a cost lost on load and reported as 0 instead of blank.
    # Isolated for the same reason as zero demand: compute_eoq's holding-cost
    # floor (1e-6) would otherwise turn a near-zero mean_unit_cost into an
    # absurdly large, silently "valid-looking" order quantity.
    n_skipped_zero_cost: int = 0


def _observed_skewness(series) -> float | None:
    """Sample skewness of the demand history, or None when it cannot be estimated.

    Feeds ``demand_distribution='auto'``: the engine fits a gamma instead of a
    normal when skewness exceeds the coefficient of variation (Vandeput ch. 9).
    """
    arr = np.asarray(series, dtype=float)
    if arr.size < 3:
        return None
    # Skewness of a (near-)constant series is undefined: scipy warns about
    # catastrophic cancellation and returns a meaningless value. Flat demand is
    # not skewed, so let the engine keep the normal.
    spread = float(np.std(arr))
    if not np.isfinite(spread) or spread <= 1e-9 * max(1.0, abs(float(np.mean(arr)))):
        return None
    value = float(_sps.skew(arr))
    return value if np.isfinite(value) else None


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
    lead_time_stds: dict[str, float] | None = None,
    observed_fill_rates: dict[str, float] | None = None,
    target_fill_rate: float = 0.95,
    intake_quality: IntakeQuality | None = None,
    intake_quality_threshold: float = 0.20,
    negative_quantity_threshold: float = 0.02,
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
        lead = lead_times.get(pid, meta.lead_time_periods) if lead_times else meta.lead_time_periods
        # sigma_L. An explicit override (lead_time_stds) wins; else fall back to the
        # CSV's own lead_time_std column (meta.lead_time_std_periods, wired in src/sources.py
        # long before this override dict existed) - both are client-supplied data, just
        # via different paths, so both are labeled "client_file". Only a true absence of
        # both, i.e. the CSV carries no such column, is a "default", not a measurement -
        # and the deliverable has to say which it was.
        override = (lead_time_stds or {}).get(pid)
        if override is not None:
            lt_std, lt_std_source = float(override), "client_file"
        else:
            lt_std = meta.lead_time_std_periods
            lt_std_source = "client_file" if lt_std > 0 else "defaulted_zero"
        sl = base_levels.get(pid, service_level)

        # A SKU with zero aggregate demand (brand-new/seasonal SKU, or a messy
        # export that split one SKU's sales across spellings) would otherwise
        # reach compute_eoq's annual_demand > 0 guard and raise, killing the
        # WHOLE portfolio's run. Isolate it instead: skip and record it as a
        # distinct, flagged recommendation, then keep going.
        if inputs["annual_demand"] <= 0:
            recs.append(
                SkuRecommendation(
                    product_id=pid, method="none", intermittent=False,
                    forecast=0.0, error_std=0.0, bias=0.0, mae=0.0,
                    policy_kind="skipped_zero_demand", order_quantity=None, order_up_to=None,
                    reorder_point=0.0, safety_stock=0.0, z_factor=0.0,
                    service_level=sl, unit_cost=meta.mean_unit_cost, lead_periods=lead,
                    cycle_investment=0.0, ss_investment=0.0, investment=0.0,
                    status="review",
                )
            )
            continue

        # Symmetric to the zero-demand skip above, on the cost axis instead of the
        # demand axis: src/sources.py already excludes non-positive unit_cost rows
        # from the mean, so mean_unit_cost <= 0 here means NO row for this SKU ever
        # carried a usable cost. Without it, holding_cost's 1e-6 floor a few lines
        # down would make compute_eoq's q_star = sqrt(2*S*D/h) explode - not a
        # crash, a silently "valid-looking" order of hundreds of thousands of units
        # that the caller has no way to distinguish from a real recommendation.
        if meta.mean_unit_cost <= 0:
            recs.append(
                SkuRecommendation(
                    product_id=pid, method="none", intermittent=False,
                    forecast=0.0, error_std=0.0, bias=0.0, mae=0.0,
                    policy_kind="skipped_zero_cost", order_quantity=None, order_up_to=None,
                    reorder_point=0.0, safety_stock=0.0, z_factor=0.0,
                    service_level=sl, unit_cost=meta.mean_unit_cost, lead_periods=lead,
                    cycle_investment=0.0, ss_investment=0.0, investment=0.0,
                    status="review",
                )
            )
            continue

        holding_cost = max(holding_rate * meta.mean_unit_cost, 1e-6)
        if observed_fill_rates and pid in observed_fill_rates:
            sl = tune_service_level(
                sl, observed_fill_rates[pid], target_fill_rate,
                step=SERVICE_LEVEL_TUNING_STEP,
            )
        skewness = _observed_skewness(series)

        if fc.is_intermittent:
            pol = periodic_review_rs(
                annual_demand=inputs["annual_demand"],
                mean_demand_per_period=inputs["mean_demand_per_period"],
                demand_std_per_period=inputs["demand_std_per_period"],
                holding_cost_per_unit=holding_cost,
                fixed_order_cost=order_cost,
                lead_time_periods=lead,
                lead_time_std=lt_std,
                review_period=1.0,
                cycle_service_level=sl,
                demand_distribution="auto",
                observed_skewness=skewness,
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
                lead_time_std=lt_std,
                cycle_service_level=sl,
                demand_distribution="auto",
                observed_skewness=skewness,
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
                sigma_source=fc.sigma_source,
                buffer_distribution=pol.safety_stock.distribution,
                lead_time_std=lt_std, lead_time_std_source=lt_std_source,
                risk_periods=pol.safety_stock.risk_periods,
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
        intake_quality=intake_quality, intake_quality_threshold=intake_quality_threshold,
        negative_quantity_threshold=negative_quantity_threshold,
        n_skipped_zero_demand=sum(1 for r in recs if r.policy_kind == "skipped_zero_demand"),
        n_skipped_zero_cost=sum(1 for r in recs if r.policy_kind == "skipped_zero_cost"),
    )
