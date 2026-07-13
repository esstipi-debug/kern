"""Supply chain digital twin - multi-echelon network simulator.

Extends the single-node (R,S) mechanics of ``src/simulation.py`` (Vandeput 2020,
ch. 5.3/13) to a supplier -> DC -> store network so the rest of the engine has
complex, realistic scenarios to analyze. Demand is configurable (trend,
seasonality, promotions, intermittency, noise) and disruptions (supplier
outage, lead-time spike, demand surge) ripple through echelons the way they do
in a real network: a starved DC delays every store it feeds.

Purpose-built to FEED the engine: the job layer (``jobs/digital_twin_job.py``)
shapes the traces into the same CSV schemas the analysis tools ingest, so a
generated scenario can be run straight through forecasting / safety stock /
policy tools as if it were a client export.

Semantics (they define what the KPIs mean):

- Stores face external demand under **lost sales**: what the shelf cannot serve
  in the period is gone, so a disruption permanently costs service.
- Inter-node orders are **never lost**: an upstream node keeps a FIFO queue of
  owed shipments and drains it as stock allows (the post-outage surge this
  produces is the bullwhip effect, on purpose).
- Per period: (1) shipments due arrive, (2) demand hits stores, (3) nodes
  review bottom-up (stores before their DC) placing order-up-to orders on their
  upstream, (4) every queue drains FIFO with same-period shipping.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from numpy.random import Generator

_NODE_KINDS = ("supplier", "dc", "store")
_DISRUPTION_KINDS = ("supplier_outage", "lead_time_spike", "demand_surge")


@dataclass(frozen=True)
class DemandProfile:
    """External (store-level) demand pattern, in units per period."""

    base: float = 100.0
    trend: float = 0.0             # additive units per period
    season_amplitude: float = 0.0  # fraction of base (0.3 -> +/-30% swing)
    season_period: int = 52
    promo_every: int = 0           # a promo window opens every N periods (0 = never)
    promo_length: int = 1
    promo_uplift: float = 0.0      # fraction of base added during a promo window
    noise_std: float = 0.0         # gaussian noise, units
    zero_prob: float = 0.0         # intermittency: P(a period sells nothing)


@dataclass(frozen=True)
class NodeSpec:
    """One network node. ``supplier`` names the upstream node (None for sources)."""

    name: str
    kind: str                        # "supplier" | "dc" | "store"
    supplier: str | None = None
    lead_time: int = 2               # transit periods from upstream to this node
    review_period: int = 1
    order_up_to: float = 0.0         # S; 0 -> auto-sized from demand
    initial_on_hand: float | None = None  # None -> start at S
    capacity: float | None = None    # max on-hand; also caps the effective S


@dataclass(frozen=True)
class Disruption:
    """A time-boxed shock. Active on periods [start, start + duration)."""

    kind: str        # "supplier_outage" | "lead_time_spike" | "demand_surge"
    target: str      # node it hits (demand_surge: must be a store)
    start: int
    duration: int
    magnitude: float = 1.0  # lead_time_spike: extra transit periods; demand_surge: demand multiplier

    def active(self, period: int) -> bool:
        return self.start <= period < self.start + self.duration


@dataclass(frozen=True)
class NodeStats:
    """Aggregated per-node service and inventory metrics."""

    name: str
    kind: str
    fill_rate: float          # stores: units served / demanded; dc/supplier: shipped / requested
    stockout_periods: int     # stores: empty-shelf periods; dc/supplier: periods owing shipments
    mean_on_hand: float
    orders_placed: int
    mean_order_qty: float


@dataclass(frozen=True)
class TwinResult:
    """One simulated scenario: KPIs plus full traces for export."""

    periods: int
    nodes: tuple[NodeStats, ...]
    network_fill_rate: float           # end-customer units served / demanded
    total_mean_on_hand: float
    demand: dict[str, np.ndarray]      # store -> external demand trace
    served: dict[str, np.ndarray]      # store -> units served trace (same period)
    on_hand: dict[str, np.ndarray]     # non-supplier node -> end-of-period on-hand
    orders: dict[str, tuple[tuple[int, float], ...]]  # node -> (period, qty) placed


def generate_demand(profile: DemandProfile, periods: int, rng: Generator) -> np.ndarray:
    """Build one store's external demand trace from its profile."""
    if profile.base < 0:
        raise ValueError("base demand must be >= 0")
    if not 0.0 <= profile.zero_prob <= 1.0:
        raise ValueError("zero_prob must be in [0, 1]")
    if profile.season_period <= 0 or profile.promo_length <= 0:
        raise ValueError("season_period and promo_length must be > 0")
    if profile.noise_std < 0 or profile.season_amplitude < 0:
        raise ValueError("noise_std and season_amplitude must be >= 0")

    t = np.arange(periods, dtype=float)
    demand = profile.base + profile.trend * t
    if profile.season_amplitude > 0:
        demand += profile.base * profile.season_amplitude * np.sin(
            2.0 * np.pi * t / profile.season_period
        )
    if profile.promo_every > 0 and profile.promo_uplift > 0:
        in_promo = (np.arange(periods) % profile.promo_every) < profile.promo_length
        demand += np.where(in_promo, profile.base * profile.promo_uplift, 0.0)
    if profile.noise_std > 0:
        demand += rng.normal(0.0, profile.noise_std, size=periods)
    if profile.zero_prob > 0:
        demand *= rng.random(periods) >= profile.zero_prob
    return np.maximum(demand, 0.0)


