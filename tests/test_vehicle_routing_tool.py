"""Tests for the vehicle-routing (Ballou Cap. 7) agent tool.

Wires src.logistics.routing into the orchestrator: a stops CSV + a depot/capacity ->
the better of the savings and sweep route plans, with ranked options on success.
"""

from dataclasses import replace

import pandas as pd
import pytest

from jobs import vehicle_routing_job as vrj
from scm_agent import intent, llm, tools
from src.deliverable import Deliverable


def _stops_df() -> pd.DataFrame:
    return pd.DataFrame({
        "stop_id": ["A", "B", "C", "D"],
        "x": [3, 6, -3, -6],
        "y": [4, 8, 4, 8],
        "demand": [2, 2, 2, 2],
    })


def test_prepare_reads_stops_and_depot_params(tmp_path):
    csv = tmp_path / "stops.csv"
    _stops_df().to_csv(csv, index=False)
    payload = vrj.prepare(str(csv), {"capacity": 4})
    assert len(payload["stops"]) == 4
    assert payload["depot"].x == 0.0 and payload["depot"].y == 0.0
    assert payload["capacity"] == 4.0


def test_prepare_errors_without_coordinates(tmp_path):
    csv = tmp_path / "bad.csv"
    pd.DataFrame({"foo": [1]}).to_csv(csv, index=False)
    with pytest.raises(ValueError, match="x|y"):
        vrj.prepare(str(csv), {"capacity": 4})


def test_prepare_errors_without_capacity(tmp_path):
    csv = tmp_path / "stops.csv"
    _stops_df().to_csv(csv, index=False)
    with pytest.raises(ValueError, match="capacity"):
        vrj.prepare(str(csv), {})


def test_prepare_errors_on_duplicate_stop_ids(tmp_path):
    df = _stops_df()
    df.loc[1, "stop_id"] = "A"  # B now collides with A
    csv = tmp_path / "stops.csv"
    df.to_csv(csv, index=False)
    with pytest.raises(ValueError, match="duplicate"):
        vrj.prepare(str(csv), {"capacity": 4})


def test_verify_flags_a_stop_missing_from_every_route():
    report = vrj.run(vrj.prepare_records(_stops_df(), {"capacity": 4}))
    dropped_routes = tuple(
        replace(r, stop_ids=r.stop_ids[:-1]) if r.stop_ids else r
        for r in report.recommended_routes
    )
    broken = replace(report, recommended_routes=dropped_routes)
    issues = vrj.verify(broken)
    assert any("expected" in i and "distinct stop id" in i for i in issues)


def test_run_picks_the_better_method_and_beats_naive():
    report = vrj.run(vrj.prepare_records(_stops_df(), {"capacity": 4}))
    assert report.n_stops == 4
    assert report.recommended_method in {"savings", "sweep"}
    assert report.recommended_distance <= report.naive_total_distance
    assert report.savings_vs_naive >= 0
    covered = {sid for r in report.recommended_routes for sid in r.stop_ids}
    assert covered == {"A", "B", "C", "D"}
    assert vrj.verify(report) == []


def test_run_flags_late_stops_when_time_windows_are_tight():
    df = _stops_df()
    df["tw_end"] = [0.1, 0.1, 0.1, 0.1]
    report = vrj.run(vrj.prepare_records(df, {"capacity": 4}))
    assert len(report.late_stops) > 0


def test_build_deck_is_ascii_deliverable():
    report = vrj.run(vrj.prepare_records(_stops_df(), {"capacity": 4}))
    deck = vrj.build_deck(report, client="Acme", citations=("Ballou Cap. 7",), confidence=0.85)
    assert isinstance(deck, Deliverable)
    md = deck.to_markdown()
    assert md.isascii()
    assert "Vehicle Routing" in md and "## Coverage & handoff" in md


def test_brief_routes_to_vehicle_routing():
    reg = tools.build_default_registry()
    res = intent.classify(
        "vehicle routing: design delivery routes with the savings algorithm for our trucks",
        reg, llm.RulesFallback(),
    )
    assert res.job_type == "vehicle_routing"


def test_registered_in_default_registry():
    reg = tools.build_default_registry()
    tool = reg.get("vehicle_routing")
    assert tool.requires_data is True
    assert tool.options is not None
