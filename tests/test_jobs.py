"""Tests for the job-fulfillment layer (intake, playbook, QA, deliverables)."""

import numpy as np
import pandas as pd
import pytest
from openpyxl import load_workbook

from jobs import deliverables, qa
from jobs.intake import detect_columns, normalize, prepare
from jobs.inventory_optimization import run
from jobs.pricing import prepare_pricing
from jobs.pricing import run as run_pricing

PORTFOLIO = "data/sample_demand_portfolio.csv"
PRICING = "data/sample_pricing.csv"


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


def test_deliverables_defuse_formula_injection_in_product_id(tmp_path):
    """A CSV-uploaded product_id like '=cmd|...' must not survive into the
    client's .xlsx as a live formula (OWASP CSV/Excel injection)."""
    payload = '=cmd|" /C calc"!A0'
    demand = prepare(PORTFOLIO)
    demand.loc[demand["product_id"] == "SKU-A", "product_id"] = payload
    report = run(demand, budget=40000)
    written = deliverables.write_all(report, tmp_path, client="Acme")

    wb = load_workbook(written["excel"])
    ws = wb["Recommendations"]
    col = [c.value for c in ws[1]].index("product id") + 1
    values = [ws.cell(row=r, column=col).value for r in range(2, ws.max_row + 1)]
    assert payload not in values
    assert any(v == "'" + payload for v in values)

    df = pd.read_csv(written["csv"])
    assert payload not in set(df["product_id"])
    assert "'" + payload in set(df["product_id"])


# ---- pricing playbook --------------------------------------------------------

def test_pricing_playbook_runs_and_passes_qa():
    demand = prepare_pricing(PRICING)
    assert "price" in demand.columns
    report = run_pricing(demand)
    assert report.n_skus == 6
    assert qa.verify_pricing(report) == []
    # elastic SKUs yield a confident move; inelastic ones are flagged
    assert report.n_actionable >= 1
    assert report.n_inelastic >= 1


def test_pricing_optimal_above_cost_for_actionable():
    report = run_pricing(prepare_pricing(PRICING))
    for r in report.recommendations:
        if r.action in {"raise", "lower"}:
            assert r.optimal_price is not None and r.optimal_price > r.unit_cost
            assert r.elasticity < -1


def test_pricing_qa_catches_tampered_rec():
    report = run_pricing(prepare_pricing(PRICING))
    actionable = next(r for r in report.recommendations if r.action in {"raise", "lower"})
    object.__setattr__(actionable, "optimal_price", actionable.unit_cost * 0.5)  # below cost
    assert any("cost" in i for i in qa.verify_pricing(report))


def test_pricing_with_no_cost_column_is_never_reported_confident():
    """Regression: a recommendation built on an IMPUTED cost (no real cost column,
    so unit_cost = cost_ratio * price - a guess) could previously still be
    reported at full confidence, e.g. "raise 18%, +22% profit" built on a
    fabricated cost. Elastic, otherwise clearly-actionable data must still come
    back not-confident once the cost itself is not real."""
    rng = np.random.default_rng(9)
    prices = np.linspace(8, 24, 30)
    quantities = 8000 * prices ** (-2.0) * rng.normal(1.0, 0.02, size=prices.size)
    demand = pd.DataFrame({
        "product_id": ["SKU-X"] * len(prices),
        "price": prices,
        "quantity": quantities,
        # deliberately no "cost" column
    })

    report = run_pricing(demand)

    rec = report.recommendations[0]
    assert rec.cost_is_imputed is True
    assert rec.action in {"raise", "lower"}  # the underlying signal is real...
    assert rec.confident is False  # ...but must not be reported as trustworthy


def test_pricing_with_a_real_cost_column_can_be_confident():
    """Same elastic signal, but with a genuine cost column - confidence is
    governed by fit quality/extrapolation only, not suppressed by imputation."""
    rng = np.random.default_rng(9)
    prices = np.linspace(8, 24, 30)
    quantities = 8000 * prices ** (-2.0) * rng.normal(1.0, 0.02, size=prices.size)
    demand = pd.DataFrame({
        "product_id": ["SKU-X"] * len(prices),
        "price": prices,
        "quantity": quantities,
        "cost": [5.0] * len(prices),
    })

    report = run_pricing(demand)

    rec = report.recommendations[0]
    assert rec.cost_is_imputed is False
    assert rec.confident is True


def test_pricing_deliverables_written(tmp_path):
    report = run_pricing(prepare_pricing(PRICING))
    written = deliverables.write_pricing_all(report, tmp_path, client="Acme")
    assert all(p.exists() for p in written.values())
    md = written["report"].read_text(encoding="utf-8")
    assert "Price Optimization — Acme" in md
    assert "Elasticity" in md


def test_pricing_deliverables_defuse_formula_injection_in_product_id(tmp_path):
    """Same OWASP CSV/Excel injection guard, for the pricing deliverable path."""
    payload = '=cmd|" /C calc"!A0'
    rng = np.random.default_rng(9)
    prices = np.linspace(8, 24, 30)
    quantities = 8000 * prices ** (-2.0) * rng.normal(1.0, 0.02, size=prices.size)
    demand = pd.DataFrame({
        "product_id": [payload] * len(prices),
        "price": prices,
        "quantity": quantities,
        "cost": [5.0] * len(prices),
    })
    report = run_pricing(demand)
    written = deliverables.write_pricing_all(report, tmp_path, client="Acme")

    wb = load_workbook(written["excel"])
    ws = wb["Price recommendations"]
    col = [c.value for c in ws[1]].index("product id") + 1
    values = [ws.cell(row=r, column=col).value for r in range(2, ws.max_row + 1)]
    assert payload not in values
    assert any(v == "'" + payload for v in values)

    df = pd.read_csv(written["csv"])
    assert payload not in set(df["product_id"])
    assert "'" + payload in set(df["product_id"])
