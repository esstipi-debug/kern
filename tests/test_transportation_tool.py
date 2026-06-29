"""Tests for the transportation (freight mode selection) agent tool.

Wires src.logistics into the orchestrator: a shipments CSV -> cheapest feasible mode per
shipment (parcel / LTL / FTL / intermodal), the saving vs all-LTL, and lane freight
cost-to-serve, with ranked freight-strategy options on success.
"""

from pathlib import Path

import pandas as pd
import pytest

from jobs import transportation_job as tj
from scm_agent import intent, llm, tools
from scm_agent.orchestrator import Orchestrator
from src.deliverable import Deliverable
from src.guided import OPTIONS


def _shipments_df() -> pd.DataFrame:
    return pd.DataFrame({
        "shipment_id": ["small", "mid", "heavy"],
        "lane": ["A->B", "A->B", "A->C"],
        "weight_kg": [10, 500, 15000],
        "distance_km": [200, 300, 1000],
        "units": [5, 50, 1000],
        "order_value": [400, 2000, 30000],
    })


def test_prepare_reads_shipments_and_rates(tmp_path):
    csv = tmp_path / "ship.csv"
    _shipments_df().to_csv(csv, index=False)
    payload = tj.prepare(str(csv), {"ftl_cost_per_km": 2.0})
    assert len(payload["shipments"]) == 3
    assert payload["rates"].ftl_cost_per_km == 2.0          # param overrides the rate card
    ship0, lane0 = payload["shipments"][0]
    assert ship0.shipment_id == "small" and lane0 == "A->B"


def test_prepare_errors_without_weight_distance(tmp_path):
    csv = tmp_path / "bad.csv"
    pd.DataFrame({"foo": [1]}).to_csv(csv, index=False)
    with pytest.raises(ValueError, match="weight_kg|distance_km"):
        tj.prepare(str(csv), {})


def test_run_picks_modes_and_saves_vs_ltl():
    report = tj.run(tj.prepare_records(_shipments_df()))
    by = {p.shipment_id: p for p in report.plans}
    assert report.n_shipments == 3
    assert by["small"].recommended_mode == "parcel"
    assert by["mid"].recommended_mode == "ltl"
    assert by["heavy"].recommended_mode == "ftl"
    assert report.total_savings > 0                          # parcel + FTL beat all-LTL
    assert report.breakeven_kg == 2000.0
    assert tj.verify(report) == []


def test_build_deck_is_ascii_deliverable():
    report = tj.run(tj.prepare_records(_shipments_df()))
    deck = tj.build_deck(report, client="Acme", citations=("Christopher (2016) Logistics & SCM",), confidence=0.85)
    assert isinstance(deck, Deliverable)
    md = deck.to_markdown()
    assert md.isascii()
    assert "Transport-Mode" in md and "## Coverage & handoff" in md


def test_brief_routes_to_transportation():
    reg = tools.build_default_registry()
    res = intent.classify(
        "which transportation mode is cheapest - parcel, LTL, FTL or intermodal - for these shipments",
        reg, llm.RulesFallback(),
    )
    assert res.job_type == "transportation"


def test_orchestrator_runs_transportation_with_ranked_options(tmp_path):
    csv = tmp_path / "ship.csv"
    _shipments_df().to_csv(csv, index=False)
    orch = Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback())
    res = orch.run("pick the cheapest freight mode (ltl vs ftl vs intermodal) per shipment",
                   data_path=str(csv), client="Acme", out_dir=tmp_path)
    assert res.status == "ok" and res.tool == "transportation"
    assert Path(res.deliverables["deck_report"]).exists()
    assert Path(res.deliverables["csv"]).exists()
    assert res.guided is not None and res.guided.status == OPTIONS
    assert sum(1 for o in res.guided.options if o.recommended) == 1
