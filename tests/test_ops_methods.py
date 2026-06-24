"""Operations methods from the Jacobs & Chase scan (small, pure engines).

Earned value (ch.4), learning curve (ch.6), lean sizing - kanban + takt (ch.14),
capacity cushion (ch.5), acceptance sampling (ch.13), and DEA (ch.25).
"""

import math

import pytest

from src.acceptance_sampling import design_single_sampling_plan, inspect_all_cheaper, prob_accept
from src.capacity_planning import capacity_cushion, required_capacity, utilization_from_cushion
from src.dea import dea_efficiency
from src.earned_value import earned_value
from src.kanban import num_kanban_cards, takt_time
from src.learning_curve import cumulative_time, learning_exponent, unit_time

# -- Earned value (SV/CV/SPI/CPI) ----------------------------------------------

def test_earned_value_metrics():
    ev = earned_value(planned=1000.0, earned=800.0, actual=900.0)
    assert ev.schedule_variance == pytest.approx(-200.0)   # behind schedule
    assert ev.cost_variance == pytest.approx(-100.0)       # over budget
    assert ev.spi == pytest.approx(0.8)
    assert ev.cpi == pytest.approx(800 / 900)
    assert ev.behind_schedule and ev.over_budget


# -- Learning curve (Yx = K x^n) -----------------------------------------------

def test_learning_curve_doubling_cuts_by_rate():
    # 80% curve: each doubling of cumulative output cuts unit time to 80%.
    assert learning_exponent(0.8) == pytest.approx(math.log(0.8) / math.log(2))
    assert unit_time(100.0, 1, 0.8) == pytest.approx(100.0)
    assert unit_time(100.0, 2, 0.8) == pytest.approx(80.0)
    assert unit_time(100.0, 4, 0.8) == pytest.approx(64.0)
    assert cumulative_time(100.0, 2, 0.8) == pytest.approx(180.0)


# -- Lean sizing: kanban cards + takt ------------------------------------------

def test_num_kanban_cards_rounds_up():
    # k = D*L*(1+S)/C = 12*1*(1+2)/6 = 6
    assert num_kanban_cards(demand_rate=12.0, lead_time=1.0, container_size=6.0, safety_factor=2.0) == 6
    assert num_kanban_cards(10.0, 1.0, 6.0, 0.0) == 2   # 10/6 -> ceil = 2


def test_takt_time():
    assert takt_time(available_time=450.0, required_units=504.0) == pytest.approx(450 / 504)


# -- Capacity cushion ----------------------------------------------------------

def test_capacity_cushion_and_utilization():
    assert capacity_cushion(capacity=120.0, expected_demand=100.0) == pytest.approx(0.20)
    assert utilization_from_cushion(0.20) == pytest.approx(1 / 1.2)
    assert required_capacity(demand=100.0, target_cushion=0.25) == pytest.approx(125.0)


# -- Acceptance sampling -------------------------------------------------------

def test_prob_accept_is_a_valid_probability():
    p = prob_accept(n=50, c=2, defect_rate=0.02)
    assert 0.0 <= p <= 1.0
    assert prob_accept(50, 50, 0.02) == pytest.approx(1.0)   # accept any number of defects


def test_design_single_sampling_plan_meets_both_risks():
    plan = design_single_sampling_plan(aql=0.01, ltpd=0.06, producer_risk=0.05, consumer_risk=0.10)
    assert plan.n > 0 and plan.c >= 0
    assert plan.prob_accept_aql >= 0.95 - 1e-9      # producer protected
    assert plan.prob_accept_ltpd <= 0.10 + 1e-9     # consumer protected


def test_inspect_all_break_even():
    # inspect iff unit inspection cost < defect_rate * downstream failure cost
    assert inspect_all_cheaper(unit_inspection_cost=0.20, defect_rate=0.03, downstream_failure_cost=10.0)
    assert not inspect_all_cheaper(0.50, 0.03, 10.0)   # 0.50 > 0.30


# -- DEA -----------------------------------------------------------------------

def test_dea_efficiency_frontier():
    # 1 input, 1 output: efficiency = (y/x) / max(y/x). C is best (ratio 2).
    inputs = [[1.0], [2.0], [1.0]]      # A, B, C
    outputs = [[1.0], [1.0], [2.0]]
    eff = dea_efficiency(inputs, outputs)
    assert eff[2] == pytest.approx(1.0, abs=1e-6)   # C on the frontier
    assert eff[0] == pytest.approx(0.5, abs=1e-6)
    assert eff[1] == pytest.approx(0.25, abs=1e-6)
    assert all(0.0 < e <= 1.0 + 1e-9 for e in eff)
