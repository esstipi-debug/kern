"""Kraljic supplier segmentation (strategic SRM, CSCP gap) - pure engine tests.

A hand-checkable 4-supplier panel lands exactly one supplier in each Kraljic
quadrant, so every band boundary and strategy mapping is asserted numerically.
"""

import math

import pytest

from src.guided import verify_guided
from src.supplier_management import (
    RiskDriver,
    SupplierInput,
    composite_risk,
    segment_outcome,
    segment_suppliers,
)

_DRIVERS = [RiskDriver("lead", 1.0), RiskDriver("single", 1.0)]


def _panel() -> list[SupplierInput]:
    # spends: A=500, B=300, C=120, D=80  (total 1000)
    # Pareto 0.8 -> A,B are high impact (cum-before < 0.8); C,D low.
    return [
        SupplierInput("A", 500.0, {"lead": 1.0, "single": 1.0}),  # high impact, high risk
        SupplierInput("B", 300.0, {"lead": 0.2, "single": 0.0}),  # high impact, low risk
        SupplierInput("C", 120.0, {"lead": 0.8, "single": 1.0}),  # low impact,  high risk
        SupplierInput("D", 80.0, {"lead": 0.1, "single": 0.0}),   # low impact,  low risk
    ]


def test_composite_risk_is_weighted_average_of_drivers():
    assert composite_risk({"lead": 1.0, "single": 1.0}, _DRIVERS) == pytest.approx(1.0)
    assert composite_risk({"lead": 0.2, "single": 0.0}, _DRIVERS) == pytest.approx(0.1)
    assert composite_risk({"lead": 0.8, "single": 1.0}, _DRIVERS) == pytest.approx(0.9)


def test_composite_risk_honors_unequal_weights():
    drivers = [RiskDriver("lead", 3.0), RiskDriver("single", 1.0)]
    # (3*1.0 + 1*0.0) / 4 = 0.75
    assert composite_risk({"lead": 1.0, "single": 0.0}, drivers) == pytest.approx(0.75)


def test_segment_assigns_one_supplier_to_each_kraljic_quadrant():
    segs = {s.supplier: s for s in segment_suppliers(_panel(), _DRIVERS)}

    assert segs["A"].quadrant == "strategic"
    assert segs["B"].quadrant == "leverage"
    assert segs["C"].quadrant == "bottleneck"
    assert segs["D"].quadrant == "non_critical"

    assert segs["A"].impact_band == "high" and segs["A"].risk_band == "high"
    assert segs["B"].impact_band == "high" and segs["B"].risk_band == "low"
    assert segs["C"].impact_band == "low" and segs["C"].risk_band == "high"
    assert segs["D"].impact_band == "low" and segs["D"].risk_band == "low"


def test_segment_computes_spend_share_and_supply_risk():
    segs = {s.supplier: s for s in segment_suppliers(_panel(), _DRIVERS)}
    assert segs["A"].spend_share == pytest.approx(0.5)
    assert segs["B"].spend_share == pytest.approx(0.3)
    assert segs["A"].supply_risk == pytest.approx(1.0)
    assert segs["C"].supply_risk == pytest.approx(0.9)
    assert segs["A"].profit_impact == pytest.approx(0.5)


def test_strategy_maps_from_quadrant():
    segs = {s.supplier: s for s in segment_suppliers(_panel(), _DRIVERS)}
    assert segs["A"].strategy.startswith("partner")
    assert segs["B"].strategy.startswith("competitive tender")
    assert segs["C"].strategy.startswith("secure supply")
    assert segs["D"].strategy.startswith("simplify")
    for s in segs.values():
        assert s.strategy.isascii()


def test_risk_threshold_is_configurable():
    # Raise the bar so C (0.9) stays high but a 0.85 supplier would flip.
    segs = {s.supplier: s for s in segment_suppliers(_panel(), _DRIVERS, risk_threshold=0.95)}
    assert segs["C"].risk_band == "low"        # 0.9 < 0.95
    assert segs["A"].risk_band == "high"       # 1.0 >= 0.95


def test_segment_outcome_is_a_protected_options_result():
    segs = segment_suppliers(_panel(), _DRIVERS)
    outcome = segment_outcome(segs, summary="Segmented 4 suppliers.")
    assert outcome.status == "options"
    assert verify_guided(outcome) == []
    # exposure = spend_share * supply_risk -> A (0.5) is the top priority.
    assert outcome.options[0].label == "A"
    assert any(o.recommended for o in outcome.options)
    for o in outcome.options:
        assert math.isfinite(o.score)


def test_empty_panel_returns_no_segments():
    assert segment_suppliers([], _DRIVERS) == []


def test_segment_outcome_raises_on_empty_segments():
    with pytest.raises(ValueError):
        segment_outcome([], summary="nothing")
