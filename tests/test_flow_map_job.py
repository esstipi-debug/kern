"""Tests for the flow_map job: intake, the QA gate, deliverables."""
import pandas as pd
import pytest

from jobs import flow_map_job
from src.flow_map import UNMEASURABLE


def _frame(rows):
    return pd.DataFrame(rows)


def _lane(origin, destination, days, n, start_day=1, qty=10.0):
    return [
        {
            "ship_date": f"2026-01-{start_day + i:02d}",
            "receipt_date": f"2026-01-{start_day + i + int(days):02d}",
            "origin": origin, "destination": destination,
            "sku": "SKU-A", "quantity": qty, "unit_cost": 5.0,
        }
        for i in range(n)
    ]


def _healthy_csv(tmp_path):
    rows = _lane("SUP-1", "DC-1", 2, 8) + _lane("DC-1", "STORE-1", 4, 8, start_day=2)
    path = tmp_path / "movimientos.csv"
    _frame(rows).to_csv(path, index=False)
    return path


def test_prepare_maps_aliases_and_measures_transit(tmp_path):
    movements = flow_map_job.prepare(str(_healthy_csv(tmp_path)))
    assert len(movements) == 16
    assert {m.origin for m in movements} == {"SUP-1", "DC-1"}
    assert all(m.transit_days > 0 for m in movements)


def test_prepare_names_the_exact_missing_column(tmp_path):
    rows = _lane("SUP-1", "DC-1", 2, 8)
    for row in rows:
        del row["receipt_date"]
    path = tmp_path / "sin_llegada.csv"
    _frame(rows).to_csv(path, index=False)
    with pytest.raises(ValueError, match="fecha_llegada"):
        flow_map_job.prepare(str(path))


def test_gate_blocks_when_most_volume_is_unmeasurable(tmp_path):
    rows = _lane("SUP-1", "DC-1", 2, 8, qty=1.0) + _lane("DC-1", "STORE-9", 3, 3, qty=500.0)
    path = tmp_path / "flaco.csv"
    _frame(rows).to_csv(path, index=False)
    report = flow_map_job.run(flow_map_job.prepare(str(path)))
    issues = flow_map_job.verify(report)
    assert any("unmeasurable" in i for i in issues)


def test_gate_blocks_on_inverted_dates(tmp_path):
    rows = _lane("SUP-1", "DC-1", 2, 8) + _lane("DC-1", "STORE-1", 4, 8, start_day=2)
    rows[0]["receipt_date"] = "2026-01-01"
    rows[0]["ship_date"] = "2026-01-09"
    path = tmp_path / "invertido.csv"
    _frame(rows).to_csv(path, index=False)
    report = flow_map_job.run(flow_map_job.prepare(str(path)))
    assert any("arrival before departure" in i for i in flow_map_job.verify(report))


def test_gate_blocks_a_single_edge_graph(tmp_path):
    path = tmp_path / "una_arista.csv"
    _frame(_lane("SUP-1", "DC-1", 2, 8)).to_csv(path, index=False)
    report = flow_map_job.run(flow_map_job.prepare(str(path)))
    assert any("fewer than two lanes" in i for i in flow_map_job.verify(report))


def test_healthy_data_passes_the_gate(tmp_path):
    report = flow_map_job.run(flow_map_job.prepare(str(_healthy_csv(tmp_path))))
    assert flow_map_job.verify(report) == []
    assert report.edges[0].status != UNMEASURABLE
