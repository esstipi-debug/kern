"""Robust operational-SCM test on the DataCo Smart Supply Chain dataset (~180k orders).

DataCo carries REAL shipping performance ('Days for shipping (real)' vs
'(scheduled)') so OTIF needs no assumed SLA, plus per-order profit, global
markets/regions, and a late-delivery-risk label. Exercises carrier OTIF
scorecards, MCDM shipping-mode selection, cost-to-serve / profitability, and
delivery-risk -> a client deliverable. Aggregate-only (no customer PII is read).

ASCII-only output. Data: data/kaggle/dataco/ (CC0, gitignored).
Usage: python examples/run_scm_dataco.py
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
from src.deliverable import (  # noqa: E402
    DataSource,
    Deliverable,
    Finding,
    Kpi,
    days,
    not_measured,
    pct,
)
from src.mcdm import Criterion, topsis_rank  # noqa: E402
from src.supplier_scorecard import rankable, score_supplier  # noqa: E402

# Operational columns only — deliberately excludes all customer PII (email, name, password, street).
COLS = ["Days for shipping (real)", "Days for shipment (scheduled)", "Delivery Status",
        "Late_delivery_risk", "Order Item Profit Ratio", "Sales", "Market", "Order Region",
        "Customer Segment", "Category Name", "Shipping Mode", "Order Item Quantity"]


def carrier_scorecards(df) -> tuple[list, tuple[str, ...]]:
    """Score each shipping mode, and name the modes this data could not score.

    The raw cell goes to ``score_supplier`` uncoerced on purpose: ``bool(nan)`` is True
    and ``float(nan)`` poisons an average, so pre-converting here would decide the very
    question - measured or absent? - that the scorecard exists to answer.
    """
    cards = []
    for mode, g in df.groupby("Shipping Mode"):
        deliveries = [{"on_time": ot, "in_full": inf, "lead_time_days": r,
                       "units": q, "defects": 0.0}
                      for ot, inf, r, q in zip(g["on_time"], g["in_full"], g["real"],
                                               g["Order Item Quantity"])]
        cards.append(score_supplier(mode, deliveries))
    panel, dropped = rankable(cards, ("otif", "on_time_rate", "avg_lead_time"))
    panel.sort(key=lambda c: c.otif, reverse=True)
    return panel, dropped


def section(t):
    print("\n" + "=" * 70 + f"\n{t}\n" + "=" * 70)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/kaggle/dataco/DataCoSupplyChainDataset.csv")
    args = ap.parse_args()
    path = (ROOT / args.data) if not Path(args.data).is_absolute() else Path(args.data)

    df = pd.read_csv(path, encoding="latin-1", usecols=COLS)
    df = df.rename(columns={"Days for shipping (real)": "real", "Days for shipment (scheduled)": "sched",
                            "Order Item Profit Ratio": "pratio"})
    df["on_time"] = df["real"] <= df["sched"]
    df["in_full"] = df["Delivery Status"] != "Shipping canceled"
    df["profit"] = df["Sales"] * df["pratio"]
    print(f"Dataset: DataCo  |  order-lines: {len(df):,}  |  markets: {df['Market'].nunique()}  "
          f"|  regions: {df['Order Region'].nunique()}  |  modes: {df['Shipping Mode'].nunique()}  |  mode: SCM")

    # 1) Carrier (shipping-mode) OTIF scorecards - REAL actual-vs-scheduled -----
    section("1) Carrier OTIF scorecards - real actual-vs-scheduled days (M8)")
    scored, unscoreable = carrier_scorecards(df)
    risk = df.groupby("Shipping Mode")["Late_delivery_risk"].mean()
    cards = {c.supplier: c for c in scored}
    for mode, c in cards.items():
        print(f"  {mode:<16} n={c.deliveries:>6}  OTIF={pct(c.otif):>6}  on-time={pct(c.on_time_rate):>6}  "
              f"avg lead={days(c.avg_lead_time):>9}  late-risk={risk[mode]*100:4.0f}%")
    for mode in unscoreable:
        print(f"  {mode:<16} {not_measured()} - no scoreable delivery in this data; "
              "left out of the ranking")
    if not cards:
        sys.exit("no shipping mode carried a scoreable delivery; nothing to rank")

    # 2) MCDM shipping-mode selection -----------------------------------------
    section("2) MCDM shipping-mode selection (TOPSIS)")
    alts = {m: {"otif": c.otif, "lead": c.avg_lead_time, "risk": float(risk[m])} for m, c in cards.items()}
    criteria = [Criterion("otif", benefit=True), Criterion("lead", benefit=False), Criterion("risk", benefit=False)]
    weights = {"otif": 0.5, "lead": 0.25, "risk": 0.25}  # [ASSUMPTION]
    rank = topsis_rank(alts, criteria, weights)
    for i, name in enumerate(rank.ranking, 1):
        print(f"  {i}. {name:<16} closeness={rank.scores[name]:.3f}")
    chosen = rank.ranking[0]

    # 3) Cost-to-serve / profitability ----------------------------------------
    section("3) Cost-to-serve & profitability (real per-order profit)")
    seg = df.groupby("Customer Segment").agg(sales=("Sales", "sum"), profit=("profit", "sum")).reset_index()
    seg["margin"] = seg["profit"] / seg["sales"]
    for _, r in seg.iterrows():
        print(f"  {r['Customer Segment']:<14} sales=${r['sales']:>14,.0f}  margin={r['margin']*100:5.1f}%")
    cat = df.groupby("Category Name").agg(sales=("Sales", "sum"), profit=("profit", "sum"))
    cat["margin"] = cat["profit"] / cat["sales"]
    loss_cats = cat[cat["profit"] < 0].sort_values("profit")
    overall_margin = df["profit"].sum() / df["Sales"].sum()
    if len(loss_cats):
        print(f"  --> {len(loss_cats)} loss-making categories; worst: {loss_cats.index[0]} "
              f"(${float(loss_cats.iloc[0]['profit']):,.0f})")
    else:
        print("  --> 0 loss-making categories")
    print(f"  --> overall margin: {overall_margin*100:.1f}%  |  network on-time: {df['on_time'].mean()*100:.1f}%  "
          f"|  late-delivery risk: {df['Late_delivery_risk'].mean()*100:.0f}%")

    # 4) Deliverable -----------------------------------------------------------
    section("4) SCM deliverable (generated)")
    kb = KnowledgeBase()
    cites = [f"{h.label} - {h.source}{(' ' + h.location) if h.location else ''}"
             for h in kb.search("on-time delivery cost to serve logistics performance", graph="books", limit=3)]
    on_time = df["on_time"].mean()
    late_risk = df["Late_delivery_risk"].mean()
    worst_seg = seg.sort_values("margin").iloc[0]
    deliverable = Deliverable(
        title="Logistics & Cost-to-Serve Review (DataCo)",
        client="DataCo Global",
        summary=(f"Analyzed {len(df):,} order-lines across {df['Market'].nunique()} markets. "
                 f"Network on-time {on_time*100:.1f}% (late-risk {late_risk*100:.0f}%); recommended mode "
                 f"'{chosen}'; overall margin {overall_margin*100:.1f}%."),
        findings=(
            Finding(f"Network on-time only {on_time*100:.0f}%",
                    f"{late_risk*100:.0f}% of orders carry late-delivery risk; real vs scheduled days diverge.",
                    impact="late deliveries erode service and incur penalties"),
            Finding("Shipping-mode reliability gap",
                    f"OTIF ranges {pct(min(c.otif for c in cards.values()))}"
                    f"-{pct(max(c.otif for c in cards.values()))} across the "
                    f"{len(cards)} mode(s) that could be scored.",
                    impact=f"prefer '{chosen}' for time-critical lanes"),
            Finding("Thin, uniform margins",
                    f"All segments ~{overall_margin*100:.0f}%; lowest is {worst_seg['Customer Segment']} "
                    f"({worst_seg['margin']*100:.1f}%)"
                    + (f"; {len(loss_cats)} loss-making categories." if len(loss_cats) else "."),
                    impact="cost-to-serve / pricing review"),
        ),
        kpis=(
            Kpi("Network on-time rate", f"{on_time*100:.1f}%", target="95%+", rationale="Delivered within scheduled days"),
            Kpi("Late-delivery risk", f"{late_risk*100:.0f}%", target="< 10%", rationale="Share of orders flagged at risk"),
            Kpi("Recommended mode", chosen, rationale="Best OTIF/lead/risk trade-off (TOPSIS)"),
            Kpi("Overall margin", f"{overall_margin*100:.1f}%", rationale="Profitability of the order book"),
            Kpi("Loss-making categories", str(len(loss_cats)), target="0", rationale="Cost-to-serve hotspots"),
        ),
        data_sources=(
            DataSource("real vs scheduled shipping days -> OTIF", "DataCoSupplyChainDataset.csv", "per run"),
            DataSource("Order Item Profit Ratio x Sales -> margin", "DataCoSupplyChainDataset.csv", "per run"),
            DataSource("Late_delivery_risk -> risk flag", "DataCoSupplyChainDataset.csv", "per run"),
        ),
        recommendations=(
            f"Route time-critical orders via '{chosen}'; audit the worst-OTIF mode's lanes.",
            f"Attack the {late_risk*100:.0f}% late-risk pool: tighten scheduled-day promises and carrier SLAs.",
            f"Cost-to-serve / pricing review on the lowest-margin segment ({worst_seg['Customer Segment']}, {worst_seg['margin']*100:.0f}%).",
        ),
        citations=tuple(cites),
        confidence=0.9,
        residual="OTIF uses real-vs-scheduled days (carrier promise, not a contractual SLA). Confirm contractual "
                 "service terms and category cost allocations before acting.",
        prepared="(stamp on export)",
    )
    out = deliverable.write_all(ROOT / "deliverables" / "scm_dataco")
    print(deliverable.to_markdown())
    print(f"  written: {out['report']}\n  written: {out['workbook']}")
    print(f"\n[mode] {SCM.label}")


if __name__ == "__main__":
    main()
