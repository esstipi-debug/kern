"""Tests for the returns / reverse-logistics agent job + tool (13th tool).

Reads a returns CSV, ranks each lot's disposition (restock/refurbish/liquidate/scrap), and -
crucially - emits a protected GuidedOutcome with **ranked, executable recovery strategies**.
The tool wires it so a "reverse logistics" brief produces both the study deck AND a set of
>=2 ranked options to act (with a recommended default), surfaced on JobResult.guided.
"""

from pathlib import Path

import pandas as pd
import pytest

from jobs import returns_job as rj
from scm_agent import intent, llm, tools
from scm_agent.orchestrator import Orchestrator
from src.deliverable import Deliverable
from src.guided import OPTIONS, passed_guided


def _returns_df() -> pd.DataFrame:
    return pd.DataFrame({
        "product_id": ["SKU-A", "SKU-B", "SKU-C"],
        "returned_units": [10.0, 5.0, 8.0],
        "reason": ["wrong_size", "damaged", "defective"],
        "unit_cost": [50.0, 50.0, 30.0],
        "resale_value": [40.0, 40.0, 25.0],
        "sellable": [True, False, False],
    })


# -- prepare ------------------------------------------------------------------


def test_prepare_reads_return_lots(tmp_path):
    csv = tmp_path / "returns.csv"
    _returns_df().to_csv(csv, index=False)

    lines = rj.prepare(str(csv), {})

    by = {ln.product_id: ln for ln in lines}
    assert by["SKU-A"].sellable is True and by["SKU-B"].sellable is False
    assert by["SKU-C"].reason == "defective"


def test_prepare_errors_without_required_columns(tmp_path):
    csv = tmp_path / "bad.csv"
    pd.DataFrame({"foo": [1]}).to_csv(csv, index=False)

    with pytest.raises(ValueError, match="product|returned_units|unit_cost"):
        rj.prepare(str(csv), {})


# -- run + qa: the core "ranked options to act" guarantee ---------------------


def test_run_emits_ranked_executable_options_with_one_recommended():
    report = rj.run(rj.prepare_records(_returns_df()))

    assert report.outcome.status == OPTIONS
    assert len(report.outcome.options) == 3                      # >=2 ways to act
    assert sum(1 for o in report.outcome.options if o.recommended) == 1
    assert passed_guided(report.outcome)                         # never a dead end
    assert report.recommended_strategy in {"recovery_max", "liquidate_all", "restock_or_scrap"}
    assert rj.verify(report) == []


def test_run_rolls_up_recovery_and_reason_pareto():
    report = rj.run(rj.prepare_records(_returns_df()))

    # best routes: A restock 40*10=400, B liquidate 10*5=50, C liquidate 6*8=48 -> 498
    assert report.recovered_value == pytest.approx(498.0)
    assert report.returns_value_at_cost == pytest.approx(990.0)  # 500 + 250 + 240
    assert report.recovery_rate == pytest.approx(498.0 / 990.0)
    assert report.top_reason == "wrong_size"                     # 10 units, the biggest driver
    assert report.dispositions[0].line.product_id == "SKU-A"     # highest recovery value
    assert report.dispositions[0].best.action == "restock"


# -- deck ---------------------------------------------------------------------


def test_build_deck_is_an_ascii_deliverable_listing_the_options():
    report = rj.run(rj.prepare_records(_returns_df()))

    deck = rj.build_deck(report, client="Acme", citations=("Grant - reverse logistics",), confidence=0.8)

    assert isinstance(deck, Deliverable)
    md = deck.to_markdown()
    assert md.isascii()
    assert "reverse" in md.lower() and "## Coverage & handoff" in md
    assert "recommended" in md.lower()


# -- routing ------------------------------------------------------------------


def test_brief_routes_to_returns():
    reg = tools.build_default_registry()
    res = intent.classify("analyze our product returns and recommend the disposition (reverse logistics)",
                          reg, llm.RulesFallback())
    assert res.job_type == "returns"


def test_returns_keywords_do_not_steal_other_briefs():
    reg = tools.build_default_registry()
    p = llm.RulesFallback()
    assert intent.classify("set up reorder points and safety stock", reg, p).job_type == "inventory_optimization"
    assert intent.classify("run a what-if sensitivity tornado", reg, p).job_type == "whatif"
    assert intent.classify("report inventory turns and GMROI", reg, p).job_type == "financial_kpis"


# -- end-to-end: ranked options reach the caller on SUCCESS -------------------


def test_orchestrator_returns_ranked_options_on_success(tmp_path):
    csv = tmp_path / "returns.csv"
    _returns_df().to_csv(csv, index=False)
    orch = Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback())

    res = orch.run("analyze product returns and rank the reverse-logistics disposition options",
                   data_path=str(csv), client="Acme", out_dir=tmp_path)

    assert res.status == "ok"
    assert res.tool == "returns"
    assert "csv" in res.deliverables
    assert Path(res.deliverables["deck_report"]).exists()
    # the whole point: a successful run still hands back >=2 ranked options to act
    assert res.guided is not None
    assert res.guided.status == OPTIONS
    assert len(res.guided.options) >= 2
    assert sum(1 for o in res.guided.options if o.recommended) == 1


def test_a_non_returns_tool_also_surfaces_ranked_options_on_success(tmp_path):
    # the directive, end-to-end: every tool (not just returns) now hands back ranked options.
    csv = tmp_path / "counts.csv"
    pd.DataFrame({
        "product_id": ["A", "B"], "system_qty": [100.0, 50.0],
        "physical_qty": [100.0, 48.0], "unit_cost": [5.0, 2.0],
    }).to_csv(csv, index=False)
    orch = Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback())

    res = orch.run("reconcile the physical count and report inventory record accuracy",
                   data_path=str(csv), client="Acme", out_dir=tmp_path)

    assert res.tool == "reconciliation"
    assert res.guided is not None and res.guided.status == OPTIONS
    assert len(res.guided.options) >= 2
    assert sum(1 for o in res.guided.options if o.recommended) == 1


def test_executed_fallback_when_a_tool_has_no_options_hook():
    # backward-compat: a result with no tool-supplied options still leaves protected (EXECUTED).
    from scm_agent.guided_bridge import to_guided_outcome
    from scm_agent.types import JobResult
    res = JobResult(status="ok", tool="x", confidence=1.0, deliverables={}, summary="done")
    assert to_guided_outcome(res).status == "executed"
