"""Tests for src/sop_engine/engine.py -- the A5 v1 strictly-sequential
pipeline (Linchpin 3.0 PR-20): forecast -> demand shaping -> purchase plan
-> coherence checks, end to end, with hand-verified numbers.
"""

from __future__ import annotations

import pytest

from src.liquidation import LiquidationLine
from src.price_optimizer import PriceOptimizationResult
from src.sop_engine.coherence import CHECK_BUDGET_FEASIBILITY, CHECK_PROMO_COVERAGE, CHECK_SERVICE_LEVEL
from src.sop_engine.engine import run_integrated_plan
from src.sop_engine.purchase_plan import SkuPurchaseInputs


def test_pipeline_reports_at_least_three_coherence_checks_with_all_passing():
    forecast = {"SKU-A": 100.0}
    # on_hand covers both the shaped demand (100) and the reorder buffer (10) so
    # every check -- coverage, budget (unset), and service level -- passes.
    sku_inputs = {"SKU-A": SkuPurchaseInputs("SKU-A", on_hand=110.0, unit_cost=2.0, reorder_point=10.0, incoming_po=0.0)}
    report = run_integrated_plan(forecast, sku_inputs)

    check_kinds = {c.check for c in report.checks}
    assert check_kinds == {CHECK_PROMO_COVERAGE, CHECK_BUDGET_FEASIBILITY, CHECK_SERVICE_LEVEL}
    assert report.n_checks >= 3
    assert report.n_skus == 1
    assert report.n_checks_passed == report.n_checks  # nothing lifts demand, budget unset, buffer covered
    assert report.n_checks_failed == 0
    assert f"{report.n_checks_passed}/{report.n_checks}" in report.summary


def test_pipeline_flags_a_real_incoherent_liquidation_scenario_end_to_end():
    """The full plan-literal scenario: a liquidation markdown implies a
    demand lift, procurement has NOT placed a covering PO -- the pipeline
    must surface this as a FAILED, citable coherence check while still
    producing the full plan (never a silent drop)."""
    forecast = {"SKU-CLEAR": 20.0}
    liquidation_lines = {
        "SKU-CLEAR": LiquidationLine(
            product_id="SKU-CLEAR", classification="excess", units_to_clear=50.0, at_risk_value=500.0,
            method="elasticity", clearance_price=8.0, weeks_to_clear=5.0, recovered_value=400.0,
            recovery_pct=0.8,
        ),
    }
    sku_inputs = {
        "SKU-CLEAR": SkuPurchaseInputs("SKU-CLEAR", on_hand=10.0, unit_cost=4.0, reorder_point=5.0, incoming_po=0.0),
    }
    report = run_integrated_plan(forecast, sku_inputs, liquidation_lines=liquidation_lines)

    assert report.demand_plan[0].shaped_demand == pytest.approx(70.0)
    promo_results = [c for c in report.checks if c.check == CHECK_PROMO_COVERAGE]
    assert any(not c.passed for c in promo_results)
    failed = next(c for c in promo_results if not c.passed)
    assert failed.product_id == "SKU-CLEAR"
    assert report.n_checks_failed >= 1
    assert "FAILED" in report.summary


def test_pipeline_flags_a_budget_exceeded_scenario_with_real_shortfall_numbers():
    forecast = {"A": 200.0, "B": 200.0}
    sku_inputs = {
        "A": SkuPurchaseInputs("A", on_hand=0.0, unit_cost=1.0, reorder_point=100.0),
        "B": SkuPurchaseInputs("B", on_hand=0.0, unit_cost=1.0, reorder_point=100.0),
    }
    report = run_integrated_plan(forecast, sku_inputs, budget=50.0)  # below even the cycle floor (200)
    budget_result = next(c for c in report.checks if c.check == CHECK_BUDGET_FEASIBILITY)
    assert budget_result.passed is False
    assert report.allocation is not None
    assert report.allocation.feasible is False
    assert f"{report.allocation.final_investment:,.2f}" in budget_result.message


def test_pipeline_flags_a_stockout_risk_scenario():
    forecast = {"A": 100.0}
    sku_inputs = {"A": SkuPurchaseInputs("A", on_hand=0.0, unit_cost=1.0, reorder_point=50.0, incoming_po=0.0)}
    report = run_integrated_plan(forecast, sku_inputs)
    service_result = next(c for c in report.checks if c.check == CHECK_SERVICE_LEVEL)
    # on_hand 0 + incoming 0 + recommended 100 - shaped 100 = 0 projected < 50 buffer
    assert service_result.passed is False
    assert report.purchase_plan[0].projected_position == pytest.approx(0.0)


def test_pipeline_raises_on_missing_purchase_inputs():
    forecast = {"A": 10.0, "B": 5.0}
    sku_inputs = {"A": SkuPurchaseInputs("A", on_hand=0.0, unit_cost=1.0)}
    with pytest.raises(ValueError, match="B"):
        run_integrated_plan(forecast, sku_inputs)


def test_pipeline_price_optimizer_signal_flows_through_to_final_checks():
    forecast = {"SKU-X": 50.0}
    price_shifts = {
        "SKU-X": PriceOptimizationResult(
            product_id="SKU-X", status="ok", reason=None, current_price=10.0, proposed_price=5.0,
            landed_cost=3.0, elasticity_used=-2.0, shrinkage_weight=1.0, category="cat_A",
            floor_applied=False, price_capped=False, competitor_context=None,
        ),
    }
    sku_inputs = {"SKU-X": SkuPurchaseInputs("SKU-X", on_hand=50.0, unit_cost=3.0, incoming_po=0.0)}
    report = run_integrated_plan(forecast, sku_inputs, price_shifts=price_shifts)
    # shaped demand = 50 * 4 = 200 (ratio 3.0 from (5/10)**-2 - 1)
    assert report.demand_plan[0].shaped_demand == pytest.approx(200.0)
    assert report.purchase_plan[0].recommended_order == pytest.approx(150.0)  # 200 - 50 - 0
