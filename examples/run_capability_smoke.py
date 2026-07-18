"""Capability smoke harness -- exercise every registered Kern tool end-to-end and
report, per capability, whether it runs clean or where it breaks.

    python examples/run_capability_smoke.py                 # run all, print a matrix
    python examples/run_capability_smoke.py --only queuing dea
    python examples/run_capability_smoke.py --json out.json --md out.md
    python examples/run_capability_smoke.py --include-network   # also try price_watch / price_intelligence

What it does: for each Tool in the default registry it forces ``job_type=<key>``
(so routing never hides a tool), feeds a representative brief plus a minimal,
schema-valid fixture (synthetic CSVs generated at runtime -- nothing committed),
runs the full orchestrator pipeline (prepare -> run -> QA -> deliver) and records
the JobResult.status.

Statuses are bucketed so the output separates *real* breakage from *expected*
protective gating:

  PASS   status == ok                      the capability produced a deliverable
  GATED  needs_data / needs_clarification  ran, then asked for input (by design)
  FAIL   qa_failed / error / EXCEPTION     something is actually broken -- look here

Two tools (price_watch, price_intelligence) reach the network; offline they are
reported as SKIP unless --include-network is passed, and even then a network
error is not counted as a FAIL.

ASCII-only console output (Windows cp1252 safe).
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

# Make `scm_agent` importable no matter where this script is launched from.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scm_agent.orchestrator import Orchestrator  # noqa: E402
from scm_agent.tools import build_default_registry  # noqa: E402

_DATA = _REPO_ROOT / "data"

# --------------------------------------------------------------------------- #
# Per-tool fixtures. Column names are drawn from each job's own accepted-name
# vocabulary (the ``_*_COLS`` constants), so the job's column sniffing resolves
# them without any explicit params. Values are minimal but semantically valid
# (e.g. queuing service_rate > arrival_rate, newsvendor price > unit_cost).
# --------------------------------------------------------------------------- #

_CSV: dict[str, str] = {
    "cost_to_serve": """Customer Segment,Sales,Quantity,Order ID,COGS,Shipping Cost,returns_units
Retail,1200,40,O1,700,60,2
Retail,800,30,O2,500,45,1
Wholesale,5000,300,O3,3800,220,10
Wholesale,4200,260,O4,3100,190,5
Online,300,12,O5,180,25,0
Online,450,18,O6,270,30,1
""",
    "sourcing": """supplier,on_time,in_full,lead_time_days,units,defects,unit_price
Alpha,0.95,0.90,12,1000,5,10.5
Beta,0.80,0.75,20,800,30,9.8
Gamma,0.99,0.97,8,1200,2,11.2
""",
    "ddmrp": """part_id,adu,dlt,on_hand,on_order,qualified_demand
P1,50,10,300,100,40
P2,20,15,150,50,30
P3,80,7,400,0,90
""",
    "landed_cost": """sku,unit_cost,qty,freight,insurance,duty_rate,handling,broker_fee,incoterm
S1,10,500,300,50,0.05,40,120,FOB
S2,25,200,180,30,0.08,25,90,CIF
""",
    "whatif": """driver,base,low,high,unit
annual_demand,12000,9000,15000,units
holding_cost,3.0,2.0,4.0,usd
fixed_order_cost,75,50,120,usd
demand_std,40,25,60,units
lead_time,2,1,4,days
""",
    "financial_kpis": """product_id,cogs,avg_inventory_value,gross_margin,units_sold,units_on_hand,net_sales
P1,50000,12000,0.35,4000,600,77000
P2,30000,9000,0.40,2500,400,50000
""",
    "reconciliation": """product_id,system_qty,physical_qty,unit_cost
P1,100,95,12
P2,50,50,30
P3,200,215,5
""",
    "returns": """product_id,returned_units,reason,unit_cost,resale_value,sellable
P1,20,damaged,12,4,False
P2,10,wrong_item,30,28,True
P3,5,defective,5,0,False
""",
    "queuing": """station,arrival_rate,service_rate,wait_cost,server_cost
Dock A,8,10,50,20
Dock B,15,20,40,25
Pick line,30,45,30,18
""",
    "scheduling": """job,processing_time,due_date
