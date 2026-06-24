"""What-if / sensitivity demo over the real EOQ + safety-stock engines.

Wraps the analytic cores as a single ``inputs -> KPIs`` model, then asks the
what-if engine three questions a planner actually pays for:

  1. Which assumption moves annual cost the most? (tornado)
  2. What is the best/worst realistic corner? (optimistic / pessimistic)
  3. At what demand does cost blow past budget? (break-even)

ASCII-only output (Windows cp1252-safe). Run:
    .venv/Scripts/python.exe examples/run_whatif.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.eoq import compute_eoq  # noqa: E402
from src.safety_stock import safety_stock  # noqa: E402
from src.whatif import (  # noqa: E402
    Driver,
    break_even,
    optimistic_case,
    pessimistic_case,
    tornado,
)


def policy_model(inp: dict) -> dict:
    """EOQ cycle cost + safety-stock holding -> annual policy cost and its drivers."""
    eoq = compute_eoq(inp["annual_demand"], inp["holding_cost"], inp["fixed_order_cost"])
    ss = safety_stock(inp["demand_std"], inp["service_level"], inp["lead_time"])
    annual_cost = eoq.optimal_total_cost + inp["holding_cost"] * ss.safety_stock
    return {
        "annual_cost": annual_cost,
        "order_quantity": eoq.order_quantity,
        "safety_stock": ss.safety_stock,
        "orders_per_year": eoq.orders_per_year,
    }


BASE = {
    "annual_demand": 12_000.0,
    "holding_cost": 3.0,
    "fixed_order_cost": 75.0,
    "demand_std": 40.0,
    "service_level": 0.95,
    "lead_time": 2.0,
}

DRIVERS = [
    Driver("annual_demand", base=12_000.0, low=9_000.0, high=15_000.0, unit="u/yr"),
    Driver("holding_cost", base=3.0, low=2.0, high=4.5, unit="$/u/yr"),
    Driver("fixed_order_cost", base=75.0, low=50.0, high=120.0, unit="$/order"),
    Driver("service_level", base=0.95, low=0.90, high=0.99, unit="CSL"),
    Driver("lead_time", base=2.0, low=1.0, high=4.0, unit="periods"),
]


def main() -> None:
    base_cost = policy_model(BASE)["annual_cost"]
    print("=== What-if over EOQ + safety stock ===")
    print(f"Base annual policy cost: ${base_cost:,.0f}\n")

    print("Tornado (annual_cost; widest swing first):")
    for row in tornado(policy_model, BASE, DRIVERS, "annual_cost"):
        print(
            f"  {row.driver:<16} low ${row.low_output:>8,.0f}  "
            f"high ${row.high_output:>8,.0f}  swing ${row.swing:>8,.0f}"
        )

    opt = optimistic_case(policy_model, BASE, DRIVERS, "annual_cost")
    pes = pessimistic_case(policy_model, BASE, DRIVERS, "annual_cost")
    print(
        f"\nOptimistic corner: ${opt.outputs['annual_cost']:,.0f}"
        f"   Pessimistic corner: ${pes.outputs['annual_cost']:,.0f}"
    )

    target = base_cost * 1.10
    be = break_even(policy_model, BASE, DRIVERS[0], "annual_cost", target=target)
    if be.found:
        print(
            f"\nBreak-even: annual_cost hits ${target:,.0f} at "
            f"annual_demand = {be.value:,.0f} u/yr"
        )
    else:
        print(f"\nBreak-even: ${target:,.0f} not reached within the demand band {be.bracket}")


if __name__ == "__main__":
    main()
