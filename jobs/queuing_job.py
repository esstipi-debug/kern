"""Queuing / waiting-line agent job: a service-points CSV -> cost-optimal staffing.

The data-prep + deck half of the queuing tool. Reads service stations (arrival rate, service
rate, and the cost of waiting vs. a server) with pandas directly (deliberately *not* via
jobs/intake.py), sizes each station to the cost-optimal number of servers via ``src.queuing``
(M/M/c + the staffing trade-off), and rolls up the staffing cost, busiest station and worst
wait. For dock doors, pick stations, packing lines, returns desks, support/repair queues.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from src.deliverable import DataSource, Deliverable, Finding, Kpi
from src.export import write_summary_csv
from src.queuing import optimize_servers

_STATION_COLS = ("station", "name", "id", "Station", "node", "resource")
_ARRIVAL_COLS = ("arrival_rate", "arrivals", "lambda", "Arrival Rate", "demand_rate")
_SERVICE_COLS = ("service_rate", "service", "mu", "Service Rate", "rate")
_WAIT_COST_COLS = ("wait_cost", "waiting_cost", "Wait Cost", "downtime_cost")
_SERVER_COST_COLS = ("server_cost", "labor_cost", "Server Cost", "staffing_cost")


@dataclass(frozen=True)
class StationPlan:
    station: str
    arrival_rate: float
    service_rate: float
    recommended_servers: int
    utilization: float
    wq: float                 # average wait in line at the recommended staffing
    total_cost: float


@dataclass(frozen=True)
class QueuingReport:
    stations: tuple[StationPlan, ...]      # sorted by utilization desc
    n_stations: int
    total_cost: float
    busiest_station: str
    max_wait: float


def _pick_column(df: pd.DataFrame, override: object, candidates: tuple[str, ...]) -> str | None:
    if override:
        return str(override) if str(override) in df.columns else None
    return next((c for c in candidates if c in df.columns), None)


def prepare_records(df: pd.DataFrame, params: dict | None = None) -> list[dict]:
    """Sniff the station columns and build one record per service point."""
    params = params or {}
    station = _pick_column(df, params.get("station_col"), _STATION_COLS)
    arrival = _pick_column(df, params.get("arrival_col"), _ARRIVAL_COLS)
    service = _pick_column(df, params.get("service_col"), _SERVICE_COLS)
    missing = [n for n, c in (("station", station), ("arrival_rate", arrival), ("service_rate", service)) if c is None]
    if missing:
        cols = list(df.columns)[:10]
        raise ValueError(f"could not find {', '.join(missing)}; pass them in params (columns seen: {cols})")
    wait_c = _pick_column(df, params.get("wait_cost_col"), _WAIT_COST_COLS)
    server_c = _pick_column(df, params.get("server_cost_col"), _SERVER_COST_COLS)

    def _num(row, col):
        return float(row[col]) if col and pd.notna(row[col]) else None

    return [
        {
            "station": str(row[station]),
            "arrival_rate": float(row[arrival]),
            "service_rate": float(row[service]),
            "wait_cost": _num(row, wait_c),
            "server_cost": _num(row, server_c),
        }
        for _, row in df.iterrows()
    ]


def prepare(data_path: str, params: dict | None = None) -> list[dict]:
    """Read a stations CSV and build the queuing records."""
    return prepare_records(pd.read_csv(data_path), params)


def run(
    records: list[dict],
    *,
    wait_cost: float = 10.0,
    server_cost: float = 5.0,
    max_servers: int = 30,
) -> QueuingReport:
    """Size each station to the cost-optimal server count and roll up the portfolio."""
    plans: list[StationPlan] = []
    for r in records:
        wc = r["wait_cost"] if r["wait_cost"] is not None else wait_cost
        sc = r["server_cost"] if r["server_cost"] is not None else server_cost
        choices = optimize_servers(
            r["arrival_rate"], r["service_rate"],
            wait_cost_per_unit_time=wc, server_cost_per_unit_time=sc, max_servers=max_servers,
        )
        if not choices:
            raise ValueError(f"station {r['station']!r} needs more than {max_servers} servers to be stable")
        best = choices[0]
        plans.append(StationPlan(
            station=r["station"], arrival_rate=r["arrival_rate"], service_rate=r["service_rate"],
            recommended_servers=best.servers, utilization=best.metrics.utilization,
            wq=best.metrics.wq, total_cost=best.total_cost,
        ))
    plans.sort(key=lambda p: p.utilization, reverse=True)
    busiest = plans[0] if plans else None
    return QueuingReport(
        stations=tuple(plans),
        n_stations=len(plans),
        total_cost=sum(p.total_cost for p in plans),
        busiest_station=busiest.station if busiest else "n/a",
        max_wait=max((p.wq for p in plans), default=0.0),
    )


def verify(report: QueuingReport) -> list[str]:
    """QA gate: stations sized and each is stable with at least one server."""
    issues: list[str] = []
    if report.n_stations <= 0:
        issues.append("no service stations to size")
    for p in report.stations:
        if p.recommended_servers < 1 or p.utilization >= 1.0:
            issues.append(f"station {p.station} is not stably staffed")
    return issues


def write_operational(report: QueuingReport, out_dir: str | Path, client: str = "Client") -> dict[str, Path]:
    """The machine-readable deliverable: per-station recommended staffing + wait."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "station": p.station,
            "arrival_rate": round(p.arrival_rate, 3),
            "service_rate": round(p.service_rate, 3),
            "recommended_servers": p.recommended_servers,
            "utilization": round(p.utilization, 3),
            "avg_wait": round(p.wq, 3),
            "total_cost": round(p.total_cost, 2),
        }
        for p in report.stations
    ]
    return {"csv": write_summary_csv(rows, d / "queuing_staffing.csv")}


