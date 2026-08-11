"""Realized customer service - what the client actually received.

This module is the OBSERVED counterpart of ``src/fill_rate.py``. That module
computes the beta a *policy implies*: given a safety stock, a cycle demand and a
dispersion, Vandeput's identity beta = 1 - Us/dc (2020, sec. 7.3.1) returns the
fill rate the model expects to achieve. It needs no orders, no shipments and no
customers - ``fill_rate_from_safety_stock(50, 500, 40)`` answers 99.6% about a
warehouse that may never have shipped a unit.

What lives here is the service that actually happened, computed from order lines:
what the customer asked for, what left the dock, and when. SCOR's RL.1.1 (Perfect
Order Fulfillment) is the frame - a line counts only when it was delivered
complete AND on the date that was promised - and the lost sale is valued at the
price of the line that went short.

The two numbers must never reach a client under the same name. Every record here
carries an explicit ``provenance`` saying where its number came from:

    observed         measured here, from order lines
    client_reported  the client measured it and handed us the number
    not_measured     nobody measured it; every field is None, never 0.0
    model_implied    the beta a sized policy implies (``src/fill_rate.py``).
                     That is not service, and is only ever labelled as such.

Doctrine borrowed from ``src/supplier_scorecard.py``, for the same reason it was
written there: every rate is computed over the lines that actually carried the
data it needs, never over ``len(lines)``. A line that never carried a delivered
quantity is an absence, not a stockout, and dividing by the row count turns "we
never measured this" into a confident "0%".
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

#: Measured here, from the client's own order lines. The strongest kind.
PROVENANCE_OBSERVED = "observed"
#: The client measured the service and handed us the number. A fact about the
#: client's operation, not about our model - but we did not compute it.
PROVENANCE_CLIENT_REPORTED = "client_reported"
#: Nobody measured it. Every metric is ``None`` so a deck prints "not measured"
#: (``src.deliverable.not_measured``) instead of a zero nobody observed.
PROVENANCE_NOT_MEASURED = "not_measured"
#: The beta a sized policy IMPLIES (``src.fill_rate``). Never a measurement, and
#: never reported under a realized-service name. Defined here so the one word the
#: whole distinction rests on has a single spelling in the codebase.
PROVENANCE_MODEL_IMPLIED = "model_implied"
#: A roll-up whose parts did not all come from the same place.
PROVENANCE_MIXED = "mixed"

METHOD_ORDER_LINES = (
    "order lines: short units valued at the line's own price; stockout days = "
    "distinct dates carrying a short line (lower bound)"
)
METHOD_ORDER_LINES_UNDATED = (
    "order lines: short units valued at the line's own price; stockout days not "
    "measured (no line carried a promised or delivered date)"
)
METHOD_ORDER_LINES_STOCK_HISTORY = (
    "order lines: short units valued at the line's own price; stockout days = "
    "client stock-zero history"
)
METHOD_REPORTED_FILL_RATE = (
    "fill rate reported by the client; censored demand served/beta - served "
    "(Vandeput 7.3.1 inverted) valued at mean unit cost"
)
METHOD_REPORTED_FILL_RATE_ZERO = (
    "fill rate reported by the client; zero fill rate, demand cannot be "
    "reconstructed from it"
)
METHOD_NOT_MEASURED = "not measured: no order lines and no fill rate reported by the client"
METHOD_AGGREGATE = (
    "portfolio roll-up: fill rate weighted by units, OTIF weighted by scored lines, "
    "stockout days and lost sales summed"
)
#: Appended when the caller supplied a real stock-zero history for a record whose
#: other numbers came from somewhere else.
_METHOD_STOCK_HISTORY_SUFFIX = "; stockout days = client stock-zero history"


@dataclass(frozen=True)
class RealizedService:
    """Service one SKU actually delivered to customers over a window.

    Every metric is ``float | None`` / ``int | None`` on purpose: ``None`` means
    nobody measured it, and only a genuine observation is ever a number. There is
    one ``method`` field and it is prose, not a metric - it says how the numbers
    beside it were derived so a client can reconcile them, and it is deliberately
    NOT named after any of them, because a sentence explaining a method must never
    be mistakable for the measurement it explains.
    """

    product_id: str
    #: Order-line fill rate (units delivered / units ordered). Not a beta from a
    #: policy - see the module docstring.
    fill_rate: float | None
    #: Outbound OTIF: share of scored lines delivered complete AND by the promised
    #: date. This is service to the CUSTOMER; ``src.supplier_scorecard.SupplierScore.otif``
    #: is the inbound mirror (supplier -> us) and the two are never the same number.
    otif: float | None
    #: Days the SKU could not serve. ``None`` when no evidence supports a count -
    #: a scalar fill rate cannot produce one (one long outage and many short ones
    #: give the same beta), so it takes order lines with dates or a stock-zero history.
    days_out_of_stock: int | None
    lost_sales_units: float | None
    lost_sales_value: float | None
    #: One of the PROVENANCE_* constants. The field that stops a model number and
    #: a measured number from being read as the same thing.
    provenance: str
    method: str
    #: Lines seen, lines that carried both quantities, lines that also carried a
    #: price. Same role as ``supplier_scorecard.Evaluable``: how much evidence each
    #: number above rests on.
    order_lines: int = 0
    lines_scored: int = 0
    lines_priced: int = 0
    units_ordered: float | None = None
    units_delivered: float | None = None
    periods_covered: int = 0


def _number(row: dict, key: str) -> float | None:
    """A recorded quantity, or ``None`` when absent, blank or not a real number."""
    value = row.get(key)
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _as_date(value: object) -> date | None:
    """A calendar date, or ``None`` when the cell carried something else.

    Accepts anything that exposes year/month/day (``date``, ``datetime``, a pandas
    ``Timestamp``) and ISO-8601 text. Anything else is refused rather than guessed:
    an unparsed date silently drops the line out of OTIF, which is better than
    ordering two unrelated string formats against each other and calling it late.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, date):
        return date(value.year, value.month, value.day)
    for attr in ("year", "month", "day"):
        if not hasattr(value, attr):
            break
    else:
        try:
            return date(int(value.year), int(value.month), int(value.day))
        except (TypeError, ValueError):
            return None
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None
    return None


