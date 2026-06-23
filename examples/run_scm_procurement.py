"""Procurement sourcing test on the Kaggle 'Procurement KPI Analysis' dataset.

777 purchase orders across 5 *competing* suppliers (Alpha/Beta/Gamma/Delta/
Epsilon) with order/delivery dates, status (full/partial/cancelled), quantity,
unit vs negotiated price, defects and compliance. This is the case Olist could
not cover: real multi-supplier sourcing -> supplier OTIF scorecards + MCDM
selection AMONG competing suppliers + negotiation savings + landed cost ->
deliverable. ASCII-only; modeling inputs labelled [ASSUMPTION].

Data: data/kaggle/procurement/  (download the CC0 set from Kaggle; gitignored).
Usage: python examples/run_scm_procurement.py
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

SLA_DAYS = 14  # [ASSUMPTION] promised procurement lead time (no promised date in data)


def section(t):
    print("\n" + "=" * 70 + f"\n{t}\n" + "=" * 70)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/kaggle/procurement/Procurement KPI Analysis Dataset.csv")
    args = ap.parse_args()
    path = (ROOT / args.data) if not Path(args.data).is_absolute() else Path(args.data)

    df = pd.read_csv(path)
    df["Order_Date"] = pd.to_datetime(df["Order_Date"], errors="coerce")
    df["Delivery_Date"] = pd.to_datetime(df["Delivery_Date"], errors="coerce")
    df["lead"] = (df["Delivery_Date"] - df["Order_Date"]).dt.days
    df["savings"] = (df["Unit_Price"] - df["Negotiated_Price"]) / df["Unit_Price"].clip(lower=0.01)
    df["Defective_Units"] = df["Defective_Units"].fillna(0.0)
    suppliers = sorted(df["Supplier"].dropna().unique())
    print(f"Dataset: Procurement KPI  |  POs: {len(df):,}  |  suppliers: {len(suppliers)}  "
          f"|  categories: {df['Item_Category'].nunique()}  |  mode: SCM")

    # 1) Supplier scorecards (on delivered/partial POs) -----------------------
    section("1) Supplier OTIF scorecards (M8) - 5 competing suppliers")
    dl = df[df["Delivery_Date"].notna()].copy()
    cards = {}
    for s in suppliers:
        g = dl[dl["Supplier"] == s]
        deliveries = [
            {"on_time": bool(ld <= SLA_DAYS), "in_full": st == "Delivered",
             "lead_time_days": float(ld), "units": float(q), "defects": float(dfu)}
            for ld, st, q, dfu in zip(g["lead"], g["Order_Status"], g["Quantity"], g["Defective_Units"])
        ]
        cards[s] = score_supplier(s, deliveries)
    stats = {}
    for s in suppliers:
        gall = df[df["Supplier"] == s]
        stats[s] = {
            "savings": float(gall["savings"].mean()),
            "compliance": float((gall["Compliance"] == "Yes").mean()),
            "cancel": float((gall["Order_Status"] == "Cancelled").mean()),
        }
        c = cards[s]
        print(f"  {s:<16} OTIF={c.otif*100:5.1f}%  on-time={c.on_time_rate*100:5.1f}%  "
              f"in-full={c.in_full_rate*100:5.1f}%  lead={c.avg_lead_time:4.1f}d  "
              f"defect_ppm={c.ppm:>8,.0f}  savings={stats[s]['savings']*100:4.1f}%  "
              f"compliant={stats[s]['compliance']*100:4.0f}%")

    # 2) MCDM supplier selection among the 5 ----------------------------------
    section("2) MCDM supplier selection (TOPSIS) - OTIF / defects / savings / lead / compliance")
    alts = {s: {"otif": cards[s].otif, "ppm": cards[s].ppm, "savings": stats[s]["savings"],
                "lead": cards[s].avg_lead_time, "compliance": stats[s]["compliance"]}
            for s in suppliers}
    criteria = [Criterion("otif", benefit=True), Criterion("ppm", benefit=False),
                Criterion("savings", benefit=True), Criterion("lead", benefit=False),
                Criterion("compliance", benefit=True)]
    weights = {"otif": 0.30, "ppm": 0.20, "savings": 0.20, "lead": 0.15, "compliance": 0.15}  # [ASSUMPTION]
    rank = topsis_rank(alts, criteria, weights)
    for i, name in enumerate(rank.ranking, 1):
        print(f"  {i}. {name:<16} closeness={rank.scores[name]:.3f}")
    chosen, worst = rank.ranking[0], rank.ranking[-1]

    # 3) Negotiation savings + landed cost for the chosen supplier ------------
    section("3) Negotiation savings + landed cost - chosen supplier")
    gc = df[df["Supplier"] == chosen]
    spend_list = float((gc["Unit_Price"] * gc["Quantity"]).sum())
    spend_neg = float((gc["Negotiated_Price"] * gc["Quantity"]).sum())
    qty = float(gc["Quantity"].sum())
    lc = landed_cost(unit_cost=spend_neg / qty, qty=qty, freight=0.06 * spend_neg,
                     insurance=0.005 * spend_neg, duty_rate=0.0, handling=0.01 * spend_neg,
                     broker_fee=0.0, incoterm="CIF")
    overall_savings = float(df["savings"].mean())
    realized = float(((df["Unit_Price"] - df["Negotiated_Price"]) * df["Quantity"]).sum())
    print(f"  chosen '{chosen}': spend list=${spend_list:,.0f} -> negotiated=${spend_neg:,.0f} "
          f"(saved {(1-spend_neg/spend_list)*100:.1f}%)")
    print(f"  landed/unit=${lc.per_unit:.2f}  (+{(lc.per_unit/(spend_neg/qty)-1)*100:.0f}% over negotiated unit)")
    print(f"  portfolio: avg negotiation savings {overall_savings*100:.1f}%  -> ${realized:,.0f} realized")

    # 4) Deliverable ----------------------------------------------------------
    section("4) SCM deliverable (generated)")
    kb = KnowledgeBase()
    cites = [f"{h.label} - {h.source}{(' ' + h.location) if h.location else ''}"
             for h in kb.search("supplier selection sourcing scorecard total cost", graph="books", limit=3)]
    deliverable = Deliverable(
        title="Strategic Sourcing Review (Procurement KPIs)",
        client="Global Enterprise",
        summary=(f"Scored {len(suppliers)} competing suppliers across {len(df):,} POs. "
                 f"Recommended '{chosen}' (best OTIF/defects/savings/compliance trade-off); "
                 f"avg negotiation savings {overall_savings*100:.1f}% (${realized:,.0f} realized)."),
        findings=(
            Finding(f"Award to '{chosen}'",
                    f"Leads the TOPSIS ranking; '{worst}' is the laggard.",
                    impact="consolidate spend on the top supplier"),
            Finding("Defect/quality spread",
                    f"Defect PPM ranges {min(c.ppm for c in cards.values()):,.0f}"
                    f"-{max(c.ppm for c in cards.values()):,.0f} across suppliers.",
                    impact="quality clauses + incoming inspection on the worst"),
            Finding("Negotiation is working",
                    f"Avg {overall_savings*100:.1f}% off list price, ${realized:,.0f} realized.",
                    impact="extend the playbook to low-savings suppliers"),
        ),
        kpis=(
            Kpi("Recommended supplier", chosen, rationale="Best multi-criteria trade-off (TOPSIS)"),
            Kpi("Best-supplier OTIF", f"{cards[chosen].otif*100:.0f}%", target="95%+",
                rationale="On-time in-full delivery reliability"),
            Kpi("Avg negotiation savings", f"{overall_savings*100:.1f}%", rationale="Cost reduction vs list price"),
            Kpi("Realized savings", f"${realized:,.0f}", rationale="Hard dollars from negotiation"),
            Kpi("Chosen-supplier landed/unit", f"${lc.per_unit:.2f}", rationale="Freight-loaded delivered cost"),
        ),
        data_sources=(
            DataSource("Order/Delivery dates + status -> OTIF", "Procurement KPI Analysis Dataset.csv", "per run"),
            DataSource("Unit vs Negotiated price -> savings", "Procurement KPI Analysis Dataset.csv", "per run"),
            DataSource("Defective_Units / Compliance -> quality & risk", "Procurement KPI Analysis Dataset.csv", "per run"),
        ),
        recommendations=(
            f"Award the next tranche to '{chosen}' and put '{worst}' on a corrective-action plan.",
            "Add quality clauses + incoming inspection where defect PPM is highest.",
            "Roll the negotiation playbook to the lowest-savings suppliers to lift realized savings.",
        ),
        citations=tuple(cites),
        confidence=0.86,
        residual="On-time uses an assumed 14-day SLA (no promised date in data) and duty is 0 (assumed domestic). "
                 "Confirm contractual lead-time SLAs and any import duty before awarding.",
        prepared="(stamp on export)",
    )
    out = deliverable.write_all(ROOT / "deliverables" / "scm_procurement")
    print(deliverable.to_markdown())
    print(f"  written: {out['report']}\n  written: {out['workbook']}")
    print(f"\n[mode] {SCM.label}")


if __name__ == "__main__":
    main()
