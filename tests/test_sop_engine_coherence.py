"""Tests for src/sop_engine/coherence.py (Linchpin 3.0 PR-20, A5 step 4).

Both pass and fail cases are exercised for all three checks, per the PR's
QA requirement. Failure messages are asserted to cite the exact upstream
numbers (never a vague warning).
"""

from __future__ import annotations

from src.constraints import allocate_under_budget
from src.sop_engine.coherence import (
    CHECK_BUDGET_FEASIBILITY,
    CHECK_PROMO_COVERAGE,
    CHECK_SERVICE_LEVEL,
    check_budget_feasibility,
    check_promo_coverage,
    check_reorder_point_service_level,
)
from src.sop_engine.demand_plan import LIQUIDATION_SOURCE, NO_SHIFT_SOURCE, DemandPlanLine
from src.sop_engine.purchase_plan import PurchasePlanLine


def _demand_line(product_id: str, base: float, shaped: float, source: str) -> DemandPlanLine:
    pct = 0.0 if base == shaped else (shaped / base - 1.0) * 100.0
    return DemandPlanLine(
        product_id=product_id, base_forecast=base, demand_shift_pct=pct, shaped_demand=shaped,
        source=source, reason="test fixture",
    )


def _purchase_line(**overrides) -> PurchasePlanLine:
    base = dict(
        product_id="SKU1", shaped_demand=100.0, on_hand=50.0, incoming_po=0.0, unit_cost=2.0,
        recommended_order=0.0, reorder_buffer=0.0, projected_position=0.0, order_value=0.0,
    )
    base.update(overrides)
    return PurchasePlanLine(**base)


# ---- check_promo_coverage: THE plan's literal example ----

def test_promo_coverage_not_applicable_when_nothing_lifts_demand():
    demand_plan = (_demand_line("A", 100.0, 100.0, NO_SHIFT_SOURCE),)
    results = check_promo_coverage(demand_plan, ())
    assert len(results) == 1
    assert results[0].passed is True
    assert results[0].product_id is None


def test_promo_coverage_fails_when_liquidation_markdown_has_no_incoming_po():
    """The plan's literal example: a SKU with a planned liquidation markdown
    has NO incoming purchase order to cover the expected demand lift."""
    demand_plan = (_demand_line("SKU-99", 20.0, 70.0, LIQUIDATION_SOURCE),)
    purchase_plan = (_purchase_line(
        product_id="SKU-99", shaped_demand=70.0, on_hand=10.0, incoming_po=0.0,
        recommended_order=60.0,
    ),)
    results = check_promo_coverage(demand_plan, purchase_plan)
    assert len(results) == 1
    result = results[0]
    assert result.check == CHECK_PROMO_COVERAGE
    assert result.passed is False
    assert result.product_id == "SKU-99"
    # citable: cites the exact upstream numbers, not a vague warning
    assert "70.0" in result.message   # shaped demand
    assert "10.0" in result.message   # on-hand
    assert "0.0" in result.message    # incoming PO
    assert "60.0" in result.message   # gap AND the recommended fix (both equal 60 here)


def test_promo_coverage_passes_when_committed_position_covers_the_lift():
    demand_plan = (_demand_line("SKU-99", 20.0, 70.0, LIQUIDATION_SOURCE),)
    purchase_plan = (_purchase_line(
        product_id="SKU-99", shaped_demand=70.0, on_hand=30.0, incoming_po=40.0,
    ),)
    results = check_promo_coverage(demand_plan, purchase_plan)
    assert results[0].passed is True
    assert "70.0" in results[0].message


def test_promo_coverage_does_not_use_this_plans_own_recommended_order_as_coverage():
    """A huge recommended_order must NOT make this check pass on its own --
    only the ALREADY-COMMITTED incoming_po counts (see purchase_plan.py's
    module docstring on why this distinction matters)."""
    demand_plan = (_demand_line("SKU-99", 20.0, 70.0, LIQUIDATION_SOURCE),)
    purchase_plan = (_purchase_line(
        product_id="SKU-99", shaped_demand=70.0, on_hand=10.0, incoming_po=0.0,
        recommended_order=1000.0,
    ),)
    results = check_promo_coverage(demand_plan, purchase_plan)
    assert results[0].passed is False


def test_promo_coverage_flags_missing_purchase_line_as_a_failure():
    demand_plan = (_demand_line("SKU-GHOST", 20.0, 70.0, LIQUIDATION_SOURCE),)
    results = check_promo_coverage(demand_plan, ())
    assert results[0].passed is False
    assert "SKU-GHOST" in results[0].message


# ---- check_budget_feasibility ----

def test_budget_feasibility_not_applicable_without_a_budget():
    result = check_budget_feasibility(None, None)
    assert result[0].passed is True


def test_budget_feasibility_passes_and_cites_scale_when_within_budget():
    from src.constraints import InventoryItem
    items = [InventoryItem("A", 100, 20, 2), InventoryItem("B", 200, 10, 1)]
    allocation = allocate_under_budget(items, budget=10_000)
    result = check_budget_feasibility(allocation, 10_000)
    assert result[0].passed is True
    assert "1.00" in result[0].message  # safety_stock_scale == 1.0


def test_budget_feasibility_fails_and_cites_the_real_shortfall():
    # Mirrors test_constraints.py::test_allocate_under_budget_infeasible_when_cycle_floor_exceeds:
    # cycle floor = 100, budget = 80 -> shortfall = 20.
    from src.constraints import InventoryItem
    items = [InventoryItem("A", 200, 50, 1)]
    allocation = allocate_under_budget(items, budget=80)
    result = check_budget_feasibility(allocation, 80)
    assert result[0].check == CHECK_BUDGET_FEASIBILITY
    assert result[0].passed is False
    assert "100.00" in result[0].message  # final_investment (cycle floor), BudgetAllocation's own number
    assert "20.00" in result[0].message   # shortfall = 100 - 80


# ---- check_reorder_point_service_level ----

def test_service_level_passes_when_projected_position_meets_the_buffer():
    line = _purchase_line(
        shaped_demand=100.0, on_hand=60.0, incoming_po=0.0, recommended_order=40.0,
        reorder_buffer=0.0,
    )
    results = check_reorder_point_service_level((line,))
    assert results[0].check == CHECK_SERVICE_LEVEL
    assert results[0].passed is True


def test_service_level_fails_and_cites_the_exact_shortfall():
    # on_hand 30 + incoming 0 + recommended 70 - shaped 100 = 0 projected;
    # reorder_buffer 20 -> shortfall 20.
    line = _purchase_line(
        shaped_demand=100.0, on_hand=30.0, incoming_po=0.0, recommended_order=70.0,
        reorder_buffer=20.0, projected_position=0.0,
    )
    results = check_reorder_point_service_level((line,))
    result = results[0]
    assert result.passed is False
    assert "20.0" in result.message  # both the buffer and the shortfall are 20.0 here
    assert "0.0" in result.message   # projected position


def test_service_level_checks_every_line_independently():
    lines = (
        _purchase_line(product_id="OK", projected_position=10.0, reorder_buffer=5.0),
        _purchase_line(product_id="BAD", projected_position=-5.0, reorder_buffer=5.0),
    )
    results = check_reorder_point_service_level(lines)
    by_id = {r.product_id: r.passed for r in results}
    assert by_id == {"OK": True, "BAD": False}
