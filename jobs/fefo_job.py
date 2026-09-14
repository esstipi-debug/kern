"""FEFO / expiry agent job: a lots CSV -> aging report + at-risk units + disposition.

The data-prep + deck half of the FEFO tool. Reads on-hand lots (quantity + days to expiry,
optional unit cost/price + daily demand) with pandas directly (deliberately *not* via
jobs/intake.py, which the parallel loop owns) and computes, via ``src.lots``: the shelf-life
aging report, the FEFO issue order, the quantity demand cannot consume before expiry, and a
markdown-vs-scrap recommendation for the at-risk units.

Expiry is taken from a ``days_to_expiry`` column, or computed from an ``expiry_date`` column
plus an explicit ``as_of`` date param (the clock is never read, so output is testable).
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import pandas as pd

from src.deliverable import DataSource, Deliverable, Finding, Kpi
from src.export import write_summary_csv
from src.lots.expiry import DEFAULT_THRESHOLDS, DispositionPlan, ExpiryBucket, aging_report, markdown_vs_scrap
from src.lots.fefo import Lot, at_risk_quantities, fefo_order

_PRODUCT_COLS = ("product_id", "product", "sku", "SKU", "item", "Product")
_LOT_COLS = ("lot_id", "lot", "batch", "batch_id", "Lot")
_QTY_COLS = ("quantity", "qty", "on_hand", "units", "Quantity")
_DAYS_COLS = ("days_to_expiry", "days", "shelf_life_days", "days_left", "ttl_days")
_EXPIRY_COLS = ("expiry_date", "expiry", "expiration", "best_before", "use_by", "Expiry")
_COST_COLS = ("unit_cost", "cost", "Unit Cost")
_PRICE_COLS = ("unit_price", "price", "sell_price", "Price")
_DEMAND_COLS = ("daily_demand", "demand_rate", "daily_sales", "run_rate")

_DEFAULT_MARKDOWN_PRICE_PCT = 0.5

#: What a money cell says when the export never carried the basis for it. A number here
#: - any number, including 0 - is read as a measurement, and 0 is the most favourable
#: measurement available for a write-off.
_NOT_MEASURED = "not measured"

#: What a run without a valuation basis costs the deck's confidence. Deliberately large:
#: the money half of an expiry study is most of the study.
_MISSING_BASIS_PENALTY = 0.25
_CONFIDENCE_FLOOR = 0.10


@dataclass(frozen=True)
class ValuationBasis:
    """Which money columns the export carried, and which lots actually filled them.

    ``src.lots.fefo.Lot`` types ``unit_cost``/``unit_price`` as plain floats, so a lot
    whose money the client never sent enters the engine as ``0.0`` - byte-identical to
    a lot that really is free. ``markdown_vs_scrap`` then multiplies that filler through
    the whole money half of the study and still returns a ``recommended`` string, because
    ``0 >= 0``. The provenance is recorded here, at the boundary that saw the cell,
    rather than teaching the shared engine about absence.
    """

    cost_column: str | None
    price_column: str | None
    lots_without_cost: tuple[str, ...]
    lots_without_price: tuple[str, ...]
    n_lots: int

    @property
    def cost_measured(self) -> bool:
        return self.cost_column is not None and not self.lots_without_cost

    @property
    def price_measured(self) -> bool:
        return self.price_column is not None and not self.lots_without_price

    @property
    def complete(self) -> bool:
        """Both halves. A disposition compares markdown against scrap, so it needs both."""
        return self.cost_measured and self.price_measured

    @property
    def notes(self) -> tuple[str, ...]:
        found = (
            _basis_note("cost", self.cost_column, self.lots_without_cost, self.n_lots),
            _basis_note("price", self.price_column, self.lots_without_price, self.n_lots),
        )
        return tuple(n for n in found if n)


def _basis_note(kind: str, column: str | None, missing: tuple[str, ...], n_lots: int) -> str | None:
    """Name the column and the lots, so the reader knows exactly what to send next."""
    canonical = f"unit_{kind}"
    if column is None:
        return (f"no unit-{kind} basis: the export carries no {canonical} column, so the "
                f"{kind} half of the study cannot be computed")
    if missing:
        shown = ", ".join(missing[:5]) + ("..." if len(missing) > 5 else "")
        return (f"no complete unit-{kind} basis: the {column} column is present but empty "
                f"for {len(missing)} of {n_lots} lot(s) ({shown})")
    return None


@dataclass(frozen=True)
class FefoReport:
    n_lots: int
    n_products: int
    total_quantity: float
    #: ``None`` means "never priced", never 0.0 standing in for "worthless".
    total_value: float | None
    aging: tuple[ExpiryBucket, ...]
    expired_quantity: float
    expiring_quantity: float
    at_risk_units: float
    at_risk_value: float | None
    disposition: DispositionPlan
    lots_fefo: tuple[Lot, ...]
    at_risk_by_lot: dict[str, float]
    summary: str
    basis: ValuationBasis
    scrap_recovery: float | None
    markdown_recovery: float | None
    recovered_value: float | None
    #: The job's own verdict, which is NOT ``disposition.recommended``: the raw engine
    #: always returns a string, even when it compared two numbers it invented.
    recommended: str | None
    products_without_demand_rate: tuple[str, ...]

    @property
    def notes(self) -> tuple[str, ...]:
        found = list(self.basis.notes)
        if self.products_without_demand_rate:
            names = ", ".join(self.products_without_demand_rate[:5])
            found.append(
                f"forward risk not assessed for {len(self.products_without_demand_rate)} "
                f"product(s) with no daily demand rate ({names}): their lots are aged but "
                "their at-risk quantity is unknown"
            )
        return tuple(found)


def _pick_column(df: pd.DataFrame, override: object, candidates: tuple[str, ...]) -> str | None:
    if override:
        return str(override) if str(override) in df.columns else None
    return next((c for c in candidates if c in df.columns), None)


def _bucket_label(days: float) -> str:
    for label, upper in DEFAULT_THRESHOLDS:
        if days <= upper:
            return label
    return DEFAULT_THRESHOLDS[-1][0]


def prepare_records(df: pd.DataFrame, params: dict | None = None) -> dict:
    """Sniff lot columns, resolve days-to-expiry, and build the lots + demand rates."""
    params = params or {}
    product = _pick_column(df, params.get("product_col"), _PRODUCT_COLS)
    lot = _pick_column(df, params.get("lot_col"), _LOT_COLS)
    qty = _pick_column(df, params.get("qty_col"), _QTY_COLS)
    missing = [n for n, c in (("product_id", product), ("lot_id", lot), ("quantity", qty)) if c is None]
    if missing:
        cols = list(df.columns)[:10]
        raise ValueError(f"could not find {', '.join(missing)}; pass them in params (columns seen: {cols})")

    days_col = _pick_column(df, params.get("days_col"), _DAYS_COLS)
    expiry_col = _pick_column(df, params.get("expiry_col"), _EXPIRY_COLS)
    as_of = params.get("as_of")
    if days_col is None:
        if expiry_col is None:
            raise ValueError("need a 'days_to_expiry' column or an 'expiry_date' column")
        if not as_of:
            raise ValueError("expiry_date present - pass as_of=YYYY-MM-DD in params to compute days to expiry")
    as_of_dt = pd.to_datetime(as_of) if (days_col is None and as_of) else None

    cost = _pick_column(df, params.get("cost_col"), _COST_COLS)
    price = _pick_column(df, params.get("price_col"), _PRICE_COLS)
    demand = _pick_column(df, params.get("demand_col"), _DEMAND_COLS)

    lots: list[Lot] = []
    rate_by_product: dict[str, float] = {}
    without_cost: list[str] = []
    without_price: list[str] = []
    for _, row in df.iterrows():
        pid = str(row[product])
        lot_id = str(row[lot])
        if days_col is not None:
            days = float(row[days_col])
        else:
            days = float((pd.to_datetime(row[expiry_col]) - as_of_dt).days)
        has_cost = bool(cost) and pd.notna(row[cost])
        has_price = bool(price) and pd.notna(row[price])
        if not has_cost:
            without_cost.append(lot_id)
        if not has_price:
            without_price.append(lot_id)
        # The 0.0 still goes into the Lot - the shared engine takes floats - but from
        # here on nothing downstream is allowed to believe it.
        lots.append(Lot(
            lot_id=lot_id, product_id=pid, quantity=float(row[qty]), days_to_expiry=days,
            unit_cost=float(row[cost]) if has_cost else 0.0,
            unit_price=float(row[price]) if has_price else 0.0,
        ))
        if demand and pd.notna(row[demand]) and pid not in rate_by_product:
            rate_by_product[pid] = float(row[demand])

    scalar_demand = params.get("daily_demand")
    if scalar_demand is not None:
        for pid in {lot.product_id for lot in lots}:
            rate_by_product.setdefault(pid, float(scalar_demand))

    return {
        "lots": lots,
        "demand_rate_by_product": rate_by_product,
        "scrap_value_pct": float(params.get("scrap_value_pct", 0.0)),
        "markdown_price_pct": float(params.get("markdown_price_pct", _DEFAULT_MARKDOWN_PRICE_PCT)),
        # Required, not optional: a hand-built payload must not be able to opt out of
        # declaring what it could and could not value by simply omitting the key.
        "basis": ValuationBasis(
            cost_column=cost, price_column=price,
            lots_without_cost=tuple(without_cost), lots_without_price=tuple(without_price),
            n_lots=len(lots),
        ),
    }


def prepare(data_path: str, params: dict | None = None) -> dict:
    """Read a lots CSV and build the FEFO payload."""
    return prepare_records(pd.read_csv(data_path), params)


def run(payload: dict) -> FefoReport:
    """Build the aging report, the FEFO order, the at-risk quantity and the disposition."""
    lots: list[Lot] = payload["lots"]
    aging = aging_report(lots)
    at_risk = at_risk_quantities(lots, payload["demand_rate_by_product"])
    disposition = markdown_vs_scrap(
        at_risk, scrap_value_pct=payload["scrap_value_pct"], markdown_price_pct=payload["markdown_price_pct"],
    )
    by_label = {b.label: b for b in aging}
    expired_q = by_label.get("expired").quantity if "expired" in by_label else 0.0
    expiring_q = by_label.get("expiring").quantity if "expiring" in by_label else 0.0

    basis: ValuationBasis = payload["basis"]
    rates = payload["demand_rate_by_product"]
    unassessed = tuple(sorted({lot.product_id for lot in lots} - set(rates)))

    # Each figure is gated by the half of the basis it actually depends on: at-risk cost
    # and scrap recovery are cost-derived, markdown recovery is price-derived, and the
    # recommendation compares the two so it needs both.
    report = FefoReport(
        n_lots=len(lots), n_products=len({lot.product_id for lot in lots}),
        total_quantity=sum(lot.quantity for lot in lots),
        total_value=sum(lot.quantity * lot.unit_cost for lot in lots) if basis.cost_measured else None,
        aging=tuple(aging), expired_quantity=expired_q, expiring_quantity=expiring_q,
        at_risk_units=disposition.at_risk_units,
        at_risk_value=disposition.at_risk_cost if basis.cost_measured else None,
        disposition=disposition, lots_fefo=tuple(fefo_order(lots)),
        at_risk_by_lot={r.lot_id: r.at_risk_quantity for r in at_risk}, summary="",
        basis=basis,
        scrap_recovery=disposition.scrap_recovery if basis.cost_measured else None,
        markdown_recovery=disposition.markdown_recovery if basis.price_measured else None,
        recovered_value=disposition.recovered_value if basis.complete else None,
        recommended=disposition.recommended if basis.complete else None,
        products_without_demand_rate=unassessed,
    )
    return replace(report, summary=_derive_summary(report))


def _derive_summary(report: FefoReport) -> str:
    """The one place the headline sentence is built, so ``verify`` can re-derive it.

    A summary that outlives the numbers it describes is its own silent failure: the
    fields go absent and the sentence keeps asserting the old total.
    """
    parts = [
        f"Lot expiry over {report.n_lots} lot(s): {report.expired_quantity:,.0f} expired + "
        f"{report.expiring_quantity:,.0f} expiring soon; {report.at_risk_units:,.0f} unit(s) at risk"
    ]
    if report.at_risk_value is not None:
        parts.append(f" ({report.at_risk_value:,.0f} cost)")
    if report.recommended is not None:
        # Defensive on recovered_value: verify() calls this on hand-mutated reports whose
        # halves disagree, and a formatter that raises would hide the finding it is meant
        # to expose behind a TypeError.
        recovered = (_NOT_MEASURED if report.recovered_value is None
                     else f"{report.recovered_value:,.0f}")
        parts.append(f", recommend {report.recommended} (recover {recovered}).")
    else:
        parts.append(". Disposition not determinable: " + "; ".join(report.basis.notes) + ".")
    if report.products_without_demand_rate:
        names = ", ".join(report.products_without_demand_rate)
        parts.append(f" Forward risk not assessed for: {names}.")
    return "".join(parts)


def verify(report: FefoReport) -> list[str]:
    """QA gate. Bites both ways on purpose.

    It vetoes a money figure or a disposition asserted without the basis to support it,
    AND an absence claimed where the basis was measured - so "return None always" is not
    a passing implementation. Same shape as ``jobs/cross_impact_job.py``, whose verify
    refuses a clean verdict while areas are unmeasured.
    """
    import math

    issues: list[str] = []
    if report.n_lots <= 0:
        issues.append("no lots to assess")
    if not math.isfinite(report.total_quantity) or report.total_quantity < 0:
        issues.append("total quantity is negative or non-finite")
    if report.at_risk_units < 0 or not math.isfinite(report.at_risk_units):
        issues.append("at-risk units invalid")

    for note in report.basis.notes:
        issues.append(f"valuation basis incomplete: {note}")

    if not report.basis.complete and report.recommended is not None:
        issues.append(
            f"a disposition ({report.recommended}) was recommended without a complete "
            "valuation basis: markdown-vs-scrap compared at least one invented number"
        )
    for name, value, needed in (
        ("total value", report.total_value, report.basis.cost_measured),
        ("at-risk value", report.at_risk_value, report.basis.cost_measured),
        ("scrap recovery", report.scrap_recovery, report.basis.cost_measured),
        ("markdown recovery", report.markdown_recovery, report.basis.price_measured),
        ("recovered value", report.recovered_value, report.basis.complete),
    ):
        if value is not None and not needed:
            issues.append(f"{name} reported without the basis to compute it")
        if value is None and needed:
            issues.append(f"{name} reported absent although its basis was measured")
    if report.basis.complete and report.recommended is None:
        issues.append("no disposition recommended although the valuation basis is complete")
    if report.recommended is not None and report.recommended not in ("markdown", "scrap"):
        issues.append(f"unknown disposition: {report.recommended}")

    if report.summary != _derive_summary(report):
        issues.append("the summary does not match the report it summarises")
    return issues


def write_operational(report: FefoReport, out_dir: str | Path, client: str = "Client") -> dict[str, Path]:
    """The machine-readable deliverable: lots in FEFO issue order with bucket + at-risk qty."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "lot_id": lot.lot_id,
            "product_id": lot.product_id,
            "quantity": round(lot.quantity, 1),
            "days_to_expiry": round(lot.days_to_expiry, 1),
            "bucket": _bucket_label(lot.days_to_expiry),
            "at_risk_quantity": round(report.at_risk_by_lot.get(lot.lot_id, 0.0), 1),
        }
        for lot in report.lots_fefo
    ]
    return {"csv": write_summary_csv(rows, d / "fefo_lots.csv")}


