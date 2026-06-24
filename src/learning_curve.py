"""Learning- / experience-curve cost model (Jacobs & Chase 15e, ch. 6).

Predicts unit labour-hours/cost as cumulative output grows: Yx = K * x^n, with
n = ln(rate)/ln(2), where ``rate`` is the learning rate (0.80 => each doubling of
cumulative output cuts unit time to 80%). Turns a learning rate into exact future unit and
total-order cost - useful for cost-down forecasting and quotation.
"""

from __future__ import annotations

import math


def learning_exponent(rate: float) -> float:
    """The curve exponent n = ln(rate)/ln(2) for a given learning rate (0 < rate <= 1)."""
    if not 0 < rate <= 1:
        raise ValueError("learning rate must be in (0, 1]")
    return math.log(rate) / math.log(2)


def unit_time(first_unit_time: float, unit_number: int, rate: float) -> float:
    """Time/cost of the x-th unit: Yx = K * x^n."""
    if unit_number < 1:
        raise ValueError("unit_number must be >= 1")
    return first_unit_time * unit_number ** learning_exponent(rate)


def cumulative_time(first_unit_time: float, n_units: int, rate: float) -> float:
    """Total time/cost to produce the first ``n_units`` (sum of the unit curve)."""
    if n_units < 0:
        raise ValueError("n_units must be >= 0")
    n = learning_exponent(rate)
    return sum(first_unit_time * x ** n for x in range(1, n_units + 1))
