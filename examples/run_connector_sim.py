"""End-to-end OFFLINE: simulated storefront -> demand -> forecast -> safe restock.

Demonstrates the connector simulator (Gap #5 unblock, no live API keys). It reads a
simulated store, bridges its orders into the forecasting engine, decides a restock to a
target cover, then applies it through the safe-staging writeback plane (dry-run ->
idempotent -> audit/rollback) and shows the inventory reflect the change. Swap
``demo_store()`` for a real adapter later and the rest of the chain is unchanged.

Usage:
    python examples/run_connector_sim.py
    python examples/run_connector_sim.py --cover 10
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.connectors.simulator import demo_store  # noqa: E402
from src.forecasting import forecast_demand  # noqa: E402
from src.sources import DataFrameDemandSource  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the offline connector simulation loop.")
    parser.add_argument("--cover", type=float, default=8.0, help="target periods of demand to cover")
    args = parser.parse_args()

    store = demo_store()
    demand = DataFrameDemandSource(store.demand_frame())
    on_hand = {lvl.sku: lvl.available for lvl in store.inventory_levels()}

    print("\n=== Simulated storefront (offline, no API keys) ===")
    print(f"  products : {', '.join(p.sku for p in store.list_products())}")
    print(f"  orders   : {len(store.orders())} over {store.demand_frame()['date'].nunique()} dates")

    # 1) forecast each SKU and decide a restock to the target cover ---------------
    restock: dict[str, float] = {}
    print("\n  SKU      on-hand  forecast/period  target  restock")
    for sku in demand.list_products():
        fc = forecast_demand(demand.demand_series(sku), method="auto").forecast
        target = fc * args.cover
        current = on_hand.get(sku, 0.0)
        gap = max(0.0, round(target - current, 1))
        if gap > 0:
            restock[sku] = gap
        print(f"  {sku:<8} {current:>7.0f}  {fc:>15.1f}  {target:>6.0f}  {gap:>7.0f}")

    if not restock:
        print("\n  All SKUs above target cover - no restock needed.")
        return

    # 2) stage -> apply through the safe-staging writeback plane ------------------
    changeset = store.stage_restock(restock, idempotency_key="sim-restock-1",
                                    reason="replenish to target cover")
    print(f"\n  Staged (dry-run): {changeset.summary()}")
    result = store.apply_restock(changeset)
    print(f"  Applied: {result.applied} (audit key {result.audit_id})")

    after = {lvl.sku: lvl.available for lvl in store.inventory_levels()}
    print("\n  Inventory after restock:")
    for sku in restock:
        print(f"    {sku}: {on_hand[sku]:.0f} -> {after[sku]:.0f}")

    # 3) the safety plane: idempotent + reversible -------------------------------
    again = store.apply_restock(changeset)
    print(f"\n  Re-apply same key -> idempotent_skip={again.idempotent_skip} (no double-restock)")
    store.rollback("sim-restock-1")
    rolled = {lvl.sku: lvl.available for lvl in store.inventory_levels()}
    print(f"  Rollback -> {next(iter(restock))}: {rolled[next(iter(restock))]:.0f} (restored)")


if __name__ == "__main__":
    main()