@dataclass(frozen=True)
class _LineScan:
    """What a set of order lines carried, counted over the lines that carried it."""

    lines: int
    scored: int
    priced: int
    ordered: float
    delivered: float
    short_units: float
    short_value: float
    otif_hits: int
    otif_scored: int
    short_dates: frozenset
    #: Scored lines carrying at least one usable date. The evidence a day count
    #: rests on: with none of them there is no window to count days over, and an
    #: empty ``short_dates`` means "never measured", not "never broke".
    dated: int = 0


def _scan_lines(lines: Sequence[dict]) -> _LineScan:
    """Walk the order lines once, counting each criterion over its own evidence."""
    scored = priced = otif_hits = otif_scored = dated = 0
    ordered = delivered = short_units = short_value = 0.0
    short_dates: set[date] = set()
    for line in lines:
        want, got = _number(line, "ordered_qty"), _number(line, "delivered_qty")
        if want is None or got is None or want <= 0 or got < 0:
            continue
        scored += 1
        ordered += want
        # Capped at what was asked: over-delivering one line does not repay the
        # units another line went short, and an uncapped sum can report beta > 1.
        delivered += min(got, want)
        short = max(want - got, 0.0)
        short_units += short
        price = _number(line, "unit_price")
        if price is not None and price >= 0:
            priced += 1
            short_value += short * price
        promised, shipped = _as_date(line.get("promised_date")), _as_date(line.get("delivered_date"))
        if promised is not None and shipped is not None:
            otif_scored += 1
            otif_hits += int(short <= 0 and shipped <= promised)
        if promised is not None or shipped is not None:
            dated += 1
        if short > 0 and (promised or shipped) is not None:
            short_dates.add(promised or shipped)
    return _LineScan(
        lines=len(lines), scored=scored, priced=priced, ordered=ordered, delivered=delivered,
        short_units=short_units, short_value=short_value, otif_hits=otif_hits,
        otif_scored=otif_scored, short_dates=frozenset(short_dates), dated=dated,
    )


def not_measured(product_id: str, *, order_lines: int = 0) -> RealizedService:
    """Nobody measured this SKU's service. Every metric ``None``, and it says so."""
    return RealizedService(
        product_id=product_id, fill_rate=None, otif=None, days_out_of_stock=None,
        lost_sales_units=None, lost_sales_value=None,
        provenance=PROVENANCE_NOT_MEASURED, method=METHOD_NOT_MEASURED,
        order_lines=order_lines,
    )