J1,4,10
J2,2,6
J3,6,18
J4,3,8
""",
    "risk": """name,category,likelihood,impact_value
Supplier default,supply,0.15,200000
Port strike,logistics,0.08,120000
Demand shock,market,0.25,90000
""",
    "data_quality": """sku,name,gtin,unit_cost
S1,Widget Blue,0712345678901,10.5
S2,Widget Blue,0712345678901,10.5
S3,Gadget,,25
S4,Mystery,0799999999999,7
""",
    "dea": """unit,input_labor,input_capital,output_orders,output_revenue
DC1,10,100,800,50000
DC2,12,90,750,48000
DC3,8,120,900,60000
DC4,15,80,700,45000
""",
    "acceptance_sampling": """part,aql,ltpd
P1,0.010,0.05
P2,0.025,0.08
""",
    "earned_value": """task,planned,earned,actual
Design,10000,9000,9500
Build,20000,15000,16000
Test,8000,4000,3800
""",
    "learning_curve": """product,first_unit_cost,learning_rate,planned_volume
A,1000,0.85,200
B,500,0.90,500
""",
    "newsvendor": """product_id,mean_demand,std_demand,price,unit_cost,salvage_value
P1,100,20,50,20,5
P2,60,15,30,12,3
""",
    "cycle_count": """product_id,abc,annual_value,annual_demand,unit_cost
P1,A,500000,10000,50
P2,B,120000,6000,20
P3,C,20000,2000,10
""",
    "multi_echelon": """stage,order,lead_time,holding_cost,mean_demand,demand_std
Retail,3,2,1.0,100,20
DC,2,5,0.6,100,20
Plant,1,10,0.3,100,20
""",
    "transportation": """shipment_id,lane,weight_kg,distance_km,units,order_value
SH1,Bogota-Medellin,1200,420,300,15000
SH2,Bogota-Cali,800,460,200,9000
SH3,Medellin-Cali,1500,420,400,20000
""",
    "fefo": """product_id,lot_id,quantity,days_to_expiry,unit_cost,unit_price,daily_demand
P1,L1,100,10,5,9,20
P1,L2,80,30,5,9,20
P2,L3,50,5,8,15,10
""",
    "slotting": """order_id,product_id,unit_volume
O1,P1,2
O1,P2,1
O2,P1,2
O2,P3,3
O3,P1,2
O3,P2,1
""",
    "simulation": """product_id,mean_demand,std_demand,lead_time,holding_cost,order_cost,backorder_cost,review_period
P1,100,20,3,1.0,50,10,5
P2,60,12,2,0.8,40,8,5
""",
    "facility_location": """name,x,y,weight
C1,0,0,100
C2,10,0,80
C3,5,8,120
C4,2,3,60
""",
    "drp": """branch,period,demand,on_hand,lead_time,safety_stock,lot_size
North,1,100,300,2,50,100
North,2,120,300,2,50,100
North,3,90,300,2,50,100
South,1,60,150,1,30,50
South,2,80,150,1,30,50
South,3,70,150,1,30,50
""",
    "vehicle_routing": """stop_id,x,y,demand
Depot,0,0,0
S2,5,3,10
S3,8,1,15
S4,2,7,8
S5,6,6,12
""",
    "launch_readiness": """product_id,launch_date,expected_lift_pct,current_price,proposed_price,elasticity,on_hand,daily_demand,lead_time_days,demand_std,lead_time_std
