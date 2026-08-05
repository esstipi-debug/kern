"""Tests for the DDMRP agent job + tool (8th tool).

Reads a parts/buffer CSV into the DDMRP inputs (pandas directly, not the parallel loop's
intake.py), sizes the red/yellow/green buffers + net-flow planning signals, and the tool
wires it into the orchestrator so "size our DDMRP buffers" produces the buffer plan deck.
"""

from pathlib import Path

import pandas as pd
import pytest

from jobs import ddmrp_job as ddj
from nosilo_core import llm
from scm_agent import intent, tools
from scm_agent.orchestrator import Orchestrator
from src.deliverable import Deliverable


def _parts_df() -> pd.DataFrame:
    return pd.DataFrame({
        "part_id": ["P1", "P2"],
        "adu": [10.0, 5.0],
        "dlt": [5.0, 10.0],
        "ltf": [0.5, 0.5],
        "vf": [0.4, 0.3],
        "on_hand": [20.0, 200.0],
        "on_order": [0.0, 0.0],
        "qualified_demand": [30.0, 10.0],
    })


# -- prepare ------------------------------------------------------------------


def test_prepare_reads_the_parts_into_buffer_inputs(tmp_path):
    csv = tmp_path / "parts.csv"
    _parts_df().to_csv(csv, index=False)

    records = ddj.prepare(str(csv), {})

    by = {r["part_id"]: r for r in records}
    assert by["P1"]["adu"] == 10.0 and by["P1"]["dlt"] == 5.0
    assert by["P1"]["qualified_demand"] == 30.0


def test_prepare_errors_without_required_columns(tmp_path):
    csv = tmp_path / "bad.csv"
    pd.DataFrame({"foo": [1]}).to_csv(csv, index=False)

    with pytest.raises(ValueError, match="part_id|adu|dlt"):
        ddj.prepare(str(csv), {})


# -- run + qa -----------------------------------------------------------------


def test_run_sizes_buffers_and_flags_the_red_part():
    records = ddj.prepare_records(_parts_df())

    report = ddj.run(records)

    by = {p.part_id: p for p in report.parts}
    # P1: yellow 50, red 35, green 25 -> TOG 110; NFP 20-30 = -10 -> red, order to TOG.
    assert by["P1"].zones.tog == pytest.approx(110.0)
    assert by["P1"].signal.zone == "red"
    assert by["P1"].signal.order_recommended is True
    assert by["P1"].signal.order_qty == pytest.approx(120.0)
    # P2 is over green -> no order.
    assert by["P2"].signal.zone == "over_green"
    assert report.n_red == 1 and report.n_order == 1
    assert report.total_order_qty == pytest.approx(120.0)
    assert ddj.verify(report) == []


def test_parts_are_sorted_most_urgent_first():
    report = ddj.run(ddj.prepare_records(_parts_df()))
    assert report.parts[0].part_id == "P1"          # lowest priority ratio = most urgent


# -- deck ---------------------------------------------------------------------


def test_build_deck_is_an_ascii_deliverable():
    report = ddj.run(ddj.prepare_records(_parts_df()))

    deck = ddj.build_deck(report, client="Acme", citations=("Ptak & Smith - DDMRP",), confidence=0.85)

    assert isinstance(deck, Deliverable)
    md = deck.to_markdown()
    assert md.isascii()
    assert "DDMRP" in md or "buffer" in md.lower()
    assert "## Coverage & handoff" in md


# -- routing ------------------------------------------------------------------


def test_brief_routes_to_ddmrp():
    reg = tools.build_default_registry()
    res = intent.classify("size our DDMRP buffers and net flow plan", reg, llm.RulesFallback())
    assert res.job_type == "ddmrp"


def test_ddmrp_keywords_do_not_steal_other_briefs():
    reg = tools.build_default_registry()
    p = llm.RulesFallback()
    assert intent.classify("set up reorder points and safety stock", reg, p).job_type == "inventory_optimization"
    assert intent.classify("build the monthly S&OP plan", reg, p).job_type == "sop"
    assert intent.classify("select the best supplier by OTIF", reg, p).job_type == "sourcing"


# -- end-to-end ---------------------------------------------------------------


def test_orchestrator_runs_ddmrp_and_emits_the_deck(tmp_path):
    csv = tmp_path / "parts.csv"
    _parts_df().to_csv(csv, index=False)
    orch = Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback())

    res = orch.run("size the DDMRP buffer zones and net flow", data_path=str(csv),
                   client="Acme", out_dir=tmp_path)

    assert res.status == "ok"
    assert res.tool == "ddmrp"
    assert "csv" in res.deliverables
    deck = Path(res.deliverables["deck_report"])
    assert deck.exists()
