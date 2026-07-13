"""A5 Integrated Plan (forecast -> demand shaping -> purchase plan ->
coherence checks, plan section 5 A5 "balance") -- operator CLI entry point.

Not a registered agent Tool (see ``jobs/integrated_plan.py``'s module
docstring for why): this script IS the entry point, the same shape
``examples/run_price_intel.py`` already established for a job with its own
multi-file intake and its own ``write_deliverable``.

    python examples/run_integrated_plan.py --demand demand.csv --stock stock.csv --client "Acme Co"
    python examples/run_integrated_plan.py --demand demand.csv --stock stock.csv \
        --prices prices.csv --budget 50000 --client "Acme Co"
    python examples/run_integrated_plan.py --demo

Demand CSV columns: sku/product_id, period (optional), demand/quantity.
Stock CSV columns: product_id, on_hand, unit_cost [, reorder_point,
incoming_po, minimum_order_quantity, order_multiple, daily_demand]. The
``daily_demand`` column (E&O's own required column) is what additionally
activates P4 liquidation-based demand shaping on the SAME file -- omit it to
skip that step entirely (P2's price-cut shaping, via ``--prices``, still
works independently).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from jobs import integrated_plan as ip
from jobs.qa import verify_integrated_plan
from src.deliverable import Branding


def _demo_files(tmp_dir: Path) -> tuple[str, dict]:
    """A small illustrative scenario, entirely offline: a healthy SKU (A)
    alongside a price-cut promo SKU (PROMO) whose purchase plan has not yet
    caught up to the expected demand lift -- the plan's own literal
    coherence-check example, self-contained so ``--demo`` needs no client
    file."""
    tmp_dir.mkdir(parents=True, exist_ok=True)
    demand_rows = [
        {"sku": sku, "period": t, "demand": q}
        for sku, series in {"SKU-A": [10.0] * 12, "SKU-PROMO": [20.0] * 12}.items()
        for t, q in enumerate(series)
    ]
    demand_path = tmp_dir / "demand.csv"
    pd.DataFrame(demand_rows).to_csv(demand_path, index=False)

    price_rows = [
        {"date": "2026-01-05", "product_id": "SKU-PROMO", "price": 4.0, "quantity": 10},
        {"date": "2026-01-12", "product_id": "SKU-PROMO", "price": 4.0, "quantity": 10},
        {"date": "2026-01-19", "product_id": "SKU-PROMO", "price": 2.0, "quantity": 40},
        {"date": "2026-01-26", "product_id": "SKU-PROMO", "price": 2.0, "quantity": 40},
    ]
    price_path = tmp_dir / "prices.csv"
    pd.DataFrame(price_rows).to_csv(price_path, index=False)

    stock_rows = [
        {"product_id": "SKU-A", "on_hand": 200, "unit_cost": 5.0, "reorder_point": 20.0, "incoming_po": 0.0},
        {"product_id": "SKU-PROMO", "on_hand": 5.0, "unit_cost": 1.0, "reorder_point": 2.0, "incoming_po": 0.0},
    ]
    stock_path = tmp_dir / "stock.csv"
    pd.DataFrame(stock_rows).to_csv(stock_path, index=False)

    return str(demand_path), {"stock_path": str(stock_path), "price_history_path": str(price_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description="A5 Integrated Plan (plan section 5, PR-20).")
    parser.add_argument("--demand", help="demand-history CSV: sku/product_id, period, demand/quantity")
    parser.add_argument("--stock", help="stock/purchase-inputs CSV: product_id, on_hand, unit_cost [...]")
    parser.add_argument("--prices", help="optional price/quantity history CSV (activates P2 demand shaping)")
    parser.add_argument("--budget", type=float, help="optional portfolio inventory-investment budget cap")
    parser.add_argument("--no-liquidation", action="store_true", help="skip P4 liquidation demand shaping")
    parser.add_argument("--client", default="Client")
    parser.add_argument("--out", default="deliverables/integrated_plan", help="output directory")
    parser.add_argument("--lang", default="es", choices=("es", "en"), help="report language (E4)")
    parser.add_argument("--brand-name", help="white-label the deck under this name instead of Kern (E6)")
    parser.add_argument("--demo", action="store_true", help="run against a bundled offline scenario")
    args = parser.parse_args()

    if not args.demo and not (args.demand and args.stock):
        parser.error("pass --demand <demand.csv> --stock <stock.csv>, or --demo")

    if args.demo:
        demand_path, params = _demo_files(Path(args.out) / "_demo_inputs")
    else:
        demand_path = args.demand
        params = {"stock_path": args.stock}
        if args.prices:
            params["price_history_path"] = args.prices
        if args.budget is not None:
            params["budget"] = args.budget
        if args.no_liquidation:
            params["include_liquidation"] = False

    payload = ip.prepare(demand_path, params)
    bundle = ip.run(payload)
    print(f"Forecast: {bundle.forecast_report.summary}")
    print(f"Plan: {bundle.plan.summary}")
    if bundle.dropped_no_purchase_data:
        print(f"  (excluded for lack of purchase data: {', '.join(bundle.dropped_no_purchase_data)})")

    issues = verify_integrated_plan(bundle)
    if issues:
        print("QA FAILED - deliverable not written:", file=sys.stderr)
        for i in issues:
            print("  - " + i, file=sys.stderr)
        return 1

    failed_checks = [c for c in bundle.plan.checks if not c.passed]
    if failed_checks:
        print(f"QA passed. {len(failed_checks)} coherence check(s) FAILED (reported in the deck, not blocking):")
        for c in failed_checks:
            print(f"  [{c.check}] {c.product_id or 'portfolio'}: {c.message}")
    else:
        print("QA passed. All coherence checks passed.")

    branding = Branding(name=args.brand_name) if args.brand_name else None
    brief = f"integrated plan for {args.client}: forecast, demand shaping, and purchase-plan coherence"
    written = ip.write_deliverable(
        bundle, out_dir=args.out, client=args.client, brief=brief, lang=args.lang, branding=branding,
    )
    for kind, path in written.items():
        print(f"  {kind:18s} -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
