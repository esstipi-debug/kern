"""Tests for the inventory-optimization -> deliverable adapter."""
from __future__ import annotations

from jobs.inventory_deliverable import build
from jobs.inventory_optimization import JobReport, SkuRecommendation


def _rec(pid: str, investment: float, *, status: str = "ok", intermittent: bool = False) -> SkuRecommendation:
    return SkuRecommendation(
        product_id=pid, method="ses", intermittent=intermittent, forecast=10.0, error_std=2.0,
        bias=3.0 if status == "high_bias" else 0.0, mae=1.5, policy_kind="(s, Q)",
        order_quantity=50.0, order_up_to=None, reorder_point=30.0, safety_stock=12.0,
        z_factor=1.64, service_level=0.95, unit_cost=8.0, lead_periods=2.0, cycle_investment=investment * 0.6,
        ss_investment=investment * 0.4, investment=investment, status=status,
    )


def _report(*, budget=None, scale=1.0, feasible=True, final=48000.0) -> JobReport:
    recs = [_rec("SKU-A", 20000.0, status="high_bias"),
            _rec("SKU-B", 18000.0, intermittent=True),
            _rec("SKU-C", 10000.0)]
    return JobReport(
        recommendations=recs,
        params={"service_level": 0.95, "holding_rate": 0.25, "order_cost": 75.0, "periods_per_year": 52.0},
        requested_investment=48000.0, cycle_floor=30000.0, final_investment=final,
        safety_stock_scale=scale, feasible=feasible, budget=budget,
        n_skus=3, n_at_risk=1, n_intermittent=1,
    )


def test_summary_reflects_report_numbers():
    d = build(_report(), client="Acme DTC", prepared="2026-06-23")
    assert "3 SKUs" in d.summary
    assert "$48,000" in d.summary
    assert "95%" in d.summary
    assert d.client == "Acme DTC"


def test_findings_flag_bias_intermittent_and_concentration():
    d = build(_report())
    titles = " ".join(f.title for f in d.findings)
    assert "high forecast bias" in titles
    assert "intermittent-demand" in titles
    assert "concentration" in titles.lower()
    # top SKU by investment appears in the concentration finding
    assert any("SKU-A" in f.detail for f in d.findings)


def test_kpis_present_with_rationale():
    d = build(_report())
    names = {k.name for k in d.kpis}
    assert {"SKUs analyzed", "Recommended investment", "Cycle service level",
            "High-bias SKUs", "Intermittent SKUs"} <= names
    assert all(k.rationale for k in d.kpis)


def test_budget_constraint_surfaces_finding_and_recommendation():
    d = build(_report(budget=25000.0, scale=0.7, feasible=False, final=25000.0))
    md = d.to_markdown()
    assert "Budget-constrained safety stock" in md
    assert "70%" in md
    assert "below the cycle-stock floor" in md  # infeasible-budget recommendation


def test_markdown_renders_and_is_ascii_safe():
    md = build(_report(), prepared="2026-06-23").to_markdown()
    for section in ["## Executive summary", "## Key findings", "## KPIs",
                    "## Data sources", "## Coverage & handoff"]:
        assert section in md
    md.encode("cp1252")  # cp1252 console safety
