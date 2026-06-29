"""Facility-location agent job: a demand-points CSV -> the cost-minimizing site.

The data-prep + deck half of the facility-location tool. Reads demand points (coordinates +
load) with pandas directly (deliberately *not* via jobs/intake.py, which the parallel loop
owns) and finds the single-facility site that minimizes weighted travel via ``src.facility_location``:
the center of gravity (fast estimate) and the Weiszfeld 1-median (true optimum), compared
against the current site when one is supplied.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from src.deliverable import DataSource, Deliverable, Finding, Kpi
from src.export import write_summary_csv
from src.facility_location import (
    DemandPoint,
    Location,
    center_of_gravity,
    total_weighted_distance,
    weiszfeld,
)

_NAME_COLS = ("name", "location", "city", "node", "point", "customer", "label")
_X_COLS = ("x", "lon", "longitude", "x_coord", "easting")
_Y_COLS = ("y", "lat", "latitude", "y_coord", "northing")
_WEIGHT_COLS = ("weight", "demand", "volume", "load", "units", "tons")


@dataclass(frozen=True)
class FacilityLocationReport:
    n_points: int
    total_weight: float
    cog: Location
    cog_distance: float
    optimum: Location
    optimum_distance: float
    current: Location | None
    current_distance: float | None
    saving_vs_current: float | None
    saving_pct: float | None
    nearest_point: str
    summary: str


def _pick_column(df: pd.DataFrame, override: object, candidates: tuple[str, ...]) -> str | None:
    if override:
        return str(override) if str(override) in df.columns else None
    return next((c for c in candidates if c in df.columns), None)


def prepare_records(df: pd.DataFrame, params: dict | None = None) -> dict:
    """Sniff the coordinate + load columns and build the demand points + optional current site."""
    params = params or {}
    x = _pick_column(df, params.get("x_col"), _X_COLS)
    y = _pick_column(df, params.get("y_col"), _Y_COLS)
    missing = [n for n, c in (("x", x), ("y", y)) if c is None]
    if missing:
        cols = list(df.columns)[:10]
        raise ValueError(f"could not find {', '.join(missing)}; pass them in params (columns seen: {cols})")

    name = _pick_column(df, params.get("name_col"), _NAME_COLS)
    weight = _pick_column(df, params.get("weight_col"), _WEIGHT_COLS)
    points = [
        DemandPoint(
            name=str(row[name]) if name else f"P{i + 1}",
            x=float(row[x]), y=float(row[y]),
            weight=float(row[weight]) if weight and pd.notna(row[weight]) else 1.0,
        )
        for i, (_, row) in enumerate(df.iterrows())
    ]
    current = None
    if params.get("current_x") is not None and params.get("current_y") is not None:
        current = Location(float(params["current_x"]), float(params["current_y"]))
    return {"points": points, "current": current, "iterations": int(params.get("iterations", 200))}


def prepare(data_path: str, params: dict | None = None) -> dict:
    """Read a demand-points CSV and build the facility-location payload."""
    return prepare_records(pd.read_csv(data_path), params)


def run(payload: dict) -> FacilityLocationReport:
    """Compute the center of gravity, the Weiszfeld optimum, and the saving vs the current site."""
    points: list[DemandPoint] = payload["points"]
    cog = center_of_gravity(points)
    opt = weiszfeld(points, iterations=payload["iterations"])
    cog_dist = total_weighted_distance(points, cog)
    opt_dist = total_weighted_distance(points, opt)

    current = payload["current"]
    current_dist = saving = saving_pct = None
    if current is not None:
        current_dist = total_weighted_distance(points, current)
        saving = current_dist - opt_dist
        saving_pct = (saving / current_dist) if current_dist > 0 else 0.0

    nearest = min(points, key=lambda p: math.hypot(p.x - opt.x, p.y - opt.y)).name
    save_txt = f", {saving:,.0f} less weighted travel than the current site" if saving is not None else ""
    summary = (
        f"Facility location over {len(points)} demand point(s): optimum near '{nearest}' "
        f"at ({opt.x:,.2f}, {opt.y:,.2f}), {opt_dist:,.0f} total weighted distance{save_txt}."
    )
    return FacilityLocationReport(
        n_points=len(points), total_weight=sum(p.weight for p in points),
        cog=cog, cog_distance=cog_dist, optimum=opt, optimum_distance=opt_dist,
        current=current, current_distance=current_dist,
        saving_vs_current=saving, saving_pct=saving_pct, nearest_point=nearest, summary=summary,
    )


def verify(report: FacilityLocationReport) -> list[str]:
    """QA gate: points present, finite coordinates and a finite, non-negative optimum distance."""
    issues: list[str] = []
    if report.n_points <= 0:
        issues.append("no demand points to locate against")
    if not (math.isfinite(report.optimum.x) and math.isfinite(report.optimum.y)):
        issues.append("optimum coordinates are non-finite")
    if not math.isfinite(report.optimum_distance) or report.optimum_distance < 0:
        issues.append("optimum distance is negative or non-finite")
    return issues


def write_operational(report: FacilityLocationReport, out_dir: str | Path, client: str = "Client") -> dict[str, Path]:
    """The machine-readable deliverable: the candidate sites + their weighted travel."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    rows = [
        {"site": "weiszfeld_optimum", "x": round(report.optimum.x, 4), "y": round(report.optimum.y, 4),
         "total_weighted_distance": round(report.optimum_distance, 2)},
        {"site": "center_of_gravity", "x": round(report.cog.x, 4), "y": round(report.cog.y, 4),
         "total_weighted_distance": round(report.cog_distance, 2)},
    ]
    if report.current is not None and report.current_distance is not None:
        rows.append({"site": "current", "x": round(report.current.x, 4), "y": round(report.current.y, 4),
                     "total_weighted_distance": round(report.current_distance, 2)})
    return {"csv": write_summary_csv(rows, d / "facility_location.csv")}