P1,2026-09-01,0.30,20,17,-1.5,500,30,10,8,2
P2,2026-09-15,0.20,45,40,-1.2,300,15,14,5,3
""",
}

# Tools satisfied by a committed sample dataset (data/ is gitignored but these
# ship with the repo).
_SAMPLE: dict[str, Path] = {
    "inventory_optimization": _DATA / "sample_demand_portfolio.csv",
    "abc_xyz": _DATA / "sample_demand_portfolio.csv",
    "pricing": _DATA / "sample_pricing.csv",
    "excess_obsolete": _DATA / "sample_stock_snapshot.csv",
    "markdown_liquidation": _DATA / "sample_stock_snapshot.csv",
}

# Brief per tool (routing is bypassed via job_type; the brief still feeds the
# summary and any LLM-optional parsing).
_BRIEF: dict[str, str] = {
    "leadership_chain": "evaluate our supply chain leadership",
    "warehouse_layout": "design the warehouse slotting layout",
    "odoo_replenishment": "run an odoo replenishment dry run",
    "digital_twin": "simulate the supply network digital twin",
    "price_watch": "monitor a competitor site for price changes",
    "price_intelligence": "acquire competitor prices for our catalog",
    "inventory_optimization": "set up reorder points and safety stock",
    "abc_xyz": "classify the portfolio by ABC-XYZ",
    "pricing": "what price maximizes profit",
    "excess_obsolete": "flag excess and obsolete stock",
    "markdown_liquidation": "plan a markdown and liquidation for dead stock",
    "cost_to_serve": "compute cost to serve by segment",
    "sop": "run the S&OP demand cycle",
    "sourcing": "score and rank our suppliers",
    "ddmrp": "size DDMRP buffers and planning signals",
    "landed_cost": "compute total landed cost by SKU",
    "whatif": "run a what-if sensitivity on the plan",
    "financial_kpis": "compute inventory financial KPIs",
    "reconciliation": "reconcile system vs physical inventory",
    "returns": "analyze product returns disposition",
    "queuing": "size the service points with a waiting line model",
    "scheduling": "sequence the jobs on the machine",
    "risk": "score and prioritize supply chain risks",
    "forecast": "forecast demand for each SKU",
    "data_quality": "audit master data quality",
    "dea": "benchmark units with data envelopment analysis",
    "acceptance_sampling": "design an acceptance sampling plan",
    "earned_value": "compute earned value for the project",
    "learning_curve": "project cost with a learning curve",
    "newsvendor": "solve the newsvendor order quantity",
    "cycle_count": "build a cycle count program",
    "multi_echelon": "optimize the multi-echelon safety stock",
    "transportation": "analyze the transportation lanes",
    "fefo": "plan FEFO picking for perishables",
    "slotting": "slot products by affinity",
    "simulation": "simulate the inventory policy",
    "facility_location": "find the best facility location",
    "drp": "build a distribution requirements plan",
    "vehicle_routing": "route the delivery vehicles",
    "launch_readiness": "assess launch readiness",
}

_OVERRIDES: dict[str, dict] = {
    "leadership_chain": {"scores": "3 2 3 1 1", "name": "Team"},
    "vehicle_routing": {"capacity": 40},
}

# Tools that reach the network. Reported SKIP offline; a network error under
# --include-network is not counted as a FAIL.
_NETWORK = {"price_watch", "price_intelligence"}

_PASS = {"ok"}
_GATED = {"needs_data", "needs_clarification"}


@dataclass
class CaseResult:
    key: str
    bucket: str  # PASS | GATED | FAIL | SKIP
    status: str
    detail: str = ""
    deliverables: list[str] = field(default_factory=list)


def _write_forecast_csv(path: Path) -> None:
    """A short multi-SKU weekly demand series (forecast/sop want history)."""
    import pandas as pd

    rows = []
    for sku, base in (("SKU-A", 100), ("SKU-B", 40)):
        for wk in range(16):
            qty = base + (wk % 4) * 5 - (wk % 3) * 3
            rows.append({"sku": sku, "period": f"2026-W{wk + 1:02d}", "demand": max(1, qty)})
    pd.DataFrame(rows).to_csv(path, index=False)


def _write_sop_csv(path: Path) -> None:
    import pandas as pd

    rows = []
    for month in range(1, 7):  # span 6 months so the S&OP horizon has >=2 periods
        for day in (5, 15, 25):
            rows.append({"date": f"2026-{month:02d}-{day:02d}", "quantity": 80 + (month % 5) * 10 + day})
    pd.DataFrame(rows).to_csv(path, index=False)


def _write_excel_replenishment_xlsx(path: Path) -> None:
    """excel_replenishment needs a real workbook, not a CSV."""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Planilla"
    ws.append(["SKU", "Stock", "ROP", "Demand", "Cost"])
    ws.append(["A-1", 40, 60, 120, 10.0])
    ws.append(["A-2", 200, 90, 80, 25.0])
    ws.append(["A-3", 15, 30, 45, 5.0])
    wb.save(path)


def _write_price_intelligence_csv(path: Path) -> None:
    import pandas as pd

    pd.DataFrame(
        [{"sku": "S1", "competitor_url": "https://example.com/p/1", "our_price": 100.0}]
    ).to_csv(path, index=False)


def _fixture_path(key: str, fixtures_dir: Path) -> Path | None:
    """Materialize (or locate) the fixture for ``key``; None => no data needed."""
    if key in _SAMPLE:
        return _SAMPLE[key]
    if key in _CSV:
        p = fixtures_dir / f"{key}.csv"
        p.write_text(_CSV[key], encoding="utf-8")
        return p
    if key == "forecast":
        p = fixtures_dir / "forecast.csv"
        _write_forecast_csv(p)
        return p
    if key == "sop":
        p = fixtures_dir / "sop.csv"
        _write_sop_csv(p)
        return p
    if key == "excel_replenishment":
        p = fixtures_dir / "planilla.xlsx"
        _write_excel_replenishment_xlsx(p)
        return p
    if key == "price_intelligence":
        p = fixtures_dir / "price_intel_refs.csv"
        _write_price_intelligence_csv(p)
        return p
    return None  # no-data tools


def _run_case(orch: Orchestrator, key: str, fixtures_dir: Path,
              out_dir: Path, include_network: bool) -> CaseResult:
    if key in _NETWORK and not include_network:
        return CaseResult(key, "SKIP", "skipped", "network tool (pass --include-network)")

    data_path = _fixture_path(key, fixtures_dir)
    brief = _BRIEF.get(key, f"run {key}")
    overrides = dict(_OVERRIDES.get(key) or {})
    if key == "launch_readiness":
        # This tool takes TWO inputs: campaigns (data_path) + a separate
        # inventory/lead-time CSV passed via params['inventory_path'].
        inv = fixtures_dir / "launch_inventory.csv"
        inv.write_text(
            "product_id,on_hand,daily_demand,lead_time_days,demand_std,lead_time_std\n"
            "P1,500,30,10,8,2\n"
            "P2,300,15,14,5,3\n",
            encoding="utf-8",
        )
        overrides["inventory_path"] = str(inv)
    overrides = overrides or None
    try:
        res = orch.run(
            brief,
            data_path=str(data_path) if data_path else None,
            overrides=overrides,
            job_type=key,
            out_dir=str(out_dir / key),
        )
    except Exception as exc:  # noqa: BLE001 - the harness must survive any tool blowing up
        if key in _NETWORK:
            return CaseResult(key, "SKIP", "network_error", f"{type(exc).__name__}: {exc}"[:160])
        return CaseResult(key, "FAIL", "EXCEPTION", f"{type(exc).__name__}: {exc}"[:200])

    status = res.status
    if status in _PASS:
        bucket = "PASS"
    elif status in _GATED:
        bucket = "GATED"
    else:
        bucket = "FAIL"
    if key in _NETWORK and status not in _PASS:
        bucket = "SKIP"  # offline gating/errors on a network tool are not failures

    detail = res.summary or ""
    if res.qa_issues:
        detail = "QA: " + "; ".join(res.qa_issues)
    elif res.clarifications:
        detail = "ASK: " + "; ".join(res.clarifications)
    return CaseResult(
        key, bucket, status, detail[:200],
        deliverables=[str(p) for p in (res.deliverables or {}).values()],
    )


# --------------------------------------------------------------------------- #
# Stress / robustness pass -- feed each data tool degenerate inputs and see
# whether it degrades with a helpful message or breaks (or, worse, silently
# produces a deliverable from garbage).
# --------------------------------------------------------------------------- #

# Outcomes, worst first:
#   CRASH     an uncaught exception escaped the tool (a real robustness bug)
#   SUSPECT   returned ok on degenerate input (a deliverable built from garbage)
#   GRACEFUL  clean protective status with a message (needs_data/error/qa_failed)
_STRESS_ORDER = {"CRASH": 0, "SUSPECT": 1, "GRACEFUL": 2}


def _stress_variants(base_csv: str) -> dict[str, str]:
    """Derive degenerate CSVs from a tool's valid fixture."""
    lines = [ln for ln in base_csv.splitlines() if ln.strip()]
    header = lines[0] if lines else "a,b"
    n_cols = len(header.split(","))
    return {
        "empty": header + "\n",  # headers, zero rows
        "wrong_schema": "unrelated_x,unrelated_y\n1,2\n3,4\n",  # none of the expected columns
        "nan_garbage": header + "\n" + "\n".join(  # right columns, non-numeric junk
            ",".join(["junk"] * n_cols) for _ in range(3)
        ) + "\n",
    }


