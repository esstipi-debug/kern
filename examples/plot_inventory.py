#!/usr/bin/env python3
"""Plot demand history and policy levels (Vandeput Ch. 5 visualization)."""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib.pyplot as plt

from src.data_loader import load_demand_csv, product_metadata
from src.eoq import compute_eoq, round_review_period_power_of_two
from src.policies import periodic_review_rs


def main() -> None:
    parser = argparse.ArgumentParser(description="Inventory analysis charts")
    parser.add_argument("--data", type=Path, default=Path("data/sample_demand.csv"))
    parser.add_argument("--product", default="SKU-A")
    parser.add_argument("--output", type=Path, default=Path("output/inventory_chart.png"))
    parser.add_argument("--holding-rate", type=float, default=0.25)
    parser.add_argument("--order-cost", type=float, default=1000.0)
    args = parser.parse_args()

    meta = product_metadata(args.data, args.product)
    demand = load_demand_csv(args.data, product_id=args.product)
    annual_demand = meta.mean_demand_per_period * 52
    h_year = args.holding_rate * meta.mean_unit_cost * 52
    eoq = compute_eoq(annual_demand, h_year, args.order_cost)
    review = round_review_period_power_of_two(eoq.review_period * 52)
    rs = periodic_review_rs(
        annual_demand,
        meta.mean_demand_per_period,
        meta.demand_std_per_period,
        h_year,
        args.order_cost,
        meta.lead_time_periods,
        review,
        0.95,
    )

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(demand.index, demand.values, label="Demand", color="#1f4e79", linewidth=1.2)
    ax.axhline(rs.order_up_to_level, color="#c00000", linestyle="--", label=f"S={rs.order_up_to_level:.0f}")
    ax.axhline(rs.order_up_to_level - rs.safety_stock.safety_stock, color="#548235", linestyle=":",
               label="Cycle stock + mean demand")
    ax.axhline(meta.mean_demand_per_period, color="#7030a0", linestyle=":", alpha=0.7, label="Mean demand")
    ax.set_title(f"{args.product} — demand vs (R,S) levels (Vandeput 2020)")
    ax.set_xlabel("Period")
    ax.set_ylabel("Units")
    ax.legend(loc="upper right")
    fig.tight_layout()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output, dpi=120)
    print(f"Chart saved: {args.output}")


if __name__ == "__main__":
    main()
