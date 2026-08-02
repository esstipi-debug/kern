"""Tests for pluggable demand data sources."""

import sqlite3

import numpy as np
import pandas as pd
import pytest

from src.sources import (
    CsvDemandSource,
    DataFrameDemandSource,
    DemandSource,
    SqlDemandSource,
)


def _frame():
    return pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-01", "2024-01-08", "2024-01-01", "2024-01-08"]),
            "product_id": ["SKU-A", "SKU-A", "SKU-B", "SKU-B"],
            "quantity": [100, 120, 40, 60],
            "unit_cost": [50, 50, 10, 10],
            "lead_time_days": [7, 7, 14, 14],
        }
    )


def test_dataframe_source_lists_and_returns_series():
    src = DataFrameDemandSource(_frame())
    assert src.list_products() == ["SKU-A", "SKU-B"]
    np.testing.assert_array_equal(src.demand_series("SKU-A"), np.array([100.0, 120.0]))


def test_dataframe_source_metadata():
    src = DataFrameDemandSource(_frame())
    meta = src.metadata("SKU-B")
    assert meta.product_id == "SKU-B"
    assert meta.mean_demand_per_period == pytest.approx(50.0)
    assert meta.mean_unit_cost == pytest.approx(10.0)
    # 14 days * 52 / 365 ~ 1.99 periods
    assert meta.lead_time_periods == pytest.approx(14 * 52 / 365)


def test_metadata_ignores_zero_cost_rows_in_the_average():
    """A unit_cost of exactly 0 is a lost-in-transit value (see
    jobs/inventory_optimization.py's zero-cost skip), not a real price - averaging
    it in would drag mean_unit_cost toward zero and, via compute_eoq's holding-cost
    floor, explode the recommended order quantity. This is the test that would fail
    against the old plain ``.mean()`` (it would return 25.0, half of 50.0)."""
    df = pd.DataFrame({
        "date": pd.to_datetime(["2024-01-01", "2024-01-08", "2024-01-15", "2024-01-22"]),
        "product_id": ["SKU-A"] * 4,
        "quantity": [100, 100, 100, 100],
        "unit_cost": [50, 50, 0, 0],
    })
    meta = DataFrameDemandSource(df).metadata("SKU-A")
    assert meta.mean_unit_cost == pytest.approx(50.0)


def test_metadata_mean_unit_cost_is_zero_when_no_row_has_a_usable_cost():
    """Every row's cost was lost -> an explicit 0.0 signal (never a computable
    average, never a silent fallback to the 'column missing' default of 1.0), so
    the caller can distinguish 'no usable cost data' from 'a real $1 item'."""
    df = pd.DataFrame({
        "date": pd.to_datetime(["2024-01-01", "2024-01-08"]),
        "product_id": ["SKU-A", "SKU-A"],
        "quantity": [100, 100],
        "unit_cost": [0, 0],
    })
    meta = DataFrameDemandSource(df).metadata("SKU-A")
    assert meta.mean_unit_cost == 0.0


def test_dataframe_source_rejects_missing_columns():
    with pytest.raises(ValueError):
        DataFrameDemandSource(pd.DataFrame({"date": [], "product_id": []}))


def test_unknown_product_raises():
    src = DataFrameDemandSource(_frame())
    with pytest.raises(ValueError):
        src.demand_series("SKU-Z")


def test_csv_source_reads_sample_data():
    src = CsvDemandSource("data/sample_demand.csv")
    assert isinstance(src, DemandSource)  # satisfies the protocol
    products = src.list_products()
    assert len(products) >= 1
    series = src.demand_series(products[0])
    assert series.ndim == 1 and len(series) > 0


def test_sources_are_interchangeable():
    """CSV and DataFrame backends expose the identical interface."""
    df_src = DataFrameDemandSource(_frame())
    assert isinstance(df_src, DemandSource)
    assert hasattr(df_src, "list_products")
    assert hasattr(df_src, "demand_series")
    assert hasattr(df_src, "metadata")


def _seed_sqlite() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE demand (date TEXT, product_id TEXT, quantity REAL, unit_cost REAL, lead_time_days REAL)"
    )
    conn.executemany(
        "INSERT INTO demand VALUES (?, ?, ?, ?, ?)",
        [
            ("2024-01-01", "SKU-A", 100, 50, 7),
            ("2024-01-08", "SKU-A", 120, 50, 7),
            ("2024-01-01", "SKU-B", 40, 10, 14),
            ("2024-01-08", "SKU-B", 60, 10, 14),
        ],
    )
    conn.commit()
    return conn


def test_sql_source_reads_live_connection():
    conn = _seed_sqlite()
    try:
        src = SqlDemandSource(conn, table="demand")
        assert isinstance(src, DemandSource)
        assert src.list_products() == ["SKU-A", "SKU-B"]
        np.testing.assert_array_equal(src.demand_series("SKU-A"), np.array([100.0, 120.0]))
        meta = src.metadata("SKU-B")
        assert meta.mean_demand_per_period == pytest.approx(50.0)
        assert meta.mean_unit_cost == pytest.approx(10.0)
    finally:
        conn.close()


def test_sql_source_supports_custom_query():
    conn = _seed_sqlite()
    try:
        src = SqlDemandSource(
            conn,
            query="SELECT date, product_id, quantity FROM demand WHERE product_id = ?",
            params=["SKU-A"],
        )
        assert src.list_products() == ["SKU-A"]
    finally:
        conn.close()


def test_sql_source_rejects_unsafe_table_name():
    conn = _seed_sqlite()
    try:
        with pytest.raises(ValueError):
            SqlDemandSource(conn, table="demand; DROP TABLE demand")
    finally:
        conn.close()


def test_sql_and_dataframe_sources_agree():
    """The SQL adapter yields the same result as the in-memory one."""
    conn = _seed_sqlite()
    try:
        sql_src = SqlDemandSource(conn, table="demand")
        df_src = DataFrameDemandSource(_frame())
        assert sql_src.list_products() == df_src.list_products()
        np.testing.assert_array_equal(
            sql_src.demand_series("SKU-A"), df_src.demand_series("SKU-A")
        )
    finally:
        conn.close()
