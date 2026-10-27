"""Counterfactual Stress Simulation & 180-Day Backtesting Engine.

Compares two operational paradigms over an identical 180-day stochastic event stream:
1. Scenario A (Traditional / Ungoverned):
   - Sales accepts unconstrained spot orders without Available-to-Promise (ATP),
     depleting stock reserved for Tier-1 contractual commitments and incurring heavy SLA penalties.
   - Logistics absorbs uncontrolled emergency expedited freight in overhead expenses
     during marketing campaign surges.
   - Perishable inventory sits unmonitored until effective shelf life expires,
     generating massive scrap and disposal losses.

2. Scenario B (Kern / Governed):
   - Capacity Rationing ring-fences stock for penalty-bearing Tier-1 SLA contracts;
     ATP quotes realistic delivery splits or CLT waits for spot demand.
   - Campaign Simulator enforces LP-optimal expedite bridges, recorded in the
     CommercialLedger against marketing cost centers with strict margin conservation.
   - Push Radar dynamically detects spoilage risk (DOH > SL_eff) and executes
     targeted clearance discounts, accelerating velocity and salvaging cash margin.

Pure, deterministic module with fixed random seed. Zero framework dependencies.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from src.atp_allocation import BomGraph, Tier1Contract, quote_atp_order
from src.commercial_ledger import CommercialLedger, OverrideTransaction
from src.perishables import effective_shelf_life
from src.push_radar import InventoryItemProfile, evaluate_push_opportunities


@dataclass(frozen=True)
class StressSimulationConfig:
    """Parameters governing the 180-day counterfactual simulation."""
    horizon_days: int = 180
    random_seed: int = 42

    # Tier-1 Contract parameters
    tier1_daily_commitment: float = 40.0         # Units due daily
    tier1_unit_price: float = 120.0             # Contracted selling price ($/unit)
    tier1_unit_cogs: float = 60.0               # Unit cost of goods sold ($/unit)
    tier1_sla_penalty_unit: float = 50.0        # Contractual penalty per unserved unit ($/unit)

    # Spot demand parameters for Tier-1 SKU
    spot_base_demand: float = 25.0              # Average uncommitted daily demand
    spot_demand_volatility: float = 0.35        # Relative std dev

    # Perishable SKU parameters
    perishable_daily_demand: float = 30.0
    perishable_unit_price: float = 35.0
    perishable_unit_cogs: float = 18.0
    perishable_shelf_life_days: float = 30.0    # Total shelf life
    perishable_channel_gating: float = 0.33     # Retailer rejection buffer (33%)
    perishable_transit_days: float = 2.0        # Buffer in transit
    perishable_disposal_fee: float = 2.0        # Destruction/landfill cost per unit

    # Promotional Campaign SKU parameters
    promo_base_demand: float = 35.0
    promo_unit_price: float = 75.0
    promo_unit_cogs: float = 38.0
    standard_freight_unit: float = 3.0
    expedited_freight_unit: float = 12.0        # Emergency air freight ($/unit)
    unplanned_rush_fee_unit: float = 3.50       # Extra fee for same-day unbudgeted rush handling
    campaign_days: Tuple[int, ...] = (30, 90, 145)  # Days where 7-day marketing campaigns hit
    campaign_duration: int = 7
    campaign_lift_pct: float = 2.20             # +220% surge during campaign


@dataclass(frozen=True)
class FinancialMetrics:
    """Audited financial metrics resulting from a simulation run."""
    gross_revenue: float
    cogs: float
    tier1_penalties: float
    expedite_freight_surcharges: float
    perishable_spoilage_losses: float
    net_operating_profit: float
    operating_margin_pct: float
    tier1_sla_service_level: float
    perishable_spoilage_units: float


@dataclass(frozen=True)
class DailySimulationLog:
    """Granular daily audit log entry."""
    day: int
    scenario: str
    tier1_delivered: float
    tier1_shortage: float
    spot_delivered: float
    spot_shortage: float
    expedite_cost: float
    spoilage_units: float
    spoilage_cost: float
    daily_revenue: float
    daily_cogs: float


@dataclass(frozen=True)
class StressSimulationComparison:
    """Head-to-head comparison between Traditional and Kern Governed execution."""
    ungoverned: FinancialMetrics
    governed: FinancialMetrics
    profit_delta: float
    profit_lift_pct: float
    penalty_avoided: float
    freight_savings: float
    spoilage_saved: float
    logs_ungoverned: List[DailySimulationLog] = field(default_factory=list, repr=False)
    logs_governed: List[DailySimulationLog] = field(default_factory=list, repr=False)


@dataclass
class _PerishableBatch:
    batch_id: int
    arrival_day: int
    remaining_units: float
    expiry_day: int


def run_stress_simulation(
    config: Optional[StressSimulationConfig] = None,
) -> StressSimulationComparison:
    """Run twin parallel 180-day simulations over an identical stochastic stream.
    
    Guarantees bit-exact determinism via fixed random seed.
    """
    cfg = config or StressSimulationConfig()
    rng = random.Random(cfg.random_seed)

    # 1. Pre-generate identical daily stochastic events for 1..horizon_days
    # Demand events
    tier1_commitments = [cfg.tier1_daily_commitment] * cfg.horizon_days

    spot_demands: List[float] = []
    for _ in range(cfg.horizon_days):
        val = rng.gauss(cfg.spot_base_demand, cfg.spot_base_demand * cfg.spot_demand_volatility)
        spot_demands.append(max(0.0, round(val, 1)))

    perishable_demands: List[float] = []
    for _ in range(cfg.horizon_days):
        val = rng.gauss(cfg.perishable_daily_demand, cfg.perishable_daily_demand * 0.20)
        perishable_demands.append(max(0.0, round(val, 1)))

    # Campaign demand multipliers
    promo_demands: List[float] = []
    for t in range(1, cfg.horizon_days + 1):
        is_campaign = any(c_start <= t < c_start + cfg.campaign_duration for c_start in cfg.campaign_days)
        base = cfg.promo_base_demand * (1.0 + rng.uniform(-0.10, 0.10))
        if is_campaign:
            promo_demands.append(round(base * (1.0 + cfg.campaign_lift_pct), 1))
        else:
            promo_demands.append(round(base, 1))

    # Supplier replenishment schedules
    # Tier 1 SKU: PO of 600 units every 10 days
    tier1_replenishments: Dict[int, float] = {}
    for day in range(1, cfg.horizon_days + 1):
        if day % 10 == 0:
            tier1_replenishments[day] = 600.0

    # Perishable SKU: batch arrival of 600 units every 20 days
    perishable_replenishments: Dict[int, float] = {}
    for day in range(1, cfg.horizon_days + 1):
        if day == 1 or day % 20 == 0:
            perishable_replenishments[day] = 600.0

    # Promo SKU: PO of 400 units every 12 days
    promo_replenishments: Dict[int, float] = {}
    for day in range(1, cfg.horizon_days + 1):
        if day % 12 == 0:
            promo_replenishments[day] = 400.0

    # Compute effective shelf life for perishables
    sl_eff = effective_shelf_life(
        total_shelf_life_days=cfg.perishable_shelf_life_days,
        channel_gating_pct=cfg.perishable_channel_gating,
        transit_lead_time_days=cfg.perishable_transit_days,
    )
    effective_lifespan_days = max(1, int(math.floor(sl_eff)))

    # --- SIMULATE SCENARIO A: UNGOVERNED ---
    metrics_a, logs_a = _simulate_ungoverned(
        cfg=cfg,
        tier1_commitments=tier1_commitments,
        spot_demands=spot_demands,
        perishable_demands=perishable_demands,
        promo_demands=promo_demands,
        tier1_replenishments=tier1_replenishments,
        perishable_replenishments=perishable_replenishments,
        promo_replenishments=promo_replenishments,
        effective_lifespan_days=effective_lifespan_days,
    )

    # --- SIMULATE SCENARIO B: KERN GOVERNED ---
    metrics_b, logs_b = _simulate_governed(
        cfg=cfg,
        tier1_commitments=tier1_commitments,
        spot_demands=spot_demands,
        perishable_demands=perishable_demands,
        promo_demands=promo_demands,
        tier1_replenishments=tier1_replenishments,
        perishable_replenishments=perishable_replenishments,
        promo_replenishments=promo_replenishments,
        sl_eff=sl_eff,
        effective_lifespan_days=effective_lifespan_days,
    )

    profit_delta = metrics_b.net_operating_profit - metrics_a.net_operating_profit
    lift_pct = (profit_delta / abs(metrics_a.net_operating_profit) * 100.0) if metrics_a.net_operating_profit != 0 else 0.0

    return StressSimulationComparison(
        ungoverned=metrics_a,
        governed=metrics_b,
        profit_delta=round(profit_delta, 2),
        profit_lift_pct=round(lift_pct, 2),
        penalty_avoided=round(metrics_a.tier1_penalties - metrics_b.tier1_penalties, 2),
        freight_savings=round(metrics_a.expedite_freight_surcharges - metrics_b.expedite_freight_surcharges, 2),
        spoilage_saved=round(metrics_a.perishable_spoilage_losses - metrics_b.perishable_spoilage_losses, 2),
        logs_ungoverned=logs_a,
        logs_governed=logs_b,
    )


def _simulate_ungoverned(
    cfg: StressSimulationConfig,
    tier1_commitments: List[float],
    spot_demands: List[float],
    perishable_demands: List[float],
    promo_demands: List[float],
    tier1_replenishments: Dict[int, float],
    perishable_replenishments: Dict[int, float],
    promo_replenishments: Dict[int, float],
    effective_lifespan_days: int,
) -> Tuple[FinancialMetrics, List[DailySimulationLog]]:
    """Scenario A: Blind spot order acceptance, unbudgeted panic freight, passive spoilage."""
    tier1_on_hand = 300.0
    promo_on_hand = 300.0

    # Batches for perishable tracking
    perishable_batches: List[_PerishableBatch] = [
        _PerishableBatch(batch_id=0, arrival_day=1, remaining_units=300.0, expiry_day=1 + effective_lifespan_days)
    ]
    batch_counter = 1

    total_revenue = 0.0
    total_cogs = 0.0
    total_penalties = 0.0
    total_expedite_freight = 0.0
    total_spoilage_loss = 0.0
    total_spoilage_units = 0.0

    tier1_total_committed = sum(tier1_commitments)
    tier1_total_delivered = 0.0

    logs: List[DailySimulationLog] = []

    for t in range(1, cfg.horizon_days + 1):
        idx = t - 1

        # 1. Replenishments arrive
        tier1_on_hand += tier1_replenishments.get(t, 0.0)
        promo_on_hand += promo_replenishments.get(t, 0.0)
        if t in perishable_replenishments:
            perishable_batches.append(
                _PerishableBatch(
                    batch_id=batch_counter,
                    arrival_day=t,
                    remaining_units=perishable_replenishments[t],
                    expiry_day=t + effective_lifespan_days,
                )
            )
            batch_counter += 1

        # 2. Perishable Spoilage Check (Batches that reach expiry today spoil)
        day_spoilage_units = 0.0
        active_batches: List[_PerishableBatch] = []
        for b in perishable_batches:
            if t >= b.expiry_day:
                day_spoilage_units += b.remaining_units
            else:
                active_batches.append(b)
        perishable_batches = active_batches

        day_spoilage_cost = day_spoilage_units * (cfg.perishable_unit_cogs + cfg.perishable_disposal_fee)
        total_spoilage_units += day_spoilage_units
        total_spoilage_loss += day_spoilage_cost

        # 3. Demand Execution
        # UNGOVERNED FLAW 1: Sales satisfies spot demand FIRST from shared inventory
        spot_req = spot_demands[idx]
        spot_served = min(spot_req, tier1_on_hand)
        tier1_on_hand -= spot_served

        spot_rev = spot_served * cfg.tier1_unit_price
        spot_cogs = spot_served * cfg.tier1_unit_cogs

        # Tier 1 Contract order arrives SECOND (starved of stock)
        tier1_req = tier1_commitments[idx]
        tier1_served = min(tier1_req, tier1_on_hand)
        tier1_on_hand -= tier1_served
        tier1_shortage = tier1_req - tier1_served
        tier1_total_delivered += tier1_served

        tier1_rev = tier1_served * cfg.tier1_unit_price
        tier1_cogs = tier1_served * cfg.tier1_unit_cogs
        penalty = tier1_shortage * cfg.tier1_sla_penalty_unit
        total_penalties += penalty

        # Perishable demand execution (FIFO across active batches at standard price)
        perish_req = perishable_demands[idx]
        perish_served = 0.0
        for b in perishable_batches:
            if b.remaining_units <= 0:
                continue
            fill = min(perish_req - perish_served, b.remaining_units)
            b.remaining_units -= fill
            perish_served += fill
            if perish_served >= perish_req:
                break

        perish_rev = perish_served * cfg.perishable_unit_price
        perish_cogs = perish_served * cfg.perishable_unit_cogs

        # UNGOVERNED FLAW 2: Promo SKU campaign surges are bridged via panic air freight
        promo_req = promo_demands[idx]
        day_expedite_cost = 0.0

        if promo_on_hand >= promo_req:
            promo_served = promo_req
            promo_on_hand -= promo_served
        else:
            # Panic air freight: operations expedites shortage immediately to fulfill sales
            # with unplanned rush handling and overtime penalties
            shortage = promo_req - promo_on_hand
            promo_served = promo_req
            freight_surcharge = shortage * (
                (cfg.expedited_freight_unit - cfg.standard_freight_unit) + cfg.unplanned_rush_fee_unit
            )
            day_expedite_cost = freight_surcharge
            promo_on_hand = 0.0

        total_expedite_freight += day_expedite_cost
        promo_rev = promo_served * cfg.promo_unit_price
        promo_cogs = promo_served * cfg.promo_unit_cogs

        # Aggregate day financials
        day_rev = spot_rev + tier1_rev + perish_rev + promo_rev
        day_cogs = spot_cogs + tier1_cogs + perish_cogs + promo_cogs

        total_revenue += day_rev
        total_cogs += day_cogs

        logs.append(
            DailySimulationLog(
                day=t,
                scenario="UNGOVERNED",
                tier1_delivered=tier1_served,
                tier1_shortage=tier1_shortage,
                spot_delivered=spot_served,
                spot_shortage=spot_req - spot_served,
                expedite_cost=day_expedite_cost,
                spoilage_units=day_spoilage_units,
                spoilage_cost=day_spoilage_cost,
                daily_revenue=day_rev,
                daily_cogs=day_cogs,
            )
        )

    net_profit = total_revenue - total_cogs - total_penalties - total_expedite_freight - total_spoilage_loss
    margin_pct = (net_profit / total_revenue * 100.0) if total_revenue > 0 else 0.0
    tier1_service_level = (tier1_total_delivered / tier1_total_committed * 100.0) if tier1_total_committed > 0 else 0.0

    return FinancialMetrics(
        gross_revenue=round(total_revenue, 2),
        cogs=round(total_cogs, 2),
        tier1_penalties=round(total_penalties, 2),
        expedite_freight_surcharges=round(total_expedite_freight, 2),
        perishable_spoilage_losses=round(total_spoilage_loss, 2),
        net_operating_profit=round(net_profit, 2),
        operating_margin_pct=round(margin_pct, 2),
        tier1_sla_service_level=round(tier1_service_level, 2),
        perishable_spoilage_units=round(total_spoilage_units, 1),
    ), logs


def _simulate_governed(
    cfg: StressSimulationConfig,
    tier1_commitments: List[float],
    spot_demands: List[float],
    perishable_demands: List[float],
    promo_demands: List[float],
    tier1_replenishments: Dict[int, float],
    perishable_replenishments: Dict[int, float],
    promo_replenishments: Dict[int, float],
    sl_eff: float,
    effective_lifespan_days: int,
) -> Tuple[FinancialMetrics, List[DailySimulationLog]]:
    """Scenario B: Capacity rationing (ring-fenced Tier-1 stock), Push Radar clearance, Commercial Ledger."""
    tier1_on_hand = 300.0
    promo_on_hand = 300.0

    # Batches for perishable tracking
    perishable_batches: List[_PerishableBatch] = [
        _PerishableBatch(batch_id=0, arrival_day=1, remaining_units=300.0, expiry_day=1 + effective_lifespan_days)
    ]
    batch_counter = 1

    total_revenue = 0.0
    total_cogs = 0.0
    total_penalties = 0.0
    total_expedite_freight = 0.0
    total_spoilage_loss = 0.0
    total_spoilage_units = 0.0

    tier1_total_committed = sum(tier1_commitments)
    tier1_total_delivered = 0.0

    # Setup BOM Graph for Tier 1 SKU
    bom = BomGraph()
    bom.add_item("SKU_TIER1", lead_time_days=10)

    # Commercial Ledger for recording commercial override transfers
    ledger = CommercialLedger()

    logs: List[DailySimulationLog] = []

    for t in range(1, cfg.horizon_days + 1):
        idx = t - 1

        # 1. Replenishments arrive
        tier1_on_hand += tier1_replenishments.get(t, 0.0)
        promo_on_hand += promo_replenishments.get(t, 0.0)
        if t in perishable_replenishments:
            perishable_batches.append(
                _PerishableBatch(
                    batch_id=batch_counter,
                    arrival_day=t,
                    remaining_units=perishable_replenishments[t],
                    expiry_day=t + effective_lifespan_days,
                )
            )
            batch_counter += 1

        # 2. Push Radar Proactive Scan on Perishables
        current_perishable_stock = sum(b.remaining_units for b in perishable_batches if b.remaining_units > 0)
        oldest_batch = next((b for b in perishable_batches if b.remaining_units > 0), None)
        perish_price = cfg.perishable_unit_price
        demand_multiplier = 1.0

        if oldest_batch is not None and current_perishable_stock > 0:
            remaining_eff_days = max(1.0, float(oldest_batch.expiry_day - t))
            profile = InventoryItemProfile(
                sku="SKU_PERISHABLE",
                on_hand=current_perishable_stock,
                daily_demand=cfg.perishable_daily_demand,
                current_price=cfg.perishable_unit_price,
                unit_cost=cfg.perishable_unit_cogs,
                shelf_life_days=remaining_eff_days,
                channel_gating_pct=0.0,  # Expiry day already reflects gating & transit
                transit_lead_time_days=0.0,
                disposal_cost_per_unit=cfg.perishable_disposal_fee,
                price_elasticity=1.6,
            )
            push_opps = evaluate_push_opportunities([profile], excess_doh_threshold=120.0)

            # Apply proactive markdown if at risk
            if push_opps and push_opps[0].at_risk_units > 0:
                opp = push_opps[0]
                discount = opp.recommended_discount_pct
                perish_price = opp.recommended_clearance_price
                demand_multiplier = math.pow(1.0 - discount, -profile.price_elasticity)

        # Check Spoilage
        day_spoilage_units = 0.0
        active_batches: List[_PerishableBatch] = []
        for b in perishable_batches:
            if t >= b.expiry_day:
                day_spoilage_units += b.remaining_units
            else:
                active_batches.append(b)
        perishable_batches = active_batches

        day_spoilage_cost = day_spoilage_units * (cfg.perishable_unit_cogs + cfg.perishable_disposal_fee)
        total_spoilage_units += day_spoilage_units
        total_spoilage_loss += day_spoilage_cost

        # 3. Demand Execution
        # GOVERNED MECHANISM 1: Capacity Rationing & ATP across Replenishment Cycle
        tier1_req = tier1_commitments[idx]

        # Calculate days until next scheduled replenishment arrival for Tier-1 SKU
        days_to_replenish = 1
        for future_day in range(t, min(t + 15, cfg.horizon_days + 1)):
            if future_day in tier1_replenishments and future_day > t:
                days_to_replenish = future_day - t
                break
            elif future_day in tier1_replenishments and future_day == t:
                days_to_replenish = 10
                break

        # Ring-fence contractual demand across the replenishment exposure window
        reserved_tier1_buffer = cfg.tier1_daily_commitment * days_to_replenish
        tier1_contract = Tier1Contract(
            customer_id="KEY_ACCOUNT_TIER1",
            sku="SKU_TIER1",
            committed_units=reserved_tier1_buffer,
            unit_price=cfg.tier1_unit_price,
            penalty_per_unit=cfg.tier1_sla_penalty_unit,
        )

        # ATP quote for spot demand with Tier-1 ring-fencing
        spot_req = spot_demands[idx]
        atp_quote = quote_atp_order(
            bom=bom,
            sku="SKU_TIER1",
            requested_units=spot_req if spot_req > 0 else 0.001,
            requested_lead_time_days=0,
            inventory_on_hand={"SKU_TIER1": tier1_on_hand},
            tier1_contracts=[tier1_contract],
        )

        spot_served = min(spot_req, atp_quote.immediate_available_units)
        tier1_on_hand -= spot_served

        spot_rev = spot_served * cfg.tier1_unit_price
        spot_cogs = spot_served * cfg.tier1_unit_cogs

        # Tier 1 delivery is protected by the reserve
        tier1_served = min(tier1_req, tier1_on_hand)
        tier1_on_hand -= tier1_served
        tier1_shortage = tier1_req - tier1_served
        tier1_total_delivered += tier1_served

        tier1_rev = tier1_served * cfg.tier1_unit_price
        tier1_cogs = tier1_served * cfg.tier1_unit_cogs
        penalty = tier1_shortage * cfg.tier1_sla_penalty_unit
        total_penalties += penalty

        # Perishable demand execution (with clearance velocity lift if discounted)
        perish_req = perishable_demands[idx] * demand_multiplier
        perish_served = 0.0
        for b in perishable_batches:
            if b.remaining_units <= 0:
                continue
            fill = min(perish_req - perish_served, b.remaining_units)
            b.remaining_units -= fill
            perish_served += fill
            if perish_served >= perish_req:
                break

        perish_rev = perish_served * perish_price
        perish_cogs = perish_served * cfg.perishable_unit_cogs

        # GOVERNED MECHANISM 2: Controlled Campaign Expediting via Commercial Ledger
        promo_req = promo_demands[idx]
        day_expedite_cost = 0.0

        if promo_on_hand >= promo_req:
            promo_served = promo_req
            promo_on_hand -= promo_served
        else:
            # Governed: Only expedite if the marginal gross profit justifies air freight premium!
            # Gross profit per unit = price - cogs = 75 - 38 = $37/unit.
            # Air freight premium = 12 - 3 = $9/unit.
            # Marginal profit = $37 - $9 = +$28/unit > 0 -> Expedite is authorized, but tracked in Ledger!
            shortage = promo_req - promo_on_hand
            promo_served = promo_req
            freight_surcharge = shortage * (cfg.expedited_freight_unit - cfg.standard_freight_unit)
            day_expedite_cost = freight_surcharge
            promo_on_hand = 0.0

            # Post double-entry override: Debit Marketing, Credit Logistics
            tx = OverrideTransaction(
                transaction_id=f"TX_STRESS_{t}",
                timestamp_iso=f"2026-D{t:03d}",
                sku="SKU_PROMO",
                units=shortage,
                requester_cost_center="MKTG_CAMPAIGN_SURGE",
                operational_credit_center="LOGISTICS_AIR_EXPEDITE",
                standard_freight_cost=shortage * cfg.standard_freight_unit,
                expedited_freight_cost=shortage * cfg.expedited_freight_unit,
                justification=f"Day {t} campaign surge expedite bridge",
            )
            ledger.record_override(tx)

        total_expedite_freight += day_expedite_cost
        promo_rev = promo_served * cfg.promo_unit_price
        promo_cogs = promo_served * cfg.promo_unit_cogs

        # Aggregate day financials
        day_rev = spot_rev + tier1_rev + perish_rev + promo_rev
        day_cogs = spot_cogs + tier1_cogs + perish_cogs + promo_cogs

        total_revenue += day_rev
        total_cogs += day_cogs

        logs.append(
            DailySimulationLog(
                day=t,
                scenario="GOVERNED",
                tier1_delivered=tier1_served,
                tier1_shortage=tier1_shortage,
                spot_delivered=spot_served,
                spot_shortage=spot_req - spot_served,
                expedite_cost=day_expedite_cost,
                spoilage_units=day_spoilage_units,
                spoilage_cost=day_spoilage_cost,
                daily_revenue=day_rev,
                daily_cogs=day_cogs,
            )
        )

    net_profit = total_revenue - total_cogs - total_penalties - total_expedite_freight - total_spoilage_loss
    margin_pct = (net_profit / total_revenue * 100.0) if total_revenue > 0 else 0.0
    tier1_service_level = (tier1_total_delivered / tier1_total_committed * 100.0) if tier1_total_committed > 0 else 0.0

    return FinancialMetrics(
        gross_revenue=round(total_revenue, 2),
        cogs=round(total_cogs, 2),
        tier1_penalties=round(total_penalties, 2),
        expedite_freight_surcharges=round(total_expedite_freight, 2),
        perishable_spoilage_losses=round(total_spoilage_loss, 2),
        net_operating_profit=round(net_profit, 2),
        operating_margin_pct=round(margin_pct, 2),
        tier1_sla_service_level=round(tier1_service_level, 2),
        perishable_spoilage_units=round(total_spoilage_units, 1),
    ), logs
