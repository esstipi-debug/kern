"""Put the SCM side of Kern to the test on the Superstore dataset.

Exercises the supply-chain-distinct modules (vs the inventory/demand side) on the
raw Superstore orders: a carrier OTIF scorecard, cost-to-serve / profitability,
MCDM carrier selection, and landed cost — then composes a client deliverable via
the deliverable generator. ASCII-only output (Windows cp1252 safe). Figures come
from the data; modeling inputs are labelled [ASSUMPTION].

Usage:
    python examples/run_scm_superstore.py --data data/kaggle/superstore.csv
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
from src.financial_kpis import cash_to_cash  # noqa: E402
from src.landed_cost import landed_cost  # noqa: E402
from src.mcdm import Criterion, topsis_rank  # noqa: E402
from src.supplier_scorecard import score_supplier  # noqa: E402

# Promised delivery SLA per ship mode (days) — the on-time benchmark. [ASSUMPTION]
SLA = {"Same Day": 0, "First Class": 2, "Second Class": 3, "Standard Class": 4}


def section(t):
    print("\n" + "=" * 68 + f"\n{t}\n" + "=" * 68)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/kaggle/superstore.csv")
    args = ap.parse_args()
    path = (ROOT / args.data) if not Path(args.data).is_absolute() else Path(args.data)

    df = pd.read_csv(path, encoding="latin-1")  # classic Superstore export is latin-1
    df["Order Date"] = pd.to_datetime(df["Order Date"], errors="coerce")
    df["Ship Date"] = pd.to_datetime(df["Ship Date"], errors="coerce")
    df["lead"] = (df["Ship Date"] - df["Order Date"]).dt.days
    df["cogs"] = df["Sales"] - df["Profit"]
    df["unit_cost"] = df["cogs"] / df["Quantity"].clip(lower=1)
    print(f"Dataset: {path.name}  |  orders: {df['Order ID'].nunique():,}  |  lines: {len(df):,}  "
          f"|  SKUs: {df['Product ID'].nunique():,}  |  mode: SCM")

    # 1) Carrier OTIF scorecard (ship modes treated as carriers) ----------------
    section("1) Carrier delivery scorecard - OTIF (M8)")
    cards = []
    for mode, g in df.groupby("Ship Mode"):
        sla = SLA.get(mode, 7)
        deliveries = [
            {"on_time": int(L) <= sla, "in_full": True, "lead_time_days": float(L),
             "units": float(q), "defects": 0.0}
            for L, q in zip(g["lead"], g["Quantity"]) if pd.notna(L)
        ]
        cards.append(score_supplier(mode, deliveries))
    cards.sort(key=lambda c: c.otif, reverse=True)
    for c in cards:
        print(f"  {c.supplier:<15} deliveries={c.deliveries:>5}  OTIF={c.otif*100:5.1f}%  "
              f"on-time={c.on_time_rate*100:5.1f}%  avg lead={c.avg_lead_time:4.1f}d")
    best, worst = cards[0], cards[-1]

    # 2) Cost-to-serve / profitability (the CFO lens) ---------------------------
    section("2) Cost-to-serve & profitability")
    seg = (df.groupby("Segment").agg(sales=("Sales", "sum"), profit=("Profit", "sum"),
                                     disc=("Discount", "mean")).reset_index())
    seg["margin"] = seg["profit"] / seg["sales"]
    for _, r in seg.iterrows():
        print(f"  {r['Segment']:<12} sales=${r['sales']:>12,.0f}  margin={r['margin']*100:5.1f}%  "
              f"avg discount={r['disc']*100:4.1f}%")
    state = df.groupby("State").agg(profit=("Profit", "sum"), sales=("Sales", "sum"))
    loss = state[state["profit"] < 0].sort_values("profit")
    overall_margin = df["Profit"].sum() / df["Sales"].sum()
    print(f"  --> {len(loss)} states are LOSS-MAKING; worst: {loss.index[0]} "
          f"(${loss.iloc[0]['profit']:,.0f} on ${loss.iloc[0]['sales']:,.0f} sales)")
    print(f"  --> overall gross margin: {overall_margin*100:.1f}%")

    # 3) MCDM carrier selection (BWM-style criteria + TOPSIS) -------------------
    section("3) MCDM carrier selection (TOPSIS)")
    alts = {c.supplier: {"otif": c.otif, "on_time": c.on_time_rate, "speed": c.avg_lead_time}
            for c in cards}
    criteria = [Criterion("otif", benefit=True), Criterion("on_time", benefit=True),
                Criterion("speed", benefit=False)]
    weights = {"otif": 0.45, "on_time": 0.35, "speed": 0.20}  # [ASSUMPTION] weighting
    rank = topsis_rank(alts, criteria, weights)
    for i, name in enumerate(rank.ranking, 1):
        print(f"  {i}. {name:<15} closeness={rank.scores[name]:.3f}")
    chosen = rank.ranking[0]

    # 4) Landed cost for the highest-volume SKU ---------------------------------
    section("4) Landed cost - top-volume SKU (CIF, [ASSUMPTION] rates)")
    top = (df.groupby("Product ID").agg(qty=("Quantity", "sum"), uc=("unit_cost", "mean"),
                                        name=("Product Name", "first"))
           .sort_values("qty", ascending=False).iloc[0])
    goods = float(top["uc"]) * float(top["qty"])
    lc = landed_cost(unit_cost=float(top["uc"]), qty=float(top["qty"]),
                     freight=0.08 * goods, insurance=0.01 * goods, duty_rate=0.05,
                     handling=0.02 * goods, broker_fee=150.0, incoterm="CIF")
    print(f"  SKU {top.name} ({str(top['name'])[:34]})  qty={top['qty']:.0f}  unit_cost=${top['uc']:.2f}")
    print(f"  landed/unit=${lc.per_unit:.2f}  (goods ${lc.goods_value:,.0f} + freight ${lc.freight:,.0f} "
          f"+ duty ${lc.duty:,.0f} + ins/handling/broker)  -> +{(lc.per_unit/top['uc']-1)*100:.0f}% over unit cost")

    # 5) Financial KPI - cash-to-cash -------------------------------------------
    c2c = cash_to_cash(dio=42.1, dso=45.0, dpo=30.0)  # [ASSUMPTION] DSO/DPO; DIO from inventory run
    print(f"\n  Cash-to-cash cycle [ASSUMPTION DSO=45, DPO=30, DIO=42 from inventory run]: {c2c:.0f} days")

    # 6) Compose the client deliverable -----------------------------------------
    section("6) SCM deliverable (generated)")
    kb = KnowledgeBase()
    cites = [f"{h.label} - {h.source}{(' ' + h.location) if h.location else ''}"
             for h in kb.search("cost to serve OTIF supplier performance", graph="books", limit=3)]
    deliverable = Deliverable(
        title="Supply Chain Performance Review (Superstore)",
        client="Superstore",
        summary=(f"Reviewed {df['Order ID'].nunique():,} orders / {len(df):,} lines across "
                 f"{df['Product ID'].nunique():,} SKUs. Overall gross margin {overall_margin*100:.1f}%; "
                 f"{len(loss)} loss-making states; recommended carrier '{chosen}'."),
        findings=(
            Finding(f"Carrier OTIF spread {worst.otif*100:.0f}%-{best.otif*100:.0f}%",
                    f"'{best.supplier}' leads on OTIF; '{worst.supplier}' lags.",
                    impact="shift volume to the reliable carrier"),
            Finding(f"{len(loss)} loss-making states",
                    f"Worst: {loss.index[0]} loses ${abs(loss.iloc[0]['profit']):,.0f}.",
                    impact="review pricing / cost-to-serve there"),
            Finding("Landed cost premium on top SKU",
                    f"Landed/unit ${lc.per_unit:.2f} is +{(lc.per_unit/top['uc']-1)*100:.0f}% over unit cost.",
                    impact="freight/duty are the levers"),
        ),
        kpis=(
            Kpi("Best carrier (OTIF)", f"{chosen} ({best.otif*100:.0f}%)", target="95%+",
                rationale="On-time in-full is the service the customer feels"),
            Kpi("Overall gross margin", f"{overall_margin*100:.1f}%", rationale="Profitability of the book of business"),
            Kpi("Loss-making states", str(len(loss)), target="0", rationale="Cost-to-serve hotspots"),
            Kpi("Top-SKU landed cost", f"${lc.per_unit:.2f}/unit", rationale="True delivered cost incl. freight/duty"),
            Kpi("Cash-to-cash cycle", f"{c2c:.0f} days", target="< 60", rationale="Working capital tied up end-to-end"),
        ),
        data_sources=(
            DataSource("Order/Ship dates -> lead time", "superstore.csv", "per run"),
            DataSource("Sales / Profit -> margin, cost-to-serve", "superstore.csv", "per run"),
            DataSource("Ship Mode -> carrier OTIF", "superstore.csv", "per run"),
        ),
        recommendations=(
            f"Route time-sensitive lanes to '{chosen}' (best OTIF/speed trade-off).",
            f"Run a cost-to-serve fix on the {len(loss)} loss-making states (pricing, min-order, freight).",
            "Renegotiate freight/duty on the top-volume SKU to cut the landed-cost premium.",
        ),
        citations=tuple(cites),
        confidence=0.85,
        residual="Carrier on-time uses assumed per-mode SLAs and DSO/DPO are placeholders; confirm "
                 "actual promised dates, carrier rates, and payment terms before acting.",
        prepared="(stamp on export)",
    )
    out = deliverable.write_all(ROOT / "deliverables" / "scm_superstore")
    print(deliverable.to_markdown())
    print(f"  written: {out['report']}")
    print(f"  written: {out['workbook']}")
    print(f"\n[mode] {SCM.label}: {len(SCM.deliverables)} deliverable types, {len(SCM.kpis)} KPIs in catalogue")


if __name__ == "__main__":
    main()
