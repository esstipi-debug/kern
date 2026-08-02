#!/usr/bin/env python3
"""Run Part I–II workflow: EOQ, policies, simulation (Vandeput 2020)."""

from __future__ import annotations

import argparse
from pathlib import Path

from src.data_loader import annualize_demand, demand_stats, load_demand_csv
from src.eoq import compute_eoq, round_review_period_power_of_two
from src.policies import continuous_review_sq, periodic_review_rs
from src.simulation import simulate_rs_policy, simulate_sq_policy


def main() -> None:
    parser = argparse.ArgumentParser(description="Inventory optimization (Vandeput 2020, Part I–II)")
    parser.add_argument(
        "--data",
        type=Path,
        default=Path("data/sample_demand.csv"),
        help="CSV with date, product_id, quantity",
    )
    parser.add_argument("--product", default="SKU-A", help="Product filter")
    parser.add_argument("--holding-cost", type=float, default=1.75, help="h (currency/unit/year)")
    parser.add_argument("--order-cost", type=float, default=50.0, help="k (currency/order)")
    parser.add_argument("--lead-time", type=float, default=2.0, help="Lead time in periods")
    parser.add_argument("--service-level", type=float, default=0.95, help="Cycle service level alpha")
    parser.add_argument("--periods-per-year", type=float, default=52.0, help="Weeks per year")
    parser.add_argument("--review-period", type=float, default=None, help="R for (R,S); default from EOQ")
    parser.add_argument("--simulate", action="store_true", help="Run Monte Carlo validation")
    args = parser.parse_args()

    series = load_demand_csv(args.data, product_id=args.product)
    stats = demand_stats(series)
    mu = stats["mean_demand_per_period"]
    sigma = stats["demand_std_per_period"]
    annual_demand = annualize_demand(mu, args.periods_per_year)

    eoq = compute_eoq(annual_demand, args.holding_cost, args.order_cost)
    review = args.review_period or round_review_period_power_of_two(
        eoq.review_period * args.periods_per_year
    )

    sq = continuous_review_sq(
        annual_demand=annual_demand,
        mean_demand_per_period=mu,
        demand_std_per_period=sigma,
        holding_cost_per_unit=args.holding_cost,
        fixed_order_cost=args.order_cost,
        lead_time_periods=args.lead_time,
        cycle_service_level=args.service_level,
    )

    rs = periodic_review_rs(
        annual_demand=annual_demand,
        mean_demand_per_period=mu,
        demand_std_per_period=sigma,
        holding_cost_per_unit=args.holding_cost,
        fixed_order_cost=args.order_cost,
        lead_time_periods=args.lead_time,
        review_period=review,
        cycle_service_level=args.service_level,
    )

    print(f"Product: {args.product}")
    print(f"Demand: mean={mu:.1f}/period, std={sigma:.1f}, annualized={annual_demand:.0f}")
    print()
    print("--- EOQ (Ch. 2) ---")
    print(f"Q* = {eoq.order_quantity:.1f}")
    print(f"Optimal yearly cost = {eoq.optimal_total_cost:.2f}")
    print(f"Optimal review period ~ {eoq.review_period * args.periods_per_year:.1f} periods")
    print(f"Rounded R (power-of-2) = {review:.0f} periods")
    print()
    print("--- (s, Q) policy (Ch. 5) ---")
    print(f"Q = {sq.order_quantity:.1f}")
    print(f"s (reorder point) = {sq.reorder_point:.1f}")
    print(f"Safety stock = {sq.safety_stock.safety_stock:.1f}")
    print()
    print("--- (R, S) policy (Ch. 5) ---")
    print(f"R = {rs.review_period:.0f}")
    print(f"S (order-up-to) = {rs.order_up_to_level:.1f}")
    print(f"Safety stock = {rs.safety_stock.safety_stock:.1f}")
    print(f"Expected on-hand ~ {rs.expected_cycle_stock + rs.safety_stock.safety_stock:.1f} (not S!)")

    if args.simulate:
        print()
        print("--- Simulation (Ch. 5.3) ---")
        hist = series.to_numpy()
        sq_sim = simulate_sq_policy(
            reorder_point=sq.reorder_point,
            order_quantity=sq.order_quantity,
            lead_time_periods=int(args.lead_time),
            historical_demand=hist,
        )
        rs_sim = simulate_rs_policy(
            order_up_to_level=rs.order_up_to_level,
            lead_time_periods=int(args.lead_time),
            review_period=int(review),
            historical_demand=hist,
        )
        print(f"(s,Q) simulated cycle SL: {sq_sim.simulated_cycle_service_level:.1%}")
        print(f"(s,Q) mean on-hand: {sq_sim.mean_on_hand:.1f}")
        print(f"(R,S) simulated cycle SL: {rs_sim.simulated_cycle_service_level:.1%}")
        print(f"(R,S) mean on-hand: {rs_sim.mean_on_hand:.1f} (target S={rs.order_up_to_level:.0f})")


if __name__ == "__main__":
    main()
