"""DRP agent job: a long-format demand CSV -> time-phased releases per branch + the DC.

The data-prep + deck half of the DRP tool. Reads one row per (branch, period) demand with
pandas directly (deliberately *not* via jobs/intake.py, which the parallel loop owns), runs the
time-phased DRP grid per branch via ``src.drp``, then rolls the branches' planned order releases
up as the gross requirements at the central DC and plans it too. Branch attributes (on-hand,
lead time, safety stock, lot size) come from columns when present, else from params; the DC
attributes come from ``dc_*`` params.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from src.deliverable import DataSource, Deliverable, Finding, Kpi
from src.drp import Branch, DrpRow, drp_plan, rollup_gross_requirements
from src.export import write_summary_csv

_BRANCH_COLS = ("branch", "location", "warehouse", "site", "node", "dc", "Branch")
_PERIOD_COLS = ("period", "week", "month", "t", "bucket", "Period")
_DEMAND_COLS = ("demand", "forecast", "quantity", "qty", "requirement", "Demand")
_ONHAND_COLS = ("on_hand", "onhand", "stock", "inventory")
_LEAD_COLS = ("lead_time", "lead", "lt")
_SS_COLS = ("safety_stock", "ss", "safety")
_LOT_COLS = ("lot_size", "lot", "moq")
#: Stock already ordered and arriving in a period -- the standard DRP input this job was
#: missing. Read per (branch, period), like demand, because a receipt lands in ONE period.
#: The output column of a replenishment plan ("pedir") is deliberately not a candidate:
#: reading a plan's own proposal back as incoming supply nets it against itself.
_RECEIPT_COLS = (
    "scheduled_receipt", "scheduled_receipts", "in_transit", "intransit", "on_order",
    "incoming", "inbound", "en_transito", "por_recibir", "oc_pendiente",
)


@dataclass(frozen=True)
class BranchPlan:
    name: str
    releases: tuple[float, ...]
    total_release: float
    plan: tuple[DrpRow, ...]


@dataclass(frozen=True)
class DrpReport:
    n_branches: int
    n_periods: int
    branch_plans: tuple[BranchPlan, ...]
    dc_gross_requirements: tuple[float, ...]
    dc_plan: tuple[DrpRow, ...]
    total_branch_releases: float
    dc_release_total: float
    peak_period: int
    peak_qty: float
    summary: str
    #: Branches whose rows declared stock already ordered and arriving. ``0`` with
    #: ``n_branches > 0`` means the file carried no such column, so every release below
    #: may re-order stock that is already bought -- the deck says so rather than letting
    #: an absent column read as an empty pipeline.
    n_with_receipts: int = 0
    #: The client's own column name for it, for the source map. Empty when there was none.
    receipt_column: str = ""


def _pick_column(df: pd.DataFrame, override: object, candidates: tuple[str, ...]) -> str | None:
    if override:
        return str(override) if str(override) in df.columns else None
    return next((c for c in candidates if c in df.columns), None)


def prepare_records(df: pd.DataFrame, params: dict | None = None) -> dict:
    """Pivot the long demand rows into a forecast series per branch + branch attributes."""
    params = params or {}
    branch_col = _pick_column(df, params.get("branch_col"), _BRANCH_COLS)
    period_col = _pick_column(df, params.get("period_col"), _PERIOD_COLS)
    demand_col = _pick_column(df, params.get("demand_col"), _DEMAND_COLS)
    missing = [n for n, c in (("branch", branch_col), ("period", period_col), ("demand", demand_col)) if c is None]
    if missing:
        cols = list(df.columns)[:10]
        raise ValueError(f"could not find {', '.join(missing)}; pass them in params (columns seen: {cols})")

    on_hand_col = _pick_column(df, params.get("on_hand_col"), _ONHAND_COLS)
    lead_col = _pick_column(df, params.get("lead_col"), _LEAD_COLS)
    ss_col = _pick_column(df, params.get("ss_col"), _SS_COLS)
    lot_col = _pick_column(df, params.get("lot_col"), _LOT_COLS)
    receipt_col = _pick_column(df, params.get("receipt_col"), _RECEIPT_COLS)

    periods = sorted(df[period_col].unique())
    index = {p: i for i, p in enumerate(periods)}
    n = len(periods)

    branches: list[Branch] = []
    n_with_receipts = 0
    for name, group in df.groupby(branch_col, sort=True):
        forecast = [0.0] * n
        # Empty when the file declares no column: an all-zero schedule would claim
        # nothing is inbound, which is the assumption this input exists to remove.
        scheduled = [0.0] * n if receipt_col else []
        for _, row in group.iterrows():
            forecast[index[row[period_col]]] += float(row[demand_col])
            if receipt_col and pd.notna(row[receipt_col]):
                scheduled[index[row[period_col]]] += float(row[receipt_col])
        if scheduled:
            n_with_receipts += 1
        first = group.iloc[0]

        def _attr(col, default):
            return float(first[col]) if col and pd.notna(first[col]) else float(default)

        branches.append(Branch(
            name=str(name), forecast=tuple(forecast),
            on_hand=_attr(on_hand_col, params.get("on_hand", 0.0)),
            lead_time=max(0, int(round(_attr(lead_col, params.get("lead_time", 1))))),
            safety_stock=_attr(ss_col, params.get("safety_stock", 0.0)),
            lot_size=_attr(lot_col, params.get("lot_size", 1.0)),
            scheduled_receipts=tuple(scheduled),
        ))

    return {
        "branches": branches,
        "n_periods": n,
        "n_with_receipts": n_with_receipts,
        "receipt_column": receipt_col or "",
        "dc": {
            "on_hand": float(params.get("dc_on_hand", 0.0)),
            "lead_time": max(0, int(round(float(params.get("dc_lead_time", 1))))),
            "safety_stock": float(params.get("dc_safety_stock", 0.0)),
            "lot_size": float(params.get("dc_lot_size", 1.0)),
            # Same rule one echelon up, from params because the DC has no rows of its own:
            # declared or empty, never a fabricated zero schedule.
            "scheduled_receipts": tuple(
                float(v) for v in (params.get("dc_scheduled_receipts") or ())
            ),
        },
    }


def prepare(data_path: str, params: dict | None = None) -> dict:
    """Read a long-format demand CSV and build the DRP payload."""
    return prepare_records(pd.read_csv(data_path), params)


def run(payload: dict) -> DrpReport:
    """Plan each branch, roll up to the DC, and plan the DC."""
    n = payload["n_periods"]
    branches: list[Branch] = payload["branches"]
    plans = [drp_plan(b, n) for b in branches]
    branch_plans = [
        BranchPlan(
            name=b.name, releases=tuple(r.planned_order_release for r in plan),
            total_release=sum(r.planned_order_release for r in plan), plan=tuple(plan),
        )
        for b, plan in zip(branches, plans)
    ]

    dc_gross = rollup_gross_requirements(plans, n)
    dc = payload["dc"]
    dc_branch = Branch("Central DC", tuple(dc_gross), dc["on_hand"], dc["lead_time"],
                       dc["safety_stock"], dc["lot_size"],
                       scheduled_receipts=tuple(dc.get("scheduled_receipts", ())))
    dc_plan = drp_plan(dc_branch, n)

    peak_period = max(range(n), key=lambda t: dc_gross[t]) if n else 0
    summary = (
        f"DRP over {len(branches)} branch(es) x {n} period(s): "
        f"{sum(bp.total_release for bp in branch_plans):,.0f} unit(s) of branch releases roll up to "
        f"{sum(r.planned_order_release for r in dc_plan):,.0f} of DC releases; peak DC requirement in "
        f"period {peak_period} ({dc_gross[peak_period] if n else 0:,.0f})."
    )
    return DrpReport(
        n_branches=len(branches), n_periods=n, branch_plans=tuple(branch_plans),
        dc_gross_requirements=tuple(dc_gross), dc_plan=tuple(dc_plan),
        total_branch_releases=sum(bp.total_release for bp in branch_plans),
        dc_release_total=sum(r.planned_order_release for r in dc_plan),
        peak_period=peak_period, peak_qty=dc_gross[peak_period] if n else 0.0, summary=summary,
        n_with_receipts=int(payload.get("n_with_receipts", 0)),
        receipt_column=str(payload.get("receipt_column", "")),
    )


def verify(report: DrpReport) -> list[str]:
    """QA gate: branches + periods present, finite non-negative release totals."""
    import math

    issues: list[str] = []
    if report.n_branches <= 0:
        issues.append("no branches to plan")
    if report.n_periods <= 0:
        issues.append("no periods in the demand data")
    if not math.isfinite(report.total_branch_releases) or report.total_branch_releases < 0:
        issues.append("branch release total is negative or non-finite")
    return issues


def write_operational(report: DrpReport, out_dir: str | Path, client: str = "Client") -> dict[str, Path]:
    """The machine-readable deliverable: the time-phased release schedule (branches + DC)."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    rows = []
    for bp in report.branch_plans:
        for r in bp.plan:
            rows.append({
                "level": "branch", "node": bp.name, "period": r.period,
                "gross_requirements": round(r.gross_requirements, 1),
                # Lo ya comprado, aparte de lo que este plan propone pedir: un archivo que
                # los mezcla manda a comprar de nuevo lo que ya viene en camino. En blanco
                # cuando el archivo no declaro la columna, nunca 0.
                "scheduled_receipt": (
                    round(r.scheduled_receipt, 1) if report.n_with_receipts else ""
                ),
                "projected_on_hand": round(r.projected_on_hand, 1),
                "planned_order_release": round(r.planned_order_release, 1),
            })
    for r in report.dc_plan:
        rows.append({
            "level": "dc", "node": "Central DC", "period": r.period,
            "gross_requirements": round(r.gross_requirements, 1),
            "scheduled_receipt": round(r.scheduled_receipt, 1) if report.n_with_receipts else "",
            "projected_on_hand": round(r.projected_on_hand, 1),
            "planned_order_release": round(r.planned_order_release, 1),
        })
    return {"csv": write_summary_csv(rows, d / "drp_schedule.csv")}


