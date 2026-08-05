"""Tests for the data-quality / SKU-master (MDM) agent job + tool.

Audits a product master: finds duplicate SKUs (shared GTIN or fuzzy name), validates
GTIN check digits (GS1 mod-10), flags completeness gaps (missing name, non-positive
cost), scores overall quality, and - crucially - emits a protected GuidedOutcome with
**ranked remediation options** (merge+remediate / merge-only / steward exception list).
"""

from pathlib import Path

import pandas as pd
import pytest
from nosilo_core.guided import OPTIONS, passed_guided

from jobs import data_quality_job as dq
from nosilo_core import llm
from scm_agent import intent, tools
from scm_agent.orchestrator import Orchestrator
from src.deliverable import Deliverable


# GS1-valid GTINs: 00012345600012 and 036000291452. 00012345600015 fails the check digit.
def _sku_df() -> pd.DataFrame:
    return pd.DataFrame({
        "sku":       ["A-100",        "B-200",       "C-300",          "D-400",          "E-500",      "F-600", "G-700"],
        "name":      ["Widget Blue",  "Gadget Red",  "Gizmo Green",    "Widget Blue",    "Gadget Red", "",      "Sprocket"],
        "gtin":      ["00012345600012", "",          "00012345600015", "00012345600012", "",           "",      "036000291452"],
        "unit_cost": [10.0,           -5.0,          3.0,              8.0,              2.0,          4.0,     5.0],
    })


# -- prepare ------------------------------------------------------------------


def test_prepare_reads_records_and_preserves_gtin_leading_zeros(tmp_path):
    csv = tmp_path / "skus.csv"
    _sku_df().to_csv(csv, index=False)

    records = dq.prepare(str(csv), {})

    by = {r["product_id"]: r for r in records}
    assert by["A-100"]["gtin"] == "00012345600012"     # leading zeros preserved (read as str)
    assert by["F-600"]["name"] == ""
    assert len(records) == 7


def test_prepare_errors_without_a_product_column(tmp_path):
    csv = tmp_path / "bad.csv"
    pd.DataFrame({"foo": [1]}).to_csv(csv, index=False)

    with pytest.raises(ValueError, match="product"):
        dq.prepare(str(csv), {})


# -- run: dedup + GTIN validation + completeness ------------------------------


def test_run_finds_duplicate_clusters_by_gtin_and_name():
    report = dq.run(dq.prepare_records(_sku_df()))

    reasons = {c.reason for c in report.duplicate_clusters}
    assert reasons == {"gtin", "name"}                 # A/D share a GTIN; B/E share a name
    assert report.n_duplicate_skus == 4
    clustered = {pid for c in report.duplicate_clusters for pid in c.product_ids}
    assert clustered == {"A-100", "D-400", "B-200", "E-500"}


def test_run_validates_gtin_check_digits():
    report = dq.run(dq.prepare_records(_sku_df()))

    assert report.gtin_valid == 3                       # A-100, D-400, G-700
    assert report.gtin_invalid == 1                     # C-300 fails the check digit
    assert report.gtin_missing == 3                     # B-200, E-500, F-600
    assert any(i.issue == "invalid_gtin" and i.product_id == "C-300" for i in report.issues)


def test_run_flags_completeness_and_scores_quality():
    report = dq.run(dq.prepare_records(_sku_df()))

    assert any(i.issue == "missing_name" and i.product_id == "F-600" for i in report.issues)
    assert any(i.issue == "nonpositive_cost" and i.product_id == "B-200" for i in report.issues)
    # G-700 is the only fully clean record -> score = 1/7
    assert report.n_records == 7
    assert report.n_clean == 1
    assert 0.0 < report.quality_score < 1.0


def test_run_emits_ranked_remediation_options_with_one_recommended():
    report = dq.run(dq.prepare_records(_sku_df()))

    assert report.outcome.status == OPTIONS
    assert len(report.outcome.options) >= 2
    assert sum(1 for o in report.outcome.options if o.recommended) == 1
    assert passed_guided(report.outcome)
    assert dq.verify(report) == []


# -- deck ---------------------------------------------------------------------


def test_build_deck_is_an_ascii_deliverable_listing_options():
    report = dq.run(dq.prepare_records(_sku_df()))

    deck = dq.build_deck(report, client="Acme", citations=("GS1 General Specifications - check digit",), confidence=0.8)

    assert isinstance(deck, Deliverable)
    md = deck.to_markdown()
    assert md.isascii()
    assert "Quality" in md
    assert "## Coverage & handoff" in md
    assert "## Options to act" in md
    assert "recommended" in md.lower()


# -- write_operational --------------------------------------------------------


def test_write_operational_emits_an_issue_register(tmp_path):
    report = dq.run(dq.prepare_records(_sku_df()))

    out = dq.write_operational(report, tmp_path, "Acme")

    assert out["csv"].exists()
    df = pd.read_csv(out["csv"])
    assert set(df.columns) >= {"product_id", "field", "issue", "severity", "detail"}
    assert len(df) >= 4


# -- routing ------------------------------------------------------------------


def test_brief_routes_to_data_quality():
    reg = tools.build_default_registry()
    res = intent.classify(
        "audit our sku master for duplicate skus and validate gtins (data quality / mdm)",
        reg, llm.RulesFallback(),
    )
    assert res.job_type == "data_quality"


def test_data_quality_keywords_do_not_steal_other_briefs():
    reg = tools.build_default_registry()
    p = llm.RulesFallback()
    assert intent.classify("set up reorder points and safety stock", reg, p).job_type == "inventory_optimization"
    assert intent.classify(
        "reconcile the physical count and report inventory record accuracy", reg, p
    ).job_type == "reconciliation"
    assert intent.classify("forecast demand and pick the method per sku", reg, p).job_type == "forecast"


# -- end-to-end ---------------------------------------------------------------


def test_orchestrator_data_quality_end_to_end_emits_deck_and_options(tmp_path):
    csv = tmp_path / "skus.csv"
    _sku_df().to_csv(csv, index=False)
    orch = Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback())

    res = orch.run(
        "clean our sku master: deduplicate skus and validate gtins (data quality)",
        data_path=str(csv), client="Acme", out_dir=tmp_path,
    )

    assert res.status == "ok"
    assert res.tool == "data_quality"
    assert "csv" in res.deliverables
    assert Path(res.deliverables["deck_report"]).exists()
    assert res.guided is not None
    assert res.guided.status == OPTIONS
    assert len(res.guided.options) >= 2
    assert sum(1 for o in res.guided.options if o.recommended) == 1


def test_registry_includes_data_quality_tool():
    reg = tools.build_default_registry()
    keys = {t.key for t in reg.list()}
    assert "data_quality" in keys
    assert reg.get("data_quality").requires_data is True
