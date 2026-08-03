"""Supplier-sourcing agent job: delivery records -> scorecards -> TOPSIS award.

The data-prep + deck half of the sourcing tool. Aggregates supplier delivery records
into OTIF/lead-time/PPM scorecards with pandas directly (deliberately *not* via
jobs/intake.py, which the parallel loop owns), ranks suppliers by TOPSIS over those
criteria (+ price when present), and composes the award deck inline.

``score_suppliers`` / ``run`` / ``verify`` / ``build_deck`` are deterministic;
``prepare`` reads a file. Column names are sniffed and overridable via params.

Two policies for a criterion this data cannot supply, and the caller picks:
``require_all_criteria=True`` (the default, and what ``prepare`` uses) refuses by name -
no award is defensible without the columns that decide it. ``require_all_criteria=False``
scores what *is* measurable, drops the rest from the TOPSIS rather than tying every
supplier at zero, and renders it "no medido" instead of a confident 0%.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from nosilo_core.guided import (
    GuidedOutcome,
    HandoffPacket,
    as_handoff,
    confidence_from_coverage,
    verify_guided,
)
from src.deliverable import DataSource, Deliverable, Finding, Kpi, days, not_measured, pct, ppm
from src.export import write_summary_csv
from src.mcdm import Criterion, RankingResult, award_outcome, topsis_rank
from src.supplier_scorecard import (
    LEAD_TIME_DECLARED,
    LEAD_TIME_MEASURED,
    SupplierScore,
    score_supplier,
)

_SUPPLIER_COLS = ("supplier", "Supplier", "vendor", "Vendor", "seller_id", "supplier_id")
_ON_TIME_COLS = ("on_time", "On Time", "on_time_delivery", "ontime")
_IN_FULL_COLS = ("in_full", "In Full", "complete", "infull")
_LEAD_COLS = ("lead_time_days", "lead_time", "Lead Time", "lead")
_UNITS_COLS = ("units", "Units", "quantity", "Quantity", "qty")
_DEFECTS_COLS = ("defects", "Defects", "rejects", "ppm_defects", "Defective_Units", "defective_units")
_PRICE_COLS = ("unit_price", "Unit Price", "price", "Price", "unit_cost", "cost", "Unit_Price")
_PRODUCT_COLS = ("product_id", "sku", "SKU", "Product_ID", "item", "Item", "material")
_PO_ID_COLS = ("po_number", "PO_Number", "po_id", "PO", "order_id", "Order_ID",
               "purchase_order", "Purchase_Order", "reference", "Reference")

# ERP exports carry dates, not booleans - these back the derivations below.
_ORDER_DATE_COLS = ("order_date", "Order_Date", "Order Date", "po_date", "PO_Date")
_DELIVERY_DATE_COLS = ("delivery_date", "Delivery_Date", "Delivery Date", "receipt_date", "Receipt_Date")
_PROMISED_DATE_COLS = ("promised_date", "Promised_Date", "Promised Date", "due_date", "Due_Date",
                       "confirmed_date", "Confirmed_Date")
_RECEIVED_QTY_COLS = ("received_qty", "Received_Qty", "qty_received", "Qty_Received",
                      "delivered_qty", "Delivered_Qty")

# criterion name -> (higher is better, how to read it off a scorecard)
_CRITERIA: tuple[tuple[str, bool, object], ...] = (
    ("otif", True, lambda card, prices: card.otif),
    ("lead_time", False, lambda card, prices: card.avg_lead_time),
    ("ppm", False, lambda card, prices: card.ppm),
    ("price", False, lambda card, prices: prices.get(card.supplier)),
)

_DEFAULT_WEIGHTS = {"otif": 0.4, "lead_time": 0.2, "ppm": 0.2, "price": 0.2}
# build_deck's confidence floor when nothing was ignored - matches the fixed default this
# replaced, so a panel with no ignored (tied, non-discriminating) criterion behaves exactly
# as before. See confidence_from_coverage: an award decided on fewer discriminating criteria
# than it weighed is less certain than one decided on all of them.
_DECK_BASE_CONFIDENCE = 0.85

# Keep these two SYMMETRIC, and keep ORDER-STATUS values out of both. An asymmetric pair
# mis-scores silently (if "no" parses but "yes" does not, a column holding only "no" reads as
# 0% instead of being refused), and a status like "delivered" is not a yes/no answer at all -
# it says nothing about timing, and it is a substring of "partially delivered". Status columns
# go through the explicit in_full_status_col + in_full_ok_values mapping instead.
_TRUE_TOKENS = frozenset({"1", "true", "t", "yes", "y", "on time", "on_time", "ontime",
                          "full", "complete"})
_FALSE_TOKENS = frozenset({"0", "false", "f", "no", "n", "late", "incomplete"})


@dataclass(frozen=True)
class SourcingReport:
    scorecards: tuple[SupplierScore, ...]
    prices: dict[str, float]
    criteria: tuple[Criterion, ...]
    weights: dict[str, float]
    ranking: RankingResult
    outcome: GuidedOutcome
    recommended: str            # "" when nothing could be ranked
    summary: str
    notes: tuple[str, ...] = ()
    # Criteria dropped because at least one supplier on the panel never measured them.
    # Only reachable via require_all_criteria=False; ranking on a tie of zeros awards
    # alphabetically, which is not a decision.
    unmeasured: tuple[str, ...] = field(default_factory=tuple)
    # The bottom of the ranking, worst first: who the review is actually about. The
    # award names the winner; nobody schedules a meeting about the winner.
    worst_for_action: tuple[str, ...] = ()


def _cell(value: float | None, digits: int) -> float | str:
    """A CSV cell: the rounded number, or blank. Never 0 standing in for absent."""
    return "" if value is None else round(value, digits)


def _pick_column(df: pd.DataFrame, override: object, candidates: tuple[str, ...]) -> str | None:
    if override:
        return str(override) if str(override) in df.columns else None
    return next((c for c in candidates if c in df.columns), None)


def _as_bool(series: pd.Series, col: str, field: str) -> pd.Series:
    """Read a yes/no column as booleans, or refuse.

    ``bool(x)`` on an arbitrary object is the defect this replaces: it scored every
    ``'Cancelled'`` purchase order as a perfect on-time delivery.
    """
    if pd.api.types.is_bool_dtype(series):
        return series.astype(bool)
    if pd.api.types.is_numeric_dtype(series):
        if series.isna().any():
            raise ValueError(f"column '{col}' ({field}) has blank rows; a blank is not a yes or a no")
        off_scale = sorted({float(v) for v in series.unique()} - {0.0, 1.0})
        if off_scale:
            raise ValueError(
                f"column '{col}' ({field}) holds {off_scale[:3]}, not 0/1 - it is a measurement, "
                "not a yes/no flag"
            )
        return series.astype(bool)
    text = series.astype(str).str.strip().str.lower()
    unknown = sorted(set(text) - _TRUE_TOKENS - _FALSE_TOKENS)
    if unknown:
        raise ValueError(
            f"column '{col}' cannot be read as {field}: values like {unknown[:3]} are free text, "
            f"not a yes/no flag. Point {field}_col at a real boolean column, or let prepare() derive "
            f"{field} (order/delivery/promised dates for on_time; ordered vs received quantity, or "
            "in_full_status_col + in_full_ok_values, for in_full)."
        )
    return text.isin(_TRUE_TOKENS)


def _as_float(series: pd.Series, col: str, field: str) -> pd.Series:
    """Read a numeric column as floats, or refuse - never coerce junk to zero."""
    out = pd.to_numeric(series, errors="coerce")
    bad_rows = out.isna() & series.notna()
    if bad_rows.any():
        offenders = sorted({str(v) for v in series[bad_rows].unique()})[:3]
        raise ValueError(f"column '{col}' ({field}) is not numeric: {offenders}")
    return out.astype("float64")


def score_suppliers(
    df: pd.DataFrame,
    *,
    supplier_col: str,
    on_time_col: str | None = None,
    in_full_col: str | None = None,
    lead_col: str | None = None,
    units_col: str | None = None,
    defects_col: str | None = None,
    price_col: str | None = None,
    require_all_criteria: bool = True,
) -> tuple[list[SupplierScore], dict[str, float]]:
    """Aggregate delivery rows into one scorecard per supplier (+ avg price if present).

    By default every column that feeds a ranking criterion is required. An absent one used
    to become ``False``/``0.0``, which produced a scorecard of zeros that still crowned a
    winner - alphabetically, since every supplier tied. Refusing is the honest answer, and
    it is what ``prepare`` asks for.

    With ``require_all_criteria=False`` an absent column contributes ``None`` (absent)
    instead: the criterion is dropped from the ranking by :func:`run` and rendered
    "no medido", so a bare purchase-order export still yields whatever it *can* support.
    Columns that ARE present are typed just as strictly either way - permissive about
    absence is not permissive about junk.
    """
    missing = [
        name for name, col in (
            ("on_time", on_time_col), ("in_full", in_full_col), ("lead_time", lead_col),
            ("units", units_col), ("defects", defects_col),
        ) if not col
    ]
    if missing and require_all_criteria:
        raise ValueError(
            "cannot score suppliers without the columns that decide the award: "
            f"{', '.join(missing)}. Map them in params (on_time_col, in_full_col, lead_col, "
            "units_col, defects_col) or give prepare() the fields it can derive them from "
            "(order/delivery/promised dates, ordered vs received quantity). To rank on the "
            "criteria this data *does* carry instead, pass require_all_criteria=False."
        )

    absent = pd.Series([None] * len(df), index=df.index, dtype="object")
    scored = pd.DataFrame({
        "supplier": df[supplier_col].astype(str),
        "on_time": _as_bool(df[on_time_col], on_time_col, "on_time") if on_time_col else absent,
        "in_full": _as_bool(df[in_full_col], in_full_col, "in_full") if in_full_col else absent,
        "lead_time_days": _as_float(df[lead_col], lead_col, "lead_time") if lead_col else absent,
        "units": _as_float(df[units_col], units_col, "units") if units_col else absent,
        "defects": _as_float(df[defects_col], defects_col, "defects") if defects_col else absent,
    })
    if price_col:
        scored["price"] = _as_float(df[price_col], price_col, "price")

    cards: list[SupplierScore] = []
    prices: dict[str, float] = {}
    # Carried, not scored: the declared lead time and where the measured one came from
    # travel to the scorecard so it can report the gap instead of picking one silently.
    pass_through = [
        c for c in ("declared_lead_time_days", "lead_time_source", "po_id", "product_id")
        if c in df.columns
    ]
    for extra in pass_through:
        scored[extra] = df[extra]
    for supplier, g in scored.groupby("supplier"):
        deliveries = g[
            ["on_time", "in_full", "lead_time_days", "units", "defects", *pass_through]
        ].to_dict("records")
        cards.append(score_supplier(str(supplier), deliveries))
        if price_col:
            mean_price = float(g["price"].mean())
            # An unpriced supplier is not a free one: leave it out and let `run` drop
            # price as a criterion rather than award it the ideal cost.
            if math.isfinite(mean_price):
                prices[str(supplier)] = mean_price
    return cards, prices


def _derive_lead_time(df: pd.DataFrame, params: dict, work: pd.DataFrame, notes: list[str]) -> pd.Series:
    """lead_time_days from a direct column, else delivery date - order date.

    The delivery date is read whenever the data carries one, even when lead time comes from a
    direct column: the promised-date comparison downstream needs it, and hanging it off only
    the subtraction path made a lead-time column silently disable on-time-vs-promise.
    """
    order_col = _pick_column(df, params.get("order_date_col"), _ORDER_DATE_COLS)
    delivery_col = _pick_column(df, params.get("delivery_date_col"), _DELIVERY_DATE_COLS)
    if delivery_col:
        work["_delivered_at"] = pd.to_datetime(df[delivery_col], errors="coerce")

    lead_col = _pick_column(df, params.get("lead_col"), _LEAD_COLS)
    if order_col and delivery_col:
        ordered_at = pd.to_datetime(df[order_col], errors="coerce")
        work["lead_time_days"] = (work["_delivered_at"] - ordered_at).dt.days.astype("float64")
        work["lead_time_source"] = LEAD_TIME_MEASURED
        notes.append(f"Lead time measured as '{delivery_col}' minus '{order_col}'.")
        if lead_col:
            # The dates win: a lead-time column is what the supplier SAYS, and the point of
            # this panel is what actually happened. The declared number is kept beside the
            # measured one so the gap is reportable instead of silently overwriting it.
            work["declared_lead_time_days"] = _as_float(df[lead_col], lead_col, "lead_time")
            notes.append(
                f"'{lead_col}' carries a declared lead time; it is reported as the declared "
                "number and compared against the measured one, not used in its place."
            )
    elif lead_col:
        work["lead_time_days"] = _as_float(df[lead_col], lead_col, "lead_time")
        work["lead_time_source"] = LEAD_TIME_DECLARED
        notes.append(
            f"Lead time taken from '{lead_col}' as DECLARED: the data has no order/delivery "
            "date pair to measure it from, so it is a promise, not an observation."
        )
    else:
        raise ValueError(
            "cannot compute lead_time: the data has no lead-time column and no order/delivery date "
            f"pair (columns seen: {list(df.columns)[:12]}). Pass lead_col, or order_date_col plus "
            "delivery_date_col."
        )

    # A delivery dated before its order is the ideal point on both criteria: fastest lead time
    # and always inside any SLA. It would win the award on a typo, so drop it.
    negative = (work["lead_time_days"] < 0).fillna(False)
    if bool(negative.any()):
        notes.append(
            f"{int(negative.sum())} row(s) have a negative lead time (delivery dated before the "
            "order) and are excluded - such a row scores as the fastest, always-on-time supplier."
        )

    complete = work["lead_time_days"].notna() & ~negative
    if "_delivered_at" in work:
        complete &= work["_delivered_at"].notna()
    return complete


def _derive_on_time(df: pd.DataFrame, params: dict, work: pd.DataFrame,
                    notes: list[str]) -> pd.Series:
    """on_time from a direct column, else the promised date, else a declared SLA.

    Returns the mask of rows whose on-time answer is actually knowable.
    """
    on_time_col = _pick_column(df, params.get("on_time_col"), _ON_TIME_COLS)
    if on_time_col:
        work["on_time"] = _as_bool(df[on_time_col], on_time_col, "on_time")
        return pd.Series(True, index=df.index)

    promised_col = _pick_column(df, params.get("promised_date_col"), _PROMISED_DATE_COLS)
    if promised_col and "_delivered_at" in work:
        promised_at = pd.to_datetime(df[promised_col], errors="coerce")
        # A NaT comparison is False, i.e. "late". An unknown promise is not a missed one.
        unknown = promised_at.isna() & work["_delivered_at"].notna()
        if bool(unknown.any()):
            notes.append(
                f"{int(unknown.sum())} delivered row(s) have no promised date in '{promised_col}' "
                "and are excluded - an unknown promise would otherwise score as a late delivery."
            )
        work["on_time"] = work["_delivered_at"] <= promised_at
        notes.append(f"On-time measured against the promised date in '{promised_col}'.")
        return ~unknown

    sla_days = params.get("sla_days")
    if sla_days is None:
        raise ValueError(
            "cannot compute on_time: the data carries no on-time column and no promised-delivery "
            "date. Pass promised_date_col, or declare the contractual lead-time SLA with "
            "sla_days=<n>. On-time scored against an undeclared default is a fabricated number."
        )
    try:
        sla = float(sla_days)
    except (TypeError, ValueError):
        raise ValueError(f"sla_days must be a number of days, got {sla_days!r}") from None
    work["on_time"] = work["lead_time_days"] <= sla
    notes.append(
        f"[ASSUMPTION] On-time measured against a declared {sla:g}-day SLA - the data carries no "
        "promised-delivery date. Confirm the contractual SLA before awarding."
    )
    return pd.Series(True, index=df.index)


def _derive_in_full(df: pd.DataFrame, params: dict, work: pd.DataFrame, notes: list[str],
                    units_col: str | None) -> pd.Series:
    """in_full from a direct column, else received vs ordered quantity, else a status mapping.

    Returns the mask of rows whose in-full answer is actually knowable.
    """
    in_full_col = _pick_column(df, params.get("in_full_col"), _IN_FULL_COLS)
    if in_full_col:
        work["in_full"] = _as_bool(df[in_full_col], in_full_col, "in_full")
        return pd.Series(True, index=df.index)

    received_col = _pick_column(df, params.get("received_qty_col"), _RECEIVED_QTY_COLS)
    if received_col and units_col:
        ordered_q = _as_float(df[units_col], units_col, "units")
        received_q = _as_float(df[received_col], received_col, "received_qty")
        # `NaN >= ordered` is False: an unrecorded receipt would score as a short shipment.
        unknown = received_q.isna() & ordered_q.notna()
        if bool(unknown.any()):
            notes.append(
                f"{int(unknown.sum())} row(s) have no received quantity in '{received_col}' and "
                "are excluded - an unrecorded receipt would otherwise score as a short shipment."
            )
        work["in_full"] = received_q >= ordered_q
        notes.append(f"In-full derived from '{received_col}' against ordered '{units_col}'.")
        return ~unknown

    status_col = params.get("in_full_status_col")
    ok_values = params.get("in_full_ok_values")
    if status_col and ok_values:
        if status_col not in df.columns:
            raise ValueError(f"in_full_status_col '{status_col}' is not a column in the data")
        ok = {str(v).strip().lower() for v in ok_values}
        matched = df[status_col].astype(str).str.strip().str.lower().isin(ok)
        # 0% in full across every supplier is a broken mapping, not a service-level finding.
        if not bool(matched.any()):
            seen = sorted({str(v) for v in df[status_col].dropna().unique()})[:5]
            raise ValueError(
                f"in_full_ok_values {sorted(ok_values)} matched no row of '{status_col}' "
                f"(values present: {seen}). Every supplier would score 0% in full."
            )
        work["in_full"] = matched
        notes.append(f"In-full derived from '{status_col}' being one of {sorted(ok_values)}.")
        return pd.Series(True, index=df.index)

    raise ValueError(
        "cannot compute in_full: the data has no in-full column, no received-vs-ordered quantity "
        "pair, and no in_full_status_col + in_full_ok_values mapping. Supply one - a delivery "
        "assumed complete is a fabricated service level."
    )


def prepare(data_path: str, params: dict | None = None) -> dict:
    """Read a supplier delivery CSV and aggregate it into scorecards + prices.

    Accepts a raw ERP purchase-order export (dates, quantities, a status column) and derives
    the OTIF fields from it, rather than requiring the caller to pre-map booleans. Anything it
    cannot derive is refused by name, never defaulted - see ``score_suppliers``.
    """
    params = params or {}
    df = pd.read_csv(data_path)
    supplier = _pick_column(df, params.get("supplier_col"), _SUPPLIER_COLS)
    if supplier is None:
        raise ValueError(f"could not find supplier_col; pass it in params (columns seen: {list(df.columns)[:10]})")

    notes: list[str] = []
    work = pd.DataFrame(index=df.index)
    work["supplier"] = df[supplier].astype(str)
    po_col = _pick_column(df, params.get("po_id_col"), _PO_ID_COLS)
    if po_col:
        work["po_id"] = df[po_col].astype(str)
    else:
        notes.append(
            "No purchase-order identifier column was found, so the scores cannot be traced "
            "back to the orders that produced them; pass po_id_col to make the panel auditable."
        )

    product_col = _pick_column(df, params.get("product_col"), _PRODUCT_COLS)
    if product_col:
        work["product_id"] = df[product_col].astype(str)
    else:
        notes.append(
            "No product identifier column was found, so a lead time that contradicts the "
            "declared one cannot name the SKUs whose safety stock and reorder point it "
            "invalidates; pass product_col to get that list."
        )

    usable = _derive_lead_time(df, params, work, notes)
    usable &= _derive_on_time(df, params, work, notes)

    units_col = _pick_column(df, params.get("units_col"), _UNITS_COLS)
    usable &= _derive_in_full(df, params, work, notes, units_col)

    if units_col is None:
        raise ValueError(
            "cannot compute defect PPM without a quantity column; pass units_col "
            f"(columns seen: {list(df.columns)[:12]})"
        )
    # PPM divides by units, and `NaN > 0` is False - a blank quantity would report flawless
    # quality rather than an unknown one, so those rows leave the panel.
    raw_units = _as_float(df[units_col], units_col, "units")
    blank_units = raw_units.isna()
    if bool(blank_units.any()):
        notes.append(
            f"{int(blank_units.sum())} row(s) have no quantity in '{units_col}' and are excluded - "
            "quantity is the denominator of defect PPM, so a blank one reads as flawless quality."
        )
    usable &= ~blank_units
    work["units"] = raw_units

    defects_col = _pick_column(df, params.get("defects_col"), _DEFECTS_COLS)
    if defects_col is None:
        raise ValueError(
            "cannot compute defect PPM without a defects column; pass defects_col. Quality is a "
            "fifth of the award weighting - assuming zero defects flatters every supplier equally."
        )
    defects = _as_float(df[defects_col], defects_col, "defects")
    # PPM is defects/units. Zero-filling a blank count does not mean "no defects", it silently
    # inflates the denominator with unmeasured units and understates the rate. Measure the rate
    # over the rows that were actually inspected, and report the coverage separately.
    known = defects.notna()
    n_blank_defects = int((~known).sum())
    work["defects"] = defects.fillna(0.0)
    work["units"] = work["units"].where(known, 0.0)
    if n_blank_defects:
        notes.append(
            f"{n_blank_defects} of {len(df)} rows had no defect count in '{defects_col}'; defect "
            "PPM is measured over the remaining rows only, not diluted across all of them."
        )

    price_col = _pick_column(df, params.get("price_col"), _PRICE_COLS)
    if price_col:
        price = _as_float(df[price_col], price_col, "price")
        if bool(price.isna().any()):
            notes.append(
                f"{int(price.isna().sum())} row(s) have no unit price in '{price_col}' and are "
                "excluded from the average price."
            )
        work["price"] = price

    scored = work[usable]
    n_open = len(work) - len(scored)
    if n_open:
        notes.append(
            f"{n_open} of {len(work)} purchase orders are excluded from the scorecard for the "
            "reasons above; the panel is scored on the remaining "
            f"{len(scored)}."
        )
    if scored.empty:
        raise ValueError(
            "no usable purchase orders left to score - every row was missing a delivery, a "
            "quantity, or a comparable date"
        )

    if price_col:
        # A supplier with no priced row at all yields a NaN mean, which propagates through
        # TOPSIS and leaves every closeness score NaN - the award falls back to input order.
        unpriced = sorted(
            str(s) for s, g in scored.groupby("supplier") if bool(g["price"].isna().all())
        )
        if unpriced:
            raise ValueError(
                f"supplier(s) {unpriced} have no unit price on any scored row in '{price_col}'. "
                "Their average price is NaN, which turns every TOPSIS score into NaN and decides "
                "the award by input order. Fill those prices or drop price_col."
            )

    cards, prices = score_suppliers(
        scored,
        supplier_col="supplier",
        on_time_col="on_time",
        in_full_col="in_full",
        lead_col="lead_time_days",
        units_col="units",
        defects_col="defects",
        price_col="price" if price_col else None,
    )
    return {"scorecards": cards, "prices": prices, "notes": tuple(notes)}


#: How many of the bottom of the panel get flagged for the supplier review.
WORST_FOR_ACTION = 3


def _worst_for_action(ranking: list[str]) -> tuple[str, ...]:
    """The bottom ``WORST_FOR_ACTION`` of the ranking, WORST first.

    Never flags the whole panel: on a two-supplier panel, "both of you are the worst"
    is not an action item. With <= WORST_FOR_ACTION suppliers only the last one is
    flagged, so the list always leaves someone to compare against.
    """
    if len(ranking) <= 1:
        return ()
    n = min(WORST_FOR_ACTION, len(ranking) - 1)
    return tuple(reversed(ranking[-n:]))


def run(
    scorecards: list[SupplierScore],
    prices: dict[str, float] | None = None,
    *,
    weights: dict[str, float] | None = None,
    notes: tuple[str, ...] | None = None,
) -> SourcingReport:
    """Rank the scored suppliers by TOPSIS and present the award as protected options.

    Only criteria measured for **every** supplier on the panel enter the ranking. Via the
    default ``prepare`` path they all are, so this is a no-op there; it is what keeps a
    ``require_all_criteria=False`` panel from being ranked on a criterion nobody recorded,
    which scores everyone an identical zero and lets TOPSIS fall back to input order.
    """
    prices = prices or {}
    measured, unmeasured = [], []
    for name, benefit, read in _CRITERIA:
        if name == "price" and not prices:
            continue      # no price column at all: not a criterion, not a gap either
        if scorecards and all(read(card, prices) is not None for card in scorecards):
            measured.append(Criterion(name, benefit=benefit))
        else:
            unmeasured.append(name)

    merged = {**_DEFAULT_WEIGHTS, **(weights or {})}
    used = {c.name: merged[c.name] for c in measured}
    readers = {name: read for name, _, read in _CRITERIA}

    if not measured:
        return _nothing_to_rank(scorecards, prices, tuple(unmeasured), tuple(notes or ()))

    alternatives = {
        card.supplier: {c.name: float(readers[c.name](card, prices)) for c in measured}
        for card in scorecards
    }
    ranking = topsis_rank(alternatives, measured, used)
    summary = (
        f"Compared {len(scorecards)} suppliers on "
        + " / ".join(c.name for c in measured)
        + f"; recommended award: {ranking.best}."
        + (f" Not measured in this data: {', '.join(unmeasured)}." if unmeasured else "")
    )
    return SourcingReport(
        scorecards=tuple(scorecards),
        prices=prices,
        criteria=tuple(measured),
        weights=used,
        ranking=ranking,
        outcome=award_outcome(ranking, summary=summary),
        recommended=ranking.best,
        summary=summary,
        notes=tuple(notes or ()),
        unmeasured=tuple(unmeasured),
        worst_for_action=_worst_for_action(ranking.ranking),
    )


def _nothing_to_rank(
    scorecards: list[SupplierScore], prices: dict[str, float],
    unmeasured: tuple[str, ...], notes: tuple[str, ...],
) -> SourcingReport:
    """No criterion survived: hand the gap to a human instead of awarding on nothing."""
    gap = ", ".join(unmeasured) if unmeasured else "every criterion"
    summary = (
        f"Cannot rank {len(scorecards)} supplier(s): {gap} was never measured in this data. "
        "No award is defensible until at least one criterion is populated."
    )
    outcome = as_handoff(
        summary,
        [HandoffPacket(
            title="Supply the missing supplier-performance columns",
            steps=[
                f"Add one of: {gap} to the delivery export (or map it via params).",
                "On-time needs order/delivery dates or a promised date; in-full needs "
                "ordered vs received quantity; quality needs units and defects.",
                "Re-run the sourcing brief once at least one criterion is populated.",
            ],
            risk_if_skipped="Awarding without a measured criterion ranks suppliers by "
                            "input order, not by performance.",
        )],
    )
    return SourcingReport(
        scorecards=tuple(scorecards), prices=prices, criteria=(), weights={},
        ranking=RankingResult({}, []), outcome=outcome, recommended="",
        summary=summary, notes=notes, unmeasured=unmeasured,
    )


def verify(report: SourcingReport) -> list[str]:
    """QA gate: a usable award honours the never-unprotected contract and scored suppliers."""
    issues = list(verify_guided(report.outcome))
    if not report.scorecards:
        issues.append("no suppliers scored")
    # A criterion dropped for not discriminating (every supplier tied - see
    # report.ranking.ignored_criteria) is NOT a QA failure on its own: the ranking
    # degraded on purpose and build_deck discloses it via a lower confidence and a
    # Finding, not a blocked run. Only "nothing left to rank on at all" blocks here.
    if not report.criteria:
        gap = ", ".join(report.unmeasured) if report.unmeasured else "every criterion"
        issues.append(f"no measured criterion to rank suppliers on ({gap} absent)")
    return issues


def write_operational(report: SourcingReport, out_dir: str | Path, client: str = "Client") -> dict[str, Path]:
    """The machine-readable deliverable: one row per supplier scorecard + TOPSIS score."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "supplier": c.supplier,
            "deliveries": c.deliveries,
            "otif": _cell(c.otif, 4),
            "otif_observations": c.evaluable.otif,
            "on_time_rate": _cell(c.on_time_rate, 4),
            "in_full_rate": _cell(c.in_full_rate, 4),
            "avg_lead_time": _cell(c.avg_lead_time, 2),
            "lead_time_source": c.lead_time_source or "",
            "lead_time_std": _cell(c.lead_time_std, 2),
            "lead_time_p90": _cell(c.lead_time_p90, 2),
            "declared_lead_time": _cell(c.declared_lead_time, 2),
            "lead_time_gap": _cell(c.lead_time_gap, 2),
            # The action the gap implies, not just the gap: these SKUs carry an SS/ROP
            # sized on a lead time their own deliveries contradict.
            "skus_to_recalculate": " ".join(c.skus_to_recalculate[:20]) + (
                f" (+{len(c.skus_to_recalculate) - 20} mas)"
                if len(c.skus_to_recalculate) > 20 else ""
            ),
            "flagged_for_action": c.supplier in report.worst_for_action,
            # Traceability, capped so a 777-PO panel stays a readable cell. The cap is
            # spelled out in the cell itself - a silent truncation reads as "these are
            # all the orders", which is the opposite of auditable.
            "scored_po_ids": " ".join(c.scored_po_ids[:20]) + (
                f" (+{len(c.scored_po_ids) - 20} mas)" if len(c.scored_po_ids) > 20 else ""
            ),
            "ppm": _cell(c.ppm, 1),
            "avg_price": _cell(report.prices.get(c.supplier), 2),
            "topsis_score": _cell(report.ranking.scores.get(c.supplier), 4),
            "rank": (
                report.ranking.ranking.index(c.supplier) + 1
                if c.supplier in report.ranking.ranking else ""
            ),
        }
        for c in report.scorecards
    ]
    return {"csv": write_summary_csv(rows, d / "supplier_scorecards.csv")}


