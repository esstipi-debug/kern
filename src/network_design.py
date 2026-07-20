"""Multi-facility network design - the p-median problem via MILP (offline).

Single-facility siting (center of gravity / Weiszfeld, src.facility_location) answers "where
should the one DC go" on a continuous plane. This module answers the distinct network-design
question "which p of these candidate sites should we open, and which demand point does each
serve, to minimize total weighted travel" - the classic p-median model (Chopra & Meindl,
Network Design in the Supply Chain). It is solved exactly as a mixed-integer linear program
via scipy.optimize.milp (HiGHS branch-and-bound) - the same scipy.optimize backend that
src.dea already uses for LP, so no new dependency.

Pure and deterministic: frozen dataclasses in, a frozen NetworkDesign out, no I/O.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp

from src.facility_location import DemandPoint, Location, total_weighted_distance


@dataclass(frozen=True)
class CandidateSite:
    """A site that may be opened; fixed_cost and capacity are optional."""

    name: str
    x: float
    y: float
    fixed_cost: float = 0.0
    capacity: float | None = None


@dataclass(frozen=True)
class NetworkDesign:
    """The p-median solution: which sites open, who each demand is served by, and the prize."""

    feasible: bool
    p: int
    open_sites: tuple[str, ...]
    assignment: dict[str, str]          # demand name -> open site name
    total_weighted_distance: float
    total_fixed_cost: float
    objective: float                    # weighted distance + fixed cost (the MILP objective)
    baseline_distance: float            # best single facility (p=1) - the "value of >1 DC" anchor
    saving_vs_baseline: float
    saving_pct: float


def _distance(point: DemandPoint, site: CandidateSite) -> float:
    return math.hypot(point.x - site.x, point.y - site.y)


def _best_single_facility_distance(
    demands: list[DemandPoint], sites: list[CandidateSite]
) -> float:
    """The p=1 baseline: lowest total weighted distance from any single candidate site.

    Reuses src.facility_location.total_weighted_distance - every demand served by one site.
    A pure distance reference (capacity/fixed cost are ignored here on purpose).
    """
    return min(
        total_weighted_distance(demands, Location(s.x, s.y)) for s in sites
    )


def _var_index(i: int, j: int, n_s: int) -> int:
    """Flattened index of x_ij (demand i served by site j) given n_s candidate sites.

    Decision variables are laid out as [ y_0..y_{n_s-1}, x_00..x_{n_d-1,n_s-1} ]:
    y_j = 1 if site j is opened; x_ij = 1 if demand i is served by site j.
    """
    return n_s + i * n_s + j


def _assignment_constraint(n_d: int, n_s: int, n_var: int) -> LinearConstraint:
    """Each demand assigned to exactly one site: sum_j x_ij = 1."""
    a = np.zeros((n_d, n_var))
    for i in range(n_d):
        for j in range(n_s):
            a[i, _var_index(i, j, n_s)] = 1.0
    return LinearConstraint(a, lb=1.0, ub=1.0)


def _linking_constraint(n_d: int, n_s: int, n_var: int) -> LinearConstraint:
    """Can only assign to an open site: x_ij - y_j <= 0."""
    n_x = n_d * n_s
    a = np.zeros((n_x, n_var))
    row = 0
    for i in range(n_d):
        for j in range(n_s):
            a[row, _var_index(i, j, n_s)] = 1.0
            a[row, j] = -1.0
            row += 1
    return LinearConstraint(a, lb=-np.inf, ub=0.0)


def _count_constraint(p: int, n_s: int, n_var: int) -> LinearConstraint:
    """Open exactly p sites: sum_j y_j = p."""
    a = np.zeros((1, n_var))
    a[0, :n_s] = 1.0
    return LinearConstraint(a, lb=float(p), ub=float(p))


def _capacity_constraint(
    demands: list[DemandPoint],
    sites: list[CandidateSite],
    n_s: int,
    n_var: int,
    *,
    respect_capacity: bool,
) -> LinearConstraint | None:
    """Capacity, only for sites that declare one: sum_i w_i x_ij - cap_j y_j <= 0.

    Returns None when there is nothing to constrain (respect_capacity=False, or no
    candidate site declares a capacity) - the caller skips appending it in that case.
    """
    cap_rows = [
        (j, s.capacity)
        for j, s in enumerate(sites)
        if respect_capacity and s.capacity is not None
    ]
    if not cap_rows:
        return None
    a = np.zeros((len(cap_rows), n_var))
    for r, (j, cap) in enumerate(cap_rows):
        for i, d in enumerate(demands):
            a[r, _var_index(i, j, n_s)] = d.weight
        a[r, j] = -float(cap)
    return LinearConstraint(a, lb=-np.inf, ub=0.0)


def solve_p_median(
    demands: list[DemandPoint],
    sites: list[CandidateSite],
    p: int,
    *,
    respect_capacity: bool = True,
) -> NetworkDesign:
    """Choose which p of `sites` to open and assign each demand to one open site, minimizing
    total weighted distance (plus any fixed costs), via scipy.optimize.milp.

    Raises ValueError for structurally impossible inputs (no demands/sites, p out of
    1..len(sites)). A run the solver finds infeasible (e.g. capacities too tight for p)
    returns NetworkDesign(feasible=False, ...) rather than raising - the job's verify()
    turns that into a QA failure with a clear message.
    """
    if not demands:
        raise ValueError("at least one demand point is required")
    if not sites:
        raise ValueError("at least one candidate site is required")
    if not 1 <= p <= len(sites):
        raise ValueError(
            f"p must be between 1 and the number of candidate sites ({len(sites)}); got {p}"
        )

    n_d = len(demands)
    n_s = len(sites)
    n_var = n_s + n_d * n_s

    # Objective: sum_j fixed_cost_j y_j + sum_ij w_i d_ij x_ij.
    c = np.zeros(n_var)
    for j, s in enumerate(sites):
        c[j] = s.fixed_cost
    for i, d in enumerate(demands):
        for j, s in enumerate(sites):
            c[_var_index(i, j, n_s)] = d.weight * _distance(d, s)

    constraints: list[LinearConstraint] = [
        _assignment_constraint(n_d, n_s, n_var),
        _linking_constraint(n_d, n_s, n_var),
        _count_constraint(p, n_s, n_var),
    ]
    cap_constraint = _capacity_constraint(
        demands, sites, n_s, n_var, respect_capacity=respect_capacity
    )
    if cap_constraint is not None:
        constraints.append(cap_constraint)

    integrality = np.ones(n_var)        # all variables binary
    bounds = Bounds(lb=0.0, ub=1.0)

    res = milp(c=c, constraints=constraints, integrality=integrality, bounds=bounds)

    baseline = _best_single_facility_distance(demands, sites)

    if not res.success or res.x is None:
        return NetworkDesign(
            feasible=False, p=p, open_sites=(), assignment={},
            total_weighted_distance=math.inf, total_fixed_cost=math.inf,
            objective=math.inf, baseline_distance=baseline,
            saving_vs_baseline=0.0, saving_pct=0.0,
        )

    x = res.x
    open_sites = tuple(sites[j].name for j in range(n_s) if x[j] > 0.5)
    assignment: dict[str, str] = {}
    weighted = 0.0
    for i, d in enumerate(demands):
        j = np.argmax(x[n_s + i * n_s : n_s + (i + 1) * n_s])
        assignment[d.name] = sites[j].name
        weighted += d.weight * _distance(d, sites[j])
    total_fixed = sum(sites[j].fixed_cost for j in range(n_s) if x[j] > 0.5)
    saving = baseline - weighted
    saving_pct = (saving / baseline) if baseline > 0 else 0.0
    return NetworkDesign(
        feasible=True, p=p, open_sites=open_sites, assignment=assignment,
        total_weighted_distance=weighted, total_fixed_cost=total_fixed,
        objective=float(res.fun), baseline_distance=baseline,
        saving_vs_baseline=saving, saving_pct=saving_pct,
    )
