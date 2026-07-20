"""Multi-facility network-design agent job: a nodes CSV -> which p sites to open.

The data-prep + deck half of the p-median network-design tool. Reads a single CSV of network
nodes with pandas directly (deliberately not via jobs/intake.py, which the parallel loop owns)
and solves the p-median problem via src.network_design: choose which p candidate sites to open
and assign each demand point to one, minimizing total weighted travel, then quantify the saving
against the best single-facility (p=1) baseline.

The CSV carries demand points and candidate sites. An optional `role` column
(demand / candidate) separates them; with no role column every row is BOTH a demand point and a
candidate site (candidate set = the demand nodes, the classic p-median setup).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from src.deliverable import DataSource, Deliverable, Finding, Kpi
from src.export import write_summary_csv
from src.facility_location import DemandPoint
from src.network_design import CandidateSite, NetworkDesign, solve_p_median

_NAME_COLS = ("name", "location", "city", "node", "point", "site", "label")
_X_COLS = ("x", "lon", "longitude", "x_coord", "easting")
_Y_COLS = ("y", "lat", "latitude", "y_coord", "northing")
_WEIGHT_COLS = ("weight", "demand", "volume", "load", "units", "tons")
_ROLE_COLS = ("role", "type", "kind", "node_type")
_FIXED_COST_COLS = ("fixed_cost", "fixed", "open_cost", "annual_cost")
_CAPACITY_COLS = ("capacity", "cap", "throughput", "max_load")

_DEMAND_ROLES = {"demand", "customer", "store", "d"}
_CANDIDATE_ROLES = {"candidate", "site", "facility", "dc", "warehouse", "c"}


@dataclass(frozen=True)
class NetworkDesignReport:
    n_demand: int
    n_sites: int
    p: int
    feasible: bool
    open_sites: tuple[str, ...]
    total_weighted_distance: float
    total_fixed_cost: float
    baseline_distance: float
    saving_vs_baseline: float
    saving_pct: float
    assignment: dict[str, str]
    summary: str


def _pick_column(df: pd.DataFrame, override: object, candidates: tuple[str, ...]) -> str | None:
    if override:
        return str(override) if str(override) in df.columns else None
    return next((c for c in candidates if c in df.columns), None)


def _row_roles(role_val: str) -> tuple[bool, bool]:
    """Whether a row counts as a demand point and/or a candidate site given its role label.

    An unrecognized role label counts as BOTH, so a typo never silently drops a node.
    """
    is_demand = role_val in _DEMAND_ROLES
    is_site = role_val in _CANDIDATE_ROLES
    if not is_demand and not is_site:
        is_demand = is_site = True
    return is_demand, is_site


def _build_nodes(
    df: pd.DataFrame, cols: dict[str, str | None]
) -> tuple[list[DemandPoint], list[CandidateSite]]:
    """Walk every row and split it into a demand point and/or a candidate site per its role."""
    name, x, y = cols["name"], cols["x"], cols["y"]
    weight, role, fixed, capacity = cols["weight"], cols["role"], cols["fixed"], cols["capacity"]
    demands: list[DemandPoint] = []
    sites: list[CandidateSite] = []
    for i, (_, row) in enumerate(df.iterrows()):
        label = str(row[name]) if name else f"N{i + 1}"
        px, py = float(row[x]), float(row[y])
        role_val = str(row[role]).strip().lower() if role and pd.notna(row[role]) else ""
        is_demand, is_site = _row_roles(role_val) if role else (True, True)
        if is_demand:
            w = float(row[weight]) if weight and pd.notna(row[weight]) else 1.0
            demands.append(DemandPoint(name=label, x=px, y=py, weight=w))
        if is_site:
            fc = float(row[fixed]) if fixed and pd.notna(row[fixed]) else 0.0
            cap = float(row[capacity]) if capacity and pd.notna(row[capacity]) else None
            sites.append(CandidateSite(name=label, x=px, y=py, fixed_cost=fc, capacity=cap))
    return demands, sites


def prepare_records(df: pd.DataFrame, params: dict | None = None) -> dict:
    """Sniff coordinate / load / role columns, build demand points + candidate sites + p."""
    params = params or {}
    x = _pick_column(df, params.get("x_col"), _X_COLS)
    y = _pick_column(df, params.get("y_col"), _Y_COLS)
    missing = [n for n, c in (("x", x), ("y", y)) if c is None]
    if missing:
        cols = list(df.columns)[:10]
        raise ValueError(
            f"could not find {', '.join(missing)}; pass them in params (columns seen: {cols})"
        )

    cols = {
        "name": _pick_column(df, params.get("name_col"), _NAME_COLS),
        "x": x, "y": y,
        "weight": _pick_column(df, params.get("weight_col"), _WEIGHT_COLS),
        "role": _pick_column(df, params.get("role_col"), _ROLE_COLS),
        "fixed": _pick_column(df, params.get("fixed_cost_col"), _FIXED_COST_COLS),
        "capacity": _pick_column(df, params.get("capacity_col"), _CAPACITY_COLS),
    }
    demands, sites = _build_nodes(df, cols)

    n_sites = len(sites)
    default_p = 2 if n_sites >= 2 else 1
    raw_p = params.get("p", params.get("facilities", params.get("num_facilities", default_p)))
    p = int(raw_p)
    if n_sites:
        p = max(1, min(p, n_sites))
    return {"demands": demands, "sites": sites, "p": p}


def prepare(data_path: str, params: dict | None = None) -> dict:
    """Read a network-nodes CSV and build the p-median payload."""
    return prepare_records(pd.read_csv(data_path), params)


def _summarize(design: NetworkDesign, demands: list[DemandPoint], sites: list[CandidateSite], p: int) -> str:
    """Human-readable one-liner for the solved (or infeasible) network."""
    if design.feasible:
        return (
            f"p-median over {len(demands)} demand point(s) and {len(sites)} candidate site(s): "
            f"open {p} site(s) [{', '.join(design.open_sites)}], "
            f"{design.total_weighted_distance:,.0f} total weighted distance, "
            f"{design.saving_vs_baseline:,.0f} less than the best single facility "
            f"({design.saving_pct * 100:.0f}% saving)."
        )
    return (
        f"p-median over {len(demands)} demand point(s) and {len(sites)} candidate site(s) with "
        f"p={p} is infeasible: the capacities are too tight to serve all demand from {p} site(s)."
    )


def run(payload: dict) -> NetworkDesignReport:
    """Solve the p-median network and quantify the saving vs a single facility."""
    demands: list[DemandPoint] = payload["demands"]
    sites: list[CandidateSite] = payload["sites"]
    p: int = payload["p"]
    design = solve_p_median(demands, sites, p)
    summary = _summarize(design, demands, sites, p)
    return NetworkDesignReport(
        n_demand=len(demands), n_sites=len(sites), p=p, feasible=design.feasible,
        open_sites=design.open_sites, total_weighted_distance=design.total_weighted_distance,
        total_fixed_cost=design.total_fixed_cost, baseline_distance=design.baseline_distance,
        saving_vs_baseline=design.saving_vs_baseline, saving_pct=design.saving_pct,
        assignment=design.assignment, summary=summary,
    )


def verify(report: NetworkDesignReport) -> list[str]:
    """QA gate: nodes present, a feasible network, exactly p open sites, every demand assigned."""
    issues: list[str] = []
    if report.n_demand <= 0:
        issues.append("no demand points to serve")
    if report.n_sites <= 0:
        issues.append("no candidate sites to choose from")
    if not report.feasible:
        issues.append(f"no feasible network opens {report.p} site(s) under the given capacities")
        return issues
    if len(report.open_sites) != report.p:
        issues.append(f"expected {report.p} open site(s), got {len(report.open_sites)}")
    if not math.isfinite(report.total_weighted_distance) or report.total_weighted_distance < 0:
        issues.append("total weighted distance is negative or non-finite")
    if len(report.assignment) != report.n_demand:
        issues.append("not every demand point was assigned to an open site")
    return issues


def write_operational(
    report: NetworkDesignReport, out_dir: str | Path, client: str = "Client"
) -> dict[str, Path]:
    """The machine-readable deliverable: which open site serves each demand point."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    rows = [
        {"demand_point": name, "assigned_site": site}
        for name, site in sorted(report.assignment.items())
    ]
    return {"csv": write_summary_csv(rows, d / "network_design.csv")}


