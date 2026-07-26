"""Refuse a number that is not a number, at the boundary where it enters.

``nan > 0`` and ``nan <= 0`` are both False, so the ubiquitous
``value = num / den if den > 0 else FALLBACK`` guard routes a NaN denominator down
the fallback branch in complete silence. The fallbacks in this codebase were not
neutral - they were the figure a well-run operation reports (100% fill rate, 0
defects, infinite inventory turns, zero cost per unit), so nothing downstream ever
looked wrong.

Validating here, once, at the point a client number enters the engine is cheaper
and louder than auditing every arithmetic site that consumes it afterwards. A blank
cell is not a zero; say so and stop.
"""

from __future__ import annotations

import math
from collections.abc import Iterable


def require_finite(value: object, name: str) -> float:
    """Return ``value`` as a float, or raise naming what was not a real number."""
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{name} must be a finite number, got {value!r} - a blank cell is not a zero"
        ) from exc
    if not math.isfinite(number):
        raise ValueError(
            f"{name} must be finite, got {number} - a blank or non-numeric input is an "
            "absence, and would otherwise be scored as a measurement"
        )
    return number


def require_all_finite(values: Iterable[object], name: str) -> list[float]:
    """Validate a sequence, naming the position of the first value that is not a number."""
    out: list[float] = []
    for i, value in enumerate(values):
        out.append(require_finite(value, f"{name}[{i}]"))
    return out


def is_positive(value: object) -> bool:
    """True only for a real, positive number - the guard ``> 0`` alone cannot express."""
    return isinstance(value, (int, float)) and math.isfinite(value) and value > 0
