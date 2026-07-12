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
class IntakeQuality:
    """How much of the raw file survived cleaning, and why the rest didn't.

    ``normalize()`` silently drops unusable rows (bad date, missing quantity,
    negative quantity) so the engine never chokes on them — but "silently" means
    a client-facing deliverable could be built on a shrunken, unrepresentative
    slice of the real data with no signal anywhere that it happened. This is
    that signal, tracked alongside (not instead of) the plain cleaned frame —
    see ``normalize_tracked``/``prepare_tracked``.
    """

    n_raw: int
    n_dropped_bad_date: int
    n_dropped_bad_quantity: int
    n_dropped_negative_quantity: int

    @property
    def n_dropped(self) -> int:
        return self.n_dropped_bad_date + self.n_dropped_bad_quantity + self.n_dropped_negative_quantity

    @property
    def dropped_fraction(self) -> float:
        return self.n_dropped / self.n_raw if self.n_raw else 0.0


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


def _normalize_core(
    df: pd.DataFrame,
    mapping: ColumnMapping,
    *,
    period: str,
    default_lead_days: float,
    default_unit_cost: float,
) -> tuple[pd.DataFrame, IntakeQuality]:
    """Shared engine behind ``normalize``/``normalize_tracked`` — identical
    cleaning + aggregation, plus a per-reason count of every row dropped along
    the way. Each row is attributed to the FIRST filter that removes it (bad
    date -> bad quantity -> negative quantity), matching the actual left-to-right
    elimination order below, so counts never double-count a single bad row.
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

    n_raw = len(out)
    after_date = out.dropna(subset=["date"])
    n_dropped_bad_date = n_raw - len(after_date)
    after_quantity = after_date.dropna(subset=["quantity"])
    n_dropped_bad_quantity = len(after_date) - len(after_quantity)
    out = after_quantity[after_quantity["quantity"] >= 0]
    n_dropped_negative_quantity = len(after_quantity) - len(out)
    quality = IntakeQuality(
        n_raw=n_raw,
        n_dropped_bad_date=n_dropped_bad_date,
        n_dropped_bad_quantity=n_dropped_bad_quantity,
        n_dropped_negative_quantity=n_dropped_negative_quantity,
    )
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
    return grouped[["date", "product_id", "quantity", "unit_cost", "lead_time_days"]], quality


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
    ('W' weekly, 'D' daily, 'MS' month-start). Drops unusable rows (bad date,
    missing/negative quantity) silently — use ``normalize_tracked`` for a
    caller that needs to know how much was dropped and why.
    """
    frame, _quality = _normalize_core(
        df, mapping, period=period, default_lead_days=default_lead_days, default_unit_cost=default_unit_cost
    )
    return frame


def normalize_tracked(
    df: pd.DataFrame,
    mapping: ColumnMapping,
    *,
    period: str = "W",
    default_lead_days: float = 14.0,
    default_unit_cost: float = 1.0,
) -> tuple[pd.DataFrame, IntakeQuality]:
    """Same cleaning + aggregation as ``normalize``, plus the ``IntakeQuality``
    signal (rows dropped, and why) a caller needs to decide whether the result
    is trustworthy enough to hand back with unqualified confidence."""
    return _normalize_core(
        df, mapping, period=period, default_lead_days=default_lead_days, default_unit_cost=default_unit_cost
    )


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


def prepare_tracked(
    path: str | Path,
    *,
    overrides: dict[str, str] | None = None,
    period: str = "W",
    default_lead_days: float = 14.0,
) -> tuple[pd.DataFrame, IntakeQuality]:
    """Same as ``prepare``, plus the ``IntakeQuality`` signal for a caller that
    needs to decide whether the result is trustworthy enough to hand back with
    unqualified confidence — see ``normalize_tracked``."""
    raw = load_table(path)
    mapping = detect_columns(raw, overrides)
    return normalize_tracked(raw, mapping, period=period, default_lead_days=default_lead_days)
