"""The Methodology section must describe the run that happened, not a template.

Two hardcoded lines in jobs/deliverables.py claimed a methodology the engine had not
used for some time:

  - "forecast with simple exponential smoothing (or Croston...)" while
    src/forecasting.py:216 routes to AutoETS/TSB whenever [forecast] is installed;
  - "SS = z * sigma_e * sqrt(L)" while src/risk_period.py:40 actually computes
    sigma_x = sqrt(tau * sigma_d^2 + sigma_L^2 * mu_d^2) and sizes SS off *that*.

A client validating the numbers against the stated formula cannot reproduce them.
That is the same failure mode as a silently substituted sigma: the document says one
thing, the code did another, and nothing flags the gap.
"""

import numpy as np
import pandas as pd
import pytest

from jobs import deliverables, inventory_optimization
from src.demand_variability import safety_stock_risk_period
from src.policies import continuous_review_sq, periodic_review_rs
from src.safety_stock import safety_stock


def _demand(n_skus: int = 2, periods: int = 60) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    rows = []
    for s in range(n_skus):
        for t in range(periods):
            rows.append({
                "product_id": f"SKU-{s + 1}",
                "date": pd.Timestamp("2024-01-01") + pd.Timedelta(weeks=t),
                "quantity": float(max(0.0, 100 + 10 * rng.standard_normal())),
                "unit_cost": 12.0,
                "lead_time_days": 14.0,
            })
    return pd.DataFrame(rows)


# ---- the distribution actually used is on the result ------------------------

def test_safety_stock_result_records_the_distribution_it_assumed():
    assert safety_stock(20.0, 0.95, risk_periods=2.0).distribution == "normal"


def test_the_risk_period_sizer_records_normal_when_it_used_the_z_formula():
    result = safety_stock_risk_period(500.0, 40.0, 0.95, 4.0, distribution="normal")

    assert result.distribution == "normal"


def test_the_risk_period_sizer_records_gamma_when_it_fitted_a_gamma():
    result = safety_stock_risk_period(500.0, 120.0, 0.95, 4.0, distribution="gamma")

    assert result.distribution == "gamma"


def test_the_policy_carries_the_distribution_through():
    sq = continuous_review_sq(
        annual_demand=5200.0, mean_demand_per_period=100.0, demand_std_per_period=20.0,
        holding_cost_per_unit=3.0, fixed_order_cost=75.0, lead_time_periods=2.0,
        cycle_service_level=0.95, demand_distribution="gamma",
    )
    rs = periodic_review_rs(
        annual_demand=5200.0, mean_demand_per_period=100.0, demand_std_per_period=20.0,
        holding_cost_per_unit=3.0, fixed_order_cost=75.0, lead_time_periods=2.0,
        review_period=1.0, cycle_service_level=0.95,
    )

    assert sq.safety_stock.distribution == "gamma"
    assert rs.safety_stock.distribution == "normal"


# ---- the job records what it actually ran -----------------------------------

def test_each_recommendation_records_the_method_and_buffer_that_produced_it():
    report = inventory_optimization.run(_demand())

    for rec in report.recommendations:
        assert rec.method in {"ses", "croston", "moving_average", "auto_ets", "tsb"}
        assert rec.buffer_distribution in {"normal", "gamma"}
        assert rec.sigma_source in {"forecast_error", "demand_std_fallback", "unavailable"}


def test_an_unsupplied_lead_time_std_is_recorded_as_defaulted_not_as_measured():
    report = inventory_optimization.run(_demand())

    for rec in report.recommendations:
        assert rec.lead_time_std == pytest.approx(0.0)
        assert rec.lead_time_std_source == "defaulted_zero"


def test_a_supplied_lead_time_std_is_recorded_as_coming_from_the_client_file():
    report = inventory_optimization.run(_demand(), lead_time_stds={"SKU-1": 0.5})

    by_id = {r.product_id: r for r in report.recommendations}
    assert by_id["SKU-1"].lead_time_std == pytest.approx(0.5)
    assert by_id["SKU-1"].lead_time_std_source == "client_file"
    assert by_id["SKU-2"].lead_time_std_source == "defaulted_zero"


