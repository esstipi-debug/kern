"""Cycle-count program agent job: a SKU list -> a balanced count schedule.

The data-prep + deck half of the cycle-count tool. Reads a SKU list with pandas directly
(deliberately *not* via jobs/intake.py, which the parallel loop owns) and builds the
cycle-count program via ``src.cycle_count``: count frequency per ABC class, each SKU's
counts spread evenly across the working year, and the resulting daily workload.

ABC class is taken from an explicit ``abc`` column when present; otherwise it is derived
from an annual-value column (or demand x unit_cost) via a Pareto split (default 80/95).
This plans the count program (the M6 cadence/schedule); ``reconciliation`` measures the
resulting record accuracy (IRA).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from src.cycle_count import (
    DEFAULT_POLICY,
    CountItem,
    CountPolicy,
    CountTask,
    annual_workload,
    build_schedule,
    daily_load,
)
from src.deliverable import DataSource, Deliverable, Finding, Kpi
from src.export import write_summary_csv

_PRODUCT_COLS = ("product_id", "sku", "SKU", "item", "Product", "product")
_ABC_COLS = ("abc", "ABC", "abc_class", "class", "Class", "abc_classification")
_VALUE_COLS = ("annual_value", "value", "Value", "annual_usage_value", "usage_value")
_DEMAND_COLS = ("annual_demand", "demand", "quantity", "qty", "Demand")
_UNIT_COST_COLS = ("unit_cost", "cost", "Unit Cost", "price")

_DEFAULT_THRESHOLDS = (0.80, 0.95)
_WORKING_DAYS = 250


@dataclass(frozen=True)
class CycleCountReport:
    n_items: int
    working_days: int
    counts_per_year: dict[str, int]        # policy: counts/year by ABC class
    workload: dict[str, int]               # annual counts by class + "total"
    total_counts: int
    peak_daily_load: int
    n_count_days: int
    by_class: dict[str, int]               # SKU count by ABC class
    schedule: tuple[CountTask, ...]
    summary: str


def _pick_column(df: pd.DataFrame, override: object, candidates: tuple[str, ...]) -> str | None:
    if override:
        return str(override) if str(override) in df.columns else None
    return next((c for c in candidates if c in df.columns), None)


def _pareto_abc(values: dict[str, float], thresholds: tuple[float, float]) -> dict[str, str]:
    """Assign ABC by descending value share: A below the first cut, B below the second, else C."""
    a_cut, b_cut = thresholds
    ranked = sorted(values.items(), key=lambda kv: (-kv[1], kv[0]))
    total = sum(v for _, v in ranked)
    out: dict[str, str] = {}
    cum = 0.0
    for pid, value in ranked:
        share = cum / total if total > 0 else 0.0
        out[pid] = "A" if share < a_cut else ("B" if share < b_cut else "C")
        cum += value
    return out


def prepare_records(df: pd.DataFrame, params: dict | None = None) -> list[CountItem]:
    """Build the ABC-classified count items: from an explicit abc column, else from value."""
    params = params or {}
    product = _pick_column(df, params.get("product_col"), _PRODUCT_COLS)
    if product is None:
        cols = list(df.columns)[:10]
        raise ValueError(f"could not find a product column (columns seen: {cols})")

    abc = _pick_column(df, params.get("abc_col"), _ABC_COLS)
    if abc is not None:
        items: list[CountItem] = []
        seen_bad: set[str] = set()
        for _, row in df.iterrows():
            cls = str(row[abc]).strip().upper()[:1]
            if cls not in ("A", "B", "C"):
                seen_bad.add(str(row[abc]))
                continue
            items.append(CountItem(product_id=str(row[product]), abc=cls))
        if seen_bad:
            raise ValueError(f"abc column has values outside A/B/C: {sorted(seen_bad)[:5]}")
        return items

    # No explicit class: derive value, then Pareto-classify.
    value = _pick_column(df, params.get("value_col"), _VALUE_COLS)
    if value is not None:
        values = {str(r[product]): float(r[value]) for _, r in df.iterrows() if pd.notna(r[value])}
    else:
        demand = _pick_column(df, params.get("demand_col"), _DEMAND_COLS)
        unit_cost = _pick_column(df, params.get("unit_cost_col"), _UNIT_COST_COLS)
        if demand is None or unit_cost is None:
            cols = list(df.columns)[:10]
            raise ValueError(
                "need an 'abc' column, or a value column, or demand + unit_cost to classify "
                f"(columns seen: {cols})"
            )
        values = {
            str(r[product]): float(r[demand]) * float(r[unit_cost])
            for _, r in df.iterrows() if pd.notna(r[demand]) and pd.notna(r[unit_cost])
        }
    thresholds = tuple(params.get("abc_thresholds", _DEFAULT_THRESHOLDS))
    classes = _pareto_abc(values, thresholds)
    return [CountItem(product_id=pid, abc=cls) for pid, cls in classes.items()]


def prepare(data_path: str, params: dict | None = None) -> list[CountItem]:
    """Read a SKU CSV and build the ABC-classified count items."""
    return prepare_records(pd.read_csv(data_path), params)


def run(items: list[CountItem], *, working_days: int = _WORKING_DAYS,
        policy: CountPolicy = DEFAULT_POLICY) -> CycleCountReport:
    """Build the balanced count schedule and roll up the annual + daily workload."""
    schedule = build_schedule(items, policy, working_days=working_days)
    workload = annual_workload(items, policy)
    load = daily_load(schedule, working_days)
    by_class: dict[str, int] = {}
    for item in items:
        by_class[item.abc] = by_class.get(item.abc, 0) + 1
    summary = (
        f"Cycle-count program for {len(items)} SKU(s): {workload.get('total', 0)} counts/year, "
        f"peak {max(load) if load else 0}/day across {sum(1 for x in load if x > 0)} count day(s)."
    )
    return CycleCountReport(
        n_items=len(items),
        working_days=working_days,
        counts_per_year=dict(policy.counts_per_year),
        workload=workload,
        total_counts=workload.get("total", 0),
        peak_daily_load=max(load) if load else 0,
        n_count_days=sum(1 for x in load if x > 0),
        by_class=by_class,
        schedule=tuple(schedule),
        summary=summary,
    )


def verify(report: CycleCountReport) -> list[str]:
    """QA gate: SKUs present, positive horizon, schedule size matches the annual workload."""
    issues: list[str] = []
    if report.n_items <= 0:
        issues.append("no SKUs to schedule")
    if report.working_days <= 0:
        issues.append("working_days must be positive")
    if len(report.schedule) != report.total_counts:
        issues.append(f"schedule size {len(report.schedule)} != annual workload {report.total_counts}")
    if report.peak_daily_load < 0:
        issues.append("negative daily load")
    return issues


def write_operational(report: CycleCountReport, out_dir: str | Path, client: str = "Client") -> dict[str, Path]:
    """The machine-readable deliverable: the prioritized count schedule (day-sorted)."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    rows = [
        {"working_day": t.day, "product_id": t.product_id, "abc": t.abc}
        for t in report.schedule
    ]
    return {"csv": write_summary_csv(rows, d / "cycle_count_schedule.csv")}