def build_deck(
    report: FefoReport,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple[str, ...] = (),
    confidence: float = 0.85,
) -> Deliverable:
    """Compose the expiry study: what is aging out, what is at risk, and how to clear it."""
    def money(value: float | None) -> str:
        return _NOT_MEASURED if value is None else f"{value:,.0f}"

    summary = (
        f"Lot expiry over {report.n_lots} lot(s) / {report.n_products} product(s): "
        f"{report.expired_quantity:,.0f} expired + {report.expiring_quantity:,.0f} expiring soon; "
        f"{report.at_risk_units:,.0f} unit(s) at risk ({money(report.at_risk_value)} cost). "
    )
    summary += (
        f"Recommended disposition: {report.recommended} (recover {money(report.recovered_value)})."
        if report.recommended is not None
        else "Recommended disposition: " + _NOT_MEASURED + " - " + "; ".join(report.basis.notes) + "."
    )

    findings = [
        Finding(
            "Shelf-life aging",
            "; ".join(f"{b.label}: {b.quantity:,.0f} unit(s) ({b.value:,.0f})" for b in report.aging),
            impact="expired and expiring stock is cash about to be written off",
        ),
        Finding(
            "At-risk quantity (won't sell before expiry)",
            f"{report.at_risk_units:,.0f} unit(s) at risk, {money(report.at_risk_value)} cost exposure.",
            impact="act before expiry to recover value instead of scrapping",
        ),
    ]
    if report.recommended is not None:
        findings.append(Finding(
            f"Recommended disposition: {report.recommended}",
            f"Markdown recovers {money(report.markdown_recovery)} vs scrap "
            f"{money(report.scrap_recovery)}; best recovery {money(report.recovered_value)}.",
            impact="clears the at-risk units at the higher recovery",
        ))
    else:
        # The finding that used to recommend destroying stock priced from nothing.
        findings.append(Finding(
            "Disposition NOT determinable",
            "; ".join(report.basis.notes) + ".",
            impact="markdown and scrap cannot be compared until the money columns arrive; "
                   "no disposition is issued",
        ))
    for note in report.notes[len(report.basis.notes):]:
        findings.append(Finding("Forward risk not assessed", note,
                                impact="those lots are aged but their at-risk quantity is unknown"))

    kpis = (
        Kpi("Lots", f"{report.n_lots}", rationale="On-hand lots assessed"),
        Kpi("Expired + expiring units", f"{report.expired_quantity + report.expiring_quantity:,.0f}",
            target="minimize", rationale="Stock at or near end of shelf life"),
        Kpi("At-risk units", f"{report.at_risk_units:,.0f}", target="minimize",
            rationale="Units demand can't consume before expiry"),
        Kpi("At-risk cost", money(report.at_risk_value), target="minimize",
            rationale="Cost exposure of the at-risk units"),
        Kpi("Recoverable value", money(report.recovered_value), target="maximize",
            rationale=(f"Best recovery via {report.recommended}" if report.recommended
                       else "No disposition: the valuation basis is incomplete")),
    )

    data_sources = (
        DataSource("Lots (quantity, days to expiry, cost/price)", "WMS / batch records", "daily"),
        DataSource("Daily demand per product", "Sales history / forecast", "per planning cycle"),
    )

    if report.recommended is not None:
        disposition_rec = (f"{report.recommended.capitalize()} the at-risk units before expiry "
                           f"to recover {money(report.recovered_value)}.")
    else:
        # Asking for the column is the only honest next step, and it is also the only
        # one the client can act on today.
        wanted = ", ".join(
            name for name, ok in (("unit_cost", report.basis.cost_measured),
                                  ("unit_price", report.basis.price_measured)) if not ok
        )
        disposition_rec = (f"Send {wanted} for every lot: without it markdown and scrap cannot "
                           "be compared and no disposition can be recommended.")
    recommendations = (
        "Issue stock First-Expired-First-Out so the soonest-to-expire lots ship first.",
        disposition_rec,
        "Tighten ordering on products that repeatedly age out - buy smaller, more often.",
    )

    return Deliverable(
        title="Lot Expiry & FEFO Disposition",
        client=client,
        summary=summary,
        findings=tuple(findings),
        kpis=kpis,
        data_sources=data_sources,
        recommendations=recommendations,
        citations=tuple(citations),
        # The caller's confidence is a ceiling, not a floor: a study whose money half
        # was never supplied cannot be as trustworthy as one whose was.
        confidence=(confidence if report.basis.complete
                    else max(_CONFIDENCE_FLOOR, confidence - _MISSING_BASIS_PENALTY)),
        residual="Physical disposition is a human act: the agent delivers the FEFO pick order and the "
                 "ranked markdown/scrap list to approve - it does not move, mark down or destroy stock. "
                 "Confirm the daily demand rate and the markdown/scrap recovery rates before acting.",
        prepared=prepared,
    )
