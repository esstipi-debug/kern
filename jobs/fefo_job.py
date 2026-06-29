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

from dataclasses import dataclass
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


@dataclass(frozen=True)
class FefoReport:
    n_lots: int
    n_products: int
    total_quantity: float
    total_value: float
    aging: tuple[ExpiryBucket, ...]
    expired_quantity: float
    expiring_quantity: float
    at_risk_units: float
    at_risk_value: float
    disposition: DispositionPlan
    lots_fefo: tuple[Lot, ...]
    at_risk_by_lot: dict[str, float]
    summary: str


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
    for _, row in df.iterrows():
        pid = str(row[product])
        if days_col is not None:
            days = float(row[days_col])
        else:
            days = float((pd.to_datetime(row[expiry_col]) - as_of_dt).days)
        lots.append(Lot(
            lot_id=str(row[lot]), product_id=pid, quantity=float(row[qty]), days_to_expiry=days,
            unit_cost=float(row[cost]) if cost and pd.notna(row[cost]) else 0.0,
            unit_price=float(row[price]) if price and pd.notna(row[price]) else 0.0,
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
    summary = (
        f"Lot expiry over {len(lots)} lot(s): {expired_q:,.0f} expired + {expiring_q:,.0f} expiring soon; "
        f"{disposition.at_risk_units:,.0f} unit(s) at risk ({disposition.at_risk_cost:,.0f} cost), "
        f"recommend {disposition.recommended} (recover {disposition.recovered_value:,.0f})."
    )
    return FefoReport(
        n_lots=len(lots), n_products=len({lot.product_id for lot in lots}),
        total_quantity=sum(lot.quantity for lot in lots),
        total_value=sum(lot.quantity * lot.unit_cost for lot in lots),
        aging=tuple(aging), expired_quantity=expired_q, expiring_quantity=expiring_q,
        at_risk_units=disposition.at_risk_units, at_risk_value=disposition.at_risk_cost,
        disposition=disposition, lots_fefo=tuple(fefo_order(lots)),
        at_risk_by_lot={r.lot_id: r.at_risk_quantity for r in at_risk}, summary=summary,
    )


def verify(report: FefoReport) -> list[str]:
    """QA gate: lots present, finite quantities, a valid disposition recommendation."""
    import math

    issues: list[str] = []
    if report.n_lots <= 0:
        issues.append("no lots to assess")
    if not math.isfinite(report.total_quantity) or report.total_quantity < 0:
        issues.append("total quantity is negative or non-finite")
    if report.at_risk_units < 0 or not math.isfinite(report.at_risk_units):
        issues.append("at-risk units invalid")
    if report.disposition.recommended not in ("markdown", "scrap"):
        issues.append(f"unknown disposition: {report.disposition.recommended}")
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
    disp = report.disposition
    summary = (
        f"Lot expiry over {report.n_lots} lot(s) / {report.n_products} product(s): "
        f"{report.expired_quantity:,.0f} expired + {report.expiring_quantity:,.0f} expiring soon; "
        f"{report.at_risk_units:,.0f} unit(s) at risk ({report.at_risk_value:,.0f} cost). "
        f"Recommended disposition: {disp.recommended} (recover {disp.recovered_value:,.0f})."
    )

    findings = [
        Finding(
            "Shelf-life aging",
            "; ".join(f"{b.label}: {b.quantity:,.0f} unit(s) ({b.value:,.0f})" for b in report.aging),
            impact="expired and expiring stock is cash about to be written off",
        ),
        Finding(
            "At-risk quantity (won't sell before expiry)",
            f"{report.at_risk_units:,.0f} unit(s) at risk, {report.at_risk_value:,.0f} cost exposure.",
            impact="act before expiry to recover value instead of scrapping",
        ),
        Finding(
            f"Recommended disposition: {disp.recommended}",
            f"Markdown recovers {disp.markdown_recovery:,.0f} vs scrap {disp.scrap_recovery:,.0f}; "
            f"best recovery {disp.recovered_value:,.0f}.",
            impact="clears the at-risk units at the higher recovery",
        ),
    ]

    kpis = (
        Kpi("Lots", f"{report.n_lots}", rationale="On-hand lots assessed"),
        Kpi("Expired + expiring units", f"{report.expired_quantity + report.expiring_quantity:,.0f}",
            target="minimize", rationale="Stock at or near end of shelf life"),
        Kpi("At-risk units", f"{report.at_risk_units:,.0f}", target="minimize",
            rationale="Units demand can't consume before expiry"),
        Kpi("At-risk cost", f"{report.at_risk_value:,.0f}", target="minimize",
            rationale="Cost exposure of the at-risk units"),
        Kpi("Recoverable value", f"{disp.recovered_value:,.0f}", target="maximize",
            rationale=f"Best recovery via {disp.recommended}"),
    )

    data_sources = (
        DataSource("Lots (quantity, days to expiry, cost/price)", "WMS / batch records", "daily"),
        DataSource("Daily demand per product", "Sales history / forecast", "per planning cycle"),
    )

    recommendations = (
        "Issue stock First-Expired-First-Out so the soonest-to-expire lots ship first.",
        f"{disp.recommended.capitalize()} the at-risk units before expiry to recover {disp.recovered_value:,.0f}.",
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
        confidence=confidence,
        residual="Physical disposition is a human act: the agent delivers the FEFO pick order and the "
                 "ranked markdown/scrap list to approve - it does not move, mark down or destroy stock. "
                 "Confirm the daily demand rate and the markdown/scrap recovery rates before acting.",
        prepared=prepared,
    )
