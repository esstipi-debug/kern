"""Hat council -- N4 tension map and N5 settlement over the shared substrate.

Pure analysis layer: no scm_agent imports (D7), no I/O, no GuidedOutcome
assembly (jobs/hats_job.py does that). Everything is deterministic: one
`evaluate()` per SKU feeds both levels, and every selection goes through
src.hats.select_best_index's shared tie-break (spec sec 6).
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

from src.hats import (
    HAT_CFO,
    HAT_COMERCIAL,
    HAT_KEYS,
    GridEvaluation,
    HatEvaluation,
    HatInputs,
    evaluate,
    hat_kpis,
    select_best_index,
)


@dataclass(frozen=True)
class Clash:
    """How far apart two hats' ideals sit, in decision units and in $."""

    hat_a: str
    hat_b: str
    delta_q: float                # Q_a - Q_b (signed, units)
    delta_capital_usd: float      # avg inventory value $ at a's ideal minus at b's
    delta_fill_rate: float        # fill rate at a's ideal minus at b's


@dataclass(frozen=True)
class TensionMap:
    """N4: the disagreement, rendered. No reconciliation here -- a human resolves."""

    sku: str
    ideals: dict[str, HatEvaluation]
    clashes: tuple[Clash, ...]
    candidates_evaluated: int


def ideal_for(inputs: HatInputs, ev: GridEvaluation, hat_key: str) -> HatEvaluation:
    """argmax of the hat's normalized utility with the shared tie-break."""
    idx = select_best_index(ev.utilities_norm[hat_key], ev.judge_costs, ev.candidates)
    cand = ev.candidates[idx]
    return HatEvaluation(
        hat_key=hat_key, candidate=cand,
        utility_raw=ev.utilities_raw[hat_key][idx],
        utility_norm=ev.utilities_norm[hat_key][idx],
        kpis=hat_kpis(inputs, hat_key, cand),
    )


def tension_map(inputs: HatInputs, ev: GridEvaluation | None = None) -> TensionMap:
    """All 4 ideals + all 6 pairwise clashes, sorted by $ magnitude desc
    (stable: the fixed HAT_KEYS pair order breaks exact-magnitude ties)."""
    ev = ev if ev is not None else evaluate(inputs)
    ideals = {k: ideal_for(inputs, ev, k) for k in HAT_KEYS}
    clashes: list[Clash] = []
    for a, b in combinations(HAT_KEYS, 2):
        ca, cb = ideals[a].candidate, ideals[b].candidate
        cap_a = hat_kpis(inputs, HAT_CFO, ca)["avg_inventory_usd"]
        cap_b = hat_kpis(inputs, HAT_CFO, cb)["avg_inventory_usd"]
        fill_a = hat_kpis(inputs, HAT_COMERCIAL, ca)["fill_rate"]
        fill_b = hat_kpis(inputs, HAT_COMERCIAL, cb)["fill_rate"]
        clashes.append(Clash(
            hat_a=a, hat_b=b,
            delta_q=ca.order_quantity - cb.order_quantity,
            delta_capital_usd=cap_a - cap_b,
            delta_fill_rate=fill_a - fill_b,
        ))
    pair_rank = {(c.hat_a, c.hat_b): i for i, c in enumerate(clashes)}
    clashes.sort(key=lambda c: (-abs(c.delta_capital_usd), pair_rank[(c.hat_a, c.hat_b)]))
    return TensionMap(
        sku=inputs.sku, ideals=ideals, clashes=tuple(clashes),
        candidates_evaluated=len(ev.candidates),
    )
