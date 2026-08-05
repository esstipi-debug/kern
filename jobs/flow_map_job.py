"""End-to-end flow map job -- intake, QA gate, deliverables.

Reads a movements export (one row per shipment leg) with pandas directly, the
same way ``jobs/disruption_scan_job.py`` reads a supplier list: the canonical
demand intake in ``jobs/intake.py`` is shaped for date/product/quantity series,
not for origin-destination legs.

The gate refuses to dress up thin data: if most volume rides on lanes with too
few observations, or the verdict leans on one of them, nothing ships.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
from nosilo_core.guided import verify_guided

from src.deliverable import DataSource, Deliverable, Finding, Kpi
from src.export import write_summary_csv
from src.flow_map import (
    MEASURED,
    MIN_OBSERVATIONS,
    UNMEASURABLE,
    FlowReport,
    Movement,
    analyze,
)

MAX_UNMEASURABLE_SHARE = 0.40
MAX_NEGATIVE_TRANSIT_SHARE = 0.01
MIN_EDGES = 2
MAX_DROPPED_SHARE = 0.10

COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "fecha_salida": ("fechasalida", "shipdate", "departuredate", "fechadespacho", "dispatchdate"),
    "fecha_llegada": ("fechallegada", "receiptdate", "arrivaldate", "fecharecepcion", "deliverydate"),
    "origen": ("origen", "origin", "from", "fromlocation", "source", "desde"),
    "destino": ("destino", "destination", "to", "tolocation", "hacia"),
    "producto": ("producto", "product", "productid", "sku", "item"),
    "cantidad": ("cantidad", "quantity", "qty", "units", "volume"),
    # MINOR (2026-07-31 flow_map fix wave): bare "cost" and "price" are ambiguous --
    # a client export where that column is a LINE TOTAL (unit_cost * quantity), not a
    # per-unit cost, is silently mis-valued (every buffer_value inflated by ~quantity).
    # Kept anyway: rejecting these two common header names would turn most real client
    # exports cost-free (CRITICAL 2 above), which is worse than a possible mis-scaling.
    "costo_unitario": ("costounitario", "unitcost", "cost", "unitprice", "price"),
}
_REQUIRED = ("fecha_salida", "fecha_llegada", "origen", "destino", "producto", "cantidad")


def _norm(header: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(header).lower())


def _resolve_columns(df: pd.DataFrame) -> dict[str, str]:
    """Canonical field -> the client's own column name. Raises naming what is missing."""
    found: dict[str, str] = {}
    for header in df.columns:
        norm = _norm(header)
        for canonical, aliases in COLUMN_ALIASES.items():
            if canonical not in found and norm in aliases:
                found[canonical] = header
                break
    missing = [c for c in _REQUIRED if c not in found]
    if missing:
        raise ValueError(
            "missing required column(s): " + ", ".join(missing)
            + ". A movements export needs one row per shipment leg with a departure date, "
            "an arrival date, an origin, a destination, a product and a quantity."
        )
    return found


