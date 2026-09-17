"""Inventory Optimization playbook.

Given canonical demand (from intake), runs the engine end to end for every SKU
— forecast (sigma_e) -> (s,Q)/(R,S) policy -> portfolio budget allocation — and
returns a structured report ready to turn into client deliverables.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
import pandas as pd
from scipy import stats as _sps

from jobs.intake import IntakeQuality
from src import order_fulfillment
from src.classification import classify_portfolio
from src.classification import service_levels as class_service_levels
from src.constraints import InventoryItem, allocate_under_budget, cheapest_feasible_lot, lot_rule
from src.fill_rate import fill_rate_from_safety_stock
from src.forecasting import (
    DEFAULT_OOS_ORIGINS,
    MIN_PERIODS_FOR_POLICY,
    forecast_with_oos_sigma,
)
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
    # Observed demand periods behind this line. Carried on the record (not just
    # counted on the report) for two reasons: jobs/qa.py asserts the minimum-history
    # gate actually fired for every priced line, and the deliverable can show the
    # reader how much history each number rests on.
    n_periods: int = 0
    # Service the client ACTUALLY got - order-line fill rate, outbound OTIF, days
    # out of stock, lost sales - with the provenance of each number attached
    # (src/order_fulfillment.py). Until this field existed the only service number
    # on the record was `service_level` above, which is the policy's TARGET: a
    # client comparing our deck against their own numbers had nothing here that
    # answered "what did we actually serve?". None only if the caller built the
    # record by hand; run() always fills it, with provenance 'not_measured' when
    # neither order lines nor a client-reported fill rate came in.
    realized_service: order_fulfillment.RealizedService | None = None
    # The beta this sized policy IMPLIES (Vandeput 7.3.1, via src/fill_rate.py):
    # provenance 'model_implied'. It is what the model expects to achieve, not what
    # happened, and it is deliberately NOT named *_fill_rate so that neither a
    # reader nor a column mapping can pick it up as realized service. None when the
    # policy has no cycle demand or no dispersion to imply a beta from.
    model_implied_beta: float | None = None
    # Supplier terms and what honouring them did to Q. `order_quantity` above is
    # the lot the supplier will ACCEPT; `order_quantity_unconstrained` is what the
    # EOQ asked for before the MOQ / case-pack rule (src.constraints.
    # cheapest_feasible_lot) moved it. The rule is applied to (s,Q) only: an (R,S)
    # policy orders a different quantity every review, so its terms are carried
    # here (`lot_rule`, `lot_moq`, `lot_multiple`) for the buyer to apply per
    # order, and the report counts those SKUs so the options can say so.
    #
    # `lot_alternative_qty` is the OTHER feasible neighbour with its own penalty:
    # the second terminal. Kern says 40 and the supplier sells by 30 -> the record
    # carries 60 (chosen) AND 30 (alternative), each priced, never just "60".
    order_quantity_unconstrained: float | None = None
    lot_rule: str = ""
    lot_moq: float = 0.0
    lot_multiple: float = 0.0
    lot_penalty_usd: float = 0.0
    lot_alternative_qty: float | None = None
    lot_alternative_penalty_usd: float | None = None


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
    # path. scm_agent/tool_options.py's inventory_options() reads this to decide
    # whether to escalate. ``IntakeQuality.reading`` (mapping / sheet / hygiene)
    # rides along so the deliverable can tell the client how their file was read.
    intake_quality: IntakeQuality | None = None
    intake_quality_threshold: float = 0.20
    # De donde salieron las columnas de servicio y que significan, cuando el frame de
    # order_lines lo declaro (jobs/intake.py::service_lines lo pone en .attrs). Viaja hasta
    # el mapa de fuentes del entregable: el deck no ve el frame de entrada, ve este reporte,
    # asi que sin este campo el caveat se queda en el resumen del operador y el cliente que
    # abre el Excel lee un OTIF sin saber si su fecha es un despacho o una llegada.
    service_note: str = ""
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
    # SKUs refused for too little demand history (see run()'s per-SKU loop and
    # src/forecasting.py's MIN_PERIODS_FOR_POLICY). Distinct from the two skips
    # above: those are degenerate DATA (no demand at all, no usable cost), this
    # one is degenerate KNOWLEDGE - there may be plenty of real demand, we simply
    # have too few observations of it to size a buffer honestly. Reported so
    # scm_agent/tool_options.py can state it as a residual instead of letting a
    # portfolio of refusals come back as a cheerful, cheap-looking plan.
    n_insufficient_history: int = 0
    # Supplier terms. `n_lot_constrained` counts (s,Q) SKUs whose file declared an
    # MOQ or a case-pack multiple (the rule was applied, whether or not it moved
    # Q); `n_lot_snapped` those it actually moved; `lot_penalty_total_usd` what
    # honouring the terms costs per year against the unconstrained EOQ, summed;
    # `n_lot_rule_unapplied` the (R,S) SKUs that carry terms this job cannot
    # apply (see SkuRecommendation.lot_rule). scm_agent/tool_options.py turns the
    # first three into a renegotiation OPTION and the last into a residual.
    n_lot_constrained: int = 0
    n_lot_snapped: int = 0
    lot_penalty_total_usd: float = 0.0
    n_lot_rule_unapplied: int = 0


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


def _lines_by_sku(order_lines: pd.DataFrame | None) -> dict[str, list[dict]]:
    """Group the client's outbound order lines by SKU, once, before the per-SKU loop."""
    if order_lines is None or order_lines.empty or "product_id" not in order_lines.columns:
        return {}
    return {
        str(pid): group.to_dict("records")
        for pid, group in order_lines.groupby("product_id")
    }


