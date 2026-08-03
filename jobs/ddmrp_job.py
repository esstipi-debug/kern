"""DDMRP agent job: a parts/buffer CSV -> red/yellow/green zones + net-flow signals.

The data-prep + deck half of the DDMRP tool. Reads a parts table (ADU, decoupled lead
time, buffer-profile factors, and the current on-hand / on-order / qualified demand) with
pandas directly (deliberately *not* via jobs/intake.py, which the parallel loop owns),
sizes each buffer and computes the net-flow planning signal, then composes the buffer
plan deck inline. Column names are sniffed and overridable via params; LTF/VF default to
0.5 when absent.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from src.ddmrp import BufferZones, PlanningSignal, planning_signal, size_buffer
from src.deliverable import DataSource, Deliverable, Finding, Kpi
from src.export import write_summary_csv

_PART_COLS = ("part_id", "Part", "part", "sku", "SKU", "product_id", "item")
_ADU_COLS = ("adu", "ADU", "avg_daily_usage", "average_daily_usage", "daily_usage")
_DLT_COLS = ("dlt", "DLT", "decoupled_lead_time", "lead_time", "lead_time_days", "lead")
_LTF_COLS = ("ltf", "LTF", "lead_time_factor")
_VF_COLS = ("vf", "VF", "variability_factor")
_MOQ_COLS = ("moq", "MOQ", "min_order_qty")
_OCD_COLS = ("order_cycle_days", "order_cycle", "order_cycle_period")
_ONHAND_COLS = ("on_hand", "On Hand", "onhand", "stock", "inventory")
_ONORDER_COLS = ("on_order", "On Order", "onorder", "open_po", "open_orders")
_QD_COLS = ("qualified_demand", "qualified", "open_demand", "sales_orders", "demand")


@dataclass(frozen=True)
class DdmrpPart:
    part_id: str
    zones: BufferZones
    signal: PlanningSignal


@dataclass(frozen=True)
class DdmrpReport:
    parts: tuple[DdmrpPart, ...]   # sorted most-urgent first
    n_parts: int
    n_red: int
    n_yellow: int
    n_order: int
    total_order_qty: float


def _pick_column(df: pd.DataFrame, override: object, candidates: tuple[str, ...]) -> str | None:
    if override:
        return str(override) if str(override) in df.columns else None
    return next((c for c in candidates if c in df.columns), None)


def prepare_records(df: pd.DataFrame, params: dict | None = None) -> list[dict]:
    """Sniff the DDMRP columns and build one input record per part."""
    params = params or {}
    part = _pick_column(df, params.get("part_col"), _PART_COLS)
    adu = _pick_column(df, params.get("adu_col"), _ADU_COLS)
    dlt = _pick_column(df, params.get("dlt_col"), _DLT_COLS)
    missing = [n for n, c in (("part_id", part), ("adu", adu), ("dlt", dlt)) if c is None]
    if missing:
        cols = list(df.columns)[:10]
        raise ValueError(f"could not find {', '.join(missing)}; pass them in params (columns seen: {cols})")

    ltf = _pick_column(df, params.get("ltf_col"), _LTF_COLS)
    vf = _pick_column(df, params.get("vf_col"), _VF_COLS)
    moq = _pick_column(df, params.get("moq_col"), _MOQ_COLS)
    ocd = _pick_column(df, params.get("order_cycle_col"), _OCD_COLS)
    onhand = _pick_column(df, params.get("on_hand_col"), _ONHAND_COLS)
    onorder = _pick_column(df, params.get("on_order_col"), _ONORDER_COLS)
    qd = _pick_column(df, params.get("qualified_demand_col"), _QD_COLS)

    def _num(row, col, default):
        return float(row[col]) if col else float(default)

    return [
        {
            "part_id": str(row[part]),
            "adu": float(row[adu]),
            "dlt": float(row[dlt]),
            "ltf": _num(row, ltf, params.get("ltf", 0.5)),
            "vf": _num(row, vf, params.get("vf", 0.5)),
            "moq": _num(row, moq, 0.0),
            "order_cycle_days": _num(row, ocd, 0.0),
            "on_hand": _num(row, onhand, 0.0),
            "on_order": _num(row, onorder, 0.0),
            "qualified_demand": _num(row, qd, 0.0),
        }
        for _, row in df.iterrows()
    ]


def prepare(data_path: str, params: dict | None = None) -> list[dict]:
    """Read a parts CSV and build the DDMRP input records."""
    return prepare_records(pd.read_csv(data_path), params)


def run(records: list[dict]) -> DdmrpReport:
    """Size every buffer and compute its net-flow planning signal, most-urgent first."""
    parts: list[DdmrpPart] = []
    for r in records:
        zones = size_buffer(r["adu"], r["dlt"], ltf=r["ltf"], vf=r["vf"],
                            moq=r["moq"], order_cycle_days=r["order_cycle_days"])
        signal = planning_signal(zones, r["on_hand"], r["on_order"], r["qualified_demand"])
        parts.append(DdmrpPart(r["part_id"], zones, signal))
    parts.sort(key=lambda p: p.signal.priority)
    return DdmrpReport(
        parts=tuple(parts),
        n_parts=len(parts),
        n_red=sum(1 for p in parts if p.signal.zone == "red"),
        n_yellow=sum(1 for p in parts if p.signal.zone == "yellow"),
        n_order=sum(1 for p in parts if p.signal.order_recommended),
        total_order_qty=sum(p.signal.order_qty for p in parts),
    )


def verify(report: DdmrpReport) -> list[str]:
    """QA gate: parts present and each buffer's zones are well-ordered."""
    issues: list[str] = []
    if not report.parts:
        issues.append("no parts to buffer")
    for p in report.parts:
        z = p.zones
        # Check the CAUSE before the symptom: a negative ADU makes the zones
        # invalid, and "invalid buffer zones" alone gives the operator no reason
        # to go look at the usage column.
        if z.adu < 0:
            issues.append(
                f"negative average daily usage for {p.part_id} ({z.adu:,.1f}) - "
                "usage cannot be negative; verify the sign convention and the column mapping"
            )
            continue
        if not (z.tog >= z.toy >= z.tor >= 0):
            issues.append(f"invalid buffer zones for {p.part_id}")
    return issues


