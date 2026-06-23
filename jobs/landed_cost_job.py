"""Landed-cost agent job: a shipment CSV -> Incoterm-aware fully-landed cost per SKU.

The data-prep + deck half of the landed-cost tool. Reads shipment lines (unit cost, qty,
freight, insurance, duty rate, Incoterm, ...) with pandas directly (deliberately *not*
via jobs/intake.py, which the parallel loop owns), computes each line's fully-landed cost
via ``src.landed_cost`` (Incoterm-aware duty base), and composes the study deck inline.
Column names are sniffed and overridable via params; optional cost legs default to 0 and
the Incoterm to FOB.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from src.deliverable import DataSource, Deliverable, Finding, Kpi
from src.export import write_summary_csv
from src.landed_cost import LandedCost, landed_cost

_SKU_COLS = ("sku", "SKU", "product_id", "item", "Part", "part_id")
_UNIT_COST_COLS = ("unit_cost", "Unit Cost", "cost", "price", "Price")
_QTY_COLS = ("qty", "quantity", "Quantity", "units", "Units")
_FREIGHT_COLS = ("freight", "Freight", "shipping", "Shipping Cost")
_INSURANCE_COLS = ("insurance", "Insurance")
_DUTY_RATE_COLS = ("duty_rate", "Duty Rate", "duty", "tariff_rate")
_HANDLING_COLS = ("handling", "Handling")
_BROKER_COLS = ("broker_fee", "Broker Fee", "broker", "customs_fee")
_INCOTERM_COLS = ("incoterm", "Incoterm", "terms")


@dataclass(frozen=True)
class LandedLine:
    sku: str
    landed: LandedCost


@dataclass(frozen=True)
class LandedCostReport:
    lines: tuple[LandedLine, ...]   # sorted by total landed cost desc
    n_lines: int
    total_goods: float
    total_freight: float
    total_insurance: float
    total_duty: float
    total_landed: float
    landed_uplift_pct: float        # (landed - goods) / goods


def _pick_column(df: pd.DataFrame, override: object, candidates: tuple[str, ...]) -> str | None:
    if override:
        return str(override) if str(override) in df.columns else None
    return next((c for c in candidates if c in df.columns), None)


def prepare_records(df: pd.DataFrame, params: dict | None = None) -> list[dict]:
    """Sniff the landed-cost columns and build one input record per shipment line."""
    params = params or {}
    sku = _pick_column(df, params.get("sku_col"), _SKU_COLS)
    unit_cost = _pick_column(df, params.get("unit_cost_col"), _UNIT_COST_COLS)
    qty = _pick_column(df, params.get("qty_col"), _QTY_COLS)
    missing = [n for n, c in (("sku", sku), ("unit_cost", unit_cost), ("qty", qty)) if c is None]
    if missing:
        cols = list(df.columns)[:10]
        raise ValueError(f"could not find {', '.join(missing)}; pass them in params (columns seen: {cols})")

    freight = _pick_column(df, params.get("freight_col"), _FREIGHT_COLS)
    insurance = _pick_column(df, params.get("insurance_col"), _INSURANCE_COLS)
    duty_rate = _pick_column(df, params.get("duty_rate_col"), _DUTY_RATE_COLS)
    handling = _pick_column(df, params.get("handling_col"), _HANDLING_COLS)
    broker = _pick_column(df, params.get("broker_col"), _BROKER_COLS)
    incoterm = _pick_column(df, params.get("incoterm_col"), _INCOTERM_COLS)

    def _num(row, col):
        return float(row[col]) if col else 0.0

    return [
        {
            "sku": str(row[sku]),
            "unit_cost": float(row[unit_cost]),
            "qty": float(row[qty]),
            "freight": _num(row, freight),
            "insurance": _num(row, insurance),
            "duty_rate": _num(row, duty_rate),
            "handling": _num(row, handling),
            "broker_fee": _num(row, broker),
            "incoterm": str(row[incoterm]) if incoterm else "FOB",
        }
        for _, row in df.iterrows()
    ]


def prepare(data_path: str, params: dict | None = None) -> list[dict]:
    """Read a shipment CSV and build the landed-cost input records."""
    return prepare_records(pd.read_csv(data_path), params)


def run(records: list[dict]) -> LandedCostReport:
    """Compute the fully-landed cost per line and roll up the totals."""
    lines: list[LandedLine] = []
    for r in records:
        lc = landed_cost(
            r["unit_cost"], r["qty"],
            freight=r["freight"], insurance=r["insurance"], duty_rate=r["duty_rate"],
            handling=r["handling"], broker_fee=r["broker_fee"], incoterm=r["incoterm"],
        )
        lines.append(LandedLine(r["sku"], lc))
    lines.sort(key=lambda ln: ln.landed.total, reverse=True)

    total_goods = sum(ln.landed.goods_value for ln in lines)
    total_landed = sum(ln.landed.total for ln in lines)
    return LandedCostReport(
        lines=tuple(lines),
        n_lines=len(lines),
        total_goods=total_goods,
        total_freight=sum(ln.landed.freight for ln in lines),
        total_insurance=sum(ln.landed.insurance for ln in lines),
        total_duty=sum(ln.landed.duty for ln in lines),
        total_landed=total_landed,
        landed_uplift_pct=((total_landed - total_goods) / total_goods) if total_goods > 0 else 0.0,
    )


def verify(report: LandedCostReport) -> list[str]:
    """QA gate: lines present and each landed total is at least the goods value."""
    issues: list[str] = []
    if not report.lines:
        issues.append("no shipment lines to cost")
    for ln in report.lines:
        if ln.landed.total + 1e-9 < ln.landed.goods_value:
            issues.append(f"landed total below goods value for {ln.sku}")
    return issues


def write_operational(report: LandedCostReport, out_dir: str | Path, client: str = "Client") -> dict[str, Path]:
    """The machine-readable deliverable: one row per SKU with the cost breakdown."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "sku": ln.sku,
            "incoterm": ln.landed.incoterm,
            "qty": round(ln.landed.qty, 2),
            "goods_value": round(ln.landed.goods_value, 2),
            "freight": round(ln.landed.freight, 2),
            "insurance": round(ln.landed.insurance, 2),
            "duty": round(ln.landed.duty, 2),
            "handling": round(ln.landed.handling, 2),
            "broker_fee": round(ln.landed.broker_fee, 2),
            "total_landed": round(ln.landed.total, 2),
            "per_unit_landed": round(ln.landed.per_unit, 4),
        }
        for ln in report.lines
    ]
    return {"csv": write_summary_csv(rows, d / "landed_cost.csv")}


