"""Multi-echelon GSM (serial) — Vandeput (2020), Chapter 10."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product

import numpy as np

from src.safety_stock import service_level_factor


@dataclass(frozen=True)
class EchelonNode:
    index: int
    lead_time: float
    holding_cost: float
    risk_period: float
    safety_stock: float
    order_up_to: float


@dataclass(frozen=True)
class GSMAllocation:
    case_id: int
    risk_periods: tuple[float, ...]
    nodes: tuple[EchelonNode, ...]
    total_holding_cost: float
    echelon_order_up_to: tuple[float, ...]


def serial_gsm_cases(
    lead_times: list[float],
    review_period: float = 1.0,
) -> list[tuple[float, ...]]:
    """
    All-or-nothing risk-period patterns for serial chain (Section 10.4.3).

    2^(n-1) cases; demand node always holds review period in its coverage when stocking.
    """
    n = len(lead_times)
    if n == 0:
        raise ValueError("lead_times required")
    total = sum(lead_times) + review_period
    cases: list[tuple[float, ...]] = []

    for mask in product([0, 1], repeat=n - 1):
        x_tau = [0.0] * n
        cumulative = 0.0
        for i in range(n - 1):
            if mask[i]:
                x_tau[i] = cumulative + lead_times[i]
                cumulative = 0.0
            else:
                cumulative += lead_times[i]
                x_tau[i] = 0.0
        x_tau[-1] = total - sum(x_tau[:-1])
        if x_tau[-1] < review_period:
            continue
        cases.append(tuple(x_tau))

    # Deduplicate while preserving order
    unique: list[tuple[float, ...]] = []
    for case in cases:
        if case not in unique:
            unique.append(case)
    return unique


def evaluate_serial_allocation(
    risk_periods: tuple[float, ...],
    lead_times: list[float],
    mean_demand_per_period: float,
    demand_std_per_period: float,
    holding_costs: list[float],
    cycle_service_level: float,
    review_period: float = 1.0,
    case_id: int = 0,
) -> GSMAllocation:
    """Ss_i = z * sigma_d * sqrt(x_i); cost = sum(Ss_i * h_i) (eq. 10.1)."""
    z = service_level_factor(cycle_service_level)
    nodes: list[EchelonNode] = []
    for i, (lt, h, x_tau) in enumerate(zip(lead_times, holding_costs, risk_periods)):
        ss = z * demand_std_per_period * (x_tau**0.5) if x_tau > 0 else 0.0
        mu_x = mean_demand_per_period * x_tau
        order_up_to = mu_x + ss if x_tau > 0 else 0.0
        nodes.append(
            EchelonNode(
                index=i,
                lead_time=lt,
                holding_cost=h,
                risk_period=x_tau,
                safety_stock=ss,
                order_up_to=order_up_to,
            )
        )

    total_cost = sum(node.safety_stock * node.holding_cost for node in nodes)
    order_up_levels = [node.order_up_to for node in nodes]
    echelon = []
    running = 0.0
    for s in reversed(order_up_levels):
        running += s
        echelon.append(running)
    echelon = tuple(reversed(echelon))

    return GSMAllocation(
        case_id=case_id,
        risk_periods=risk_periods,
        nodes=tuple(nodes),
        total_holding_cost=total_cost,
        echelon_order_up_to=echelon,
    )


def optimize_serial_gsm(
    lead_times: list[float],
    mean_demand_per_period: float,
    demand_std_per_period: float,
    holding_costs: list[float],
    cycle_service_level: float,
    review_period: float = 1.0,
) -> GSMAllocation:
    """Pick allocation minimizing holding cost (Section 10.4.3)."""
    cases = serial_gsm_cases(lead_times, review_period)
    best: GSMAllocation | None = None
    for idx, case in enumerate(cases, start=1):
        candidate = evaluate_serial_allocation(
            case,
            lead_times,
            mean_demand_per_period,
            demand_std_per_period,
            holding_costs,
            cycle_service_level,
            review_period,
            case_id=idx,
        )
        if best is None or candidate.total_holding_cost < best.total_holding_cost:
            best = candidate
    if best is None:
        raise ValueError("no feasible GSM allocation")
    return best


def echelon_inventory(
    local_on_hand: list[float],
) -> list[float]:
    """Echelon inventory = sum from node i through downstream (Section 10.4.4)."""
    n = len(local_on_hand)
    result = []
    for i in range(n):
        result.append(sum(local_on_hand[i:]))
    return result


def echelon_orders(
    local_on_hand: list[float],
    in_transit: list[float],
    echelon_targets: tuple[float, ...],
    customer_backorders: float = 0.0,
) -> list[float]:
    """Orders = echelon target - echelon net inventory."""
    net_local = [on_hand + transit for on_hand, transit in zip(local_on_hand, in_transit)]
    echelon_net = echelon_inventory(net_local)
    if customer_backorders > 0:
        echelon_net = [net - customer_backorders for net in echelon_net]
    return [max(target - net, 0.0) for target, net in zip(echelon_targets, echelon_net)]


@dataclass(frozen=True)
class GSMSimulationResult:
    periods: int
    mean_echelon_inventory: tuple[float, ...]
    fill_rate: float
    stockout_periods: int
    mean_backorders: float = 0.0


def simulate_serial_gsm(
    allocation: GSMAllocation,
    lead_times: list[int],
    review_period: int = 1,
    periods: int = 5_000,
    *,
    mean_demand: float = 100.0,
    std_demand: float = 25.0,
    seed: int | None = 42,
    backorders: bool = True,
) -> GSMSimulationResult:
    """
    Serial echelon base-stock simulation (Section 10.5).

    Customer demand at the last node; periodic echelon order-up-to at each stage.
    Backorders accrue at the demand node when enabled.
    """
    n = len(lead_times)
    if n == 0 or len(allocation.echelon_order_up_to) != n:
        raise ValueError("allocation and lead_times length mismatch")

    rng = np.random.default_rng(seed)
    demand = np.maximum(rng.normal(mean_demand, std_demand, size=periods), 0.0)

    on_hand = [allocation.nodes[i].order_up_to for i in range(n)]
    pipeline: list[list[tuple[int, float]]] = [[] for _ in range(n)]
    echelon_sums = [0.0] * n
    backorder_trace: list[float] = []
    customer_backorders = 0.0
    stockouts = 0
    total_demand = 0.0
    units_served = 0.0
    last = n - 1

    for t in range(periods):
        for i in range(n):
            arrivals = [qty for due, qty in pipeline[i] if due == t]
            if arrivals:
                received = sum(arrivals)
                on_hand[i] += received
                if i == last and customer_backorders > 0:
                    fulfilled = min(on_hand[i], customer_backorders)
                    on_hand[i] -= fulfilled
                    customer_backorders -= fulfilled
                    units_served += fulfilled
            pipeline[i] = [(due, qty) for due, qty in pipeline[i] if due != t]

        transit = [sum(qty for _, qty in pipe) for pipe in pipeline]
        net_local = [oh + tr for oh, tr in zip(on_hand, transit)]
        echelon_net = echelon_inventory(net_local)
        if customer_backorders > 0:
            echelon_net = [e - customer_backorders for e in echelon_net]
        for i in range(n):
            echelon_sums[i] += echelon_net[i]
        backorder_trace.append(customer_backorders)

        d = demand[t]
        total_demand += d
        if on_hand[last] >= d:
            on_hand[last] -= d
            units_served += d
        else:
            served = on_hand[last]
            units_served += served
            short = d - served
            if backorders:
                customer_backorders += short
            on_hand[last] = 0.0
            if d > 0:
                stockouts += 1

        if t % review_period == 0:
            orders = echelon_orders(
                on_hand, transit, allocation.echelon_order_up_to, customer_backorders
            )
            for i in range(n):
                if orders[i] <= 0:
                    continue
                if i == 0:
                    # The root node draws from an infinite exogenous supplier (the
                    # standard serial-GSM boundary condition): it always ships in full.
                    shipped = orders[i]
                else:
                    # Every other node orders FROM the node upstream of it and can only
                    # receive what that node actually has on hand right now. Previously
                    # every stage materialized its full order from nowhere regardless of
                    # what stage i-1 held, so on_hand[i-1] was only ever incremented and
                    # an upstream shortage could never delay (or even touch) downstream
                    # stages - the chain wasn't coupled by material flow at all. Any
                    # unshipped remainder is simply not fulfilled this period; it is not
                    # lost, because echelon_orders() recomputes next period from the
                    # still-low net inventory, so the shortfall carries forward as a
                    # larger order once stage i-1 restocks.
                    shipped = min(orders[i], on_hand[i - 1])
                    on_hand[i - 1] -= shipped
                if shipped > 0:
                    pipeline[i].append((t + lead_times[i], shipped))

    fill_rate = units_served / total_demand if total_demand > 0 else 1.0
    mean_echelon = tuple(s / periods for s in echelon_sums)
    return GSMSimulationResult(
        periods=periods,
        mean_echelon_inventory=mean_echelon,
        fill_rate=fill_rate,
        stockout_periods=stockouts,
        mean_backorders=float(np.mean(backorder_trace)) if backorder_trace else 0.0,
    )
