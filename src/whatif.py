"""What-if / sensitivity engine (efficacy roadmap #4 - 2026 table-stakes).

Sits over any pure model ``inputs -> outputs``. Where ``decision_options`` ranks a
fixed set of executable plans, this answers the orthogonal question: *how do the
outputs move when the assumptions move?* It emits one-way sensitivity (tornado),
best/worst corner bundles, and break-even thresholds - the auditable "why this
decision is robust" layer that 2026 SCM buyers expect.

Pure (no external deps): frozen dataclasses + pure functions, mirroring
``decision_options`` and ``guided``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

# A model maps named assumptions to named KPIs. Any analytic core can be wrapped as one.
Model = Callable[[Mapping[str, float]], Mapping[str, float]]


@dataclass(frozen=True)
class Driver:
    """One assumption to sweep, with the low/high band that frames the what-if."""

    name: str
    base: float
    low: float
    high: float
    unit: str = ""

    def __post_init__(self) -> None:
        if self.low > self.high:
            raise ValueError(
                f"driver {self.name!r}: low ({self.low}) must not exceed high ({self.high})"
            )


@dataclass(frozen=True)
class OneWay:
    """How one metric moves when a single driver swings low->high (others held at base)."""

    driver: str
    metric: str
    base_output: float
    low_output: float
    high_output: float

    @property
    def swing(self) -> float:
        """Total spread of the metric across the driver's band (the tornado bar length)."""
        return abs(self.high_output - self.low_output)

    @property
    def low_delta(self) -> float:
        return self.low_output - self.base_output

    @property
    def high_delta(self) -> float:
        return self.high_output - self.base_output


@dataclass(frozen=True)
class ScenarioCase:
    """A named bundle of input overrides applied on top of the base case."""

    label: str
    overrides: dict = field(default_factory=dict)


@dataclass(frozen=True)
class CaseResult:
    """The fully-resolved inputs and the KPIs they produce for one scenario."""

    label: str
    inputs: dict
    outputs: dict


@dataclass(frozen=True)
class BreakEven:
    """The driver value at which a metric crosses a target, if one exists in band."""

    driver: str
    metric: str
    target: float
    value: float | None
    found: bool
    bracket: tuple[float, float]


def _metric_at(model: Model, inputs: Mapping[str, float], name: str, value: float, metric: str) -> float:
    """Evaluate ``metric`` with ``name`` pinned to ``value`` and everything else at base."""
    outputs = model({**inputs, name: value})
    if metric not in outputs:
        raise KeyError(metric)
    return float(outputs[metric])


def one_way(model: Model, base_inputs: Mapping[str, float], driver: Driver, metric: str) -> OneWay:
    """Sweep a single driver low->high and report the metric at base/low/high."""
    base_out = _metric_at(model, base_inputs, driver.name, driver.base, metric)
    low_out = _metric_at(model, base_inputs, driver.name, driver.low, metric)
    high_out = _metric_at(model, base_inputs, driver.name, driver.high, metric)
    return OneWay(driver.name, metric, base_out, low_out, high_out)


def tornado(
    model: Model, base_inputs: Mapping[str, float], drivers: list[Driver], metric: str
) -> list[OneWay]:
    """One-way sweep for every driver, ranked by swing descending (tornado-chart order)."""
    rows = [one_way(model, base_inputs, d, metric) for d in drivers]
    return sorted(rows, key=lambda r: r.swing, reverse=True)


def evaluate_cases(
    model: Model, base_inputs: Mapping[str, float], cases: list[ScenarioCase]
) -> list[CaseResult]:
    """Resolve each named override bundle against the base and evaluate its KPIs."""
    results: list[CaseResult] = []
    for case in cases:
        merged = {**base_inputs, **case.overrides}
        results.append(CaseResult(case.label, merged, dict(model(merged))))
    return results


def _favourable_value(
    model: Model,
    base_inputs: Mapping[str, float],
    driver: Driver,
    metric: str,
    *,
    maximize: bool,
    optimistic: bool,
) -> float:
    """Pick the band endpoint that is best (optimistic) or worst (pessimistic) for the metric.

    A driver that does not move the metric is left at its base value rather than perturbed
    arbitrarily, so the corner case stays meaningful.
    """
    low_out = _metric_at(model, base_inputs, driver.name, driver.low, metric)
    high_out = _metric_at(model, base_inputs, driver.name, driver.high, metric)
    if low_out == high_out:
        return driver.base
    best_endpoint_is_low = low_out > high_out if maximize else low_out < high_out
    pick_low = best_endpoint_is_low if optimistic else not best_endpoint_is_low
    return driver.low if pick_low else driver.high


def _corner(
    model: Model,
    base_inputs: Mapping[str, float],
    drivers: list[Driver],
    metric: str,
    *,
    maximize: bool,
    optimistic: bool,
    label: str,
) -> CaseResult:
    inputs = dict(base_inputs)
    for d in drivers:
        inputs[d.name] = _favourable_value(
            model, base_inputs, d, metric, maximize=maximize, optimistic=optimistic
        )
    return CaseResult(label, inputs, dict(model(inputs)))


def optimistic_case(
    model: Model,
    base_inputs: Mapping[str, float],
    drivers: list[Driver],
    metric: str,
    *,
    maximize: bool = False,
) -> CaseResult:
    """The best-realistic corner: every driver at its most favourable endpoint for the metric."""
    return _corner(
        model, base_inputs, drivers, metric, maximize=maximize, optimistic=True, label="optimistic"
    )


def pessimistic_case(
    model: Model,
    base_inputs: Mapping[str, float],
    drivers: list[Driver],
    metric: str,
    *,
    maximize: bool = False,
) -> CaseResult:
    """The worst-realistic corner: every driver at its least favourable endpoint for the metric."""
    return _corner(
        model, base_inputs, drivers, metric, maximize=maximize, optimistic=False, label="pessimistic"
    )


def break_even(
    model: Model,
    base_inputs: Mapping[str, float],
    driver: Driver,
    metric: str,
    target: float,
    *,
    tol: float = 1e-7,
    max_iter: int = 200,
) -> BreakEven:
    """Find the driver value where ``metric`` crosses ``target`` within the driver's band.

    Bisection over [low, high]; works for a metric monotonic (increasing or decreasing) in
    the driver. Returns ``found=False`` with ``value=None`` when the target is not bracketed.
    """
    bracket = (driver.low, driver.high)
    f_lo = _metric_at(model, base_inputs, driver.name, driver.low, metric) - target
    f_hi = _metric_at(model, base_inputs, driver.name, driver.high, metric) - target
    if f_lo == 0.0:
        return BreakEven(driver.name, metric, target, driver.low, True, bracket)
    if f_hi == 0.0:
        return BreakEven(driver.name, metric, target, driver.high, True, bracket)
    if (f_lo > 0.0) == (f_hi > 0.0):  # same sign -> no crossing in band
        return BreakEven(driver.name, metric, target, None, False, bracket)

    a, b, f_a = driver.low, driver.high, f_lo
    mid = 0.5 * (a + b)
    for _ in range(max_iter):
        mid = 0.5 * (a + b)
        f_mid = _metric_at(model, base_inputs, driver.name, mid, metric) - target
        if abs(f_mid) <= tol or (b - a) <= tol:
            break
        if (f_mid > 0.0) == (f_a > 0.0):
            a, f_a = mid, f_mid
        else:
            b = mid
    return BreakEven(driver.name, metric, target, mid, True, bracket)