def prepare(data_path: str, params: dict | None = None) -> list[Movement]:
    """Load the movements file and turn each row into a Movement with measured transit."""
    path = Path(data_path)
    if not path.exists():
        raise FileNotFoundError(f"{data_path} does not exist")
    df = pd.read_excel(path) if path.suffix.lower() in {".xlsx", ".xls"} else pd.read_csv(path)
    if df.empty:
        raise ValueError("the movements file has no rows")
    cols = _resolve_columns(df)

    # format="mixed": client movement files carry heterogeneous date formats
    # (row-by-row), so this states that intent explicitly rather than letting
    # pandas warn about falling back to per-element dateutil parsing.
    # RISK (MINOR, 2026-07-31 flow_map fix wave): format="mixed" parses each row with
    # dateutil's per-element guesser, which is ambiguous whenever both day and month
    # are <=12. A column mixing "13/01/2026" (unambiguous day-first) with "05/03/2026"
    # (which dateutil will read month-first, i.e. May 3rd, not March 5th) parses each
    # row under a DIFFERENT convention with no error or warning, silently shifting
    # transit_days for the ambiguous rows. This is not auto-detected or corrected here
    # -- only made visible, so the next reader knows to ask the client for an
    # unambiguous export (ISO 8601) rather than trust transit_days blindly.
    departed = pd.to_datetime(df[cols["fecha_salida"]], errors="coerce", format="mixed")
    arrived = pd.to_datetime(df[cols["fecha_llegada"]], errors="coerce", format="mixed")
    cost_col = cols.get("costo_unitario")

    total_rows = len(df)
    dropped_date = 0
    dropped_quantity = 0
    movements: list[Movement] = []
    for i in range(len(df)):
        if pd.isna(departed.iloc[i]) or pd.isna(arrived.iloc[i]):
            dropped_date += 1
            continue
        quantity = pd.to_numeric(df[cols["cantidad"]].iloc[i], errors="coerce")
        if pd.isna(quantity):
            dropped_quantity += 1
            continue
        # None means "no cost recorded for this row" (the column was absent, or this
        # cell was blank/unparseable) -- distinct from a genuinely recorded 0.0 (a real
        # zero-cost transfer). Collapsing both to 0.0 is exactly the CRITICAL 2 defect
        # this fix wave closes: a missing cost must never ship to the client as a value.
        cost: float | None = None
        if cost_col is not None:
            parsed = pd.to_numeric(df[cost_col].iloc[i], errors="coerce")
            cost = None if pd.isna(parsed) else float(parsed)
        movements.append(Movement(
            origin=str(df[cols["origen"]].iloc[i]).strip(),
            destination=str(df[cols["destino"]].iloc[i]).strip(),
            product=str(df[cols["producto"]].iloc[i]).strip(),
            quantity=float(quantity),
            transit_days=(arrived.iloc[i] - departed.iloc[i]).total_seconds() / 86400.0,
            unit_cost=cost,
            departed_day=departed.iloc[i].toordinal(),
        ))
    if not movements:
        raise ValueError("no usable movements: every row was missing a date or a quantity")

    # Precedence: the all-dropped case above always fires first (it is the more
    # specific failure -- nothing at all survived). This threshold check only
    # runs once at least one movement did survive, so a file that is mostly
    # garbage does not silently produce a confident report from the remainder.
    dropped = dropped_date + dropped_quantity
    dropped_share = dropped / total_rows if total_rows else 0.0
    if dropped_share > MAX_DROPPED_SHARE:
        raise ValueError(
            f"{dropped} of {total_rows} row(s) ({dropped_share * 100:.0f}%) were dropped "
            f"(limit {MAX_DROPPED_SHARE * 100:.0f}%): {dropped_date} row(s) had a missing or "
            f"unparseable fecha_salida/fecha_llegada, {dropped_quantity} row(s) had a missing or "
            "unparseable cantidad. Fix those columns in the source file and re-run."
        )
    return movements


def calendar_span(movements: list[Movement]) -> float:
    """Days from the first departure to the last arrival, floored at 1."""
    first = min(m.departed_day for m in movements)
    last = max(m.departed_day + m.transit_days for m in movements)
    return max(last - first, 1.0)


def run(movements: list[Movement], params: dict | None = None) -> FlowReport:
    """Analyse the movements. The calendar span is derived from the movements."""
    params = params or {}
    span = float(params.get("span_days") or calendar_span(movements))
    return analyze(
        movements,
        min_observations=int(params.get("min_observations", MIN_OBSERVATIONS)),
        cycle_service_level=float(params.get("service_level", 0.95)),
        span_days=span,
    )


def verify(report: FlowReport) -> list[str]:
    """QA gate: four conditions. A gate that thin data can pass is not a gate."""
    issues = list(verify_guided(report.outcome))
    if report.unmeasurable_volume_share > MAX_UNMEASURABLE_SHARE:
        issues.append(
            f"{report.unmeasurable_volume_share * 100:.0f}% of volume rides on unmeasurable "
            f"lanes (limit {MAX_UNMEASURABLE_SHARE * 100:.0f}%)"
        )
    if report.n_movements and (
        report.n_negative_transit / report.n_movements > MAX_NEGATIVE_TRANSIT_SHARE
    ):
        issues.append(
            f"{report.n_negative_transit} movement(s) record arrival before departure "
            f"(limit {MAX_NEGATIVE_TRANSIT_SHARE * 100:.0f}% of rows)"
        )
    if len(report.edges) < MIN_EDGES:
        issues.append(f"the graph has fewer than two lanes ({len(report.edges)})")
    # Invariant guard, not a reachable-in-practice condition: src.flow_map._buffers
    # only ever ranks MEASURED edges, so today these two sets are always disjoint.
    # This can only fire if that invariant is ever broken upstream in the engine.
    ranked = {(b.origin, b.destination) for b in report.buffers[:3]}
    unmeasurable = {
        (e.origin, e.destination) for e in report.edges if e.status == UNMEASURABLE
    }
    if ranked & unmeasurable:
        issues.append("a ranked option leans on an unmeasurable lane")
    return issues


