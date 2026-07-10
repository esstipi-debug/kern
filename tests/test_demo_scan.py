"""Unit tests for webapp/demo_scan.py: the /demo funnel's one-CSV scan.

Covers the derivations (stock snapshot -> ABC items / finance records), the
QA gate (any verify() issue or non-finite headline => no findings, not ok),
and the rendered artifacts (mini-report + follow-up draft).
"""
from __future__ import annotations

import pandas as pd
import pytest

from webapp.demo_scan import (
    CTA_PATH,
    DAYS_PER_YEAR,
    derive_abc_items,
    derive_finance_records,
    render_followup_email,
    render_mini_report,
    run_demo_scan,
    safe_lead_dirname,
)


def _stock_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"product_id": "SKU-001", "on_hand": 320, "daily_demand": 6.0,
             "unit_cost": 7.0, "days_since_last_sale": 3},
            {"product_id": "SKU-002", "on_hand": 900, "daily_demand": 1.5,
             "unit_cost": 12.0, "days_since_last_sale": 210},
            {"product_id": "SKU-004", "on_hand": 500, "daily_demand": 0.0,
             "unit_cost": 9.5, "days_since_last_sale": 260},
            {"product_id": "SKU-006", "on_hand": 1200, "daily_demand": 3.0,
             "unit_cost": 5.0, "days_since_last_sale": 95},
        ]
    )


def test_scan_ok_with_money_figure_and_three_findings():
    result = run_demo_scan(_stock_df())
    assert result.ok
    assert result.qa_issues == ()
    assert result.eo.eo_value > 0
    assert len(result.findings) == 3
    h = result.headline
    assert h["eo_value"] > 0
    assert 0.0 <= h["eo_pct_of_value"] <= 1.0
    assert 0.0 < h["a_value_share"] <= 1.0
    assert h["dio"] > 0
    assert h["n_skus"] == 4


def test_findings_quote_the_money_and_the_cta_numbers():
    result = run_demo_scan(_stock_df())
    f1, f2, f3 = result.findings
    assert "$" in f1 and "atrapados" in f1
    assert "clase A" in f2
    assert "DIO" in f3 or "dias" in f3


def test_derive_abc_items_annualizes_demand():
    stocks = run_demo_scan(_stock_df()).eo.lines  # any SkuStock-shaped source
    items = derive_abc_items(
        __import__("jobs.excess_obsolete_job", fromlist=["prepare_records"])
        .prepare_records(_stock_df())["stocks"]
    )
    assert all(len(it["demand"]) == 1 for it in items)
    by_id = {it["product_id"]: it for it in items}
    assert by_id["SKU-001"]["demand"][0] == pytest.approx(6.0 * DAYS_PER_YEAR)
    assert len(stocks) == len(items)


def test_derive_finance_records_cost_basis():
    payload = __import__("jobs.excess_obsolete_job", fromlist=["prepare_records"]).prepare_records(
        _stock_df()
    )
    records = derive_finance_records(payload["stocks"])
    by_id = {r["product_id"]: r for r in records}
    assert by_id["SKU-001"]["cogs"] == pytest.approx(6.0 * DAYS_PER_YEAR * 7.0)
    assert by_id["SKU-001"]["avg_inventory_value"] == pytest.approx(320 * 7.0)
    assert by_id["SKU-004"]["cogs"] == 0.0


def test_qa_gate_without_unit_cost_fails_and_emits_no_findings():
    df = _stock_df().drop(columns=["unit_cost"])
    result = run_demo_scan(df)
    assert not result.ok
    assert result.findings == ()
    assert any("inventario" in issue or "inventory" in issue for issue in result.qa_issues)


def test_qa_gate_all_zero_demand_blocks_nonfinite_dio():
    df = _stock_df().assign(daily_demand=0.0)
    result = run_demo_scan(df)
    assert not result.ok
    assert result.findings == ()


def test_missing_required_columns_raise_value_error():
    with pytest.raises(ValueError, match="on_hand"):
        run_demo_scan(pd.DataFrame([{"product_id": "X", "daily_demand": 1.0}]))


@pytest.mark.parametrize(
    ("email", "expected"),
    [
        ("Foo.Bar@Empresa.COM", "foo.bar_at_empresa.com"),
        ("a b@c.com", "a_b_at_c.com"),
        ("../../evil@x.com", "_.._evil_at_x.com"),
        ("...", "lead"),
        ("", "lead"),
    ],
)
def test_safe_lead_dirname_is_single_safe_segment(email, expected):
    name = safe_lead_dirname(email)
    assert name == expected
    assert "/" not in name and "\\" not in name
    assert name not in (".", "..")


def test_mini_report_contains_headline_and_cta():
    result = run_demo_scan(_stock_df())
    md = render_mini_report(result, email="a@b.com", dataset_label="stock.csv", ts="2026-07-10T00:00:00Z")
    assert "$" in md
    assert CTA_PATH in md
    assert "a@b.com" in md and "stock.csv" in md


def test_followup_email_is_a_draft_never_sent():
    result = run_demo_scan(_stock_df())
    draft = render_followup_email(result, email="a@b.com", dataset_label="stock.csv")
    assert draft.startswith("Para: a@b.com")
    assert "Asunto:" in draft
    assert "BORRADOR" in draft
    assert "nunca envia correo automaticamente" in draft
    assert CTA_PATH in draft
