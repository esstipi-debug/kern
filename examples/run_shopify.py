"""End-to-end Shopify connector: read catalog/on-hand/orders -> forecast -> protected handoff.

Runs against a LIVE storefront when SHOPIFY_SHOP_URL / SHOPIFY_ACCESS_TOKEN are set;
otherwise it falls back to an in-memory stand-in so the whole flow is demonstrable with no
credentials. The read chain is identical to the other backends (demand bridge -> forecast
-> restock to target cover).

Where it deliberately stops short of ``run_odoo.py``: that script ends by writing a reorder
point back to Odoo. Shopify has no reorder point. Its only writable inventory number is
``available`` -- physical units -- so this script prints the restock as a handoff for
purchasing, and only writes when you pass ``--count SKU=QTY``, which reconciles what the
storefront believes is on the shelf against a real count.

Usage:
    python examples/run_shopify.py
    python examples/run_shopify.py --cover 10
    python examples/run_shopify.py --count SKU-3=9 --approved-by "ops@acme.com"
    # against a real storefront:
    SHOPIFY_SHOP_URL=https://acme.myshopify.com SHOPIFY_ACCESS_TOKEN=shpat_... \\
        python examples/run_shopify.py
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nosilo_core import writeback  # noqa: E402
from src.connectors.replenish import plan_replenishment  # noqa: E402
from src.connectors.shopify import ShopifyConnector, demo_shopify  # noqa: E402

DEFAULT_LOCATION = "gid://shopify/Location/1"


def _connector(location_id: str) -> ShopifyConnector:
    """A live Shopify pair when SHOPIFY_* env vars are present, else an in-memory stand-in."""
    shop = os.environ.get("SHOPIFY_SHOP_URL")
    token = os.environ.get("SHOPIFY_ACCESS_TOKEN")
    if shop and token:
        import httpx

        from src.connectors.shopify import ShopifyCommerceClient
        from src.connectors.shopify_inventory import ShopifyInventoryClient

        print(f"  connecting to live Shopify at {shop}")
        http = httpx.Client(base_url=shop, headers={"X-Shopify-Access-Token": token}, timeout=30.0)
        return ShopifyConnector(ShopifyCommerceClient(http), ShopifyInventoryClient(http), location_id=location_id)
    print("  no SHOPIFY_* env vars set -> using in-memory Shopify stand-in (demo data)")
    return ShopifyConnector(demo_shopify(), location_id=location_id)


def _parse_counts(pairs: list[str]) -> dict[str, float]:
    counted: dict[str, float] = {}
    for pair in pairs:
        sku, _, qty = pair.partition("=")
        if not sku or not qty:
            raise SystemExit(f"--count expects SKU=QTY, got {pair!r}")
        counted[sku.strip()] = float(qty)
    return counted


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Shopify connector replenishment loop.")
    parser.add_argument("--cover", type=float, default=8.0, help="target periods of demand to cover")
    parser.add_argument("--location", default=os.environ.get("SHOPIFY_LOCATION_ID", DEFAULT_LOCATION),
                        help="Shopify location GID to read on-hand from")
    parser.add_argument("--count", action="append", default=[], metavar="SKU=QTY",
                        help="reconcile Shopify's on-hand to a physical count (repeatable)")
    parser.add_argument("--approved-by", default="", help="the human approving a reconciliation")
    args = parser.parse_args()

    print("\n=== Shopify connector (read -> forecast -> protected handoff) ===")
    connector = _connector(args.location)

    products = connector.list_products()
    on_hand = {lvl.sku: lvl.available for lvl in connector.inventory_levels()}
    print(f"  variants : {', '.join(p.sku for p in products)}")
    print(f"  orders   : {len(connector.orders())} order(s)")
    if connector.truncated:
        print("  NOTE     : the catalog read hit the page limit; this is a partial plan")

    plan = plan_replenishment(connector, cover_periods=args.cover)
    restock = plan.restock

    print("\n  SKU      on-hand  restock-to-target")
    for line in plan.lines:
        print(f"  {line.sku:<8} {on_hand.get(line.sku, 0.0):>7.0f}  {restock.get(line.sku, 0.0):>17.1f}")

    print(f"\n  Outcome: {plan.outcome.status} - {plan.outcome.summary}")
    if restock:
        print("  Shopify raises no purchase order: hand these quantities to purchasing (ERP or supplier).")
    else:
        print("  Nothing to do (all SKUs above target cover).")

    counted = _parse_counts(args.count)
    if not counted:
        return

    if not args.approved_by:
        raise SystemExit("--count writes to the storefront; pass --approved-by to name the approver")

    print("\n=== Reconciliation (the one write this connector offers) ===")
    changeset = connector.stage_reconciliation(
        counted,
        # Derivada del conteo, no constante: correr el demo dos veces con conteos
        # distintos tiene que estacionar dos operaciones, no una y un duplicado.
        idempotency_key=writeback.derive_idempotency_key(
            "run-shopify", {sku: {"counted": qty} for sku, qty in counted.items()}),
    )
    print(f"  Staged (dry-run): {changeset.summary()}")

    approval = writeback.approve(changeset, args.approved_by)
    result = connector.apply_reconciliation(changeset, approval=approval)
    print(f"  Applied: {result.applied} (audit key {result.audit_id}, approved by {args.approved_by})")

    again = connector.apply_reconciliation(changeset, approval=approval)
    print(f"  Re-apply same key -> idempotent_skip={again.idempotent_skip} (no double-write)")


if __name__ == "__main__":
    main()
