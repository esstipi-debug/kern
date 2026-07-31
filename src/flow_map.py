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
    unit_cost: float = 0.0
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
        notes.append(
            f"bidirectional flow detected ({legs}); the lower-volume leg is ignored "
            "when computing echelons"
        )

    echelon = {n: 0 for n in nodes_set}
    if not sources:
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
