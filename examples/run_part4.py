#!/usr/bin/env python3
"""Part IV workflow: gamma, GSM, newsvendor, KDE, simulation opt (Vandeput 2020, Ch. 9-13)."""

from __future__ import annotations

import argparse

from src.data_loader import demand_stats, load_demand_csv
from src.discrete_demand import histogram_pmf, kde_pmf
from src.distributions import safety_stock_gamma, select_distribution
from src.multi_echelon import optimize_serial_gsm, serial_gsm_cases
from src.newsvendor import muffin_pmf, optimal_newsvendor_discrete
from src.simulation_opt import find_best_safety_stock_smart_start


def main() -> None:
    parser = argparse.ArgumentParser(description="Part IV models (Ch. 9-13)")
    parser.add_argument("--data", default="data/sample_demand.csv")
    parser.add_argument("--product", default="SKU-A")
    args = parser.parse_args()

    series = load_demand_csv(args.data, product_id=args.product)
    stats = demand_stats(series)
    mu, sigma = stats["mean_demand_per_period"], stats["demand_std_per_period"]
    data = series.values.astype(float)

    print(f"Product: {args.product} | mean={mu:.1f}, std={sigma:.1f}")
    print()

    # Ch. 9 — gamma vs normal
    print("--- Gamma demand (Ch. 9) ---")
    fit = select_distribution(data)
    print(f"Observed skewness: {fit.observed_skewness:.2f}")
    print(f"Recommended: {fit.recommended.value}")
    tau = 5
    mu_x, sigma_x = mu * tau, sigma * (tau**0.5)
    _, ss_gamma = safety_stock_gamma(mu_x, sigma_x, 0.95)
    ss_normal = 1.645 * sigma_x
    print(f"Risk period tau={tau}: Ss normal~{ss_normal:.0f}, Ss gamma~{ss_gamma:.0f}")
    print()

    # Ch. 10 — serial GSM
    print("--- Multi-echelon GSM (Ch. 10) ---")
    lead_times = [4, 3, 2]
    holding = [1, 2, 4]
    cases = serial_gsm_cases(lead_times, review_period=1.0)
    for i, case in enumerate(cases, 1):
        print(f"  Case #{i}: x_tau={case}")
    best = optimize_serial_gsm(lead_times, 100, 25, holding, 0.95, 1.0)
    print(f"Optimal case #{best.case_id}, holding cost = {best.total_holding_cost:.0f}")
    print(f"Echelon S levels: {[round(s) for s in best.echelon_order_up_to]}")
    print()

    # Ch. 11 — newsvendor muffins
    print("--- Newsvendor muffins (Ch. 11) ---")
    pmf = muffin_pmf()
    nv = optimal_newsvendor_discrete(pmf, price=6, unit_cost=2, salvage_value=1)
    print(f"Critical ratio: {nv.critical_ratio:.0%}")
    print(f"Optimal bake quantity Q*: {nv.optimal_quantity:.0f}")
    print(f"Expected profit: {nv.expected_profit:.2f} EUR")
    print()

    # Ch. 12 — discrete demand from data
    print("--- Discrete demand PMF (Ch. 12) ---")
    hist = histogram_pmf(data, bins=8)
    kde = kde_pmf(data)
    print(f"Histogram PMF: {len(hist.values)} bins")
    print(f"KDE PMF: support {kde.values.min()}-{kde.values.max()}, sum(p)={kde.probabilities.sum():.3f}")
    print()

    # Ch. 13 — simulation optimization
    print("--- Simulation optimization (Ch. 13) ---")
    sim, start_ss = find_best_safety_stock_smart_start(
        mean_demand=mu,
        std_demand=sigma,
        lead_time_periods=2,
        review_period=1,
        holding_cost_per_period=1.25,
        fixed_order_cost=1000,
        backorder_cost=50,
        step_size=max(1, int(sigma / 2)),
        search_radius=max(20, sigma * 3),
        periods=3_000,
        seed=42,
    )
    print(f"Analytical start Ss: {start_ss:.1f}")
    print(f"Sim-optimal Ss: {sim.safety_stock:.1f}")
    print(f"Sim cost/period: {sim.total_cost:.2f} (fill rate {sim.fill_rate:.1%})")


if __name__ == "__main__":
    main()
