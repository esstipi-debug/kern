"""Supplier-sourcing agent job: delivery records -> scorecards -> TOPSIS award.

The data-prep + deck half of the sourcing tool. Aggregates supplier delivery records
into OTIF/lead-time/PPM scorecards with pandas directly (deliberately *not* via
jobs/intake.py, which the parallel loop owns), ranks suppliers by TOPSIS over those
criteria (+ price when present), and composes the award deck inline.

``score_suppliers`` / ``run`` / ``verify`` / ``build_deck`` are deterministic;
``prepare`` reads a file. Column names are sniffed and overridable via params.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from src.deliverable import DataSource, Deliverable, Finding, Kpi
from src.export import write_summary_csv
from src.guided import GuidedOutcome, verify_guided
from src.mcdm import Criterion, RankingResult, award_outcome, topsis_rank
from src.supplier_scorecard import SupplierScore, score_supplier

_SUPPLIER_COLS = ("supplier", "Supplier", "vendor", "Vendor", "seller_id", "supplier_id")
_ON_TIME_COLS = ("on_time", "On Time", "on_time_delivery", "ontime")
_IN_FULL_COLS = ("in_full", "In Full", "complete", "infull")
_LEAD_COLS = ("lead_time_days", "lead_time", "Lead Time", "lead")
_UNITS_COLS = ("units", "Units", "quantity", "Quantity", "qty")
_DEFECTS_COLS = ("defects", "Defects", "rejects", "ppm_defects", "Defective_Units", "defective_units")
_PRICE_COLS = ("unit_price", "Unit Price", "price", "Price", "unit_cost", "cost", "Unit_Price")

# ERP exports carry dates, not booleans - these back the derivations below.
_ORDER_DATE_COLS = ("order_date", "Order_Date", "Order Date", "po_date", "PO_Date")
_DELIVERY_DATE_COLS = ("delivery_date", "Delivery_Date", "Delivery Date", "receipt_date", "Receipt_Date")
_PROMISED_DATE_COLS = ("promised_date", "Promised_Date", "Promised Date", "due_date", "Due_Date",
                       "confirmed_date", "Confirmed_Date")
_RECEIVED_QTY_COLS = ("received_qty", "Received_Qty", "qty_received", "Qty_Received",
                      "delivered_qty", "Delivered_Qty")

_DEFAULT_WEIGHTS = {"otif": 0.4, "lead_time": 0.2, "ppm": 0.2, "price": 0.2}

_TRUE_TOKENS = frozenset({"1", "true", "t", "yes", "y", "on time", "on_time", "ontime", "full", "complete"})
_FALSE_TOKENS = frozenset({"0", "false", "f", "no", "n", "late", "partial", "incomplete", "delivered"})


@dataclass(frozen=True)
class SourcingReport:
    scorecards: tuple[SupplierScore, ...]
    prices: dict[str, float]
    criteria: tuple[Criterion, ...]
    weights: dict[str, float]
    ranking: RankingResult
    outcome: GuidedOutcome
    recommended: str
    summary: str
    notes: tuple[str, ...] = ()


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
) -> tuple[list[SupplierScore], dict[str, float]]:
    """Aggregate delivery rows into one scorecard per supplier (+ avg price if present).

    Every column that feeds a ranking criterion is required. An absent one used to become
    ``False``/``0.0``, which produced a scorecard of zeros that still crowned a winner -
    alphabetically, since every supplier tied. Refusing is the honest answer.
    """
    missing = [
        field for field, col in (
            ("on_time", on_time_col), ("in_full", in_full_col), ("lead_time", lead_col),
            ("units", units_col), ("defects", defects_col),
        ) if not col
    ]
    if missing:
        raise ValueError(
            "cannot score suppliers without the columns that decide the award: "
            f"{', '.join(missing)}. Map them in params (on_time_col, in_full_col, lead_col, "
            "units_col, defects_col) or give prepare() the fields it can derive them from "
            "(order/delivery/promised dates, ordered vs received quantity)."
        )

    scored = pd.DataFrame({
        "supplier": df[supplier_col].astype(str),
        "on_time": _as_bool(df[on_time_col], on_time_col, "on_time"),
        "in_full": _as_bool(df[in_full_col], in_full_col, "in_full"),
        "lead_time_days": _as_float(df[lead_col], lead_col, "lead_time"),
        "units": _as_float(df[units_col], units_col, "units"),
        "defects": _as_float(df[defects_col], defects_col, "defects"),
    })
    if price_col:
        scored["price"] = _as_float(df[price_col], price_col, "price")

    cards: list[SupplierScore] = []
    prices: dict[str, float] = {}
    for supplier, g in scored.groupby("supplier"):
        deliveries = g[["on_time", "in_full", "lead_time_days", "units", "defects"]].to_dict("records")
        cards.append(score_supplier(str(supplier), deliveries))
        if price_col:
            prices[str(supplier)] = float(g["price"].mean())
    return cards, prices


def _derive_lead_time(df: pd.DataFrame, params: dict, work: pd.DataFrame, notes: list[str]) -> pd.Series:
    """lead_time_days from a direct column, else delivery date - order date."""
    lead_col = _pick_column(df, params.get("lead_col"), _LEAD_COLS)
    if lead_col:
        work["lead_time_days"] = _as_float(df[lead_col], lead_col, "lead_time")
        return pd.Series(True, index=df.index)

    order_col = _pick_column(df, params.get("order_date_col"), _ORDER_DATE_COLS)
    delivery_col = _pick_column(df, params.get("delivery_date_col"), _DELIVERY_DATE_COLS)
    if not (order_col and delivery_col):
        raise ValueError(
            "cannot compute lead_time: the data has no lead-time column and no order/delivery date "
            f"pair (columns seen: {list(df.columns)[:12]}). Pass lead_col, or order_date_col plus "
            "delivery_date_col."
        )
    ordered_at = pd.to_datetime(df[order_col], errors="coerce")
    delivered_at = pd.to_datetime(df[delivery_col], errors="coerce")
    work["lead_time_days"] = (delivered_at - ordered_at).dt.days.astype("float64")
    work["_delivered_at"] = delivered_at
    notes.append(f"Lead time measured as '{delivery_col}' minus '{order_col}'.")
    return ordered_at.notna() & delivered_at.notna()


def _derive_on_time(df: pd.DataFrame, params: dict, work: pd.DataFrame, notes: list[str]) -> None:
    """on_time from a direct column, else the promised date, else a declared SLA."""
    on_time_col = _pick_column(df, params.get("on_time_col"), _ON_TIME_COLS)
    if on_time_col:
        work["on_time"] = _as_bool(df[on_time_col], on_time_col, "on_time")
        return

    promised_col = _pick_column(df, params.get("promised_date_col"), _PROMISED_DATE_COLS)
    if promised_col and "_delivered_at" in work:
        promised_at = pd.to_datetime(df[promised_col], errors="coerce")
        if promised_at.isna().all():
            raise ValueError(f"promised-date column '{promised_col}' holds no parseable dates")
        work["on_time"] = work["_delivered_at"] <= promised_at
        notes.append(f"On-time measured against the promised date in '{promised_col}'.")
        return

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


def _derive_in_full(df: pd.DataFrame, params: dict, work: pd.DataFrame, notes: list[str],
                    units_col: str | None) -> None:
    """in_full from a direct column, else received vs ordered quantity, else a status mapping."""
    in_full_col = _pick_column(df, params.get("in_full_col"), _IN_FULL_COLS)
    if in_full_col:
        work["in_full"] = _as_bool(df[in_full_col], in_full_col, "in_full")
        return

    received_col = _pick_column(df, params.get("received_qty_col"), _RECEIVED_QTY_COLS)
    if received_col and units_col:
        ordered_q = _as_float(df[units_col], units_col, "units")
        received_q = _as_float(df[received_col], received_col, "received_qty")
        work["in_full"] = received_q >= ordered_q
        notes.append(f"In-full derived from '{received_col}' against ordered '{units_col}'.")
        return

    status_col = params.get("in_full_status_col")
    ok_values = params.get("in_full_ok_values")
    if status_col and ok_values:
        if status_col not in df.columns:
            raise ValueError(f"in_full_status_col '{status_col}' is not a column in the data")
        ok = {str(v).strip().lower() for v in ok_values}
        work["in_full"] = df[status_col].astype(str).str.strip().str.lower().isin(ok)
        notes.append(f"In-full derived from '{status_col}' being one of {sorted(ok_values)}.")
        return

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

    complete = _derive_lead_time(df, params, work, notes)
    _derive_on_time(df, params, work, notes)

    units_col = _pick_column(df, params.get("units_col"), _UNITS_COLS)
    _derive_in_full(df, params, work, notes, units_col)

    if units_col is None:
        raise ValueError(
            "cannot compute defect PPM without a quantity column; pass units_col "
            f"(columns seen: {list(df.columns)[:12]})"
        )
    work["units"] = _as_float(df[units_col], units_col, "units")

    defects_col = _pick_column(df, params.get("defects_col"), _DEFECTS_COLS)
    if defects_col is None:
        raise ValueError(
            "cannot compute defect PPM without a defects column; pass defects_col. Quality is a "
            "fifth of the award weighting - assuming zero defects flatters every supplier equally."
        )
    defects = pd.to_numeric(df[defects_col], errors="coerce")
    n_blank_defects = int(defects.isna().sum())
    work["defects"] = defects.fillna(0.0).astype("float64")
    if n_blank_defects:
        notes.append(
            f"{n_blank_defects} of {len(df)} rows had no defect count in '{defects_col}'; "
            "counted as zero defects."
        )

    price_col = _pick_column(df, params.get("price_col"), _PRICE_COLS)
    if price_col:
        work["price"] = _as_float(df[price_col], price_col, "price")

    scored = work[complete & work["lead_time_days"].notna()]
    n_open = len(work) - len(scored)
    if n_open:
        notes.append(
            f"{n_open} of {len(work)} purchase orders have no completed delivery and are excluded "
            "from the scorecard."
        )
    if scored.empty:
        raise ValueError("no completed deliveries to score - every row is missing a delivery date")

    # PPM divides by units, and `NaN > 0` is False - so a delivered PO with a blank quantity
    # falls into the `else 0.0` branch and reports flawless quality. Refuse instead.
    n_blank_units = int(scored["units"].isna().sum())
    if n_blank_units:
        raise ValueError(
            f"{n_blank_units} delivered purchase order(s) have a blank quantity in '{units_col}'. "
            "Quantity is the denominator of defect PPM: a blank one scores as zero defects, i.e. "
            "flawless quality. Fill the quantity or exclude those rows before scoring."
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


def run(
    scorecards: list[SupplierScore],
    prices: dict[str, float] | None = None,
    *,
    weights: dict[str, float] | None = None,
    notes: tuple[str, ...] | None = None,
) -> SourcingReport:
    """Rank the scored suppliers by TOPSIS and present the award as protected options."""
    prices = prices or {}
    criteria = [Criterion("otif", benefit=True), Criterion("lead_time", benefit=False),
                Criterion("ppm", benefit=False)]
    alternatives: dict[str, dict[str, float]] = {}
    for s in scorecards:
        alt = {"otif": s.otif, "lead_time": s.avg_lead_time, "ppm": s.ppm}
        if prices:
            alt["price"] = prices.get(s.supplier, 0.0)
        alternatives[s.supplier] = alt
    if prices:
        criteria.append(Criterion("price", benefit=False))

    merged = {**_DEFAULT_WEIGHTS, **(weights or {})}
    used = {c.name: merged[c.name] for c in criteria}

    ranking = topsis_rank(alternatives, criteria, used)
    summary = (
        f"Compared {len(scorecards)} suppliers on OTIF / lead time / quality"
        + (" / price" if prices else "")
        + f"; recommended award: {ranking.best}."
    )
    outcome = award_outcome(ranking, summary=summary)
    return SourcingReport(
        scorecards=tuple(scorecards),
        prices=prices,
        criteria=tuple(criteria),
        weights=used,
        ranking=ranking,
        outcome=outcome,
        recommended=ranking.best,
        summary=summary,
        notes=tuple(notes or ()),
    )


def verify(report: SourcingReport) -> list[str]:
    """QA gate: a usable award honours the never-unprotected contract and scored suppliers."""
    issues = list(verify_guided(report.outcome))
    if not report.scorecards:
        issues.append("no suppliers scored")
    return issues


def write_operational(report: SourcingReport, out_dir: str | Path, client: str = "Client") -> dict[str, Path]:
    """The machine-readable deliverable: one row per supplier scorecard + TOPSIS score."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "supplier": c.supplier,
            "deliveries": c.deliveries,
            "otif": round(c.otif, 4),
            "on_time_rate": round(c.on_time_rate, 4),
            "in_full_rate": round(c.in_full_rate, 4),
            "avg_lead_time": round(c.avg_lead_time, 2),
            "ppm": round(c.ppm, 1),
            "avg_price": round(report.prices.get(c.supplier, 0.0), 2),
            "topsis_score": round(report.ranking.scores.get(c.supplier, 0.0), 4),
            "rank": report.ranking.ranking.index(c.supplier) + 1,
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
    confidence: float = 0.85,
) -> Deliverable:
    """Compose the sourcing study: the recommended award and why, with the scorecard spread."""
    best = next(c for c in report.scorecards if c.supplier == report.recommended)
    others = [c for c in report.scorecards if c.supplier != report.recommended]

    summary = (
        f"Compared {len(report.scorecards)} suppliers; recommended award to "
        f"{report.recommended} (OTIF {best.otif * 100:.0f}%, lead {best.avg_lead_time:.0f}d, "
        f"{best.ppm:,.0f} PPM)."
    )

    findings = [
        Finding(
            f"Recommended award: {report.recommended}",
            f"Best TOPSIS closeness ({report.ranking.scores[report.recommended]:.3f}) across the "
            "weighted criteria - the best balance of service, lead time and quality.",
            impact="consolidate volume here, with a backup qualified",
        )
    ]
    if report.scorecards:
        otifs = [c.otif for c in report.scorecards]
        findings.append(Finding(
            "OTIF spread across the panel",
            f"On-time-in-full ranges {min(otifs) * 100:.0f}%-{max(otifs) * 100:.0f}% - the gap is the "
            "reliability premium the award buys.",
            impact="hold underperformers to a corrective plan",
        ))
    if others:
        alt = "; ".join(
            f"{c.supplier} (OTIF {c.otif * 100:.0f}%, {report.ranking.scores[c.supplier]:.3f})"
            for c in others
        )
        findings.append(Finding("Alternatives considered", f"Ranked against: {alt}.",
                                impact="documented for the sourcing decision"))

    kpis = (
        Kpi("Recommended supplier", report.recommended, rationale="Top TOPSIS-ranked award"),
        Kpi("Its OTIF", f"{best.otif * 100:.0f}%", target="95%+", rationale="On-time-in-full reliability"),
        Kpi("Its lead time", f"{best.avg_lead_time:.0f} days", target="minimize",
            rationale="Replenishment responsiveness"),
        Kpi("Its quality (PPM)", f"{best.ppm:,.0f}", target="minimize", rationale="Defect parts per million"),
        Kpi("Suppliers compared", str(len(report.scorecards)), rationale="Panel breadth"),
    )

    data_sources = (
        DataSource("Supplier delivery records (on-time / in-full / lead / defects)", "ERP / receiving data", "monthly"),
        DataSource("Criteria weights", "engagement parameters", "per run"),
    )

    recommendations = [
        f"Award the primary volume to {report.recommended}; keep the runner-up qualified as backup.",
        "Put underperforming suppliers on a corrective-action plan with an OTIF target.",
    ]

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
    )