def write_operational(report: DdmrpReport, out_dir: str | Path, client: str = "Client") -> dict[str, Path]:
    """The machine-readable deliverable: one row per part with zones + signal."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "part_id": p.part_id,
            "adu": round(p.zones.adu, 2),
            "dlt": round(p.zones.dlt, 2),
            "red": round(p.zones.red, 1),
            "yellow": round(p.zones.yellow, 1),
            "green": round(p.zones.green, 1),
            "tor": round(p.zones.tor, 1),
            "toy": round(p.zones.toy, 1),
            "tog": round(p.zones.tog, 1),
            "nfp": round(p.signal.nfp, 1),
            "zone": p.signal.zone,
            "order_qty": round(p.signal.order_qty, 1),
            "priority": round(p.signal.priority, 3),
        }
        for p in report.parts
    ]
    return {"csv": write_summary_csv(rows, d / "ddmrp_buffers.csv")}


def build_deck(
    report: DdmrpReport,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple[str, ...] = (),
    confidence: float = 0.85,
) -> Deliverable:
    """Compose the DDMRP buffer plan: what to order now and how the buffers are sized."""
    summary = (
        f"Sized DDMRP buffers for {report.n_parts} parts: {report.n_red} in the red, "
        f"{report.n_order} need an order totaling {report.total_order_qty:,.0f} units."
    )

    findings: list[Finding] = []
    red = [p for p in report.parts if p.signal.zone == "red"]
    if red:
        names = ", ".join(f"{p.part_id} (+{p.signal.order_qty:,.0f})" for p in red[:5])
        findings.append(Finding(
            f"{len(red)} part(s) in the red - order now",
            f"Net-flow position has dropped into the red zone for: {names}. Order back up to "
            "Top of Green.",
            impact="prevent imminent stockouts",
        ))
    over = [p for p in report.parts if p.signal.zone == "over_green"]
    if over:
        findings.append(Finding(
            f"{len(over)} part(s) above Top of Green",
            "Net-flow position exceeds the full buffer - excess stock or open orders tied up here.",
            impact="defer / cancel open supply; free working capital",
        ))
    findings.append(Finding(
        "Buffers driven by net flow, not a forecast-only reorder point",
        "Each zone = ADU x DLT scaled by the lead-time and variability factors; execution "
        "follows the net-flow position (on-hand + on-order - qualified demand).",
        impact="decoupled, demand-driven replenishment",
    ))

    kpis = (
        Kpi("Parts buffered", str(report.n_parts), rationale="Catalog coverage of the buffer plan"),
        Kpi("In the red", str(report.n_red), target="0", rationale="Parts needing an immediate order"),
        Kpi("Orders recommended", str(report.n_order), rationale="Parts at/below Top of Yellow"),
        Kpi("Total order quantity", f"{report.total_order_qty:,.0f}",
            rationale="Units to bring net flow back to Top of Green"),
    )

    data_sources = (
        DataSource("Part ADU / decoupled lead time / on-hand / on-order / qualified demand", "ERP / MRP data", "daily"),
        DataSource("Buffer-profile factors (LTF / VF / MOQ)", "engagement parameters", "per run"),
    )

    recommendations = ["Issue replenishment orders for the red parts up to Top of Green."]
    if over:
        recommendations.append("Review parts above Top of Green for deferral / cancellation of open supply.")
    recommendations.append("Re-profile buffers (LTF/VF) as lead times and demand variability shift.")

    return Deliverable(
        title="DDMRP Buffer Plan",
        client=client,
        summary=summary,
        findings=tuple(findings),
        kpis=kpis,
        data_sources=data_sources,
        recommendations=tuple(recommendations),
        citations=tuple(citations),
        confidence=confidence,
        residual="Issuing the replenishment orders (and adjusting buffer profiles) is the "
                 "planner's call; this sizes the buffers and ranks the execution priority.",
        prepared=prepared,
    )
