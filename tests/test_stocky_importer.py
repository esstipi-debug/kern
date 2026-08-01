"""Tests for jobs/stocky_importer.py."""

from __future__ import annotations

from jobs.stocky_importer import (
    StockyMigrationBatch,
    parse_stocky_purchase_orders_csv,
    parse_stocky_reorder_points_csv,
    parse_stocky_suppliers_csv,
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
