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


def test_cli_prints_residuals_and_escalation(tmp_path, capsys):
    from examples import run_agent

    # 25% unparseable dates trips intake's own 20% escalation threshold, while a
    # single negative row (0.5%, under the 2% gate) rides along so the printed
    # residual names it. Deliberately NOT a material negative share: that path
    # fails QA and returns no guided outcome at all, so it could not test this.
    rows = ["date,product_id,quantity,unit_cost,lead_time_days"]
    start = pd.Timestamp("2023-01-02")
    for i in range(200):
        date = "not-a-date" if i % 4 == 0 else (start + pd.Timedelta(weeks=i)).date()
        qty = -100 if i == 3 else 100
        rows.append(f"{date},SKU-A,{qty},50,7")
    data = tmp_path / "dirty_dates.csv"
    data.write_text("\n".join(rows), encoding="utf-8")

    run_agent.main([
        "--brief", "demo", "--job", "inventory_optimization",
        "--data", str(data), "--out", str(tmp_path / "out"), "--client", "Demo",
    ])
    printed = capsys.readouterr().out
    assert "Residuals" in printed
    assert "negative quantity" in printed
    assert "Escalated" in printed
    assert "operations owner" in printed


def test_abc_xyz_blocks_on_a_net_negative_sku():
    from jobs import abc_xyz_job

    df = pd.DataFrame({
        "product_id": ["A"] * 4 + ["B"] * 4,
        "period": [1, 2, 3, 4] * 2,
        "quantity": [100, 100, 100, -300, 100, 100, 100, 100],
        "unit_cost": [10] * 8,
    })
    payload = abc_xyz_job.prepare_records(df)
    report = abc_xyz_job.run(payload)
    issues = abc_xyz_job.verify(report)
    assert any("negative" in issue for issue in issues)


def test_abc_xyz_clean_data_passes():
    from jobs import abc_xyz_job

    df = pd.DataFrame({
        "product_id": ["A"] * 4 + ["B"] * 4,
        "period": [1, 2, 3, 4] * 2,
        "quantity": [100, 90, 110, 100, 10, 12, 8, 10],
        "unit_cost": [10] * 8,
    })
    report = abc_xyz_job.run(abc_xyz_job.prepare_records(df))
    assert abc_xyz_job.verify(report) == []


def test_forecast_counts_negatives_before_flooring_them():
    from jobs import forecast_job

    df = pd.DataFrame({
        "product_id": ["A"] * 6,
        "period": range(6),
        "quantity": [100, 100, 100, -400, 100, 100],
    })
    payload = forecast_job.prepare_records(df)
    assert payload["audit"].n_negative == 1
    assert payload["audit"].negative_total == -400.0
    # The clip stays: the SERIES is still floored, it is just no longer silent.
    assert payload["series"]["A"] == [100.0, 100.0, 100.0, 0.0, 100.0, 100.0]

    report = forecast_job.run(payload)
    assert any("negative" in issue for issue in forecast_job.verify(report))


def test_forecast_clean_data_passes():
    from jobs import forecast_job

    df = pd.DataFrame({
        "product_id": ["A"] * 6,
        "period": range(6),
        "quantity": [100, 105, 95, 100, 102, 98],
    })
    report = forecast_job.run(forecast_job.prepare_records(df))
    assert forecast_job.verify(report) == []


def test_excess_obsolete_blocks_on_any_negative_on_hand():
    from jobs import excess_obsolete_job as eo

    df = pd.DataFrame({
        "product_id": ["A", "B"],
        "on_hand": [-500.0, 100.0],
        "daily_demand": [10.0, 10.0],
        "unit_cost": [20.0, 20.0],
    })
    report = eo.run(eo.prepare_records(df))
    issues = eo.verify(report)
    assert any("negative" in issue and "on_hand" in issue for issue in issues)


def test_excess_obsolete_clean_stock_passes():
    from jobs import excess_obsolete_job as eo

    df = pd.DataFrame({
        "product_id": ["A", "B"],
        "on_hand": [500.0, 100.0],
        "daily_demand": [10.0, 10.0],
        "unit_cost": [20.0, 20.0],
    })
    report = eo.run(eo.prepare_records(df))
    assert eo.verify(report) == []
