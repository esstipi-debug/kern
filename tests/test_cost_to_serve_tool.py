"""Tests for the cost-to-serve agent job + tool (closes part of the 3-tools gap).

The job aggregates raw order-line data into per-segment activity (decoupled from the
parallel loop's intake.py - pandas directly), runs the cost-to-serve + working-capital
analysis, and the tool wires it into the orchestrator (prepare/run/qa/deliver/deck) so
an agent brief like "cost to serve by segment" produces the CFO deck end-to-end.
"""

from pathlib import Path

import pandas as pd
import pytest

from jobs import cost_to_serve_job as ctj
from scm_agent import intent, llm, tools
from scm_agent.orchestrator import Orchestrator
from src.cost_to_serve import ServiceCostRates


def _orderlines() -> pd.DataFrame:
    return pd.DataFrame({
        "Segment": ["Retail", "Retail", "Wholesale"],
        "Sales": [100.0, 50.0, 200.0],
        "Quantity": [10, 5, 20],
        "Order ID": ["o1", "o2", "o3"],
        "COGS": [60.0, 30.0, 120.0],
    })


# -- order-line aggregation ---------------------------------------------------


def test_aggregate_rolls_order_lines_up_by_segment():
    segs = ctj.aggregate_segments(
        _orderlines(), segment_col="Segment", revenue_col="Sales",
        qty_col="Quantity", order_col="Order ID", cogs_col="COGS",
    )

    by = {s.segment: s for s in segs}
    assert by["Retail"].revenue == pytest.approx(150.0)
    assert by["Retail"].units == pytest.approx(15.0)
    assert by["Retail"].orders == pytest.approx(2.0)        # 2 distinct order ids
    assert by["Retail"].cogs == pytest.approx(90.0)
    assert by["Wholesale"].revenue == pytest.approx(200.0)


def test_cogs_falls_back_to_cost_ratio_when_absent():
    df = pd.DataFrame({"Segment": ["A"], "Sales": [100.0], "Quantity": [10]})

    segs = ctj.aggregate_segments(df, segment_col="Segment", revenue_col="Sales",
                                  qty_col="Quantity", cost_ratio=0.6)

    assert segs[0].cogs == pytest.approx(60.0)              # 100 * 0.6


def test_orders_default_to_line_count_without_an_order_column():
    df = pd.DataFrame({"Segment": ["A", "A"], "Sales": [100.0, 50.0], "Quantity": [10, 5]})

    segs = ctj.aggregate_segments(df, segment_col="Segment", revenue_col="Sales", qty_col="Quantity")

    assert segs[0].orders == pytest.approx(2.0)             # 2 lines, no order id


# -- prepare (CSV -> activities) ----------------------------------------------


def test_prepare_reads_a_csv_and_picks_columns(tmp_path):
    csv = tmp_path / "orders.csv"
    _orderlines().to_csv(csv, index=False)

    segs = ctj.prepare(str(csv), {})

    assert {s.segment for s in segs} == {"Retail", "Wholesale"}


def test_prepare_errors_helpfully_when_the_segment_column_is_missing(tmp_path):
    csv = tmp_path / "bad.csv"
    pd.DataFrame({"foo": [1], "Sales": [2.0]}).to_csv(csv, index=False)

    with pytest.raises(ValueError, match="segment_col"):
        ctj.prepare(str(csv), {})


# -- run + qa -----------------------------------------------------------------


def test_run_builds_a_portfolio_and_optional_cash_lens():
    segs = ctj.aggregate_segments(_orderlines(), segment_col="Segment", revenue_col="Sales",
                                  qty_col="Quantity", order_col="Order ID", cogs_col="COGS")

    report = ctj.run(segs, rates=ServiceCostRates(cost_per_order=2.0), dio=60.0, dso=40.0, dpo=30.0,
                     dio_days=5.0)

    assert report.portfolio.total_revenue == pytest.approx(350.0)
    assert report.working_cap is not None
    assert report.cash_release is not None and report.cash_release.total_cash_released > 0
    assert ctj.verify(report) == []                         # passes the QA gate


def test_run_without_cash_params_omits_the_working_capital_lens():
    segs = ctj.aggregate_segments(_orderlines(), segment_col="Segment", revenue_col="Sales",
                                  qty_col="Quantity")

    report = ctj.run(segs)

    assert report.working_cap is None
    assert report.cash_release is None
    assert ctj.verify(report) == []


# -- intent routing (must not steal the other tools' briefs) ------------------


def test_brief_routes_to_cost_to_serve():
    reg = tools.build_default_registry()
    res = intent.classify("analyze cost to serve and working capital by customer segment",
                          reg, llm.RulesFallback())
    assert res.job_type == "cost_to_serve"


def test_existing_briefs_still_route_correctly():
    reg = tools.build_default_registry()
    p = llm.RulesFallback()
    assert intent.classify("set up reorder points and safety stock", reg, p).job_type == "inventory_optimization"
    assert intent.classify("what price maximizes profit", reg, p).job_type == "pricing"
    assert intent.classify("evaluate our supply chain leadership (CHAIN)", reg, p).job_type == "leadership_chain"


# -- end-to-end through the orchestrator --------------------------------------


def test_orchestrator_runs_cost_to_serve_and_emits_the_deck(tmp_path):
    csv = tmp_path / "orders.csv"
    _orderlines().to_csv(csv, index=False)
    orch = Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback())

    res = orch.run("cost to serve and profitability by segment", data_path=str(csv),
                   client="Acme", out_dir=tmp_path)

    assert res.status == "ok"
    assert res.tool == "cost_to_serve"
    assert "csv" in res.deliverables                        # operational P&L
    deck = Path(res.deliverables["deck_report"])
    assert deck.exists()
    text = deck.read_text(encoding="utf-8")
    assert "Cost-to-Serve" in text and "## Coverage & handoff" in text
