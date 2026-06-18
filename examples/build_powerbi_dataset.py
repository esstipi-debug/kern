#!/usr/bin/env python3
"""Build CSV dataset for Power BI Desktop."""

from __future__ import annotations

import argparse
from pathlib import Path

from src.powerbi_export import build_powerbi_dataset


def main() -> None:
    parser = argparse.ArgumentParser(description="Export Power BI dataset (CSV star schema)")
    parser.add_argument("--data", type=Path, default=Path("data/sample_demand.csv"))
    parser.add_argument("--output", type=Path, default=Path("power-bi/dataset"))
    parser.add_argument("--holding-cost", type=float, default=1.25)
    parser.add_argument("--order-cost", type=float, default=1000.0)
    parser.add_argument("--backorder-cost", type=float, default=50.0)
    parser.add_argument("--lead-time", type=float, default=2.0)
    parser.add_argument("--service-level", type=float, default=0.95)
    parser.add_argument("--simulate", action="store_true")
    args = parser.parse_args()

    paths = build_powerbi_dataset(
        args.data,
        args.output,
        holding_cost_per_period=args.holding_cost,
        order_cost=args.order_cost,
        backorder_cost=args.backorder_cost,
        lead_time=args.lead_time,
        service_level=args.service_level,
        simulate=args.simulate,
    )
    print(f"Power BI dataset: {paths.root}")
    for name in [
        "demand_history",
        "product_summary",
        "policies",
        "simulation",
        "cost_optimization",
        "fill_rate",
        "gsm_nodes",
        "newsvendor",
        "parameters",
    ]:
        p = getattr(paths, name)
        if p.exists() and p.stat().st_size > 0:
            print(f"  {p.name}")


if __name__ == "__main__":
    main()
