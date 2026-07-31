"""Intake adapter — map arbitrary client demand data to the canonical schema.

Real client files (ERP exports, transaction logs, Kaggle-style sets) arrive in
their own shapes. This detects the date / product / quantity (and optional unit
cost / lead time) columns by header aliases, then normalizes to one row per
product per period (weekly by default) ready for the engine.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path

import pandas as pd

CANONICAL_REQUIRED = ("date", "product_id", "quantity")
# ``revenue`` is detected but never emitted: it exists only to recover a real
# unit cost (revenue / quantity) from files that carry money but no cost column.
CANONICAL_OPTIONAL = ("unit_cost", "lead_time_days", "lead_time_std", "revenue")

ALIASES: dict[str, list[str]] = {
    "date": ["date", "order_date", "orderdate", "invoicedate", "invoice_date", "week", "period", "ds", "day", "timestamp", "datetime"],
    "product_id": ["product_id", "productid", "sku", "item", "item_id", "itemid", "stockcode", "stock_code", "product", "material", "article", "part_number", "upc"],
    "quantity": ["quantity", "qty", "sales", "demand", "units", "unit_sales", "unitsales", "order_qty", "orderqty", "volume", "sold", "units_sold"],
    "unit_cost": ["unit_cost", "unitcost", "price", "unitprice", "unit_price", "cost", "sell_price", "sellprice", "selling_price"],
    "lead_time_days": ["lead_time_days", "leadtimedays", "lead_time", "leadtime", "lead", "lt_days", "supplier_lead_time"],
    # Lead-time dispersion (sigma_LT). Same alias set jobs/launch_readiness_job.py
    # already accepts, so a file that works there works here too.
    "lead_time_std": ["lead_time_std", "leadtimestd", "lead_std", "sigma_lead", "sigma_l"],
    "revenue": ["revenue", "sales", "sales_amount", "amount", "net_sales", "line_total", "total"],
}


def _norm(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _parse_dates(series: pd.Series) -> pd.Series:
    """Parse a date column, tolerating a genuinely mixed-format column.

    ``pd.to_datetime`` without ``format="mixed"`` locks onto whichever format
    it infers from the column and silently turns every row in a DIFFERENT
    format into NaT -- a real failure mode for messy client exports that mix
    ISO and slash-delimited dates in the same column, not a hypothetical.
    ``format="mixed"`` infers the format PER VALUE instead of once for the
    whole column, so it does not drop rows just because an earlier row set a
    different format.

    Known, accepted limitation: a slash-delimited date where BOTH components
    are <= 12 (e.g. "06/01/2025") is genuinely ambiguous without locale
    context, and this defaults to month-first (the international/US
    convention) absent one. That is a narrow, honest ambiguity -- it never
    drops a row and it never corrupts an already-unambiguous date (ISO, or
    any slash date with a component > 12) the way an earlier, reverted
    ``dayfirst=True`` retry was found to do (it silently swapped month/day on
    ~39% of ISO dates whenever a mixed column happened to lock onto a slash
    format first).
    """
    return pd.to_datetime(series, errors="coerce", format="mixed")


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
    # Canonical column -> how many cells were filled from a default rather than read
    # from the file. Dropped rows were never the only way a deliverable could rest on
    # data that is not the client's: a column absent from the source is synthesized
    # wholesale, and counting rows alone reports that as perfect quality.
    synthesized: tuple[tuple[str, int], ...] = ()
    n_canonical_rows: int = 0

    @property
    def n_dropped(self) -> int:
        return self.n_dropped_bad_date + self.n_dropped_bad_quantity + self.n_dropped_negative_quantity

    @property
    def dropped_fraction(self) -> float:
        return self.n_dropped / self.n_raw if self.n_raw else 0.0

    @property
    def fully_synthesized_columns(self) -> tuple[str, ...]:
        """Columns where every single cell came from a default."""
        if not self.n_canonical_rows:
            return ()
        return tuple(
            name for name, count in self.synthesized if count >= self.n_canonical_rows
        )


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
    out["date"] = _parse_dates(df[m["date"]])
    out["product_id"] = df[m["product_id"]].astype(str).str.strip()
    out["quantity"] = pd.to_numeric(df[m["quantity"]], errors="coerce")
    if "unit_cost" in m:
        out["unit_cost"] = pd.to_numeric(df[m["unit_cost"]], errors="coerce")
    elif "revenue" in m and m["revenue"] != m["quantity"]:
        # No cost column, but the file carries money: recover the real unit cost
        # rather than falling back to a flat default (which makes any value-based
        # ranking collapse onto units). Guarded against revenue *being* the
        # quantity column -- 'sales' is an alias of both.
        revenue = pd.to_numeric(df[m["revenue"]], errors="coerce")
        qty = out["quantity"]
        out["unit_cost"] = (revenue / qty.where(qty != 0)).replace(
            [float("inf"), float("-inf")], None
        )
    if "lead_time_days" in m:
        out["lead_time_days"] = pd.to_numeric(df[m["lead_time_days"]], errors="coerce")
    if "lead_time_std" in m:
        out["lead_time_std"] = pd.to_numeric(df[m["lead_time_std"]], errors="coerce")

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
    if "lead_time_std" in out.columns:
        agg["lead_time_std"] = "median"

    grouped = out.groupby(["product_id", "bucket"], as_index=False).agg(agg).rename(columns={"bucket": "date"})
    synthesized: dict[str, int] = {}

    def _fill(column: str, default: float) -> None:
        """Fill a missing/blank optional column and record how many cells we invented."""
        if column not in grouped.columns:
            synthesized[column] = len(grouped)
            grouped[column] = default
            return
        blank = int(grouped[column].isna().sum())
        if blank:
            synthesized[column] = blank
            grouped[column] = grouped[column].fillna(default)

    _fill("unit_cost", default_unit_cost)
    _fill("lead_time_days", default_lead_days)
    # 0.0 = "no dispersion known", which is what the engine assumed before this
    # column existed — so a file without it behaves exactly as it did.
    _fill("lead_time_std", 0.0)

    quality = replace(
        quality,
        synthesized=tuple(sorted(synthesized.items())),
        n_canonical_rows=len(grouped),
    )

    grouped = grouped.sort_values(["product_id", "date"]).reset_index(drop=True)
    return grouped[["date", "product_id", "quantity", "unit_cost", "lead_time_days", "lead_time_std"]], quality


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
    default_unit_cost: float = 1.0,
) -> pd.DataFrame:
    """Load a client file and return canonical, period-aggregated demand.

    ``default_lead_days`` / ``default_unit_cost`` fill lead time and unit cost only
    where the file carries none (missing column or blank cells) — per-SKU CSV values
    always win, and a unit cost derived from revenue/quantity wins over the default.
    """
    raw = load_table(path)
    mapping = detect_columns(raw, overrides)
    return normalize(
        raw, mapping, period=period, default_lead_days=default_lead_days,
        default_unit_cost=default_unit_cost,
    )


def prepare_tracked(
    path: str | Path,
    *,
    overrides: dict[str, str] | None = None,
    period: str = "W",
    default_lead_days: float = 14.0,
    default_unit_cost: float = 1.0,
) -> tuple[pd.DataFrame, IntakeQuality]:
    """Same as ``prepare``, plus the ``IntakeQuality`` signal for a caller that
    needs to decide whether the result is trustworthy enough to hand back with
    unqualified confidence — see ``normalize_tracked``."""
    raw = load_table(path)
    mapping = detect_columns(raw, overrides)
    return normalize_tracked(
        raw, mapping, period=period, default_lead_days=default_lead_days,
        default_unit_cost=default_unit_cost,
    )
