"""End-to-end: a monthly S&OP / IBP cadence -> ranked option-package -> client deck.

Demonstrates capability gap #2. Takes a 12-month consensus demand plan (the output
of the demand-review step), runs the reconciliation as three competing supply
strategies (chase / level / hybrid), ranks them into a protected option-package, and
writes the S&OP / IBP deck. With ``--data`` it forecasts the demand baseline from a
real history first, showing the bridge from the forecasting engine into S&OP.

Usage:
    python examples/run_sop_cycle.py
    python examples/run_sop_cycle.py --data data/sample_demand.csv
    python examples/run_sop_cycle.py --opening 400 --target 150 --out deliverables/sop
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jobs.sop_deliverable import build as build_deck  # noqa: E402
from scm_agent.knowledge import KnowledgeBase  # noqa: E402
from src.forecasting import forecast_demand  # noqa: E402
from src.sop import CostModel, run_sop_cycle  # noqa: E402

# A seasonal 12-month shape (peaks into Q4), scaled by a base monthly level. In a real
# engagement these indices come from the demand plan; here they make the cadence visible.
_SEASONAL_INDEX = (0.85, 0.80, 0.95, 1.00, 1.05, 1.10, 1.05, 1.10, 1.20, 1.35, 1.45, 1.10)
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _baseline_from_history(path: str) -> float:
    """Forecast a flat monthly baseline from a demand history (first product)."""
    from src.data_loader import list_products, load_demand_csv

    product = list_products(path)[0]
    series = load_demand_csv(path, product_id=product)
    return float(forecast_demand(series.to_numpy(), method="auto").forecast)


def _citations(limit: int = 3) -> tuple[str, ...]:
    """Pull S&OP grounding from the L3 books graph; fall back to the canonical sources."""
    kb = KnowledgeBase()
    hits = kb.search("sales and operations aggregate planning", graph="books", limit=limit)
    cites = []
    for c in hits:
        text = c.label
        if c.source:
            text += f" - {c.source}"
        if c.location:
            text += f", {c.location}"
        cites.append(text)
    if not cites:
        cites = [
            "Chopra & Meindl, Supply Chain Management - Sales & Operations / Aggregate Planning",
            "Heizer & Render, Operations Management - Aggregate Planning and S&OP",
        ]
    return tuple(cites)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a monthly S&OP / IBP cadence and write the deck.")
    parser.add_argument("--data", default=None, help="optional demand history CSV to set the baseline")
    parser.add_argument("--base", type=float, default=1000.0, help="base monthly demand level")
    parser.add_argument("--opening", type=float, default=900.0, help="opening inventory (units)")
    parser.add_argument("--target", type=float, default=300.0, help="per-period inventory target / buffer")
    parser.add_argument("--client", default="Demo Client")
    parser.add_argument("--out", default="deliverables/sop")
    args = parser.parse_args()

    base = _baseline_from_history(args.data) if args.data else args.base
    demand = [round(base * idx, 1) for idx in _SEASONAL_INDEX]

    cost = CostModel(
        holding_per_unit_per_period=1.0,
        shortage_per_unit_per_period=6.0,
        capacity_change_per_unit=3.0,
    )
    review = run_sop_cycle(
        demand,
        opening_inventory=args.opening,
        target=args.target,
        cost=cost,
        period_labels=_MONTHS,
        confidence=0.8,
    )

    print(f"\n=== S&OP / IBP cadence: {args.client} ===")
    print(f"  horizon          : {len(demand)} months, total demand {sum(demand):,.0f} units")
    print(f"  baseline         : {base:,.1f}/month" + (f" (forecast from {args.data})" if args.data else ""))
    print("\n  Option-package (ranked, recommended first):")
    for i, opt in enumerate(review.outcome.options, 1):
        flag = "  <= recommended" if opt.recommended else ""
        print(f"   {i}. {opt.summary}{flag}")
        print(f"      trade-offs: {opt.tradeoffs}")

    rec = review.recommended
    print(f"\n  Recommended plan : {rec.name}")
    print(f"    fill rate      : {rec.fill_rate * 100:.0f}%")
    print(f"    peak inventory : {rec.peak_inventory:,.0f} units (avg {rec.average_inventory:,.0f})")
    print(f"    plan cost      : {rec.total_cost:,.0f}")
    print(f"    capacity flex  : {rec.capacity_changes:,.0f} units")
    gaps = [p.period for p in rec.periods if p.shortfall > 0]
    if gaps:
        print(f"    demand-supply gap in: {', '.join(gaps)} ({rec.total_shortfall:,.0f} units short)")

    deck = build_deck(review, client=args.client, prepared="2026-06-23", citations=_citations())
    paths = deck.write_all(args.out)
    print("\n  Deliverable written:")
    for kind, path in paths.items():
        print(f"    {kind:8}: {path}")


if __name__ == "__main__":
    main()
