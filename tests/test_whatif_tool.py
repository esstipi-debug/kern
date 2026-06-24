"""Tests for the what-if agent job + tool (10th tool).

Reads a drivers CSV (driver, base, low, high) into a sensitivity sweep over the inventory
policy cost model (EOQ + safety stock), and the tool wires it into the orchestrator so
"run a what-if sensitivity analysis" produces the tornado + break-even study deck.
"""

from pathlib import Path

import pandas as pd
import pytest

from jobs import whatif_job as wj
from scm_agent import intent, llm, tools
from scm_agent.orchestrator import Orchestrator
from src.deliverable import Deliverable


def _drivers_df() -> pd.DataFrame:
    return pd.DataFrame({
        "driver": ["annual_demand", "holding_cost"],
        "base": [12_000.0, 3.0],
        "low": [9_000.0, 2.0],
        "high": [15_000.0, 4.5],
        "unit": ["u/yr", "$/u/yr"],
    })


# -- prepare ------------------------------------------------------------------


def test_prepare_reads_drivers_and_carries_model_defaults(tmp_path):
    csv = tmp_path / "drivers.csv"
    _drivers_df().to_csv(csv, index=False)

    payload = wj.prepare(str(csv), {})

    assert [d.name for d in payload["drivers"]] == ["annual_demand", "holding_cost"]
    assert payload["base_inputs"]["annual_demand"] == 12_000.0
    assert payload["base_inputs"]["fixed_order_cost"] == 75.0  # unlisted input -> default


def test_prepare_defaults_base_when_absent(tmp_path):
    csv = tmp_path / "nobase.csv"
    pd.DataFrame({"driver": ["holding_cost"], "low": [2.0], "high": [4.0]}).to_csv(csv, index=False)

    payload = wj.prepare(str(csv), {})

    assert payload["drivers"][0].base == pytest.approx(3.0)  # _DEFAULTS["holding_cost"]


def test_prepare_errors_without_band_columns(tmp_path):
    csv = tmp_path / "bad.csv"
    pd.DataFrame({"driver": ["annual_demand"], "base": [12_000.0]}).to_csv(csv, index=False)

    with pytest.raises(ValueError, match="low|high|band"):
        wj.prepare(str(csv), {})


def test_prepare_rejects_unknown_driver(tmp_path):
    csv = tmp_path / "unk.csv"
    pd.DataFrame({"driver": ["wibble"], "low": [1.0], "high": [2.0]}).to_csv(csv, index=False)

    with pytest.raises(ValueError, match="wibble|unknown|valid"):
        wj.prepare(str(csv), {})


# -- run + qa -----------------------------------------------------------------


def test_run_ranks_drivers_and_brackets_the_corners():
    report = wj.run(wj.prepare_records(_drivers_df()), metric="annual_cost", budget_pct=0.10)

    assert report.base_value == pytest.approx(2602.9, rel=1e-3)
    assert report.rows[0].driver == "holding_cost"  # widest swing
    assert report.optimistic_value < report.base_value < report.pessimistic_value
    assert report.top_driver == "holding_cost"
    assert wj.verify(report) == []


def test_run_finds_break_even_on_the_top_driver():
    report = wj.run(wj.prepare_records(_drivers_df()), metric="annual_cost", budget_pct=0.10)

    assert report.breakeven_found
    assert 2.0 < report.breakeven_value < 4.5
    hit = wj.policy_model({**_BASE_FOR_TEST, "holding_cost": report.breakeven_value})
    assert hit["annual_cost"] == pytest.approx(report.breakeven_target, rel=1e-3)


_BASE_FOR_TEST = {
    "annual_demand": 12_000.0,
    "holding_cost": 3.0,
    "fixed_order_cost": 75.0,
    "demand_std": 40.0,
    "service_level": 0.95,
    "lead_time": 2.0,
}


# -- deck ---------------------------------------------------------------------


def test_build_deck_is_an_ascii_deliverable():
    report = wj.run(wj.prepare_records(_drivers_df()))

    deck = wj.build_deck(report, client="Acme", citations=("Vandeput - sensitivity",), confidence=0.8)

    assert isinstance(deck, Deliverable)
    md = deck.to_markdown()
    assert md.isascii()
    assert "Sensitivity" in md and "## Coverage & handoff" in md


# -- routing ------------------------------------------------------------------


def test_brief_routes_to_whatif():
    reg = tools.build_default_registry()
    res = intent.classify(
        "run a what-if sensitivity analysis: tornado of the drivers and the break-even point",
        reg, llm.RulesFallback(),
    )
    assert res.job_type == "whatif"


def test_whatif_keywords_do_not_steal_other_briefs():
    reg = tools.build_default_registry()
    p = llm.RulesFallback()
    assert intent.classify("set up reorder points and safety stock", reg, p).job_type == "inventory_optimization"
    assert intent.classify("compute the landed cost with duty and freight", reg, p).job_type == "landed_cost"
    assert intent.classify("size our DDMRP buffers", reg, p).job_type == "ddmrp"


# -- end-to-end ---------------------------------------------------------------


def test_orchestrator_runs_whatif_and_emits_the_deck(tmp_path):
    csv = tmp_path / "drivers.csv"
    _drivers_df().to_csv(csv, index=False)
    orch = Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback())

    res = orch.run(
        "what-if sensitivity analysis of the inventory policy cost (tornado + break-even)",
        data_path=str(csv), client="Acme", out_dir=tmp_path,
    )

    assert res.status == "ok"
    assert res.tool == "whatif"
    assert "csv" in res.deliverables
    deck = Path(res.deliverables["deck_report"])
    assert deck.exists()
