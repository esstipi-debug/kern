"""Waiting-line / queuing engine (Jacobs & Chase, *Operations and Supply Chain
Management* 15e, ch. 10).

Closed-form congestion models Kern previously lacked entirely. They turn an arrival
rate (lambda) and service rate (mu) - and, for the G/G/c case, the *variability* of each -
into queue length, wait time, and the cost-optimal number of servers. The use cases are
capacity/staffing decisions: dock doors, pick stations, packing lines, returns desks,
call/support queues, and MRO repair crews (finite source).

Pure (no external deps beyond the stdlib ``math``): frozen dataclasses + pure functions,
mirroring the analytical-core style of ``eoq`` / ``safety_stock``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class QueueMetrics:
    """Steady-state performance of a queue. Times are in the same unit as 1/rate."""

    servers: int
    utilization: float      # rho - fraction of server capacity used (0..1)
    lq: float               # average number waiting in line
    ls: float               # average number in the system (waiting + in service)
    wq: float               # average time waiting in line
    ws: float               # average time in the system
    prob_wait: float        # probability an arrival has to wait at all


def _check_rates(arrival_rate: float, service_rate: float) -> None:
    if arrival_rate <= 0 or service_rate <= 0:
        raise ValueError("arrival_rate and service_rate must be > 0")


def mm1(arrival_rate: float, service_rate: float) -> QueueMetrics:
    """Single-server, Poisson arrivals, exponential service (M/M/1)."""
    _check_rates(arrival_rate, service_rate)
    rho = arrival_rate / service_rate
    if rho >= 1:
        raise ValueError(f"unstable queue: utilization {rho:.3f} >= 1 (arrival_rate >= service_rate)")
    lq = rho * rho / (1 - rho)
    ls = rho / (1 - rho)
    return QueueMetrics(
        servers=1, utilization=rho, lq=lq, ls=ls,
        wq=lq / arrival_rate, ws=ls / arrival_rate, prob_wait=rho,
    )


def md1(arrival_rate: float, service_rate: float) -> QueueMetrics:
    """Single server with *constant* service time (M/D/1) - halves the M/M/1 queue."""
    _check_rates(arrival_rate, service_rate)
    rho = arrival_rate / service_rate
    if rho >= 1:
        raise ValueError(f"unstable queue: utilization {rho:.3f} >= 1")
    lq = rho * rho / (2 * (1 - rho))           # exactly half of M/M/1
    ls = lq + rho
    return QueueMetrics(
        servers=1, utilization=rho, lq=lq, ls=ls,
        wq=lq / arrival_rate, ws=ls / arrival_rate, prob_wait=rho,
    )


def _erlang_c(a: float, servers: int, rho: float) -> tuple[float, float]:
    """Return (P0, prob_wait) for an M/M/c queue. a = lambda/mu, rho = a/c."""
    terms = sum(a ** n / math.factorial(n) for n in range(servers))
    last = a ** servers / (math.factorial(servers) * (1 - rho))
    p0 = 1.0 / (terms + last)
    prob_wait = last * p0
    return p0, prob_wait


def mmc(arrival_rate: float, service_rate: float, servers: int) -> QueueMetrics:
    """Multi-server, Poisson arrivals, exponential service (M/M/c, Erlang-C)."""
    _check_rates(arrival_rate, service_rate)
    if servers < 1:
        raise ValueError("servers must be >= 1")
    a = arrival_rate / service_rate
    rho = a / servers
    if rho >= 1:
        raise ValueError(f"unstable queue: utilization {rho:.3f} >= 1 (need servers > lambda/mu)")
    p0, prob_wait = _erlang_c(a, servers, rho)
    lq = prob_wait * rho / (1 - rho)
    ls = lq + a
    return QueueMetrics(
        servers=servers, utilization=rho, lq=lq, ls=ls,
        wq=lq / arrival_rate, ws=ls / arrival_rate, prob_wait=prob_wait,
    )


def ggc_approx(
    arrival_rate: float,
    service_rate: float,
    servers: int,
    cv_arrival: float,
    cv_service: float,
) -> QueueMetrics:
    """General arrivals/service approximation (Sakasegawa / Kingman 'VUT' form).

    Wait scales with the variability factor (Ca^2 + Cs^2)/2 and explodes as rho -> 1, so a
    deterministic engine fed only the *mean and CV* of interarrival and service times can
    quantify how process variability drives congestion. Reduces to exact M/M/1 when c=1 and
    both CVs are 1.
    """
    _check_rates(arrival_rate, service_rate)
    if servers < 1:
        raise ValueError("servers must be >= 1")
    if cv_arrival < 0 or cv_service < 0:
        raise ValueError("coefficients of variation must be >= 0")
    rho = arrival_rate / (servers * service_rate)
    if rho >= 1:
        raise ValueError(f"unstable queue: utilization {rho:.3f} >= 1")
    variability = (cv_arrival ** 2 + cv_service ** 2) / 2.0
    util_factor = rho ** (math.sqrt(2 * (servers + 1)) - 1) / (servers * (1 - rho))
    wq = variability * util_factor / service_rate
    lq = arrival_rate * wq
    ls = lq + arrival_rate / service_rate
    return QueueMetrics(
        servers=servers, utilization=rho, lq=lq, ls=ls,
        wq=wq, ws=ls / arrival_rate, prob_wait=min(1.0, rho),
    )


def finite_source(
    population: int, servers: int, run_rate: float, service_rate: float
) -> QueueMetrics:
    """Finite-source (machine-repair) queue: N units, each fails at ``run_rate`` while up,
    repaired at ``service_rate`` by one of ``servers`` repairers.

    Solved exactly via the birth-death recursion (state-dependent arrival rate (N-n)*lambda),
    so no finite-queuing table lookup is needed. Returns server utilization, the expected
    number down (Ls) and waiting for a repairer (Lq), and the corresponding times.
    """
    if population < 1 or servers < 1 or servers > population:
        raise ValueError("require population >= 1 and 1 <= servers <= population")
    _check_rates(run_rate, service_rate)
    # Unnormalised state probabilities p[n], n = number of units down.
    p = [1.0]
    for n in range(1, population + 1):
        served = min(n, servers)
        p.append(p[n - 1] * ((population - n + 1) * run_rate) / (served * service_rate))
    total = sum(p)
    prob = [x / total for x in p]

    ls = sum(n * prob[n] for n in range(population + 1))                 # avg down
    lq = sum(max(n - servers, 0) * prob[n] for n in range(population + 1))  # avg waiting
    in_service = sum(min(n, servers) * prob[n] for n in range(population + 1))
    eff_arrival = sum((population - n) * run_rate * prob[n] for n in range(population + 1))
    prob_wait = sum(prob[n] for n in range(servers, population + 1))
    return QueueMetrics(
        servers=servers,
        utilization=in_service / servers,
        lq=lq, ls=ls,
        wq=lq / eff_arrival if eff_arrival > 0 else 0.0,
        ws=ls / eff_arrival if eff_arrival > 0 else 0.0,
        prob_wait=prob_wait,
    )


@dataclass(frozen=True)
class StaffingChoice:
    """One server-count option and its costed trade-off (in-system + staffing cost)."""

    servers: int
    metrics: QueueMetrics
    waiting_cost: float     # cost of customers/units in the system per unit time
    service_cost: float     # cost of the servers per unit time
    total_cost: float


def optimize_servers(
    arrival_rate: float,
    service_rate: float,
    *,
    wait_cost_per_unit_time: float,
    server_cost_per_unit_time: float,
    max_servers: int = 20,
) -> list[StaffingChoice]:
    """Rank feasible server counts by total cost = (Ls * wait cost) + (servers * server cost).

    The classic queuing staffing decision: trade the cost of units sitting in the system
    against the cost of the servers. Returns the options best-first (lowest total cost).
    """
    _check_rates(arrival_rate, service_rate)
    if wait_cost_per_unit_time < 0 or server_cost_per_unit_time < 0:
        raise ValueError("costs must be >= 0")
    c_min = math.floor(arrival_rate / service_rate) + 1   # smallest c with rho < 1
    choices: list[StaffingChoice] = []
    for c in range(c_min, max_servers + 1):
        m = mmc(arrival_rate, service_rate, c)
        waiting = m.ls * wait_cost_per_unit_time
        service = c * server_cost_per_unit_time
        choices.append(StaffingChoice(c, m, waiting, service, waiting + service))
    choices.sort(key=lambda ch: ch.total_cost)
    return choices