def _realized_service(
    pid: str,
    lines: list[dict],
    observed_fill_rates: dict[str, float] | None,
    stockout_days: dict[str, int] | None,
    promise_days: dict[str, float] | float | None = None,
    *,
    shipped_units: float,
    unit_cost: float,
    n_periods: int,
) -> order_fulfillment.RealizedService:
    """The service this SKU actually delivered, from the strongest evidence available.

    Order lines beat a reported rate: they carry the dates OTIF needs and the days
    a scalar beta can never produce. A reported rate is still a fact about the
    client's operation, so it is carried through and labelled 'client_reported'
    rather than being consumed by the tuning loop and thrown away. With neither,
    the answer is 'not_measured' - never a zero nobody observed.
    """
    days = (stockout_days or {}).get(pid)
    if lines:
        return order_fulfillment.measure_from_order_lines(
            pid, lines, stockout_days=days, periods_covered=n_periods,
            promise_days=promise_days,
        )
    reported = (observed_fill_rates or {}).get(pid)
    if reported is None:
        return order_fulfillment.not_measured(pid)
    return order_fulfillment.from_reported_fill_rate(
        pid,
        float(reported),
        shipped_units=shipped_units,
        unit_cost=unit_cost if unit_cost > 0 else None,
        periods_covered=n_periods,
        stockout_days=days,
    )


