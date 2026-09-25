"""Tests for the slotting (COI + affinity) agent tool.

Wires src.space + src.slotting_affinity into the orchestrator: an order-lines CSV -> COI zone
assignment per SKU + affinity co-location clusters, with ranked re-slotting options on success.
"""

from pathlib import Path

import pandas as pd
import pytest

from jobs import slotting_job as sj
from nosilo_core import llm
from nosilo_core.guided import OPTIONS
from scm_agent import intent, tools
from scm_agent.orchestrator import Orchestrator
from src.deliverable import Deliverable


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


# ---- warehouse utilization: only from a real stored cube ---------------------
# A per-unit volume is not a stored cube. Summing one unit of each SKU against the
# building's volume used to report ~0% utilization as if it had been measured.

def _with(**cols) -> pd.DataFrame:
    df = _orders_df()
    for name, by_sku in cols.items():
        df[name] = df["product_id"].map(by_sku)
    return df


_UNIT = {"P1": 0.02, "P2": 0.05, "P3": 0.10, "P4": 0.04, "P5": 0.01}
_ON_HAND = {"P1": 500, "P2": 200, "P3": 100, "P4": 50, "P5": 1000}


def test_unit_volume_times_on_hand_is_the_stored_cube():
    report = sj.run(sj.prepare_records(_with(unit_volume=_UNIT, on_hand=_ON_HAND), {"warehouse_volume": 100}))
    stored = sum(_UNIT[s] * _ON_HAND[s] for s in _UNIT)          # 10 + 10 + 10 + 2 + 10 = 42 m3
    assert report.total_required_space == pytest.approx(stored)
    assert report.utilization == pytest.approx(stored / 100)
    assert report.utilization_note == ""
    assert {s.product_id: s.required_space for s in report.slots}["P3"] == pytest.approx(10.0)
    assert sj.verify(report) == []


def test_unit_volume_alone_does_not_report_utilization():
    report = sj.run(sj.prepare_records(_with(unit_volume=_UNIT), {"warehouse_volume": 1000}))
    assert report.utilization is None
    assert "unit_volume" in report.utilization_note and "on_hand" in report.utilization_note
    assert "not computed" in report.summary
    # COI still ranks by the unit volume (the pre-existing proxy): P1 is densest per pick.
    assert report.slots[0].product_id == "P1"
    assert sj.verify(report) == []


def test_no_volume_column_does_not_report_utilization():
    # A uniform 1.0 per SKU ranks COI by pick frequency; divided by the building it
    # would be "SKU count / m3", which is not a utilization.
    report = sj.run(sj.prepare_records(_orders_df(), {"warehouse_volume": 1000}))
    assert report.utilization is None
    assert "no volume column" in report.utilization_note


def test_required_space_is_taken_as_the_total_cube():
    report = sj.run(sj.prepare_records(_with(required_space=_ON_HAND), {"warehouse_volume": 5000}))
    assert report.utilization == pytest.approx(sum(_ON_HAND.values()) / 5000)


def test_partial_on_hand_withholds_utilization():
    on_hand = {k: v for k, v in _ON_HAND.items() if k != "P5"}
    report = sj.run(sj.prepare_records(_with(unit_volume=_UNIT, on_hand=on_hand), {"warehouse_volume": 100}))
    assert report.utilization is None
    assert "1 of 5" in report.utilization_note


def test_negative_on_hand_is_zero_cube_not_negative_space():
    on_hand = dict(_ON_HAND, P4=-30)
    report = sj.run(sj.prepare_records(_with(unit_volume=_UNIT, on_hand=on_hand), {"warehouse_volume": 100}))
    assert {s.product_id: s.required_space for s in report.slots}["P4"] == 0.0
    assert report.utilization == pytest.approx(40 / 100)


def test_deck_says_utilization_was_not_measured():
    report = sj.run(sj.prepare_records(_with(unit_volume=_UNIT), {"warehouse_volume": 1000}))
    md = sj.build_deck(report, client="Acme").to_markdown()
    assert md.isascii()
    assert "Space utilization not measured" in md
    assert "of the usable warehouse volume" not in md      # no measured-utilization finding
