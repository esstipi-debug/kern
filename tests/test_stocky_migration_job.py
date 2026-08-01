"""Tests for jobs/stocky_migration_job.py -- prepare -> run -> write_operational."""

from __future__ import annotations

from jobs.stocky_migration_job import prepare, run, write_operational

SUPPLIERS_CSV = "Supplier Name,Lead Time (days),Currency\nAlpha,14,USD\nBeta,28,USD\n"
POS_CSV = (
    "PO Number,Supplier Name,Status,SKU,Quantity Ordered,Quantity Received,Cost Price\n"
    "PO-1,Alpha,sent,SKU-1,10,0,5.0\n"
    "PO-2,Beta,received,SKU-2,20,20,7.5\n"
)
REORDER_CSV = "SKU,Min Stock,Max Stock,Target Stock\nSKU-1,5,20,20\nSKU-2,3,12,12\n"


def _write(tmp_path, name, content):
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


def test_prepare_reads_all_three_exports(tmp_path):
    batch = prepare(
        suppliers_csv=_write(tmp_path, "s.csv", SUPPLIERS_CSV),
        purchase_orders_csv=_write(tmp_path, "po.csv", POS_CSV),
        reorder_points_csv=_write(tmp_path, "ro.csv", REORDER_CSV),
    )
    assert batch.summary() == {
        "suppliers_count": 2,
        "purchase_orders_count": 2,
        "reorder_points_count": 2,
    }


def test_prepare_tolerates_missing_exports(tmp_path):
    batch = prepare(reorder_points_csv=_write(tmp_path, "ro.csv", REORDER_CSV))
    assert batch.summary()["reorder_points_count"] == 2
    assert batch.summary()["suppliers_count"] == 0


def test_run_produces_verdict_profile_and_sku_audit(tmp_path):
    batch = prepare(
        suppliers_csv=_write(tmp_path, "s.csv", SUPPLIERS_CSV),
        purchase_orders_csv=_write(tmp_path, "po.csv", POS_CSV),
        reorder_points_csv=_write(tmp_path, "ro.csv", REORDER_CSV),
    )
    result = run(batch)
    assert result.assessment.shopify_native_sufficient is False
    assert result.client_profile_params["lead_time_days"] == 21.0  # median(14, 28)
    # clean 2-SKU reorder master -> no false-positive duplicate cluster
    assert result.sku_audit.clean is True
    assert result.sku_audit.n_skus == 2
    assert result.sku_audit.duplicate_skus == ()


def test_run_sku_audit_flags_a_dirty_master(tmp_path):
    dirty = "SKU,Min Stock,Max Stock,Target Stock\nSKU-1,50,20,20\nSKU-2,0,10,10\nSKU-2,5,10,10\n"
    batch = prepare(reorder_points_csv=_write(tmp_path, "ro.csv", dirty))
    result = run(batch)
    assert result.sku_audit.clean is False
    assert "SKU-2" in result.sku_audit.duplicate_skus       # listed twice
    assert "SKU-1" in result.sku_audit.inconsistent_minmax  # min 50 > max 20
    assert "SKU-2" in result.sku_audit.nonpositive_reorder  # a row with min 0


def test_run_sku_audit_notes_when_no_reorder_data(tmp_path):
    batch = prepare(suppliers_csv=_write(tmp_path, "s.csv", SUPPLIERS_CSV))
    result = run(batch)
    assert result.sku_audit.clean is False
    assert "Sin puntos de reorden" in result.sku_audit.summary


def test_write_operational_renders_a_markdown_report(tmp_path):
    batch = prepare(
        reorder_points_csv=_write(tmp_path, "ro.csv", REORDER_CSV),
        purchase_orders_csv=_write(tmp_path, "po.csv", POS_CSV),
    )
    result = run(batch)
    paths = write_operational(result, tmp_path / "out", client="Tienda X")
    report = paths["report"].read_text(encoding="utf-8")
    assert "Chequeo de migracion Stocky -- Tienda X" in report
    assert "Veredicto" in report
    assert "BRECHA" in report  # at least one gap rendered
    assert "Kern no accede a tu tienda" in report  # read-only disclaimer
