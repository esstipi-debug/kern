"""End-to-end channel -> ERP loop: storefront demand, ERP stock, reorder point written back.

Runs against a live pair when SHOPIFY_* and ODOO_* are set; otherwise both fall back to
their in-memory stand-ins, so the whole cross-system loop is demonstrable with no
credentials at all.

This is the script that shows what neither single-connector example can: the plan is built
from one system's orders and written into the other system's reorder points, and where the
two disagree about on-hand it says so instead of picking a winner quietly.

Usage:
    python examples/run_channel.py
    python examples/run_channel.py --cover 10
    python examples/run_channel.py --apply
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jobs import channel_replenishment_job as job  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the channel -> ERP replenishment loop.")
    parser.add_argument("--channel", default=job.DEFAULT_CHANNEL, choices=list(job.CHANNELS),
                        help="which sales channel to read demand from")
    parser.add_argument("--cover", type=float, default=8.0, help="target periods of demand to cover")
    parser.add_argument("--apply", action="store_true",
                        help="apply the staged reorder points to the ERP (reversible)")
    args = parser.parse_args()

    print("\n=== Channel -> ERP replenishment ===")
    payload = job.prepare(None, {"channel": args.channel})
    print(f"  demand from : {payload['channel_source']}")
    print(f"  stock from  : {payload['erp_source']}")
    print(f"  matched     : {len(payload['matched'])} SKU(s)")
    if payload["channel_only"]:
        print(f"  channel-only: {', '.join(payload['channel_only'])} (no ERP product to replenish)")
    if payload["erp_only"]:
        print(f"  ERP-only    : {', '.join(payload['erp_only'])} (not sold on this channel)")

    report = job.run(payload, cover_periods=args.cover)

    print("\n  SKU      demand/period  ERP stock  channel stock  restock")
    for line in report.lines:
        print(f"  {line.sku:<8} {line.channel_demand_per_period:>13.1f} "
              f"{line.erp_on_hand:>10.0f} {line.channel_on_hand:>14.0f} {line.restock_qty:>8.1f}")

    if report.divergences:
        print("\n  Stock divergences (channel vs ERP) - one of the two is wrong:")
        for dv in report.divergences:
            print(f"    {dv.sku:<8} channel {dv.channel_on_hand:>7.0f}  ERP {dv.erp_on_hand:>7.0f}  "
                  f"delta {dv.delta:>+8.0f}")

    print(f"\n  Outcome: {report.outcome.status} - {report.outcome.summary}")
    issues = job.verify(report)
    if issues:
        print(f"  QA: {'; '.join(issues)}")
        return

    if report.changeset is None:
        print("  Nothing to stage (all matched SKUs above target cover).")
        return

    print(f"  Staged (dry-run): {report.changeset.summary()}")
    print(f"  Restock valued at ${report.restock_value:,.2f} (ERP book cost, by declared ownership)")

    if not args.apply:
        print("  Not applied. Re-run with --apply to write the reorder points to the ERP.")
        return

    erp = payload["erp"]
    result = erp.apply_restock(report.changeset)
    print(f"  Applied reorder point(s): {result.applied} (audit key {result.audit_id})")
    again = erp.apply_restock(report.changeset)
    print(f"  Re-apply same key -> idempotent_skip={again.idempotent_skip} (no double-write)")


if __name__ == "__main__":
    main()
