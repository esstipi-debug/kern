"""Flow map engine -- the observed supply network, measured.

Pure and dependency-light (stdlib + scipy's normal quantile via
``src.safety_stock.service_level_factor``): it takes movements the client
actually recorded and returns the network they imply, the transit statistics of
every leg, and the capital that each leg's variability forces downstream.

Deliberately I/O-free, like the other analytical cores: ``jobs/flow_map_job.py``
owns file reading, the QA gate and the deliverables.

An edge with too few observations is reported as UNMEASURABLE with empty
statistics -- never as zero. A declared hole is an honest result; an assumed
transit time is not.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from nosilo_core.guided import (
    ExecutionOption,
    GuidedOutcome,
    HandoffPacket,
    Residual,
    as_handoff,
    as_options,
)

from src.safety_stock import service_level_factor

MEASURED = "measured"
UNMEASURABLE = "unmeasurable"


@dataclass(frozen=True)
class Movement:
    """One observed shipment leg. ``transit_days`` may be negative (bad data).

    ``departed_day`` is the departure date as a day ordinal. It exists so the
    calendar span the movements cover is derivable from the movements themselves,
    with no side channel between the job's prepare and run stages.
    """

    origin: str
    destination: str
    product: str
    quantity: float
    transit_days: float
    # None means the cost column was absent/unparseable for this row -- distinct from
    # a genuinely recorded 0.0 (a legitimate zero-cost transfer). See EdgeStat.mean_unit_cost.
    unit_cost: float | None = None
    departed_day: float = 0.0


@dataclass(frozen=True)
class FlowGraph:
    """The network implied by the movements. ``echelon`` is depth from a source."""

    nodes: tuple[str, ...]
    echelon: dict[str, int] = field(default_factory=dict)
    edges: tuple[tuple[str, str], ...] = ()
    bidirectional: tuple[tuple[str, str], ...] = ()
    cycle_note: str = ""


def _edge_volume(movements: list[Movement]) -> dict[tuple[str, str], float]:
    volume: dict[tuple[str, str], float] = defaultdict(float)
    for m in movements:
        volume[(m.origin, m.destination)] += m.quantity
    return dict(volume)


def _bidirectional_pairs(
    volume: dict[tuple[str, str], float],
) -> tuple[tuple[tuple[str, str], ...], set[tuple[str, str]]]:
    """Find A<->B pairs; return the pairs (sorted, canonical) and the weaker legs."""
    pairs: list[tuple[str, str]] = []
    weaker: set[tuple[str, str]] = set()
    for (a, b) in sorted(volume):
        if a >= b or (b, a) not in volume:
            continue
        pairs.append((a, b))
        if volume[(a, b)] != volume[(b, a)]:
            weaker.add((a, b) if volume[(a, b)] < volume[(b, a)] else (b, a))
    return tuple(pairs), weaker


def build_graph(movements: list[Movement]) -> FlowGraph:
    """Nodes, sorted edges, topological echelons, and an explicit cycle note."""
    volume = _edge_volume(movements)
    edges = tuple(sorted(volume))
    nodes_set = {n for edge in edges for n in edge}
    pairs, weaker = _bidirectional_pairs(volume)

    acyclic = [e for e in edges if e not in weaker]
    indegree = {n: 0 for n in nodes_set}
    downstream: dict[str, list[str]] = defaultdict(list)
    for origin, destination in acyclic:
        indegree[destination] += 1
        downstream[origin].append(destination)

    sources = sorted(n for n in nodes_set if indegree[n] == 0)
    notes: list[str] = []
    if pairs:
        legs = ", ".join(f"{a} <-> {b}" for a, b in pairs)
        note = f"bidirectional flow detected ({legs})"
        # Per-pair, not all-or-nothing: a graph can mix an equal-volume 2-cycle (no leg
        # ignored) with an unequal-volume one (lower-volume leg ignored) in the same
        # note, so name exactly which pair(s) had a leg ignored rather than a blanket
        # clause that is wrong for every pair whenever even one pair is equal-volume.
        if weaker:
            ignored = ", ".join(f"{a} <-> {b}" for a, b in sorted(weaker))
            note += f"; the lower-volume leg is ignored when computing echelons for {ignored}"
        notes.append(note)

    echelon = {n: 0 for n in nodes_set}
    if nodes_set and not sources:
        notes.append("no source node (every node has inbound flow); echelons flattened to 0")
    else:
        frontier = list(sources)
        seen = set(sources)
        depth = 0
        while frontier:
            depth += 1
            nxt: list[str] = []
            for node in frontier:
                for child in sorted(downstream[node]):
                    if child in seen:
                        continue
                    echelon[child] = depth
                    seen.add(child)
                    nxt.append(child)
            frontier = sorted(nxt)

    nodes = tuple(sorted(nodes_set, key=lambda n: (echelon[n], n)))
    return FlowGraph(
        nodes=nodes,
        echelon=echelon,
        edges=edges,
        bidirectional=pairs,
        cycle_note="; ".join(notes),
    )


MIN_OBSERVATIONS = 8  # below this an edge reports no transit statistics at all


@dataclass(frozen=True)
class EdgeStat:
    """One leg's observed transit behaviour. Statistics are None when UNMEASURABLE."""

    origin: str
    destination: str
    n: int
    volume: float
    status: str
    mean_days: float | None = None
    stdev_days: float | None = None
    p50_days: float | None = None
    p90_days: float | None = None
    # None when no row on this edge carried a cost -- distinct from a measured 0.0
    # (a genuinely free/zero-cost transfer). See _buffers and jobs/flow_map_job.py.
    mean_unit_cost: float | None = None
    bidirectional: bool = False


