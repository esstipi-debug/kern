"""S&OP / IBP cadence engine (capability gap #2: the monthly demand->supply review).

Sales & Operations Planning (evolved into Integrated Business Planning) is the
monthly executive cadence that reconciles a demand plan against supply into one
financially-validated operating plan. This module is the analytic core of that
cadence: given a demand plan over a horizon and an opening inventory, it builds the
three classic aggregate-planning supply strategies, projects each one's inventory
balance, and quantifies the cost / service / working-capital trade-offs an exec
team weighs in the reconciliation step.

The three strategies (Chopra & Meindl, *Supply Chain Management*, aggregate
planning; Heizer & Render, "Aggregate Planning and S&OP"):

- **Chase** - flex supply to track demand, holding inventory flat at the target.
  Minimal inventory / working capital, but high capacity change (overtime, hire/
  fire, expedite).
- **Level** - a constant production rate; inventory absorbs the demand swings.
  Minimal capacity change, but inventory (and the cash tied up in it) builds, and
  a front-loaded horizon can dip into shortage.
- **Hybrid** - a tunable blend of the two.

Pure / deterministic (frozen dataclasses + pure functions, no external deps),
mirroring the analytical-core style so it is auditable for the QA gate. The
ranked, never-dead-end option-package is assembled in ``run_sop_cycle`` over the
Guided Execution Layer (``src.decision_options`` / ``src.guided``).
"""

from __future__ import annotations

from dataclasses import dataclass

from src.decision_options import Objective, Scenario, decide
from src.guided import OWNER_HUMAN, GuidedOutcome, Residual, recommend


@dataclass(frozen=True)
class CostModel:
    """Per-unit cost rates for scoring an aggregate plan.

    Defaults make shortage the most painful and holding the cheapest, the usual
    aggregate-planning ordering; ``production_per_unit`` is normally left at 0 since
    every strategy produces the same total over the horizon (so it carries no signal).
    """

    holding_per_unit_per_period: float = 1.0
    shortage_per_unit_per_period: float = 5.0
    capacity_change_per_unit: float = 2.0
    production_per_unit: float = 0.0


@dataclass(frozen=True)
class PeriodPlan:
    """One period's demand/supply balance row."""

    period: str
    demand: float
    production: float
    opening_inventory: float
    closing_inventory: float   # may be negative => that much is short / backordered
    on_hand: float             # max(0, closing) - inventory actually carried
    shortfall: float           # max(0, -closing) - unmet demand that period


@dataclass(frozen=True)
class PlanEvaluation:
    """A fully-costed supply plan: the per-period balance plus its roll-up metrics."""

    name: str
    periods: tuple[PeriodPlan, ...]
    total_demand: float
    total_production: float
    holding_cost: float
    shortage_cost: float
    capacity_change_cost: float
    production_cost: float
    total_cost: float
    peak_inventory: float       # max on-hand across the horizon (working-capital proxy)
    average_inventory: float
    total_shortfall: float
    fill_rate: float            # 1 - shortfall/demand: the share of demand served on time
    capacity_changes: float     # sum of |Delta production| - operational disruption


def _as_floats(demand: object) -> list[float]:
    out = [float(d) for d in demand]  # type: ignore[union-attr]
    if not out:
        raise ValueError("demand horizon is empty")
    return out


def level_plan(demand: object, *, opening_inventory: float = 0.0, target: float = 0.0) -> list[float]:
    """A constant production rate that lands ending inventory on ``target``.

    rate = (total demand + target - opening) / horizon. Inventory absorbs the swings.
    """
    d = _as_floats(demand)
    rate = (sum(d) + target - opening_inventory) / len(d)
    return [rate] * len(d)


def chase_plan(demand: object, *, opening_inventory: float = 0.0, target: float = 0.0) -> list[float]:
    """Track demand so closing inventory sits at ``target`` each period.

    Production is clamped at zero (you cannot un-produce), so a large opening stock
    simply coasts down rather than forcing negative output.
    """
    d = _as_floats(demand)
    plan: list[float] = []
    inv = opening_inventory
    for dt in d:
        produce = max(0.0, dt + target - inv)
        inv = inv + produce - dt
        plan.append(produce)
    return plan


