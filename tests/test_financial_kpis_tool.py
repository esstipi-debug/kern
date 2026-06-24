"""Tests for the inventory financial-KPIs agent job + tool (11th tool).

Reads a per-SKU financials CSV (cogs, average inventory value, gross margin, ...), rolls it
up, and computes the auditable inventory-finance pack (turns, DIO, GMROI, sell-through,
inventory-to-sales, cash-to-cash). The tool wires it into the orchestrator so "show the
inventory financial KPIs" produces the finance dashboard deck.
"""

from pathlib import Path

import pandas as pd
import pytest

from jobs import financial_kpis_job as fkj
from scm_agent import intent, llm, tools
from scm_agent.orchestrator import Orchestrator
from src.deliverable import Deliverable


def _financials_df() -> pd.DataFrame:
    return pd.DataFrame({
        "product_id": ["SKU-A", "SKU-B"],
        "cogs": [100_000.0, 60_000.0],
        "avg_inventory_value": [25_000.0, 30_000.0],
        "gross_margin": [40_000.0, 20_000.0],
        "units_sold": [8_000.0, 4_000.0],
        "units_on_hand": [2_000.0, 1_000.0],
        "net_sales": [140_000.0, 80_000.0],
    })


# -- prepare ------------------------------------------------------------------


def test_prepare_reads_financial_rows(tmp_path):
    csv = tmp_path / "fin.csv"
    _financials_df().to_csv(csv, index=False)

    records = fkj.prepare(str(csv), {})

    by = {r["product_id"]: r for r in records}
    assert by["SKU-A"]["cogs"] == 100_000.0
    assert by["SKU-B"]["avg_inventory_value"] == 30_000.0


def test_prepare_errors_without_required_columns(tmp_path):
    csv = tmp_path / "bad.csv"
    pd.DataFrame({"foo": [1]}).to_csv(csv, index=False)

    with pytest.raises(ValueError, match="cogs|inventory|product"):
        fkj.prepare(str(csv), {})


# -- run + qa -----------------------------------------------------------------


def test_run_rolls_up_the_kpi_pack():
    report = fkj.run(fkj.prepare_records(_financials_df()), dso=45.0, dpo=30.0)

    assert report.n_skus == 2
    assert report.turns == pytest.approx(160_000 / 55_000, rel=1e-6)
    assert report.dio == pytest.approx(55_000 / 160_000 * 365, rel=1e-6)
    assert report.gmroi == pytest.approx(60_000 / 55_000, rel=1e-6)
    assert report.sell_through == pytest.approx(0.8)
    assert report.inventory_to_sales == pytest.approx(0.25)
    assert report.cash_to_cash == pytest.approx(report.dio + 45.0 - 30.0)
    assert fkj.verify(report) == []


def test_worst_gmroi_sku_is_flagged_first():
    report = fkj.run(fkj.prepare_records(_financials_df()))
    # A GMROI 40000/25000=1.6, B 20000/30000=0.667 -> B is the underperformer
    assert report.worst[0].product_id == "SKU-B"


# -- deck ---------------------------------------------------------------------


def test_build_deck_is_an_ascii_deliverable():
    report = fkj.run(fkj.prepare_records(_financials_df()))

    deck = fkj.build_deck(report, client="Acme", citations=("SCOR - cash-to-cash",), confidence=0.85)

    assert isinstance(deck, Deliverable)
    md = deck.to_markdown()
    assert md.isascii()
    assert "GMROI" in md and "## Coverage & handoff" in md


# -- routing ------------------------------------------------------------------


def test_brief_routes_to_financial_kpis():
    reg = tools.build_default_registry()
    res = intent.classify("show the inventory financial KPIs: GMROI, inventory turns and sell-through",
                          reg, llm.RulesFallback())
    assert res.job_type == "financial_kpis"


# -- end-to-end ---------------------------------------------------------------


def test_orchestrator_runs_financial_kpis_and_emits_the_deck(tmp_path):
    csv = tmp_path / "fin.csv"
    _financials_df().to_csv(csv, index=False)
    orch = Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback())

    res = orch.run("report the inventory financial KPIs (GMROI, turns, DIO)",
                   data_path=str(csv), client="Acme", out_dir=tmp_path)

    assert res.status == "ok"
    assert res.tool == "financial_kpis"
    assert "csv" in res.deliverables
    assert Path(res.deliverables["deck_report"]).exists()