def measure_from_order_lines(
    product_id: str,
    lines: Sequence[dict],
    *,
    stockout_days: int | None = None,
    periods_covered: int = 0,
) -> RealizedService:
    """Service measured from the client's own order lines.

    Each line may carry ``ordered_qty``, ``delivered_qty``, ``promised_date``,
    ``delivered_date`` and ``unit_price``; a line missing what a criterion needs
    is left out of that criterion's denominator, not counted as a failure.

    ``stockout_days`` is the client's real stock-zero count and wins when given.
    Without it the count falls back to the distinct dates that carried a short
    line, which is a LOWER BOUND (a day nobody ordered on cannot be seen) and is
    declared as such in ``method``.

    That fallback needs dated lines to exist at all. When not one line carried a
    promised or a delivered date the count is ``None``, not 0: an empty set of
    short dates over undated lines is the module docstring's own failure mode -
    counting over rows that never carried the data, and turning "we never
    measured this" into a confident zero next to a shortfall we did measure.
    """
    scan = _scan_lines(lines)
    if scan.scored == 0:
        return not_measured(product_id, order_lines=scan.lines)
    measured_days = stockout_days is not None
    if measured_days:
        days, method = int(stockout_days), METHOD_ORDER_LINES_STOCK_HISTORY
    elif scan.dated:
        days, method = len(scan.short_dates), METHOD_ORDER_LINES
    else:
        days, method = None, METHOD_ORDER_LINES_UNDATED
    return RealizedService(
        product_id=product_id,
        fill_rate=scan.delivered / scan.ordered,
        otif=(scan.otif_hits / scan.otif_scored) if scan.otif_scored else None,
        days_out_of_stock=days,
        lost_sales_units=scan.short_units,
        lost_sales_value=scan.short_value if scan.priced else None,
        provenance=PROVENANCE_OBSERVED,
        method=method,
        order_lines=scan.lines, lines_scored=scan.scored, lines_priced=scan.priced,
        units_ordered=scan.ordered, units_delivered=scan.delivered,
        periods_covered=periods_covered,
    )


def _check_reported(
    product_id: str, fill_rate: float, shipped_units: float, unit_cost: float | None
) -> None:
    """Refuse a reported number that cannot be a measurement, rather than clamping it.

    Same stance as ``supplier_scorecard._flag``: a value outside the range the
    metric lives in means the source column is not what the caller thinks it is,
    and silently coercing it produces a confident, wrong service figure.
    """
    if not 0.0 <= float(fill_rate) <= 1.0:
        raise ValueError(
            f"fill_rate for {product_id!r} is {fill_rate!r}: a measured fill rate lives in "
            "[0, 1]. Fix the source column rather than clamping it - a beta above 1 means "
            "the two quantities being divided are not ordered and delivered units."
        )
    if shipped_units < 0:
        raise ValueError(f"shipped_units for {product_id!r} must be >= 0, got {shipped_units!r}")
    if unit_cost is not None and unit_cost < 0:
        raise ValueError(f"unit_cost for {product_id!r} must be >= 0, got {unit_cost!r}")


