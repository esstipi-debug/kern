"""Tests for sourcing_job multi-sourcing LP integration with MCDM TOPSIS."""

from pathlib import Path
import pandas as pd
import pytest

from jobs import sourcing_job as srcj
from src.mcdm import MultiSourcingPlan, solve_multi_sourcing, SupplierAllocation


def _deliveries_df() -> pd.DataFrame:
    # 3 suppliers with different performance and prices
    return pd.DataFrame({
        "supplier": ["Alpha"] * 4 + ["Beta"] * 4 + ["Gamma"] * 4,
        "on_time": [1, 1, 1, 1, 1, 1, 0, 1, 0, 0, 1, 0],
        "in_full": [1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 1, 1],
        "lead_time_days": [5, 6, 5, 6, 8, 9, 8, 9, 14, 15, 13, 14],
        "units": [100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 100, 100],
        "defects": [0, 0, 0, 1, 1, 2, 1, 1, 5, 4, 3, 4],
        "unit_price": [12.0] * 4 + [10.0] * 4 + [8.0] * 4,
    })


def test_sourcing_job_without_multi_sourcing_preserves_single_award():
    cards, prices = srcj.score_suppliers(
        _deliveries_df(),
        supplier_col="supplier",
        on_time_col="on_time",
        in_full_col="in_full",
        lead_col="lead_time_days",
        units_col="units",
        defects_col="defects",
        price_col="unit_price",
    )
    report = srcj.run(cards, prices)
    assert report.recommended != ""
    assert report.multi_sourcing_plan is None
    assert srcj.verify(report) == []


def test_sourcing_job_with_max_share_cap():
    cards, prices = srcj.score_suppliers(
        _deliveries_df(),
        supplier_col="supplier",
        on_time_col="on_time",
        in_full_col="in_full",
        lead_col="lead_time_days",
        units_col="units",
        defects_col="defects",
        price_col="unit_price",
    )
    # Demand 1000 units, max share 60% requires at least 2 suppliers
    report = srcj.run(
        cards,
        prices,
        demand=1000.0,
        max_single_supplier_share=0.60,
    )
    assert report.multi_sourcing_plan is not None
    plan = report.multi_sourcing_plan
    assert isinstance(plan, MultiSourcingPlan)
    assert plan.total_units == 1000.0
    assert len(plan.allocations) >= 2
    for alloc in plan.allocations:
        assert alloc.share_pct <= 0.60 + 1e-6
    assert srcj.verify(report) == []


def test_sourcing_job_with_capacities():
    cards, prices = srcj.score_suppliers(
        _deliveries_df(),
        supplier_col="supplier",
        on_time_col="on_time",
        in_full_col="in_full",
        lead_col="lead_time_days",
        units_col="units",
        defects_col="defects",
        price_col="unit_price",
    )
    # Demand 500 units, Alpha has capacity 200, Beta has capacity 250, Gamma has 200
    report = srcj.run(
        cards,
        prices,
        demand=500.0,
        capacities={"Alpha": 200.0, "Beta": 250.0, "Gamma": 200.0},
        max_single_supplier_share=0.80,
    )
    assert report.multi_sourcing_plan is not None
    plan = report.multi_sourcing_plan
    alloc_map = {a.supplier: a.units for a in plan.allocations}
    assert alloc_map.get("Alpha", 0) <= 200.0 + 1e-6
    assert alloc_map.get("Beta", 0) <= 250.0 + 1e-6
    assert sum(alloc_map.values()) == pytest.approx(500.0)
    assert srcj.verify(report) == []


def test_sourcing_job_write_operational_includes_multi_sourcing(tmp_path):
    cards, prices = srcj.score_suppliers(
        _deliveries_df(),
        supplier_col="supplier",
        on_time_col="on_time",
        in_full_col="in_full",
        lead_col="lead_time_days",
        units_col="units",
        defects_col="defects",
        price_col="unit_price",
    )
    report = srcj.run(
        cards,
        prices,
        demand=1000.0,
        max_single_supplier_share=0.50,
    )
    out = srcj.write_operational(report, tmp_path)
    assert "csv" in out
    assert "multi_sourcing_csv" in out
    ms_csv = Path(out["multi_sourcing_csv"])
    assert ms_csv.exists()
    content = ms_csv.read_text(encoding="utf-8")
    assert "supplier,units,share_pct,unit_cost,total_cost,topsis_score" in content