def build_deck(
    report: LandedCostReport,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple[str, ...] = (),
    confidence: float = 0.85,
) -> Deliverable:
    """Compose the landed-cost study: the true delivered cost and where it accrues."""
    uplift = report.landed_uplift_pct * 100
    adders = report.total_landed - report.total_goods
    summary = (
        f"Landed cost across {report.n_lines} SKU(s): {report.total_landed:,.0f} total - "
        f"{uplift:.0f}% over the {report.total_goods:,.0f} goods value."
    )

    findings = [
        Finding(
            "True delivered cost is above invoice price",
            f"Freight, insurance and duty add {adders:,.0f} ({uplift:.0f}%) on top of goods - "
            "price-only sourcing comparisons understate cost.",
            impact="compare suppliers on landed cost, not unit price",
        ),
        Finding(
            "Cost adders breakdown",
            f"Freight {report.total_freight:,.0f}, insurance {report.total_insurance:,.0f}, "
            f"duty {report.total_duty:,.0f} - duty base follows the Incoterm.",
            impact="target the largest leg (often freight or duty)",
        ),
    ]
    top = report.lines[0] if report.lines else None
    if top is not None:
        findings.append(Finding(
            f"Highest landed cost: {top.sku}",
            f"{top.landed.total:,.0f} landed ({top.landed.per_unit:,.2f}/unit, {top.landed.incoterm}).",
            impact="biggest lever for a cost-down or re-sourcing",
        ))

    kpis = (
        Kpi("Total landed cost", f"{report.total_landed:,.0f}", rationale="Fully delivered cost of the shipments"),
        Kpi("Goods value", f"{report.total_goods:,.0f}", rationale="Invoice price before logistics + duty"),
        Kpi("Landed uplift", f"{uplift:.0f}%", target="minimize",
            rationale="How much freight/insurance/duty add over invoice"),
        Kpi("Duty", f"{report.total_duty:,.0f}", rationale="Tariff cost (Incoterm-aware duty base)"),
        Kpi("Freight", f"{report.total_freight:,.0f}", rationale="Inbound transport cost"),
    )

    data_sources = (
        DataSource("Shipment lines (unit cost / qty / freight / insurance / duty rate / Incoterm)", "supplier quotes / freight invoices", "per shipment"),
        DataSource("Duty rates / Incoterm", "tariff schedule + contract terms", "per run"),
    )

    recommendations = [
        "Compare and award suppliers on landed cost, not unit price.",
        "Attack the largest cost leg (renegotiate freight, review Incoterm, or verify duty classification).",
    ]

    return Deliverable(
        title="Landed-Cost Study",
        client=client,
        summary=summary,
        findings=tuple(findings),
        kpis=kpis,
        data_sources=data_sources,
        recommendations=tuple(recommendations),
        citations=tuple(citations),
        confidence=confidence,
        residual="HS-code classification and duty rates are advisory here - confirm tariff "
                 "treatment with a licensed customs broker before relying on the duty figures.",
        prepared=prepared,
    )
