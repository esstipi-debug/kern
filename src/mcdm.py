"""Multi-criteria supplier selection (M8, plan §2.6).

Two auditable building blocks on base deps only (numpy + scipy - no optional MCDM
library required):

* ``bwm_weights`` - the **Best-Worst Method** (Rezaei 2015, linear model): derive
  criteria weights from a best-to-others and an others-to-worst comparison vector by
  minimizing the maximum consistency deviation via ``scipy.optimize.linprog``. Returns
  the weights, the optimal deviation ``xi``, and the consistency ratio.
* ``topsis_rank`` - classic vector-normalized **TOPSIS**: rank alternatives by their
  closeness to the ideal solution, honoring benefit/cost criteria.

``award_outcome`` maps a ranking to a protected ``as_options`` outcome so the award
decision is presented as ranked, executable options with the best recommended - never a
dead end (Guided Execution Layer, §2.14).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import linprog

from nosilo_core.guided import (
    COVERAGE_PENALTY,
    ExecutionOption,
    GuidedOutcome,
    as_options,
    confidence_from_coverage,
)

# Rezaei's consistency index by a_BW (how many times the best beats the worst).
_BWM_CI = {1: 0.0, 2: 0.44, 3: 1.0, 4: 1.63, 5: 2.30, 6: 3.0, 7: 3.73, 8: 4.47, 9: 5.23}
_BWM_CI_MAX = 5.23


@dataclass(frozen=True)
class Criterion:
    """A decision criterion. ``benefit=True`` means higher is better; cost otherwise."""

    name: str
    benefit: bool = True


@dataclass(frozen=True)
class BWMResult:
    weights: dict[str, float]
    xi: float                       # optimal max consistency deviation (0 == perfectly consistent)
    consistency_ratio: float        # xi / CI(a_BW); lower is better


@dataclass(frozen=True)
class RankingResult:
    scores: dict[str, float]        # alternative -> closeness coefficient in [0,1]
    ranking: list[str] = field(default_factory=list)  # best-first
    # Criteria dropped because every alternative scored the same on them: they carried
    # weight but could not discriminate. Callers must disclose these in the
    # deliverable -- a decision presented as "balanced across N criteria" when it
    # actually rested on fewer is the bug this field exists to prevent.
    ignored_criteria: tuple[str, ...] = ()

    @property
    def best(self) -> str:
        return self.ranking[0]


def bwm_weights(
    best: str,
    worst: str,
    best_to_others: dict[str, float],
    others_to_worst: dict[str, float],
    *,
    criteria: list[str],
) -> BWMResult:
    """Best-Worst Method weights via the linear program (minimize max deviation)."""
    if best not in criteria or worst not in criteria:
        raise ValueError("best and worst must both be in criteria")
    if set(best_to_others) != set(criteria) or set(others_to_worst) != set(criteria):
        raise ValueError("comparison vectors must cover exactly the criteria")

    n = len(criteria)
    idx = {c: i for i, c in enumerate(criteria)}
    jb, jw = idx[best], idx[worst]

    # Variables: w_0..w_{n-1}, xi. Minimize xi.
    c_obj = np.zeros(n + 1)
    c_obj[-1] = 1.0

    rows: list[np.ndarray] = []
    for cj in criteria:
        j = idx[cj]
        a_bj = best_to_others[cj]
        a_jw = others_to_worst[cj]
        # |w_B - a_bj * w_j| <= xi  ->  two rows (each side of the absolute value)
        for sign in (1.0, -1.0):
            row = np.zeros(n + 1)
            row[jb] += sign
            row[j] -= sign * a_bj
            row[-1] = -1.0
            rows.append(row)
        # |w_j - a_jw * w_W| <= xi
        for sign in (1.0, -1.0):
            row = np.zeros(n + 1)
            row[j] += sign
            row[jw] -= sign * a_jw
            row[-1] = -1.0
            rows.append(row)

    a_ub = np.array(rows)
    b_ub = np.zeros(len(rows))
    a_eq = np.array([np.append(np.ones(n), 0.0)])  # sum(w) = 1
    b_eq = np.array([1.0])
    bounds = [(0.0, None)] * (n + 1)

    res = linprog(c_obj, A_ub=a_ub, b_ub=b_ub, A_eq=a_eq, b_eq=b_eq, bounds=bounds, method="highs")
    if not res.success:
        raise ValueError(f"BWM linear program failed: {res.message}")

    w = res.x[:n]
    xi = float(res.x[-1])
    weights = {c: float(w[idx[c]]) for c in criteria}
    ci = _BWM_CI.get(int(round(best_to_others[worst])), _BWM_CI_MAX)
    cr = xi / ci if ci > 0 else 0.0
    return BWMResult(weights, xi, cr)


def topsis_rank(
    alternatives: dict[str, dict[str, float]],
    criteria: list[Criterion],
    weights: dict[str, float],
) -> RankingResult:
    """Rank alternatives by classic vector-normalized TOPSIS closeness (best-first)."""
    if not alternatives:
        raise ValueError("no alternatives to rank")

    names = list(alternatives)
    full_matrix = np.array([[float(alternatives[a][c.name]) for c in criteria] for a in names])

    # Dispersion gate: a criterion identical across every alternative contributes the
    # same offset to all of them, so it cannot change the ranking -- it only makes the
    # comparison look broader than it is. Drop it, renormalize, and report it.
    if len(names) > 1:
        spread_per_criterion = full_matrix.max(axis=0) - full_matrix.min(axis=0)
        discriminates = ~np.isclose(spread_per_criterion, 0.0)
    else:
        discriminates = np.ones(len(criteria), dtype=bool)
    ignored = tuple(c.name for c, keep in zip(criteria, discriminates) if not keep)
    if not discriminates.any():
        raise ValueError(
            "no criterion can discriminate between the alternatives: every one of "
            f"{[c.name for c in criteria]} is identical across all {len(names)} "
            "alternatives, so any ranking would be arbitrary (insertion order). "
            "Check that the scoring inputs were actually populated."
        )
    criteria = [c for c, keep in zip(criteria, discriminates) if keep]
    matrix = full_matrix[:, discriminates]

    w = np.array([weights[c.name] for c in criteria], dtype=float)
    if w.sum() <= 0:
        raise ValueError("weights must be positive")
    w = w / w.sum()

    denom = np.sqrt((matrix**2).sum(axis=0))
    denom[denom == 0] = 1.0
    weighted = (matrix / denom) * w

    benefit = np.array([c.benefit for c in criteria])
    col_max, col_min = weighted.max(axis=0), weighted.min(axis=0)
    ideal_best = np.where(benefit, col_max, col_min)
    ideal_worst = np.where(benefit, col_min, col_max)

    d_best = np.sqrt(((weighted - ideal_best) ** 2).sum(axis=1))
    d_worst = np.sqrt(((weighted - ideal_worst) ** 2).sum(axis=1))
    spread = d_best + d_worst
    spread[spread == 0] = 1.0
    closeness = d_worst / spread

    scores = {names[i]: float(closeness[i]) for i in range(len(names))}
    ranking = sorted(names, key=lambda a: scores[a], reverse=True)
    return RankingResult(scores, ranking, ignored_criteria=ignored)


# ── An unmeasured criterion is a different gap, and it needs its own name ───────────
# `confidence_from_coverage` raises KeyError on a gap name it does not know, on purpose:
# "so a typo cannot silently mean no penalty". The table it checks lives in nosilo_core,
# which ships here as a built dependency with no source checkout on the machines that
# build Kern, so the name has to be registered from this side.
#
# Registering is deliberately NOT forking. The penalty stays in the one table every
# caller reads, so there is a single place to change the number. The alternative --
# subtracting our own constant after calling the helper -- would be a second
# implementation of the same rule, and contracts/README.md is explicit about what two
# copies of one rule do: they drift apart without anything noticing.
#
# `setdefault`, not assignment: if nosilo_core ever ships this gap itself, its value
# wins and this line quietly becomes a no-op instead of silently overriding upstream.
#
# 0.20 sits above `ignored_criterion` (0.15) and level with
# `synthesized_critical_column`, because it is the same kind of hole. A tie IS
# information -- it says the alternatives are indistinguishable on that criterion. A
# criterion nobody measured is the absence of information, and an award that rests on
# fewer measured criteria than it weighed is less certain than one that does not.
UNMEASURED_CRITERION = "unmeasured_criterion"
COVERAGE_PENALTY.setdefault(UNMEASURED_CRITERION, 0.20)


def award_outcome(
    ranking: RankingResult,
    *,
    summary: str,
    action_prefix: str = "stage:award:",
    unmeasured: Sequence[str] = (),
) -> GuidedOutcome:
    """Present the award as ranked, executable options with the best recommended.

    ``unmeasured`` names the criteria this data never carried, which :func:`topsis_rank`
    never sees: it is handed only the criteria that survived. They are a real coverage
    hole and they belong in the confidence, which is why the caller passes them in
    rather than the ranking carrying them.
    """
    options = [
        ExecutionOption(
            label=a,
            summary=f"award to {a} (closeness {ranking.scores[a]:.3f})",
            score=ranking.scores[a],
            action=f"{action_prefix}{a}",
        )
        for a in ranking.ranking
    ]
    # An award decided on fewer criteria than it was asked to weigh is less certain
    # than one decided on all of them; the default 1.0 claimed otherwise.
    #
    # Both holes count, and they are not the same hole. A criterion every alternative
    # tied on was measured and did not discriminate; one in `unmeasured` was never
    # measured at all. Passing only the first is how this outcome reported the SAME
    # confidence for a panel scored on five criteria and one scored on three.
    confidence = confidence_from_coverage(
        ["ignored_criterion"] * len(ranking.ignored_criteria)
        + [UNMEASURED_CRITERION] * len(unmeasured)
    )
    return as_options(summary, options, confidence=confidence)


# ── Multi-sourcing constrained allocation (linear programming) ───────────────────


@dataclass(frozen=True)
class SupplierAllocation:
    supplier: str
    units: float
    share_pct: float
    unit_cost: float
    total_cost: float
    topsis_score: float


@dataclass(frozen=True)
class MultiSourcingPlan:
    allocations: tuple[SupplierAllocation, ...]
    total_units: float
    total_cost: float
    weighted_topsis_score: float
    single_source_cost: float
    diversification_premium_usd: float
    diversification_premium_pct: float
    single_source_winner: str


def solve_multi_sourcing(
    demand: float,
    suppliers: list[str],
    unit_costs: dict[str, float],
    topsis_scores: dict[str, float],
    *,
    capacities: dict[str, float] | None = None,
    max_share_pct: float = 0.70,
    tradeoff_lambda: float = 0.0,
) -> MultiSourcingPlan:
    """Solve the bounded multi-sourcing allocation via linear programming.

    Parameters:
      demand: Total units required to procure (must be > 0).
      suppliers: List of supplier identifiers to consider.
      unit_costs: Supplier unit price or landed cost.
      topsis_scores: Supplier multi-criteria performance score in [0, 1].
      capacities: Maximum units each supplier can provide (None = unlimited).
      max_share_pct: Maximum fraction of demand allowed for any single supplier (e.g. 0.70).
      tradeoff_lambda: Weight given to TOPSIS score vs unit cost in objective.
        Objective: minimize sum_i (unit_cost_i - tradeoff_lambda * topsis_score_i) * x_i.
    """
    if demand <= 0:
        raise ValueError(f"demand must be positive, got {demand}")
    if not suppliers:
        raise ValueError("at least one supplier is required")
    if not (0.0 < max_share_pct <= 1.0):
        raise ValueError(f"max_share_pct must be in (0, 1], got {max_share_pct}")

    n = len(suppliers)
    for s in suppliers:
        if s not in unit_costs:
            raise ValueError(f"missing unit_cost for supplier {s!r}")
        if s not in topsis_scores:
            raise ValueError(f"missing topsis_score for supplier {s!r}")

    # Objective coefficients: effective cost per unit
    c_eff = [
        unit_costs[s] - (tradeoff_lambda * topsis_scores[s])
        for s in suppliers
    ]

    # Constraint: sum(x_i) == demand
    A_eq = [[1.0] * n]
    b_eq = [demand]

    # Upper bounds: min(capacity, max_share * demand)
    bounds = []
    max_units = max_share_pct * demand
    for s in suppliers:
        cap = capacities.get(s, float("inf")) if capacities else float("inf")
        ub = min(cap, max_units)
        bounds.append((0.0, ub))

    # Check total available theoretical capacity
    if sum(ub for _, ub in bounds) < demand - 1e-9:
        raise ValueError(
            f"infeasible allocation: maximum combined capacity of suppliers ({sum(ub for _, ub in bounds):.1f}) "
            f"cannot satisfy total demand ({demand:.1f}) under max_share_pct={max_share_pct:.2f}"
        )

    res = linprog(c_eff, A_eq=A_eq, b_eq=b_eq, bounds=bounds, method="highs")
    if not res.success:
        raise ValueError(f"multi-sourcing solver failed: {res.message}")

    # Determine single source baseline winner (best TOPSIS score)
    single_winner = max(suppliers, key=lambda s: topsis_scores[s])
    single_source_cost = demand * unit_costs[single_winner]

    allocations_list = []
    total_cost = 0.0
    weighted_score_sum = 0.0

    for i, s in enumerate(suppliers):
        qty = float(res.x[i])
        if qty > 1e-6:
            share = qty / demand
            cost = qty * unit_costs[s]
            total_cost += cost
            weighted_score_sum += qty * topsis_scores[s]
            allocations_list.append(
                SupplierAllocation(
                    supplier=s,
                    units=qty,
                    share_pct=share,
                    unit_cost=unit_costs[s],
                    total_cost=cost,
                    topsis_score=topsis_scores[s],
                )
            )

    # Sort allocations: largest allocation first
    allocations_list.sort(key=lambda a: a.units, reverse=True)

    diversification_premium = total_cost - single_source_cost
    diversification_pct = (diversification_premium / single_source_cost) if single_source_cost > 0 else 0.0

    return MultiSourcingPlan(
        allocations=tuple(allocations_list),
        total_units=demand,
        total_cost=total_cost,
        weighted_topsis_score=weighted_score_sum / demand,
        single_source_cost=single_source_cost,
        diversification_premium_usd=diversification_premium,
        diversification_premium_pct=diversification_pct,
        single_source_winner=single_winner,
    )
