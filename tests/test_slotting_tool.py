"""Tests for the slotting (COI + affinity) agent tool.

Wires src.space + src.slotting_affinity into the orchestrator: an order-lines CSV -> COI zone
assignment per SKU + affinity co-location clusters, with ranked re-slotting options on success.
"""

from pathlib import Path

import pandas as pd
import pytest

from jobs import slotting_job as sj
from scm_agent import intent, llm, tools
from scm_agent.orchestrator import Orchestrator
from src.deliverable import Deliverable
from src.guided import OPTIONS


def _orders_df() -> pd.DataFrame:
    # P1 is in every order (fastest mover); P2/P3 twice; P4/P5 once.
    return pd.DataFrame({
        "order_id": ["O1", "O1", "O1", "O2", "O2", "O3", "O3", "O4", "O4", "O5", "O5"],
        "product_id": ["P1", "P2", "P3", "P1", "P2", "P1", "P3", "P1", "P4", "P1", "P5"],
    })


def test_prepare_builds_pick_frequency_and_baskets(tmp_path):
    csv = tmp_path / "orders.csv"
    _orders_df().to_csv(csv, index=False)
    payload = sj.prepare(str(csv), {})
    by = {s["product_id"]: s for s in payload["skus"]}
    assert by["P1"]["pick_frequency"] == 5.0      # in all 5 orders
    assert by["P4"]["pick_frequency"] == 1.0
    assert len(payload["baskets"]) == 5


def test_prepare_errors_without_order_and_product(tmp_path):
    csv = tmp_path / "bad.csv"
    pd.DataFrame({"foo": [1]}).to_csv(csv, index=False)
    with pytest.raises(ValueError, match="order_id|product_id"):
        sj.prepare(str(csv), {})


def test_run_zones_by_coi_and_finds_co_location():
    report = sj.run(sj.prepare_records(_orders_df()))
    assert report.n_skus == 5 and report.n_orders == 5
    assert report.slots[0].product_id == "P1"     # lowest COI (most picks) ranks first
    assert report.slots[0].zone == "A"
    assert report.n_a == 1
    assert len(report.co_location_groups) >= 1     # P1 co-occurs with all -> a cluster forms
    assert sj.verify(report) == []


def test_build_deck_is_ascii_deliverable():
    report = sj.run(sj.prepare_records(_orders_df()))
    deck = sj.build_deck(report, client="Acme", citations=("Kallina & Lynn (1976) COI",), confidence=0.85)
    assert isinstance(deck, Deliverable)
    md = deck.to_markdown()
    assert md.isascii()
    assert "Slotting" in md and "## Coverage & handoff" in md


def test_brief_routes_to_slotting():
    reg = tools.build_default_registry()
    res = intent.classify(
        "slotting: assign each SKU to its slot by cube-per-order index and co-location",
        reg, llm.RulesFallback(),
    )
    assert res.job_type == "slotting"


def test_orchestrator_runs_slotting_with_ranked_options(tmp_path):
    csv = tmp_path / "orders.csv"
    _orders_df().to_csv(csv, index=False)
    orch = Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback())
    res = orch.run("slotting: re-slot SKUs into pick zones by cube-per-order index",
                   data_path=str(csv), client="Acme", out_dir=tmp_path)
    assert res.status == "ok" and res.tool == "slotting"
    assert Path(res.deliverables["deck_report"]).exists()
    assert Path(res.deliverables["csv"]).exists()
    assert res.guided is not None and res.guided.status == OPTIONS
    assert sum(1 for o in res.guided.options if o.recommended) == 1
