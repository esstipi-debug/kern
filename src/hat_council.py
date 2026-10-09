"""Hat council -- N4 tension map and N5 settlement over the shared substrate.

Pure analysis layer: no scm_agent imports (D7), no I/O, no GuidedOutcome
assembly (jobs/hats_job.py does that). Everything is deterministic: one
`evaluate()` per SKU feeds both levels, and every selection goes through
src.hats.select_best_index's shared tie-break (spec sec 6).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from itertools import combinations

from src.constraints import lot_rule
from src.hats import (
    HAT_CFO,
    HAT_COMERCIAL,
    HAT_COMPRADOR,
    HAT_KEYS,
    HAT_PLANNER,
    HAT_TRANSPORTE,
    Candidate,
    GridEvaluation,
    HatEvaluation,
    HatInputs,
    baseline_plan,
    decision_cost,
    evaluable_hats,
    evaluate,
    hat_kpis,
    headline_kpi,
    parse_weights,
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
    """N4: the disagreement, rendered. No reconciliation here -- a human resolves.

    `ideals` holds one entry per EVALUABLE hat, so its size is 4 or 5 and the
    clash count is C(n,2) -- 6 or 10. `excluded_hats` names the areas that were
    not on the table; it is a field rather than something each renderer re-derives
    precisely so a deliverable cannot forget to say it.
    """

    sku: str
    ideals: dict[str, HatEvaluation]
    clashes: tuple[Clash, ...]
    candidates_evaluated: int
    excluded_hats: tuple[str, ...] = ()
    #: Supplier terms the grid was laid over (0.0 = none). Carried on the map so
    #: the QA gate can check every ideal is a lot the supplier accepts without
    #: needing the inputs back, and so a deck can say the axis was constrained.
    minimum_order_quantity: float = 0.0
    order_multiple: float = 0.0

    @property
    def lot_rule(self) -> str:
        return lot_rule(self.minimum_order_quantity, self.order_multiple)


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
    """Every evaluable hat's ideal + all its pairwise clashes, sorted by $
    magnitude desc (stable: the fixed HAT_KEYS pair order breaks exact-magnitude
    ties). 4 hats -> 6 clashes; 5 -> 10."""
    ev = ev if ev is not None else evaluate(inputs)
    hats = evaluable_hats(inputs)
    ideals = {k: ideal_for(inputs, ev, k) for k in hats}
    clashes: list[Clash] = []
    for a, b in combinations(hats, 2):
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
        excluded_hats=tuple(k for k in HAT_KEYS if k not in hats),
        minimum_order_quantity=inputs.minimum_order_quantity,
        order_multiple=inputs.order_multiple,
    )


@dataclass(frozen=True)
class ActaEntry:
    """One hat's line in the concession record: where it wanted to be, how much
    of its ideal it keeps at the chosen plan, and its own KPI at both points
    (units per src.hats.headline_kpi)."""

    hat_key: str
    ideal: Candidate
    utility_norm_at_chosen: float
    concesion: float              # 1 - utility_norm_at_chosen, in [0, 1]
    kpi_ideal: float
    kpi_chosen: float


#: A hat conceding more than this much of its ideal is not "aligned with some
#: give" -- it lost the argument, and an acta that folds that into an average is
#: hiding the disagreement it exists to record. Half its ideal utility is the
#: line: below it the hat still recognises the plan as broadly its own.
UNRESOLVED_CONCESSION_THRESHOLD = 0.5

#: What the owner field says when nobody was named. An empty string reads as
#: "already assigned, name omitted"; this reads as what it is.
UNASSIGNED = "unassigned"


@dataclass(frozen=True)
class UnresolvedGap:
    """A hat that gave up more than the threshold: the disagreement the acta must
    carry forward instead of settling. Named, quantified and traceable to its
    acta line, so the next S&OP can reopen it rather than rediscover it."""

    hat_key: str
    concesion: float
    kpi_ideal: float
    kpi_chosen: float


@dataclass(frozen=True)
class Settlement:
    """N5: one reconciled plan + the acta. `weights` is the operator's POLICY
    (D4) -- normalized here, echoed everywhere the settlement is rendered.
    `value_vs_baseline_usd` is SIGNED: negative under extreme weight policies
    is information (what that policy costs), reported without makeup."""

    sku: str
    chosen: Candidate
    weights: dict[str, float]
    acta: tuple[ActaEntry, ...]
    judge_cost_chosen: float
    judge_cost_baseline: float
    value_vs_baseline_usd: float
    #: The demand the council settled ON. Without it the acta records what was
    #: agreed but not what it was agreed about, and the next cycle cannot tell a
    #: plan change from a demand change.
    agreed_demand_plan: float = 0.0
    #: Concessions above the threshold, declared instead of averaged away.
    unresolved_gaps: tuple[UnresolvedGap, ...] = ()
    #: Areas NOT on the table (no inputs to score them with). An acta that omits
    #: this reads as unanimity among five when it was unanimity among four.
    excluded_hats: tuple[str, ...] = ()
    #: Who owns the decision and when it was taken. A plan nobody signed is a
    #: suggestion; the criterion asks for a decision, which needs both.
    decision_owner: str = UNASSIGNED
    decision_date: date | None = None
    #: Supplier terms the plan was settled under (0.0 = none); see TensionMap.
    minimum_order_quantity: float = 0.0
    order_multiple: float = 0.0

    @property
    def lot_rule(self) -> str:
        return lot_rule(self.minimum_order_quantity, self.order_multiple)


@dataclass(frozen=True)
class ValueRow:
    """One row of the spec sec 9 value table (all judge costs, $/year)."""

    sku: str
    c_baseline: float
    c_comprador: float
    c_planner: float
    c_cfo: float
    c_comercial: float
    c_n5: float
    delta_usd: float              # c_baseline - c_n5, signed
    #: None when transporte was not evaluable -- never 0.0, which in a column of
    #: judge costs would read as a free policy rather than an absent one.
    c_transporte: float | None = None


def settle(
    inputs: HatInputs,
    weights: str | dict | None,
    ev: GridEvaluation | None = None,
    *,
    owner: str = UNASSIGNED,
    decided_on: date | None = None,
) -> Settlement:
    """Weighted sum of NORMALIZED utilities over the grid, shared tie-break.

    The weights are re-validated/normalized here (parse_weights) so every
    caller path shares one validation. With w_x = 1 the score equals hat x's
    normalized utility, so the settlement collapses exactly onto x's ideal
    (same argmax, same tie-break).
    """
    hats = evaluable_hats(inputs)
    w = parse_weights(weights, hats)
    ev = ev if ev is not None else evaluate(inputs)
    scores = tuple(
        sum(w[k] * ev.utilities_norm[k][i] for k in hats)
        for i in range(len(ev.candidates))
    )
    idx = select_best_index(scores, ev.judge_costs, ev.candidates)
    chosen = ev.candidates[idx]
    ideals = {k: ideal_for(inputs, ev, k) for k in hats}
    acta = tuple(
        ActaEntry(
            hat_key=k,
            ideal=ideals[k].candidate,
            utility_norm_at_chosen=ev.utilities_norm[k][idx],
            concesion=1.0 - ev.utilities_norm[k][idx],
            kpi_ideal=ideals[k].kpis[headline_kpi(k)],
            kpi_chosen=hat_kpis(inputs, k, chosen)[headline_kpi(k)],
        )
        for k in hats
    )
    c_base = decision_cost(inputs, baseline_plan(inputs))
    c_chosen = ev.judge_costs[idx]
    return Settlement(
        sku=inputs.sku, chosen=chosen, weights=w, acta=acta,
        judge_cost_chosen=c_chosen, judge_cost_baseline=c_base,
        value_vs_baseline_usd=c_base - c_chosen,
        agreed_demand_plan=inputs.annual_demand,
        unresolved_gaps=tuple(
            UnresolvedGap(e.hat_key, e.concesion, e.kpi_ideal, e.kpi_chosen)
            for e in acta
            if e.concesion > UNRESOLVED_CONCESSION_THRESHOLD
        ),
        decision_owner=owner or UNASSIGNED,
        decision_date=decided_on or date.today(),
        excluded_hats=tuple(k for k in HAT_KEYS if k not in hats),
        minimum_order_quantity=inputs.minimum_order_quantity,
        order_multiple=inputs.order_multiple,
    )


def top1_by_judge(inputs: HatInputs, tmap: TensionMap) -> Candidate:
    """N4's top-1: cheapest by judge among the evaluable ideals + the baseline --
    the informational ranking the OPTIONS outcome scores by (spec sec 7)."""
    cands = tuple(
        tmap.ideals[k].candidate for k in HAT_KEYS if k in tmap.ideals
    ) + (baseline_plan(inputs),)
    costs = tuple(decision_cost(inputs, c) for c in cands)
    idx = select_best_index(tuple(-c for c in costs), costs, cands)
    return cands[idx]


def agreement_at_1(pairs: list[tuple[Candidate, Candidate]]) -> float:
    """Fraction of SKUs where N4's top-1 (by judge) coincides with the N5
    settlement. Both come from the same grid, so exact equality is meaningful.
    This is the declared offline proxy for the human choice (spec sec 9)."""
    if not pairs:
        raise ValueError("agreement_at_1 needs at least one (top1, chosen) pair")
    hits = sum(
        1 for top1, chosen in pairs
        if top1.order_quantity == chosen.order_quantity
        and top1.service_level == chosen.service_level
    )
    return hits / len(pairs)


def value_row(inputs: HatInputs, tmap: TensionMap, settlement: Settlement) -> ValueRow:
    """One spec sec 9 row: judge cost at baseline (a), at each hat's ideal
    (comercial is (b)), at the settlement (c), and Delta $ = a - c."""
    c = {k: decision_cost(inputs, e.candidate) for k, e in tmap.ideals.items()}
    return ValueRow(
        sku=inputs.sku,
        c_baseline=settlement.judge_cost_baseline,
        c_comprador=c[HAT_COMPRADOR],
        c_planner=c[HAT_PLANNER],
        c_cfo=c[HAT_CFO],
        c_comercial=c[HAT_COMERCIAL],
        c_n5=settlement.judge_cost_chosen,
        delta_usd=settlement.judge_cost_baseline - settlement.judge_cost_chosen,
        c_transporte=c.get(HAT_TRANSPORTE),
    )
