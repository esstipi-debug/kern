"""Transportation agent job: a shipments CSV -> cheapest mode per shipment + lane freight.

The data-prep + deck half of the transportation tool. Reads shipments (weight, lane distance,
optional units / order value) with pandas directly (deliberately *not* via jobs/intake.py,
which the parallel loop owns) and, per shipment, picks the cheapest feasible transport mode
(parcel / LTL / FTL / intermodal) from a configurable rate card (``src.logistics``). Rolls up
the mode mix, the saving vs defaulting everything to LTL, the freight cost-to-serve by lane,
and the LTL->FTL breakeven weight. No carrier APIs - the rate card is offline / configurable.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path

import pandas as pd

from src.deliverable import DataSource, Deliverable, Finding, Kpi
from src.export import write_summary_csv
from src.logistics.freight import FreightLine, LaneCost, lane_cost_to_serve
from src.logistics.modes import (
    MODES,
    FreightRates,
    Shipment,
    ltl_ftl_breakeven_kg,
    select_mode,
)

_ID_COLS = ("shipment_id", "shipment", "id", "order_id", "Shipment")
_LANE_COLS = ("lane", "destination", "dest", "route", "customer", "origin_dest", "Lane")
_WEIGHT_COLS = ("weight_kg", "weight", "kg", "gross_weight", "Weight")
_DISTANCE_COLS = ("distance_km", "distance", "km", "lane_km", "Distance")
_UNITS_COLS = ("units", "qty", "quantity", "Units")
_VALUE_COLS = ("order_value", "value", "revenue", "sales", "Order Value")

_RATE_FIELDS = {f.name for f in fields(FreightRates)}


@dataclass(frozen=True)
class ShipmentPlan:
    shipment_id: str
    lane: str
    weight_kg: float
    distance_km: float
    recommended_mode: str
    cost: float
    transit_days: float
    savings_vs_next: float


@dataclass(frozen=True)
class TransportationReport:
    n_shipments: int
    plans: tuple[ShipmentPlan, ...]
    total_recommended_cost: float
    baseline_ltl_cost: float
    total_savings: float                 # baseline_ltl_cost - total_recommended_cost
    mode_mix: dict[str, int]
    lanes: tuple[LaneCost, ...]
    worst_lane: LaneCost | None          # highest freight as a share of value
    breakeven_kg: float                  # LTL -> FTL crossover weight
    summary: str


def _pick_column(df: pd.DataFrame, override: object, candidates: tuple[str, ...]) -> str | None:
    if override:
        return str(override) if str(override) in df.columns else None
    return next((c for c in candidates if c in df.columns), None)


def _num(row: pd.Series, col: str | None, default: float = 0.0) -> float:
    if col is None or pd.isna(row[col]):
        return default
    return float(row[col])


def prepare_records(df: pd.DataFrame, params: dict | None = None) -> dict:
    """Sniff shipment columns, build (Shipment, lane) records + the rate card."""
    params = params or {}
    weight = _pick_column(df, params.get("weight_col"), _WEIGHT_COLS)
    distance = _pick_column(df, params.get("distance_col"), _DISTANCE_COLS)
    missing = [n for n, c in (("weight_kg", weight), ("distance_km", distance)) if c is None]
    if missing:
        cols = list(df.columns)[:10]
        raise ValueError(f"could not find {', '.join(missing)}; pass them in params (columns seen: {cols})")

    sid = _pick_column(df, params.get("id_col"), _ID_COLS)
    lane = _pick_column(df, params.get("lane_col"), _LANE_COLS)
    units = _pick_column(df, params.get("units_col"), _UNITS_COLS)
    value = _pick_column(df, params.get("value_col"), _VALUE_COLS)

    shipments: list[tuple[Shipment, str]] = []
    for i, (_, row) in enumerate(df.iterrows()):
        shipment = Shipment(
            shipment_id=str(row[sid]) if sid else f"S{i + 1}",
            weight_kg=_num(row, weight),
            distance_km=_num(row, distance),
            units=_num(row, units),
            order_value=_num(row, value),
        )
        shipments.append((shipment, str(row[lane]) if lane else "all"))

    overrides = {k: float(v) for k, v in params.items() if k in _RATE_FIELDS}
    max_transit = params.get("max_transit_days")
    return {
        "shipments": shipments,
        "rates": FreightRates(**overrides),
        "max_transit_days": float(max_transit) if max_transit is not None else None,
    }


def prepare(data_path: str, params: dict | None = None) -> dict:
    """Read a shipments CSV and build the transportation payload."""
    return prepare_records(pd.read_csv(data_path), params)


def run(payload: dict) -> TransportationReport:
    """Pick the cheapest feasible mode per shipment and roll up mode mix + lane freight."""
    rates = payload["rates"]
    max_transit = payload["max_transit_days"]
    plans: list[ShipmentPlan] = []
    freight_lines: list[FreightLine] = []
    mode_mix: dict[str, int] = {}
    total_rec = 0.0
    baseline_ltl = 0.0

    for shipment, lane in payload["shipments"]:
        sel = select_mode(shipment, rates, max_transit_days=max_transit)
        ltl_cost = next((q.cost for q in sel.quotes if q.mode == "ltl"), sel.recommended_cost)
        baseline_ltl += ltl_cost
        total_rec += sel.recommended_cost
        mode_mix[sel.recommended_mode] = mode_mix.get(sel.recommended_mode, 0) + 1
        plans.append(ShipmentPlan(
            shipment_id=shipment.shipment_id, lane=lane, weight_kg=shipment.weight_kg,
            distance_km=shipment.distance_km, recommended_mode=sel.recommended_mode,
            cost=sel.recommended_cost, transit_days=sel.transit_days, savings_vs_next=sel.savings_vs_next,
        ))
        freight_lines.append(FreightLine(
            lane=lane, freight_cost=sel.recommended_cost,
            units=shipment.units, order_value=shipment.order_value, weight_kg=shipment.weight_kg,
        ))

    lanes = lane_cost_to_serve(freight_lines)
    worst = None
    if lanes:
        with_value = [lc for lc in lanes if lc.freight_pct_of_value > 0]
        worst = max(with_value, key=lambda lc: lc.freight_pct_of_value) if with_value else lanes[0]

    total_savings = baseline_ltl - total_rec
    mix_str = ", ".join(f"{n} {m}" for m, n in sorted(mode_mix.items(), key=lambda kv: -kv[1]))
    summary = (
        f"Mode plan for {len(plans)} shipment(s): {mix_str}; {total_rec:,.0f} freight, "
        f"{total_savings:,.0f} saved vs all-LTL."
    )
    return TransportationReport(
        n_shipments=len(plans), plans=tuple(plans), total_recommended_cost=total_rec,
        baseline_ltl_cost=baseline_ltl, total_savings=total_savings, mode_mix=mode_mix,
        lanes=tuple(lanes), worst_lane=worst, breakeven_kg=ltl_ftl_breakeven_kg(rates),
        summary=summary,
    )


def verify(report: TransportationReport) -> list[str]:
    """QA gate: shipments present, finite costs, every recommended mode is a known mode."""
    import math

    issues: list[str] = []
    if report.n_shipments <= 0:
        issues.append("no shipments to route")
    if not math.isfinite(report.total_recommended_cost) or report.total_recommended_cost < 0:
        issues.append("total recommended cost is negative or non-finite")
    for p in report.plans:
        if p.recommended_mode not in MODES:
            issues.append(f"{p.shipment_id}: unknown mode {p.recommended_mode}")
        if p.cost < 0 or not math.isfinite(p.cost):
            issues.append(f"{p.shipment_id}: invalid cost")
    return issues


def write_operational(report: TransportationReport, out_dir: str | Path, client: str = "Client") -> dict[str, Path]:
    """The machine-readable deliverable: the per-shipment mode plan."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "shipment_id": p.shipment_id,
            "lane": p.lane,
            "weight_kg": round(p.weight_kg, 1),
            "distance_km": round(p.distance_km, 1),
            "recommended_mode": p.recommended_mode,
            "cost": round(p.cost, 2),
            "transit_days": round(p.transit_days, 1),
            "savings_vs_next": round(p.savings_vs_next, 2),
        }
        for p in report.plans
    ]
    return {"csv": write_summary_csv(rows, d / "transportation.csv")}


