"""Tests for the queuing / staffing agent tool (15th tool).

Wires src.queuing into the orchestrator: a stations CSV -> cost-optimal server count per
service point + the wait/labour trade-off, with ranked staffing options on success.
"""

from pathlib import Path

import pandas as pd

from jobs import queuing_job as qj
from scm_agent import intent, llm, tools
from scm_agent.orchestrator import Orchestrator
from src.deliverable import Deliverable
from src.guided import OPTIONS


def _stations_df() -> pd.DataFrame:
    return pd.DataFrame({
        "station": ["Dock", "Pick"],
        "arrival_rate": [2.0, 5.0],
        "service_rate": [3.0, 2.0],
        "wait_cost": [10.0, 8.0],
        "server_cost": [5.0, 4.0],
    })


def test_prepare_reads_stations(tmp_path):
    csv = tmp_path / "stations.csv"
    _stations_df().to_csv(csv, index=False)
    records = qj.prepare(str(csv), {})
    by = {r["station"]: r for r in records}
    assert by["Dock"]["arrival_rate"] == 2.0 and by["Pick"]["service_rate"] == 2.0


def test_prepare_errors_without_required_columns(tmp_path):
    csv = tmp_path / "bad.csv"
    pd.DataFrame({"foo": [1]}).to_csv(csv, index=False)
    import pytest
    with pytest.raises(ValueError, match="station|arrival|service"):
        qj.prepare(str(csv), {})


def test_run_sizes_each_station_cost_optimally():
    report = qj.run(qj.prepare_records(_stations_df()))
    by = {p.station: p for p in report.stations}
    assert report.n_stations == 2
    assert by["Dock"].recommended_servers == 2          # c=1 -> 25, c=2 -> 17.5
    assert all(0.0 < p.utilization < 1.0 for p in report.stations)
    assert report.busiest_station == "Pick"             # heavier load
    assert qj.verify(report) == []


def test_build_deck_is_ascii_deliverable():
    report = qj.run(qj.prepare_records(_stations_df()))
    deck = qj.build_deck(report, client="Acme", citations=("Jacobs & Chase ch.10",), confidence=0.85)
    assert isinstance(deck, Deliverable)
    md = deck.to_markdown()
    assert md.isascii()
    assert "Staffing" in md and "## Coverage & handoff" in md


def test_brief_routes_to_queuing():
    reg = tools.build_default_registry()
    res = intent.classify("how many servers do we need to cut the waiting line at the service desk",
                          reg, llm.RulesFallback())
    assert res.job_type == "queuing"


def test_orchestrator_runs_queuing_with_ranked_options(tmp_path):
    csv = tmp_path / "stations.csv"
    _stations_df().to_csv(csv, index=False)
    orch = Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback())
    res = orch.run("size staffing for the waiting lines / queues", data_path=str(csv), client="Acme", out_dir=tmp_path)
    assert res.status == "ok" and res.tool == "queuing"
    assert Path(res.deliverables["deck_report"]).exists()
    assert res.guided is not None and res.guided.status == OPTIONS
    assert sum(1 for o in res.guided.options if o.recommended) == 1
