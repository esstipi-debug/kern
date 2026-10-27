"""Tests for haversine distance, circuity factor, and injected distance matrix in routing."""

import pytest

from src.logistics.routing import (
    Depot,
    Stop,
    clarke_wright_savings,
    haversine_distance,
    pair_distance,
)


def test_haversine_distance_known_city_pairs():
    # London: (lat 51.5074, lon -0.1278), Paris: (lat 48.8566, lon 2.3522)
    # Known great circle distance is ~343.5 - 344.0 km
    dist = haversine_distance(51.5074, -0.1278, 48.8566, 2.3522)
    assert dist == pytest.approx(343.8, rel=1e-2)

    # Madrid: (lat 40.4168, lon -3.7038), Barcelona: (lat 41.3879, lon 2.1699)
    # Known great circle distance is ~504 - 506 km
    dist_spain = haversine_distance(40.4168, -3.7038, 41.3879, 2.1699)
    assert dist_spain == pytest.approx(505.0, rel=1e-2)


def test_pair_distance_geographic_with_circuity():
    # Between Madrid and Barcelona, with k=1.30:
    # road distance should be ~505 * 1.30 = ~656.5 km
    # lon is x, lat is y
    road_dist = pair_distance(
        -3.7038, 40.4168,
        2.1699, 41.3879,
        is_geographic=True,
        circuity_factor=1.30,
    )
    assert road_dist == pytest.approx(505.0 * 1.30, rel=1e-2)


def test_pair_distance_injected_matrix():
    matrix = {
        ("depot", "A"): 12.5,
        ("A", "B"): 8.0,
        ("B", "depot"): 15.0,
    }
    # Regardless of (x, y) coordinates, matrix value takes priority
    d1 = pair_distance(0, 0, 100, 100, id_a="depot", id_b="A", matrix=matrix)
    assert d1 == 12.5

    # Symmetric lookup if reversed
    d2 = pair_distance(100, 100, 0, 0, id_a="A", id_b="depot", matrix=matrix)
    assert d2 == 12.5


def test_clarke_wright_savings_with_geographic_mode():
    # Depot in Santiago center: lat -33.4489, lon -70.6693
    # Two stops nearby in Santiago
    depot = Depot(x=-70.6693, y=-33.4489)
    stops = [
        Stop("Providencia", x=-70.6100, y=-33.4300, demand=2),
        Stop("LasCondes", x=-70.5800, y=-33.4100, demand=2),
        Stop("Maipu", x=-70.7600, y=-33.5100, demand=3),
    ]

    plan = clarke_wright_savings(
        stops, depot, capacity=5,
        is_geographic=True, circuity_factor=1.30,
    )

    assert plan.method == "savings"
    assert plan.distance_source == "haversine_circuity"
    # Total distance is positive and in kilometers, not tiny degree fractions!
    assert plan.total_distance > 10.0


def test_clarke_wright_savings_with_distance_matrix():
    depot = Depot(0, 0)
    stops = [
        Stop("S1", 0, 0, demand=1),
        Stop("S2", 0, 0, demand=1),
    ]
    # Injected real road matrix
    matrix = {
        ("depot", "S1"): 10.0,
        ("depot", "S2"): 20.0,
        ("S1", "S2"): 5.0,
    }

    plan = clarke_wright_savings(stops, depot, capacity=2, matrix=matrix)
    assert plan.distance_source == "matrix"
    # Consolidating S1 and S2: depot -> S1 -> S2 -> depot = 10 + 5 + 20 = 35.0
    assert plan.total_distance == pytest.approx(35.0)