def _model_implied_beta(pol, kind: str, mean_demand_per_period: float, review_period: float = 1.0):
    """beta = 1 - Us/dc for the policy just sized (Vandeput 7.3.1). A MODEL number.

    Reported next to - never instead of - ``realized_service``: it says what the
    buffer is designed to achieve, which is exactly the claim a client checks
    against their own fill rate. ``None`` where the identity is undefined
    (no cycle demand, or no dispersion over the risk period).
    """
    cycle_demand = pol.order_quantity if kind == "(s, Q)" else mean_demand_per_period * review_period
    sigma_x = pol.demand_std_risk_period
    if not cycle_demand or cycle_demand <= 0 or sigma_x <= 0:
        return None
    return fill_rate_from_safety_stock(
        pol.safety_stock.safety_stock, float(cycle_demand), float(sigma_x)
    )


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
    order_lines: pd.DataFrame | None = None,
    stockout_days: dict[str, int] | None = None,
    delivery_promise_days: dict[str, float] | float | None = None,
    intake_quality: IntakeQuality | None = None,
    intake_quality_threshold: float = 0.20,
    min_periods: int = MIN_PERIODS_FOR_POLICY,
    period_freq: str | None = None,
    oos_origins: int = DEFAULT_OOS_ORIGINS,
    minimum_order_quantities: dict[str, float] | None = None,
    order_multiples: dict[str, float] | None = None,
) -> JobReport:
    """Run the inventory-optimization analysis over canonical demand.

    Safety stock is differentiated per SKU: ``service_levels`` overrides the cycle service
    level per product (or set ``differentiate_by_class`` to derive it from the ABC-XYZ class);
    ``lead_times`` overrides the per-SKU risk period (e.g. observed supplier lead times); and
    ``observed_fill_rates`` closes the loop, nudging each SKU's service level toward
    ``target_fill_rate`` so chronic stockouts raise the buffer and over-service relaxes it.

    Two inputs describe what the client's customers actually experienced, and they
    only ever feed ``SkuRecommendation.realized_service`` - never the policy:

    ``order_lines``
        One row per outbound order line, with ``product_id`` plus any of
        ``ordered_qty``, ``delivered_qty``, ``promised_date``, ``delivered_date``
        and ``unit_price``. The only input that can produce an outbound OTIF.
    ``stockout_days``
        Days at zero stock per SKU, when the client measured them. Without it the
        count falls back to the dates the order lines went short, a lower bound.

    ``observed_fill_rates`` doubles as the weakest of the three: absent order lines
    it is still reported, as a client-reported measurement, instead of being
    consumed by the tuning loop and discarded. A SKU with none of the three comes
    back 'not_measured' rather than as a service number nobody observed.

    ``minimum_order_quantities`` / ``order_multiples`` override, per SKU, the
    supplier terms the canonical frame carries in its ``moq`` / ``order_multiple``
    columns (jobs/intake.py emits them only when the file had them). An (s,Q)
    quantity is then the cheapest lot the supplier accepts, with the runner-up
    kept on the record -- see ``SkuRecommendation.lot_rule``.
    """
    if not 0 < service_level < 1:
        raise ValueError("service_level must be in (0, 1)")
    if holding_rate <= 0 or order_cost <= 0:
        raise ValueError("holding_rate and order_cost must be > 0")

    source = DataFrameDemandSource(
        demand, periods_per_year=periods_per_year, period_freq=period_freq
    )
    base_levels: dict[str, float] = dict(service_levels or {})
    if not base_levels and differentiate_by_class:
        class_items = [
            {"product_id": p, "unit_cost": source.metadata(p).mean_unit_cost,
             "demand": list(source.demand_series(p))}
            for p in source.list_products()
        ]
        base_levels = class_service_levels(classify_portfolio(class_items))
    lines_by_sku = _lines_by_sku(order_lines)
    recs: list[SkuRecommendation] = []
    items: list[InventoryItem] = []

    for pid in source.list_products():
        series = source.demand_series(pid)
        meta = source.metadata(pid)
        fc = forecast_with_oos_sigma(series, max_origins=oos_origins)
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
        n_periods = int(len(series))
        # Measured BEFORE the policy gates below, and independently of them: what the
        # customer got last quarter does not stop being a fact because this SKU has too
        # little history to size a buffer from, or no usable cost on file.
        realized = _realized_service(
            pid, lines_by_sku.get(pid, []), observed_fill_rates, stockout_days,
            delivery_promise_days,
            shipped_units=float(np.sum(series)),
            unit_cost=meta.mean_unit_cost,
            n_periods=n_periods,
        )

        # Too little history to size ANY buffer honestly. This gate runs BEFORE the
        # zero-demand and zero-cost guards below on purpose: with two observations
        # you cannot conclude "this SKU has no demand", you can only conclude "I
        # have not seen enough of it" - and labelling the first as the second is
        # exactly the confident-but-wrong claim this whole gate exists to stop.
        #
        # Refuse rather than degrade. A quantity with no dispersion behind it is
        # indistinguishable, in the deliverable, from one backed by two years of
        # history; the operator has no way to tell them apart, so we must not
        # produce it. min_periods is a parameter (not a hardcoded read of the
        # constant) so a client with, say, monthly buckets can raise it - lowering
        # it below the default is a decision someone has to make explicitly.
        if n_periods < min_periods:
            recs.append(
                SkuRecommendation(
                    product_id=pid, method="none", intermittent=False,
                    forecast=0.0, error_std=0.0, bias=0.0, mae=0.0,
                    policy_kind="insufficient_history", order_quantity=None, order_up_to=None,
                    reorder_point=0.0, safety_stock=0.0, z_factor=0.0,
                    service_level=sl, unit_cost=meta.mean_unit_cost, lead_periods=lead,
                    cycle_investment=0.0, ss_investment=0.0, investment=0.0,
                    status="review",
                    sigma_source="insufficient_history",
                    n_periods=n_periods,
                    realized_service=realized,
                )
            )
            continue

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
                    n_periods=n_periods,
                    realized_service=realized,
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
                    n_periods=n_periods,
                    realized_service=realized,
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

        # Supplier terms. Both come from the file (src/sources.py reads intake's
        # moq / order_multiple columns) unless the caller overrode them per SKU.
        moq = float((minimum_order_quantities or {}).get(pid, meta.minimum_order_quantity))
        multiple = float((order_multiples or {}).get(pid, meta.order_multiple))
        rule = lot_rule(moq, multiple)
        q_unconstrained: float | None = None
        penalty, alt_qty, alt_penalty = 0.0, None, None
        if kind == "(s, Q)" and rule:
            # The cheapest lot the supplier accepts, valued on the same cost curve
            # the EOQ came from, with the other neighbour kept as the second
            # terminal. Q, cycle stock, the implied beta and the budget item all
            # move together to the chosen lot: a plan that rounds Q but keeps the
            # cycle investment of the unrounded one understates what it costs.
            decision = cheapest_feasible_lot(
                pol.order_quantity,
                annual_demand=inputs["annual_demand"],
                holding_cost_per_unit=holding_cost,
                fixed_order_cost=order_cost,
                minimum_order_quantity=moq,
                order_multiple=multiple,
            )
            q_unconstrained = pol.order_quantity
            order_quantity = decision.chosen.quantity
            penalty = decision.chosen.penalty_usd
            if decision.alternative is not None:
                alt_qty = decision.alternative.quantity
                alt_penalty = decision.alternative.penalty_usd
            if decision.snapped:
                pol = replace(
                    pol, order_quantity=order_quantity,
                    expected_cycle_stock=order_quantity / 2.0,
                )

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
                n_periods=n_periods,
                realized_service=realized,
                model_implied_beta=_model_implied_beta(
                    pol, kind, inputs["mean_demand_per_period"]
                ),
                order_quantity_unconstrained=q_unconstrained,
                lot_rule=rule, lot_moq=moq, lot_multiple=multiple,
                lot_penalty_usd=penalty,
                lot_alternative_qty=alt_qty, lot_alternative_penalty_usd=alt_penalty,
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
        # ``getattr`` sobre ``.attrs``: un DataFrame siempre lo tiene, pero un llamador
        # puede pasar cualquier cosa con forma de frame y esto es un extra, no un
        # requisito. Un entregable no se cae por una nota que falta.
        service_note=str(getattr(order_lines, "attrs", {}).get("service_note", "") or ""),
        params={"service_level": service_level, "holding_rate": holding_rate, "order_cost": order_cost, "periods_per_year": periods_per_year},
        requested_investment=requested, cycle_floor=cycle_floor, final_investment=final,
        safety_stock_scale=scale, feasible=feasible, budget=budget,
        n_skus=len(recs), n_at_risk=sum(1 for r in recs if r.status == "high_bias"),
        n_intermittent=sum(1 for r in recs if r.intermittent),
        intake_quality=intake_quality, intake_quality_threshold=intake_quality_threshold,
        n_skipped_zero_demand=sum(1 for r in recs if r.policy_kind == "skipped_zero_demand"),
        n_skipped_zero_cost=sum(1 for r in recs if r.policy_kind == "skipped_zero_cost"),
        n_insufficient_history=sum(1 for r in recs if r.policy_kind == "insufficient_history"),
        n_lot_constrained=sum(1 for r in recs if r.policy_kind == "(s, Q)" and r.lot_rule),
        n_lot_snapped=sum(
            1 for r in recs
            if r.policy_kind == "(s, Q)" and r.order_quantity_unconstrained is not None
            and r.order_quantity != r.order_quantity_unconstrained
        ),
        lot_penalty_total_usd=sum(r.lot_penalty_usd for r in recs),
        n_lot_rule_unapplied=sum(1 for r in recs if r.policy_kind == "(R, S)" and r.lot_rule),
    )