def _percentile(values: list[float], q: float) -> float:
    """Linear-interpolation percentile on a sorted list (numpy's default method)."""
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _stdev(values: list[float], mean: float) -> float:
    """Sample standard deviation (n-1). Callers guarantee len(values) >= 2."""
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return variance**0.5


def measure_edges(
    movements: list[Movement],
    *,
    min_observations: int = MIN_OBSERVATIONS,
    bidirectional: tuple[tuple[str, str], ...] = (),
) -> tuple[EdgeStat, ...]:
    """Transit statistics per edge, sorted by (origin, destination)."""
    grouped: dict[tuple[str, str], list[Movement]] = defaultdict(list)
    for m in movements:
        grouped[(m.origin, m.destination)].append(m)

    two_way = {p for a, b in bidirectional for p in ((a, b), (b, a))}
    stats: list[EdgeStat] = []
    for (origin, destination) in sorted(grouped):
        rows = grouped[(origin, destination)]
        days = [r.transit_days for r in rows]
        volume = sum(r.quantity for r in rows)
        # A row's cost is present iff unit_cost is not None -- a genuinely recorded 0.0
        # (a real zero-cost transfer) must count as present, not be mistaken for absent.
        cost_rows = [r for r in rows if r.unit_cost is not None]
        mean_cost = sum(r.unit_cost for r in cost_rows) / len(cost_rows) if cost_rows else None
        is_two_way = (origin, destination) in two_way
        if len(days) < min_observations or len(days) < 2:
            stats.append(EdgeStat(
                origin=origin, destination=destination, n=len(days), volume=volume,
                status=UNMEASURABLE, mean_unit_cost=mean_cost, bidirectional=is_two_way,
            ))
            continue
        mean = sum(days) / len(days)
        stats.append(EdgeStat(
            origin=origin, destination=destination, n=len(days), volume=volume,
            status=MEASURED, mean_days=mean, stdev_days=_stdev(days, mean),
            p50_days=_percentile(days, 0.5), p90_days=_percentile(days, 0.9),
            mean_unit_cost=mean_cost, bidirectional=is_two_way,
        ))
    return tuple(stats)


_TOP_OPTIONS = 3


@dataclass(frozen=True)
class EdgeBuffer:
    """Protective inventory an edge's transit variability forces downstream."""

    origin: str
    destination: str
    buffer_units: float
    # None when the lane has no observed unit cost (see EdgeStat.mean_unit_cost) --
    # never a 0.0 standing in for "we don't know", which reads to a client as "worthless".
    buffer_value: float | None


@dataclass(frozen=True)
class FlowReport:
    """Everything the job layer needs: the network, the measurement, the verdict."""

    graph: FlowGraph
    edges: tuple[EdgeStat, ...]
    buffers: tuple[EdgeBuffer, ...]
    n_movements: int
    n_negative_transit: int
    unmeasurable_volume_share: float
    span_days: float
    outcome: GuidedOutcome
    summary: str


def _buffers(
    edges: tuple[EdgeStat, ...], *, z: float, span_days: float
) -> tuple[EdgeBuffer, ...]:
    """z * mean_daily_demand * sigma_LT, valued at the edge's mean unit cost.

    Only the lead-time-variability term of the combined safety-stock formula:
    ``src.safety_stock.safety_stock`` computes the DEMAND-variability term, which
    an edge's transit noise does not drive. Only ``service_level_factor`` is reused.
    """
    out: list[EdgeBuffer] = []
    for e in edges:
        if e.status != MEASURED or e.stdev_days is None:
            continue
        units = z * (e.volume / span_days) * e.stdev_days
        value = None if e.mean_unit_cost is None else units * e.mean_unit_cost
        out.append(EdgeBuffer(
            origin=e.origin, destination=e.destination,
            buffer_units=units, buffer_value=value,
        ))
    # Ranking rule: lanes with a known value are ranked first (highest value first);
    # lanes with no cost data sort after them, ranked by units instead since value is
    # unavailable. Ties within each group break on units, then on (origin, destination)
    # for a total, deterministic order -- never comparing None to a float.
    return tuple(sorted(
        out,
        key=lambda b: (
            b.buffer_value is None,
            -b.buffer_value if b.buffer_value is not None else 0.0,
            -b.buffer_units,
            b.origin,
            b.destination,
        ),
    ))


