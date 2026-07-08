"""Vehicle-routing agent job: a stops CSV + depot/capacity -> the better of two route plans.

The data-prep + deck half of the vehicle-routing tool (Ballou Cap. 7). Reads stops (coordinates
+ demand, optional service time / time window) with pandas directly (deliberately *not* via
jobs/intake.py, which the parallel loop owns) and runs both classic construction heuristics via
``src.logistics.routing`` - Clarke-Wright savings and the sweep algorithm - recommending whichever
yields the lower total distance, against a naive one-truck-per-stop baseline.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from src.deliverable import DataSource, Deliverable, Finding, Kpi
from src.export import write_summary_csv
from src.logistics.routing import (
    Depot,
    Route,
    RoutingPlan,
    Stop,
    clarke_wright_savings,
    naive_distance,
    sweep,
)

_ID_COLS = ("stop_id", "stop", "id", "name", "location", "customer", "node", "point", "label")
_X_COLS = ("x", "lon", "longitude", "x_coord", "easting")
_Y_COLS = ("y", "lat", "latitude", "y_coord", "northing")
_DEMAND_COLS = ("demand", "qty", "quantity", "units", "load", "weight")
_SERVICE_COLS = ("service_time", "service_minutes", "dwell_time")
_TW_START_COLS = ("tw_start", "time_window_start", "window_start")
_TW_END_COLS = ("tw_end", "time_window_end", "window_end")


@dataclass(frozen=True)
class VehicleRoutingReport:
    n_stops: int
    capacity: float
    naive_total_distance: float
    savings_plan: RoutingPlan
    sweep_plan: RoutingPlan
    recommended_method: str
    recommended_distance: float
    recommended_routes: tuple[Route, ...]
    savings_vs_naive: float
    late_stops: tuple[str, ...]
    summary: str


def _pick_column(df: pd.DataFrame, override: object, candidates: tuple[str, ...]) -> str | None:
    if override:
        return str(override) if str(override) in df.columns else None
    return next((c for c in candidates if c in df.columns), None)


def prepare_records(df: pd.DataFrame, params: dict | None = None) -> dict:
    """Sniff stop columns, build Stop records + the depot/capacity/speed payload."""
    params = params or {}
    x = _pick_column(df, params.get("x_col"), _X_COLS)
    y = _pick_column(df, params.get("y_col"), _Y_COLS)
    missing = [n for n, c in (("x", x), ("y", y)) if c is None]
    if missing:
        cols = list(df.columns)[:10]
        raise ValueError(f"could not find {', '.join(missing)}; pass them in params (columns seen: {cols})")

    capacity = params.get("capacity")
    if capacity is None:
        raise ValueError("a vehicle capacity is required (params.capacity - max demand units per truck)")

    sid = _pick_column(df, params.get("id_col"), _ID_COLS)
    demand = _pick_column(df, params.get("demand_col"), _DEMAND_COLS)
    service = _pick_column(df, params.get("service_col"), _SERVICE_COLS)
    tw_start = _pick_column(df, params.get("tw_start_col"), _TW_START_COLS)
    tw_end = _pick_column(df, params.get("tw_end_col"), _TW_END_COLS)

    def _num(row: pd.Series, col: str | None, default: float = 0.0) -> float:
        if col is None or pd.isna(row[col]):
            return default
        return float(row[col])

    def _opt_num(row: pd.Series, col: str | None) -> float | None:
        if col is None or pd.isna(row[col]):
            return None
        return float(row[col])

    stops = [
        Stop(
            stop_id=str(row[sid]) if sid else f"S{i + 1}",
            x=float(row[x]), y=float(row[y]),
            demand=_num(row, demand, 1.0),
            service_time=_num(row, service, 0.0),
            tw_start=_opt_num(row, tw_start),
            tw_end=_opt_num(row, tw_end),
        )
        for i, (_, row) in enumerate(df.iterrows())
    ]
    ids_seen = [s.stop_id for s in stops]
    dupes = sorted({sid for sid in ids_seen if ids_seen.count(sid) > 1})
    if dupes:
        raise ValueError(f"duplicate stop id(s), each stop must have a unique id: {', '.join(dupes)}")
    depot = Depot(float(params.get("depot_x", 0.0)), float(params.get("depot_y", 0.0)))
    return {
        "stops": stops, "depot": depot, "capacity": float(capacity),
        "speed": float(params.get("speed", 1.0)),
    }


def prepare(data_path: str, params: dict | None = None) -> dict:
    """Read a stops CSV and build the vehicle-routing payload."""
    return prepare_records(pd.read_csv(data_path), params)


def run(payload: dict) -> VehicleRoutingReport:
    """Run savings + sweep, recommend the lower-distance plan, and roll up the comparison."""
    stops: list[Stop] = payload["stops"]
    depot: Depot = payload["depot"]
    capacity: float = payload["capacity"]
    speed: float = payload["speed"]

    savings_plan = clarke_wright_savings(stops, depot, capacity, speed=speed)
    sweep_plan = sweep(stops, depot, capacity, speed=speed)
    naive = naive_distance(stops, depot)

    best = savings_plan if savings_plan.total_distance <= sweep_plan.total_distance else sweep_plan
    late = tuple(sorted({sid for r in best.routes for sid in r.late_stops}))
    saving = naive - best.total_distance

    summary = (
        f"Vehicle routing over {len(stops)} stop(s): {best.method} plan uses {best.n_vehicles} "
        f"route(s), {best.total_distance:,.1f} total distance ({saving:,.1f} saved vs one truck "
        f"per stop)."
    )
    if late:
        summary += f" {len(late)} stop(s) miss their time window."

    return VehicleRoutingReport(
        n_stops=len(stops), capacity=capacity, naive_total_distance=naive,
        savings_plan=savings_plan, sweep_plan=sweep_plan,
        recommended_method=best.method, recommended_distance=best.total_distance,
        recommended_routes=best.routes, savings_vs_naive=saving, late_stops=late,
        summary=summary,
    )


def verify(report: VehicleRoutingReport) -> list[str]:
    """QA gate: every stop covered exactly once, no route over capacity, finite distances."""
    issues: list[str] = []
    if report.n_stops <= 0:
        issues.append("no stops to route")
    if not math.isfinite(report.recommended_distance) or report.recommended_distance < 0:
        issues.append("recommended distance is negative or non-finite")

    seen: dict[str, int] = {}
    for r in report.recommended_routes:
        if r.load > report.capacity + 1e-6:
            issues.append(f"route {r.stop_ids}: load {r.load} exceeds capacity {report.capacity}")
        if not math.isfinite(r.distance) or r.distance < 0:
            issues.append(f"route {r.stop_ids}: invalid distance")
        for sid in r.stop_ids:
            seen[sid] = seen.get(sid, 0) + 1
    duplicates = [sid for sid, n in seen.items() if n > 1]
    if duplicates:
        issues.append(f"stop(s) appear on more than one route: {', '.join(duplicates)}")
    if len(seen) != report.n_stops:
        issues.append(
            f"expected {report.n_stops} stop(s) covered, found {len(seen)} distinct stop id(s) "
            "across the recommended routes"
        )
    return issues


def write_operational(report: VehicleRoutingReport, out_dir: str | Path, client: str = "Client") -> dict[str, Path]:
    """The machine-readable deliverable: the recommended route/stop sequence."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "route": route_idx + 1,
            "sequence": seq + 1,
            "stop_id": sid,
            "arrival": round(route.arrivals[seq], 2) if route.arrivals else None,
            "late": sid in route.late_stops,
        }
        for route_idx, route in enumerate(report.recommended_routes)
        for seq, sid in enumerate(route.stop_ids)
    ]
    return {"csv": write_summary_csv(rows, d / "vehicle_routing.csv")}


