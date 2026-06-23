"""Tests for the S&OP / IBP deck composer (jobs.sop_deliverable).

Turns a SopReview (the monthly cadence output) into the client-facing deliverable
SCM mode advertises: the recommended supply plan, the demand-supply gap, the cost /
working-capital / service trade-offs, and the exec sign-off handoff.
"""

import pytest

from jobs.sop_deliverable import build
from src.decision_options import Objective
from src.deliverable import Deliverable
from src.sop import CostModel, run_sop_cycle

_DEMAND = [100.0, 120.0, 80.0, 100.0]


def test_build_returns_a_deliverable_naming_the_recommended_plan():
    review = run_sop_cycle(_DEMAND, opening_inventory=50.0, target=20.0)

    d = build(review, client="Acme")

    assert isinstance(d, Deliverable)
    assert d.client == "Acme"
    assert review.recommended.name in d.summary
    assert d.confidence == pytest.approx(review.outcome.confidence)


def test_kpis_cover_service_and_working_capital():
    review = run_sop_cycle(_DEMAND, opening_inventory=50.0, target=20.0)

    d = build(review)

    names = {k.name for k in d.kpis}
    assert "Fill rate" in names
    assert any("inventory" in k.name.lower() for k in d.kpis)  # peak inventory / working capital


def test_a_shortfall_plan_surfaces_a_demand_supply_gap_finding():
    # Front-loaded demand + a steep capacity-change cost makes the cheap level plan
    # win even though it stocks out -> the gap must be called out, not hidden.
    demand = [200.0, 40.0, 40.0, 40.0]
    review = run_sop_cycle(
        demand,
        opening_inventory=0.0,
        target=0.0,
        cost=CostModel(capacity_change_per_unit=100.0, shortage_per_unit_per_period=1.0),
        objectives=[
            Objective("total_cost", weight=3.0, maximize=False),
            Objective("fill_rate", weight=1.0, maximize=True),
        ],
    )
    assert review.recommended.name == "Level"
    assert review.recommended.total_shortfall > 0

    text = build(review).to_markdown().lower()
    assert "short" in text or "unmet" in text or "gap" in text


def test_recommendations_include_adopting_the_recommended_plan():
    review = run_sop_cycle(_DEMAND, opening_inventory=50.0, target=20.0)

    d = build(review)

    assert any(review.recommended.name.lower() in r.lower() for r in d.recommendations)


def test_coverage_block_states_the_exec_signoff_residual():
    review = run_sop_cycle(_DEMAND, opening_inventory=50.0, target=20.0)

    md = build(review).to_markdown().lower()

    assert "sign-off" in md or "sign off" in md


def test_markdown_is_ascii_only_for_cp1252_safety():
    review = run_sop_cycle(_DEMAND, opening_inventory=50.0, target=20.0)

    md = build(review, citations=("Chopra & Meindl, Aggregate Planning",)).to_markdown()

    assert md.isascii()


def test_citations_pass_through_to_the_deliverable():
    review = run_sop_cycle(_DEMAND, opening_inventory=50.0, target=20.0)

    d = build(review, citations=("Oliver Wight - 5-step S&OP cadence",))

    assert "Oliver Wight - 5-step S&OP cadence" in d.citations


def test_alternatives_are_documented_as_findings():
    review = run_sop_cycle(_DEMAND, opening_inventory=50.0, target=20.0)

    d = build(review)

    text = d.to_markdown()
    # The two strategies that were not recommended should still appear (auditability).
    not_recommended = {"Chase", "Level", "Hybrid"} - {review.recommended.name}
    assert all(name in text for name in not_recommended)
