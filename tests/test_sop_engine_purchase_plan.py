"""Tests for src/sop_engine/purchase_plan.py (Linchpin 3.0 PR-20, A5 step 3)."""

from __future__ import annotations

import pytest

from src.sop_engine.demand_plan import NO_SHIFT_SOURCE, DemandPlanLine
from src.sop_engine.purchase_plan import SkuPurchaseInputs, build_purchase_plan


def _line(product_id: str, base: float, shaped: float, source: str = NO_SHIFT_SOURCE) -> DemandPlanLine:
    return DemandPlanLine(
        product_id=product_id, base_forecast=base, demand_shift_pct=0.0 if source == NO_SHIFT_SOURCE else 1.0,
        shaped_demand=shaped, source=source, reason="test fixture",
    )


# ---- SkuPurchaseInputs validation ----

def test_sku_purchase_inputs_rejects_negative_on_hand():
    with pytest.raises(ValueError):
        SkuPurchaseInputs("A", on_hand=-1.0, unit_cost=1.0)


def test_sku_purchase_inputs_rejects_nonpositive_unit_cost():
    with pytest.raises(ValueError):
        SkuPurchaseInputs("A", on_hand=0.0, unit_cost=0.0)


def test_sku_purchase_inputs_rejects_negative_incoming_po():
    with pytest.raises(ValueError):
        SkuPurchaseInputs("A", on_hand=0.0, unit_cost=1.0, incoming_po=-5.0)


# ---- build_purchase_plan basics ----

def test_gap_fill_orders_exactly_the_shortfall_when_no_rules_apply():
    demand_plan = (_line("A", 100.0, 100.0),)
    inputs = {"A": SkuPurchaseInputs("A", on_hand=30.0, unit_cost=5.0, reorder_point=20.0)}
    lines, allocation = build_purchase_plan(demand_plan, inputs)
    assert allocation is None
    line = lines[0]
    assert line.recommended_order == pytest.approx(70.0)  # 100 - 30 - 0(incoming)
    assert line.projected_position == pytest.approx(0.0)  # 30 + 0 + 70 - 100
    assert line.reorder_buffer == pytest.approx(20.0)  # unscaled, no budget
    assert line.order_value == pytest.approx(350.0)  # 70 * 5


def test_incoming_po_reduces_the_recommended_top_up():
    demand_plan = (_line("A", 100.0, 100.0),)
    inputs = {"A": SkuPurchaseInputs("A", on_hand=30.0, unit_cost=5.0, incoming_po=40.0)}
    lines, _ = build_purchase_plan(demand_plan, inputs)
    line = lines[0]
    assert line.recommended_order == pytest.approx(30.0)  # 100 - 30 - 40
    assert line.projected_position == pytest.approx(0.0)  # 30 + 40 + 30 - 100


def test_moq_and_case_pack_rules_are_applied_via_constraints_module():
    demand_plan = (_line("A", 40.0, 40.0),)
    inputs = {"A": SkuPurchaseInputs("A", on_hand=10.0, unit_cost=2.0, minimum_order_quantity=50.0, order_multiple=12.0)}
    lines, _ = build_purchase_plan(demand_plan, inputs)
    line = lines[0]
    # gap = 40 - 10 = 30; MOQ raises 30 -> 50; case-pack of 12 rounds 50 -> 60
    # (same MOQ-then-pack rounding test_constraints.py::test_apply_order_rules_moq_then_pack_then_cap
    # exercises on the identical 30/50/12 inputs, minus that test's additional max_quantity cap).
    assert line.recommended_order == pytest.approx(60.0)


def test_missing_sku_inputs_raises_value_error_naming_the_sku():
    demand_plan = (_line("A", 10.0, 10.0), _line("B", 5.0, 5.0))
    inputs = {"A": SkuPurchaseInputs("A", on_hand=0.0, unit_cost=1.0)}
    with pytest.raises(ValueError, match="B"):
        build_purchase_plan(demand_plan, inputs)


# ---- budget allocation: mirrors tests/test_constraints.py's own hand-verified examples ----

def test_budget_scales_reorder_buffer_but_never_recommended_order():
    # Same numbers as test_constraints.py::test_allocate_under_budget_scales_safety_stock_to_fit:
    # cycle floor = 100 + 100 = 200; safety(reorder) = 100 + 100 = 200; total = 400; budget = 300
    # -> scale 0.5, final_investment = 300.
    demand_plan = (_line("A", 200.0, 200.0), _line("B", 200.0, 200.0))
    inputs = {
        "A": SkuPurchaseInputs("A", on_hand=0.0, unit_cost=1.0, reorder_point=100.0),
        "B": SkuPurchaseInputs("B", on_hand=0.0, unit_cost=1.0, reorder_point=100.0),
    }
    lines, allocation = build_purchase_plan(demand_plan, inputs, budget=300.0)
    assert allocation.feasible
    assert allocation.safety_stock_scale == pytest.approx(0.5)
    assert allocation.final_investment == pytest.approx(300.0)
    for line in lines:
        assert line.recommended_order == pytest.approx(200.0)  # cycle floor untouched
        assert line.reorder_buffer == pytest.approx(50.0)      # 100 * 0.5
        assert line.projected_position == pytest.approx(0.0)   # 0 + 0 + 200 - 200


def test_budget_infeasible_zeroes_reorder_buffer_and_keeps_cycle_floor():
    # Same numbers as test_constraints.py::test_allocate_under_budget_infeasible_when_cycle_floor_exceeds:
    # cycle floor = 100 (200 units / 2 * $1); budget 80 < cycle floor -> infeasible.
    demand_plan = (_line("A", 200.0, 200.0),)
    inputs = {"A": SkuPurchaseInputs("A", on_hand=0.0, unit_cost=1.0, reorder_point=50.0)}
    lines, allocation = build_purchase_plan(demand_plan, inputs, budget=80.0)
    assert allocation.feasible is False
    assert allocation.final_investment == pytest.approx(100.0)
    line = lines[0]
    assert line.recommended_order == pytest.approx(200.0)  # units never trimmed
    assert line.reorder_buffer == pytest.approx(0.0)       # buffer zeroed first
