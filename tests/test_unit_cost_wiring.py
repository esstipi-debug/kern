"""Acceptance test for the unit_cost wiring (audit fix 2).

``jobs/intake.py`` defines ``default_unit_cost=1.0`` but ``prepare()`` and
``prepare_tracked()`` neither accepted nor forwarded it, so 1.0 was unreachable
from the agent path. When a client file carries no cost column, every SKU got
cost 1.0 — and ``src/classification.py`` computes
``annual_value = unit_cost * annual_demand``, so ABC silently ranked on UNITS
while the deliverable column stayed labelled ``annual_value``.

On data/kaggle/superstore.csv that put 1,111 of 1,862 SKUs (59.7%) in class A.
The real unit price is recoverable from the same file: Sales / Quantity.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from jobs import abc_xyz_job, intake

REPO = Path(__file__).resolve().parents[1]


def test_prepare_exposes_default_unit_cost(tmp_path):
    """prepare()/prepare_tracked() must let the caller set the fallback cost."""
    csv = tmp_path / "demand.csv"
    pd.DataFrame(
        {"date": ["2024-01-01", "2024-01-08"], "product_id": ["A", "A"], "quantity": [10, 12]}
    ).to_csv(csv, index=False)

    assert intake.prepare(str(csv))["unit_cost"].eq(1.0).all()  # documented default
    assert intake.prepare(str(csv), default_unit_cost=12.5)["unit_cost"].eq(12.5).all()
    frame, _quality = intake.prepare_tracked(str(csv), default_unit_cost=12.5)
    assert frame["unit_cost"].eq(12.5).all()


def test_unit_cost_derived_from_revenue_when_no_cost_column():
    """No cost column + a revenue column -> unit_cost = revenue / quantity."""
    raw = pd.DataFrame(
        {
            "Order Date": ["2024-01-01", "2024-01-02"],
            "Product ID": ["A", "B"],
            "Quantity": [2, 4],
            "Sales": [50.0, 100.0],
        }
    )
    mapping = intake.detect_columns(raw)
    assert "unit_cost" not in mapping.mapping, "no cost column in this file"
    assert mapping.mapping.get("revenue") == "Sales"

    canon = intake.normalize(raw, mapping)
    by_sku = canon.set_index("product_id")["unit_cost"]
    assert by_sku["A"] == pytest.approx(25.0)  # 50 / 2
    assert by_sku["B"] == pytest.approx(25.0)  # 100 / 4


def test_revenue_not_used_when_it_is_the_quantity_column():
    """'sales' is also a quantity alias; deriving cost from itself would give 1.0."""
    raw = pd.DataFrame({"date": ["2024-01-01"], "sku": ["A"], "Sales": [7.0]})
    mapping = intake.detect_columns(raw)
    assert mapping.mapping["quantity"] == "Sales"
    canon = intake.normalize(raw, mapping)
    assert canon["unit_cost"].eq(1.0).all(), "must fall back, not divide Sales by Sales"


@pytest.mark.skipif(
    not (REPO / "data" / "kaggle" / "superstore.csv").exists()
    or not (REPO / "data" / "kaggle" / "superstore_canonical.csv").exists(),
    reason="data/kaggle/superstore*.csv not present - large Kaggle dataset, "
           "fetched locally, not versioned",
)
def test_abc_ranks_on_value_not_units_on_real_superstore():
    """The audit's headline: 1,111 A-items -> 414 once real cost reaches the classifier."""
    raw_path = REPO / "data" / "kaggle" / "superstore.csv"
    raw = pd.read_csv(raw_path, encoding="latin-1")

    canon = intake.normalize(raw, intake.detect_columns(raw))
    assert canon["unit_cost"].nunique() > 1, "cost must vary across the portfolio"

    report = abc_xyz_job.run(
        abc_xyz_job.aggregate_skus(
            canon, product_col="product_id", demand_col="quantity", cost_col="unit_cost"
        )
    )
    assert report.n_skus == 1862
    assert report.n_a == 414
    assert report.n_a / report.n_skus == pytest.approx(0.222, abs=0.001)

    # and the shipped canonical file is what produced the old number
    shipped = pd.read_csv(REPO / "data" / "kaggle" / "superstore_canonical.csv")
    old = abc_xyz_job.run(
        abc_xyz_job.aggregate_skus(
            shipped, product_col="product_id", demand_col="quantity", cost_col="unit_cost"
        )
    )
    assert old.n_a == 1111, "the pre-fix artifact still ranks on units"