def test_a_supplied_lead_time_std_actually_widens_the_buffer():
    """If the report is going to name sigma_L, sigma_L had better reach the math."""
    flat = inventory_optimization.run(_demand())
    varied = inventory_optimization.run(_demand(), lead_time_stds={"SKU-1": 3.0})

    flat_ss = {r.product_id: r.safety_stock for r in flat.recommendations}
    varied_ss = {r.product_id: r.safety_stock for r in varied.recommendations}
    assert varied_ss["SKU-1"] > flat_ss["SKU-1"]
    assert varied_ss["SKU-2"] == pytest.approx(flat_ss["SKU-2"])


# ---- the written report describes that run ----------------------------------

def _methodology(report, tmp_path) -> str:
    """Just the Methodology section - a substring hit elsewhere proves nothing."""
    md = deliverables.write_report_md(report, tmp_path / "r.md").read_text(encoding="utf-8")
    return md.split("## Methodology")[1].split("## Assumptions")[0]


def test_the_methodology_names_the_forecast_methods_that_actually_ran(tmp_path):
    report = inventory_optimization.run(_demand())
    section = _methodology(report, tmp_path)

    used = {r.method for r in report.recommendations}
    for method in used:
        label = deliverables.METHOD_LABEL[method]
        assert label in section, f"report never names the {label} run it actually performed"


def test_the_methodology_does_not_claim_a_method_that_did_not_run(tmp_path):
    report = inventory_optimization.run(_demand())
    section = _methodology(report, tmp_path)

    used = {r.method for r in report.recommendations}
    for unused in set(deliverables.METHOD_LABEL) - used:
        label = deliverables.METHOD_LABEL[unused]
        assert label not in section, f"report claims {label}, which never ran"


def test_the_methodology_states_the_risk_period_formula_the_engine_uses(tmp_path):
    """`SS = z*sigma_e*sqrt(L)` is not what src/risk_period.py computes."""
    report = inventory_optimization.run(_demand())
    md = deliverables.write_report_md(report, tmp_path / "r.md").read_text(encoding="utf-8")

    assert "sigma_L" in md
    assert "tau" in md
    assert "z * sigma_e * sqrt(L)" not in md
    assert "z · σₑ · √L" not in md


def test_the_assumptions_state_the_lead_time_std_and_where_it_came_from(tmp_path):
    report = inventory_optimization.run(_demand())
    md = deliverables.write_report_md(report, tmp_path / "r.md").read_text(encoding="utf-8")

    assert "defaulted to 0" in md


def test_the_assumptions_say_when_the_lead_time_std_came_from_the_file(tmp_path):
    report = inventory_optimization.run(_demand(), lead_time_stds={"SKU-1": 0.5, "SKU-2": 0.5})
    md = deliverables.write_report_md(report, tmp_path / "r.md").read_text(encoding="utf-8")

    assert "from your file" in md
    assert "defaulted to 0" not in md


def test_the_assumptions_disclose_a_demand_std_fallback(tmp_path):
    """Ties item 1 to the document: a substituted sigma must reach the reader."""
    report = inventory_optimization.run(_demand())
    fallbacks = [r for r in report.recommendations if r.sigma_source == "demand_std_fallback"]
    md = deliverables.write_report_md(report, tmp_path / "r.md").read_text(encoding="utf-8")

    if fallbacks:
        assert "demand standard deviation" in md
    else:
        assert "forecast-error standard deviation" in md


def test_the_methodology_names_the_buffer_distribution_it_assumed(tmp_path):
    report = inventory_optimization.run(_demand())
    md = deliverables.write_report_md(report, tmp_path / "r.md").read_text(encoding="utf-8")

    assert "normal" in md.lower()