def from_reported_fill_rate(
    product_id: str,
    fill_rate: float,
    *,
    shipped_units: float,
    unit_cost: float | None = None,
    periods_covered: int = 0,
    stockout_days: int | None = None,
) -> RealizedService:
    """Service the CLIENT measured, carried through instead of being thrown away.

    ``fill_rate`` is stored exactly as received - it is their measurement, and
    re-deriving a ratio from our own totals would quietly replace it with ours.

    The demand behind a shipped history is censored: what left the dock is the
    served fraction beta of what was asked. Inverting Vandeput's identity gives
    ordered = served / beta and the shortfall served * (1 - beta) / beta, valued
    at ``unit_cost``. ``otif`` and ``days_out_of_stock`` stay ``None``: neither is
    derivable from a scalar beta (one long outage and many short ones give the
    same number), so they take order lines - or, for the days, an explicit
    stock-zero history the caller measured elsewhere.
    """
    _check_reported(product_id, fill_rate, shipped_units, unit_cost)
    beta = float(fill_rate)
    served = float(shipped_units)
    reconstructable = beta > 0 and served > 0
    lost_units = served * (1.0 - beta) / beta if reconstructable else None
    method = METHOD_REPORTED_FILL_RATE if beta > 0 else METHOD_REPORTED_FILL_RATE_ZERO
    return RealizedService(
        product_id=product_id,
        fill_rate=beta,
        otif=None,
        days_out_of_stock=int(stockout_days) if stockout_days is not None else None,
        lost_sales_units=lost_units,
        # ``unit_cost is not None``, never truthiness: ``_check_reported`` only
        # refuses a NEGATIVE cost, so 0.0 is a legal, measured cost (a giveaway,
        # a promotional line). Under truthiness it fell to the None branch and a
        # lost sale we had valued at zero was reported as never valued at all.
        lost_sales_value=(
            lost_units * float(unit_cost)
            if lost_units is not None and unit_cost is not None else None
        ),
        provenance=PROVENANCE_CLIENT_REPORTED,
        method=method + (_METHOD_STOCK_HISTORY_SUFFIX if stockout_days is not None else ""),
        units_ordered=served / beta if reconstructable else None,
        units_delivered=served,
        periods_covered=periods_covered,
    )


def _weighted(pairs: list[tuple[float, float]]) -> float | None:
    """Weighted mean over the pairs that existed, or ``None`` when none did."""
    weight = sum(w for _, w in pairs)
    return sum(v * w for v, w in pairs) / weight if weight > 0 else None


def _summed(values: list[float]) -> float | None:
    """Total of what was observed, or ``None`` when nothing was."""
    return sum(values) if values else None


def aggregate(
    records: Sequence[RealizedService], *, product_id: str = "PORTFOLIO"
) -> RealizedService:
    """Roll SKU-level realized service up to the portfolio.

    Weighted by evidence, never by SKU count: a 100%-served SKU that shipped four
    units does not offset a 40%-served one that shipped forty thousand. Stockout
    days add up as SKU-days. A metric no record measured stays ``None``.
    """
    units = [(r.units_delivered, r.units_ordered) for r in records
             if r.units_ordered and r.units_delivered is not None]
    otifs = [(r.otif, float(r.lines_scored)) for r in records
             if r.otif is not None and r.lines_scored > 0]
    origins = {r.provenance for r in records if r.provenance != PROVENANCE_NOT_MEASURED}
    # Resolved BEFORE the constructor call, and without consuming ``origins``.
    # ``origins.pop()`` inline emptied the set, and Python evaluates keyword
    # arguments in source order: by the time ``method`` was reached the set was
    # empty, so the ordinary case - every record from the same place - reported
    # METHOD_NOT_MEASURED over numbers it had just measured.
    provenance = (
        next(iter(origins)) if len(origins) == 1
        else (PROVENANCE_MIXED if origins else PROVENANCE_NOT_MEASURED)
    )
    ordered_total = sum(o for _, o in units)
    return RealizedService(
        product_id=product_id,
        fill_rate=(sum(d for d, _ in units) / ordered_total) if ordered_total > 0 else None,
        otif=_weighted(otifs),
        days_out_of_stock=(
            int(sum(r.days_out_of_stock for r in records if r.days_out_of_stock is not None))
            if any(r.days_out_of_stock is not None for r in records) else None
        ),
        lost_sales_units=_summed([r.lost_sales_units for r in records
                                  if r.lost_sales_units is not None]),
        lost_sales_value=_summed([r.lost_sales_value for r in records
                                  if r.lost_sales_value is not None]),
        provenance=provenance,
        method=METHOD_AGGREGATE if origins else METHOD_NOT_MEASURED,
        order_lines=sum(r.order_lines for r in records),
        lines_scored=sum(r.lines_scored for r in records),
        lines_priced=sum(r.lines_priced for r in records),
        units_ordered=ordered_total if units else None,
        units_delivered=sum(d for d, _ in units) if units else None,
        periods_covered=max((r.periods_covered for r in records), default=0),
    )


def skus_out_of_stock(records: Sequence[RealizedService]) -> tuple[str, ...]:
    """SKUs with at least one measured stockout day, for the deck's finding.

    A SKU whose days were never measured is not listed: an unmeasured SKU is not
    a healthy one, and this list is read as "these are the ones that broke".
    """
    return tuple(r.product_id for r in records if (r.days_out_of_stock or 0) > 0)
