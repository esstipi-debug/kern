"""Pluggable demand data sources — decouple the engine from any one backend.

The models used to read demand straight from a CSV path. Real deployments pull
demand from a database, an ERP/WMS export, or an API response. This module puts
a single ``DemandSource`` interface in front of all of them, so swapping CSV for
SQL/API later means writing one adapter — not touching the engine.

Built-in adapters:
  - ``CsvDemandSource``       : a demand CSV on disk (today's default)
  - ``DataFrameDemandSource`` : an in-memory DataFrame (SQL/API results)

A future ``SqlDemandSource`` / ``ErpDemandSource`` only needs to satisfy the
``DemandSource`` protocol: ``list_products``, ``demand_series``, ``metadata``.
"""

from __future__ import annotations

import re
from typing import Any, Protocol, runtime_checkable

import numpy as np
import pandas as pd

from src.data_loader import ProductMetadata

REQUIRED_COLUMNS = {"date", "product_id", "quantity"}

# A bare table name is interpolated into SQL, so it must be a safe identifier.
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@runtime_checkable
class DemandSource(Protocol):
    """Any backend that can yield demand history and product metadata."""

    def list_products(self) -> list[str]: ...

    def demand_series(self, product_id: str) -> np.ndarray: ...

    def metadata(self, product_id: str) -> ProductMetadata: ...


def _validate_frame(df: pd.DataFrame) -> pd.DataFrame:
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"missing columns: {missing}")
    return df.sort_values(["product_id", "date"])


def _series_from_frame(df: pd.DataFrame, product_id: str) -> np.ndarray:
    sku = df[df["product_id"] == product_id]
    if sku.empty:
        raise ValueError(f"no rows for product_id={product_id!r}")
    return sku["quantity"].astype(float).to_numpy()


def _metadata_from_frame(
    df: pd.DataFrame,
    product_id: str,
    *,
    periods_per_year: float,
    default_lead_time_periods: float,
) -> ProductMetadata:
    sku = df[df["product_id"] == product_id]
    if sku.empty:
        raise ValueError(f"no rows for product_id={product_id!r}")

    values = sku["quantity"].astype(float).to_numpy()
    mu = float(np.mean(values))
    sigma = float(np.std(values, ddof=1)) if len(values) > 1 else 0.0

    if "unit_cost" in sku.columns:
        costs = sku["unit_cost"].astype(float)
        positive_costs = costs[costs > 0]
        # A cost of exactly $0 is a lost-in-transit value, not a real price (see
        # jobs/inventory_optimization.py's zero-cost skip) -- averaging it in drags
        # mean_unit_cost toward zero and, via holding_cost's 1e-6 floor, explodes the
        # EOQ formula into an absurd order quantity. 0.0 when NO row has a usable
        # cost is a deliberate, explicit "no valid cost observed" signal, distinct
        # from the "column missing entirely" default of 1.0 below.
        unit_cost = float(positive_costs.mean()) if len(positive_costs) > 0 else 0.0
    else:
        unit_cost = 1.0

    if "lead_time_days" in sku.columns:
        lt_periods = float(sku["lead_time_days"].astype(float).median()) * periods_per_year / 365.0
    else:
        lt_periods = default_lead_time_periods

    # sigma_LT, in the same period units as the mean lead time. 0.0 when the file
    # carries no dispersion, which is the deterministic-lead-time case.
    if "lead_time_std" in sku.columns:
        lt_std_periods = float(sku["lead_time_std"].astype(float).median()) * periods_per_year / 365.0
    else:
        lt_std_periods = 0.0

    return ProductMetadata(
        product_id=product_id,
        mean_demand_per_period=mu,
        demand_std_per_period=sigma,
        periods=len(values),
        mean_unit_cost=unit_cost,
        lead_time_periods=lt_periods,
        lead_time_std_periods=lt_std_periods,
    )


class _FrameDemandSource:
    """Shared implementation backed by a validated DataFrame."""

    def __init__(
        self,
        frame: pd.DataFrame,
        *,
        periods_per_year: float = 52.0,
        default_lead_time_periods: float = 2.0,
    ) -> None:
        self._df = _validate_frame(frame)
        self._periods_per_year = periods_per_year
        self._default_lead_time_periods = default_lead_time_periods

    def list_products(self) -> list[str]:
        return sorted(self._df["product_id"].unique().tolist())

    def demand_series(self, product_id: str) -> np.ndarray:
        return _series_from_frame(self._df, product_id)

    def metadata(self, product_id: str) -> ProductMetadata:
        return _metadata_from_frame(
            self._df,
            product_id,
            periods_per_year=self._periods_per_year,
            default_lead_time_periods=self._default_lead_time_periods,
        )


class DataFrameDemandSource(_FrameDemandSource):
    """Demand source backed by an in-memory DataFrame (e.g. SQL/API results)."""


class CsvDemandSource(_FrameDemandSource):
    """Demand source backed by a CSV file on disk."""

    def __init__(
        self,
        path: str,
        *,
        periods_per_year: float = 52.0,
        default_lead_time_periods: float = 2.0,
    ) -> None:
        frame = pd.read_csv(path, parse_dates=["date"])
        super().__init__(
            frame,
            periods_per_year=periods_per_year,
            default_lead_time_periods=default_lead_time_periods,
        )


class SqlDemandSource(_FrameDemandSource):
    """Demand source backed by any DB-API 2.0 connection (live data).

    Pass a live connection (``sqlite3``, ``psycopg2``, ``mysql.connector`` ...)
    and either a ``table`` name or a custom ``query`` returning at least
    ``date``, ``product_id``, ``quantity`` (optionally ``unit_cost``,
    ``lead_time_days``). This is the bridge from CSV exports to a live system:
    swap the connection, keep the rest of the chain unchanged.

    A bare ``table`` name is validated as a SQL identifier before interpolation;
    for anything dynamic, pass a parameterised ``query`` + ``params`` instead.
    """

    def __init__(
        self,
        connection: Any,
        *,
        table: str = "demand",
        query: str | None = None,
        params: list[Any] | tuple[Any, ...] | None = None,
        periods_per_year: float = 52.0,
        default_lead_time_periods: float = 2.0,
    ) -> None:
        if query is None:
            if not _SAFE_IDENTIFIER.match(table):
                raise ValueError(f"unsafe table name: {table!r}")
            query = f"SELECT * FROM {table}"  # noqa: S608 - identifier validated above

        cursor = connection.cursor()
        try:
            cursor.execute(query, params or [])
            columns = [desc[0] for desc in cursor.description]
            rows = cursor.fetchall()
        finally:
            cursor.close()

        frame = pd.DataFrame(rows, columns=columns)
        super().__init__(
            frame,
            periods_per_year=periods_per_year,
            default_lead_time_periods=default_lead_time_periods,
        )
