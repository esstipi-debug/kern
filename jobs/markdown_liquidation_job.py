"""Markdown-liquidation agent job: a stock CSV (+ optional price history) -> a per-SKU
disposition plan with a clearance price, weeks-to-clear, and cash recovered vs. write-off.

The data-prep + deck half of the markdown_liquidation tool. Crosses the E&O
classification (``src.excess_obsolete``) with clearance pricing (``src.pricing``)
via ``src.liquidation.plan_liquidation`` — turning the E&O "you have dead stock"
diagnosis into "sell it at this price, over this many weeks, and recover $X".

Inputs (reusing the existing prep so the client sends familiar files):
  * the same stock CSV the E&O tool reads (``jobs.excess_obsolete_job.prepare_records``);
  * optionally, the same price/quantity history the pricing tool reads
    (``jobs.pricing.prepare_pricing``), passed as ``params['price_history_path']`` —
    SKUs with enough price variation get an elasticity-priced clearance; the rest
    fall back to the documented default-markdown / salvage heuristics.

``resolve_competitor_contexts`` (Linchpin 3.0 PR-19, plan section 7 P4 v2) is this
job's I/O side of the liquidation CALENDAR built on top of this module's
``LiquidationReport`` output — see ``src.liquidation_calendar`` for the pure
calendar/Omnibus/competitive-floor logic itself.
"""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd

from jobs.excess_obsolete_job import prepare_records as _prepare_stock_records
from jobs.pricing import prepare_pricing
from src.deliverable import DataSource, Deliverable, Finding, Kpi
from src.export import write_summary_csv
from src.liquidation import LiquidationReport, PriceHistory, plan_liquidation
from src.price_optimizer import CompetitorPriceContext
from src.pricing_intel.ledger import PriceLedger
from src.pricing_intel.match.sku_map import SkuMap

_DEFAULT_HORIZON_WEEKS = 13.0
_DEFAULT_MARKDOWN_PCT = 0.40
_DEFAULT_SALVAGE_RECOVERY_PCT = 0.30

_CSV_COLUMNS = (
    "product_id", "classification", "method", "units_to_clear", "at_risk_value",
    "clearance_price", "weeks_to_clear", "recovered_value", "recovery_pct",
)


def _load_price_history(params: dict) -> PriceHistory | None:
    """Read the optional price/quantity history into product_id -> (prices, quantities).

    Buckets weekly (the same period the horizon is measured in) so the elasticity
    fit predicts demand-per-week; returns None when no history path was supplied OR
    when the file can't be turned into usable history (missing columns, all-zero/NaN
    prices, no overlapping rows after cleaning). Price history is an OPTIONAL
    enhancement - the engine already degrades cleanly to default-markdown/salvage
    heuristics when it is None (verified in tests), so a broken *optional* file must
    not abort a plan that would otherwise succeed; only a missing/unreadable *stock*
    CSV (the required input) should block the run.
    """
    path = params.get("price_history_path")
    if not path:
        return None
    try:
        demand = prepare_pricing(path, period=str(params.get("price_period", "W")))
    except (ValueError, FileNotFoundError):
        return None
    history: PriceHistory = {
        str(pid): (grp["price"].tolist(), grp["quantity"].tolist())
        for pid, grp in demand.groupby("product_id")
    }
    return history or None


def prepare_records(df: pd.DataFrame, params: dict | None = None) -> dict:
    """Build the liquidation payload from an already-loaded stock frame (+ params)."""
    params = params or {}
    stock = _prepare_stock_records(df, params)
    return {
        "stocks": stock["stocks"],
        "price_history": _load_price_history(params),
        "target_cover_days": stock["target_cover_days"],
        "dead_threshold_days": stock["dead_threshold_days"],
        "horizon_weeks": float(params.get("horizon_weeks", _DEFAULT_HORIZON_WEEKS)),
        "default_markdown_pct": float(params.get("default_markdown_pct", _DEFAULT_MARKDOWN_PCT)),
        "salvage_recovery_pct": float(params.get("salvage_recovery_pct", _DEFAULT_SALVAGE_RECOVERY_PCT)),
        "floor_ratio": float(params.get("floor_ratio", 0.0)),
    }


def prepare(data_path: str, params: dict | None = None) -> dict:
    """Read a stock CSV (+ optional price history) and build the liquidation payload."""
    return prepare_records(pd.read_csv(data_path), params)


def run(payload: dict) -> LiquidationReport:
    """Plan the disposition for every excess/dead SKU."""
    return plan_liquidation(
        payload["stocks"],
        payload["price_history"],
        target_cover_days=payload["target_cover_days"],
        dead_threshold_days=payload["dead_threshold_days"],
        horizon_weeks=payload["horizon_weeks"],
        default_markdown_pct=payload["default_markdown_pct"],
        salvage_recovery_pct=payload["salvage_recovery_pct"],
        floor_ratio=payload["floor_ratio"],
    )


