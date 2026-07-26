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

from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import linprog

from src.guided import ExecutionOption, GuidedOutcome, as_options

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
    matrix = np.array([[float(alternatives[a][c.name]) for c in criteria] for a in names])

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
    return RankingResult(scores, ranking)


def award_outcome(
    ranking: RankingResult,
    *,
    summary: str,
    action_prefix: str = "stage:award:",
) -> GuidedOutcome:
    """Present the award as ranked, executable options with the best recommended."""
    options = [
        ExecutionOption(
            label=a,
            summary=f"award to {a} (closeness {ranking.scores[a]:.3f})",
            score=ranking.scores[a],
            action=f"{action_prefix}{a}",
        )
        for a in ranking.ranking
    ]
    return as_options(summary, options)
