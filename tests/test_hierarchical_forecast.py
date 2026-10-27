"""Unit tests for Hierarchical Forecast Reconciliation and MinT (src/hierarchical_forecast.py)."""

import numpy as np
import pytest

from src.hierarchical_forecast import (
    HierarchyStructure,
    _shrinkage_covariance,
    reconcile_bottom_up,
    reconcile_mint,
    reconcile_top_down,
)


@pytest.fixture
def sample_hierarchy():
    # 2 warehouses (WH_A, WH_B), each with 2 SKUs
    # Total -> (WH_A, WH_B) -> (SKU_A1, SKU_A2, SKU_B1, SKU_B2)
    bottom = ["SKU_A1", "SKU_A2", "SKU_B1", "SKU_B2"]
    groups = {
        "WH_A": ["SKU_A1", "SKU_A2"],
        "WH_B": ["SKU_B1", "SKU_B2"],
    }
    return HierarchyStructure.from_groupings(bottom, groups, root_name="Total")


def test_hierarchy_structural_matrix(sample_hierarchy):
    h = sample_hierarchy
    # Nodes: Total (0), WH_A (1), WH_B (2), SKU_A1 (3), SKU_A2 (4), SKU_B1 (5), SKU_B2 (6)
    # Total should sum all 4
    assert np.all(h.S[0, :] == [1.0, 1.0, 1.0, 1.0])
    # WH_A sums SKU_A1 and SKU_A2
    assert np.all(h.S[1, :] == [1.0, 1.0, 0.0, 0.0])
    # WH_B sums SKU_B1 and SKU_B2
    assert np.all(h.S[2, :] == [0.0, 0.0, 1.0, 1.0])


def test_reconcile_bottom_up_enforces_coherence(sample_hierarchy):
    h = sample_hierarchy
    base = {
        "Total": 500.0,  # Incoherent with bottom
        "WH_A": 200.0,
        "WH_B": 250.0,
        "SKU_A1": 50.0,
        "SKU_A2": 60.0,
        "SKU_B1": 70.0,
        "SKU_B2": 80.0,
    }
    res = reconcile_bottom_up(h, base)
    assert res.is_coherent is True
    assert res.coherence_max_discrepancy == pytest.approx(0.0)

    # Bottom values preserved
    assert res.reconciled_forecasts["SKU_A1"] == 50.0
    assert res.reconciled_forecasts["SKU_B2"] == 80.0

    # Aggregated values must equal exact bottom sums
    assert res.reconciled_forecasts["WH_A"] == pytest.approx(50.0 + 60.0)
    assert res.reconciled_forecasts["WH_B"] == pytest.approx(70.0 + 80.0)
    assert res.reconciled_forecasts["Total"] == pytest.approx(110.0 + 150.0)


def test_reconcile_top_down_disaggregates_root(sample_hierarchy):
    h = sample_hierarchy
    base = {
        "Total": 1000.0,
        "WH_A": 400.0,
        "WH_B": 400.0,
        "SKU_A1": 100.0,
        "SKU_A2": 100.0,
        "SKU_B1": 100.0,
        "SKU_B2": 100.0,
    }
    # Equal proportions
    res = reconcile_top_down(h, base)
    assert res.is_coherent is True
    assert res.reconciled_forecasts["Total"] == pytest.approx(1000.0)
    assert res.reconciled_forecasts["SKU_A1"] == pytest.approx(250.0)
    assert res.reconciled_forecasts["SKU_B2"] == pytest.approx(250.0)
    assert res.reconciled_forecasts["WH_A"] == pytest.approx(500.0)


def test_reconcile_mint_ols(sample_hierarchy):
    h = sample_hierarchy
    # Incoherent base forecasts
    base = {
        "Total": 320.0,
        "WH_A": 130.0,
        "WH_B": 170.0,
        "SKU_A1": 55.0,
        "SKU_A2": 65.0,
        "SKU_B1": 75.0,
        "SKU_B2": 85.0,
    }
    res = reconcile_mint(h, base, covariance_method="ols")
    assert res.is_coherent is True
    assert res.coherence_max_discrepancy < 1e-4

    # Check structural coherence: Total = WH_A + WH_B
    tot = res.reconciled_forecasts["Total"]
    wh_sum = res.reconciled_forecasts["WH_A"] + res.reconciled_forecasts["WH_B"]
    assert tot == pytest.approx(wh_sum)


def test_reconcile_mint_shrinkage_with_residuals(sample_hierarchy):
    h = sample_hierarchy
    rng = np.random.default_rng(42)
    m = len(h.node_names)
    residuals = rng.normal(0, 5, size=(50, m))

    base = {
        "Total": 400.0,
        "WH_A": 190.0,
        "WH_B": 220.0,
        "SKU_A1": 80.0,
        "SKU_A2": 100.0,
        "SKU_B1": 90.0,
        "SKU_B2": 110.0,
    }
    res = reconcile_mint(h, base, residuals=residuals, covariance_method="mint_shrink")
    assert res.is_coherent is True
    assert res.coherence_max_discrepancy < 1e-4


def test_shrinkage_covariance_well_conditioned():
    rng = np.random.default_rng(123)
    residuals = rng.normal(0, 1, size=(10, 5))  # few samples, high dimension
    W = _shrinkage_covariance(residuals)
    assert W.shape == (5, 5)
    # Must be positive definite
    eigvals = np.real(np.linalg.eigvals(W))
    assert np.all(eigvals > 0)


def test_non_negative_constraint(sample_hierarchy):
    h = sample_hierarchy
    # Base forecasts with small / negative tendencies
    base = {
        "Total": 10.0,
        "WH_A": -5.0,
        "WH_B": 15.0,
        "SKU_A1": -10.0,
        "SKU_A2": 2.0,
        "SKU_B1": 5.0,
        "SKU_B2": 8.0,
    }
    res = reconcile_mint(h, base, covariance_method="wls_struct", non_negative=True)
    assert res.is_coherent is True
    for name, val in res.reconciled_forecasts.items():
        assert val >= 0.0
