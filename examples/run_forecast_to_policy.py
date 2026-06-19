"""End-to-end: raw demand history -> forecast -> inventory policy.

This is the piece that makes the engine usable on real data. Instead of
feeding the models a pre-computed mean/std, it forecasts demand per SKU and
uses the forecast-error sigma (sigma_e) as the dispersion for safety stock.

Usage:
    python examples/run_forecast_to_policy.py
    python examples/run_forecast_to_policy.py --data data/sample_demand.csv --service-level 0.95
"""

from __future__ import annotations

import argparse

from src.data_loader import list_products, load_demand_csv, product_metadata
from src.forecasting import forecast_demand
from src.policies import continuous_review_sq


def main() -> None:
    parser = argparse.ArgumentParser(description="Forecast demand and derive an (s,Q) policy per SKU.")
    parser.add_argument("--data", default="data/sample_demand.csv")
    parser.add_argument("--method", default="auto", choices=["auto", "ses", "croston", "moving_average"])
    parser.add_argument("--service-level", type=float, default=0.95)
    parser.add_argument("--holding-rate", type=float, default=0.25, help="annual holding cost as fraction of unit cost")
    parser.add_argument("--order-cost", type=float, default=50.0)
    parser.add_argument("--periods-per-year", type=float, default=52.0)
    args = parser.parse_args()

    for product_id in list_products(args.data):
        series = load_demand_csv(args.data, product_id=product_id)
        meta = product_metadata(args.data, product_id, periods_per_year=args.periods_per_year)

        forecast = forecast_demand(series.to_numpy(), method=args.method)
        inputs = forecast.to_engine_inputs(periods_per_year=args.periods_per_year)

        holding_cost = max(args.holding_rate * meta.mean_unit_cost, 1e-6)
        policy = continuous_review_sq(
            **inputs,
            holding_cost_per_unit=holding_cost,
            fixed_order_cost=args.order_cost,
            lead_time_periods=meta.lead_time_periods,
            cycle_service_level=args.service_level,
        )

        print(f"\n=== {product_id} ===")
        print(f"  method           : {forecast.method} (intermittent={forecast.is_intermittent})")
        print(f"  forecast demand  : {forecast.forecast:.2f}/period   sigma_e={forecast.error_std:.2f}")
        print(f"  bias / MAE       : {forecast.bias:+.2f} / {forecast.mae:.2f}")
        print(f"  EOQ Q*           : {policy.order_quantity:.1f}")
        print(f"  reorder point s  : {policy.reorder_point:.1f}")
        print(f"  safety stock     : {policy.safety_stock.safety_stock:.1f}")


if __name__ == "__main__":
    main()
