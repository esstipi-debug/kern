"""Run the Stocky migration check end-to-end (Kern catalog rung #2).

Point it at a merchant's Stocky CSV exports, or use --demo for synthetic data:

    python examples/run_stocky_migration.py \
        --suppliers suppliers.csv --purchase-orders pos.csv --reorder-points reorder.csv \
        --client "Tienda X" --out deliverables/stocky

    python examples/run_stocky_migration.py --demo

Read-only over the CSVs -- never touches a Shopify store. Prints the verdict
(ASCII) and writes a markdown report deliverable.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from jobs.stocky_migration_job import prepare, run, write_operational

_DEMO_SUPPLIERS = "Supplier Name,Lead Time (days),MOQ,Currency\nAcme Imports,21,100,USD\nBolt Supply,7,10,USD\n"
_DEMO_POS = (
    "PO Number,Supplier Name,Status,SKU,Quantity Ordered,Quantity Received,Cost Price,Order Date\n"
    "PO-1001,Acme Imports,received,SKU-100,50,50,12.50,2026-02-01\n"
    "PO-1002,Bolt Supply,sent,SKU-200,20,0,45.00,2026-05-01\n"
)
_DEMO_REORDER = "SKU,Min Stock,Max Stock,Target Stock\nSKU-100,15,60,60\nSKU-200,5,20,20\n"


def _demo_paths(tmp: Path) -> dict[str, Path]:
    tmp.mkdir(parents=True, exist_ok=True)
    files = {"suppliers": _DEMO_SUPPLIERS, "purchase_orders": _DEMO_POS, "reorder_points": _DEMO_REORDER}
    out = {}
    for name, content in files.items():
        p = tmp / f"{name}.csv"
        p.write_text(content, encoding="utf-8")
        out[name] = p
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Kern Stocky migration check")
    parser.add_argument("--suppliers")
    parser.add_argument("--purchase-orders")
    parser.add_argument("--reorder-points")
    parser.add_argument("--client", default="Cliente")
    parser.add_argument("--out", default="deliverables/stocky")
    parser.add_argument("--demo", action="store_true", help="run with synthetic Stocky exports")
    args = parser.parse_args()

    if args.demo:
        demo = _demo_paths(Path(args.out) / "_demo_inputs")
        batch = prepare(
            suppliers_csv=demo["suppliers"],
            purchase_orders_csv=demo["purchase_orders"],
            reorder_points_csv=demo["reorder_points"],
        )
    else:
        batch = prepare(
            suppliers_csv=args.suppliers,
            purchase_orders_csv=args.purchase_orders,
            reorder_points_csv=args.reorder_points,
        )

    result = run(batch)
    paths = write_operational(result, args.out, client=args.client)

    print(f"Stocky migration check -- {args.client}")
    print(f"  batch: {result.batch_summary}")
    print(f"  sufficient (Shopify native alone): {result.assessment.shopify_native_sufficient}")
    print(f"  gaps: {list(result.assessment.gaps)}")
    print(f"  SKU master: {result.sku_audit.summary}")
    print(f"  report written: {paths['report']}")


if __name__ == "__main__":
    main()