def verify(report: LiquidationReport) -> list[str]:
    """QA gate: finite, non-negative money; positive clearance prices where set.

    An empty plan (all stock healthy — nothing to liquidate) is a valid, non-failing
    outcome, not a QA error: the E&O prep already gates the "no stock at all" case.
    """
    issues: list[str] = []
    if not math.isfinite(report.total_recovered) or report.total_recovered < 0:
        issues.append("recovered value is negative or non-finite")
    if not math.isfinite(report.total_at_risk) or report.total_at_risk < 0:
        issues.append("at-risk value is negative or non-finite")
    for line in report.lines:
        if line.clearance_price is not None and line.clearance_price <= 0:
            issues.append(f"{line.product_id}: non-positive clearance price")
        if not math.isfinite(line.recovered_value) or line.recovered_value < 0:
            issues.append(f"{line.product_id}: invalid recovered value")
    return issues


def resolve_competitor_contexts(
    report: LiquidationReport,
    sku_map: SkuMap,
    ledger: PriceLedger,
) -> dict[str, CompetitorPriceContext]:
    """Resolve a ``{product_id: CompetitorPriceContext}`` map for
    ``src.liquidation_calendar.build_liquidation_calendar``'s competitive
    floor check (Linchpin 3.0 PR-19, plan section 7 P4 v2) -- the I/O side
    of that check, kept out of ``src/liquidation_calendar.py`` on purpose
    (matching ``src.price_optimizer.CompetitorPriceContext``'s own
    established discipline: pure modules never read the ledger themselves).

    For each SKU in ``report.lines``: only a CONFIRMED pricing_intel match
    (``sku_map.latest_confirmed_for_product`` -- the plan S6.5 QA invariant,
    "solo confirmed alimenta P2/A5") is eligible at all; a SKU with only
    ``suspect``/``rejected`` matches, or none, is silently skipped here --
    NOT flagged as an error, since "no confirmed competitor match" is the
    documented v1-compatible fallback the calendar itself falls back to
    (plan QA row). Among a SKU's confirmed matches, the CHEAPEST latest
    observed price across sites (``ledger.latest_for_product``) is used as
    the competitive floor signal -- the binding constraint: if any one
    confirmed competitor is cheaper, undercutting that one still needs a
    documented reason, so picking the cheapest is the conservative choice
    (never the one a caller could cherry-pick to avoid the check).

    Duplicate ``product_id``s in ``report.lines`` are resolved once (the
    lookup is per-product, not per-line -- matching a duplicate SKU's own
    identical competitor signal for every line that shares it).
    """
    contexts: dict[str, CompetitorPriceContext] = {}
    for line in report.lines:
        if line.product_id in contexts:
            continue
        confirmed = sku_map.latest_confirmed_for_product(line.product_id)
        if not confirmed:
            continue
        records = ledger.latest_for_product(line.product_id)
        if not records:
            continue
        cheapest = min(records, key=lambda r: r.offer.price_normalized)
        contexts[line.product_id] = CompetitorPriceContext.from_ledger_record(cheapest)
    return contexts


def write_operational(report: LiquidationReport, out_dir: str | Path, client: str = "Client") -> dict[str, Path]:
    """The machine-readable deliverable: the ranked per-SKU disposition plan.

    An all-healthy portfolio yields an empty (but valid) plan - report.lines == ().
    write_summary_csv([...]) on an empty list would emit a headerless, content-free
    file (pd.DataFrame([]) has no columns), leaving a client staring at a blank
    deliverable with no explanation. Write the stable header explicitly instead so
    "nothing to liquidate" is still a legible, well-formed CSV.
    """
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    out_path = d / "markdown_liquidation.csv"
    if not report.lines:
        pd.DataFrame(columns=list(_CSV_COLUMNS)).to_csv(out_path, index=False)
        return {"csv": out_path}
    rows = [
        {
            "product_id": line.product_id,
            "classification": line.classification,
            "method": line.method,
            "units_to_clear": round(line.units_to_clear, 1),
            "at_risk_value": round(line.at_risk_value, 2),
            "clearance_price": ("" if line.clearance_price is None else round(line.clearance_price, 2)),
            "weeks_to_clear": ("inf" if math.isinf(line.weeks_to_clear) else round(line.weeks_to_clear, 1)),
            "recovered_value": round(line.recovered_value, 2),
            "recovery_pct": round(line.recovery_pct * 100, 1),
        }
        for line in report.lines
    ]
    return {"csv": write_summary_csv(rows, out_path)}


