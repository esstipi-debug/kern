"""A KPI with no denominator is not measurable, and must not read as excellent.

The degenerate branches in src/financial_kpis.py encoded absence as the most
favourable value a reader could see: no inventory at all returned *infinite*
turns, and a stockout count over zero periods returned "0% stockout". Both are
the answer a healthy operation would produce, printed for an operation nobody
measured.
"""

import math

import pandas as pd
import pytest

from jobs import financial_kpis_job as fkj
from src.deliverable import NOT_MEASURED
from src.financial_kpis import (
    cash_to_cash,
    days_inventory_outstanding,
    gmroi,
    inventory_to_sales,
    inventory_turns,
    sell_through,
    stockout_rate,
    weeks_of_supply,
)

_ROWS = pd.DataFrame({
    "product_id": ["A", "B"],
    "cogs": [1000.0, 2000.0],
    "avg_inventory_value": [250.0, 500.0],
    "gross_margin": [400.0, 700.0],
    "units_sold": [100.0, 200.0],
    "units_on_hand": [25.0, 50.0],
    "net_sales": [1400.0, 2700.0],
})


# ---- the engine: no denominator means no number ------------------------------

def test_no_inventory_is_not_infinite_turns():
    assert inventory_turns(1000.0, 0.0) is None


def test_no_cogs_is_not_infinite_days_on_hand():
    assert days_inventory_outstanding(500.0, 0.0) is None


def test_no_inventory_cost_is_not_infinite_gmroi():
    assert gmroi(400.0, 0.0) is None


def test_no_demand_is_not_infinite_weeks_of_supply():
    assert weeks_of_supply(100.0, 0.0) is None


def test_no_sales_is_not_an_infinite_inventory_to_sales_ratio():
    assert inventory_to_sales(500.0, 0.0) is None


def test_no_units_at_all_is_not_zero_sell_through():
    assert sell_through(0.0, 0.0) is None


def test_no_periods_observed_is_not_a_zero_percent_stockout_rate():
    """The most credibility-damaging one: 'we never stocked out' from no data."""
    assert stockout_rate(0.0, 0.0) is None
    assert stockout_rate(3.0, 10.0) == pytest.approx(0.3)


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_a_non_finite_input_is_refused_rather_than_propagated(bad):
    assert inventory_turns(bad, 500.0) is None
    assert days_inventory_outstanding(bad, 1000.0) is None
    assert gmroi(bad, 500.0) is None
    assert inventory_to_sales(bad, 1000.0) is None
    assert weeks_of_supply(bad, 10.0) is None


def test_cash_to_cash_cannot_be_computed_from_an_unmeasurable_dio():
    assert cash_to_cash(None, 45.0, 30.0) is None
    assert cash_to_cash(60.0, 45.0, 30.0) == pytest.approx(75.0)


def test_healthy_inputs_are_untouched():
    assert inventory_turns(1000.0, 250.0) == pytest.approx(4.0)
    assert days_inventory_outstanding(250.0, 1000.0) == pytest.approx(91.25)
    assert gmroi(400.0, 250.0) == pytest.approx(1.6)
    assert sell_through(75.0, 25.0) == pytest.approx(0.75)
    assert weeks_of_supply(100.0, 10.0) == pytest.approx(10.0)
    assert inventory_to_sales(250.0, 1000.0) == pytest.approx(0.25)


# ---- the job: an absent column is absent, not zero --------------------------

def test_an_absent_optional_column_stays_absent_instead_of_becoming_zero():
    """`_num` mapped a missing margin to 0.0, i.e. 'this SKU earns nothing'."""
    df = _ROWS.drop(columns=["gross_margin", "net_sales"])

    records = fkj.prepare_records(df)

    assert all(r["gross_margin"] is None for r in records)
    assert all(r["net_sales"] is None for r in records)
    assert all(r["units_sold"] is not None for r in records)


def test_a_blank_cell_is_absent_not_zero():
    df = _ROWS.copy()
    df.loc[0, "gross_margin"] = None

    records = fkj.prepare_records(df)

    assert records[0]["gross_margin"] is None
    assert records[1]["gross_margin"] == pytest.approx(700.0)


def test_a_blank_required_cell_is_refused_at_the_door():
    """COGS and average inventory are the denominators; a blank one poisons the pack."""
    df = _ROWS.copy()
    df.loc[0, "cogs"] = None

    with pytest.raises(ValueError, match="cogs"):
        fkj.prepare_records(df)


def test_an_unmeasurable_kpi_is_none_on_the_report_not_zero():
    df = _ROWS.drop(columns=["gross_margin"])

    report = fkj.run(fkj.prepare_records(df))

    assert report.total_gross_margin is None
    assert report.gmroi is None
    assert report.turns == pytest.approx(4.0)      # this one *was* measurable


def test_the_qa_gate_blocks_a_pack_whose_headline_kpi_is_unmeasurable():
    df = _ROWS.copy()
    df["avg_inventory_value"] = 0.0

    report = fkj.run(fkj.prepare_records(df))

    assert report.turns is None
    assert fkj.verify(report) != []


def test_the_deck_says_not_measured_instead_of_printing_a_number():
    df = _ROWS.drop(columns=["gross_margin", "net_sales"])
    report = fkj.run(fkj.prepare_records(df))

    md = fkj.build_deck(report, client="Acme").to_markdown()

    assert NOT_MEASURED in md
    assert "GMROI 0.00" not in md
    assert md.isascii()


def test_the_operational_csv_leaves_an_unmeasurable_gmroi_blank(tmp_path):
    df = _ROWS.drop(columns=["gross_margin"])
    report = fkj.run(fkj.prepare_records(df))

    paths = fkj.write_operational(report, tmp_path)
    rows = pd.read_csv(paths["csv"], keep_default_na=False)

    assert set(rows["gmroi"]) == {""}


def test_a_fully_populated_pack_is_unchanged():
    report = fkj.run(fkj.prepare_records(_ROWS), dso=45.0, dpo=30.0)

    assert report.turns == pytest.approx(4.0)
    assert report.gmroi == pytest.approx(1100.0 / 750.0)
    assert report.sell_through == pytest.approx(300.0 / 375.0)
    assert math.isfinite(report.cash_to_cash)
    assert fkj.verify(report) == []


# ---- the agent surface must survive an unmeasurable KPI ----------------------

def test_the_agent_summary_and_options_survive_an_unmeasurable_gmroi(tmp_path):
    """verify() lets this through (turns *is* measurable), so the surfaces past the
    QA gate must handle it rather than crash formatting `None`."""
    from scm_agent import llm, tools
    from scm_agent.orchestrator import Orchestrator

    csv = tmp_path / "fin.csv"
    _ROWS.drop(columns=["gross_margin", "net_sales"]).to_csv(csv, index=False)
    orch = Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback())

    res = orch.run("inventory financial KPIs: turns, DIO, GMROI, cash-to-cash",
                   data_path=str(csv), client="Acme", out_dir=tmp_path)

    assert res.status == "ok", res.summary
    assert NOT_MEASURED in res.summary or "GMROI" in res.summary
    assert res.guided is not None and res.guided.options