def _base_csv_for(key: str, fixtures_dir: Path) -> str | None:
    """The valid fixture as text, or None if the tool has no CSV fixture to mutate."""
    if key in _CSV:
        return _CSV[key]
    if key in _SAMPLE and _SAMPLE[key].exists():
        return _SAMPLE[key].read_text(encoding="utf-8")
    if key == "forecast":
        p = fixtures_dir / "f.csv"
        _write_forecast_csv(p)
        return p.read_text(encoding="utf-8")
    if key == "sop":
        p = fixtures_dir / "s.csv"
        _write_sop_csv(p)
        return p.read_text(encoding="utf-8")
    return None  # no-data / xlsx / network tools


def _run_stress(orch: Orchestrator, key: str, fixtures_dir: Path, out_dir: Path) -> CaseResult:
    base = _base_csv_for(key, fixtures_dir)
    if base is None:
        return CaseResult(key, "SKIP", "no-csv-fixture", "no data CSV to mutate")

    overrides = dict(_OVERRIDES.get(key) or {})
    if key == "launch_readiness":
        inv = fixtures_dir / "launch_inventory_stress.csv"
        inv.write_text(
            "product_id,on_hand,daily_demand,lead_time_days,demand_std,lead_time_std\n"
            "P1,500,30,10,8,2\n",
            encoding="utf-8",
        )
        overrides["inventory_path"] = str(inv)
    brief = _BRIEF.get(key, f"run {key}")

    worst = "GRACEFUL"
    worst_detail = ""
    for vname, csv in _stress_variants(base).items():
        p = fixtures_dir / f"stress_{key}_{vname}.csv"
        p.write_text(csv, encoding="utf-8")
        try:
            res = orch.run(brief, data_path=str(p), overrides=overrides or None,
                           job_type=key, out_dir=str(out_dir / "stress" / key / vname))
            outcome = "SUSPECT" if res.status in _PASS else "GRACEFUL"
            detail = f"{vname}: {res.status}"
        except Exception as exc:  # noqa: BLE001
            outcome = "CRASH"
            detail = f"{vname}: {type(exc).__name__}: {exc}"[:180]
        if _STRESS_ORDER[outcome] < _STRESS_ORDER[worst]:
            worst, worst_detail = outcome, detail
    return CaseResult(key, worst, worst, worst_detail[:200])