def build_deck(
    report: TransportationReport,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple[str, ...] = (),
    confidence: float = 0.85,
) -> Deliverable:
    """Compose the transport-mode study: how to ship each lane and the freight at stake."""
    mix_str = ", ".join(f"{n} {m}" for m, n in sorted(report.mode_mix.items(), key=lambda kv: -kv[1]))
    summary = (
        f"Transport-mode plan over {report.n_shipments} shipment(s): {mix_str}. "
        f"Recommended freight {report.total_recommended_cost:,.0f}, "
        f"{report.total_savings:,.0f} saved vs defaulting to LTL."
    )

    findings = [
        Finding(
            "Mode mix and saving vs all-LTL",
            f"Cheapest feasible mode per shipment: {mix_str}. Freight "
            f"{report.total_recommended_cost:,.0f} vs {report.baseline_ltl_cost:,.0f} all-LTL.",
            impact=f"{report.total_savings:,.0f} freight saved by routing each shipment to its best mode",
        ),
        Finding(
            "LTL -> FTL breakeven",
            f"Above ~{report.breakeven_kg:,.0f} kg a full truckload beats LTL on the same lane.",
            impact="consolidate LTL volume past this weight to switch to FTL",
        ),
    ]
    if report.worst_lane is not None and report.worst_lane.freight_pct_of_value > 0:
        wl = report.worst_lane
        findings.append(Finding(
            f"Costliest lane to serve: {wl.lane}",
            f"Freight is {wl.freight_pct_of_value * 100:.0f}% of order value "
            f"({wl.total_freight:,.0f} across {wl.shipments} shipment(s)).",
            impact="re-price, consolidate, or re-source this lane first",
        ))

    kpis = [
        Kpi("Shipments", f"{report.n_shipments}", rationale="Shipments routed"),
        Kpi("Recommended freight", f"{report.total_recommended_cost:,.0f}", target="minimize",
            rationale="Total cost at the cheapest feasible mode per shipment"),
        Kpi("Saving vs all-LTL", f"{report.total_savings:,.0f}", target="maximize",
            rationale="Freight saved vs defaulting every shipment to LTL"),
        Kpi("LTL->FTL breakeven", f"{report.breakeven_kg:,.0f} kg", target="-",
            rationale="Weight at which a full truck beats LTL"),
    ]
    if report.worst_lane is not None and report.worst_lane.freight_pct_of_value > 0:
        kpis.append(Kpi("Worst lane freight % of value",
                        f"{report.worst_lane.freight_pct_of_value * 100:.0f}%", target="minimize",
                        rationale="Highest freight burden relative to order value"))

    data_sources = (
        DataSource("Shipments (weight, lane distance, units, value)", "TMS / order + freight records", "per shipment"),
        DataSource("Freight rate card (parcel/LTL/FTL/intermodal)", "Carrier contracts / tariff (configurable)", "per contract"),
    )

    recommendations = (
        "Route each shipment to its recommended mode - the plan CSV lists them.",
        "Consolidate small LTL shipments on dense lanes past the FTL breakeven weight.",
        "Set transit-time rules so only time-sensitive lanes pay for the faster modes.",
    )

    return Deliverable(
        title="Transport-Mode Selection & Lane Freight",
        client=client,
        summary=summary,
        findings=tuple(findings),
        kpis=tuple(kpis),
        data_sources=data_sources,
        recommendations=recommendations,
        citations=tuple(citations),
        confidence=confidence,
        residual="Offline rate card: confirm the parcel/LTL/FTL/intermodal rates, weight caps and "
                 "lane distances against your carrier contracts before booking; live, lane-specific "
                 "quotes require a carrier API (a deferred, credentialed connector).",
        prepared=prepared,
    )
