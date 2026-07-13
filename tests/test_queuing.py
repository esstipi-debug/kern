"""Tests for the waiting-line / queuing engine (from Jacobs & Chase ch. 10).

A capability Kern lacked entirely: closed-form congestion models that turn arrival/
service rates (and their variability) into queue length, wait time, and the cost-optimal
number of servers - for sizing dock doors, pick stations, labour, MRO crews, returns desks.
"""

import math

import pytest

from src.queuing import (
    finite_source,
    ggc_approx,
    md1,
    mm1,
    mmc,
    optimize_servers,
)

# -- M/M/1 ---------------------------------------------------------------------

def test_mm1_closed_form():
    q = mm1(arrival_rate=2.0, service_rate=3.0)
    assert q.utilization == pytest.approx(2 / 3)
    assert q.ls == pytest.approx(2.0)           # rho/(1-rho) = lambda/(mu-lambda)
    assert q.lq == pytest.approx(4 / 3)         # lambda^2 / (mu(mu-lambda))
    assert q.wq == pytest.approx(q.lq / 2.0)    # Wq = Lq/lambda
    assert q.ws == pytest.approx(1.0)           # 1/(mu-lambda)


def test_mm1_requires_stability():
    with pytest.raises(ValueError):
        mm1(arrival_rate=3.0, service_rate=3.0)   # rho >= 1


# -- M/D/1 (constant service halves the queue) ---------------------------------

def test_md1_halves_the_mm1_queue():
    assert md1(2.0, 3.0).lq == pytest.approx(mm1(2.0, 3.0).lq / 2.0)


# -- M/M/c (Erlang-C) ----------------------------------------------------------

def test_mmc_two_servers():
    q = mmc(arrival_rate=2.0, service_rate=3.0, servers=2)
    assert q.utilization == pytest.approx(1 / 3)     # lambda/(c*mu)
    assert q.lq == pytest.approx(0.083333, abs=1e-4)
    assert q.ls == pytest.approx(0.75, abs=1e-4)     # Lq + lambda/mu
    assert 0.0 < q.prob_wait < 1.0


def test_mmc_reduces_to_mm1_for_one_server():
    a = mmc(2.0, 3.0, servers=1)
    b = mm1(2.0, 3.0)
    assert a.lq == pytest.approx(b.lq)
    assert a.ls == pytest.approx(b.ls)


def test_mmc_requires_enough_servers():
    with pytest.raises(ValueError):
        mmc(arrival_rate=10.0, service_rate=3.0, servers=3)   # lambda >= c*mu


# -- G/G/c approximation (Kingman / Sakasegawa: variability drives the wait) ----

def test_ggc_matches_mm1_when_both_cv_are_one():
    approx = ggc_approx(2.0, 3.0, servers=1, cv_arrival=1.0, cv_service=1.0)
    assert approx.wq == pytest.approx(mm1(2.0, 3.0).wq, rel=1e-6)


def test_ggc_wait_grows_with_variability():
    low = ggc_approx(2.0, 3.0, 1, cv_arrival=0.5, cv_service=0.5)
    high = ggc_approx(2.0, 3.0, 1, cv_arrival=2.0, cv_service=2.0)
    assert high.wq > low.wq                       # more variable -> longer wait


# -- finite-source (machine-repair) --------------------------------------------

def test_finite_source_sanity_and_monotonicity():
    one = finite_source(population=5, servers=1, run_rate=0.5, service_rate=2.0)
    two = finite_source(population=5, servers=2, run_rate=0.5, service_rate=2.0)
    for q in (one, two):
        assert 0.0 < q.utilization <= 1.0
        assert q.lq >= 0.0
        assert 0.0 <= q.ls <= 5.0
        assert math.isfinite(q.wq) and q.wq >= 0.0
    assert two.lq < one.lq                        # a second repairer cuts the queue


# -- server-count cost optimization --------------------------------------------

def test_optimize_servers_picks_min_total_cost():
    choices = optimize_servers(
        arrival_rate=2.0, service_rate=3.0,
        wait_cost_per_unit_time=10.0, server_cost_per_unit_time=5.0, max_servers=4,
    )
    best = choices[0]
    assert best.servers == 2                      # c=1 -> 25.0, c=2 -> 17.5, c=3 -> ~21.9
    assert best.total_cost == pytest.approx(17.5, abs=0.2)
    assert choices == sorted(choices, key=lambda c: c.total_cost)
    assert all(c.total_cost == c.waiting_cost + c.service_cost for c in choices)