def _validate_network(nodes: list[NodeSpec] | tuple[NodeSpec, ...]) -> dict[str, NodeSpec]:
    by_name: dict[str, NodeSpec] = {}
    for node in nodes:
        if node.name in by_name:
            raise ValueError(f"duplicate node name: {node.name}")
        if node.kind not in _NODE_KINDS:
            raise ValueError(f"{node.name}: unknown kind {node.kind!r}")
        if node.lead_time < 0 or node.review_period <= 0:
            raise ValueError(f"{node.name}: invalid lead_time or review_period")
        by_name[node.name] = node

    for node in by_name.values():
        if node.kind == "supplier":
            if node.supplier is not None:
                raise ValueError(f"{node.name}: a supplier node cannot have an upstream")
            continue
        if node.supplier is None:
            raise ValueError(f"{node.name}: {node.kind} nodes need a supplier")
        upstream = by_name.get(node.supplier)
        if upstream is None:
            raise ValueError(f"{node.name}: upstream {node.supplier!r} not found")
        if upstream.kind == "store":
            raise ValueError(f"{node.name}: a store cannot supply other nodes")

    if not any(n.kind == "store" for n in by_name.values()):
        raise ValueError("network needs at least one store")
    if not any(n.kind == "supplier" for n in by_name.values()):
        raise ValueError("network needs at least one supplier source")
    return by_name


def _validate_disruptions(
    disruptions: tuple[Disruption, ...], by_name: dict[str, NodeSpec]
) -> None:
    for d in disruptions:
        if d.kind not in _DISRUPTION_KINDS:
            raise ValueError(f"unknown disruption kind: {d.kind!r}")
        target = by_name.get(d.target)
        if target is None:
            raise ValueError(f"disruption target {d.target!r} not found")
        if d.kind == "demand_surge" and target.kind != "store":
            raise ValueError("demand_surge targets must be store nodes")
        if d.start < 0 or d.duration <= 0 or d.magnitude < 0:
            raise ValueError("disruption start/duration/magnitude out of range")


def _bottom_up_order(by_name: dict[str, NodeSpec]) -> list[str]:
    """Stores first, then their DCs, then upper tiers - children before parents."""
    height: dict[str, int] = {}

    def _height(name: str) -> int:
        if name in height:
            return height[name]
        children = [n.name for n in by_name.values() if n.supplier == name]
        h = 0 if not children else 1 + max(_height(c) for c in children)
        height[name] = h
        return h

    return sorted(by_name, key=_height)


def _auto_size(
    by_name: dict[str, NodeSpec], demand: DemandProfile, factor: float
) -> dict[str, float]:
    """Order-up-to levels sized from mean demand over the risk period when S is 0."""
    mu: dict[str, float] = {}

    def _mu(name: str) -> float:
        if name in mu:
            return mu[name]
        node = by_name[name]
        if node.kind == "store":
            promo = (
                demand.base * demand.promo_uplift * demand.promo_length / demand.promo_every
                if demand.promo_every > 0 else 0.0
            )
            value = (demand.base + promo) * (1.0 - demand.zero_prob)
        else:
            value = sum(_mu(n.name) for n in by_name.values() if n.supplier == name)
        mu[name] = value
        return value

    levels: dict[str, float] = {}
    for name, node in by_name.items():
        if node.kind == "supplier":
            continue
        s = node.order_up_to if node.order_up_to > 0 else (
            _mu(name) * (node.lead_time + node.review_period) * factor
        )
        if node.capacity is not None:
            s = min(s, node.capacity)
        levels[name] = s
    return levels


@dataclass
class _NodeState:
    """Mutable per-node bookkeeping while the simulation runs."""

    spec: NodeSpec
    order_up_to: float
    on_hand: float
    pipeline: list[tuple[int, float]] = field(default_factory=list)
    unshipped: float = 0.0     # ordered upstream, not yet on a truck (owed to us)
    queue: list[tuple[str, float]] = field(default_factory=list)  # owed downstream (receiver, qty)
    total_demand: float = 0.0
    total_served: float = 0.0
    stockout_periods: int = 0
    on_hand_sum: float = 0.0
    orders: list[tuple[int, float]] = field(default_factory=list)


