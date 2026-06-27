"""End-to-end Odoo connector: read products/inventory/sales -> forecast -> safe restock.

Runs against a LIVE Odoo when ODOO_URL / ODOO_DB / ODOO_USERNAME / ODOO_API_KEY are set
(XML-RPC, no extra deps); otherwise it falls back to an in-memory Odoo stand-in so the
whole flow is demonstrable with no credentials. The chain after the connector is identical
to the other backends: demand bridge -> forecast -> stage a restock through the safe-staging
writeback plane (here, as a reversible Odoo reorder-point update).

Usage:
    python examples/run_odoo.py
    python examples/run_odoo.py --cover 10
    # against a real Odoo:
    ODOO_URL=https://my.odoo.com ODOO_DB=mydb ODOO_USERNAME=me ODOO_API_KEY=... \\
        python examples/run_odoo.py
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.connectors.odoo import OdooConnector, OdooRPC, demo_odoo  # noqa: E402
from src.connectors.replenish import plan_replenishment  # noqa: E402


def _rpc() -> OdooRPC:
    """A live ``OdooClient`` when ODOO_* env vars are present, else an in-memory stand-in."""
    url, db = os.environ.get("ODOO_URL"), os.environ.get("ODOO_DB")
    user, key = os.environ.get("ODOO_USERNAME"), os.environ.get("ODOO_API_KEY")
    if url and db and user and key:
        from src.connectors.odoo import OdooClient

        print(f"  connecting to live Odoo at {url} (db={db}, user={user})")
        return OdooClient(url, db, user, key)
    print("  no ODOO_* env vars set -> using in-memory Odoo stand-in (demo data)")
    return demo_odoo()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Odoo connector replenishment loop.")
    parser.add_argument("--cover", type=float, default=8.0, help="target periods of demand to cover")
    parser.add_argument("--draft-po", action="store_true",
                        help="raise draft purchase orders instead of applying reorder points")
    args = parser.parse_args()

    print("\n=== Odoo connector (read -> forecast -> safe restock) ===")
    connector = OdooConnector(_rpc())

    products = connector.list_products()
    on_hand = {lvl.sku: lvl.available for lvl in connector.inventory_levels()}
    lead = connector.lead_times()
    print(f"  products : {', '.join(p.sku for p in products)}")
    print(f"  orders   : {len(connector.orders())} confirmed sales order(s)")

    plan = plan_replenishment(connector, cover_periods=args.cover, store=connector)

    print("\n  SKU      on-hand  lead(d)  restock-to-target")
    restock = plan.restock
    for line in plan.lines:
        gap = restock.get(line.sku, 0.0)
        print(f"  {line.sku:<8} {on_hand.get(line.sku, 0.0):>7.0f}  {lead.get(line.sku, 0.0):>7.0f}  {gap:>17.1f}")

    print(f"\n  Outcome: {plan.outcome.status} - {plan.outcome.summary}")
    if not restock:
        print("  Nothing to do (all SKUs above target cover).")
        return

    if args.draft_po:
        # Option 2: raise draft purchase orders (RFQs), grouped by each SKU's primary supplier.
        po = connector.create_draft_purchase_orders(restock)
        print(f"\n  Created {po.n_orders} draft PO(s) in Odoo (left unconfirmed for a buyer to review):")
        for po_id, skus in po.purchase_orders.items():
            print(f"    PO {po_id}: {', '.join(skus)}")
        if po.unsourced:
            print(f"  Unsourced (no supplier in Odoo): {', '.join(po.unsourced)}")
        return

    # Option 1 (default): apply reorder points. Stage is a dry-run; apply writes them back.
    if plan.changeset is None:
        return
    print(f"  Staged (dry-run): {plan.changeset.summary()}")
    result = connector.apply_restock(plan.changeset)
    print(f"  Applied reorder point(s): {result.applied} (audit key {result.audit_id})")

    again = connector.apply_restock(plan.changeset)
    print(f"  Re-apply same key -> idempotent_skip={again.idempotent_skip} (no double-write)")


if __name__ == "__main__":
    main()