def _options(buffers: tuple[EdgeBuffer, ...], edges: tuple[EdgeStat, ...]) -> GuidedOutcome:
    by_edge = {(e.origin, e.destination): e for e in edges}

    if not buffers:
        packet = HandoffPacket(
            title="Widen the export window and re-run flow_map",
            steps=[
                "No lane has the minimum observed movements to measure transit variance.",
                "Widen the movement export's date range (or lower min_observations) so more "
                "lanes clear the threshold.",
                "Re-run flow_map analyze on the wider export.",
            ],
            risk_if_skipped=(
                "No protective-inventory verdict can be produced; capital allocation across "
                "lanes stays a guess."
            ),
        )
        residual = Residual(
            description=(
                "No lane cleared the observation threshold, so no ranking could be formed."
            ),
            risk_if_skipped="Protective inventory decisions proceed without any measurement.",
        )
        return as_handoff(
            "No lane has enough observations to rank; widen the export window and re-run.",
            [packet],
            residuals=[residual],
        )

    options: list[ExecutionOption] = []
    for rank, b in enumerate(buffers[:_TOP_OPTIONS]):
        e = by_edge[(b.origin, b.destination)]
        # No cost was recorded for this lane -- omit the value clause entirely rather
        # than print a 0, which would read as "this buffer is worthless" instead of
        # "we don't know its value."
        value_clause = (
            f" ({b.buffer_value:,.0f} in value)" if b.buffer_value is not None else ""
        )
        options.append(ExecutionOption(
            label=f"Stabilise {b.origin} -> {b.destination}",
            summary=(
                f"Transit averages {e.mean_days:.1f} d with a {e.stdev_days:.1f} d spread "
                f"over {e.n} observed movement(s); that spread alone forces "
                f"{b.buffer_units:,.0f} unit(s) of protective stock{value_clause} downstream."
            ),
            score=b.buffer_value if b.buffer_value is not None else 0.0,
            recommended=rank == 0,
            action=f"review_lane:{b.origin}->{b.destination}",
            tradeoffs=(
                "Cutting transit variability on this lane releases the buffer; cutting the "
                "average transit time does not, on its own, release any of it."
            ),
        ))
    residual = Residual(
        description=(
            "Why the lane varies (carrier, customs, dock congestion, batching) is not in the "
            "movement data; confirm the cause with the lane owner before acting."
        ),
        risk_if_skipped="Acting on the wrong root cause leaves the buffer in place.",
    )
    return as_options(
        "Three lanes ranked by the protective inventory their transit variance forces.",
        options,
        residuals=[residual],
    )


def analyze(
    movements: list[Movement],
    *,
    min_observations: int = MIN_OBSERVATIONS,
    cycle_service_level: float = 0.95,
    span_days: float = 0.0,
) -> FlowReport:
    """Build the graph, measure every edge, and rank the capital each lane locks up."""
    if span_days <= 0:
        raise ValueError("span_days must be > 0 (the calendar span the movements cover)")
    graph = build_graph(movements)
    edges = measure_edges(
        movements, min_observations=min_observations, bidirectional=graph.bidirectional
    )
    total_volume = sum(e.volume for e in edges)
    unmeasurable_volume = sum(e.volume for e in edges if e.status == UNMEASURABLE)
    share = unmeasurable_volume / total_volume if total_volume > 0 else 0.0
    buffers = _buffers(
        edges, z=service_level_factor(cycle_service_level), span_days=span_days
    )
    outcome = _options(buffers, edges)
    n_measured = sum(1 for e in edges if e.status == MEASURED)
    top = (
        f"the {buffers[0].origin} -> {buffers[0].destination} lane locks up the most capital"
        if buffers else "no lane has enough observations to rank"
    )
    summary = (
        f"Mapped {len(graph.nodes)} node(s) and {len(edges)} lane(s) from "
        f"{len(movements)} movement(s); {n_measured} lane(s) measurable, "
        f"{share * 100:.0f}% of volume on unmeasurable lanes; {top}."
    )
    return FlowReport(
        graph=graph, edges=edges, buffers=buffers, n_movements=len(movements),
        n_negative_transit=sum(1 for m in movements if m.transit_days < 0),
        unmeasurable_volume_share=share, span_days=span_days,
        outcome=outcome, summary=summary,
    )