def _print_stress(results: list[CaseResult]) -> None:
    icon = {"CRASH": "[CRSH]", "SUSPECT": "[SUSP]", "GRACEFUL": "[ OK ]", "SKIP": "[SKIP]"}
    order = {"CRASH": 0, "SUSPECT": 1, "GRACEFUL": 2, "SKIP": 3}
    shown = [r for r in results if r.bucket != "SKIP"]
    print("\n" + "=" * 78)
    print("KERN CAPABILITY STRESS -- {} data tools x (empty / wrong-schema / garbage)".format(len(shown)))
    print("=" * 78)
    for r in sorted(results, key=lambda x: (order[x.bucket], x.key)):
        if r.bucket == "SKIP":
            continue
        print("{icon} {key:24s} {detail}".format(icon=icon[r.bucket], key=r.key, detail=r.detail)[:118])
    counts = {b: sum(1 for r in results if r.bucket == b) for b in ("CRASH", "SUSPECT", "GRACEFUL", "SKIP")}
    print("-" * 78)
    print("CRASH={CRASH}  SUSPECT={SUSPECT}  GRACEFUL={GRACEFUL}  (skipped {SKIP} non-CSV tools)".format(**counts))
    print("=" * 78)


def _print_matrix(results: list[CaseResult]) -> None:
    icon = {"PASS": "[ OK ]", "GATED": "[GATE]", "FAIL": "[FAIL]", "SKIP": "[SKIP]"}
    print("\n" + "=" * 78)
    print("KERN CAPABILITY SMOKE -- {} tools".format(len(results)))
    print("=" * 78)
    for r in sorted(results, key=lambda x: ({"FAIL": 0, "GATED": 1, "SKIP": 2, "PASS": 3}[x.bucket], x.key)):
        line = "{icon} {key:24s} {status:20s} {detail}".format(
            icon=icon[r.bucket], key=r.key, status=r.status, detail=r.detail
        )
        print(line[:118])
    counts = {b: sum(1 for r in results if r.bucket == b) for b in ("PASS", "GATED", "FAIL", "SKIP")}
    print("-" * 78)
    print("PASS={PASS}  GATED={GATED}  FAIL={FAIL}  SKIP={SKIP}".format(**counts))
    print("=" * 78)