def build_deck(
    report: SourcingReport,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple[str, ...] = (),
    confidence: float | None = None,
    lang: str = "en",
) -> Deliverable:
    """Compose the sourcing study: the recommended award and why, with the scorecard spread.

    Every criterion prints what was measured, or the words ``no medido``. A rate over zero
    observations is not zero percent, and a deck that prints it as one is the exact failure
    this tool is supposed to catch in the client's own reporting.

    ``confidence`` is derived from the ranking's coverage when not supplied: an award
    decided on fewer discriminating criteria than it weighed is less certain than one
    decided on all of them (see src/guided.py::confidence_from_coverage). A criterion
    every supplier tied on (``report.ranking.ignored_criteria``) is a different gap than
    one this data never measured at all (``report.unmeasured``, disclosed separately below).
    """
    if not report.recommended:
        raise ValueError(
            "no award to present: " + report.summary + " (verify() blocks this report)"
        )
    if confidence is None:
        confidence = confidence_from_coverage(
            ["ignored_criterion"] * len(report.ranking.ignored_criteria),
            base=_DECK_BASE_CONFIDENCE,
        )
    best = next(c for c in report.scorecards if c.supplier == report.recommended)
    others = [c for c in report.scorecards if c.supplier != report.recommended]
    ranked = " / ".join(c.name for c in report.criteria)

    summary = (
        f"Compared {len(report.scorecards)} suppliers on {ranked}; recommended award to "
        f"{report.recommended} (OTIF {pct(best.otif, lang)}, lead {days(best.avg_lead_time, lang)}, "
        f"{ppm(best.ppm, lang)})."
    )

    findings = [
        Finding(
            f"Recommended award: {report.recommended}",
            f"Best TOPSIS closeness ({report.ranking.scores[report.recommended]:.3f}) across the "
            f"weighted criteria actually measured here ({ranked}).",
            impact="consolidate volume here, with a backup qualified",
        )
    ]
    if report.unmeasured:
        findings.append(Finding(
            "Criteria this data could not measure",
            f"{', '.join(report.unmeasured)} - not present for every supplier on the panel, so "
            "excluded from the ranking rather than scored as zero.",
            impact=f"the award rests on {ranked} alone; populate the rest to widen it",
        ))
    if report.ranking.ignored_criteria:
        ignored = report.ranking.ignored_criteria
        dropped_weight = sum(report.weights.get(name, 0.0) for name in ignored)
        findings.append(Finding(
            f"{len(ignored)} criteri{'on' if len(ignored) == 1 else 'a'} did not discriminate",
            f"Every supplier scored identically on {', '.join(ignored)}, so "
            f"{'it was' if len(ignored) == 1 else 'they were'} dropped from the ranking "
            f"despite carrying {dropped_weight:.0%} of the criteria weight. The award "
            f"rests on the criteria that actually varied.",
            impact="supply data that actually varies before treating this as a full "
                    "confirmation of the award",
        ))
    otifs = [c.otif for c in report.scorecards if c.otif is not None]
    if otifs:
        findings.append(Finding(
            "OTIF spread across the panel",
            f"On-time-in-full ranges {pct(min(otifs), lang)}-{pct(max(otifs), lang)} across the "
            f"{len(otifs)} supplier(s) that recorded it - the gap is the reliability "
            "premium the award buys.",
            impact="hold underperformers to a corrective plan",
        ))
    if others:
        alt = "; ".join(
            f"{c.supplier} (OTIF {pct(c.otif, lang)}, {report.ranking.scores[c.supplier]:.3f})"
            for c in others
        )
        findings.append(Finding("Alternatives considered", f"Ranked against: {alt}.",
                                impact="documented for the sourcing decision"))

    kpis = (
        Kpi("Recommended supplier", report.recommended, rationale="Top TOPSIS-ranked award"),
        Kpi("Its OTIF", pct(best.otif, lang), target="95%+",
            rationale=f"On-time-in-full over {best.evaluable.otif} scoreable delivery record(s)"),
        Kpi("Its lead time", days(best.avg_lead_time, lang), target="minimize",
            rationale=f"Replenishment responsiveness over {best.evaluable.lead_time} record(s)"),
        Kpi("Its quality (PPM)", ppm(best.ppm, lang), target="minimize",
            rationale=f"Defect parts per million over {best.evaluable.ppm} record(s)"),
        Kpi("Suppliers compared", str(len(report.scorecards)), rationale="Panel breadth"),
        Kpi("Criteria ranked on", ranked or not_measured(lang),
            rationale="Only criteria measured for every supplier enter the TOPSIS"),
    )

    data_sources = (
        DataSource("Supplier delivery records (on-time / in-full / lead / defects)", "ERP / receiving data", "monthly"),
        DataSource("Criteria weights", "engagement parameters", "per run"),
    )

    # Only prescribe a target on something this data actually measured: "hold them to an
    # OTIF target" is advice the reader cannot act on without an OTIF.
    measured_names = {c.name for c in report.criteria}
    corrective = (
        "Put underperforming suppliers on a corrective-action plan with an OTIF target."
        if "otif" in measured_names
        else f"Put underperforming suppliers on a corrective-action plan against {ranked}, "
             "the criteria this data supports."
    )
    recommendations = [
        f"Award the primary volume to {report.recommended}; keep the runner-up qualified as backup.",
        corrective,
    ]
    if report.unmeasured:
        recommendations.append(
            f"Add {', '.join(report.unmeasured)} to the delivery export so the next award "
            "weighs service and quality, not just what happened to be in the file."
        )

    return Deliverable(
        title="Supplier Sourcing & Selection",
        client=client,
        summary=summary,
        findings=tuple(findings),
        kpis=kpis,
        data_sources=data_sources,
        recommendations=tuple(recommendations),
        citations=tuple(citations),
        confidence=confidence,
        residual=" ".join((
            *report.notes,
            "The final award, price negotiation, and contract terms are commercial / legal "
            "decisions - this ranks and prepares the packet; a human signs the award.",
        )),
        prepared=prepared,
        lang=lang,      # section headers / table columns, not the content
    )
