"""Kraljic supplier-segmentation job: suppliers CSV -> normalized risk drivers -> deck.

The data-prep + deck half of the supplier_management tool. Reads a suppliers CSV
with pandas directly (deliberately NOT via jobs/intake.py), sniffs the spend
column and any present risk-driver columns, min-max normalizes each driver to
[0,1] (higher = riskier), then segments on the Kraljic matrix via the pure
src/supplier_management engine and composes the deck inline.

``run``/``verify``/``build_deck`` are deterministic; ``prepare`` reads a file.
Column names are sniffed and overridable via params.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from src.deliverable import DataSource, Deliverable, Finding, Kpi
from src.export import write_summary_csv
from src.guided import GuidedOutcome, verify_guided
from src.supplier_management import (
    RiskDriver,
    SupplierInput,
    SupplierSegment,
    segment_outcome,
    segment_suppliers,
)

_SUPPLIER_COLS = ("supplier", "Supplier", "vendor", "Vendor", "supplier_name", "supplier_id")
_SPEND_COLS = ("annual_value", "annual_spend", "spend", "spend_usd", "annual_value_usd", "value", "Spend")

# risk driver key -> candidate column names (all "higher = riskier").
_DRIVER_COLS: dict[str, tuple[str, ...]] = {
    "lead": ("lead_time_days", "lead_time", "Lead Time", "lead"),
    "single": ("single_source", "sole_source", "single_sourced", "sole_sourced"),
    "quality": ("defect_ppm", "ppm", "defect_rate", "reject_rate"),
    "financial": ("financial_risk", "credit_risk", "supplier_financial_risk"),
    "geo": ("geo_risk", "country_risk", "geopolitical_risk"),
}


@dataclass(frozen=True)
class SupplierManagementReport:
    segments: tuple[SupplierSegment, ...]
    drivers: tuple[RiskDriver, ...]
    outcome: GuidedOutcome
    quadrant_counts: dict[str, int]
    summary: str


def _pick_column(df: pd.DataFrame, override: object, candidates: tuple[str, ...]) -> str | None:
    if override:
        return str(override) if str(override) in df.columns else None
    return next((c for c in candidates if c in df.columns), None)


def _resolve_driver_cols(df: pd.DataFrame, params: dict) -> dict[str, str]:
    """Map each risk-driver key to a present column (params override the sniffing)."""
    overrides: dict[str, str] = params.get("risk_cols", {}) or {}
    resolved: dict[str, str] = {}
    for key, candidates in _DRIVER_COLS.items():
        col = _pick_column(df, overrides.get(key), candidates)
        if col is not None:
            resolved[key] = col
    return resolved


def normalize_drivers(df: pd.DataFrame, *, driver_cols: dict[str, str]) -> dict[str, dict[str, float]]:
    """Min-max scale each driver column to [0,1] (constant column -> all 0.0).

    Returns supplier name -> {driver_key: normalized score}, keyed by the sniffed
    supplier column (falls back to the DataFrame's positional index if no supplier
    column is present, so the function stays usable standalone on an arbitrary df).
    """
    supplier_col = _pick_column(df, None, _SUPPLIER_COLS)
    keys = df[supplier_col].astype(str) if supplier_col is not None else df.index.to_series().astype(str)

    normed: dict[str, dict[str, float]] = {}
    for key, col in driver_cols.items():
        series = pd.to_numeric(df[col], errors="coerce").fillna(0.0).astype(float)
        lo, hi = float(series.min()), float(series.max())
        span = hi - lo
        scaled = (series - lo) / span if span > 1e-12 else series * 0.0
        for pos, val in scaled.items():
            normed.setdefault(str(keys.loc[pos]), {})[key] = float(val)
    return normed


def prepare(data_path: str, params: dict | None = None) -> dict:
    """Read a suppliers CSV and build normalized SupplierInputs + the driver set."""
    params = params or {}
    df = pd.read_csv(data_path)

    supplier_col = _pick_column(df, params.get("supplier_col"), _SUPPLIER_COLS)
    if supplier_col is None:
        raise ValueError(
            f"could not find a supplier column; pass supplier_col in params "
            f"(columns seen: {list(df.columns)[:10]})"
        )
    spend_col = _pick_column(df, params.get("spend_col"), _SPEND_COLS)
    if spend_col is None:
        raise ValueError(
            f"could not find an annual spend/value column; pass spend_col in params "
            f"(columns seen: {list(df.columns)[:10]})"
        )

    driver_cols = _resolve_driver_cols(df, params)
    df = df.reset_index(drop=True)
    normed = normalize_drivers(df, driver_cols=driver_cols)

    suppliers: list[SupplierInput] = []
    for _, row in df.iterrows():
        suppliers.append(
            SupplierInput(
                supplier=str(row[supplier_col]),
                annual_value=float(pd.to_numeric(row[spend_col], errors="coerce") or 0.0),
                risk_scores=normed.get(str(row[supplier_col]), {}),
            )
        )
    drivers = [RiskDriver(key, 1.0) for key in driver_cols]
    return {"suppliers": suppliers, "drivers": drivers}


def run(
    suppliers: list[SupplierInput],
    drivers: list[RiskDriver],
    *,
    impact_pareto: float = 0.8,
    risk_threshold: float = 0.5,
) -> SupplierManagementReport:
    """Segment suppliers on the Kraljic matrix and present the SRM action list."""
    segments = segment_suppliers(
        suppliers, drivers, impact_pareto=impact_pareto, risk_threshold=risk_threshold
    )
    counts = {q: 0 for q in ("strategic", "bottleneck", "leverage", "non_critical")}
    for s in segments:
        counts[s.quadrant] += 1
    summary = (
        f"Segmented {len(segments)} suppliers on the Kraljic matrix: "
        f"{counts['strategic']} strategic, {counts['bottleneck']} bottleneck, "
        f"{counts['leverage']} leverage, {counts['non_critical']} non-critical."
    )
    outcome = (
        segment_outcome(segments, summary=summary)
        if segments
        else GuidedOutcome(status="executed", summary=summary)
    )
    return SupplierManagementReport(
        segments=tuple(segments),
        drivers=tuple(drivers),
        outcome=outcome,
        quadrant_counts=counts,
        summary=summary,
    )


def verify(report: SupplierManagementReport) -> list[str]:
    """QA gate: a usable segmentation honors the contract and placed suppliers."""
    issues = list(verify_guided(report.outcome))
    if not report.segments:
        issues.append("no suppliers segmented")
    return issues


def write_operational(
    report: SupplierManagementReport, out_dir: str | Path, client: str = "Client"
) -> dict[str, Path]:
    """The machine-readable deliverable: one row per supplier with its quadrant."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "supplier": s.supplier,
            "annual_value": round(s.annual_value, 2),
            "spend_share": round(s.spend_share, 4),
            "impact_band": s.impact_band,
            "supply_risk": round(s.supply_risk, 4),
            "risk_band": s.risk_band,
            "quadrant": s.quadrant,
            "strategy": s.strategy,
        }
        for s in report.segments
    ]
    return {"csv": write_summary_csv(rows, d / "supplier_segmentation.csv")}


