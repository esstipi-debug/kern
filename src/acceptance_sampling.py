"""Acceptance sampling: single-plan design + inspection break-even (Jacobs & Chase 15e, ch.13).

Turns producer/consumer risk targets into an inspect-``n`` / accept-``c`` receiving rule, and
decides when 100% inspection is economically justified. The plan search computes the binomial
OC curve directly (no table lookup), so it is deterministic and auditable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


def prob_accept(n: int, c: int, defect_rate: float) -> float:
    """Probability a lot of fraction-defective ``defect_rate`` is accepted by plan (n, c):
    P(defects in sample <= c) under the binomial."""
    if n < 0 or c < 0:
        raise ValueError("n and c must be >= 0")
    if not 0 <= defect_rate <= 1:
        raise ValueError("defect_rate must be in [0, 1]")
    c = min(c, n)
    return sum(
        math.comb(n, k) * defect_rate ** k * (1 - defect_rate) ** (n - k)
        for k in range(c + 1)
    )


@dataclass(frozen=True)
class SamplingPlan:
    n: int                  # sample size
    c: int                  # acceptance number
    prob_accept_aql: float  # >= 1 - producer_risk
    prob_accept_ltpd: float  # <= consumer_risk


def design_single_sampling_plan(
    aql: float,
    ltpd: float,
    *,
    producer_risk: float = 0.05,
    consumer_risk: float = 0.10,
    max_n: int = 1500,
) -> SamplingPlan:
    """Smallest single sampling plan (n, c) meeting both risks.

    Producer protected: P(accept | AQL) >= 1 - producer_risk. Consumer protected:
    P(accept | LTPD) <= consumer_risk. Searches n upward; for each n takes the smallest c
    that meets the producer risk, then checks the consumer risk.
    """
    if not 0 < aql < ltpd < 1:
        raise ValueError("require 0 < aql < ltpd < 1")
    for n in range(1, max_n + 1):
        for c in range(n + 1):
            if prob_accept(n, c, aql) >= 1 - producer_risk:
                pa_ltpd = prob_accept(n, c, ltpd)
                if pa_ltpd <= consumer_risk:
                    return SamplingPlan(n, c, prob_accept(n, c, aql), pa_ltpd)
                break  # larger c only raises P(accept|LTPD); this n cannot work
    raise ValueError(f"no plan found within max_n={max_n}; loosen the risks or raise max_n")


def inspect_all_cheaper(
    unit_inspection_cost: float, defect_rate: float, downstream_failure_cost: float
) -> bool:
    """100% inspection pays iff unit inspection cost < defect_rate * downstream failure cost."""
    return unit_inspection_cost < defect_rate * downstream_failure_cost