def build_deck(
    report: CycleCountReport,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple[str, ...] = (),
    confidence: float = 0.85,
) -> Deliverable:
    """Compose the cycle-count program: how often to count each class and the daily load."""
    cpy = report.counts_per_year
    summary = (
        f"Cycle-count program for {report.n_items} SKU(s): {report.total_counts} counts/year "
        f"(A x{cpy.get('A', 0)} / B x{cpy.get('B', 0)} / C x{cpy.get('C', 0)} per year), "
        f"peak {report.peak_daily_load} count(s)/day across {report.n_count_days} working day(s)."
    )

    findings = [
        Finding(
            "Count frequency by ABC class",
            f"A x{cpy.get('A', 0)}/yr, B x{cpy.get('B', 0)}/yr, C x{cpy.get('C', 0)}/yr; "
            f"{report.total_counts} counts/year total.",
            impact="high-value SKUs are verified most often",
        ),
        Finding(
            "Balanced daily workload",
            f"Peak {report.peak_daily_load} count(s)/day across {report.n_count_days} of "
            f"{report.working_days} working days.",
            impact="even load keeps counting feasible without an annual shutdown",
        ),
        Finding(
            "SKU mix by class",
            f"{report.by_class.get('A', 0)} A / {report.by_class.get('B', 0)} B / "
            f"{report.by_class.get('C', 0)} C SKU(s).",
            impact="drives where the counting effort concentrates",
        ),
    ]

    kpis = (
        Kpi("SKUs", f"{report.n_items}", rationale="Items in the count program"),
        Kpi("Counts per year", f"{report.total_counts}", target="balance",
            rationale="Total annual count workload"),
        Kpi("Peak daily load", f"{report.peak_daily_load}", target="minimize",
            rationale="Busiest day's count count - the staffing constraint"),
        Kpi("Count days", f"{report.n_count_days}", rationale="Working days with at least one count"),
    )

    data_sources = (
        DataSource("SKU list + ABC class (or value to classify)", "Item master / ABC analysis",
                   "annual / on master change"),
    )

    recommendations = (
        "Run the schedule as a standing daily task; reconcile each count with the IRA tool.",
        "Raise A-item count frequency until inventory record accuracy holds above target.",
        "Re-classify ABC at least annually - value migration changes count frequency.",
    )

    return Deliverable(
        title="Cycle-Count Program",
        client=client,
        summary=summary,
        findings=tuple(findings),
        kpis=kpis,
        data_sources=data_sources,
        recommendations=recommendations,
        citations=tuple(citations),
        confidence=confidence,
        residual="Physical counting is a human act: the agent delivers the prioritized count "
                 "schedule and proposed cadence to approve and assign - it does not perform the count.",
        prepared=prepared,
    )
