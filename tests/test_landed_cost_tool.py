"""Tests for the landed-cost agent job + tool (9th tool).

Reads a shipment CSV into landed-cost inputs (pandas directly, not the parallel loop's
intake.py), computes the Incoterm-aware fully-landed cost per SKU, and the tool wires it
into the orchestrator so "what's our landed cost" produces the landed-cost study deck.
"""

from pathlib import Path

import pandas as pd
import pytest

from jobs import landed_cost_job as lcj
from scm_agent import intent, llm, tools
from scm_agent.orchestrator import Orchestrator
from src.deliverable import Deliverable


def _shipments_df() -> pd.DataFrame:
    return pd.DataFrame({
        "sku": ["SKU-A", "SKU-B"],
        "unit_cost": [10.0, 20.0],
        "qty": [100.0, 50.0],
        "freight": [200.0, 100.0],
        "insurance": [50.0, 0.0],
        "duty_rate": [0.05, 0.10],
        "incoterm": ["FOB", "CIF"],
    })


# -- prepare ------------------------------------------------------------------


def test_prepare_reads_shipment_lines(tmp_path):
    csv = tmp_path / "ship.csv"
    _shipments_df().to_csv(csv, index=False)

    records = lcj.prepare(str(csv), {})

    by = {r["sku"]: r for r in records}
    assert by["SKU-A"]["unit_cost"] == 10.0 and by["SKU-A"]["qty"] == 100.0
    assert by["SKU-B"]["incoterm"] == "CIF"


def test_prepare_errors_without_required_columns(tmp_path):
    csv = tmp_path / "bad.csv"
    pd.DataFrame({"foo": [1]}).to_csv(csv, index=False)

    with pytest.raises(ValueError, match="sku|unit_cost|qty"):
        lcj.prepare(str(csv), {})


# -- run + qa -----------------------------------------------------------------


def test_run_computes_incoterm_aware_landed_cost():
    report = lcj.run(lcj.prepare_records(_shipments_df()))

    by = {ln.sku: ln for ln in report.lines}
    # SKU-A FOB: duty on goods only (1000*0.05=50) -> total 1000+200+50+50 = 1300.
    assert by["SKU-A"].landed.total == pytest.approx(1300.0)
    assert by["SKU-A"].landed.per_unit == pytest.approx(13.0)
    # SKU-B CIF: duty base = goods+freight+insurance (1100*0.10=110).
    assert by["SKU-B"].landed.duty == pytest.approx(110.0)
    assert report.total_landed == pytest.approx(2510.0)
    assert report.landed_uplift_pct == pytest.approx(0.255)   # (2510-2000)/2000
    assert lcj.verify(report) == []


def test_lines_sorted_by_total_landed_cost():
    report = lcj.run(lcj.prepare_records(_shipments_df()))
    assert report.lines[0].sku == "SKU-A"          # 1300 >= 1210


# -- deck ---------------------------------------------------------------------


def test_build_deck_is_an_ascii_deliverable():
    report = lcj.run(lcj.prepare_records(_shipments_df()))

    deck = lcj.build_deck(report, client="Acme", citations=("Ellram - TCO",), confidence=0.85)

    assert isinstance(deck, Deliverable)
    md = deck.to_markdown()
    assert md.isascii()
    assert "Landed" in md and "## Coverage & handoff" in md


# -- routing ------------------------------------------------------------------


def test_brief_routes_to_landed_cost():
    reg = tools.build_default_registry()
    res = intent.classify("compute the landed cost with duty and freight by incoterm",
                          reg, llm.RulesFallback())
    assert res.job_type == "landed_cost"


def test_landed_cost_keywords_do_not_steal_other_briefs():
    reg = tools.build_default_registry()
    p = llm.RulesFallback()
    assert intent.classify("set up reorder points and safety stock", reg, p).job_type == "inventory_optimization"
    assert intent.classify("select the best supplier by OTIF", reg, p).job_type == "sourcing"
    assert intent.classify("size our DDMRP buffers", reg, p).job_type == "ddmrp"


# -- end-to-end ---------------------------------------------------------------


def test_orchestrator_runs_landed_cost_and_emits_the_deck(tmp_path):
    csv = tmp_path / "ship.csv"
    _shipments_df().to_csv(csv, index=False)
    orch = Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback())

    res = orch.run("compute the landed cost (duty + freight + insurance) per SKU",
                   data_path=str(csv), client="Acme", out_dir=tmp_path)

    assert res.status == "ok"
    assert res.tool == "landed_cost"
    assert "csv" in res.deliverables
    deck = Path(res.deliverables["deck_report"])
    assert deck.exists()
