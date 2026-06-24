"""Earned Value Management (Jacobs & Chase 15e, ch. 4).

Objective project / initiative cost-and-schedule control: compares the budgeted cost of
work *performed* against work *scheduled* and the actual cost, yielding the four standard
scalar metrics. Pure arithmetic, fully auditable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class EarnedValue:
    """The four EVM metrics from BCWS (planned), BCWP (earned), and AC (actual)."""

    planned: float
    earned: float
    actual: float
    schedule_variance: float    # SV = earned - planned (<0 = behind)
    cost_variance: float        # CV = earned - actual  (<0 = over budget)
    spi: float                  # schedule performance index = earned / planned
    cpi: float                  # cost performance index = earned / actual

    @property
    def behind_schedule(self) -> bool:
        return self.schedule_variance < 0

    @property
    def over_budget(self) -> bool:
        return self.cost_variance < 0


def earned_value(planned: float, earned: float, actual: float) -> EarnedValue:
    """Compute SV, CV, SPI, CPI from BCWS / BCWP / AC."""
    return EarnedValue(
        planned=planned,
        earned=earned,
        actual=actual,
        schedule_variance=earned - planned,
        cost_variance=earned - actual,
        spi=earned / planned if planned else math.inf,
        cpi=earned / actual if actual else math.inf,
    )
