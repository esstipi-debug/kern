"""Fulfill an inventory-optimization job from a client file, end to end.

    python examples/run_inventory_job.py --data client_demand.csv --budget 50000
    python examples/run_inventory_job.py --data orders.xlsx --service-level 0.97 \
        --holding-rate 0.25 --order-cost 80 --out deliverables/acme --client "Acme Co"

Pipeline: intake (adapt any client columns) -> playbook (forecast -> policy ->
budget) -> QA (verify numbers) -> deliverables (Excel + report + CSV).
"""

from __future__ import annotations

import argparse
import sys

from jobs import deliverables, intake, qa
from jobs.inventory_optimization import run


def main() -> int:
    parser = argparse.ArgumentParser(description="Inventory Optimization job from a client demand file.")
    parser.add_argument("--data", required=True, help="client CSV/Excel demand file")
    parser.add_argument("--out", default="deliverables", help="output directory for deliverables")
    parser.add_argument("--client", default="Client", help="client name for the report")
    parser.add_argument("--period", default="W", help="pandas period for bucketing (W weekly, D daily, MS monthly)")
    parser.add_argument("--service-level", type=float, default=0.95)
    parser.add_argument("--holding-rate", type=float, default=0.25)
    parser.add_argument("--order-cost", type=float, default=75.0)
    parser.add_argument("--budget", type=float, default=None)
    parser.add_argument("--periods-per-year", type=float, default=52.0)
    args = parser.parse_args()

    demand = intake.prepare(args.data, period=args.period)
    print(f"Intake: {demand['product_id'].nunique()} SKUs · {len(demand)} period-rows from {args.data}")

    report = run(
        demand,
        service_level=args.service_level,
        holding_rate=args.holding_rate,
        order_cost=args.order_cost,
        budget=args.budget,
        periods_per_year=args.periods_per_year,
    )

    issues = qa.verify(report)
    if issues:
        print("QA FAILED — deliverables not written:", file=sys.stderr)
        for i in issues:
            print("  - " + i, file=sys.stderr)
        return 1

    written = deliverables.write_all(report, args.out, client=args.client)
    print(f"QA passed. Recommended investment ${report.final_investment:,.0f}"
          + (f" (budget ${report.budget:,.0f}, scale {report.safety_stock_scale * 100:.0f}%)" if report.budget else ""))
    for kind, path in written.items():
        print(f"  {kind:7s} -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
