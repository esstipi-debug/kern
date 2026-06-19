#!/usr/bin/env python3
"""End-to-end workflow: Vandeput (2020) Ch. 1-13 on one SKU."""

from __future__ import annotations

import argparse
from pathlib import Path

from src.cost_optimization import optimize_rs_policy, optimize_sq_policy
from src.data_loader import annualize_demand, demand_stats, load_demand_csv
from src.distributions import safety_stock_gamma, select_distribution
from src.eoq import compute_eoq, round_review_period_power_of_two
from src.excel_export import gsm_allocation_to_dict, write_analysis_workbook
from src.export import write_policy_comparison, write_summary_csv
from src.fill_rate import safety_stock_for_fill_rate
from src.multi_echelon import optimize_serial_gsm, simulate_serial_gsm
from src.newsvendor import muffin_pmf, optimal_newsvendor_discrete
from src.policies import continuous_review_sq, periodic_review_rs
from src.powerbi_export import build_powerbi_dataset
from src.risk_period import demand_over_risk_period
from src.simulation import simulate_rs_policy, simulate_sq_policy
from src.simulation_opt import find_best_safety_stock_smart_start


def main() -> None:
    parser = argparse.ArgumentParser(description="Full inventory analysis (Ch. 1-13)")
    parser.add_argument("--data", type=Path, default=Path("data/sample_demand.csv"))
    parser.add_argument("--product", default="SKU-A")
    parser.add_argument("--holding-cost", type=float, default=1.25)
    parser.add_argument("--order-cost", type=float, default=1000.0)
    parser.add_argument("--backorder-cost", type=float, default=50.0)
    parser.add_argument("--lead-time", type=float, default=2.0)
    parser.add_argument("--service-level", type=float, default=0.95)
    parser.add_argument("--fill-rate-target", type=float, default=0.98)
    parser.add_argument("--periods-per-year", type=float, default=52.0)
    parser.add_argument("--simulate", action="store_true")
    parser.add_argument("--export", type=Path, default=None, help="CSV output path")
    parser.add_argument("--excel", type=Path, default=None, help="Excel .xlsx output path")
    parser.add_argument("--powerbi", type=Path, default=None, help="Power BI dataset folder")
    args = parser.parse_args()

    series = load_demand_csv(args.data, product_id=args.product)
    stats = demand_stats(series)
    mu, sigma = stats["mean_demand_per_period"], stats["demand_std_per_period"]
    annual_demand = annualize_demand(mu, args.periods_per_year)
    h_year = args.holding_cost * args.periods_per_year
    hist = series.to_numpy()

    eoq = compute_eoq(annual_demand, h_year, args.order_cost)
    review = round_review_period_power_of_two(eoq.review_period * args.periods_per_year)

    sq = continuous_review_sq(
        annual_demand=annual_demand,
        mean_demand_per_period=mu,
        demand_std_per_period=sigma,
        holding_cost_per_unit=args.holding_cost * args.periods_per_year,
        fixed_order_cost=args.order_cost,
        lead_time_periods=args.lead_time,
        cycle_service_level=args.service_level,
        periods_per_year=args.periods_per_year,
    )
    rs = periodic_review_rs(
        annual_demand=annual_demand,
        mean_demand_per_period=mu,
        demand_std_per_period=sigma,
        holding_cost_per_unit=args.holding_cost * args.periods_per_year,
        fixed_order_cost=args.order_cost,
        lead_time_periods=args.lead_time,
        review_period=review,
        cycle_service_level=args.service_level,
    )

    print(f"=== Complete analysis: {args.product} ===")
    print(f"Demand: mean={mu:.1f}/period, std={sigma:.1f}, D={annual_demand:.0f}/year\n")

    print("[Ch. 2] EOQ")
    print(f"  Q*={eoq.order_quantity:.0f}, C*={eoq.optimal_total_cost:.0f}, R~{review:.0f}\n")

    print("[Ch. 5] Policies")
    print(f"  (s,Q): Q={sq.order_quantity:.0f}, s={sq.reorder_point:.0f}, Ss={sq.safety_stock.safety_stock:.0f}")
    print(f"  (R,S): R={rs.review_period:.0f}, S={rs.order_up_to_level:.0f}, Ss={rs.safety_stock.safety_stock:.0f}\n")

    risk = demand_over_risk_period(mu, sigma, args.lead_time, review_period=review)
    fr = safety_stock_for_fill_rate(risk.mean_demand, risk.demand_std, args.fill_rate_target)
    print("[Ch. 7] Fill rate")
    print(f"  beta={args.fill_rate_target:.0%} -> Ss={fr.safety_stock:.1f}, alpha={fr.cycle_service_level:.0%}\n")

    best_rs = optimize_rs_policy(mu, sigma, args.lead_time, args.holding_cost, args.order_cost, args.backorder_cost)
    best_sq = optimize_sq_policy(annual_demand, mu, sigma, args.lead_time, h_year, args.order_cost, args.backorder_cost)
    print("[Ch. 8] Cost optimization")
    print(f"  Optimal R={best_rs.review_period:.0f}, cost/period={best_rs.cost.total:.2f}")
    print(f"  Optimal (s,Q): Q={best_sq.order_quantity:.0f}, s={best_sq.reorder_point:.0f}\n")

    fit = select_distribution(hist)
    _, ss_gamma = safety_stock_gamma(mu * 5, sigma * (5**0.5), args.service_level)
    print("[Ch. 9] Distribution")
    print(f"  Recommended: {fit.recommended.value}, gamma Ss(tau=5)={ss_gamma:.0f}\n")

    gsm = optimize_serial_gsm([4, 3, 2], 100, 25, [1, 2, 4], args.service_level, 1.0)
    gsm_sim = simulate_serial_gsm(gsm, [4, 3, 2], periods=2000, seed=1) if args.simulate else None
    nv = optimal_newsvendor_discrete(muffin_pmf(), price=6, unit_cost=2, salvage_value=1)
    print("[Ch. 10] GSM")
    print(f"  Optimal x_tau={gsm.risk_periods}, holding cost={gsm.total_holding_cost:.0f}")
    if gsm_sim:
        print(f"  Sim fill rate={gsm_sim.fill_rate:.1%}, mean backorders={gsm_sim.mean_backorders:.1f}\n")
    else:
        print()

    print("[Ch. 11] Newsvendor")
    print(f"  Muffins Q*={nv.optimal_quantity:.0f}, profit={nv.expected_profit:.2f}\n")

    sim_row: dict[str, float] = {}
    if args.simulate:
        sq_sim = simulate_sq_policy(sq.reorder_point, sq.order_quantity, int(args.lead_time), historical_demand=hist)
        rs_sim = simulate_rs_policy(rs.order_up_to_level, int(args.lead_time), int(review), historical_demand=hist)
        sim_opt, start_ss = find_best_safety_stock_smart_start(
            mu, sigma, int(args.lead_time), int(review),
            args.holding_cost, args.order_cost, args.backorder_cost,
            step_size=max(1, int(sigma / 2)), search_radius=max(20, sigma * 3), periods=3000, seed=42,
        )
        print("[Ch. 5/13] Simulation")
        print(f"  (s,Q) cycle SL={sq_sim.simulated_cycle_service_level:.1%}, on-hand={sq_sim.mean_on_hand:.0f}")
        print(f"  (R,S) cycle SL={rs_sim.simulated_cycle_service_level:.1%}, on-hand={rs_sim.mean_on_hand:.0f}")
        print(f"  Sim-opt Ss={sim_opt.safety_stock:.0f} (start {start_ss:.0f}), cost={sim_opt.total_cost:.2f}\n")
        sim_row = {
            "sq_cycle_sl": sq_sim.simulated_cycle_service_level,
            "rs_cycle_sl": rs_sim.simulated_cycle_service_level,
            "sim_opt_ss": sim_opt.safety_stock,
            "sim_opt_cost": sim_opt.total_cost,
        }

    if args.export:
        row = write_policy_comparison(
            args.product,
            {"Q": eoq.order_quantity, "cost": eoq.optimal_total_cost},
            {"Q": sq.order_quantity, "s": sq.reorder_point, "Ss": sq.safety_stock.safety_stock},
            {"R": rs.review_period, "S": rs.order_up_to_level, "Ss": rs.safety_stock.safety_stock},
            simulation=sim_row or None,
            extra={
                "mean_demand": mu,
                "demand_std": sigma,
                "optimal_R": best_rs.review_period,
                "optimal_rs_cost": best_rs.cost.total,
                "gsm_cost": gsm.total_holding_cost,
                "distribution": fit.recommended.value,
            },
        )
        out = write_summary_csv([row], args.export)
        print(f"Exported CSV: {out}")

    if args.excel:
        sim_export = {
            **sim_row,
            **(
                {
                    "gsm_fill_rate": gsm_sim.fill_rate,
                    "gsm_mean_backorders": gsm_sim.mean_backorders,
                }
                if gsm_sim
                else {}
            ),
        }
        xlsx = write_analysis_workbook(
            args.excel,
            product_id=args.product,
            parameters={
                "annual_demand": annual_demand,
                "mean_demand_per_period": mu,
                "demand_std": sigma,
                "holding_cost_per_period": args.holding_cost,
                "order_cost": args.order_cost,
                "backorder_cost": args.backorder_cost,
                "lead_time": args.lead_time,
                "service_level": args.service_level,
                "fill_rate_target": args.fill_rate_target,
            },
            results={
                "EOQ Q*": eoq.order_quantity,
                "EOQ cost": eoq.optimal_total_cost,
                "(s,Q) Q": sq.order_quantity,
                "(s,Q) s": sq.reorder_point,
                "(R,S) R": rs.review_period,
                "(R,S) S": rs.order_up_to_level,
                "Fill rate Ss": fr.safety_stock,
                "Optimal R": best_rs.review_period,
                "Distribution": fit.recommended.value,
            },
            gsm=gsm_allocation_to_dict(gsm),
            simulation=sim_export or None,
            newsvendor={
                "Q*": nv.optimal_quantity,
                "critical ratio": nv.critical_ratio,
                "expected profit": nv.expected_profit,
            },
        )
        print(f"Exported Excel: {xlsx}")

    if args.powerbi:
        pbi = build_powerbi_dataset(
            args.data,
            args.powerbi,
            holding_cost_per_period=args.holding_cost,
            order_cost=args.order_cost,
            backorder_cost=args.backorder_cost,
            lead_time=args.lead_time,
            service_level=args.service_level,
            fill_rate_target=args.fill_rate_target,
            periods_per_year=args.periods_per_year,
            simulate=args.simulate,
        )
        print(f"Exported Power BI dataset: {pbi.root}")


if __name__ == "__main__":
    main()