def build_deck(
    report: QueuingReport,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple[str, ...] = (),
    confidence: float = 0.85,
) -> Deliverable:
    """Compose the staffing study: how many servers each station needs, and where it hurts."""
    summary = (
        f"Sized staffing for {report.n_stations} service point(s): {report.total_cost:,.0f} total "
        f"cost; busiest is '{report.busiest_station}', worst average wait {report.max_wait:.2f}."
    )
    findings = [
        Finding(
            f"Busiest station: {report.busiest_station}",
            "Highest utilization in the network; the recommended staffing keeps it stable.",
            impact="watch this point first; it sets the service experience",
        ),
        Finding(
            "Cost-optimal staffing balances wait against labour",
            f"Each station's server count minimizes (cost of units in system) + (server cost); "
            f"portfolio total {report.total_cost:,.0f}.",
            impact="add a server only where the wait cost beats the labour cost",
        ),
    ]
    top = report.stations[0] if report.stations else None
    if top is not None:
        findings.append(Finding(
            f"Recommended servers at {top.station}: {top.recommended_servers}",
            f"Utilization {top.utilization * 100:.0f}%, average wait {top.wq:.2f}.",
            impact="staff to this to hold the wait without overspending",
        ))
    kpis = (
        Kpi("Service points", str(report.n_stations), rationale="Stations sized"),
        Kpi("Total staffing cost", f"{report.total_cost:,.0f}", target="minimize",
            rationale="In-system cost + server cost across the network"),
        Kpi("Busiest station", report.busiest_station, rationale="Highest utilization"),
        Kpi("Worst average wait", f"{report.max_wait:.2f}", target="minimize",
            rationale="Longest queue wait at the recommended staffing"),
    )
    data_sources = (
        DataSource("Service points (arrival rate / service rate / wait + server cost)", "ops + labour data", "per run"),
        DataSource("Queuing model (M/M/c)", "src.queuing", "deterministic"),
    )
    recommendations = [
        "Staff each station to its cost-optimal server count.",
        f"Prioritize '{report.busiest_station}' - it is the binding constraint on service.",
        "Re-run as arrival or service rates shift (e.g. peak vs off-peak).",
    ]
    return Deliverable(
        title="Queuing / Staffing Study",
        client=client,
        summary=summary,
        findings=tuple(findings),
        kpis=kpis,
        data_sources=data_sources,
        recommendations=tuple(recommendations),
        citations=tuple(citations),
        confidence=confidence,
        residual="Arrival/service rates are averages - confirm the variability (peaks) before "
                 "committing rosters; the G/G/c case widens the wait.",
        prepared=prepared,
    )
