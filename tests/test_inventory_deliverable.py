"""Tests for the inventory-optimization -> deliverable adapter."""
from __future__ import annotations

from dataclasses import replace

from jobs.inventory_deliverable import build
from jobs.inventory_optimization import JobReport, SkuRecommendation
from src import order_fulfillment as of


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
    # "Cycle service level" now carries "(model target)": it is what the policy is
    # sized for, and the deck used to present it where a reader looks for what was
    # actually served. The two realized KPIs beside it are the answer to that.
    assert {"SKUs analyzed", "Recommended investment", "Cycle service level (model target)",
            "Realized fill rate (observed)", "Realized OTIF (observed)",
            "High-bias SKUs", "Intermittent SKUs"} <= names
    assert "Cycle service level" not in names
    assert all(k.rationale for k in d.kpis)


def test_budget_constraint_surfaces_finding_and_recommendation():
    d = build(_report(budget=25000.0, scale=0.7, feasible=False, final=25000.0))
    md = d.to_markdown()
    assert "Budget-constrained safety stock" in md
    assert "70%" in md
    assert "below the cycle-stock floor" in md  # infeasible-budget recommendation


def test_zero_demand_skip_excluded_from_concentration_finding():
    """A skipped-zero-demand recommendation always carries investment=0.0 (see
    jobs/inventory_optimization.py's run()) - it must never displace or clutter a
    real finding in the 'Largest investments' top-3 summary."""
    skipped = SkuRecommendation(
        product_id="ZERO-C", method="none", intermittent=False,
        forecast=0.0, error_std=0.0, bias=0.0, mae=0.0,
        policy_kind="skipped_zero_demand", order_quantity=None, order_up_to=None,
        reorder_point=0.0, safety_stock=0.0, z_factor=0.0,
        service_level=0.95, unit_cost=1.0, lead_periods=1.0,
        cycle_investment=0.0, ss_investment=0.0, investment=0.0, status="review",
    )
    report = JobReport(
        recommendations=[_rec("GOOD-A", 2793.0), skipped],
        params={"service_level": 0.95, "holding_rate": 0.25, "order_cost": 75.0, "periods_per_year": 52.0},
        requested_investment=2793.0, cycle_floor=2000.0, final_investment=2793.0,
        safety_stock_scale=1.0, feasible=True, budget=None,
        n_skus=2, n_at_risk=0, n_intermittent=0,
    )
    d = build(report)
    concentration = next(f for f in d.findings if "concentration" in f.title.lower())
    assert "ZERO-C" not in concentration.detail
    assert "GOOD-A" in concentration.detail


def test_markdown_renders_and_is_ascii_safe():
    md = build(_report(), prepared="2026-06-23").to_markdown()
    for section in ["## Executive summary", "## Key findings", "## KPIs",
                    "## Data sources", "## Coverage & handoff"]:
        assert section in md
    md.encode("cp1252")  # cp1252 console safety


def _served(pid: str, lines: list[dict]) -> SkuRecommendation:
    """A recommendation whose realized service was measured from order lines."""
    rec = _rec(pid, 10000.0)
    return replace(rec, realized_service=of.measure_from_order_lines(pid, lines))


def test_unmeasured_service_prints_not_measured_rather_than_a_zero():
    """The hand-built recommendations of _report() carry no realized service. A deck
    that answered '0%' there would be reporting a stockout nobody observed."""
    d = build(_report())
    by_name = {k.name: k.value for k in d.kpis}

    assert by_name["Realized fill rate (observed)"] == "not measured"
    assert by_name["Realized OTIF (observed)"] == "not measured"
    assert not any("did not serve" in f.title for f in d.findings)


def test_measured_service_reaches_the_kpis_and_names_the_broken_skus():
    lines = [{"ordered_qty": 100, "delivered_qty": 60, "unit_price": 9.0,
              "promised_date": "2026-03-02", "delivered_date": "2026-03-02"}]
    report = JobReport(
        recommendations=[_served("SKU-A", lines)],
        params={"service_level": 0.95, "holding_rate": 0.25, "order_cost": 75.0,
                "periods_per_year": 52.0},
        requested_investment=10000.0, cycle_floor=6000.0, final_investment=10000.0,
        safety_stock_scale=1.0, feasible=True, budget=None,
        n_skus=1, n_at_risk=0, n_intermittent=0,
    )

    d = build(report)
    by_name = {k.name: k.value for k in d.kpis}
    gap = next(f for f in d.findings if "did not serve" in f.title)

    assert by_name["Realized fill rate (observed)"] == "60%"
    assert by_name["Realized OTIF (observed)"] == "0%"      # short line, so not OTIF
    assert "40 units" in gap.detail
    assert "SKU-A" in gap.detail
    assert "$360.00" in gap.impact


def test_the_deck_never_prints_a_bare_service_level_next_to_a_realized_one():
    """The defect in one assertion: the model's target and the served number must be
    distinguishable on the page, and the data-source map must say which is which."""
    md = build(_report()).to_markdown()

    assert "Cycle service level (model target)" in md
    assert "Realized fill rate (observed)" in md
    assert "never a measurement of service" in md
    assert "cycle service level target" in md   # executive summary says target too
