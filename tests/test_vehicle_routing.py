"""Tests for the offline vehicle-routing engine: Clarke-Wright savings + sweep (Ballou Cap. 7)."""

import math

import pytest

from src.logistics.routing import (
    Depot,
    Stop,
    arrival_times,
    clarke_wright_savings,
    naive_distance,
    route_distance,
    sweep,
    time_window_violations,
)

DEPOT = Depot(0.0, 0.0)


def _four_stops() -> list[Stop]:
    # Two natural pairs on opposite sides of the depot; nice integer (3-4-5-style) distances.
    return [
        Stop("A", 3, 4, demand=2),    # depot-A = 5
        Stop("B", 6, 8, demand=2),    # depot-B = 10, A-B = 5
        Stop("C", -3, 4, demand=2),   # depot-C = 5, A-C = 6
        Stop("D", -6, 8, demand=2),   # depot-D = 10, C-D = 5
    ]


def test_naive_distance_is_round_trip_per_stop():
    stops = _four_stops()
    assert naive_distance(stops, DEPOT) == pytest.approx(2 * (5 + 10 + 5 + 10))


def test_route_distance_through_depot_and_back():
    stops = _four_stops()
    by_id = {s.stop_id: s for s in stops}
    assert route_distance(("A", "B"), by_id, DEPOT) == pytest.approx(5 + 5 + 10)


def test_savings_merges_the_two_geographic_pairs_under_capacity():
    stops = _four_stops()
    plan = clarke_wright_savings(stops, DEPOT, capacity=4)
    assert plan.method == "savings"
    assert plan.n_vehicles == 2
    route_sets = {frozenset(r.stop_ids) for r in plan.routes}
    assert route_sets == {frozenset({"A", "B"}), frozenset({"C", "D"})}
    assert plan.total_distance == pytest.approx(40.0)  # 20 + 20, see docstring math
    for r in plan.routes:
        assert r.load == pytest.approx(4.0)


def test_savings_with_unlimited_capacity_merges_everything_with_positive_savings():
    stops = _four_stops()
    plan = clarke_wright_savings(stops, DEPOT, capacity=1_000_000)
    # every pairwise savings is positive here, so a single route should win
    assert plan.n_vehicles == 1
    assert plan.total_distance < naive_distance(stops, DEPOT)


def test_savings_rejects_capacity_smaller_than_a_single_stop_demand():
    stops = [Stop("A", 1, 0, demand=5)]
    with pytest.raises(ValueError, match="capacity"):
        clarke_wright_savings(stops, DEPOT, capacity=4)


def test_savings_single_stop_is_a_trivial_round_trip():
    stops = [Stop("A", 3, 4, demand=1)]
    plan = clarke_wright_savings(stops, DEPOT, capacity=10)
    assert plan.n_vehicles == 1
    assert plan.routes[0].stop_ids == ("A",)
    assert plan.total_distance == pytest.approx(10.0)  # there and back


def _sweep_stops() -> list[Stop]:
    # Four stops at 0/90/180/270 degrees so angular clustering is unambiguous.
    return [
        Stop("E", 10, 0, demand=3),
        Stop("N", 0, 10, demand=3),
        Stop("W", -10, 0, demand=3),
        Stop("S", 0, -10, demand=3),
    ]


def test_sweep_clusters_contiguous_angles_under_capacity():
    stops = _sweep_stops()
    plan = sweep(stops, DEPOT, capacity=6)
    assert plan.method == "sweep"
    assert plan.n_vehicles == 2
    route_sets = {frozenset(r.stop_ids) for r in plan.routes}
    assert route_sets == {frozenset({"E", "N"}), frozenset({"W", "S"})}
    assert plan.total_distance == pytest.approx(2 * (10 + math.hypot(10, 10) + 10))


def test_sweep_rejects_capacity_smaller_than_a_single_stop_demand():
    stops = [Stop("A", 1, 0, demand=5)]
    with pytest.raises(ValueError, match="capacity"):
        sweep(stops, DEPOT, capacity=4)


def test_sweep_and_savings_reject_empty_stop_list():
    with pytest.raises(ValueError):
        sweep([], DEPOT, capacity=10)
    with pytest.raises(ValueError):
        clarke_wright_savings([], DEPOT, capacity=10)


def test_sweep_and_savings_reject_non_positive_speed():
    stops = [Stop("A", 1, 0, demand=1)]
    with pytest.raises(ValueError, match="speed"):
        clarke_wright_savings(stops, DEPOT, capacity=10, speed=0)
    with pytest.raises(ValueError, match="speed"):
        sweep(stops, DEPOT, capacity=10, speed=-1)


def test_savings_capacity_tolerates_float_imprecision():
    # 0.1 + 0.1 + 0.1 != 0.3 exactly in binary float; the merge must still be allowed.
    stops = [Stop("A", 1, 0, demand=0.1), Stop("B", 2, 0, demand=0.1), Stop("C", 3, 0, demand=0.1)]
    plan = clarke_wright_savings(stops, DEPOT, capacity=0.3)
    assert plan.n_vehicles == 1


def test_arrival_times_accumulate_travel_and_service_time():
    stops = [Stop("A", 3, 4, demand=1, service_time=2.0), Stop("B", 6, 8, demand=1)]
    by_id = {s.stop_id: s for s in stops}
    arrivals = arrival_times(("A", "B"), by_id, DEPOT, speed=1.0)
    assert arrivals[0] == pytest.approx(5.0)          # depot -> A
    assert arrivals[1] == pytest.approx(5.0 + 2.0 + 5.0)  # + service + A -> B


def test_arrival_time_waits_for_an_early_time_window():
    stops = [Stop("A", 3, 4, demand=1, tw_start=20.0)]
    by_id = {s.stop_id: s for s in stops}
    arrivals = arrival_times(("A",), by_id, DEPOT, speed=1.0)
    assert arrivals[0] == pytest.approx(20.0)          # arrives at 5, waits until the window opens


def test_time_window_violations_flags_late_arrivals_only():
    stops = [Stop("A", 3, 4, demand=1, tw_end=1.0), Stop("B", 6, 8, demand=1, tw_end=100.0)]
    by_id = {s.stop_id: s for s in stops}
    arrivals = arrival_times(("A", "B"), by_id, DEPOT, speed=1.0)
    late = time_window_violations(("A", "B"), arrivals, by_id)
    assert late == ["A"]                               # arrives at 5 > tw_end 1; B is on time
