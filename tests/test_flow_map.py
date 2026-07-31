"""Tests for the pure flow_map engine: graph, measurement, verdict."""
from src.flow_map import Movement, build_graph


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
