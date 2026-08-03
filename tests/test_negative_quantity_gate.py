"""Regression tests for the negative-quantity gate.

Spec: docs/superpowers/specs/2026-08-03-negative-quantity-gate-design.md
Every test here fails against the pre-gate code.
"""

import pandas as pd
import pytest

from scm_agent import Orchestrator


def _write_demand_csv(path, n_rows, negative_indexes, qty=100.0):
    rows = ["date,product_id,quantity,unit_cost,lead_time_days"]
    day = pd.Timestamp("2023-01-02")
    for i in range(n_rows):
        value = -qty if i in negative_indexes else qty
        rows.append(f"{(day + pd.Timedelta(weeks=i)).date()},SKU-A,{value},50,7")
    path.write_text("\n".join(rows), encoding="utf-8")
    return path


def test_material_negative_demand_blocks_the_deliverable(tmp_path):
    """10 negatives in 200 rows = 5%, over the 2% tolerance."""
    data = _write_demand_csv(tmp_path / "dirty.csv", 200, {10, 20, 30, 40, 50, 60, 70, 80, 90, 100})
    result = Orchestrator(clients_root=None).run(
        "set reorder points", data_path=str(data), job_type="inventory_optimization",
        client="Demo", out_dir=str(tmp_path / "out"),
    )
    assert result.status == "qa_failed"
    assert result.deliverables == {}
    assert any("negative" in issue for issue in result.qa_issues)


def test_immaterial_negative_demand_still_delivers_but_discloses(tmp_path):
    data = _write_demand_csv(tmp_path / "slightly_dirty.csv", 200, {33})
    out = tmp_path / "out"
    result = Orchestrator(clients_root=None).run(
        "set reorder points", data_path=str(data), job_type="inventory_optimization",
        client="Demo", out_dir=str(out),
    )
    assert result.status == "ok"
    assert result.deliverables
    report_md = (out / "inventory_optimization" / "report.md").read_text(encoding="utf-8")
    assert "Data quality" in report_md
    assert "negative" in report_md.lower()


def test_clean_file_has_no_data_quality_section(tmp_path):
    data = _write_demand_csv(tmp_path / "clean.csv", 60, set())
    out = tmp_path / "out"
    result = Orchestrator(clients_root=None).run(
        "set reorder points", data_path=str(data), job_type="inventory_optimization",
        client="Demo", out_dir=str(out),
    )
    assert result.status == "ok"
    assert result.qa_issues == []
    report_md = (out / "inventory_optimization" / "report.md").read_text(encoding="utf-8")
    assert "Data quality" not in report_md


def test_threshold_is_overridable_per_run(tmp_path):
    data = _write_demand_csv(tmp_path / "dirty.csv", 200, set(range(0, 100, 10)))
    result = Orchestrator(clients_root=None).run(
        "set reorder points", data_path=str(data), job_type="inventory_optimization",
        client="Demo", out_dir=str(tmp_path / "out"),
        overrides={"negative_quantity_threshold": 0.5},
    )
    assert result.status == "ok"
