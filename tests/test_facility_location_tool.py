"""Tests for the facility-location (network design) agent tool.

Wires src.facility_location into the orchestrator: a demand-points CSV -> center of gravity +
Weiszfeld optimum + saving vs the current site, with ranked siting options on success.
"""

from pathlib import Path

import pandas as pd
import pytest

from jobs import facility_location_job as flj
from scm_agent import intent, llm, tools
from scm_agent.orchestrator import Orchestrator
from src.deliverable import Deliverable
from src.guided import OPTIONS


def _points_df() -> pd.DataFrame:
    return pd.DataFrame({
        "name": ["N", "S", "E", "W"],
        "x": [0, 0, 10, -10],
        "y": [10, -10, 0, 0],
        "weight": [1, 1, 5, 1],
    })


def test_prepare_reads_points(tmp_path):
    csv = tmp_path / "pts.csv"
    _points_df().to_csv(csv, index=False)
    payload = flj.prepare(str(csv), {})
    assert len(payload["points"]) == 4
    assert payload["current"] is None


def test_prepare_errors_without_coordinates(tmp_path):
    csv = tmp_path / "bad.csv"
    pd.DataFrame({"foo": [1]}).to_csv(csv, index=False)
    with pytest.raises(ValueError, match="x|y"):
        flj.prepare(str(csv), {})


def test_run_locates_toward_the_heavy_node():
    report = flj.run(flj.prepare_records(_points_df()))
    assert report.n_points == 4
    assert report.optimum.x > 0                  # pulled east toward the weight-5 node
    assert report.optimum_distance >= 0
    assert report.nearest_point in {"N", "S", "E", "W"}
    assert flj.verify(report) == []


def test_run_reports_saving_vs_current_site():
    report = flj.run(flj.prepare_records(_points_df(), {"current_x": -50, "current_y": -50}))
    assert report.current is not None
    assert report.saving_vs_current is not None and report.saving_vs_current > 0


def test_build_deck_is_ascii_deliverable():
    report = flj.run(flj.prepare_records(_points_df()))
    deck = flj.build_deck(report, client="Acme", citations=("Ballou network design",), confidence=0.85)
    assert isinstance(deck, Deliverable)
    md = deck.to_markdown()
    assert md.isascii()
    assert "Facility Location" in md and "## Coverage & handoff" in md


def test_brief_routes_to_facility_location():
    reg = tools.build_default_registry()
    res = intent.classify(
        "facility location / network design: center of gravity for the optimal DC location",
        reg, llm.RulesFallback(),
    )
    assert res.job_type == "facility_location"


def test_orchestrator_runs_facility_location_with_ranked_options(tmp_path):
    csv = tmp_path / "pts.csv"
    _points_df().to_csv(csv, index=False)
    orch = Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback())
    res = orch.run("facility location by center of gravity: where to locate the DC",
                   data_path=str(csv), client="Acme", out_dir=tmp_path)
    assert res.status == "ok" and res.tool == "facility_location"
    assert Path(res.deliverables["deck_report"]).exists()
    assert Path(res.deliverables["csv"]).exists()
    assert res.guided is not None and res.guided.status == OPTIONS
    assert sum(1 for o in res.guided.options if o.recommended) == 1
