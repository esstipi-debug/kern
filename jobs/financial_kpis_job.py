"""Inventory financial-KPIs agent job: a per-SKU financials CSV -> the finance pack.

The data-prep + deck half of the financial-KPIs tool. Reads per-SKU financial rows (COGS,
average inventory value, gross margin, units sold/on-hand, net sales) with pandas directly
(deliberately *not* via jobs/intake.py, which the parallel loop owns), rolls them up, and
computes the auditable inventory-finance pack via ``src.financial_kpis`` (turns, DIO, GMROI,
sell-through, inventory-to-sales, cash-to-cash).

An optional column the sniffer could not find, or a blank cell inside one, is **absent**:
it contributes nothing and its roll-up is ``None``. It used to contribute 0.0, which reads
as "this SKU earns no margin" / "this SKU sold nothing" - a measurement nobody took. The
two denominators (COGS, average inventory value) are required outright: a blank there
poisons every ratio in the pack, so it is refused at the door instead.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from src.deliverable import DataSource, Deliverable, Finding, Kpi, not_measured
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
    gmroi: float | None
    avg_inventory_value: float
    gross_margin: float | None


@dataclass(frozen=True)
class FinancialReport:
    """Every ``None`` here means "not measurable from this data", never "zero"."""

    n_skus: int
    total_cogs: float
    avg_inventory_value: float
    total_gross_margin: float | None
    total_units_sold: float | None
    total_units_on_hand: float | None
    total_net_sales: float | None
    turns: float | None
    dio: float | None
    gmroi: float | None
    sell_through: float | None
    inventory_to_sales: float | None
    dso: float | None               # from the AR ledger; None when nobody supplied it
    dpo: float | None               # from the AP ledger; None when nobody supplied it
    cash_to_cash: float | None
    worst: tuple[SkuFinance, ...]   # all SKUs, ranked by GMROI ascending (worst first)


def _pick_column(df: pd.DataFrame, override: object, candidates: tuple[str, ...]) -> str | None:
    if override:
        return str(override) if str(override) in df.columns else None
    return next((c for c in candidates if c in df.columns), None)


def _total(records: list[dict], key: str) -> float | None:
    """Sum the observed values; ``None`` when nothing was observed at all."""
    observed = [r[key] for r in records if r[key] is not None]
    return sum(observed) if observed else None


def _num(value: float | None, digits: int) -> float | str:
    """A CSV cell: the rounded number, or blank. Never 0 standing in for absent."""
    return "" if value is None else round(value, digits)


def fmt(value: float | None, spec: str, lang: str = "en") -> str:
    """A figure, or the words that say it was never measurable.

    Public because the agent surfaces (``scm_agent/tools.py`` summary and
    ``tool_options.financial_kpis_options``) run *after* the QA gate, which only
    blocks on ``turns`` - every other KPI can still legitimately be ``None`` there.
    """
    return not_measured(lang) if value is None else format(value, spec)


_fmt = fmt


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

    def _optional(row, col) -> float | None:
        """An observed number, or ``None``. A blank margin is not a zero margin."""
        if not col:
            return None
        value = row[col]
        if pd.isna(value):
            return None
        number = float(value)
        return number if math.isfinite(number) else None

    for name, col in (("cogs", cogs), ("avg_inventory_value", inv)):
        # `_is_finite` is already False for NaN, so testing isna() as well would
        # count every blank twice - a wrong number inside the honesty check itself.
        blanks = int((~df[col].apply(_is_finite)).sum())
        if blanks:
            raise ValueError(
                f"{blanks} row(s) have a blank or non-numeric '{name}' ({col}); it is a "
                "denominator for every ratio in this pack, so the pack cannot be computed "
                "from them - drop or fill those rows before re-running"
            )

    return [
        {
            "product_id": str(row[product]),
            "cogs": float(row[cogs]),
            "avg_inventory_value": float(row[inv]),
            "gross_margin": _optional(row, margin),
            "units_sold": _optional(row, sold),
            "units_on_hand": _optional(row, on_hand),
            "net_sales": _optional(row, net_sales),
        }
        for _, row in df.iterrows()
    ]


def _is_finite(value: object) -> bool:
    """True when the cell holds a real number (not blank, not text, not inf)."""
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def prepare(data_path: str, params: dict | None = None) -> list[dict]:
    """Read a financials CSV and build the per-SKU records."""
    return prepare_records(pd.read_csv(data_path), params)


def run(
    records: list[dict], *, dso: float | None = None, dpo: float | None = None
) -> FinancialReport:
    """Roll up the records and compute the inventory-finance KPI pack.

    A KPI whose inputs were never observed comes back ``None``. It is not zero, and
    it is certainly not the infinite turns / infinite GMROI the degenerate branches
    used to report.

    ``dso``/``dpo`` come from the AR/AP ledgers, which inventory data never contains.
    Defaulting them to 0.0 made ``cash_to_cash`` return ``DIO + 0 - 0 == DIO`` and the
    deck print it as a measured cycle. Unsupplied is ``None``; a supplied 0.0 (cash on
    delivery) is still a real measurement.
    """
    total_cogs = sum(r["cogs"] for r in records)
    total_inv = sum(r["avg_inventory_value"] for r in records)
    total_margin = _total(records, "gross_margin")
    units_sold = _total(records, "units_sold")
    units_on_hand = _total(records, "units_on_hand")
    net_sales = _total(records, "net_sales")

    dio = days_inventory_outstanding(total_inv, total_cogs)
    worst = tuple(sorted(
        (
            SkuFinance(
                product_id=r["product_id"],
                gmroi=gmroi(r["gross_margin"], r["avg_inventory_value"])
                if r["gross_margin"] is not None else None,
                avg_inventory_value=r["avg_inventory_value"],
                gross_margin=r["gross_margin"],
            )
            for r in records
        ),
        # Unmeasurable GMROI sorts last: it is not the worst performer, it is no reading.
        key=lambda s: (s.gmroi is None, s.gmroi if s.gmroi is not None else 0.0),
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
        gmroi=gmroi(total_margin, total_inv) if total_margin is not None else None,
        sell_through=(
            sell_through(units_sold, units_on_hand)
            if units_sold is not None and units_on_hand is not None else None
        ),
        inventory_to_sales=(
            inventory_to_sales(total_inv, net_sales) if net_sales is not None else None
        ),
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
    if report.turns is None:
        issues.append("inventory turns is not measurable from this data")
    return issues


def write_operational(report: FinancialReport, out_dir: str | Path, client: str = "Client") -> dict[str, Path]:
    """The machine-readable deliverable: per-SKU GMROI ranking (worst first)."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "product_id": s.product_id,
            "gmroi": _num(s.gmroi, 3),
            "gross_margin": _num(s.gross_margin, 2),
            "avg_inventory_value": _num(s.avg_inventory_value, 2),
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
    lang: str = "en",
) -> Deliverable:
    """Compose the inventory-finance dashboard: how hard the inventory dollars work.

    A KPI the data could not support prints ``no medido``. Printing 0.00 GMROI (or an
    infinite turn rate) for a column nobody supplied is the failure this pack is meant
    to find in the client's own reporting.
    """
    turns = fmt(report.turns, ".1f", lang)
    dio, gm = fmt(report.dio, ".0f", lang), fmt(report.gmroi, ".2f", lang)
    c2c = fmt(report.cash_to_cash, ".0f", lang)
    inv_sales = fmt(report.inventory_to_sales, ".2f", lang)
    sell = not_measured(lang) if report.sell_through is None else f"{report.sell_through * 100:.0f}%"

    summary = (
        f"Inventory finance across {report.n_skus} SKU(s): {turns} turns, "
        f"{dio}-day DIO, GMROI {gm}, cash-to-cash {c2c} days."
    )

    findings = [
        Finding(
            "How hard the inventory dollars work",
            f"GMROI {gm} means each inventory dollar returns {gm} in gross margin; "
            f"{turns} turns/yr ({dio} days on hand).",
            impact="below ~1.0 GMROI or under ~4 turns signals overstock / dead stock",
        ),
        Finding(
            "Working capital tied up",
            f"Cash-to-cash {c2c} days (DIO {dio} + DSO "
            f"{fmt(report.dso, '.0f', lang)} - DPO {fmt(report.dpo, '.0f', lang)}); "
            f"inventory-to-sales {inv_sales}.",
            impact="every day of DIO removed releases working capital",
        ),
    ]
    unmeasurable = [
        name for name, value in (
            ("inventory turns", report.turns), ("DIO", report.dio), ("GMROI", report.gmroi),
            ("sell-through", report.sell_through),
            ("inventory-to-sales", report.inventory_to_sales),
            ("cash-to-cash", report.cash_to_cash),
        ) if value is None
    ]
    if unmeasurable:
        findings.append(Finding(
            "KPIs this data could not support",
            f"{', '.join(unmeasurable)} - the inputs were absent, so they are reported as "
            f"'{not_measured(lang)}' rather than as a figure.",
            impact="supply the missing column(s) to complete the finance pack",
        ))
    measured_worst = [s for s in report.worst if s.gmroi is not None]
    if measured_worst:
        w = measured_worst[0]
        findings.append(Finding(
            f"Weakest GMROI: {w.product_id}",
            f"GMROI {w.gmroi:.2f} on {w.avg_inventory_value:,.0f} of inventory - "
            "the biggest drag on portfolio return.",
            impact="markdown, re-buy less, or delist the bottom GMROI SKUs",
        ))

    kpis = (
        Kpi("Inventory turns", turns, target="maximize",
            rationale="COGS / average inventory; how fast stock cycles"),
        Kpi("DIO (days)", dio, target="minimize",
            rationale="Average days a unit sits in stock"),
        Kpi("GMROI", gm, target="maximize",
            rationale="Gross margin return per inventory dollar (>1 earns its keep)"),
        Kpi("Sell-through", sell, target="maximize",
            rationale="Units sold / (sold + on hand)"),
        Kpi("Inventory-to-sales", inv_sales, target="minimize",
            rationale="Average inventory value / net sales"),
        Kpi("Cash-to-cash (days)", c2c, target="minimize",
            rationale="Cash conversion cycle = DIO + DSO - DPO"),
    )

    data_sources = (
        DataSource("Per-SKU financials (COGS / avg inventory / margin / units / sales)", "ERP + finance close", "monthly"),
        DataSource("DSO / DPO", "AR / AP ledgers (not derivable from inventory data)", "per run"),
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
        lang=lang,      # section headers / table columns, not the content
    )
