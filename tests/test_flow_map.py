"""Tests for the pure flow_map engine: graph, measurement, verdict."""
from src.flow_map import (
    MEASURED,
    MIN_OBSERVATIONS,
    UNMEASURABLE,
    Movement,
    build_graph,
    measure_edges,
)


def _mv(origin, destination, days=3.0, qty=10.0, cost=1.0, product="SKU-A"):
    return Movement(
        origin=origin, destination=destination, product=product,
        quantity=qty, transit_days=days, unit_cost=cost,
    )


def test_build_graph_assigns_echelons_from_sources():
    movements = [
        _mv("SUP-1", "DC-1"),
        _mv("DC-1", "STORE-1"),
        _mv("DC-1", "STORE-2"),
    ]
    graph = build_graph(movements)
    assert graph.nodes == ("SUP-1", "DC-1", "STORE-1", "STORE-2")
    assert graph.echelon == {"SUP-1": 0, "DC-1": 1, "STORE-1": 2, "STORE-2": 2}
    assert graph.edges == (("DC-1", "STORE-1"), ("DC-1", "STORE-2"), ("SUP-1", "DC-1"))
    assert graph.bidirectional == ()
    assert graph.cycle_note == ""


def test_two_cycle_is_declared_and_the_smaller_leg_is_ignored_for_echelons():
    movements = [
        _mv("SUP-1", "DC-1"),
        _mv("DC-1", "DC-2", qty=100.0),
        _mv("DC-2", "DC-1", qty=5.0),
    ]
    graph = build_graph(movements)
    assert graph.bidirectional == (("DC-1", "DC-2"),)
    assert graph.echelon["DC-2"] == 2
    assert "DC-1 <-> DC-2" in graph.cycle_note


def test_fully_cyclic_graph_flattens_echelons_and_says_so():
    movements = [_mv("A", "B", qty=10.0), _mv("B", "A", qty=10.0)]
    graph = build_graph(movements)
    assert set(graph.echelon.values()) == {0}
    assert "no source node" in graph.cycle_note


def test_empty_movements_produces_honest_cycle_note():
    movements = []
    graph = build_graph(movements)
    assert graph.nodes == ()
    assert graph.cycle_note == ""


def test_equal_volume_bidirectional_pair_does_not_claim_leg_ignored():
    movements = [
        _mv("SUP-1", "A", qty=100.0),
        _mv("A", "B", qty=50.0),
        _mv("B", "A", qty=50.0),
    ]
    graph = build_graph(movements)
    assert graph.bidirectional == (("A", "B"),)
    assert "lower-volume leg is ignored" not in graph.cycle_note
    assert "bidirectional flow detected (A <-> B)" in graph.cycle_note


def test_edge_with_enough_observations_reports_statistics():
    movements = [_mv("DC-1", "STORE-1", days=d) for d in [2, 2, 3, 3, 4, 4, 5, 9]]
    (edge,) = measure_edges(movements)
    assert edge.n == 8
    assert edge.status == MEASURED
    assert edge.mean_days == 4.0
    assert round(edge.stdev_days, 4) == 2.2678
    assert edge.p50_days == 3.5
    assert round(edge.p90_days, 4) == 6.2
    assert edge.volume == 80.0


def test_edge_below_the_threshold_is_unmeasurable_with_empty_statistics():
    movements = [_mv("DC-1", "STORE-9", days=d) for d in [2, 3, 4, 5, 6, 7, 8]]
    (edge,) = measure_edges(movements)
    assert edge.n == 7
    assert edge.status == UNMEASURABLE
    assert edge.mean_days is None
    assert edge.stdev_days is None
    assert edge.p50_days is None
    assert edge.p90_days is None
    assert edge.volume == 70.0


def test_threshold_is_configurable():
    movements = [_mv("DC-1", "STORE-9", days=d) for d in [2, 3, 4]]
    (edge,) = measure_edges(movements, min_observations=3)
    assert edge.status == MEASURED
    assert MIN_OBSERVATIONS == 8


def test_edges_are_sorted_and_bidirectional_is_flagged():
    movements = [_mv("B", "A"), _mv("A", "B")]
    edges = measure_edges(movements, bidirectional=(("A", "B"),))
    assert [(e.origin, e.destination) for e in edges] == [("A", "B"), ("B", "A")]
    assert all(e.bidirectional for e in edges)


def test_bidirectional_flagging_does_not_cross_contaminate():
    # Regression test: bidirectional set must contain exact pairs, not just pooled nodes.
    # With pairs (("A","B"), ("C","D")), the A->D cross-edge should NOT be flagged.
    movements = [
        _mv("A", "B"),  # declared pair
        _mv("B", "A"),  # declared pair
        _mv("C", "D"),  # declared pair
        _mv("D", "C"),  # declared pair
        _mv("A", "D"),  # cross-edge, NOT declared
    ]
    edges = measure_edges(movements, bidirectional=(("A", "B"), ("C", "D")))
    edge_flags = {(e.origin, e.destination): e.bidirectional for e in edges}
    assert edge_flags[("A", "B")] is True
    assert edge_flags[("A", "D")] is False
    assert edge_flags[("B", "A")] is True
    assert edge_flags[("C", "D")] is True
    assert edge_flags[("D", "C")] is True
