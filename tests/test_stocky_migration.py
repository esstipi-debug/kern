"""Tests for src/stocky_migration.py -- the Shopify-native migration verdict."""

from __future__ import annotations

from jobs.stocky_importer import (
    StockyMigrationBatch,
    parse_stocky_purchase_orders_csv,
    parse_stocky_reorder_points_csv,
    parse_stocky_suppliers_csv,
)
from src.stocky_migration import SHOPIFY_NATIVE_COVERAGE, assess_migration, audit_sku_master


def _full_batch() -> StockyMigrationBatch:
    batch = StockyMigrationBatch()
    batch.suppliers = parse_stocky_suppliers_csv("Supplier Name,Lead Time (days)\nAlpha,14\n")
    batch.purchase_orders = parse_stocky_purchase_orders_csv(
        "PO Number,Supplier Name,Status,SKU,Quantity Ordered,Quantity Received,Cost Price\n"
        "PO-1,Alpha,sent,SKU-1,10,0,5.0\n"
    )
    batch.reorder_points = parse_stocky_reorder_points_csv("SKU,Min Stock,Max Stock,Target Stock\nSKU-1,5,20,20\n")
    return batch


def test_stocky_layer_does_not_migrate_cleanly_to_native():
    # Grounding constant: each Stocky-added category is a MIGRATION gap
    # (covered=False) with a stated reason. Native may have a basic version of
    # some of these (e.g. basic POs) -- the gap is that the Stocky data/
    # automation does not carry over cleanly, not that native is featureless.
    for category in ("reorder_points", "purchase_orders", "suppliers", "demand_forecasting"):
        covered, reason = SHOPIFY_NATIVE_COVERAGE[category]
        assert covered is False
        assert reason  # a stated reason exists for every verdict


def test_purchase_orders_reason_does_not_overclaim_native_lacks_pos():
    # Regression guard: native DOES have basic purchase orders. The reason must
    # acknowledge that (say "SI ... basicas") and pin the gap on the lossy
    # migration, not claim native has no POs at all.
    _, reason = SHOPIFY_NATIVE_COVERAGE["purchase_orders"]
    assert "no gestiona ordenes de compra" not in reason  # the old overclaim
    assert "perdida" in reason or "draft" in reason


def test_full_batch_verdict_is_not_sufficient_and_lists_gaps():
    a = assess_migration(_full_batch())
    assert a.shopify_native_sufficient is False
    assert "NO te alcanza" in a.headline
    assert set(a.gaps) == {"reorder_points", "purchase_orders", "suppliers", "demand_forecasting"}


def test_forecasting_is_flagged_whenever_any_inventory_exported():
    # Only reorder points exported -> forecasting still surfaces as a loss.
    batch = StockyMigrationBatch()
    batch.reorder_points = parse_stocky_reorder_points_csv("SKU,Min Stock,Max Stock,Target Stock\nSKU-1,5,20,20\n")
    a = assess_migration(batch)
    assert "demand_forecasting" in a.gaps
    # suppliers/POs were not exported -> reported as not_in_export, not a gap
    by_cat = {c.category: c for c in a.categories}
    assert by_cat["suppliers"].verdict == "not_in_export"
    assert by_cat["reorder_points"].verdict == "gap"


def test_recommended_options_lead_with_starter_when_gaps_exist():
    a = assess_migration(_full_batch())
    assert a.recommended_options
    assert "Starter" in a.recommended_options[0].label


def test_empty_batch_is_not_reassured_as_sufficient():
    a = assess_migration(StockyMigrationBatch())
    assert a.shopify_native_sufficient is False
    assert "No se exportaron datos" in a.headline
    assert a.gaps == ()
    # every category is honestly reported as not evaluated
    assert all(c.verdict == "not_in_export" for c in a.categories)


def test_audit_sku_master_clean():
    batch = StockyMigrationBatch()
    batch.reorder_points = parse_stocky_reorder_points_csv(
        "SKU,Min Stock,Max Stock,Target Stock\nSKU-1,5,20,20\nSKU-2,3,12,12\n"
    )
    audit = audit_sku_master(batch)
    assert audit.clean is True
    assert audit.n_skus == 2
    assert audit.duplicate_skus == () and audit.inconsistent_minmax == ()


def test_audit_sku_master_flags_dup_minmax_and_nonpositive():
    batch = StockyMigrationBatch()
    batch.reorder_points = parse_stocky_reorder_points_csv(
        "SKU,Min Stock,Max Stock,Target Stock\nSKU-1,50,20,20\nSKU-2,0,10,10\nSKU-2,5,10,10\n"
    )
    audit = audit_sku_master(batch)
    assert audit.clean is False
    assert audit.duplicate_skus == ("SKU-2",)
    assert audit.inconsistent_minmax == ("SKU-1",)
    assert audit.nonpositive_reorder == ("SKU-2",)
