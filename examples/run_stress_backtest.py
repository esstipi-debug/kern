"""Counterfactual 180-Day Stress Backtest: Traditional vs Kern Governed Execution.

Simulates 6 months (180 days) of operations under identical daily stochastic events:
- Contractual Tier-1 demand under strict SLA delivery penalties.
- Volatile commercial spot demand.
- 3 surprise marketing campaign demand surges (+220% lift).
- Perishable product batches subject to channel-gating and transit shelf-life degradation.

Run:
    python examples/run_stress_backtest.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.stress_simulation import StressSimulationConfig, run_stress_simulation  # noqa: E402


def format_currency(val: float) -> str:
    """Format floating point values as financial currency."""
    return f"${val:,.2f}"


def run_backtest() -> None:
    print("=" * 84)
    print(" KERN COUNTERFACTUAL STRESS BACKTEST: 180-DAY (6 MONTH) PARALLEL SIMULATION")
    print(" Benchmark: Ungoverned Traditional Operations vs Kern Governed Operations")
    print("=" * 84)
    print()

    cfg = StressSimulationConfig(horizon_days=180, random_seed=42)
    comparison = run_stress_simulation(cfg)

    ungov = comparison.ungoverned
    gov = comparison.governed

    # Financial Comparison Table
    col_w = 20
    header_metric = "Financial Metric (180 Days)"
    print(f"  {header_metric:<36} | {'Scenario A (Traditional)':<{col_w}} | {'Scenario B (Kern)':<{col_w}} | {'Variance / Delta':<{col_w}}")
    print("  " + "-" * 102)

    rows = [
        ("Gross Revenue", format_currency(ungov.gross_revenue), format_currency(gov.gross_revenue), format_currency(gov.gross_revenue - ungov.gross_revenue)),
        ("Cost of Goods Sold (COGS)", format_currency(ungov.cogs), format_currency(gov.cogs), format_currency(gov.cogs - ungov.cogs)),
        ("Contractual Tier-1 SLA Penalties", format_currency(ungov.tier1_penalties), format_currency(gov.tier1_penalties), f"-{format_currency(comparison.penalty_avoided)} (-{comparison.penalty_avoided/ungov.tier1_penalties*100:.1f}%)" if ungov.tier1_penalties > 0 else "$0.00"),
        ("Expedited Freight Surcharges", format_currency(ungov.expedite_freight_surcharges), format_currency(gov.expedite_freight_surcharges), f"-{format_currency(comparison.freight_savings)} (-{comparison.freight_savings/ungov.expedite_freight_surcharges*100:.1f}%)" if ungov.expedite_freight_surcharges > 0 else "$0.00"),
        ("Perishable Spoilage & Disposal", format_currency(ungov.perishable_spoilage_losses), format_currency(gov.perishable_spoilage_losses), f"-{format_currency(comparison.spoilage_saved)} (-{comparison.spoilage_saved/ungov.perishable_spoilage_losses*100:.1f}%)" if ungov.perishable_spoilage_losses > 0 else "$0.00"),
    ]

    for label, a_val, b_val, delta in rows:
        print(f"  {label:<36} | {a_val:>{col_w}} | {b_val:>{col_w}} | {delta:>{col_w}}")

    print("  " + "=" * 102)
    net_line = (
        f"  {'NET OPERATING PROFIT':<36} | "
        f"{format_currency(ungov.net_operating_profit):>{col_w}} | "
        f"{format_currency(gov.net_operating_profit):>{col_w}} | "
        f"+{format_currency(comparison.profit_delta):>{col_w}} (+{comparison.profit_lift_pct:.2f}%)"
    )
    print(net_line)
    margin_line = (
        f"  {'Operating Profit Margin':<36} | "
        f"{ungov.operating_margin_pct:>19.2f}% | "
        f"{gov.operating_margin_pct:>19.2f}% | "
        f"+{gov.operating_margin_pct - ungov.operating_margin_pct:>18.2f}% bps"
    )
    print(margin_line)
    print()

    # Operational Service & Spoilage Metrics Table
    print("  OPERATIONAL SERVICE LEVELS & PHYSICAL INVENTORY WASTE")
    print("  " + "-" * 102)
    print(
        f"  {'Tier-1 SLA Contract Fulfillment':<36} | "
        f"{ungov.tier1_sla_service_level:>19.2f}% | "
        f"{gov.tier1_sla_service_level:>19.2f}% | "
        f"+{gov.tier1_sla_service_level - ungov.tier1_sla_service_level:>19.2f}%"
    )
    spoilage_pct_reduction = (
        (ungov.perishable_spoilage_units - gov.perishable_spoilage_units) / ungov.perishable_spoilage_units * 100.0
        if ungov.perishable_spoilage_units > 0 else 0.0
    )
    print(
        f"  {'Perishable Spoilage Scrap Volume':<36} | "
        f"{ungov.perishable_spoilage_units:>18.1f} u | "
        f"{gov.perishable_spoilage_units:>18.1f} u | "
        f"-{ungov.perishable_spoilage_units - gov.perishable_spoilage_units:>13.1f} u (-{spoilage_pct_reduction:.1f}%)"
    )
    print()

    # Executive Diagnostic
    print("=" * 84)
    print(" ROOT CAUSAL MECHANISMS IDENTIFIED BY BACKTEST")
    print("=" * 84)
    print("  1. Capacity Rationing & ATP Protection:")
    print("     In Scenario A, spot orders depleted uncommitted inventory, starving Tier-1")
    print(f"     contractual deliveries and generating {format_currency(ungov.tier1_penalties)} in contractual SLA penalties.")
    print(f"     Scenario B ring-fenced stock across replenishment cycles, recovering {format_currency(comparison.penalty_avoided)}.")
    print()
    print("  2. Push Radar Markdown vs Waste Dump:")
    print(f"     Scenario A allowed {ungov.perishable_spoilage_units:.1f} units to spoil at full cost ({format_currency(ungov.perishable_spoilage_losses)} write-off).")
    print("     Scenario B detected DOH > SL_eff and applied targeted clearance discounts,")
    print(f"     reducing waste to {gov.perishable_spoilage_units:.1f} units and saving {format_currency(comparison.spoilage_saved)} in net cash.")
    print()
    print("  3. Advance Campaign Expedite LP Planning vs Panic Freight:")
    print("     Scenario A bridged marketing surges via reactive last-minute air freight")
    print(f"     with unplanned rush fees ({format_currency(ungov.expedite_freight_surcharges)} absorbed into logistics overhead).")
    print("     Scenario B solved the expedite bridge in advance via HiGHS LP and transferred")
    print(f"     the exact surcharge into the CommercialLedger, capturing {format_currency(comparison.freight_savings)} in freight savings.")
    print()
    print("=" * 84)
    print(f" BOTTOM-LINE IMPACT: +{format_currency(comparison.profit_delta)} (+{comparison.profit_lift_pct:.2f}% NET PROFIT LIFT)")
    print("=" * 84)


if __name__ == "__main__":
    run_backtest()
