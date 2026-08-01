"""Run the new Fase 0/1 capabilities on a real dataset.

Usage:
    python examples/run_new_capabilities.py --data data/sample_demand_portfolio.csv

ASCII-only output (Windows cp1252 safe). Exact figures come from the data; any
modeling assumption is labelled [ASSUMPTION].
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jobs.financial_kpis_job import fmt  # noqa: E402
from scm_agent.orchestrator import Orchestrator  # noqa: E402
from src.alerting import alerts_outcome, detect_events  # noqa: E402
from src.classification import classify_portfolio, portfolio_summary  # noqa: E402
from src.ddmrp import planning_signal, size_buffer  # noqa: E402
from src.financial_kpis import (  # noqa: E402
    days_inventory_outstanding,
    gmroi,
    inventory_turns,
)
from src.guided import passed_guided  # noqa: E402


def load(data_path: Path):
    df = pd.read_csv(data_path)
    df["date"] = pd.to_datetime(df["date"])
    period_days = int(df.groupby("product_id")["date"].diff().dt.days.median() or 7)
    window_days = max(1, (df["date"].max() - df["date"].min()).days)

    skus = []
    for pid, g in df.groupby("product_id"):
        g = g.sort_values("date")
        demand = g["quantity"].astype(float).tolist()
        skus.append({
            "product_id": str(pid),
            "unit_cost": float(g["unit_cost"].mean()),
            "lead_time_days": float(g["lead_time_days"].median()),
            "demand": demand,
            "adu_daily": (sum(demand) / len(demand)) / period_days,
            "last_on_hand": demand[-1],
        })
    return df, skus, period_days, window_days


def section(title):
    print("\n" + "=" * 68 + f"\n{title}\n" + "=" * 68)


def print_financial_kpis(annual_cogs: float, avg_inventory_cost: float, gm_pct: float) -> None:
    """The turns / DIO / GMROI line, printed so an absence cannot pass for a good score.

    All three are ``float | None`` since PR #193. They used to return ``inf`` with no
    inventory to cycle - infinite turns and a zero-day DIO is a flawless operation, which
    is the most flattering thing this demo could possibly print from having no data.
    """
    annual_sales = annual_cogs / (1 - gm_pct)
    gross_margin_value = annual_sales - annual_cogs
    turns = fmt(inventory_turns(annual_cogs, avg_inventory_cost), ".2f")
    dio = fmt(days_inventory_outstanding(avg_inventory_cost, annual_cogs), ".1f")
    gm = fmt(gmroi(gross_margin_value, avg_inventory_cost), ".2f")
    print(f"  inventory turns: {turns}   DIO: {dio} days   "
          f"GMROI [ASSUMPTION GM {gm_pct*100:.0f}%]: {gm}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/sample_demand_portfolio.csv")
    args = ap.parse_args()
    data_path = (ROOT / args.data) if not Path(args.data).is_absolute() else Path(args.data)

    df, skus, period_days, window_days = load(data_path)
    print(f"Dataset: {data_path.name}  |  SKUs: {len(skus)}  |  rows: {len(df)}  "
          f"|  period: {period_days}d  |  window: {window_days}d")

    # 1) ABC-XYZ classification (M4)
    section("1) ABC-XYZ classification (M4)")
    items = [{"product_id": s["product_id"], "unit_cost": s["unit_cost"], "demand": s["demand"]} for s in skus]
    classes = classify_portfolio(items)
    summ = portfolio_summary(classes)
    for cell in sorted(summ):
        b = summ[cell]
        print(f"  {cell}: {b['count']:>4} SKUs   value share {b['value_share']*100:5.1f}%")
    print("  top 5 by value:")
    for c in classes[:5]:
        print(f"    {c.product_id:<10} {c.cell}  SL={c.service_level:.2f}  CV={c.cv:5.2f}  "
              f"policy={c.policy}  buffer={c.buffer_distribution}")

    # 2) DDMRP buffer + net-flow signal (M5) for the top-value SKU
    section("2) DDMRP buffer + net-flow planning (M5) - top-value SKU")
    top = max(skus, key=lambda s: s["unit_cost"] * sum(s["demand"]))
    adu, dlt = top["adu_daily"], top["lead_time_days"]
    zones = size_buffer(adu, dlt, ltf=0.5, vf=0.5)  # [ASSUMPTION] medium LTF/VF profile
    on_hand = round(top["last_on_hand"])
    sig = planning_signal(zones, on_hand=on_hand, on_order=0, qualified_demand=round(adu * period_days))
    print(f"  SKU {top['product_id']}  ADU={adu:.2f}/day  DLT={dlt:.0f}d  [ASSUMPTION LTF=VF=0.5]")
    print(f"  zones  RED={zones.red:.0f}  YELLOW={zones.yellow:.0f}  GREEN={zones.green:.0f}  "
          f"TOG={zones.tog:.0f}")
    print(f"  on_hand={on_hand}  ->  NFP={sig.nfp:.0f}  zone={sig.zone.upper()}  "
          f"reorder={sig.order_recommended}  qty={sig.order_qty:.0f}  priority={sig.priority:.2f}")

    # 3) Inventory financial KPIs (M13) - portfolio aggregate
    section("3) Inventory financial KPIs (M13) - portfolio aggregate")
    cogs_window = float((df["quantity"] * df["unit_cost"]).sum())
    annual_cogs = cogs_window * 365.0 / window_days
    cover_weeks = (df["lead_time_days"].mean() / 7.0) + 4.0  # [ASSUMPTION] lead + 4 wks safety
    avg_inventory_cost = annual_cogs * cover_weeks / 52.0
    gm_pct = 0.35  # [ASSUMPTION] gross margin
    print(f"  annual COGS (from data, annualized): {annual_cogs:,.0f}")
    print(f"  avg inventory value [ASSUMPTION {cover_weeks:.1f} wks cover]: {avg_inventory_cost:,.0f}")
    print_financial_kpis(annual_cogs, avg_inventory_cost, gm_pct)

    # 4) Inventory alerting (M14) -> Guided Execution Layer
    section("4) Inventory alerting (M14) -> protected handoff")
    snapshot = []
    for s in skus:
        rop = round(s["adu_daily"] * s["lead_time_days"])
        snapshot.append({
            "product_id": s["product_id"],
            "on_hand": round(s["last_on_hand"]),  # last observed demand as a current-stock proxy
            "reorder_point": rop,
            "avg_daily_demand": s["adu_daily"],
        })
    events = detect_events(snapshot)
    from collections import Counter
    kinds = Counter(e.kind for e in events)
    print(f"  events: {dict(kinds)}  (of {len(snapshot)} SKUs)")
    for e in events[:5]:
        print(f"    [{e.severity}] {e.message}")
    outcome = alerts_outcome(events)
    print(f"  guided outcome: status={outcome.status}  protected={passed_guided(outcome)}  "
          f"high-severity residuals={len(outcome.residuals)}")

    # 5) End-to-end agent run (orchestrator) -> guided contract attached
    section("5) Orchestrator end-to-end (guided contract attached)")
    result = Orchestrator().run(
        "set up reorder points", data_path=str(data_path),
        out_dir=str(ROOT / "deliverables" / "_demo"),
    )
    g = result.guided
    print(f"  status={result.status}  confidence={result.confidence:.2f}  "
          f"deliverables={len(result.deliverables)}")
    print(f"  guided: status={g.status if g else None}  "
          f"options={len(g.options) if g else 0}  handoffs={len(g.handoffs) if g else 0}")
    if result.deliverables:
        for name, path in result.deliverables.items():
            print(f"    -> {name}: {path}")


if __name__ == "__main__":
    main()