def build_deck(
    report: DrpReport,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple[str, ...] = (),
    confidence: float = 0.85,
) -> Deliverable:
    """Compose the DRP study: the time-phased release plan across the distribution network."""
    summary = (
        f"DRP over {report.n_branches} branch(es) across {report.n_periods} period(s): "
        f"{report.total_branch_releases:,.0f} unit(s) of branch order releases roll up to "
        f"{report.dc_release_total:,.0f} at the central DC; peak DC requirement in period "
        f"{report.peak_period} ({report.peak_qty:,.0f})."
    )

    findings = []
    if report.n_branches and not report.n_with_receipts:
        # Antes de las cifras que califica: sin entradas programadas, cada liberacion de
        # abajo puede estar re-pidiendo una OC que ya esta en camino a la sucursal.
        findings.append(
            Finding(
                "Releases do NOT net stock already on order",
                "The file declared no scheduled-receipt column, so every branch was planned "
                "against its shelf alone. An order already raised to a branch is "
                "indistinguishable here from one that was never raised.",
                impact="subtract open orders by hand, or add a scheduled_receipt / en_transito "
                       "column per branch and period",
            )
        )
    elif report.n_with_receipts:
        findings.append(
            Finding(
                "Releases are net of stock already on order",
                f"{report.n_with_receipts} of {report.n_branches} branch(es) declared scheduled "
                f"receipts (column '{report.receipt_column}'); those units cover demand before "
                "any new release is planned.",
                impact="what is already inbound is discounted; do not order it twice",
            )
        )
    findings += [
        Finding(
            "Branch order releases",
            f"{report.total_branch_releases:,.0f} unit(s) planned for release across "
            f"{report.n_branches} branch(es), time-phased by each branch's lead time.",
            impact="the replenishment each branch must launch, period by period",
        ),
        Finding(
            "Central DC requirement",
            f"Rolled up to {report.dc_release_total:,.0f} of DC releases; the peak lands in period "
            f"{report.peak_period} ({report.peak_qty:,.0f}).",
            impact="size DC inbound + capacity to the peak period, not the average",
        ),
    ]

    kpis = (
        Kpi("Branches", f"{report.n_branches}", rationale="Stocking locations planned"),
        Kpi("Periods", f"{report.n_periods}", rationale="Planning horizon buckets"),
        Kpi("Branch releases", f"{report.total_branch_releases:,.0f}", target="-",
            rationale="Total branch order-release units over the horizon"),
        Kpi("DC releases", f"{report.dc_release_total:,.0f}", target="-",
            rationale="Total central-DC order-release units"),
        Kpi("Peak DC period", f"{report.peak_period}", target="-",
            rationale="Period carrying the largest DC requirement"),
    )

    data_sources = (
        DataSource("Per-branch demand by period", "Forecast / DRP demand plan", "per cycle"),
        DataSource("Branch on-hand, lead time, safety stock, lot size", "WMS / planning master", "per cycle"),
        DataSource(
            "Stock already on order per period",
            f"column '{report.receipt_column}'" if report.n_with_receipts
            else "not declared in the file - releases are planned against the shelf alone",
            "per cycle" if report.n_with_receipts else "absent",
        ),
    )

    recommendations = (
        "Release the planned branch orders on their offset periods to stay ahead of demand.",
        "Pre-position DC inbound for the peak period so branch releases are covered on time.",
        "Smooth lumpy releases with lot sizing or a small safety stock where capacity is tight.",
    )

    return Deliverable(
        title="Distribution Requirements Planning (DRP)",
        client=client,
        summary=summary,
        findings=tuple(findings),
        kpis=kpis,
        data_sources=data_sources,
        recommendations=recommendations,
        citations=tuple(citations),
        confidence=confidence,
        residual="Time-phased plan on a deterministic forecast: confirm the lead times, safety stock "
                 "and lot sizes, and check that requirements landing in period 0 (past-due) are "
                 "feasible to expedite before releasing the schedule.",
        prepared=prepared,
    )
