"""Excess & Obsolete (E&O) agent job: a stock CSV -> dead / excess classification + cash at risk.

The data-prep + deck half of the E&O tool. Reads on-hand stock (quantity, daily demand,
optional unit cost + days since last sale) with pandas directly (deliberately *not* via
jobs/intake.py, which the parallel loop owns) and classifies each SKU healthy / excess / dead
via ``src.excess_obsolete``, sizing the cash tied up in slow and non-moving stock.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from src.deliverable import DataSource, Deliverable, Finding, Kpi
from src.excess_obsolete import DEAD, EXCESS, SkuEO, SkuStock, classify_excess_obsolete
from src.export import write_summary_csv

_PRODUCT_COLS = ("product_id", "sku", "SKU", "item", "Product", "product")
_ONHAND_COLS = ("on_hand", "quantity", "qty", "stock", "units", "On Hand")
_DEMAND_COLS = ("daily_demand", "demand", "demand_rate", "daily_sales", "run_rate")
_COST_COLS = ("unit_cost", "cost", "Unit Cost", "price")
_LASTSALE_COLS = ("days_since_last_sale", "last_sale_days", "days_no_sale", "since_last_sale", "idle_days")

_DEFAULT_TARGET_COVER_DAYS = 90.0
_DEFAULT_DEAD_THRESHOLD_DAYS = 180.0
# The mean Gregorian month, not the warehouse's 30. Cover is stated in months to the
# people who decide (a buyer plans in months, an S&OP cycle runs in months), and a 30-day
# month quietly inflates every figure by ~1.7% - small per SKU, not small over a catalog.
_DAYS_PER_MONTH = 365.25 / 12


@dataclass(frozen=True)
class EOReport:
    n_skus: int
    n_dead: int
    n_excess: int
    n_healthy: int
    total_on_hand_value: float
    dead_value: float
    excess_value: float
    eo_value: float                 # dead_value + excess_value (cash at risk)
    eo_pct_of_value: float          # eo_value / total_on_hand_value
    lines: tuple[SkuEO, ...]        # ranked by at-risk value desc
    target_cover_days: float
    dead_threshold_days: float
    summary: str


def _pick_column(
    df: pd.DataFrame,
    override: object,
    candidates: tuple[str, ...],
    canonical: str | None = None,
) -> str | None:
    """Pick the client's column for a canonical field.

    Order, and it matters: an explicit override wins, then this job's own short
    list, then -- only if both came up empty -- the wide alias table in
    jobs/intake.py.

    The fallback is strictly additive: it fires exactly where this function used
    to return None, which is where the caller used to raise. No export that works
    today changes behaviour, because for those the short list already matched.

    It exists because the short lists here are English-only while intake's table
    is rich in Spanish. A Defontana or Bsale export ("codigo", "existencia",
    "costo_unitario") went through intake and then died in the two jobs that
    produce what the client actually sees. 05-GTM-KERN.md declares the unfair
    advantage in LatAm to be the language; this was the code contradicting it on
    the first file a client uploads.
    """
    if override:
        return str(override) if str(override) in df.columns else None
    directo = next((c for c in candidates if c in df.columns), None)
    if directo is not None or canonical is None:
        return directo

    # Lazy: keeps the import cost off every caller that never needs the fallback.
    from jobs.intake import ALIASES

    conocidas = {str(c).strip().lower(): str(c) for c in df.columns}
    for alias in ALIASES.get(canonical, ()):
        encontrada = conocidas.get(alias.strip().lower())
        if encontrada is not None:
            return encontrada
    return None


def _months_of_cover(days: float) -> float:
    """Days of cover restated in months, preserving inf for a SKU with no demand.

    A unit conversion, on purpose in the deliverable layer rather than on ``SkuEO``:
    ``days_of_cover`` is what the model computes and what src/alerting.py,
    src/liquidation.py and jobs/seo_priority.py consume. Months are how the report is
    read. inf survives the division and must stay inf - "never runs out" is not a big
    number, and rounding it into one would put a dead SKU on a replenishment plan.
    """
    return days / _DAYS_PER_MONTH


def _fmt_cover(value: float) -> str | float:
    """inf renders as the literal 'inf', never as a number a spreadsheet can average."""
    return "inf" if value == float("inf") else round(value, 1)


def _is_number(value: object) -> bool:
    """True when the cell holds a real number - not blank, not text, not inf."""
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _require_numeric(df: pd.DataFrame, product_col: str, col: str, field: str, why: str) -> None:
    """Refuse a column with blanks or junk, naming the SKUs and the consequence."""
    bad = df.loc[~df[col].apply(_is_number), product_col].astype(str).tolist()
    if bad:
        shown = ", ".join(bad[:10]) + (f" (+{len(bad) - 10} more)" if len(bad) > 10 else "")
        raise ValueError(
            f"{len(bad)} SKU(s) have a blank or non-numeric '{field}' in column '{col}': "
            f"{shown}. Fill or drop those rows - {why}."
        )


def prepare_records(df: pd.DataFrame, params: dict | None = None) -> dict:
    """Sniff the stock columns and build the per-SKU stock records."""
    params = params or {}
    product = _pick_column(df, params.get("product_col"), _PRODUCT_COLS, "product_id")
    on_hand = _pick_column(df, params.get("on_hand_col"), _ONHAND_COLS, "on_hand")
    demand = _pick_column(df, params.get("demand_col"), _DEMAND_COLS, "daily_demand")
    missing = [n for n, c in (("product_id", product), ("on_hand", on_hand), ("daily_demand", demand)) if c is None]
    if missing:
        cols = list(df.columns)[:10]
        raise ValueError(f"could not find {', '.join(missing)}; pass them in params (columns seen: {cols})")

    cost = _pick_column(df, params.get("cost_col"), _COST_COLS, "unit_cost")
    last_sale = _pick_column(df, params.get("last_sale_col"), _LASTSALE_COLS, "days_since_last_sale")

    _require_numeric(df, product, on_hand, "on_hand", "it is the quantity every other number is built on")
    # A blank demand used to be read as 0.0, which is `daily_demand <= 0` -> DEAD, and DEAD
    # writes off the SKU's *entire* on-hand quantity as cash at risk and recommends
    # liquidating it. That number is the headline of the public demo scan.
    _require_numeric(df, product, demand, "daily_demand",
                     "a blank one reads as zero demand, which marks the SKU dead and reports "
                     "its whole on-hand value as cash at risk")
    if cost:
        # Valuing one SKU at 0 while the rest carry costs silently shrinks the total
        # "cash at risk" - the one figure the scan exists to produce.
        _require_numeric(df, product, cost, "unit_cost",
                         "a blank one values that SKU at zero and understates the cash at risk")

    stocks = [
        SkuStock(
            product_id=str(row[product]),
            on_hand=float(row[on_hand]),
            daily_demand=float(row[demand]),
            unit_cost=float(row[cost]) if cost else 0.0,
            # None, not 0.0: "we never recorded a last sale" is not "it sold today".
            days_since_last_sale=(
                float(row[last_sale]) if last_sale and _is_number(row[last_sale]) else None
            ),
        )
        for _, row in df.iterrows()
    ]
    return {
        "stocks": stocks,
        "target_cover_days": float(params.get("target_cover_days", _DEFAULT_TARGET_COVER_DAYS)),
        "dead_threshold_days": float(params.get("dead_threshold_days", _DEFAULT_DEAD_THRESHOLD_DAYS)),
    }


def prepare(data_path: str, params: dict | None = None) -> dict:
    """Read a stock CSV and build the E&O payload."""
    return prepare_records(pd.read_csv(data_path), params)


def run(payload: dict) -> EOReport:
    """Classify each SKU and roll up the cash tied up in dead + excess stock."""
    lines = classify_excess_obsolete(
        payload["stocks"], target_cover_days=payload["target_cover_days"],
        dead_threshold_days=payload["dead_threshold_days"],
    )
    dead_value = sum(e.excess_value for e in lines if e.classification == DEAD)
    excess_value = sum(e.excess_value for e in lines if e.classification == EXCESS)
    total_value = sum(e.on_hand_value for e in lines)
    eo_value = dead_value + excess_value
    n_dead = sum(1 for e in lines if e.classification == DEAD)
    n_excess = sum(1 for e in lines if e.classification == EXCESS)
    summary = (
        f"E&O over {len(lines)} SKU(s): {n_dead} dead + {n_excess} excess hold "
        f"{eo_value:,.0f} ({(eo_value / total_value * 100) if total_value > 0 else 0:.0f}% of "
        f"{total_value:,.0f} on-hand value) at risk."
    )
    return EOReport(
        n_skus=len(lines), n_dead=n_dead, n_excess=n_excess,
        n_healthy=len(lines) - n_dead - n_excess,
        total_on_hand_value=total_value, dead_value=dead_value, excess_value=excess_value,
        eo_value=eo_value, eo_pct_of_value=(eo_value / total_value) if total_value > 0 else 0.0,
        lines=tuple(lines), target_cover_days=payload["target_cover_days"],
        dead_threshold_days=payload["dead_threshold_days"], summary=summary,
    )


def verify(report: EOReport) -> list[str]:
    """QA gate: SKUs present, finite non-negative values, E&O share a valid fraction."""
    import math

    issues: list[str] = []
    if report.n_skus <= 0:
        issues.append("no SKUs to assess")
    if not math.isfinite(report.eo_value) or report.eo_value < 0:
        issues.append("E&O value is negative or non-finite")
    if not 0.0 <= report.eo_pct_of_value <= 1.0:
        issues.append(f"E&O share out of [0,1]: {report.eo_pct_of_value}")
    return issues


def write_operational(report: EOReport, out_dir: str | Path, client: str = "Client") -> dict[str, Path]:
    """The machine-readable deliverable: the at-risk SKUs (dead/excess) by cash exposure.

    Cover is written in both units. ``days_of_cover`` is the model's own figure and the
    existing contract; ``months_of_cover`` is the unit the decision is made in - a buyer
    asked to cut stock reasons in months, and "1,278 days" is a number nobody converts in
    their head before agreeing to it. Additive: the days column stays.
    """
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "product_id": e.product_id,
            "classification": e.classification,
            "on_hand": round(e.on_hand, 1),
            "days_of_cover": _fmt_cover(e.days_of_cover),
            "months_of_cover": _fmt_cover(_months_of_cover(e.days_of_cover)),
            "excess_units": round(e.excess_units, 1),
            "at_risk_value": round(e.excess_value, 2),
            "recommended_action": e.recommended_action,
        }
        for e in report.lines
    ]
    return {"csv": write_summary_csv(rows, d / "excess_obsolete.csv")}


def build_deck(
    report: EOReport,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple[str, ...] = (),
    confidence: float = 0.85,
) -> Deliverable:
    """Compose the E&O study: how much cash is stuck in dead and excess stock, and what to do."""
    summary = (
        f"Excess & obsolete over {report.n_skus} SKU(s): {report.n_dead} dead "
        f"({report.dead_value:,.0f}) + {report.n_excess} excess ({report.excess_value:,.0f}) = "
        f"{report.eo_value:,.0f} at risk, {report.eo_pct_of_value * 100:.0f}% of the "
        f"{report.total_on_hand_value:,.0f} on-hand value."
    )

    worst = report.lines[0] if report.lines else None
    findings = [
        Finding(
            "Dead stock (non-moving)",
            f"{report.n_dead} SKU(s) with no sale in {report.dead_threshold_days:.0f}+ days hold "
            f"{report.dead_value:,.0f}.",
            impact="cash and space locked up - liquidate, return to vendor, or write off",
        ),
        Finding(
            "Excess stock (over cover)",
            f"{report.n_excess} SKU(s) above the {report.target_cover_days:.0f}-day cover target hold "
            f"{report.excess_value:,.0f} of excess.",
            impact="stop buying and draw down before it ages into dead stock",
        ),
    ]
    if worst is not None and worst.excess_value > 0:
        # Cover in months, the unit the buyer decides in. inf is spelled out rather than
        # printed: "inf months" reads as a formatting glitch, and rounding it to a large
        # number would hide that the SKU has no demand at all.
        cover_txt = (
            "no demand recorded, cover unbounded"
            if worst.days_of_cover == float("inf")
            else f"{_months_of_cover(worst.days_of_cover):,.1f} months of cover"
        )
        findings.append(Finding(
            f"Largest single exposure: {worst.product_id}",
            f"{worst.classification}, {worst.excess_value:,.0f} at risk "
            f"({worst.on_hand:,.0f} on hand, {cover_txt}).",
            impact="act on this SKU first",
        ))

    kpis = (
        Kpi("SKUs", f"{report.n_skus}", rationale="Stock lines assessed"),
        Kpi("Dead SKUs", f"{report.n_dead}", target="minimize", rationale="No movement past the threshold"),
        Kpi("Excess SKUs", f"{report.n_excess}", target="minimize", rationale="Above the cover target"),
        Kpi("Cash at risk (E&O)", f"{report.eo_value:,.0f}", target="minimize",
            rationale="Dead value + excess value"),
        Kpi("E&O share of value", f"{report.eo_pct_of_value * 100:.0f}%", target="minimize",
            rationale="At-risk cash as a share of on-hand value"),
    )

    data_sources = (
        DataSource("On-hand stock, daily demand, days since last sale", "WMS / sales history", "weekly"),
        DataSource("Unit cost", "Cost master", "per cost review"),
    )

    recommendations = (
        "Liquidate / return / write off the dead stock to release the locked cash and space.",
        "Stop buying the excess SKUs and redistribute or promote them before they age to dead.",
        "Set a days-of-cover ceiling per ABC class to keep excess from rebuilding.",
    )

    return Deliverable(
        title="Excess & Obsolete (E&O) Stock",
        client=client,
        summary=summary,
        findings=tuple(findings),
        kpis=kpis,
        data_sources=data_sources,
        recommendations=recommendations,
        citations=tuple(citations),
        confidence=confidence,
        residual="Disposition is a human / commercial decision: the agent delivers the ranked at-risk "
                 "list and recommended actions to approve - it does not liquidate, return or write off "
                 "stock. Confirm the cover target, the dead threshold and salvage channels before acting.",
        prepared=prepared,
    )
