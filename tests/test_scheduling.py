"""Tests for the scheduling / sequencing engine (Jacobs & Chase ch. 22).

Operational sequencing Kern lacked: Johnson's rule (2-machine makespan), the
assignment/Hungarian optimum (one-to-one allocation), single-machine dispatching rules
(SPT/EDD/FCFS/LPT) with flow-time/lateness metrics, and the first-hour shift roster.
"""

import pytest

from src.scheduling import (
    Job,
    assign,
    dispatch_sequence,
    first_hour_roster,
    johnson_two_machine,
)

# -- Johnson's rule (two-machine flow shop, minimize makespan) ------------------

def test_johnson_classic_sequence_and_makespan():
    jobs = [("A", 5, 2), ("B", 1, 6), ("C", 9, 7), ("D", 3, 8), ("E", 10, 4)]
    seq, makespan = johnson_two_machine(jobs)
    assert seq == ["B", "D", "C", "E", "A"]
    assert makespan == pytest.approx(30.0)


# -- Assignment / Hungarian (minimize total cost) ------------------------------

def test_assignment_minimizes_total_cost():
    assignment, total = assign([[1.0, 2.0], [2.0, 1.0]])
    assert assignment == {0: 0, 1: 1}
    assert total == pytest.approx(2.0)


def test_assignment_three_by_three():
    cost = [[9, 2, 7], [6, 4, 3], [5, 8, 1]]
    assignment, total = assign(cost)
    # optimum: row0->col1 (2), row1->col0 (6), row2->col2 (1) = 9
    assert total == pytest.approx(9.0)
    assert sorted(assignment.values()) == [0, 1, 2]   # a valid one-to-one assignment


# -- Single-machine dispatching rules ------------------------------------------

_JOBS = [Job("A", processing=3.0, due=5.0), Job("B", processing=1.0, due=3.0),
         Job("C", processing=2.0, due=7.0)]


def test_spt_minimizes_mean_flow_time():
    res = dispatch_sequence(_JOBS, rule="SPT")
    assert res.sequence == ["B", "C", "A"]
    assert res.mean_flow_time == pytest.approx((1 + 3 + 6) / 3)


def test_edd_orders_by_due_date():
    res = dispatch_sequence(_JOBS, rule="EDD")
    assert res.sequence == ["B", "A", "C"]


def test_unknown_rule_raises():
    with pytest.raises(ValueError):
        dispatch_sequence(_JOBS, rule="WAT")


# -- First-hour shift roster ---------------------------------------------------

def test_first_hour_roster_covers_each_hour():
    starts, on_duty = first_hour_roster([3, 5, 4, 2], shift_length=2)
    assert starts == [3, 2, 2, 0]
    assert all(on_duty[h] >= [3, 5, 4, 2][h] for h in range(4))
