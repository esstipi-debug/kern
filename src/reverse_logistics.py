"""Reverse-logistics / returns-disposition engine (efficacy roadmap #6).

For each returned lot the engine ranks the executable dispositions - restock, refurbish,
liquidate, scrap - by net recovery per unit, picks the best, and rolls up recovery rate,
value at risk and the reason Pareto. The ranked dispositions feed the Guided Execution Layer
so the tool offers *choices to act*, not just a number.

Pure (no external deps): frozen dataclasses + pure functions, mirroring decision_options /
guided.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from src.finite import require_finite

# The four standard end-of-life routes for a returned unit (best-recovery first is decided
# per line, not by this order).
DISPOSITIONS = ("restock", "refurbish", "liquidate", "scrap")

# Why a lot came back decides what may be DONE with it, not just what to report: a unit
# that failed cannot be resold as-is, and a picking mistake has nothing wrong with the
# unit. Kept as three explicit kinds - anything the words do not clearly say stays
# UNSPECIFIED and changes nothing, because guessing here restocks a defective unit.
REASON_PRODUCT_FAILURE = "product_failure"
REASON_DISPATCH_ERROR = "dispatch_error"
REASON_UNSPECIFIED = "unspecified"

_FAILURE_WORDS = (
    "defect", "defectu", "falla", "fallo", "faulty", "broken", "roto", "rotura",
    "damaged", "danad", "dañad", "quality", "calidad", "expired", "vencid", "no funciona",
    "malfunction", "leak", "contaminad",
)
_DISPATCH_WORDS = (
    "wrong item", "wrong_item", "wrong sku", "wrong_sku", "wrong size", "wrong_size",
    "error de despacho", "error_despacho", "mis-pick", "mispick", "picking",
    "producto equivocado", "articulo equivocado", "shipping error", "wrong address",
    "direccion equivocada", "sobre-envio", "over-shipment",
)


def classify_reason(reason: str) -> str:
    """Map a free-text return reason onto the kind that changes the decision.

    Word-list based and deliberately conservative: a reason that matches neither list
    (or both) is :data:`REASON_UNSPECIFIED`, which leaves every disposition on the
    table exactly as before. The corrective action differs by kind - a product failure
    is claimable to the supplier and must never go back to the shelf as-is; a dispatch
    error is an intact unit and a picking problem.
    """
    texto = reason.strip().lower()
    falla = any(w in texto for w in _FAILURE_WORDS)
    despacho = any(w in texto for w in _DISPATCH_WORDS)
    if falla and not despacho:
        return REASON_PRODUCT_FAILURE
    if despacho and not falla:
        return REASON_DISPATCH_ERROR
    return REASON_UNSPECIFIED


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
    """Rank the feasible dispositions for a returned lot, best net recovery first.

    A lot returned for a PRODUCT FAILURE never gets ``restock`` offered, even when the
    row says ``sellable``: putting a failed unit back on the shelf at full resale value
    is the wrong action, not a cheaper one. Refurbish stays (that is what reconditioning
    is for). A dispatch error leaves the unit intact, so it keeps every route.
    """
    options: list[Disposition] = []
    fallado = classify_reason(line.reason) == REASON_PRODUCT_FAILURE
    if line.sellable and not fallado:
        options.append(Disposition(
            "restock", line.resale_value - rates.restock_handling_per_unit,
            "resell as-is to the primary channel",
        ))
    if line.sellable:
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

    @property
    def reason_kind(self) -> str:
        """Why it came back, in the terms that changed the route (see :func:`classify_reason`)."""
        return classify_reason(self.line.reason)

    @property
    def supplier_claim_base(self) -> float:
        """Cost value of this lot when the return is a PRODUCT FAILURE, else 0.

        The claim BASE, not a claim amount: what is actually recoverable depends on the
        terms agreed with each supplier, which this engine is never given. Naming it
        "base" keeps it from being read as money already owed.
        """
        if self.reason_kind != REASON_PRODUCT_FAILURE:
            return 0.0
        return self.line.returned_units * self.line.unit_cost


def best_disposition(line: ReturnLine, rates: DispositionRates) -> LineDisposition:
    """Resolve a returned lot into its ranked options + recommended route."""
    return LineDisposition(line, tuple(options_for(line, rates)))


def returns_value_at_cost(lines: list[ReturnLine]) -> float:
    """Original cost value tied up in the returns - the value at risk.

    A blank unit count or cost is refused: it would make the total NaN, lose the
    ``var > 0`` test in :func:`recovery_rate`, and report a 0% recovery as a fact.
    """
    return sum(
        require_finite(ln.returned_units, f"{ln.product_id} returned_units")
        * require_finite(ln.unit_cost, f"{ln.product_id} unit_cost")
        for ln in lines
    )


def recovered_value(dispositions: list[LineDisposition]) -> float:
    """Total net value recovered under the recommended route per lot."""
    return sum(d.recovery_value for d in dispositions)


def recovery_rate(dispositions: list[LineDisposition], lines: list[ReturnLine]) -> float:
    """Recovered value / value at risk (can exceed 1 if resale beats cost, or go negative)."""
    var = returns_value_at_cost(lines)
    return recovered_value(dispositions) / var if math.isfinite(var) and var > 0 else 0.0


def supplier_claim_base(dispositions: list[LineDisposition]) -> float:
    """Cost value of everything that came back for a product failure (the claim base)."""
    return sum(d.supplier_claim_base for d in dispositions)


def units_by_reason_kind(lines: list[ReturnLine]) -> dict[str, float]:
    """Units returned per reason KIND - the split the corrective action hangs off."""
    totals: dict[str, float] = {}
    for ln in lines:
        kind = classify_reason(ln.reason)
        totals[kind] = totals.get(kind, 0.0) + ln.returned_units
    return totals


def reason_pareto(lines: list[ReturnLine]) -> list[tuple[str, float]]:
    """Units returned per reason, ranked descending (the root-cause Pareto)."""
    totals: dict[str, float] = {}
    for ln in lines:
        totals[ln.reason] = totals.get(ln.reason, 0.0) + ln.returned_units
    return sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
