"""Scheduling / sequencing engine (Jacobs & Chase, *Operations and Supply Chain
Management* 15e, ch. 22).

Operational, deterministic sequencing that Kern's aggregate S&OP layer did not reach:

- **Johnson's rule** - the optimal two-machine flow-shop sequence (minimum makespan).
- **Assignment / Hungarian** - optimal one-to-one allocation (job->machine, SKU->DC,
  shipment->carrier-lane) minimising total cost.
- **Dispatching rules** - single-machine sequencing (FCFS / SPT / LPT / EDD) with the
  flow-time and lateness metrics each rule optimises.
- **First-hour roster** - staff a time-varying hourly requirement with fixed-length shifts.

Pure analytic core; the assignment optimum reuses ``scipy`` (already a project dependency).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import linear_sum_assignment

# -- Johnson's rule ------------------------------------------------------------

def _makespan_two_machine(sequence: list[str], times: dict[str, tuple[float, float]]) -> float:
    m1 = m2 = 0.0
    for jid in sequence:
        t1, t2 = times[jid]
        m1 += t1
        m2 = max(m2, m1) + t2
    return m2


def johnson_two_machine(jobs: list[tuple[str, float, float]]) -> tuple[list[str], float]:
    """Optimal 2-machine flow-shop sequence (minimum makespan), Johnson's rule.

    ``jobs`` = list of (id, time_on_machine_1, time_on_machine_2). Repeatedly take the
    shortest remaining operation: on machine 1 -> schedule as early as possible; on machine
    2 -> as late as possible. Returns (sequence, makespan).
    """
    remaining = list(jobs)
    front: list[str] = []
    back: list[str] = []
    while remaining:
        best_time = best_i = best_machine = None
        for i, (_jid, t1, t2) in enumerate(remaining):
            if best_time is None or t1 < best_time:
                best_time, best_i, best_machine = t1, i, 1
            if t2 < best_time:
                best_time, best_i, best_machine = t2, i, 2
        jid = remaining[best_i][0]
        if best_machine == 1:
            front.append(jid)
        else:
            back.insert(0, jid)
        remaining.pop(best_i)
    sequence = front + back
    times = {jid: (t1, t2) for jid, t1, t2 in jobs}
    return sequence, _makespan_two_machine(sequence, times)


# -- Assignment / Hungarian ----------------------------------------------------

def assign(cost_matrix: list[list[float]]) -> tuple[dict[int, int], float]:
    """Optimal one-to-one assignment minimising total cost (Hungarian algorithm).

    Returns ({row: col}, total_cost). Rectangular matrices are allowed (scipy leaves the
    surplus rows/cols unassigned).
    """
    arr = np.asarray(cost_matrix, dtype=float)
    if arr.ndim != 2 or arr.size == 0:
        raise ValueError("cost_matrix must be a non-empty 2-D matrix")
    rows, cols = linear_sum_assignment(arr)
    assignment = {int(r): int(c) for r, c in zip(rows, cols)}
    total = float(arr[rows, cols].sum())
    return assignment, total


# -- Single-machine dispatching rules ------------------------------------------

@dataclass(frozen=True)
class Job:
    """One job to sequence on a single resource."""

    id: str
    processing: float
    due: float = 0.0


@dataclass(frozen=True)
class DispatchResult:
    sequence: list[str]
    mean_flow_time: float       # SPT minimises this
    mean_lateness: float
    max_lateness: float         # EDD minimises this


_RULES = ("FCFS", "SPT", "LPT", "EDD")


def dispatch_sequence(jobs: list[Job], rule: str = "SPT") -> DispatchResult:
    """Sequence jobs on one machine by a priority rule and report flow-time / lateness.

    FCFS = arrival order; SPT = shortest processing first (minimises mean flow time);
    LPT = longest first; EDD = earliest due date first (minimises maximum lateness).
    """
    rule = rule.upper()
    if rule not in _RULES:
        raise ValueError(f"unknown rule {rule!r}; choose from {_RULES}")
    if rule == "FCFS":
        ordered = list(jobs)
    elif rule == "SPT":
        ordered = sorted(jobs, key=lambda j: j.processing)
    elif rule == "LPT":
        ordered = sorted(jobs, key=lambda j: -j.processing)
    else:  # EDD
        ordered = sorted(jobs, key=lambda j: j.due)

    clock = 0.0
    flows: list[float] = []
    lates: list[float] = []
    for j in ordered:
        clock += j.processing
        flows.append(clock)
        lates.append(max(0.0, clock - j.due))
    n = len(ordered) or 1
    return DispatchResult(
        sequence=[j.id for j in ordered],
        mean_flow_time=sum(flows) / n,
        mean_lateness=sum(lates) / n,
        max_lateness=max(lates) if lates else 0.0,
    )


# -- First-hour shift roster ---------------------------------------------------

def first_hour_roster(
    requirements: list[int], shift_length: int
) -> tuple[list[int], list[int]]:
    """Staff a per-hour requirement with fixed-length shifts (the first-hour principle).

    Each hour, add only enough new starts to cover the requirement given who is still on
    shift (a worker started at hour s covers hours s .. s+shift_length-1). Returns
    (starts_per_hour, on_duty_per_hour).
    """
    if shift_length < 1:
        raise ValueError("shift_length must be >= 1")
    horizon = len(requirements)
    starts = [0] * horizon

    def on_at(hour: int) -> int:
        lo = max(0, hour - shift_length + 1)
        return sum(starts[s] for s in range(lo, hour + 1))

    for h in range(horizon):
        starts[h] = max(0, requirements[h] - on_at(h))
    on_duty = [on_at(h) for h in range(horizon)]
    return starts, on_duty
