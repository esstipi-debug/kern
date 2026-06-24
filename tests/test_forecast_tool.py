"""Tests for the demand-forecasting agent job + tool.

Reads a demand-history CSV, segments each SKU by forecastability (Syntetos-Boylan
quadrants), auto-selects + backtests the matching method, quantifies Forecast Value-Add
vs a naive baseline, and emits a protected GuidedOutcome with **ranked forecasting-policy
options** (auto-per-segment / global / review-lumpy). The tool wires it so a "forecast
demand" brief produces both the study deck AND >=2 ranked options to act.
"""

from pathlib import Path

import pandas as pd
import pytest

from jobs import forecast_job as fj
from scm_agent import intent, llm, tools
from scm_agent.orchestrator import Orchestrator
from src.deliverable import Deliverable
from src.guided import OPTIONS, passed_guided

_SMOOTH = [10, 11, 9, 10, 12, 8, 10, 11, 9, 10, 11, 10]
_ERRATIC = [1, 20, 2, 18, 3, 25, 1, 22, 4, 19, 2, 23]
_INTERMITTENT = [10, 0, 10, 0, 10, 0, 10, 0, 10, 0, 10, 0]
_LUMPY = [0, 30, 0, 0, 5, 0, 0, 40, 0, 2, 0, 15]


def _demand_df() -> pd.DataFrame:
    series = {"A": _SMOOTH, "B": _ERRATIC, "C": _INTERMITTENT, "D": _LUMPY}
    rows = [
        {"sku": sku, "period": t, "demand": q}
        for sku, vals in series.items()
        for t, q in enumerate(vals)
    ]
    return pd.DataFrame(rows)


# -- prepare ------------------------------------------------------------------


def test_prepare_builds_one_ordered_series_per_sku(tmp_path):
    csv = tmp_path / "demand.csv"
    _demand_df().to_csv(csv, index=False)

    series = fj.prepare(str(csv), {})

    assert set(series) == {"A", "B", "C", "D"}
    assert series["A"] == _SMOOTH                      # order preserved by period
    assert len(series["C"]) == 12


def test_prepare_cleans_missing_and_negative_to_zero(tmp_path):
    df = pd.DataFrame({"sku": ["A", "A", "A"], "period": [0, 1, 2], "demand": [5.0, None, -3.0]})
    csv = tmp_path / "d.csv"
    df.to_csv(csv, index=False)

    series = fj.prepare(str(csv), {})

    assert series["A"] == [5.0, 0.0, 0.0]              # NaN -> 0, negative floored to 0


def test_prepare_errors_without_required_columns(tmp_path):
    csv = tmp_path / "bad.csv"
    pd.DataFrame({"foo": [1]}).to_csv(csv, index=False)

    with pytest.raises(ValueError, match="product|quantity"):
        fj.prepare(str(csv), {})


# -- run + qa: forecastability + auto-method + value-add + options ------------


def test_run_segments_and_picks_method_per_quadrant():
    report = fj.run(fj.prepare_records(_demand_df()))

    by = {s.name: s for s in report.skus}
    assert by["A"].quadrant == "smooth" and by["A"].method == "ses"
    assert by["C"].quadrant == "intermittent" and by["C"].method == "croston"
    assert by["D"].quadrant == "lumpy" and by["D"].method == "croston"
    assert report.mix == {"smooth": 1, "erratic": 1, "intermittent": 1, "lumpy": 1}
    assert report.n_skus == 4


def test_run_quantifies_value_add_vs_naive():
    report = fj.run(fj.prepare_records(_demand_df()))

    # smooth and intermittent series are comfortably forecastable -> beat the naive baseline
    by = {s.name: s for s in report.skus}
    assert by["A"].beats_naive is True
    assert by["C"].beats_naive is True
    assert by["A"].fva > 0.0
    assert report.n_beating_naive >= 2


def test_run_emits_ranked_policy_options_with_one_recommended():
    report = fj.run(fj.prepare_records(_demand_df()))

    assert report.outcome.status == OPTIONS
    assert len(report.outcome.options) >= 2
    assert sum(1 for o in report.outcome.options if o.recommended) == 1
    assert passed_guided(report.outcome)
    assert fj.verify(report) == []


# -- deck ---------------------------------------------------------------------


def test_build_deck_is_an_ascii_deliverable_listing_options():
    report = fj.run(fj.prepare_records(_demand_df()))

    deck = fj.build_deck(report, client="Acme", citations=("Syntetos-Boylan 2005 - SBC categorization",), confidence=0.8)

    assert isinstance(deck, Deliverable)
    md = deck.to_markdown()
    assert md.isascii()
    assert "Forecast" in md
    assert "## Coverage & handoff" in md
    assert "## Options to act" in md
    assert "recommended" in md.lower()


# -- write_operational --------------------------------------------------------


def test_write_operational_one_row_per_sku(tmp_path):
    report = fj.run(fj.prepare_records(_demand_df()))

    out = fj.write_operational(report, tmp_path, "Acme")

    assert out["csv"].exists()
    df = pd.read_csv(out["csv"])
    assert len(df) == 4
    assert set(df.columns) >= {"name", "quadrant", "adi", "cv2", "method", "forecast", "mase", "fva", "beats_naive"}


# -- routing ------------------------------------------------------------------


def test_brief_routes_to_forecast():
    reg = tools.build_default_registry()
    res = intent.classify(
        "forecast demand per sku, segment forecastability and handle intermittent and lumpy demand",
        reg, llm.RulesFallback(),
    )
    assert res.job_type == "forecast"


def test_forecast_keywords_do_not_steal_other_briefs():
    reg = tools.build_default_registry()
    p = llm.RulesFallback()
    assert intent.classify("set up reorder points and safety stock", reg, p).job_type == "inventory_optimization"
    assert intent.classify("classify our SKUs with ABC XYZ analysis", reg, p).job_type == "abc_xyz"
    assert intent.classify("rank the reverse logistics disposition of product returns", reg, p).job_type == "returns"


# -- end-to-end ---------------------------------------------------------------


def test_orchestrator_forecast_end_to_end_emits_deck_and_options(tmp_path):
    csv = tmp_path / "demand.csv"
    _demand_df().to_csv(csv, index=False)
    orch = Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback())

    res = orch.run(
        "forecast demand for each sku and recommend the forecasting method (intermittent / croston)",
        data_path=str(csv), client="Acme", out_dir=tmp_path,
    )

    assert res.status == "ok"
    assert res.tool == "forecast"
    assert "csv" in res.deliverables
    assert Path(res.deliverables["deck_report"]).exists()
    assert res.guided is not None
    assert res.guided.status == OPTIONS
    assert len(res.guided.options) >= 2
    assert sum(1 for o in res.guided.options if o.recommended) == 1


def test_registry_includes_forecast_tool():
    reg = tools.build_default_registry()
    keys = {t.key for t in reg.list()}
    assert "forecast" in keys
    assert reg.get("forecast").requires_data is True