def build_deck(
    report: LiquidationReport,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple[str, ...] = (),
    confidence: float = 0.8,
) -> Deliverable:
    """Compose the liquidation plan: what to clear, at what price, and what it recovers."""
    summary = (
        f"Markdown-liquidation plan over {report.n_assessed} at-risk SKU(s) "
        f"({report.n_excess} excess + {report.n_dead} dead): recover ~{report.total_recovered:,.0f} of "
        f"{report.total_at_risk:,.0f} at risk ({report.recovery_pct * 100:.0f}%) instead of writing it "
        f"to zero, via {report.n_elasticity} elasticity-priced, {report.n_default_discount} "
        f"default-markdown, and {report.n_salvage} salvage disposition(s)."
    )

    worst = report.lines[0] if report.lines else None
    findings = [
        Finding(
            "Cash recoverable vs. write-off",
            f"{report.total_at_risk:,.0f} of excess/dead stock would be lost if written to zero; the "
            f"plan recovers ~{report.total_recovered:,.0f} ({report.recovery_pct * 100:.0f}%).",
            impact="turns a diagnosis into a dated, priced disposition with a recovery number",
        ),
        Finding(
            "Disposition mix",
            f"{report.n_elasticity} SKU(s) priced off a fitted demand curve, "
            f"{report.n_default_discount} at a {report.default_markdown_pct * 100:.0f}% default markdown "
            f"(flat price history), {report.n_salvage} at a {report.salvage_recovery_pct * 100:.0f}% "
            f"salvage recovery (no usable price / non-moving).",
            impact="the elasticity-priced lines are the most defensible; the rest are documented heuristics",
        ),
    ]
    if worst is not None and worst.at_risk_value > 0:
        price = "salvage" if worst.clearance_price is None else f"{worst.clearance_price:,.2f}/unit"
        weeks = "n/a" if math.isinf(worst.weeks_to_clear) else f"~{worst.weeks_to_clear:.0f} wk"
        findings.append(Finding(
            f"Largest exposure: {worst.product_id}",
            f"{worst.classification}, {worst.at_risk_value:,.0f} at risk -> clear at {price} ({weeks}), "
            f"recovering ~{worst.recovered_value:,.0f}.",
            impact="act on this SKU first",
        ))

    kpis = (
        Kpi("At-risk SKUs", f"{report.n_assessed}", rationale="Excess + dead lines assessed"),
        Kpi("Cash at risk", f"{report.total_at_risk:,.0f}", target="minimize",
            rationale="Cost basis of the excess/dead units"),
        Kpi("Cash recoverable", f"{report.total_recovered:,.0f}", target="maximize",
            rationale="Expected recovery if the plan is executed"),
        Kpi("Recovery rate", f"{report.recovery_pct * 100:.0f}%", target="maximize",
            rationale="Recovered vs. writing the stock to zero"),
        Kpi("Clearance horizon", f"{report.horizon_weeks:.0f} weeks", rationale="Target window to drain the stock"),
    )

    data_sources = (
        DataSource("On-hand stock, daily demand, days since last sale", "WMS / sales history", "weekly"),
        DataSource("Unit cost", "Cost master", "per cost review"),
        DataSource("Price/quantity history (optional, for elasticity)", "POS / order history", "weekly"),
    )

    recommendations = (
        "Execute the elasticity-priced clearances first - they are the most defensible and time-bounded.",
        "Treat the default-markdown lines as a starting price; refine once price-response history exists.",
        "Route the salvage lines (dead / no price) to return-to-vendor, jobbers, or a write-down decision.",
        "Re-run after each clearance window to re-rank the remaining exposure.",
    )

    return Deliverable(
        title="Markdown Liquidation Plan",
        client=client,
        summary=summary,
        findings=tuple(findings),
        kpis=kpis,
        data_sources=data_sources,
        recommendations=recommendations,
        citations=tuple(citations),
        confidence=confidence,
        residual=(
            "This is the single-pass deterministic limit of dynamic clearance pricing (Gallego & van "
            "Ryzin), not a multi-stage markdown optimiser and not an inventory-depletion model "
            "(Smith & Achabal); the default-markdown and salvage rates are practitioner heuristics, not "
            "optima, and weeks-to-clear for default-markdown lines is a conservative bound at current "
            "demand. Pricing and disposition are commercial decisions: the agent delivers the ranked, "
            "priced plan to approve - it does not change prices or dispose of stock. Confirm the "
            "clearance horizon, margin floor, and salvage channels before acting."
        ),
        prepared=prepared,
    )
