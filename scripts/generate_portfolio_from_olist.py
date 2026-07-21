"""Generate a REALISTIC demand portfolio from real Olist order history, as an
opt-in alternative to data/sample_demand_portfolio.csv's synthetic SKU-A..H
series -- which several tests (test_webapp.py, test_forecasting_auto.py, ...)
assert specific IDs and properties against, so this script writes a SEPARATE
file rather than overwriting it. Point the webapp at this file instead via the
LINCHPIN_PORTFOLIO_DATA_FILE env var (see README below) when you want a
customer-facing demo grounded in real transactions instead of synthetic data.

Picks the 8 highest-volume real product categories from the Brazilian Olist
marketplace dataset, and for each one aggregates real weekly order-item counts
(2017, the dataset's most complete calendar year) into the same CSV shape the
Inventory Planner already reads. Unit cost is the real mean price paid in that
category. Lead time is the real mean purchase->delivered-to-customer duration --
Olist is direct-to-consumer, so it has no supplier-replenishment lead time; this
is documented here as a stand-in, not hidden.

Requires data/kaggle/olist/*.csv (fetch first: python scripts/fetch_olist.py).

Run:
    python scripts/generate_portfolio_from_olist.py
    LINCHPIN_PORTFOLIO_DATA_FILE=data/sample_demand_portfolio_olist.csv \\
        py -m uvicorn webapp.app:app --reload
"""

from __future__ import annotations

import csv
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OLIST_DIR = ROOT / "data" / "kaggle" / "olist"
OUT_PATH = ROOT / "data" / "sample_demand_portfolio_olist.csv"

TOP_N_CATEGORIES = 8
WINDOW_START = "2017-01-01"
WINDOW_END = "2017-12-31"  # Olist's most complete full calendar year


def main() -> None:
    for name in ("olist_order_items_dataset.csv", "olist_orders_dataset.csv", "olist_products_dataset.csv"):
        if not (OLIST_DIR / name).exists():
            raise SystemExit(f"missing {OLIST_DIR / name} -- run: python scripts/fetch_olist.py")

    items = pd.read_csv(OLIST_DIR / "olist_order_items_dataset.csv")
    orders = pd.read_csv(
        OLIST_DIR / "olist_orders_dataset.csv",
        parse_dates=["order_purchase_timestamp", "order_delivered_customer_date"],
    )
    products = pd.read_csv(OLIST_DIR / "olist_products_dataset.csv")

    delivered = orders[orders["order_status"] == "delivered"].copy()
    delivered["lead_days"] = (
        delivered["order_delivered_customer_date"] - delivered["order_purchase_timestamp"]
    ).dt.days

    merged = items.merge(
        delivered[["order_id", "order_purchase_timestamp", "lead_days"]], on="order_id", how="inner"
    )
    merged = merged.merge(products[["product_id", "product_category_name"]], on="product_id", how="left")
    merged = merged.dropna(subset=["product_category_name"])

    window = merged[
        (merged["order_purchase_timestamp"] >= WINDOW_START)
        & (merged["order_purchase_timestamp"] <= WINDOW_END)
    ].copy()

    cat_stats = (
        window.groupby("product_category_name")
        .agg(n=("order_id", "count"), unit_cost=("price", "mean"), lead_days=("lead_days", "mean"))
        .sort_values("n", ascending=False)
    )
    top_categories = cat_stats.head(TOP_N_CATEGORIES)

    window["week"] = window["order_purchase_timestamp"].dt.to_period("W-SUN").apply(lambda p: p.start_time.date())
    all_weeks = pd.period_range(WINDOW_START, WINDOW_END, freq="W-SUN")
    week_starts = [p.start_time.date() for p in all_weeks]

    rows: list[list[object]] = []
    for category, stats in top_categories.iterrows():
        cat_rows = window[window["product_category_name"] == category]
        weekly_qty = cat_rows.groupby("week").size()
        sku_id = category.upper().replace("_", "-")[:24]
        unit_cost = round(float(stats["unit_cost"]), 2)
        lead_days = max(1, round(float(stats["lead_days"])))
        for wk in week_starts:
            qty = int(weekly_qty.get(wk, 0))
            rows.append([wk.isoformat(), sku_id, qty, unit_cost, lead_days])

    with OUT_PATH.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "product_id", "quantity", "unit_cost", "lead_time_days"])
        w.writerows(rows)

    print(f"Wrote {len(rows)} rows for {len(top_categories)} real Olist categories -> {OUT_PATH}")
    for category, stats in top_categories.iterrows():
        sku_id = category.upper().replace("_", "-")[:24]
        print(f"  {sku_id:26s} n={int(stats['n']):5d}  avg_price=R${stats['unit_cost']:.2f}  avg_lead={stats['lead_days']:.1f}d")


if __name__ == "__main__":
    main()
