"""Tests for vehicle_routing_job distance matrix, geodesic calculation, and engine wiring."""

import pandas as pd

from jobs import vehicle_routing_job as vrj
from src.logistics.routing import haversine_distance, pair_distance


def _stops_df() -> pd.DataFrame:
    return pd.DataFrame({
        "stop_id": ["A", "B", "C"],
        "x": [3.0, 6.0, 9.0],
        "y": [4.0, 8.0, 12.0],
        "demand": [1.0, 1.0, 1.0],
    })


def _lat_lon_stops_df() -> pd.DataFrame:
    # Santiago center coordinates
    return pd.DataFrame({
        "stop_id": ["Providencia", "LasCondes", "Maipu"],
        "lon": [-70.6100, -70.5800, -70.7600],
        "lat": [-33.4300, -33.4100, -33.5100],
        "demand": [2.0, 2.0, 3.0],
    })


def test_routing_job_with_explicit_distance_matrix():
    # Injected real road matrix:
    matrix = {
        ("depot", "A"): 10.0,
        ("depot", "B"): 20.0,
        ("depot", "C"): 30.0,
        ("A", "B"): 5.0,
        ("B", "C"): 5.0,
        ("A", "C"): 12.0,
    }
    payload = vrj.prepare_records(_stops_df(), {"capacity": 3, "distance_matrix": matrix})
    assert payload["matrix"] is not None
    assert payload["matrix"][("depot", "A")] == 10.0

    report = vrj.run(payload)
    assert report.distance_matrix is not None
    assert report.distance_source == "matrix"
    assert report.recommended_distance > 0
    assert vrj.verify(report) == []


def test_routing_job_with_csv_matrix(tmp_path):
    matrix_csv = tmp_path / "distances.csv"
    matrix_df = pd.DataFrame([
        {"origin": "depot", "dest": "A", "distance": 15.0},
        {"origin": "depot", "dest": "B", "distance": 25.0},
        {"origin": "depot", "dest": "C", "distance": 35.0},
        {"origin": "A", "dest": "B", "distance": 6.0},
        {"origin": "B", "dest": "C", "distance": 7.0},
        {"origin": "A", "dest": "C", "distance": 14.0},
    ])
    matrix_df.to_csv(matrix_csv, index=False)

    payload = vrj.prepare_records(_stops_df(), {"capacity": 3, "matrix_path": str(matrix_csv)})
    assert payload["matrix"] is not None
    assert payload["matrix"][("depot", "A")] == 15.0
    report = vrj.run(payload)
    assert report.distance_source == "matrix"


def test_routing_job_geodesic_with_lat_lon():
    df = _lat_lon_stops_df()
    # Depot at Santiago center
    payload = vrj.prepare_records(
        df,
        {"capacity": 5, "depot_x": -70.6693, "depot_y": -33.4489, "circuity_factor": 1.30},
    )
    assert payload["is_geographic"] is True
    assert payload["matrix"] is not None
    # Depot to Providencia should be ~6-10 km road distance
    d_depot_prov = payload["matrix"][("depot", "Providencia")]
    assert 5.0 < d_depot_prov < 15.0

    report = vrj.run(payload)
    assert report.distance_source == "matrix"
    assert report.recommended_distance > 10.0
    assert vrj.verify(report) == []


def test_routing_job_pair_and_haversine_exports():
    assert callable(haversine_distance)
    assert callable(pair_distance)
    d = haversine_distance(0, 0, 0, 1)
    assert d > 100.0  # 1 degree of longitude at equator is ~111 km