def build_deck(
    report: SupplierManagementReport,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple[str, ...] = (),
    confidence: float = 0.85,
) -> Deliverable:
    """Compose the Kraljic segmentation study: quadrant mix + the priority actions."""
    counts = report.quadrant_counts
    strategic = [s for s in report.segments if s.quadrant == "strategic"]
    bottleneck = [s for s in report.segments if s.quadrant == "bottleneck"]

    findings = [
        Finding(
            "Kraljic quadrant mix",
            f"{counts['strategic']} strategic, {counts['bottleneck']} bottleneck, "
            f"{counts['leverage']} leverage, {counts['non_critical']} non-critical.",
            impact="each quadrant gets a distinct SRM playbook, not one-size-fits-all",
        )
    ]
    if strategic:
        names = ", ".join(s.supplier for s in strategic)
        findings.append(Finding(
            "Strategic suppliers - partner and develop",
            f"High spend AND high supply risk: {names}. These carry the business; "
            "manage them as long-term partnerships with joint planning and dual capacity.",
            impact="protect continuity of the highest-exposure spend",
        ))
    if bottleneck:
        names = ", ".join(s.supplier for s in bottleneck)
        findings.append(Finding(
            "Bottleneck suppliers - secure supply",
            f"Low spend but high supply risk: {names}. Small money, big disruption risk; "
            "buffer stock, qualify alternates and hedge the single points of failure.",
            impact="cut the tail risk that low spend hides",
        ))

    kpis = (
        Kpi("Suppliers segmented", str(len(report.segments)), rationale="Panel breadth"),
        Kpi("Strategic", str(counts["strategic"]), rationale="High impact + high risk"),
        Kpi("Bottleneck", str(counts["bottleneck"]), rationale="Low impact + high risk"),
        Kpi("Leverage", str(counts["leverage"]), rationale="High impact + low risk"),
        Kpi("Non-critical", str(counts["non_critical"]), rationale="Low impact + low risk"),
    )

    data_sources = (
        DataSource("Annual spend by supplier", "AP / procurement spend cube", "quarterly"),
        DataSource("Supply-risk drivers (lead time, single-source, quality, financial, geo)",
                   "supplier master / scorecards", "quarterly"),
    )

    recommendations = [
        "Partner and develop the strategic suppliers; lock long-term contracts with dual capacity.",
        "Dual-source or buffer every bottleneck supplier before its single point of failure bites.",
        "Run competitive tenders on leverage suppliers to convert buying power into savings.",
        "Automate non-critical spend (catalog / P-card) to reclaim buyer time.",
    ]

    return Deliverable(
        title="Supplier Portfolio Segmentation (Kraljic)",
        client=client,
        summary=report.summary,
        findings=tuple(findings),
        kpis=kpis,
        data_sources=data_sources,
        recommendations=tuple(recommendations),
        citations=tuple(citations),
        confidence=confidence,
        residual="Quadrant placement is analytical; the actual partnership, dual-sourcing and "
                 "tender decisions are commercial calls - this prepares the segmentation and the "
                 "prioritized action list; a human owns each supplier move.",
        prepared=prepared,
    )