def hybrid_plan(
    demand: object, *, opening_inventory: float = 0.0, target: float = 0.0, mix: float = 0.5
) -> list[float]:
    """Blend chase and level period-by-period. ``mix=1`` is pure chase, ``mix=0`` pure level."""
    chase = chase_plan(demand, opening_inventory=opening_inventory, target=target)
    level = level_plan(demand, opening_inventory=opening_inventory, target=target)
    return [mix * c + (1.0 - mix) * lv for c, lv in zip(chase, level)]


def _capacity_changes(production: list[float], initial_rate: float | None) -> float:
    """Sum of period-to-period |Delta production|.

    With no ``initial_rate`` the first period is the baseline (a plan is not charged
    for existing); otherwise the ramp from the current run-rate is counted too.
    """
    if not production:
        return 0.0
    prev = initial_rate if initial_rate is not None else production[0]
    start = 0 if initial_rate is not None else 1
    total = 0.0
    for value in production[start:]:
        total += abs(value - prev)
        prev = value
    return total


def project_plan(
    demand: object,
    production: object,
    *,
    opening_inventory: float = 0.0,
    name: str = "custom",
    period_labels: object | None = None,
    cost: CostModel | None = None,
    initial_rate: float | None = None,
) -> PlanEvaluation:
    """Roll a demand plan and a production plan into a fully-costed PlanEvaluation."""
    d = _as_floats(demand)
    p = [float(x) for x in production]  # type: ignore[union-attr]
    if len(p) != len(d):
        raise ValueError(f"production length {len(p)} != demand length {len(d)}")
    cost = cost or CostModel()
    labels = list(period_labels) if period_labels is not None else [f"M{i + 1}" for i in range(len(d))]

    periods: list[PeriodPlan] = []
    inv = opening_inventory
    for label, dt, pt in zip(labels, d, p):
        opening = inv
        closing = opening + pt - dt
        periods.append(
            PeriodPlan(
                period=str(label),
                demand=dt,
                production=pt,
                opening_inventory=opening,
                closing_inventory=closing,
                on_hand=max(0.0, closing),
                shortfall=max(0.0, -closing),
            )
        )
        inv = closing

    on_hands = [pp.on_hand for pp in periods]
    total_demand = sum(d)
    total_shortfall = sum(pp.shortfall for pp in periods)
    capacity_changes = _capacity_changes(p, initial_rate)

    holding_cost = cost.holding_per_unit_per_period * sum(on_hands)
    shortage_cost = cost.shortage_per_unit_per_period * total_shortfall
    capacity_change_cost = cost.capacity_change_per_unit * capacity_changes
    production_cost = cost.production_per_unit * sum(p)

    return PlanEvaluation(
        name=name,
        periods=tuple(periods),
        total_demand=total_demand,
        total_production=sum(p),
        holding_cost=holding_cost,
        shortage_cost=shortage_cost,
        capacity_change_cost=capacity_change_cost,
        production_cost=production_cost,
        total_cost=holding_cost + shortage_cost + capacity_change_cost + production_cost,
        peak_inventory=max(on_hands) if on_hands else 0.0,
        average_inventory=sum(on_hands) / len(on_hands) if on_hands else 0.0,
        total_shortfall=total_shortfall,
        fill_rate=(1.0 - total_shortfall / total_demand) if total_demand > 0 else 1.0,
        capacity_changes=capacity_changes,
    )


def build_scenarios(
    demand: object,
    *,
    opening_inventory: float = 0.0,
    target: float = 0.0,
    cost: CostModel | None = None,
    period_labels: object | None = None,
    initial_rate: float | None = None,
    hybrid_mix: float = 0.5,
) -> dict[str, PlanEvaluation]:
    """Evaluate the three aggregate-planning strategies over one demand horizon."""
    cost = cost or CostModel()
    common = dict(
        opening_inventory=opening_inventory,
        cost=cost,
        period_labels=period_labels,
        initial_rate=initial_rate,
    )
    plans = {
        "Chase": chase_plan(demand, opening_inventory=opening_inventory, target=target),
        "Level": level_plan(demand, opening_inventory=opening_inventory, target=target),
        "Hybrid": hybrid_plan(demand, opening_inventory=opening_inventory, target=target, mix=hybrid_mix),
    }
    return {name: project_plan(demand, plan, name=name, **common) for name, plan in plans.items()}


