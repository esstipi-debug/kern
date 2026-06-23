"""Offline connector-backed replenishment -> a guided, never-unprotected restock packet.

Reads a simulated store, plans the restock to a target cover, and prints the guided
handoff: the staged dry-run plus the human step to approve/apply. Demonstrates the
execution loop end-to-end with no API keys; point ``plan_replenishment`` at a real
adapter later and nothing else changes.

Usage:
    python examples/run_replenishment.py
    python examples/run_replenishment.py --cover 10 --apply
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.connectors.replenish import plan_replenishment  # noqa: E402
from src.connectors.simulator import demo_store  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Plan a restock from a simulated store (offline).")
    parser.add_argument("--cover", type=float, default=8.0, help="target periods of demand to cover")
    parser.add_argument("--apply", action="store_true", help="approve + apply the staged restock")
    args = parser.parse_args()

    store = demo_store()
    plan = plan_replenishment(store, cover_periods=args.cover, store=store)

    print("\n=== Replenishment plan (offline, no API keys) ===")
    print("  SKU      on-hand  forecast/period  target  restock")
    for line in plan.lines:
        print(f"  {line.sku:<8} {line.on_hand:>7.0f}  {line.forecast_per_period:>15.1f}  "
              f"{line.target:>6.0f}  {line.restock_qty:>7.0f}")

    out = plan.outcome
    print(f"\n  Outcome: {out.status} - {out.summary}")
    for packet in out.handoffs:
        print(f"  Handoff: {packet.title}")
        for step in packet.steps:
            print(f"    - {step}")
        print(f"    Restock:\n{packet.artifact}")
    for residual in out.residuals:
        print(f"  Residual (human): {residual.description} [risk: {residual.risk_if_skipped}]")

    if args.apply and plan.changeset is not None:
        result = store.apply_restock(plan.changeset)
        after = {lvl.sku: lvl.available for lvl in store.inventory_levels()}
        print(f"\n  Applied: {result.applied}. Inventory now: "
              + ", ".join(f"{sku}={after[sku]:.0f}" for sku in plan.restock))


if __name__ == "__main__":
    main()
