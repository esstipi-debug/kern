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

from src.flow_map import (
    MIN_OBSERVATIONS,
    UNMEASURABLE,
    FlowReport,
    Movement,
    analyze,
)
from src.guided import verify_guided

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

    departed = pd.to_datetime(df[cols["fecha_salida"]], errors="coerce")
    arrived = pd.to_datetime(df[cols["fecha_llegada"]], errors="coerce")
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
        cost = 0.0
        if cost_col is not None:
            parsed = pd.to_numeric(df[cost_col].iloc[i], errors="coerce")
            cost = 0.0 if pd.isna(parsed) else float(parsed)
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
