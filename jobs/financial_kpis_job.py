"""Inventory financial-KPIs agent job: a per-SKU financials CSV -> the finance pack.

The data-prep + deck half of the financial-KPIs tool. Reads per-SKU financial rows (COGS,
average inventory value, gross margin, units sold/on-hand, net sales) with pandas directly
(deliberately *not* via jobs/intake.py, which the parallel loop owns), rolls them up, and
computes the auditable inventory-finance pack via ``src.financial_kpis`` (turns, DIO, GMROI,
sell-through, inventory-to-sales, cash-to-cash). Optional legs default to 0.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from src.deliverable import DataSource, Deliverable, Finding, Kpi
from src.export import write_summary_csv
from src.financial_kpis import (
    cash_to_cash,
    days_inventory_outstanding,
    gmroi,
    inventory_to_sales,
    inventory_turns,
    sell_through,
)

_PRODUCT_COLS = ("product_id", "sku", "SKU", "item", "Product")
_COGS_COLS = ("cogs", "COGS", "cost_of_goods", "Cost of Goods")
_INV_COLS = ("avg_inventory_value", "average_inventory_value", "avg_inventory", "inventory_value")
_MARGIN_COLS = ("gross_margin", "margin", "Gross Margin")
_UNITS_SOLD_COLS = ("units_sold", "sold", "Units Sold")
_UNITS_OH_COLS = ("units_on_hand", "on_hand", "Units On Hand", "stock")
_NET_SALES_COLS = ("net_sales", "sales", "revenue", "Net Sales")


@dataclass(frozen=True)
class SkuFinance:
    product_id: str
    gmroi: float
    avg_inventory_value: float
    gross_margin: float


@dataclass(frozen=True)
class FinancialReport:
    n_skus: int
    total_cogs: float
    avg_inventory_value: float
    total_gross_margin: float
    total_units_sold: float
    total_units_on_hand: float
    total_net_sales: float
    turns: float
    dio: float
    gmroi: float
    sell_through: float
    inventory_to_sales: float
    dso: float
    dpo: float
    cash_to_cash: float
    worst: tuple[SkuFinance, ...]   # all SKUs, ranked by GMROI ascending (worst first)


def _pick_column(df: pd.DataFrame, override: object, candidates: tuple[str, ...]) -> str | None:
    if override:
        return str(override) if str(override) in df.columns else None
    return next((c for c in candidates if c in df.columns), None)


def prepare_records(df: pd.DataFrame, params: dict | None = None) -> list[dict]:
    """Sniff the financial columns and build one record per SKU."""
    params = params or {}
    product = _pick_column(df, params.get("product_col"), _PRODUCT_COLS)
    cogs = _pick_column(df, params.get("cogs_col"), _COGS_COLS)
    inv = _pick_column(df, params.get("inventory_col"), _INV_COLS)
    missing = [n for n, c in (("product_id", product), ("cogs", cogs), ("avg_inventory_value", inv)) if c is None]
    if missing:
        cols = list(df.columns)[:10]
        raise ValueError(f"could not find {', '.join(missing)}; pass them in params (columns seen: {cols})")

    margin = _pick_column(df, params.get("margin_col"), _MARGIN_COLS)
    sold = _pick_column(df, params.get("units_sold_col"), _UNITS_SOLD_COLS)
    on_hand = _pick_column(df, params.get("units_on_hand_col"), _UNITS_OH_COLS)
    net_sales = _pick_column(df, params.get("net_sales_col"), _NET_SALES_COLS)

    def _num(row, col):
        return float(row[col]) if col and pd.notna(row[col]) else 0.0

    return [
        {
            "product_id": str(row[product]),
            "cogs": float(row[cogs]),
            "avg_inventory_value": float(row[inv]),
            "gross_margin": _num(row, margin),
            "units_sold": _num(row, sold),
            "units_on_hand": _num(row, on_hand),
            "net_sales": _num(row, net_sales),
        }
        for _, row in df.iterrows()
    ]


def prepare(data_path: str, params: dict | None = None) -> list[dict]:
    """Read a financials CSV and build the per-SKU records."""
    return prepare_records(pd.read_csv(data_path), params)


def run(records: list[dict], *, dso: float = 0.0, dpo: float = 0.0) -> FinancialReport:
    """Roll up the records and compute the inventory-finance KPI pack."""
    total_cogs = sum(r["cogs"] for r in records)
    total_inv = sum(r["avg_inventory_value"] for r in records)
    total_margin = sum(r["gross_margin"] for r in records)
    units_sold = sum(r["units_sold"] for r in records)
    units_on_hand = sum(r["units_on_hand"] for r in records)
    net_sales = sum(r["net_sales"] for r in records)

    dio = days_inventory_outstanding(total_inv, total_cogs)
    worst = tuple(sorted(
        (
            SkuFinance(
                product_id=r["product_id"],
                gmroi=gmroi(r["gross_margin"], r["avg_inventory_value"]),
                avg_inventory_value=r["avg_inventory_value"],
                gross_margin=r["gross_margin"],
            )
            for r in records
        ),
        key=lambda s: s.gmroi,
    ))

    return FinancialReport(
        n_skus=len(records),
        total_cogs=total_cogs,
        avg_inventory_value=total_inv,
        total_gross_margin=total_margin,
        total_units_sold=units_sold,
        total_units_on_hand=units_on_hand,
        total_net_sales=net_sales,
        turns=inventory_turns(total_cogs, total_inv),
        dio=dio,
        gmroi=gmroi(total_margin, total_inv),
        sell_through=sell_through(units_sold, units_on_hand),
        inventory_to_sales=inventory_to_sales(total_inv, net_sales),
        dso=dso,
        dpo=dpo,
        cash_to_cash=cash_to_cash(dio, dso, dpo),
        worst=worst,
    )


def verify(report: FinancialReport) -> list[str]:
    """QA gate: SKUs present and a real inventory base to divide by."""
    issues: list[str] = []
    if report.n_skus <= 0:
        issues.append("no SKUs to analyze")
    if report.avg_inventory_value <= 0:
        issues.append("no average inventory value to compute KPIs")
    if not math.isfinite(report.turns):
        issues.append("inventory turns is not finite")
    return issues


def write_operational(report: FinancialReport, out_dir: str | Path, client: str = "Client") -> dict[str, Path]:
    """The machine-readable deliverable: per-SKU GMROI ranking (worst first)."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "product_id": s.product_id,
            "gmroi": round(s.gmroi, 3),
            "gross_margin": round(s.gross_margin, 2),
            "avg_inventory_value": round(s.avg_inventory_value, 2),
        }
        for s in report.worst
    ]
    return {"csv": write_summary_csv(rows, d / "financial_kpis.csv")}


