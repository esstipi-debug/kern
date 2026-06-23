"""Fetch the Olist SCM tables from a public GitHub mirror (no Kaggle login needed).

Mirrors the Kaggle 'Brazilian E-Commerce Public Dataset by Olist'. Downloads to
data/kaggle/olist/ (gitignored). Used by examples/run_scm_olist.py.

    python scripts/fetch_olist.py
"""
from __future__ import annotations

import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "data" / "kaggle" / "olist"
BASE = "https://raw.githubusercontent.com/Ganesh7699/Brazilian-E-Commerce-OList/main"
FILES = [
    "olist_orders_dataset",
    "olist_order_items_dataset",
    "olist_sellers_dataset",
    "olist_products_dataset",
    "olist_order_reviews_dataset",
]
# Expected row counts (incl. header) — sanity check against the canonical dataset.
EXPECTED = {
    "olist_orders_dataset": 99442,
    "olist_order_items_dataset": 112651,
    "olist_sellers_dataset": 3096,
}


def main() -> None:
    DEST.mkdir(parents=True, exist_ok=True)
    for f in FILES:
        out = DEST / f"{f}.csv"
        print(f"fetching {f}.csv ...", flush=True)
        urllib.request.urlretrieve(f"{BASE}/{f}.csv", out)
        rows = sum(1 for _ in out.open(encoding="utf-8", errors="ignore"))
        exp = EXPECTED.get(f)
        flag = "" if exp is None else ("  OK" if abs(rows - exp) < 5 else f"  WARN expected ~{exp}")
        print(f"  -> {out.name}  {out.stat().st_size / 1e6:.1f} MB  {rows:,} rows{flag}")
    print("done.")


if __name__ == "__main__":
    main()
