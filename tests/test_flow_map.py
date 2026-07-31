"""Tests for the pure flow_map engine: graph, measurement, verdict."""
import pytest

from src.flow_map import (
    MEASURED,
    MIN_OBSERVATIONS,
    UNMEASURABLE,
    Movement,
    analyze,
    build_graph,
    measure_edges,
)
from src.guided import HANDOFF, OPTIONS, verify_guided


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


def test_mixed_equal_and_unequal_bidirectional_pairs_get_per_pair_wording():
    """Regression (MINOR, 2026-07-31 flow_map fix wave): with one equal-volume 2-cycle
    (A<->B) and one unequal-volume 2-cycle (C<->D), the old all-or-nothing note dropped
    the 'ignored' clause entirely because not ALL pairs were unequal -- even though a leg
    genuinely was ignored for C<->D. The note must name C<->D specifically and must not
    claim A<->B had a leg ignored.
    """
    movements = [
        _mv("A", "B", qty=50.0),
        _mv("B", "A", qty=50.0),
        _mv("C", "D", qty=100.0),
        _mv("D", "C", qty=5.0),
    ]
    graph = build_graph(movements)
    assert graph.bidirectional == (("A", "B"), ("C", "D"))
    # The weaker (ignored) leg is named in the exact direction it was observed (D->C
    # carried less volume than C->D), not the pair's canonical (sorted) order.
    assert "the lower-volume leg is ignored when computing echelons for D <-> C" in graph.cycle_note
    assert "for A <-> B" not in graph.cycle_note


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


def _edge_movements(origin, destination, days, qty=10.0, cost=2.0):
    return [_mv(origin, destination, days=d, qty=qty, cost=cost) for d in days]


def test_edge_with_no_cost_rows_has_none_mean_unit_cost_not_zero():
    """Regression for CRITICAL 2 (2026-07-31 flow_map fix wave): a cost-free movements
    file must report mean_unit_cost as None, not 0.0 -- 0.0 reads to a client as "this
    lane's protective stock is worthless," which is not what "we never measured it" means.
    """
    movements = [_mv("DC-1", "STORE-1", days=d, cost=None) for d in [2, 2, 3, 3, 4, 4, 5, 9]]
    (edge,) = measure_edges(movements)
    assert edge.status == MEASURED
    assert edge.mean_unit_cost is None


def test_edge_with_genuinely_zero_cost_rows_is_not_treated_as_missing():
    """A real zero-cost transfer (e.g. an intracompany relocation) must still count as a
    measured cost of 0.0, not be folded into "no cost recorded."
    """
    movements = [_mv("DC-1", "STORE-1", days=d, cost=0.0) for d in [2, 2, 3, 3, 4, 4, 5, 9]]
    (edge,) = measure_edges(movements)
    assert edge.status == MEASURED
    assert edge.mean_unit_cost == 0.0


def test_buffers_omit_value_when_no_cost_is_recorded_and_rank_by_units():
    """No lane in this graph has a recorded cost, so every buffer_value is None; ranking
    must still be deterministic, falling back to buffer_units.
    """
    noisy_small = [1.0, 9.0, 2.0, 8.0, 1.0, 9.0, 2.0, 8.0]
    noisy_big = [1.0, 9.0, 2.0, 8.0, 1.0, 9.0, 2.0, 8.0]
    movements = (
        [Movement(origin="DC-1", destination="STORE-A", product="SKU-A", quantity=10.0,
                   transit_days=d, unit_cost=None) for d in noisy_small]
        + [Movement(origin="DC-1", destination="STORE-B", product="SKU-A", quantity=50.0,
                     transit_days=d, unit_cost=None) for d in noisy_big]
    )
    report = analyze(movements, span_days=90.0)
    assert all(b.buffer_value is None for b in report.buffers)
    # Higher volume -> higher stdev-driven buffer_units -> ranked first when value is
    # unavailable for every lane.
    assert report.buffers[0].destination == "STORE-B"
    option = report.outcome.options[0]
    assert "in value" not in option.summary
    assert "unit(s) of protective stock" in option.summary


def test_analyze_ranks_the_noisiest_edge_first_and_recommends_it():
    steady = [2.0, 2.0, 2.0, 2.0, 2.0, 2.0, 2.0, 2.0]
    noisy = [1.0, 9.0, 2.0, 8.0, 1.0, 9.0, 2.0, 8.0]
    movements = (
        _edge_movements("DC-1", "STORE-STEADY", steady)
        + _edge_movements("DC-1", "STORE-NOISY", noisy)
        + _edge_movements("SUP-1", "DC-1", steady)
    )
    report = analyze(movements, span_days=90.0)
    assert report.outcome.status == OPTIONS
    assert report.buffers[0].destination == "STORE-NOISY"
    assert report.buffers[0].buffer_value > report.buffers[1].buffer_value
    assert report.outcome.options[0].recommended is True
    assert "STORE-NOISY" in report.outcome.options[0].label


def test_unmeasurable_edges_never_enter_the_ranking():
    movements = (
        _edge_movements("DC-1", "STORE-A", [1.0, 9.0, 1.0, 9.0, 1.0, 9.0, 1.0, 9.0])
        + _edge_movements("DC-1", "STORE-SHORT", [1.0, 20.0, 1.0], qty=1000.0)
    )
    report = analyze(movements, span_days=90.0)
    ranked = {(b.origin, b.destination) for b in report.buffers}
    assert ("DC-1", "STORE-SHORT") not in ranked
    short = next(e for e in report.edges if e.destination == "STORE-SHORT")
    assert short.status == UNMEASURABLE


def test_unmeasurable_volume_share_and_negative_transit_are_counted():
    movements = (
        _edge_movements("DC-1", "STORE-A", [1.0, 9.0, 1.0, 9.0, 1.0, 9.0, 1.0, 9.0], qty=10.0)
        + _edge_movements("DC-1", "STORE-SHORT", [-3.0, 4.0], qty=30.0)
    )
    report = analyze(movements, span_days=90.0)
    assert report.n_movements == 10
    assert report.n_negative_transit == 1
    assert round(report.unmeasurable_volume_share, 4) == 0.4286


def test_analyze_requires_a_positive_span():
    with pytest.raises(ValueError):
        analyze([_mv("A", "B")], span_days=0.0)


def test_analyze_returns_handoff_when_no_lane_is_measurable():
    movements = (
        _edge_movements("DC-1", "STORE-A", [1.0, 2.0, 3.0], qty=10.0)
        + _edge_movements("DC-1", "STORE-B", [4.0, 5.0], qty=5.0)
    )
    report = analyze(movements, span_days=90.0)
    assert report.outcome.status == HANDOFF
    assert report.buffers == ()
    assert len(report.outcome.handoffs) == 1
    assert report.outcome.handoffs[0].steps
    assert verify_guided(report.outcome) == []


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
