"""SCM supplier-sourcing test on the Olist Brazilian e-commerce dataset.

Olist is a real multi-table SCM dataset (orders + order_items + sellers +
products + reviews) with proper keys and *real* delivered-vs-estimated dates and
per-item freight — so it exercises the sourcing/logistics modules Superstore
could not: real seller (supplier) OTIF scorecards, MCDM seller selection,
landed cost from real freight, and delivery performance -> a client deliverable.

ASCII-only output (cp1252 safe). Modeling inputs labelled [ASSUMPTION].

Usage:
    python examples/run_scm_olist.py --dir data/kaggle/olist
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scm_agent.knowledge import KnowledgeBase  # noqa: E402
from scm_agent.modes import SCM  # noqa: E402
from src.deliverable import DataSource, Deliverable, Finding, Kpi  # noqa: E402
from src.landed_cost import landed_cost  # noqa: E402
from src.mcdm import Criterion, topsis_rank  # noqa: E402
from src.supplier_scorecard import score_supplier  # noqa: E402

MIN_DELIVERIES = 50  # only score sellers with enough delivered orders for stable stats


def section(t):
    print("\n" + "=" * 70 + f"\n{t}\n" + "=" * 70)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="data/kaggle/olist")
    args = ap.parse_args()
    d = (ROOT / args.dir) if not Path(args.dir).is_absolute() else Path(args.dir)

    orders = pd.read_csv(d / "olist_orders_dataset.csv")
    items = pd.read_csv(d / "olist_order_items_dataset.csv")
    sellers = pd.read_csv(d / "olist_sellers_dataset.csv")
    products = pd.read_csv(d / "olist_products_dataset.csv")
    try:
        reviews = pd.read_csv(d / "olist_order_reviews_dataset.csv")
        rev = reviews.groupby("order_id")["review_score"].mean()
    except Exception:
        rev = None

    for c in ["order_purchase_timestamp", "order_delivered_customer_date", "order_estimated_delivery_date"]:
        orders[c] = pd.to_datetime(orders[c], errors="coerce")

    # Join items -> orders (dates/status) -> sellers (state) -> products (category)
    df = items.merge(
        orders[["order_id", "order_status", "order_purchase_timestamp",
                "order_delivered_customer_date", "order_estimated_delivery_date"]],
        on="order_id", how="left")
    df = df.merge(sellers[["seller_id", "seller_state"]], on="seller_id", how="left")
    df = df.merge(products[["product_id", "product_category_name"]], on="product_id", how="left")
    if rev is not None:
        df = df.merge(rev.rename("review_score"), on="order_id", how="left")

    # Delivered lines only, with both dates present.
    df = df[(df["order_status"] == "delivered")
            & df["order_delivered_customer_date"].notna()
            & df["order_estimated_delivery_date"].notna()].copy()
    df["on_time"] = df["order_delivered_customer_date"] <= df["order_estimated_delivery_date"]
    df["lead_days"] = (df["order_delivered_customer_date"] - df["order_purchase_timestamp"]).dt.days
    df["freight_pct"] = df["freight_value"] / (df["price"] + df["freight_value"]).clip(lower=0.01)
    print(f"Dataset: Olist  |  delivered lines: {len(df):,}  |  sellers: {df['seller_id'].nunique():,}  "
          f"|  categories: {df['product_category_name'].nunique()}  |  mode: SCM")

    # 1) Real seller (supplier) OTIF scorecards -------------------------------
    section("1) Seller (supplier) OTIF scorecards - real delivered-vs-estimated (M8)")
    vol = df.groupby("seller_id").size().sort_values(ascending=False)
    top_ids = vol[vol >= MIN_DELIVERIES].head(8).index.tolist()
    cards = {}
    for sid in top_ids:
        g = df[df["seller_id"] == sid]
        deliveries = [
            {"on_time": bool(ot), "in_full": True, "lead_time_days": float(ld) if pd.notna(ld) else 0.0,
             "units": 1.0, "defects": 1.0 if (rev is not None and pd.notna(rs) and rs <= 2) else 0.0}
            for ot, ld, rs in zip(g["on_time"], g["lead_days"],
                                  g["review_score"] if rev is not None else [None] * len(g))
        ]
        cards[sid] = score_supplier(sid[:8], deliveries)
    for sid in top_ids:
        c = cards[sid]
        fr = df[df["seller_id"] == sid]["freight_pct"].mean()
        print(f"  {sid[:8]}  deliveries={c.deliveries:>4}  OTIF={c.otif*100:5.1f}%  "
              f"avg lead={c.avg_lead_time:4.1f}d  freight={fr*100:4.1f}%  defect_ppm={c.ppm:,.0f}")

    # 2) MCDM seller selection -------------------------------------------------
    section("2) MCDM seller selection (TOPSIS) - real OTIF / lead / freight / quality")
    alts = {}
    for sid in top_ids:
        c = cards[sid]
        g = df[df["seller_id"] == sid]
        alts[sid[:8]] = {"otif": c.otif, "lead": c.avg_lead_time,
                         "freight": float(g["freight_pct"].mean()),
                         "quality": float(g["review_score"].mean()) if rev is not None else 3.0}
    criteria = [Criterion("otif", benefit=True), Criterion("lead", benefit=False),
                Criterion("freight", benefit=False), Criterion("quality", benefit=True)]
    weights = {"otif": 0.4, "lead": 0.2, "freight": 0.2, "quality": 0.2}  # [ASSUMPTION]
    rank = topsis_rank(alts, criteria, weights)
    for i, name in enumerate(rank.ranking, 1):
        print(f"  {i}. seller {name:<10} closeness={rank.scores[name]:.3f}")
    chosen = rank.ranking[0]

    # 3) Landed cost - chosen seller's representative basket -------------------
    section("3) Landed cost from REAL freight - chosen seller (CIF, [ASSUMPTION] duty)")
    chosen_sid = {sid[:8]: sid for sid in top_ids}[chosen]
    gsel = df[df["seller_id"] == chosen_sid]
    goods = float(gsel["price"].sum())
    freight_real = float(gsel["freight_value"].sum())
    qty = float(len(gsel))
    lc = landed_cost(unit_cost=goods / qty, qty=qty, freight=freight_real,
                     insurance=0.005 * goods, duty_rate=0.0, handling=0.01 * goods,
                     broker_fee=0.0, incoterm="CIF")
    print(f"  seller {chosen}: {qty:.0f} items  goods=R${goods:,.0f}  REAL freight=R${freight_real:,.0f} "
          f"({freight_real/goods*100:.1f}% of goods)")
    print(f"  landed/unit=R${lc.per_unit:.2f}  total landed=R${lc.total:,.0f}")

    # 4) Cost-to-serve: freight burden by category -----------------------------
    section("4) Cost-to-serve - freight burden by category")
    cat = (df.groupby("product_category_name")
           .agg(lines=("price", "size"), freight_pct=("freight_pct", "mean"))
           .query("lines >= 200").sort_values("freight_pct", ascending=False))
    for name, r in cat.head(5).iterrows():
        print(f"  {str(name)[:34]:<34} freight={r['freight_pct']*100:5.1f}% of order value ({int(r['lines'])} lines)")
    worst_cat = str(cat.index[0])

    # 5) Delivery performance overall ------------------------------------------
    section("5) Delivery performance (network-wide)")
    on_time_rate = df["on_time"].mean()
    avg_lead = df["lead_days"].mean()
    late = (~df["on_time"]).sum()
    print(f"  on-time rate: {on_time_rate*100:.1f}%   avg order->delivery lead: {avg_lead:.1f}d   "
          f"late deliveries: {late:,}")

    # 6) Deliverable ------------------------------------------------------------
    section("6) SCM deliverable (generated)")
    kb = KnowledgeBase()
    cites = [f"{h.label} - {h.source}{(' ' + h.location) if h.location else ''}"
             for h in kb.search("supplier selection sourcing OTIF freight cost", graph="books", limit=3)]
    best_card, worst_card = cards[top_ids[0]], cards[top_ids[-1]]
    deliverable = Deliverable(
        title="Supplier & Logistics Review (Olist)",
        client="Olist Marketplace",
        summary=(f"Reviewed {len(df):,} delivered order-lines from {df['seller_id'].nunique():,} sellers. "
                 f"Network on-time {on_time_rate*100:.1f}%; recommended seller '{chosen}'; "
                 f"highest freight burden in '{worst_cat}'."),
        findings=(
            Finding("Wide seller OTIF spread",
                    f"Top-volume sellers range OTIF {min(c.otif for c in cards.values())*100:.0f}%"
                    f"-{max(c.otif for c in cards.values())*100:.0f}%.",
                    impact="consolidate volume on reliable sellers"),
            Finding(f"Network on-time {on_time_rate*100:.0f}%",
                    f"{late:,} late deliveries vs estimated date; avg lead {avg_lead:.0f}d.",
                    impact="estimated-date promises need tightening"),
            Finding(f"Freight-heavy category: {worst_cat}",
                    f"Freight is {cat.iloc[0]['freight_pct']*100:.0f}% of order value there.",
                    impact="renegotiate freight or set free-ship thresholds"),
        ),
        kpis=(
            Kpi("Recommended seller", chosen, rationale="Best OTIF/lead/freight/quality trade-off (TOPSIS)"),
            Kpi("Network on-time rate", f"{on_time_rate*100:.1f}%", target="95%+",
                rationale="Delivered on/before the promised estimate"),
            Kpi("Avg delivery lead", f"{avg_lead:.0f} days", rationale="Order to customer delivery"),
            Kpi("Chosen-seller landed/unit", f"R${lc.per_unit:.2f}", rationale="Real freight-loaded delivered cost"),
            Kpi("Worst freight burden", f"{cat.iloc[0]['freight_pct']*100:.0f}% ({worst_cat[:20]})",
                target="< 15%", rationale="Cost-to-serve hotspot"),
        ),
        data_sources=(
            DataSource("Delivered vs estimated dates -> OTIF", "olist_orders_dataset.csv", "per run"),
            DataSource("price + freight_value -> landed cost", "olist_order_items_dataset.csv", "per run"),
            DataSource("seller_id -> supplier scorecards", "olist_sellers_dataset.csv", "per run"),
            DataSource("review_score -> quality (defect proxy)", "olist_order_reviews_dataset.csv", "per run"),
        ),
        recommendations=(
            f"Route more volume to seller '{chosen}' and the top-OTIF cohort.",
            f"Tighten delivery-estimate promises: {on_time_rate*100:.0f}% on-time leaves room.",
            f"Attack freight in '{worst_cat}' (free-ship threshold, carrier renegotiation, zone consolidation).",
        ),
        citations=tuple(cites),
        confidence=0.88,
        residual="OTIF uses delivered-vs-estimated (a promise, not a hard SLA); duty assumed 0 (domestic BR). "
                 "Confirm contractual SLAs and any cross-border duty before acting.",
        prepared="(stamp on export)",
    )
    out = deliverable.write_all(ROOT / "deliverables" / "scm_olist")
    print(deliverable.to_markdown())
    print(f"  written: {out['report']}\n  written: {out['workbook']}")
    print(f"\n[mode] {SCM.label}")


if __name__ == "__main__":
    main()
