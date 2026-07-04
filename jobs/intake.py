"""Intake adapter — map arbitrary client demand data to the canonical schema.

Real client files (ERP exports, transaction logs, Kaggle-style sets) arrive in
their own shapes. This detects the date / product / quantity (and optional unit
cost / lead time) columns by header aliases, then normalizes to one row per
product per period (weekly by default) ready for the engine.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

CANONICAL_REQUIRED = ("date", "product_id", "quantity")
CANONICAL_OPTIONAL = ("unit_cost", "lead_time_days")

ALIASES: dict[str, list[str]] = {
    "date": ["date", "order_date", "orderdate", "invoicedate", "invoice_date", "week", "period", "ds", "day", "timestamp", "datetime"],
    "product_id": ["product_id", "productid", "sku", "item", "item_id", "itemid", "stockcode", "stock_code", "product", "material", "article", "part_number", "upc"],
    "quantity": ["quantity", "qty", "sales", "demand", "units", "unit_sales", "unitsales", "order_qty", "orderqty", "volume", "sold", "units_sold"],
    "unit_cost": ["unit_cost", "unitcost", "price", "unitprice", "unit_price", "cost", "sell_price", "sellprice", "selling_price"],
    "lead_time_days": ["lead_time_days", "leadtimedays", "lead_time", "leadtime", "lead", "lt_days", "supplier_lead_time"],
}


def _norm(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


@dataclass(frozen=True)
class ColumnMapping:
    """Canonical-field -> source-column, plus any required fields not found."""

    mapping: dict[str, str]
    unmatched_required: list[str]

    @property
    def ok(self) -> bool:
        return not self.unmatched_required


def detect_columns(df: pd.DataFrame, overrides: dict[str, str] | None = None) -> ColumnMapping:
    """Heuristically map canonical fields to the client's column names."""
    overrides = overrides or {}
    by_norm = {_norm(c): c for c in df.columns}
    mapping: dict[str, str] = {}
    for canon in (*CANONICAL_REQUIRED, *CANONICAL_OPTIONAL):
        if canon in overrides and overrides[canon] in df.columns:
            mapping[canon] = overrides[canon]
            continue
        for alias in ALIASES[canon]:
            hit = by_norm.get(_norm(alias))
            if hit is not None:
                mapping[canon] = hit
                break
    unmatched = [c for c in CANONICAL_REQUIRED if c not in mapping]
    return ColumnMapping(mapping=mapping, unmatched_required=unmatched)


def load_table(path: str | Path) -> pd.DataFrame:
    """Read a client file (CSV or Excel) into a DataFrame."""
    p = Path(path)
    if p.suffix.lower() in (".xlsx", ".xlsm", ".xls"):
        return pd.read_excel(p)
    return pd.read_csv(p)


def normalize(
    df: pd.DataFrame,
    mapping: ColumnMapping,
    *,
    period: str = "W",
    default_lead_days: float = 14.0,
    default_unit_cost: float = 1.0,
) -> pd.DataFrame:
    """
    Normalize raw client data to the canonical schema, aggregated per period.

    Returns columns: date, product_id, quantity, unit_cost, lead_time_days —
    one row per (product_id, period). ``period`` is a pandas offset alias
    ('W' weekly, 'D' daily, 'MS' month-start).
    """
    if not mapping.ok:
        raise ValueError(f"could not detect required columns: {mapping.unmatched_required}")

    m = mapping.mapping
    out = pd.DataFrame()
    out["date"] = pd.to_datetime(df[m["date"]], errors="coerce")
    out["product_id"] = df[m["product_id"]].astype(str).str.strip()
    out["quantity"] = pd.to_numeric(df[m["quantity"]], errors="coerce")
    if "unit_cost" in m:
        out["unit_cost"] = pd.to_numeric(df[m["unit_cost"]], errors="coerce")
    if "lead_time_days" in m:
        out["lead_time_days"] = pd.to_numeric(df[m["lead_time_days"]], errors="coerce")

    out = out.dropna(subset=["date", "quantity"])
    out = out[out["quantity"] >= 0]
    if out.empty:
        raise ValueError("no usable rows after cleaning (check date/quantity columns)")

    out["bucket"] = out["date"].dt.to_period(period).dt.start_time
    agg: dict[str, str] = {"quantity": "sum"}
    if "unit_cost" in out.columns:
        agg["unit_cost"] = "mean"
    if "lead_time_days" in out.columns:
        agg["lead_time_days"] = "median"

    grouped = out.groupby(["product_id", "bucket"], as_index=False).agg(agg).rename(columns={"bucket": "date"})
    if "unit_cost" not in grouped.columns:
        grouped["unit_cost"] = default_unit_cost
    else:
        grouped["unit_cost"] = grouped["unit_cost"].fillna(default_unit_cost)
    if "lead_time_days" not in grouped.columns:
        grouped["lead_time_days"] = default_lead_days
    else:
        grouped["lead_time_days"] = grouped["lead_time_days"].fillna(default_lead_days)

    grouped = grouped.sort_values(["product_id", "date"]).reset_index(drop=True)
    return grouped[["date", "product_id", "quantity", "unit_cost", "lead_time_days"]]


def prepare(
    path: str | Path,
    *,
    overrides: dict[str, str] | None = None,
    period: str = "W",
    default_lead_days: float = 14.0,
) -> pd.DataFrame:
    """Load a client file and return canonical, period-aggregated demand.

    ``default_lead_days`` fills lead time only where the file carries none
    (missing column or blank cells) — per-SKU CSV values always win.
    """
    raw = load_table(path)
    mapping = detect_columns(raw, overrides)
    return normalize(raw, mapping, period=period, default_lead_days=default_lead_days)