def build_deck(
    report: FacilityLocationReport,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple[str, ...] = (),
    confidence: float = 0.85,
) -> Deliverable:
    """Compose the network-design study: where to put the facility and the travel saved."""
    summary = (
        f"Single-facility location over {report.n_points} demand point(s): the cost-minimizing "
        f"site sits at ({report.optimum.x:,.2f}, {report.optimum.y:,.2f}) near '{report.nearest_point}', "
        f"{report.optimum_distance:,.0f} total weighted distance."
    )

    findings = [
        Finding(
            "Optimal site (Weiszfeld 1-median)",
            f"({report.optimum.x:,.2f}, {report.optimum.y:,.2f}), nearest existing node "
            f"'{report.nearest_point}'; {report.optimum_distance:,.0f} weighted travel.",
            impact="minimizes total load x distance across the network",
        ),
        Finding(
            "Center of gravity (quick estimate)",
            f"({report.cog.x:,.2f}, {report.cog.y:,.2f}); {report.cog_distance:,.0f} weighted travel.",
            impact="simpler to justify and usually close to the optimum",
        ),
    ]
    if report.saving_vs_current is not None:
        findings.append(Finding(
            "Saving vs the current site",
            f"Current site has {report.current_distance:,.0f} weighted travel; the optimum cuts "
            f"{report.saving_vs_current:,.0f} ({(report.saving_pct or 0) * 100:.0f}%).",
            impact="the prize from relocating - weigh against the move cost",
        ))

    kpis = [
        Kpi("Demand points", f"{report.n_points}", rationale="Nodes the facility serves"),
        Kpi("Total load", f"{report.total_weight:,.0f}", rationale="Sum of demand-point weights"),
        Kpi("Optimum weighted distance", f"{report.optimum_distance:,.0f}", target="minimize",
            rationale="Total load x distance at the optimal site"),
    ]
    if report.saving_vs_current is not None:
        kpis.append(Kpi("Saving vs current", f"{report.saving_vs_current:,.0f}", target="maximize",
                        rationale="Weighted-travel reduction from relocating"))

    data_sources = (
        DataSource("Demand points (coordinates + load)", "Customer / store master + volumes", "per network review"),
    )

    recommendations = (
        "Site the facility at the optimum (or the nearest feasible real location to it).",
        "Compare the relocation saving against the one-time move and lease costs before committing.",
        "For multiple facilities, cluster demand first and locate one site per cluster.",
    )

    return Deliverable(
        title="Facility Location (Network Design)",
        client=client,
        summary=summary,
        findings=tuple(findings),
        kpis=tuple(kpis),
        data_sources=data_sources,
        recommendations=recommendations,
        citations=tuple(citations),
        confidence=confidence,
        residual="Single-facility model on straight-line distance: confirm coordinates and loads, and "
                 "that real road distance, land availability and labour don't override the geometric "
                 "optimum. Multi-facility networks need clustering / p-median (a separate step).",
        prepared=prepared,
    )
