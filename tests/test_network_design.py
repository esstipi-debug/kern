"""Tests for src/network_design.py - the p-median multi-facility MILP engine.

Every instance is small enough to verify by hand / enumeration.
"""
import math

import pytest

from src.facility_location import DemandPoint
from src.network_design import CandidateSite, solve_p_median


def _line(*xs_w: tuple[float, float]) -> list[DemandPoint]:
    return [DemandPoint(f"D{i}", x, 0.0, w) for i, (x, w) in enumerate(xs_w)]


def test_p1_picks_the_weighted_median_site():
    demands = _line((0, 1), (1, 1), (2, 1))
    sites = [CandidateSite("S0", 0, 0), CandidateSite("S1", 1, 0), CandidateSite("S2", 2, 0)]
    d = solve_p_median(demands, sites, 1)
    assert d.feasible
    assert d.open_sites == ("S1",)                       # x=1 is the median of {0,1,2}
    assert d.total_weighted_distance == pytest.approx(2.0)   # 1 + 0 + 1
    assert d.saving_vs_baseline == pytest.approx(0.0)        # p=1 == single-facility baseline


def test_p2_two_clusters_open_the_two_cluster_medians():
    demands = [
        DemandPoint("D0", 0, 0, 1), DemandPoint("D1", 1, 0, 1), DemandPoint("D2", 2, 0, 1),
        DemandPoint("D3", 10, 0, 1), DemandPoint("D4", 11, 0, 1), DemandPoint("D5", 12, 0, 1),
    ]
    sites = [CandidateSite(f"S{x}", x, 0) for x in (0, 1, 2, 10, 11, 12)]
    d = solve_p_median(demands, sites, 2)
    assert d.feasible
    assert set(d.open_sites) == {"S1", "S11"}            # unique optimum: the two cluster medians
    assert d.total_weighted_distance == pytest.approx(4.0)   # (1+0+1) per cluster
    assert d.assignment["D0"] == "S1" and d.assignment["D5"] == "S11"
    assert d.baseline_distance == pytest.approx(30.0)        # best single site (x=2 or x=10): 30
    assert d.saving_vs_baseline == pytest.approx(26.0)
    assert d.saving_pct == pytest.approx(26.0 / 30.0)


def test_capacity_forces_a_non_nearest_assignment():
    demands = [DemandPoint("A", 0, 0, 10), DemandPoint("B", 0.5, 0, 10)]
    sites = [CandidateSite("near", 0, 0, capacity=10), CandidateSite("far", 10, 0, capacity=100)]
    d = solve_p_median(demands, sites, 2)                 # only 2 sites, p=2 -> both open
    assert d.feasible
    assert d.assignment["A"] == "near"                   # near is full (cap 10) after A
    assert d.assignment["B"] == "far"                    # B pushed to far by the cap
    assert d.total_weighted_distance == pytest.approx(95.0)  # 0 + 9.5*10


def test_infeasible_when_capacity_cannot_cover_demand():
    demands = [DemandPoint("A", 0, 0, 10), DemandPoint("B", 1, 0, 10), DemandPoint("C", 2, 0, 10)]
    sites = [CandidateSite("only", 0, 0, capacity=20)]   # 20 < 30 total demand
    d = solve_p_median(demands, sites, 1)
    assert d.feasible is False
    assert d.open_sites == ()
    assert not math.isfinite(d.total_weighted_distance)


def test_fixed_cost_breaks_a_distance_tie():
    demands = _line((0, 1), (10, 1))
    sites = [CandidateSite("cheap_mid", 5, 0, fixed_cost=1.0),
             CandidateSite("dear_mid", 5, 0, fixed_cost=5.0)]   # same location, cheaper wins
    d = solve_p_median(demands, sites, 1)
    assert d.open_sites == ("cheap_mid",)
    assert d.total_weighted_distance == pytest.approx(10.0)     # 5 + 5
    assert d.total_fixed_cost == pytest.approx(1.0)
    assert d.objective == pytest.approx(11.0)                   # distance 10 + fixed 1


def test_rejects_p_out_of_range():
    demands = _line((0, 1), (1, 1))
    sites = [CandidateSite("S0", 0, 0), CandidateSite("S1", 1, 0)]
    with pytest.raises(ValueError):
        solve_p_median(demands, sites, 0)
    with pytest.raises(ValueError):
        solve_p_median(demands, sites, 3)