# Bucket display order across both modes (worst first). `.get(..., 99)` keeps
# this safe if a new bucket is ever added.
_BUCKET_ORDER = {"FAIL": 0, "CRASH": 0, "GATED": 1, "SUSPECT": 1, "SKIP": 2, "PASS": 3, "GRACEFUL": 3}


def _write_reports(results: list[CaseResult], json_path: Path | None, md_path: Path | None) -> None:
    buckets = sorted({r.bucket for r in results}, key=lambda b: _BUCKET_ORDER.get(b, 99))
    counts = {b: sum(1 for r in results if r.bucket == b) for b in buckets}
    payload = {
        "total": len(results),
        "counts": counts,
        "cases": [
            {"key": r.key, "bucket": r.bucket, "status": r.status,
             "detail": r.detail, "deliverables": r.deliverables}
            for r in results
        ],
    }
    if json_path:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    if md_path:
        md_path.parent.mkdir(parents=True, exist_ok=True)
        lines = ["# Kern capability smoke", "",
                 "| Bucket | Count |", "|---|---|"]
        for b in buckets:
            lines.append("| {} | {} |".format(b, counts[b]))
        lines += ["", "| Tool | Bucket | Status | Detail |", "|---|---|---|---|"]
        for r in sorted(results, key=lambda x: (_BUCKET_ORDER.get(x.bucket, 99), x.key)):
            detail = r.detail.replace("|", "\\|")
            lines.append("| {} | {} | {} | {} |".format(r.key, r.bucket, r.status, detail))
        md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Exercise every Kern capability end-to-end.")
    p.add_argument("--only", nargs="*", default=None, help="run only these tool keys")
    p.add_argument("--include-network", action="store_true",
                   help="also attempt price_watch / price_intelligence (need network)")
    p.add_argument("--stress", action="store_true",
                   help="robustness pass: feed each data tool degenerate inputs "
                        "(empty / wrong-schema / garbage) and report crashes vs graceful handling")
    p.add_argument("--out", default="deliverables/capability_smoke",
                   help="root output directory for generated deliverables + reports")
    p.add_argument("--json", default=None, help="write the JSON report here (default: <out>/report.json)")
    p.add_argument("--md", default=None, help="write the Markdown report here (default: <out>/report.md)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out_dir = Path(args.out)
    json_path = Path(args.json) if args.json else out_dir / "report.json"
    md_path = Path(args.md) if args.md else out_dir / "report.md"

    registry = build_default_registry()
    orch = Orchestrator(registry=registry)
    keys = [t.key for t in registry.list()]
    if args.only:
        wanted = set(args.only)
        keys = [k for k in keys if k in wanted]
        missing = wanted - set(keys)
        if missing:
            print("unknown tool keys: {}".format(", ".join(sorted(missing))), file=sys.stderr)

    results: list[CaseResult] = []
    with tempfile.TemporaryDirectory(prefix="kern_smoke_") as tmp:
        fixtures_dir = Path(tmp)
        if args.stress:
            for key in keys:
                results.append(_run_stress(orch, key, fixtures_dir, out_dir))
        else:
            for key in keys:
                results.append(_run_case(orch, key, fixtures_dir, out_dir, args.include_network))

    if args.stress:
        _print_stress(results)
        _write_reports(results, json_path, md_path)
        print("reports -> {} , {}".format(json_path, md_path))
        return 1 if any(r.bucket == "CRASH" for r in results) else 0

    _print_matrix(results)
    _write_reports(results, json_path, md_path)
    print("reports -> {} , {}".format(json_path, md_path))
    return 1 if any(r.bucket == "FAIL" for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