def build_deck(
    report: VehicleRoutingReport,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple[str, ...] = (),
    confidence: float = 0.85,
) -> Deliverable:
    """Compose the routing study: how to route the fleet and the distance saved."""
    other = report.sweep_plan if report.recommended_method == "savings" else report.savings_plan
    summary = (
        f"Vehicle routing over {report.n_stops} stop(s): the {report.recommended_method} plan uses "
        f"{len(report.recommended_routes)} route(s) for {report.recommended_distance:,.1f} total "
        f"distance, {report.savings_vs_naive:,.1f} less than one truck per stop."
    )

    findings = [
        Finding(
            f"Recommended: {report.recommended_method} plan",
            f"{len(report.recommended_routes)} route(s), {report.recommended_distance:,.1f} total "
            f"distance vs {other.total_distance:,.1f} for the {other.method} plan.",
            impact=f"{report.savings_vs_naive:,.1f} distance saved vs a dedicated truck per stop",
        ),
    ]
    if report.late_stops:
        findings.append(Finding(
            "Stops missing their time window",
            f"{len(report.late_stops)} stop(s) arrive after their window on the recommended plan: "
            f"{', '.join(report.late_stops)}.",
            impact="re-sequence, add a vehicle, or renegotiate the window for these stops",
        ))

    kpis = [
        Kpi("Stops", f"{report.n_stops}", rationale="Stops routed"),
        Kpi("Vehicles used", f"{len(report.recommended_routes)}", target="minimize",
            rationale="Routes in the recommended plan"),
        Kpi("Total distance", f"{report.recommended_distance:,.1f}", target="minimize",
            rationale="Recommended plan's total travel distance"),
        Kpi("Saved vs one truck per stop", f"{report.savings_vs_naive:,.1f}", target="maximize",
            rationale="Distance saved by consolidating stops onto shared routes"),
    ]
    if report.late_stops:
        kpis.append(Kpi("Stops missing their window", f"{len(report.late_stops)}", target="minimize",
                        rationale="Stops arriving after their time window on the recommended plan"))

    data_sources = (
        DataSource("Stops (coordinates, demand, optional service time/time window)",
                    "Order / delivery master", "per routing run"),
        DataSource("Depot location and vehicle capacity", "Fleet / operations input (params)", "per run"),
    )

    recommendations = (
        f"Adopt the {report.recommended_method} plan - the route CSV lists each vehicle's stop sequence.",
        "Compare against the sweep/savings alternative when it is close - simplicity of explanation "
        "to drivers can outweigh a small distance difference.",
        "Resolve any stops missing their time window before dispatch (re-sequence, add a vehicle, "
        "or renegotiate the window).",
    )

    return Deliverable(
        title="Vehicle Routing & Scheduling",
        client=client,
        summary=summary,
        findings=tuple(findings),
        kpis=tuple(kpis),
        data_sources=data_sources,
        recommendations=recommendations,
        citations=tuple(citations),
        confidence=confidence,
        residual="Straight-line (Euclidean) distance from a single depot: confirm against real road "
                 "distance/time before dispatch. Time-window handling is a lightweight feasibility "
                 "check (arrival-time accumulation + late-arrival flagging), not a full VRPTW solver.",
        prepared=prepared,
    )