def build_deck(
    report: FinancialReport,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple[str, ...] = (),
    confidence: float = 0.85,
) -> Deliverable:
    """Compose the inventory-finance dashboard: how hard the inventory dollars work."""
    summary = (
        f"Inventory finance across {report.n_skus} SKU(s): {report.turns:.1f} turns, "
        f"{report.dio:.0f}-day DIO, GMROI {report.gmroi:.2f}, "
        f"cash-to-cash {report.cash_to_cash:.0f} days."
    )

    findings = [
        Finding(
            "How hard the inventory dollars work",
            f"GMROI {report.gmroi:.2f} means each inventory dollar returns "
            f"{report.gmroi:.2f} in gross margin; {report.turns:.1f} turns/yr "
            f"({report.dio:.0f} days on hand).",
            impact="below ~1.0 GMROI or under ~4 turns signals overstock / dead stock",
        ),
        Finding(
            "Working capital tied up",
            f"Cash-to-cash {report.cash_to_cash:.0f} days (DIO {report.dio:.0f} + DSO "
            f"{report.dso:.0f} - DPO {report.dpo:.0f}); inventory-to-sales {report.inventory_to_sales:.2f}.",
            impact="every day of DIO removed releases working capital",
        ),
    ]
    if report.worst:
        w = report.worst[0]
        findings.append(Finding(
            f"Weakest GMROI: {w.product_id}",
            f"GMROI {w.gmroi:.2f} on {w.avg_inventory_value:,.0f} of inventory - "
            "the biggest drag on portfolio return.",
            impact="markdown, re-buy less, or delist the bottom GMROI SKUs",
        ))

    kpis = (
        Kpi("Inventory turns", f"{report.turns:.1f}", target="maximize",
            rationale="COGS / average inventory; how fast stock cycles"),
        Kpi("DIO (days)", f"{report.dio:.0f}", target="minimize",
            rationale="Average days a unit sits in stock"),
        Kpi("GMROI", f"{report.gmroi:.2f}", target="maximize",
            rationale="Gross margin return per inventory dollar (>1 earns its keep)"),
        Kpi("Sell-through", f"{report.sell_through * 100:.0f}%", target="maximize",
            rationale="Units sold / (sold + on hand)"),
        Kpi("Inventory-to-sales", f"{report.inventory_to_sales:.2f}", target="minimize",
            rationale="Average inventory value / net sales"),
        Kpi("Cash-to-cash (days)", f"{report.cash_to_cash:.0f}", target="minimize",
            rationale="Cash conversion cycle = DIO + DSO - DPO"),
    )

    data_sources = (
        DataSource("Per-SKU financials (COGS / avg inventory / margin / units / sales)", "ERP + finance close", "monthly"),
        DataSource("DSO / DPO", "AR / AP ledgers", "per run"),
    )

    recommendations = [
        "Attack the bottom-GMROI SKUs first (markdown, smaller re-buys, or delist).",
        "Cut DIO to release working capital; track cash-to-cash monthly.",
        "Set a GMROI / turns floor per ABC class and review against it.",
    ]

    return Deliverable(
        title="Inventory Financial KPIs",
        client=client,
        summary=summary,
        findings=tuple(findings),
        kpis=kpis,
        data_sources=data_sources,
        recommendations=tuple(recommendations),
        citations=tuple(citations),
        confidence=confidence,
        residual="DSO / DPO come from finance, not inventory data - confirm them with the "
                 "controller before quoting the cash-to-cash figure externally.",
        prepared=prepared,
    )