def _deck_findings(report: NetworkDesignReport) -> list[Finding]:
    """The 2-3 headline findings: which sites, the saving vs one DC, and fixed cost if any."""
    findings = [
        Finding(
            f"Open {report.p} site(s)",
            f"[{', '.join(report.open_sites)}]; {report.total_weighted_distance:,.0f} total weighted travel.",
            impact="minimizes total load x distance across the whole network",
        ),
        Finding(
            "Saving vs a single facility",
            f"A single DC would incur {report.baseline_distance:,.0f} weighted travel; opening "
            f"{report.p} cuts {report.saving_vs_baseline:,.0f} ({report.saving_pct * 100:.0f}%).",
            impact="the prize from a multi-facility network - weigh against fixed + running cost",
        ),
    ]
    if report.total_fixed_cost > 0:
        findings.append(Finding(
            "Fixed cost of the opened sites",
            f"{report.total_fixed_cost:,.0f} in fixed cost across the {report.p} opened site(s).",
            impact="netted into the objective when comparing network configurations",
        ))
    return findings


def _deck_kpis(report: NetworkDesignReport) -> list[Kpi]:
    """The headline KPI table for the network-design deck."""
    return [
        Kpi("Demand points", f"{report.n_demand}", rationale="Nodes the network serves"),
        Kpi("Candidate sites", f"{report.n_sites}", rationale="Sites the model could open"),
        Kpi("Facilities opened", f"{report.p}", rationale="p in the p-median model"),
        Kpi("Total weighted distance", f"{report.total_weighted_distance:,.0f}", target="minimize",
            rationale="Total load x distance at the chosen network"),
        Kpi("Saving vs single facility", f"{report.saving_vs_baseline:,.0f}", target="maximize",
            rationale="Weighted-travel reduction from opening p sites instead of one"),
    ]


