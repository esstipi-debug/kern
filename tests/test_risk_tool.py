"""Tests for the supply-chain risk agent job + tool (14th tool).

Reads a risk register CSV, scores it (likelihood x impact -> EMV / FMEA RPN / 5x5 heatmap),
flags TTR>TTS resilience gaps, and - crucially - emits a protected GuidedOutcome with
**ranked, executable mitigation options** for the top risk. The tool wires it so a
"supply chain risk" brief produces both the study deck AND a set of >=2 ranked options to
act (with a recommended default), surfaced on JobResult.guided.
"""

from pathlib import Path

import pandas as pd
import pytest

from jobs import risk_job as rj
from scm_agent import intent, llm, tools
from scm_agent.orchestrator import Orchestrator
from src.deliverable import Deliverable
from src.guided import OPTIONS, passed_guided


def _risk_df() -> pd.DataFrame:
    return pd.DataFrame({
        "name": ["Port strike", "Demand spike", "FX swing"],
        "category": ["logistics", "demand", "financial"],
        "likelihood": [0.5, 0.3, 0.2],
        "impact_value": [400_000.0, 120_000.0, 60_000.0],
        "detectability_days": [14.0, 3.0, 45.0],
        "time_to_recover": [60.0, 10.0, 20.0],
        "time_to_survive": [20.0, 15.0, 30.0],
    })


def _mitigations() -> dict:
    # Attached to the top risk (Port strike, EMV 200k) so it offers >=2 ways to act.
    return {
        "Port strike": [
            {"name": "Dual-port routing", "kind": "flexibility", "cost": 20_000.0, "likelihood_reduction": 0.4},
            {"name": "Pre-position buffer stock", "kind": "redundancy", "cost": 50_000.0, "impact_reduction": 0.5},
        ],
    }


# -- prepare ------------------------------------------------------------------


def test_prepare_reads_risk_factors(tmp_path):
    csv = tmp_path / "risks.csv"
    _risk_df().to_csv(csv, index=False)

    records = rj.prepare(str(csv), {})

    by = {r.name: r for r in records}
    assert by["Port strike"].category == "logistics"
    assert by["Demand spike"].likelihood == pytest.approx(0.3)
    assert by["FX swing"].impact_value == pytest.approx(60_000.0)


def test_prepare_attaches_mitigations_from_params(tmp_path):
    csv = tmp_path / "risks.csv"
    _risk_df().to_csv(csv, index=False)

    records = rj.prepare(str(csv), {"mitigations": _mitigations()})

    top = next(r for r in records if r.name == "Port strike")
    assert len(top.mitigations) == 2
    assert top.mitigations[0].name == "Dual-port routing"


def test_prepare_errors_without_required_columns(tmp_path):
    csv = tmp_path / "bad.csv"
    pd.DataFrame({"foo": [1]}).to_csv(csv, index=False)

    with pytest.raises(ValueError, match="name|likelihood|impact_value"):
        rj.prepare(str(csv), {})


# -- run + qa: the core "ranked options to act" guarantee ---------------------


def test_run_emits_ranked_mitigation_options_with_one_recommended():
    report = rj.run(rj.prepare_records(_risk_df(), {"mitigations": _mitigations()}))

    assert report.outcome.status == OPTIONS
    assert len(report.outcome.options) >= 2                          # >=2 ways to act
    assert sum(1 for o in report.outcome.options if o.recommended) == 1
    assert passed_guided(report.outcome)                             # never a dead end
    assert report.top_risk == "Port strike"
    assert rj.verify(report) == []


def test_run_ranks_by_emv_and_buys_down_residual():
    report = rj.run(rj.prepare_records(_risk_df(), {"mitigations": _mitigations()}))
    rr = report.risk_report

    assert [a.name for a in rr.assessments] == ["Port strike", "Demand spike", "FX swing"]
    assert rr.total_emv == pytest.approx(200_000.0 + 36_000.0 + 12_000.0)
    # top risk's recommended mitigation cuts its EMV, so residual < total
    assert rr.residual_emv == pytest.approx(120_000.0 + 36_000.0 + 12_000.0)
    assert rr.residual_emv < rr.total_emv
    assert "Port strike" in report.summary
    assert "->" in report.summary


def test_run_without_mitigations_still_protected():
    # Every risk falls back to "Accept / monitor"; outcome stays a valid OPTIONS result.
    report = rj.run(rj.prepare_records(_risk_df()))
    assert report.outcome.status == OPTIONS
    assert passed_guided(report.outcome)
    assert rj.verify(report) == []


# -- deck ---------------------------------------------------------------------


def test_build_deck_is_an_ascii_deliverable_listing_the_options():
    report = rj.run(rj.prepare_records(_risk_df(), {"mitigations": _mitigations()}))

    deck = rj.build_deck(
        report, client="Acme",
        citations=("Likelihood-Impact Risk Assessment - L3 risk module",), confidence=0.8,
    )

    assert isinstance(deck, Deliverable)
    md = deck.to_markdown()
    assert md.isascii()
    assert "Risk" in md
    assert "## Coverage & handoff" in md
    assert "## Options to act" in md
    assert "recommended" in md.lower()


# -- write_operational --------------------------------------------------------


def test_write_operational_one_row_per_risk(tmp_path):
    report = rj.run(rj.prepare_records(_risk_df(), {"mitigations": _mitigations()}))

    out = rj.write_operational(report, tmp_path, "Acme")

    assert out["csv"].exists()
    df = pd.read_csv(out["csv"])
    assert list(df["name"]) == ["Port strike", "Demand spike", "FX swing"]
    assert set(df.columns) >= {
        "name", "category", "zone", "score", "rpn", "emv", "exposure_gap_days", "recommended",
    }


# -- routing ------------------------------------------------------------------


def test_brief_routes_to_risk():
    reg = tools.build_default_registry()
    res = intent.classify(
        "build a supply chain risk register with a likelihood impact heatmap and a mitigation plan",
        reg, llm.RulesFallback(),
    )
    assert res.job_type == "risk"


def test_risk_keywords_do_not_steal_other_briefs():
    reg = tools.build_default_registry()
    p = llm.RulesFallback()
    assert intent.classify("set up reorder points and safety stock", reg, p).job_type == "inventory_optimization"
    assert intent.classify("run a what-if sensitivity tornado", reg, p).job_type == "whatif"
    assert intent.classify("rank suppliers by otif and award the best supplier", reg, p).job_type == "sourcing"


# -- end-to-end: deck written AND ranked options reach the caller on SUCCESS --


def test_orchestrator_risk_end_to_end_emits_deck_and_options(tmp_path):
    csv = tmp_path / "risks.csv"
    _risk_df().to_csv(csv, index=False)
    orch = Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback())

    res = orch.run(
        "assess our supply chain risk register and rank the mitigation plan options",
        data_path=str(csv), client="Acme", out_dir=tmp_path, overrides={"mitigations": _mitigations()},
    )

    assert res.status == "ok"
    assert res.tool == "risk"
    assert "csv" in res.deliverables
    assert Path(res.deliverables["deck_report"]).exists()
    # the whole point: a successful run still hands back >=2 ranked options to act
    assert res.guided is not None
    assert res.guided.status == OPTIONS
    assert len(res.guided.options) >= 2
    assert sum(1 for o in res.guided.options if o.recommended) == 1


def test_registry_includes_risk_tool():
    # risk is the newest analytical capability; it joins the existing surface (now 17 tools).
    reg = tools.build_default_registry()
    keys = {t.key for t in reg.list()}
    assert "risk" in keys
    assert len(keys) == 17
    assert reg.get("risk").requires_data is True