def write_operational(
    report: FlowReport, out_dir: str | Path, client: str = "Client"
) -> dict[str, Path]:
    """One row per lane plus the self-contained map. Unmeasurable cells stay empty."""
    from src.flow_svg import render_page

    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    buffer_by_edge = {(b.origin, b.destination): b for b in report.buffers}
    rows = []
    for e in report.edges:
        b = buffer_by_edge.get((e.origin, e.destination))
        rows.append({
            "origin": e.origin,
            "destination": e.destination,
            "echelon_origin": report.graph.echelon.get(e.origin, ""),
            "echelon_destination": report.graph.echelon.get(e.destination, ""),
            "movements": e.n,
            "volume": round(e.volume, 2),
            "status": e.status,
            "mean_days": "" if e.mean_days is None else round(e.mean_days, 2),
            "stdev_days": "" if e.stdev_days is None else round(e.stdev_days, 2),
            "p50_days": "" if e.p50_days is None else round(e.p50_days, 2),
            "p90_days": "" if e.p90_days is None else round(e.p90_days, 2),
            "buffer_units": "" if b is None else round(b.buffer_units, 2),
            # b.buffer_value is None when the lane has no recorded unit cost (CRITICAL 2
            # of the 2026-07-31 fix wave): an empty cell, never a printed 0.
            "buffer_value": "" if b is None or b.buffer_value is None else round(b.buffer_value, 2),
            "bidirectional": "yes" if e.bidirectional else "",
        })
    html_path = d / "flow_map.html"
    html_path.write_text(render_page(report, client=client), encoding="utf-8")
    return {"csv": write_summary_csv(rows, d / "flow_map.csv"), "html": html_path}


def build_deck(
    report: FlowReport,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple[str, ...] = (),
    confidence: float = 0.6,
    lang: str = "en",
) -> Deliverable:
    """The client-facing map: which lane locks up capital, and what we could not measure."""
    findings: list[Finding] = []
    if report.buffers:
        top = report.buffers[0]
        edge = next(
            e for e in report.edges
            if (e.origin, e.destination) == (top.origin, top.destination)
        )
        findings.append(Finding(
            f"Transit variability on {top.origin} -> {top.destination} locks up the most capital",
            f"Measured over {edge.n} movement(s): {edge.mean_days:.1f} d average transit, "
            f"{edge.stdev_days:.1f} d standard deviation, p90 {edge.p90_days:.1f} d. That spread "
            f"forces roughly {top.buffer_units:,.0f} unit(s) of protective stock downstream.",
            impact="stabilising this lane releases the buffer it forces",
        ))
        for b in report.buffers[1:3]:
            findings.append(Finding(
                f"Next lane: {b.origin} -> {b.destination}",
                f"Forces roughly {b.buffer_units:,.0f} unit(s) of protective stock.",
                impact="queue behind the top lane",
            ))
    else:
        findings.append(Finding(
            "No lane has enough observations to measure",
            f"Every one of the {len(report.edges)} lane(s) fell below the "
            f"{MIN_OBSERVATIONS}-movement threshold, so no transit statistic is reported.",
            impact="widen the export window before treating any lane as stable",
        ))

    unmeasurable = [e for e in report.edges if e.status == UNMEASURABLE]
    if unmeasurable:
        names = ", ".join(f"{e.origin} -> {e.destination}" for e in unmeasurable[:5])
        findings.append(Finding(
            "Lanes we could not measure",
            f"{len(unmeasurable)} lane(s) carry fewer than {MIN_OBSERVATIONS} observed "
            f"movement(s) ({names}). They are drawn on the map but report no transit "
            "number and are excluded from the ranking.",
            impact="an empty cell is a declared gap, not a zero",
        ))
    if report.graph.cycle_note:
        findings.append(Finding(
            "Bidirectional flow in the network",
            report.graph.cycle_note,
            impact="confirm whether these legs are intended or are re-handling",
        ))

    measured = sum(1 for e in report.edges if e.status == MEASURED)
    kpis = (
        Kpi("Nodes mapped", f"{len(report.graph.nodes)}", rationale="Distinct origins and destinations"),
        Kpi("Lanes mapped", f"{len(report.edges)}", rationale="Distinct origin-destination pairs"),
        Kpi("Lanes measurable", f"{measured}", target=f"{len(report.edges)}",
            rationale=f"Lanes with at least {MIN_OBSERVATIONS} observed movements"),
        Kpi("Volume on unmeasurable lanes",
            f"{report.unmeasurable_volume_share * 100:.0f}%", target="0%",
            rationale="Share of volume with no transit statistic"),
        Kpi("Movements read", f"{report.n_movements}", rationale="Rows that carried both dates and a quantity"),
    )
    data_sources = (
        DataSource("Movements export (origin / destination / dates / quantity)", "client-supplied", "per run"),
    )
    recommendations = [
        "Stabilise the top-ranked lane before adding stock to absorb it.",
        "Widen the export window for any lane reported as unmeasurable, then re-run.",
        "Feed the measured per-lane transit spread into the safety-stock review; it replaces an assumption.",
    ]
    return Deliverable(
        title="Flow Map - Observed Network and Measured Transit",
        client=client,
        summary=report.summary,
        findings=tuple(findings),
        kpis=kpis,
        data_sources=data_sources,
        recommendations=tuple(recommendations),
        options=tuple(report.outcome.options),
        citations=tuple(citations),
        confidence=confidence,
        residual=(
            "Transit is measured from the movement dates the client recorded, not from carrier "
            "telemetry: a late-keyed receipt inflates the spread. The map shows the network that "
            "actually moved goods in the export window, which may omit seasonal lanes."
        ),
        prepared=prepared,
        lang=lang,
    )
