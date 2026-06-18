"""Power BI dataset export — star-schema CSVs from Vandeput models."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from src.cost_optimization import optimize_rs_policy, optimize_sq_policy
from src.data_loader import annualize_demand, demand_stats, load_demand_csv
from src.distributions import safety_stock_gamma, select_distribution
from src.eoq import compute_eoq, round_review_period_power_of_two
from src.excel_export import gsm_allocation_to_dict
from src.fill_rate import safety_stock_for_fill_rate
from src.multi_echelon import optimize_serial_gsm, simulate_serial_gsm
from src.newsvendor import muffin_pmf, optimal_newsvendor_discrete
from src.policies import continuous_review_sq, periodic_review_rs
from src.risk_period import demand_over_risk_period
from src.simulation import simulate_rs_policy, simulate_sq_policy


@dataclass(frozen=True)
class PowerBIDatasetPaths:
    root: Path
    demand_history: Path
    product_summary: Path
    policies: Path
    simulation: Path
    cost_optimization: Path
    fill_rate: Path
    gsm_nodes: Path
    newsvendor: Path
    parameters: Path


def _write(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def analyze_product(
    data_path: Path,
    product_id: str,
    *,
    holding_cost_per_period: float = 1.25,
    order_cost: float = 1000.0,
    backorder_cost: float = 50.0,
    lead_time: float = 2.0,
    service_level: float = 0.95,
    fill_rate_target: float = 0.98,
    periods_per_year: float = 52.0,
    simulate: bool = False,
) -> dict[str, pd.DataFrame]:
    """Run analysis for one SKU; return tables for Power BI."""
    series = load_demand_csv(data_path, product_id=product_id)
    stats = demand_stats(series)
    mu, sigma = stats["mean_demand_per_period"], stats["demand_std_per_period"]
    annual_demand = annualize_demand(mu, periods_per_year)
    h_year = holding_cost_per_period * periods_per_year
    hist = series.to_numpy()

    eoq = compute_eoq(annual_demand, h_year, order_cost)
    review = round_review_period_power_of_two(eoq.review_period * periods_per_year)
    sq = continuous_review_sq(
        annual_demand, mu, sigma, h_year, order_cost, lead_time, service_level, periods_per_year
    )
    rs = periodic_review_rs(
        annual_demand, mu, sigma, h_year, order_cost, lead_time, review, service_level
    )
    risk = demand_over_risk_period(mu, sigma, lead_time, review_period=review)
    fr = safety_stock_for_fill_rate(risk.mean_demand, risk.demand_std, fill_rate_target)
    best_rs = optimize_rs_policy(mu, sigma, lead_time, holding_cost_per_period, order_cost, backorder_cost)
    best_sq = optimize_sq_policy(
        annual_demand, mu, sigma, lead_time, h_year, order_cost, backorder_cost
    )
    fit = select_distribution(hist)
    _, ss_gamma = safety_stock_gamma(mu * 5, sigma * (5**0.5), service_level)
    gsm = optimize_serial_gsm([4, 3, 2], 100, 25, [1, 2, 4], service_level, 1.0)

    product_row = {
        "product_id": product_id,
        "mean_demand_per_period": mu,
        "demand_std_per_period": sigma,
        "annual_demand": annual_demand,
        "periods_observed": int(stats["periods"]),
        "distribution": fit.recommended.value,
        "observed_skewness": fit.observed_skewness,
        "gamma_ss_tau5": ss_gamma,
    }

    policy_rows = [
        {
            "product_id": product_id,
            "policy": "EOQ",
            "Q": eoq.order_quantity,
            "s": None,
            "S": None,
            "R": review,
            "safety_stock": None,
            "optimal_cost": eoq.optimal_total_cost,
        },
        {
            "product_id": product_id,
            "policy": "sQ",
            "Q": sq.order_quantity,
            "s": sq.reorder_point,
            "S": None,
            "R": None,
            "safety_stock": sq.safety_stock.safety_stock,
            "optimal_cost": None,
        },
        {
            "product_id": product_id,
            "policy": "RS",
            "Q": None,
            "s": None,
            "S": rs.order_up_to_level,
            "R": rs.review_period,
            "safety_stock": rs.safety_stock.safety_stock,
            "optimal_cost": None,
        },
    ]

    cost_rows = [
        {
            "product_id": product_id,
            "model": "RS_optimal",
            "review_period": best_rs.review_period,
            "order_quantity": None,
            "reorder_point": None,
            "order_up_to": best_rs.order_up_to_level,
            "cycle_service_level": best_rs.cost.cycle_service_level,
            "fill_rate": best_rs.cost.fill_rate,
            "total_cost_per_period": best_rs.cost.total,
            "holding_cost": best_rs.cost.holding,
            "ordering_cost": best_rs.cost.transaction,
            "backorder_cost": best_rs.cost.backorder,
        },
        {
            "product_id": product_id,
            "model": "sQ_optimal",
            "review_period": None,
            "order_quantity": best_sq.order_quantity,
            "reorder_point": best_sq.reorder_point,
            "order_up_to": None,
            "cycle_service_level": best_sq.cost.cycle_service_level,
            "fill_rate": best_sq.cost.fill_rate,
            "total_cost_per_period": best_sq.cost.total / periods_per_year,
            "holding_cost": best_sq.cost.holding / periods_per_year,
            "ordering_cost": best_sq.cost.transaction / periods_per_year,
            "backorder_cost": best_sq.cost.backorder / periods_per_year,
        },
    ]

    fill_row = {
        "product_id": product_id,
        "target_fill_rate": fill_rate_target,
        "safety_stock": fr.safety_stock,
        "cycle_service_level": fr.cycle_service_level,
        "inventory_level": risk.mean_demand + fr.safety_stock,
        "mean_demand_risk": risk.mean_demand,
        "demand_std_risk": risk.demand_std,
    }

    gsm_dict = gsm_allocation_to_dict(gsm)
    gsm_rows = [
        {
            "product_id": product_id,
            "case_id": gsm.case_id,
            "node_index": n["index"],
            "lead_time": n["lead_time"],
            "x_tau": n["risk_period"],
            "safety_stock": n["safety_stock"],
            "local_S": n["order_up_to"],
            "holding_cost_rate": n["holding_cost"],
            "total_case_cost": gsm.total_holding_cost,
        }
        for n in gsm_dict["nodes"]
    ]

    sim_rows: list[dict[str, Any]] = []
    if simulate:
        sq_sim = simulate_sq_policy(
            sq.reorder_point, sq.order_quantity, int(lead_time), historical_demand=hist
        )
        rs_sim = simulate_rs_policy(
            rs.order_up_to_level, int(lead_time), int(review), historical_demand=hist
        )
        gsm_sim = simulate_serial_gsm(gsm, [4, 3, 2], periods=2000, seed=1)
        sim_rows = [
            {
                "product_id": product_id,
                "policy": "sQ",
                "simulated_cycle_sl": sq_sim.simulated_cycle_service_level,
                "simulated_period_sl": sq_sim.simulated_period_service_level,
                "mean_on_hand": sq_sim.mean_on_hand,
                "stockout_periods": sq_sim.stockout_periods,
            },
            {
                "product_id": product_id,
                "policy": "RS",
                "simulated_cycle_sl": rs_sim.simulated_cycle_service_level,
                "simulated_period_sl": rs_sim.simulated_period_service_level,
                "mean_on_hand": rs_sim.mean_on_hand,
                "stockout_periods": rs_sim.stockout_periods,
            },
            {
                "product_id": product_id,
                "policy": "GSM",
                "simulated_cycle_sl": None,
                "simulated_period_sl": None,
                "mean_on_hand": None,
                "stockout_periods": gsm_sim.stockout_periods,
                "fill_rate": gsm_sim.fill_rate,
                "mean_backorders": gsm_sim.mean_backorders,
            },
        ]

    return {
        "product_summary": pd.DataFrame([product_row]),
        "policies": pd.DataFrame(policy_rows),
        "cost_optimization": pd.DataFrame(cost_rows),
        "fill_rate": pd.DataFrame([fill_row]),
        "gsm_nodes": pd.DataFrame(gsm_rows),
        "simulation": pd.DataFrame(sim_rows) if sim_rows else pd.DataFrame(),
    }


def build_powerbi_dataset(
    data_path: Path | str,
    output_dir: Path | str,
    *,
    product_ids: list[str] | None = None,
    holding_cost_per_period: float = 1.25,
    order_cost: float = 1000.0,
    backorder_cost: float = 50.0,
    lead_time: float = 2.0,
    service_level: float = 0.95,
    fill_rate_target: float = 0.98,
    periods_per_year: float = 52.0,
    simulate: bool = False,
) -> PowerBIDatasetPaths:
    """Export CSV dataset for Power BI Desktop."""
    data_path = Path(data_path)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    raw = pd.read_csv(data_path, parse_dates=["date"])
    if product_ids is None:
        product_ids = sorted(raw["product_id"].unique().tolist())

    demand = raw[raw["product_id"].isin(product_ids)].copy()
    _write(demand, out / "demand_history.csv")

    params = pd.DataFrame(
        [
            {
                "holding_cost_per_period": holding_cost_per_period,
                "order_cost": order_cost,
                "backorder_cost": backorder_cost,
                "lead_time": lead_time,
                "service_level": service_level,
                "fill_rate_target": fill_rate_target,
                "periods_per_year": periods_per_year,
            }
        ]
    )
    _write(params, out / "parameters.csv")

    nv = optimal_newsvendor_discrete(muffin_pmf(), price=6, unit_cost=2, salvage_value=1)
    _write(
        pd.DataFrame(
            [
                {
                    "example": "muffins",
                    "Q_star": nv.optimal_quantity,
                    "critical_ratio": nv.critical_ratio,
                    "expected_profit": nv.expected_profit,
                }
            ]
        ),
        out / "newsvendor.csv",
    )

    frames: dict[str, list[pd.DataFrame]] = {
        "product_summary": [],
        "policies": [],
        "cost_optimization": [],
        "fill_rate": [],
        "gsm_nodes": [],
        "simulation": [],
    }

    for pid in product_ids:
        result = analyze_product(
            data_path,
            pid,
            holding_cost_per_period=holding_cost_per_period,
            order_cost=order_cost,
            backorder_cost=backorder_cost,
            lead_time=lead_time,
            service_level=service_level,
            fill_rate_target=fill_rate_target,
            periods_per_year=periods_per_year,
            simulate=simulate,
        )
        for key, df in result.items():
            if not df.empty:
                frames[key].append(df)

    paths = PowerBIDatasetPaths(
        root=out,
        demand_history=out / "demand_history.csv",
        product_summary=out / "product_summary.csv",
        policies=out / "policies.csv",
        simulation=out / "simulation.csv",
        cost_optimization=out / "cost_optimization.csv",
        fill_rate=out / "fill_rate.csv",
        gsm_nodes=out / "gsm_nodes.csv",
        newsvendor=out / "newsvendor.csv",
        parameters=out / "parameters.csv",
    )

    for key, filename in [
        ("product_summary", paths.product_summary),
        ("policies", paths.policies),
        ("cost_optimization", paths.cost_optimization),
        ("fill_rate", paths.fill_rate),
        ("gsm_nodes", paths.gsm_nodes),
        ("simulation", paths.simulation),
    ]:
        combined = pd.concat(frames[key], ignore_index=True) if frames[key] else pd.DataFrame()
        _write(combined, filename)

    return paths
