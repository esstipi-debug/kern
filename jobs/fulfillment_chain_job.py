"""Fulfillment-chain job: read a timestamp export, scan dwell, refuse inverted clocks.

Pandas-only prepare on this file's own CSV -- not ``jobs.intake``, which is shaped
for demand series. Long form (one row per event) is canonical; wide form
(one column per hito) is accepted and normalised to events. An empty wide cell
is an absent hop, never a zero dwell.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from jobs.intake import _norm
from nosilo_core.guided import verify_guided
from src.connectors import COVERAGE_MEASURED, COVERAGE_UNAVAILABLE, FieldCoverage
from src.connectors.milestones import (
    MILESTONE_IDS,
    MilestoneCoverage,
    MilestoneEvent,
)
from src.deliverable import DataSource, Deliverable, Finding, Kpi, not_measured
from src.export import write_summary_csv
from src.fulfillment_milestones import ChainScan, scan_chain

MILESTONE_ALIASES: dict[str, tuple[str, ...]] = {
    "order_created": (
        "ordercreated", "createdat", "heurdt", "fechacreacion", "order_creation",
        "ordercreationts",
    ),
    "released_to_warehouse": (
        "releasedtowarehouse", "released", "transmit", "enviadocd", "fechaliberacion",
    ),
    "pack_end": ("packend", "packingend", "finpacking", "packdone", "packing_end"),
    "load_end": ("loadend", "loaded", "fincarga", "loaddone", "load_end"),
    "carrier_pickup": (
        "carrierpickup", "pickup", "puata", "retirotransportista", "pickupat",
    ),
    "arrived": (
        "arrived", "storearrival", "fechallegadatienda", "arrivedon", "store_arrival",
    ),
}

_LONG_MARKERS = ("milestoneid", "hito", "milestone", "event")
_ORDER_ALIASES = ("orderid", "ordernumber", "pedido", "plnord")
_WHEN_ALIASES = ("occurredat", "timestamp", "fechahora", "eventtime", "occurred_at")
_SOURCE_ALIASES = ("sourcesystem", "sistema", "source")
_PROMISED_ALIASES = (
    "promiseddate", "fechaprometida", "requesteddeliverydate", "promised_date",
)


@dataclass(frozen=True)
class PreparedChain:
    events: tuple[MilestoneEvent, ...]
    promised: dict[str, str]
    coverage: MilestoneCoverage
    source_name: str


def _match_alias(norm: str, aliases: tuple[str, ...]) -> bool:
    compact = aliases + tuple(a.replace("_", "") for a in aliases)
    return norm in compact or norm in aliases


def _resolve_long_columns(df: pd.DataFrame) -> dict[str, str] | None:
    found: dict[str, str] = {}
    for header in df.columns:
        norm = _norm(header)
        if "order" not in found and _match_alias(norm, _ORDER_ALIASES):
            found["order"] = header
        elif "milestone" not in found and norm in _LONG_MARKERS:
            found["milestone"] = header
        elif "when" not in found and _match_alias(norm, _WHEN_ALIASES):
            found["when"] = header
        elif "source" not in found and _match_alias(norm, _SOURCE_ALIASES):
            found["source"] = header
        elif "promised" not in found and _match_alias(norm, _PROMISED_ALIASES):
            found["promised"] = header
    if "milestone" in found and "order" in found and "when" in found:
        return found
    return None


def _map_milestone_token(raw: str) -> str | None:
    norm = _norm(raw)
    if norm in MILESTONE_IDS:
        return norm
    for mid, aliases in MILESTONE_ALIASES.items():
        if norm == mid.replace("_", "") or norm in aliases or _match_alias(norm, aliases):
            return mid
    return None


def _coverage_from_present(backend: str, present: set[str], sources: dict[str, str]) -> MilestoneCoverage:
    def field(mid: str) -> FieldCoverage:
        if mid in present:
            return FieldCoverage(
                COVERAGE_MEASURED,
                sources.get(mid, mid),
                f"column mapped to {mid} in {backend}",
            )
        return FieldCoverage(
            COVERAGE_UNAVAILABLE, "", f"{backend} has no {mid} timestamp",
        )

    return MilestoneCoverage(
        backend=backend,
        order_created=field("order_created"),
        released_to_warehouse=field("released_to_warehouse"),
        pack_end=field("pack_end"),
        load_end=field("load_end"),
        carrier_pickup=field("carrier_pickup"),
        arrived=field("arrived"),
    )


def _cell_text(value: object) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if pd.isna(value):
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "nat", "none"}:
        return None
    return text


def _iso_from_value(value: object) -> str | None:
    text = _cell_text(value)
    if text is None:
        return None
    parsed = pd.to_datetime(text, errors="coerce", format="mixed")
    if pd.isna(parsed):
        return None
    ts = pd.Timestamp(parsed)
    raw_has_time = "T" in text.upper() or ":" in text
    if raw_has_time:
        return ts.strftime("%Y-%m-%dT%H:%M:%S")
    return ts.strftime("%Y-%m-%d")


def prepare(data_path: str, params: dict | None = None) -> PreparedChain:
    path = Path(data_path)
    if not path.exists():
        raise FileNotFoundError(f"{data_path} does not exist")
    params = params or {}
    df = pd.read_excel(path) if path.suffix.lower() in {".xlsx", ".xls"} else pd.read_csv(path)
    if df.empty:
        raise ValueError("the fulfillment-events file has no rows")
    default_source = str(params.get("source_system") or path.name).strip() or path.name
    long_cols = _resolve_long_columns(df)
    if long_cols:
        events, promised, present, sources, unknown = _from_long(df, long_cols, default_source)
        if unknown:
            raise ValueError(
                "unknown milestone_id value(s): " + ", ".join(sorted(unknown))
                + ". Map them to one of: " + ", ".join(MILESTONE_IDS)
            )
    else:
        events, promised, present, sources = _from_wide(df, default_source)
    if not events:
        raise ValueError(
            "no usable fulfillment events: every timestamp was empty or unparseable"
        )
    coverage = _coverage_from_present(default_source, present, sources)
    return PreparedChain(tuple(events), promised, coverage, default_source)


def _from_long(
    df: pd.DataFrame, cols: dict[str, str], default_source: str,
) -> tuple[list[MilestoneEvent], dict[str, str], set[str], dict[str, str], set[str]]:
    events: list[MilestoneEvent] = []
    promised: dict[str, str] = {}
    present: set[str] = set()
    sources: dict[str, str] = {}
    unknown: set[str] = set()
    for _, row in df.iterrows():
        oid = _cell_text(row[cols["order"]])
        token = _cell_text(row[cols["milestone"]])
        when = _iso_from_value(row[cols["when"]])
        if not oid or not token:
            continue
        mid = _map_milestone_token(token)
        if mid is None:
            unknown.add(token)
            continue
        if when is None:
            continue
        src = default_source
        if "source" in cols:
            src = _cell_text(row[cols["source"]]) or default_source
        events.append(MilestoneEvent(oid, mid, when, src))
        present.add(mid)
        sources.setdefault(mid, cols["milestone"])
        if "promised" in cols:
            due = _iso_from_value(row[cols["promised"]])
            if due:
                promised[oid] = due[:10]
    return events, promised, present, sources, unknown


def _from_wide(
    df: pd.DataFrame, default_source: str,
) -> tuple[list[MilestoneEvent], dict[str, str], set[str], dict[str, str]]:
    order_col = None
    promised_col = None
    milestone_cols: dict[str, str] = {}
    for header in df.columns:
        norm = _norm(header)
        if order_col is None and _match_alias(norm, _ORDER_ALIASES):
            order_col = header
            continue
        if promised_col is None and _match_alias(norm, _PROMISED_ALIASES):
            promised_col = header
            continue
        mapped = _map_milestone_token(header)
        if mapped:
            milestone_cols[mapped] = header
    if order_col is None:
        raise ValueError(
            "missing order_id. A wide export needs one row per order and a column "
            "per milestone; a long export needs order_id, milestone_id, occurred_at."
        )
    if not milestone_cols:
        raise ValueError(
            "no milestone columns found. Name them order_created, pack_end, load_end, "
            "carrier_pickup, arrived (or the documented aliases)."
        )
    events: list[MilestoneEvent] = []
    promised: dict[str, str] = {}
    present: set[str] = set()
    sources: dict[str, str] = {}
    for _, row in df.iterrows():
        oid = _cell_text(row[order_col])
        if not oid:
            continue
        if promised_col is not None:
            due = _iso_from_value(row[promised_col])
            if due:
                promised[oid] = due[:10]
        for mid, header in milestone_cols.items():
            when = _iso_from_value(row[header])
            if when is None:
                continue
            events.append(MilestoneEvent(oid, mid, when, default_source))
            present.add(mid)
            sources[mid] = header
    return events, promised, present, sources


def run(payload: PreparedChain, params: dict | None = None) -> ChainScan:
    del params
    return scan_chain(
        payload.events, promised=payload.promised, coverage=payload.coverage,
    )


def verify(report: ChainScan) -> list[str]:
    issues = list(verify_guided(report.outcome))
    if report.sequence_violations:
        sample = report.sequence_violations[0]
        issues.append(
            f"{len(report.sequence_violations)} sequence inversion(s); "
            f"e.g. order {sample.order_id} has {sample.later_id} at {sample.later_at} "
            f"before {sample.earlier_id} at {sample.earlier_at}"
        )
    if report.n_events == 0:
        issues.append("no events survived prepare")
    if report.measurable_hops == 0 and getattr(report.outcome, "status", "") == "EXECUTED":
        issues.append("EXECUTED with no measurable hop presents an empty chain as healthy")
    return issues


def write_operational(
    report: ChainScan, out_dir: str | Path, client: str = "Client"
) -> dict[str, Path]:
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    rows = []
    for hop in report.hops:
        rows.append({
            "from_id": hop.from_id,
            "to_id": hop.to_id,
            "n_orders": hop.n_orders,
            "n_missing": hop.n_missing,
            "unit": hop.unit,
            "p50": "" if hop.p50 is None else round(hop.p50, 4),
            "mixed_clock": "yes" if hop.mixed_clock else "",
        })
    written = {"csv": write_summary_csv(rows, d / "fulfillment_chain.csv")}
    written.update(build_deck(report, client=client).write_all(d))
    return written


def build_deck(
    report: ChainScan,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple[str, ...] = (),
    confidence: float = 0.6,
    lang: str = "en",
) -> Deliverable:
    findings: list[Finding] = []
    measured = [h for h in report.hops if h.p50 is not None]
    if measured:
        slowest = max(measured, key=lambda h: h.p50_seconds or 0)
        findings.append(Finding(
            f"Longest measured hop: {slowest.from_id} -> {slowest.to_id}",
            f"Median {slowest.p50:.2f} {slowest.unit} over {slowest.n_orders} order(s).",
            impact="this is dwell, not blame; a definition pack is required to name an owner",
        ))
    missing = [h for h in report.hops if h.p50 is None]
    if missing:
        names = ", ".join(f"{h.from_id}->{h.to_id}" for h in missing)
        findings.append(Finding(
            "Hops with no number",
            f"{names}. An empty cell is a missing clock, not a zero dwell.",
            impact="instrument those ends before treating the chain as explained",
        ))
    if report.uninstrumented_late is None:
        findings.append(Finding(
            "Lateness not scored",
            "No promised date travelled with the events, so the report does not talk about delay.",
            impact="add promised_date to score uninstrumented-late orders",
        ))
    elif report.uninstrumented_late_scored:
        findings.append(Finding(
            "Late arrivals with holes in the clock",
            f"{report.uninstrumented_late} of {report.uninstrumented_late_scored} "
            "late arrival(s) are missing at least one canonical hop.",
            impact="that share is unexplained by instrumentation, not attributed to a team",
        ))
    if report.mixed_clock_caveat:
        findings.append(Finding(
            "Mixed date and datetime clocks",
            report.mixed_clock_caveat,
            impact="export ISO datetimes if dock-hour dwell matters",
        ))

    kpis = (
        Kpi("Orders scanned", f"{report.n_orders}", rationale="Distinct order_id values with at least one event"),
        Kpi("Complete chains", f"{report.complete_chain_n}",
            rationale="Orders that carry all six canonical milestones"),
        Kpi("Warehouse/transport cut present", f"{report.warehouse_transport_cut_n}",
            rationale="Orders with both pack_end and carrier_pickup -- still not a blame split"),
        Kpi("Measurable hops", f"{report.measurable_hops}", target="5",
            rationale="Canonical adjacencies with both ends"),
        Kpi(
            "Uninstrumented late",
            not_measured(lang) if report.uninstrumented_late is None
            else f"{report.uninstrumented_late}",
            rationale="Late vs promise with at least one hop missing; None when no promise",
        ),
    )
    data_sources = [
        DataSource("Fulfillment events", report.coverage.backend, "per run"),
    ]
    for mid, cov in report.coverage.fields().items():
        data_sources.append(DataSource(
            f"Milestone {mid}",
            f"{cov.status}: {cov.means}" + (f" (source {cov.source})" if cov.source else ""),
            "per run",
        ))
    return Deliverable(
        title="Fulfillment Chain - Measured Dwell per Hop",
        client=client,
        summary=report.summary,
        findings=tuple(findings),
        kpis=kpis,
        data_sources=tuple(data_sources),
        recommendations=(
            "Do not treat dwell as a root cause. Sign a definition pack before attributing delay.",
            "Fill empty hops from WMS/TMS; do not skip to the next present timestamp.",
            "Keep promised_date beside arrived if you want the uninstrumented-late share.",
        ),
        citations=citations,
        confidence=confidence,
        residual=(
            "Dwell is a clock, not a cause. Empty hops are missing timestamps, never zero."
        ),
        prepared=prepared,
        options=tuple(getattr(report.outcome, "options", ()) or ()),
        lang=lang,
    )