def simulate_network(
    nodes: list[NodeSpec] | tuple[NodeSpec, ...],
    demand: DemandProfile,
    *,
    periods: int = 364,
    disruptions: tuple[Disruption, ...] = (),
    seed: int | None = 42,
    auto_size_factor: float = 1.5,
) -> TwinResult:
    """Simulate the network for ``periods`` and return KPIs plus full traces."""
    if periods <= 0:
        raise ValueError("periods must be > 0")
    by_name = _validate_network(nodes)
    _validate_disruptions(disruptions, by_name)

    rng = np.random.default_rng(seed)
    levels = _auto_size(by_name, demand, auto_size_factor)
    stores = [n.name for n in by_name.values() if n.kind == "store"]
    demand_traces = {name: generate_demand(demand, periods, rng) for name in stores}
    for d in disruptions:
        if d.kind == "demand_surge":
            trace = demand_traces[d.target].copy()
            trace[d.start : d.start + d.duration] *= d.magnitude
            demand_traces[d.target] = trace

    states: dict[str, _NodeState] = {}
    for name, spec in by_name.items():
        s = levels.get(name, 0.0)
        start = spec.initial_on_hand if spec.initial_on_hand is not None else s
        states[name] = _NodeState(spec=spec, order_up_to=s, on_hand=start)

    served_traces = {name: np.zeros(periods) for name in stores}
    on_hand_traces = {
        name: np.zeros(periods) for name, n in by_name.items() if n.kind != "supplier"
    }
    review_order = _bottom_up_order(by_name)

    def _extra_lead(receiver: str, t: int) -> int:
        return sum(
            int(round(d.magnitude))
            for d in disruptions
            if d.kind == "lead_time_spike" and d.target == receiver and d.active(t)
        )

    def _blocked(shipper: str, t: int) -> bool:
        return any(
            d.kind == "supplier_outage" and d.target == shipper and d.active(t)
            for d in disruptions
        )

    def _drain_queue(state: _NodeState, t: int) -> None:
        """Ship owed orders FIFO with whatever this node can put on trucks today."""
        if not state.queue or _blocked(state.spec.name, t):
            if state.queue:
                state.stockout_periods += 1
            return
        remaining: list[tuple[str, float]] = []
        for receiver_name, qty in state.queue:
            if state.spec.kind == "supplier":
                shipped = qty
            else:
                shipped = min(state.on_hand, qty)
                state.on_hand -= shipped
            if shipped > 0:
                state.total_served += shipped
                receiver = states[receiver_name]
                receiver.unshipped -= shipped
                lead = receiver.spec.lead_time + _extra_lead(receiver_name, t)
                receiver.pipeline.append((t + lead, shipped))
            if shipped < qty:
                remaining.append((receiver_name, qty - shipped))
        state.queue = remaining
        if state.queue:
            state.stockout_periods += 1

    for t in range(periods):
        # 1. arrivals
        for state in states.values():
            due = sum(qty for when, qty in state.pipeline if when == t)
            if due > 0:
                state.on_hand += due
                if state.spec.capacity is not None:
                    state.on_hand = min(state.on_hand, state.spec.capacity)
                state.pipeline = [(w, q) for w, q in state.pipeline if w != t]

        # 2. external demand hits stores (lost sales)
        for name in stores:
            state = states[name]
            d_t = demand_traces[name][t]
            state.total_demand += d_t
            if state.on_hand <= 0 and d_t > 0:
                state.stockout_periods += 1
            served = min(state.on_hand, d_t)
            state.on_hand -= served
            state.total_served += served
            served_traces[name][t] = served

        # 3. reviews, bottom-up: stores order before their DC reviews
        for name in review_order:
            state = states[name]
            if state.spec.kind == "supplier" or t % state.spec.review_period != 0:
                continue
            owed = sum(qty for _, qty in state.queue)
            net = state.on_hand + sum(q for _, q in state.pipeline) + state.unshipped - owed
            order_qty = max(state.order_up_to - net, 0.0)
            if order_qty <= 0:
                continue
            state.orders.append((t, order_qty))
            state.unshipped += order_qty
            upstream = states[state.spec.supplier]
            upstream.total_demand += order_qty
            upstream.queue.append((name, order_qty))

        # 4. every node ships what it owes
        for name in review_order:
            _drain_queue(states[name], t)

        # 5. end-of-period traces
        for name, state in states.items():
            if state.spec.kind != "supplier":
                on_hand_traces[name][t] = state.on_hand
                state.on_hand_sum += state.on_hand

    stats: list[NodeStats] = []
    for name in review_order:
        state = states[name]
        qtys = [q for _, q in state.orders]
        stats.append(NodeStats(
            name=name,
            kind=state.spec.kind,
            fill_rate=(state.total_served / state.total_demand) if state.total_demand > 0 else 1.0,
            stockout_periods=state.stockout_periods,
            mean_on_hand=state.on_hand_sum / periods if state.spec.kind != "supplier" else 0.0,
            orders_placed=len(qtys),
            mean_order_qty=float(np.mean(qtys)) if qtys else 0.0,
        ))

    total_demand = sum(states[s].total_demand for s in stores)
    total_served = sum(states[s].total_served for s in stores)
    return TwinResult(
        periods=periods,
        nodes=tuple(stats),
        network_fill_rate=(total_served / total_demand) if total_demand > 0 else 1.0,
        total_mean_on_hand=sum(
            st.on_hand_sum / periods for st in states.values() if st.spec.kind != "supplier"
        ),
        demand=demand_traces,
        served=served_traces,
        on_hand=on_hand_traces,
        orders={name: tuple(states[name].orders) for name in review_order},
    )
