"""Tests for jobs/stocky_importer.py."""

from __future__ import annotations

import pandas as pd

from jobs.stocky_importer import (
    StockyMigrationBatch,
    parse_stocky_purchase_orders_csv,
    parse_stocky_reorder_points_csv,
    parse_stocky_suppliers_csv,
    to_client_profile_params,
    to_intake_frame,
)


def test_parse_stocky_suppliers():
    csv_data = """Supplier Name,Contact Email,Lead Time (days),MOQ,Currency
Supplier Alpha,alpha@example.com,21,100,USD
Supplier Beta,beta@example.com,7,10,EUR
"""
    suppliers = parse_stocky_suppliers_csv(csv_data)
    assert len(suppliers) == 2
    assert suppliers[0].name == "Supplier Alpha"
    assert suppliers[0].lead_time_days == 21
    assert suppliers[0].moq == 100
    assert suppliers[1].currency == "EUR"


def test_parse_stocky_purchase_orders():
    csv_data = """PO Number,Supplier Name,Status,SKU,Quantity Ordered,Quantity Received,Cost Price
PO-1001,Supplier Alpha,sent,SKU-100,50,0,12.50
PO-1002,Supplier Beta,received,SKU-200,20,20,45.00
"""
    pos = parse_stocky_purchase_orders_csv(csv_data)
    assert len(pos) == 2
    assert pos[0].po_number == "PO-1001"
    assert pos[0].sku == "SKU-100"
    assert pos[0].quantity_ordered == 50
    assert pos[0].cost_per_unit == 12.50
    assert pos[1].status == "received"


def test_parse_stocky_reorder_points():
    csv_data = """SKU,Min Stock,Max Stock,Target Stock
SKU-100,15,60,60
SKU-200,5,20,20
"""
    reorders = parse_stocky_reorder_points_csv(csv_data)
    assert len(reorders) == 2
    assert reorders[0].sku == "SKU-100"
    assert reorders[0].min_reorder_point == 15
    assert reorders[0].max_reorder_point == 60


def test_stocky_migration_batch_summary():
    batch = StockyMigrationBatch()
    batch.suppliers = parse_stocky_suppliers_csv("Supplier Name\nTest Supp\n")
    summary = batch.summary()
    assert summary["suppliers_count"] == 1
    assert summary["purchase_orders_count"] == 0


def _batch_with_reorders_and_pos() -> StockyMigrationBatch:
    batch = StockyMigrationBatch()
    batch.reorder_points = parse_stocky_reorder_points_csv(
        "SKU,Min Stock,Max Stock,Target Stock\nSKU-100,15,60,60\nSKU-200,5,20,20\n"
    )
    batch.purchase_orders = parse_stocky_purchase_orders_csv(
        "PO Number,Supplier Name,Status,SKU,Quantity Ordered,Quantity Received,Cost Price,Order Date\n"
        "PO-1,Alpha,received,SKU-100,50,50,10.00,2026-01-01\n"
        "PO-2,Beta,sent,SKU-100,30,0,12.00,2026-06-01\n"
    )
    return batch


def test_to_intake_frame_keys_by_sku_and_latest_po_wins():
    frame = to_intake_frame(_batch_with_reorders_and_pos())
    assert list(frame.columns) == [
        "sku", "reorder_point", "max_reorder_point", "target_stock", "primary_supplier", "last_unit_cost",
    ]
    assert set(frame["sku"]) == {"SKU-100", "SKU-200"}
    row_100 = frame[frame["sku"] == "SKU-100"].iloc[0]
    assert row_100["reorder_point"] == 15
    # PO-2 (2026-06-01) is later than PO-1 (2026-01-01) -> its supplier/cost win
    assert row_100["primary_supplier"] == "Beta"
    assert row_100["last_unit_cost"] == 12.00
    # SKU-200 has a reorder point but no PO -> supplier/cost are missing (NaN,
    # pandas' sentinel; data_quality reads it via pd.notna)
    row_200 = frame[frame["sku"] == "SKU-200"].iloc[0]
    assert pd.isna(row_200["primary_supplier"])


def test_to_intake_frame_empty_batch_has_columns_no_rows():
    frame = to_intake_frame(StockyMigrationBatch())
    assert len(frame) == 0
    assert "sku" in frame.columns


def test_to_client_profile_params_uses_median_lead_time():
    batch = StockyMigrationBatch()
    batch.suppliers = parse_stocky_suppliers_csv(
        "Supplier Name,Lead Time (days),Currency\nA,10,USD\nB,20,USD\nC,30,USD\n"
    )
    params = to_client_profile_params(batch)
    assert params["lead_time_days"] == 20.0  # median of 10,20,30
    assert params["currency"] == "USD"


def test_to_client_profile_params_omits_currency_when_mixed():
    batch = StockyMigrationBatch()
    batch.suppliers = parse_stocky_suppliers_csv(
        "Supplier Name,Lead Time (days),Currency\nA,10,USD\nB,20,EUR\n"
    )
    params = to_client_profile_params(batch)
    assert "currency" not in params


def test_to_client_profile_params_empty_without_suppliers():
    assert to_client_profile_params(StockyMigrationBatch()) == {}
