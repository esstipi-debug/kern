"""Decision-options engine (Guided Execution Layer, plan §2.14).

The scenario engine that sits *over* the analytic engines. Each engine proposes a few
executable scenarios (3 reorder plans, 3 supplier awards, ...) with competing metrics;
this module scores their trade-offs multi-objectively, ranks them best-first, and emits
a protected ``as_options`` outcome whose recommended default is the best balance.

Pure (no external deps), mirroring the analytical-core style: frozen dataclasses + pure
functions. The actual write each option points to lives in its ``action`` (a staged
change id), so picking an option stays on the safe-staging writeback plane.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from src.guided import ExecutionOption, GuidedOutcome, Residual, as_options


@dataclass(frozen=True)
class Objective:
    """One scoring dimension. ``maximize=False`` (default) treats the metric as cost-like."""

    metric: str
    weight: float = 1.0
    maximize: bool = False


@dataclass(frozen=True)
class Scenario:
    """One executable choice and the metrics that characterise its trade-offs."""

    label: str
    summary: str
    metrics: dict = field(default_factory=dict)
    action: str = ""        # the ready-to-run action behind it (e.g. a staged change id)
    tradeoffs: str = ""


def _metric_values(scenarios: list[Scenario], metric: str) -> list[float]:
    try:
        return [float(s.metrics[metric]) for s in scenarios]
    except KeyError as exc:
        raise KeyError(f"scenario is missing objective metric {exc}") from exc


def weighted_scores(scenarios: list[Scenario], objectives: list[Objective]) -> list[float]:
    """Min-max normalize each objective across scenarios and return a weighted desirability.

    Higher is better. A constant metric (all scenarios equal) contributes nothing, so it
    cannot skew the ranking.
    """
    totals = [0.0] * len(scenarios)
    for obj in objectives:
        values = _metric_values(scenarios, obj.metric)
        lo, hi = min(values), max(values)
        span = hi - lo
        for i, v in enumerate(values):
            if span == 0:
                continue  # no signal from this metric
            norm = (v - lo) / span                       # 0..1, higher raw value
            desirability = norm if obj.maximize else 1.0 - norm
            totals[i] += obj.weight * desirability
    return totals


def rank(scenarios: list[Scenario], objectives: list[Objective]) -> list[ExecutionOption]:
    """Score and sort scenarios best-first into ExecutionOptions; flag the top one."""
    scores = weighted_scores(scenarios, objectives)
    ranked = sorted(zip(scenarios, scores), key=lambda pair: pair[1], reverse=True)
    options = [
        ExecutionOption(
            label=s.label,
            summary=s.summary,
            score=score,
            action=s.action,
            tradeoffs=s.tradeoffs,
        )
        for s, score in ranked
    ]
    if options:
        options[0] = replace(options[0], recommended=True)
    return options


def decide(
    summary: str,
    scenarios: list[Scenario],
    objectives: list[Objective],
    *,
    confidence: float = 1.0,
    residuals: list[Residual] | None = None,
) -> GuidedOutcome:
    """Turn ranked scenarios into a protected, never-dead-end options outcome."""
    return as_options(
        summary, rank(scenarios, objectives), confidence=confidence, residuals=residuals
    )
