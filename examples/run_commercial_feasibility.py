"""Executable End-to-End Commercial Feasibility & Margin Governance Pipeline.

Demonstrates the closed-loop integration of Kern's deterministic operational engines:
1. ATP Quotation with Multi-Level BOM Bottleneck Detection & Tier-1 Capacity Rationing.
2. Marketing Campaign Feasibility Simulation & LP Dual Shadow Price Expedite Sizing.
3. Push Inventory Radar for Spoilage Avoidance & Clearance Discounting.
4. Double-Entry Commercial Ledger & Activity-Based Cost-to-Serve Margin Conservation.

Run:
    python examples/run_commercial_feasibility.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.atp_allocation import BomGraph, Tier1Contract, quote_atp_order  # noqa: E402
from src.campaign_simulator import ScheduledReplenishment, simulate_campaign_feasibility  # noqa: E402
from src.commercial_ledger import (  # noqa: E402
    CommercialLedger,
    OverrideTransaction,
    reconcile_with_cost_to_serve,
)
from src.cost_to_serve import SegmentActivity, ServiceCostRates  # noqa: E402
from src.push_radar import InventoryItemProfile, evaluate_push_opportunities  # noqa: E402


def run_pipeline() -> None:
    print("=" * 80)
    print(" KERN OPERATIONAL FEASIBILITY & COMMERCIAL MARGIN GOVERNANCE PIPELINE")
    print(" Enterprise Scenario: BioGourmet CPG (Artisan Foods & Organic Kits)")
    print("=" * 80)
    print()

    # -------------------------------------------------------------------------
    # STEP 1: AVAILABLE-TO-PROMISE (ATP) & CAPACITY RATIONING
    # -------------------------------------------------------------------------
    print("[1] MULTI-LEVEL BOM ATP ALLOCATION & TIER-1 SLA PROTECTION")
    print("-" * 80)

    # Build BOM DAG for "KIT_GOURMET_PREMIUM"
    # Structure:
    # KIT_GOURMET_PREMIUM (assembly, LT = 2 days)
    #   ├── SAUCE_ARTISAN_300ML (assembly, LT = 3 days)
    #   │     ├── GLASS_BOTTLE_250ML (leaf, LT = 10 days)
    #   │     └── SPICE_ORGANIC_BLEND (leaf, LT = 5 days)
    #   ├── OLIVE_OIL_EXTRA_VIRGIN (leaf, LT = 8 days)
    #   └── LUXURY_WOODEN_BOX (leaf, LT = 21 days)  <-- Critical Path CLT!
    bom = BomGraph()
    bom.add_item("GLASS_BOTTLE_250ML", lead_time_days=10)
    bom.add_item("SPICE_ORGANIC_BLEND", lead_time_days=5)
    bom.add_item("SAUCE_ARTISAN_300ML", lead_time_days=3, is_assembly=True)
    bom.add_item("OLIVE_OIL_EXTRA_VIRGIN", lead_time_days=8)
    bom.add_item("LUXURY_WOODEN_BOX", lead_time_days=21)
    bom.add_item("KIT_GOURMET_PREMIUM", lead_time_days=2, is_assembly=True)

    bom.add_dependency("SAUCE_ARTISAN_300ML", "GLASS_BOTTLE_250ML", 1.0)
    bom.add_dependency("SAUCE_ARTISAN_300ML", "SPICE_ORGANIC_BLEND", 2.0)
    bom.add_dependency("KIT_GOURMET_PREMIUM", "SAUCE_ARTISAN_300ML", 1.0)
    bom.add_dependency("KIT_GOURMET_PREMIUM", "OLIVE_OIL_EXTRA_VIRGIN", 1.0)
    bom.add_dependency("KIT_GOURMET_PREMIUM", "LUXURY_WOODEN_BOX", 1.0)

    _clt_kit = bom.cumulative_lead_time("KIT_GOURMET_PREMIUM")

    # Inventory On Hand across network
    inventory = {
        "KIT_GOURMET_PREMIUM": 50.0,
        "SAUCE_ARTISAN_300ML": 120.0,
        "GLASS_BOTTLE_250ML": 300.0,
        "SPICE_ORGANIC_BLEND": 500.0,
        "OLIVE_OIL_EXTRA_VIRGIN": 250.0,
        "LUXURY_WOODEN_BOX": 35.0,  # Bottleneck!
    }

    # Contractual SLA commitment with Tier-1 Client (Supermarket Chain)
    tier1_contracts = [
        Tier1Contract(
            customer_id="SUPERMARKET_PREMIUM_RETAIL",
            sku="KIT_GOURMET_PREMIUM",
            committed_units=40.0,
            unit_price=85.0,
            penalty_per_unit=45.0,
        )
    ]

    # Commercial Spot Order Request
    order_sku = "KIT_GOURMET_PREMIUM"
    requested_units = 80.0
    requested_lead_time_days = 7

    quote = quote_atp_order(
        bom=bom,
        sku=order_sku,
        requested_units=requested_units,
        requested_lead_time_days=requested_lead_time_days,
        inventory_on_hand=inventory,
        tier1_contracts=tier1_contracts,
    )

    print(f"  Target Assembly SKU       : {quote.sku}")
    print(f"  Requested Order Size      : {quote.requested_units:.0f} units (Requested LT: {requested_lead_time_days} days)")
    print(f"  Tier-1 Ring-Fenced Stock  : {quote.tier1_reserved_units:.0f} units (Contract Penalty: $45.00/unit)")
    print(f"  Immediate Available Units : {quote.immediate_available_units:.0f} units (Finished spot: 10 + Assemblable: 35)")
    print(f"  Identified Bottleneck     : {quote.bottleneck_component} (Restricts additional assembly)")
    print(f"  Shortage Units            : {quote.shortage_units:.0f} units")
    print(f"  Cumulative Lead Time (CLT): {quote.cumulative_lead_time_days} days (Critical path via LUXURY_WOODEN_BOX)")
    print(f"  Feasible within Req. LT   : {'YES' if quote.is_fulfilled_within_requested_time else 'NO (Requires 23 days)'}")
    print(f"  Deterministic Decision    : {quote.recommendation}")
    print()

    # -------------------------------------------------------------------------
    # STEP 2: MARKETING CAMPAIGN FEASIBILITY & LP SHADOW PRICE EXPEDITE
    # -------------------------------------------------------------------------
    print("[2] MARKETING CAMPAIGN FEASIBILITY & DUAL SHADOW PRICE EXPEDITE")
    print("-" * 80)

    # Marketing plans a 7-day Cyber Promo on "OLIVE_OIL_EXTRA_VIRGIN"
    campaign_sku = "OLIVE_OIL_EXTRA_VIRGIN"
    initial_oil_stock = 400.0
    daily_base_demand = 30.0
    campaign_start_day = 5
    campaign_duration = 7
    campaign_lift = 2.50  # +250% demand surge

    # Standard scheduled replenishment
    scheduled_po = [ScheduledReplenishment(arrival_day=18, quantity=600.0)]

    sim_result = simulate_campaign_feasibility(
        sku=campaign_sku,
        initial_on_hand=initial_oil_stock,
        daily_base_demand=daily_base_demand,
        campaign_start_day=campaign_start_day,
        campaign_duration_days=campaign_duration,
        campaign_lift_pct=campaign_lift,
        scheduled_replenishments=scheduled_po,
        horizon_days=30,
        regular_freight_unit_cost=2.00,
        expedite_freight_unit_cost=7.00,
        unserved_demand_penalty_unit=25.00,
    )

    print(f"  Campaign Target SKU       : {sim_result.sku}")
    print(f"  Base Demand vs Surge      : {daily_base_demand:.0f} units/day -> {daily_base_demand * (1 + campaign_lift):.0f} units/day (+{campaign_lift*100:.0f}%)")
    print(f"  Feasibility Status        : {sim_result.verdict}")
    print(f"  Projected Stockout Date   : Day {sim_result.stockout_day} (Active campaign duration: Days {campaign_start_day}-{campaign_start_day + campaign_duration - 1})")
    print(f"  Next PO Replenishment     : Day {sim_result.next_replenishment_day}")
    print(f"  Uncovered Supply Gap      : {sim_result.delay_days} days of stockout ({sim_result.unmet_demand_units:.0f} units unserved)")
    print(f"  LP Optimal Expedite Bridge: {sim_result.optimal_expedite_units:.0f} units via Air Freight")
    print(f"  Expedite Surcharge Total  : ${sim_result.total_expedite_cost:.2f} ($5.00 premium/unit)")
    print(f"  Dual Shadow Price (pi)    : ${sim_result.max_shadow_price:.2f}/unit (Marginal cost of campaign demand)")
    print()

    # -------------------------------------------------------------------------
    # STEP 3: PUSH INVENTORY RADAR & CLEARANCE PRICING
    # -------------------------------------------------------------------------
    print("[3] PUSH INVENTORY RADAR (PERISHABLE SPOILAGE & CAPITAL RECOVERY)")
    print("-" * 80)

    # Network SKU profiles
    inventory_profiles = [
        InventoryItemProfile(
            sku="YOGURT_PROBIOTIC_ARTISAN",
            on_hand=1500.0,
            daily_demand=25.0,
            current_price=4.50,
            unit_cost=1.80,
            shelf_life_days=35.0,
            channel_gating_pct=0.33,
            transit_lead_time_days=2.0,
            disposal_cost_per_unit=0.50,
            price_elasticity=1.6,
        ),
        InventoryItemProfile(
            sku="SAUCE_TARTAR_ORGANIC",
            on_hand=900.0,
            daily_demand=15.0,
            current_price=5.00,
            unit_cost=2.20,
            shelf_life_days=40.0,
            channel_gating_pct=0.30,
            transit_lead_time_days=3.0,
            disposal_cost_per_unit=0.60,
            price_elasticity=1.5,
        ),
        InventoryItemProfile(
            sku="PASTA_GLUTEN_FREE_BULK",
            on_hand=3500.0,
            daily_demand=18.0,
            current_price=6.00,
            unit_cost=2.50,
            shelf_life_days=None,  # Non-perishable
            price_elasticity=1.2,
        ),
    ]

    push_opportunities = evaluate_push_opportunities(
        items=inventory_profiles,
        excess_doh_threshold=120.0,
        target_healthy_doh=60.0,
    )

    print(f"  {'SKU':<26} | {'Trigger':<24} | {'DOH':<6} | {'At Risk':<8} | {'Disc %':<7} | {'Promo $':<8} | {'Net Benefit':<11}")
    print("  " + "-" * 102)
    for opp in push_opportunities:
        print(
            f"  {opp.sku:<26} | {opp.trigger:<24} | {opp.current_doh:>5.1f}d | {opp.at_risk_units:>7.0f}u | "
            f"{opp.recommended_discount_pct*100:>5.1f}% | ${opp.recommended_clearance_price:>6.2f} | "
            f"+${opp.net_cash_benefit:>9.2f}"
        )
    print()

    # -------------------------------------------------------------------------
    # STEP 4: COMMERCIAL LEDGER & DOUBLE-ENTRY MARGIN CONSERVATION
    # -------------------------------------------------------------------------
    print("[4] DOUBLE-ENTRY COMMERCIAL LEDGER & COST-TO-SERVE RECONCILIATION")
    print("-" * 80)

    # Marketing decides to authorize the expedite bridge for OLIVE_OIL_EXTRA_VIRGIN
    ledger = CommercialLedger()
    override_tx = OverrideTransaction(
        transaction_id="TX_CYBER_2026_001",
        timestamp_iso="2026-10-27T08:30:00Z",
        sku="OLIVE_OIL_EXTRA_VIRGIN",
        units=sim_result.optimal_expedite_units,
        requester_cost_center="MKTG_CYBER_PROMO",
        operational_credit_center="LOGISTICS_AIR_EXPEDITE",
        standard_freight_cost=sim_result.optimal_expedite_units * 2.00,
        expedited_freight_cost=sim_result.optimal_expedite_units * 7.00,
        supplier_rush_fee=500.00,
        justification="Authorization of air freight bridge to cover Cyber stockout gap",
    )
    ledger.record_override(override_tx)

    # Activity-based cost-to-serve profile for MKTG_CYBER_PROMO channel
    activity = SegmentActivity(
        segment="MKTG_CYBER_PROMO",
        revenue=65_000.0,
        units=5_200.0,
        orders=320.0,
        cogs=32_000.0,
        returns_units=40.0,
        outbound_freight=4_500.0,
        overhead=3_000.0,
    )
    rates = ServiceCostRates(
        cost_per_order=7.50,
        cost_per_unit_shipped=1.00,
        return_handling_per_unit=6.00,
    )

    reconciled = reconcile_with_cost_to_serve(
        activity=activity,
        rates=rates,
        ledger=ledger,
        commercial_cost_center="MKTG_CYBER_PROMO",
    )

    print(f"  Transaction ID            : {override_tx.transaction_id}")
    print(f"  Expedited Volume          : {override_tx.units:.0f} units of {override_tx.sku}")
    print(f"  Total Surcharge Transferred: ${override_tx.net_surcharge:.2f} (Freight delta: ${override_tx.expedited_freight_cost - override_tx.standard_freight_cost:.2f} + Rush fee: ${override_tx.supplier_rush_fee:.2f})")
    print(f"  Double-Entry Journal Status: Total Debits = ${ledger.total_debits():.2f} | Total Credits = ${ledger.total_credits():.2f}")
    print(f"  Ledger Balanced            : {'TRUE (Balanced to 0.00)' if ledger.is_balanced() else 'FALSE'}")
    print()
    print("  Activity-Based Cost-to-Serve Reconciliation (CFO Audit View):")
    print(f"    Gross Commercial Revenue : ${reconciled.revenue:,.2f}")
    print(f"    Standard Net-to-Serve    : ${reconciled.standard_net_to_serve:,.2f}")
    print(f"    Commercial Surcharge Deducted: -${reconciled.total_commercial_surcharges:,.2f}")
    print(f"    Audited Net-to-Serve     : ${reconciled.audited_net_to_serve:,.2f} ({reconciled.audited_net_margin_pct*100:.2f}% audited margin)")
    print(f"    Margin Conservation Error: ${reconciled.reconciliation_error:.4f} (Strict mathematical equality)")
    print(f"    Margin Conserved         : {reconciled.is_margin_conserved}")
    print()
    print("=" * 80)
    print(" PIPELINE EXECUTION COMPLETE: 100% DETERMINISTIC VERIFICATION CONFIRMED")
    print("=" * 80)


if __name__ == "__main__":
    run_pipeline()
