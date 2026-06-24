"""Reverse-logistics / returns-disposition engine (efficacy roadmap #6).

For each returned lot the engine ranks the executable dispositions - restock, refurbish,
liquidate, scrap - by net recovery per unit, picks the best, and rolls up recovery rate,
value at risk and the reason Pareto. The ranked dispositions feed the Guided Execution Layer
so the tool offers *choices to act*, not just a number.

Pure (no external deps): frozen dataclasses + pure functions, mirroring decision_options /
guided.
"""

from __future__ import annotations

from dataclasses import dataclass

# The four standard end-of-life routes for a returned unit (best-recovery first is decided
# per line, not by this order).
DISPOSITIONS = ("restock", "refurbish", "liquidate", "scrap")


@dataclass(frozen=True)
class DispositionRates:
    """The economics of each disposition route (engagement parameters)."""

    restock_handling_per_unit: float = 0.0    # cost to inspect + put a sellable return back
    refurbish_cost_per_unit: float = 0.0      # cost to recondition a unit
    refurbish_resale_factor: float = 0.6      # refurbished sells at this fraction of resale value
    liquidation_recovery_pct: float = 0.2     # a liquidator pays this fraction of unit cost
    scrap_cost_per_unit: float = 0.0          # disposal cost per unit (a net loss)


@dataclass(frozen=True)
class ReturnLine:
    """One returned lot: how many, why, and the cost/resale economics."""

    product_id: str
    returned_units: float
    reason: str
    unit_cost: float
    resale_value: float                       # primary-channel price if returned to stock
    sellable: bool = True                     # restock/refurbish allowed (False if unsafe/expired)


@dataclass(frozen=True)
class Disposition:
    """One executable route for a returned lot and its per-unit net recovery."""

    action: str
    net_recovery_per_unit: float
    rationale: str


def options_for(line: ReturnLine, rates: DispositionRates) -> list[Disposition]:
    """Rank the feasible dispositions for a returned lot, best net recovery first."""
    options: list[Disposition] = []
    if line.sellable:
        options.append(Disposition(
            "restock", line.resale_value - rates.restock_handling_per_unit,
            "resell as-is to the primary channel",
        ))
        options.append(Disposition(
            "refurbish",
            rates.refurbish_resale_factor * line.resale_value - rates.refurbish_cost_per_unit,
            "recondition, then resell",
        ))
    options.append(Disposition(
        "liquidate", rates.liquidation_recovery_pct * line.unit_cost,
        "sell to a secondary-market liquidator",
    ))
    options.append(Disposition(
        "scrap", -rates.scrap_cost_per_unit, "dispose / recycle (a net cost)",
    ))
    return sorted(options, key=lambda d: d.net_recovery_per_unit, reverse=True)


@dataclass(frozen=True)
class LineDisposition:
    """A returned lot with its ranked options and the recommended (best) route."""

    line: ReturnLine
    options: tuple[Disposition, ...]

    @property
    def best(self) -> Disposition:
        return self.options[0]

    @property
    def recovery_value(self) -> float:
        """Net recovery for the whole lot under the best route."""
        return self.best.net_recovery_per_unit * self.line.returned_units


def best_disposition(line: ReturnLine, rates: DispositionRates) -> LineDisposition:
    """Resolve a returned lot into its ranked options + recommended route."""
    return LineDisposition(line, tuple(options_for(line, rates)))


def total_returned_units(lines: list[ReturnLine]) -> float:
    return sum(ln.returned_units for ln in lines)


def returns_value_at_cost(lines: list[ReturnLine]) -> float:
    """Original cost value tied up in the returns - the value at risk."""
    return sum(ln.returned_units * ln.unit_cost for ln in lines)


def recovered_value(dispositions: list[LineDisposition]) -> float:
    """Total net value recovered under the recommended route per lot."""
    return sum(d.recovery_value for d in dispositions)


def recovery_rate(dispositions: list[LineDisposition], lines: list[ReturnLine]) -> float:
    """Recovered value / value at risk (can exceed 1 if resale beats cost, or go negative)."""
    var = returns_value_at_cost(lines)
    return recovered_value(dispositions) / var if var > 0 else 0.0


def reason_pareto(lines: list[ReturnLine]) -> list[tuple[str, float]]:
    """Units returned per reason, ranked descending (the root-cause Pareto)."""
    totals: dict[str, float] = {}
    for ln in lines:
        totals[ln.reason] = totals.get(ln.reason, 0.0) + ln.returned_units
    return sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
