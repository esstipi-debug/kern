"""Tests for the job-fulfillment layer (intake, playbook, QA, deliverables)."""

import pandas as pd
import pytest

from jobs import deliverables, qa
from jobs.intake import detect_columns, normalize, prepare
from jobs.inventory_optimization import run

PORTFOLIO = "data/sample_demand_portfolio.csv"


def test_detect_columns_from_arbitrary_headers():
    df = pd.DataFrame(columns=["Order Date", "SKU", "Qty", "Unit Price", "Lead Time"])
    m = detect_columns(df)
    assert m.ok
    assert m.mapping["date"] == "Order Date"
    assert m.mapping["product_id"] == "SKU"
    assert m.mapping["quantity"] == "Qty"
    assert m.mapping["unit_cost"] == "Unit Price"
    assert m.mapping["lead_time_days"] == "Lead Time"


def test_detect_columns_reports_missing_required():
    df = pd.DataFrame(columns=["foo", "bar"])
    m = detect_columns(df)
    assert not m.ok
    assert set(m.unmatched_required) == {"date", "product_id", "quantity"}


def test_normalize_aggregates_transactions_to_weekly():
    # two same-week transactions for one SKU must sum into one weekly row
    raw = pd.DataFrame(
        {
            "InvoiceDate": ["2024-01-01", "2024-01-03", "2024-01-08"],
            "StockCode": ["A", "A", "A"],
            "Quantity": [10, 5, 20],
            "UnitPrice": [2.0, 2.0, 2.5],
        }
    )
    canon = normalize(raw, detect_columns(raw), period="W")
    assert list(canon.columns) == ["date", "product_id", "quantity", "unit_cost", "lead_time_days"]
    assert len(canon) == 2  # week 1 (10+5) and week 2 (20)
    assert canon.iloc[0]["quantity"] == 15
    assert "lead_time_days" in canon.columns  # defaulted when absent


def test_normalize_rejects_undetectable_data():
    raw = pd.DataFrame({"foo": [1], "bar": [2]})
    with pytest.raises(ValueError):
        normalize(raw, detect_columns(raw))


def test_playbook_runs_and_passes_qa():
    demand = prepare(PORTFOLIO)
    report = run(demand, service_level=0.95, holding_rate=0.25, order_cost=75.0)
    assert report.n_skus == 8
    assert qa.verify(report) == []  # internally consistent
    # at least one (s,Q) and one intermittent (R,S)
    kinds = {r.policy_kind for r in report.recommendations}
    assert "(s, Q)" in kinds and "(R, S)" in kinds
    for r in report.recommendations:
        assert r.investment == pytest.approx(r.cycle_investment + r.ss_investment)


def test_budget_scaling_and_feasibility():
    demand = prepare(PORTFOLIO)
    full = run(demand)
    tight = run(demand, budget=full.requested_investment * 0.7)
    assert tight.safety_stock_scale < 1.0
    assert tight.final_investment <= tight.requested_investment + 1e-6
    assert qa.verify(tight) == []

    infeasible = run(demand, budget=1.0)
    assert infeasible.feasible is False
    assert qa.verify(infeasible) == []  # consistent even when infeasible


def test_qa_catches_tampered_report():
    demand = prepare(PORTFOLIO)
    report = run(demand)
    bad = report.recommendations[0]
    object.__setattr__(bad, "investment", bad.investment + 999)  # corrupt the math
    issues = qa.verify(report)
    assert any("investment" in i for i in issues)


def test_deliverables_written(tmp_path):
    demand = prepare(PORTFOLIO)
    report = run(demand, budget=40000)
    written = deliverables.write_all(report, tmp_path, client="Acme")
    assert written["excel"].exists()
    assert written["report"].exists()
    assert written["csv"].exists()
    md = written["report"].read_text(encoding="utf-8")
    assert "Inventory Optimization — Acme" in md
    assert "Methodology" in md