def build_deck(
    report: NetworkDesignReport,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple[str, ...] = (),
    confidence: float = 0.85,
) -> Deliverable:
    """Compose the multi-facility network-design study: which p sites to open and the travel saved."""
    summary = (
        f"p-median network design over {report.n_demand} demand point(s) and {report.n_sites} "
        f"candidate site(s): open {report.p} site(s) [{', '.join(report.open_sites)}], "
        f"{report.total_weighted_distance:,.0f} total weighted travel, "
        f"{report.saving_pct * 100:.0f}% less than the best single facility."
    )

    data_sources = (
        DataSource("Demand points (coordinates + load)", "Customer / store master + volumes", "per network review"),
        DataSource("Candidate sites (coordinates + fixed cost / capacity)", "Real-estate / DC option list", "per network review"),
    )

    recommendations = (
        "Open the p sites the model selected (or the nearest feasible real locations to them).",
        "Weigh the weighted-travel saving against the fixed and running cost of each extra facility.",
        "Confirm capacities and that real road distance, land and labour don't override the geometric optimum.",
    )

    return Deliverable(
        title="Network Design (Multi-Facility p-Median)",
        client=client,
        summary=summary,
        findings=tuple(_deck_findings(report)),
        kpis=tuple(_deck_kpis(report)),
        data_sources=data_sources,
        recommendations=recommendations,
        citations=tuple(citations),
        confidence=confidence,
        residual="p-median on straight-line distance with single-source assignment: confirm the "
                 "candidate coordinates, loads, capacities and fixed costs, and that road distance and "
                 "site availability match. Splitting a demand point across sites, or road / time "
                 "distance, needs a richer model.",
        prepared=prepared,
    )