# ── The cadence: rank the strategies into a protected option-package ──────────

# The reconciliation step weighs three things at once: the cost of the plan, the
# working capital it ties up (peak inventory), and the service it delivers.
DEFAULT_OBJECTIVES: tuple[Objective, ...] = (
    Objective("total_cost", weight=1.0, maximize=False),
    Objective("peak_inventory", weight=1.0, maximize=False),
    Objective("fill_rate", weight=1.0, maximize=True),
)

# The irreducible human step: executives own the consensus sign-off and the
# demand-shaping levers (promotions / pricing) that sit outside the supply plan.
_SIGNOFF_RESIDUAL = Residual(
    description=(
        "Executive sign-off on the consensus S&OP plan and any demand-shaping "
        "(promotion / pricing) levers"
    ),
    owner=OWNER_HUMAN,
    risk_if_skipped=(
        "Supply commitments and capacity changes proceed without an agreed, "
        "financially-validated plan - cost and service exposure goes unmanaged"
    ),
)


@dataclass(frozen=True)
class SopReview:
    """The output of one monthly S&OP cycle: the costed strategies plus the ranked,
    never-dead-end option-package the exec team chooses from."""

    summary: str
    evaluations: dict[str, PlanEvaluation]
    outcome: GuidedOutcome
    recommended: PlanEvaluation
    objectives: tuple[Objective, ...]


def _scenario_for(ev: PlanEvaluation) -> Scenario:
    """Map a costed plan onto a decision-options Scenario the ranker can score."""
    return Scenario(
        label=ev.name,
        summary=(
            f"{ev.name}: {ev.fill_rate * 100:.0f}% fill, peak inventory "
            f"{ev.peak_inventory:,.0f} units, total cost {ev.total_cost:,.0f}"
        ),
        metrics={
            "total_cost": ev.total_cost,
            "peak_inventory": ev.peak_inventory,
            "fill_rate": ev.fill_rate,
        },
        action=f"stage:sop-plan-{ev.name.lower()}",
        tradeoffs=(
            f"holding {ev.holding_cost:,.0f} + shortage {ev.shortage_cost:,.0f} + "
            f"capacity-change {ev.capacity_change_cost:,.0f}; "
            f"{ev.capacity_changes:,.0f} units of capacity flex, "
            f"{ev.total_shortfall:,.0f} units short"
        ),
    )


def _cycle_summary(demand: list[float], evals: dict[str, PlanEvaluation]) -> str:
    total = sum(demand)
    rec = min(evals.values(), key=lambda e: e.total_cost)
    return (
        f"S&OP review over {len(demand)} periods of {total:,.0f} units demand: chase / "
        f"level / hybrid supply strategies compared on cost, working capital and service "
        f"(lowest-cost option: {rec.name})."
    )


def run_sop_cycle(
    demand: object,
    *,
    opening_inventory: float = 0.0,
    target: float = 0.0,
    cost: CostModel | None = None,
    objectives: list[Objective] | None = None,
    period_labels: object | None = None,
    initial_rate: float | None = None,
    hybrid_mix: float = 0.5,
    confidence: float = 0.8,
    residuals: list[Residual] | None = None,
    summary: str | None = None,
) -> SopReview:
    """Run one monthly S&OP cadence: build, cost, and rank the supply strategies into
    a protected option-package with a recommended default."""
    objs = list(objectives) if objectives is not None else list(DEFAULT_OBJECTIVES)
    evals = build_scenarios(
        demand,
        opening_inventory=opening_inventory,
        target=target,
        cost=cost,
        period_labels=period_labels,
        initial_rate=initial_rate,
        hybrid_mix=hybrid_mix,
    )
    scenarios = [_scenario_for(evals[name]) for name in ("Chase", "Level", "Hybrid")]
    text = summary or _cycle_summary(_as_floats(demand), evals)
    outcome = decide(
        text,
        scenarios,
        objs,
        confidence=confidence,
        residuals=residuals if residuals is not None else [_SIGNOFF_RESIDUAL],
    )
    recommended = evals[recommend(outcome.options).label]
    return SopReview(
        summary=text,
        evaluations=evals,
        outcome=outcome,
        recommended=recommended,
        objectives=tuple(objs),
    )
