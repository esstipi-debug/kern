"""Defects an adversarial review of 578e361 confirmed against the committed code.

Each test below reproduces one finding that survived independent refutation. They are
the same defect class the commit exists to close - a plausible number where there was
no measurement, or a document asserting something the code did not do - either left
behind by the commit or newly introduced by it.
"""

import numpy as np
import pandas as pd
import pytest

from jobs import deliverables, inventory_optimization
from jobs import landed_cost_job as lcj
from jobs import sourcing_job as srcj
from src.supplier_scorecard import score_supplier
from src.working_capital import working_capital


def _demand(n_skus: int = 2, periods: int = 60) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    return pd.DataFrame([
        {
            "product_id": f"SKU-{s + 1}",
            "date": pd.Timestamp("2024-01-01") + pd.Timedelta(weeks=t),
            "quantity": float(max(0.0, 100 + 10 * rng.standard_normal())),
            "unit_cost": 12.0,
            "lead_time_days": 14.0,
        }
        for s in range(n_skus) for t in range(periods)
    ])


# ---- CRITICAL: bool("No") is True -------------------------------------------

def test_a_yes_no_text_column_is_read_not_scored_as_a_perfect_delivery():
    """`bool("No")` is True, so a Yes/No export scored 100% OTIF over 60 records.

    "No" *does* have exactly one meaning, so the fix is to read it, not to refuse it.
    """
    deliveries = [{"on_time": "Yes", "in_full": "Yes"}] * 20 + [
        {"on_time": "No", "in_full": "No"}
    ] * 40

    s = score_supplier("Beta", deliveries)

    assert s.on_time_rate == pytest.approx(20 / 60)
    assert s.otif == pytest.approx(20 / 60)
    assert s.evaluable.otif == 60


def test_a_status_word_is_refused_rather_than_read_as_a_successful_delivery():
    with pytest.raises(ValueError, match="yes/no"):
        score_supplier("Beta", [{"on_time": "Cancelled"}])


@pytest.mark.parametrize("truthy,falsy", [
    ("yes", "no"), ("Y", "N"), ("true", "false"), ("1", "0"), ("TRUE", "FALSE"),
])
def test_the_yes_no_words_that_do_have_one_meaning_are_read_correctly(truthy, falsy):
    s = score_supplier("Acme", [{"on_time": truthy}, {"on_time": falsy}])

    assert s.on_time_rate == pytest.approx(0.5)
    assert s.evaluable.on_time == 2


def test_a_numeric_flag_column_still_works():
    s = score_supplier("Acme", [{"on_time": 1}, {"on_time": 0}, {"on_time": True}])

    assert s.on_time_rate == pytest.approx(2 / 3)


def test_a_days_late_measurement_is_refused_instead_of_read_as_a_flag():
    """`bool(3)` is True, so a 3-day-late delivery scored as on time."""
    with pytest.raises(ValueError, match="0/1"):
        score_supplier("Acme", [{"on_time": 0}, {"on_time": 3}, {"on_time": 7}])


def test_a_text_column_is_refused_end_to_end_through_the_job():
    df = pd.DataFrame({
        "supplier": ["Alpha"] * 2 + ["Beta"] * 2,
        "on_time": ["Delivered", "Cancelled", "Delivered", "Delivered"],
        "lead_time_days": [7, 8, 12, 11],
    })

    with pytest.raises(ValueError, match="yes/no"):
        srcj.score_suppliers(df, supplier_col="supplier", require_all_criteria=False,
                             on_time_col="on_time", lead_col="lead_time_days")


# ---- CRITICAL: the fallback-disclosure test was vacuous ----------------------

def _assumptions(report, tmp_path) -> str:
    md = deliverables.write_report_md(report, tmp_path / "r.md").read_text(encoding="utf-8")
    return md.split("## Assumptions")[1]


def test_the_assumptions_section_itself_discloses_a_substituted_sigma(tmp_path):
    """The earlier test matched the unconditional Methodology bullet, so it never failed."""
    report = inventory_optimization.run(_demand())
    fallbacks = [r for r in report.recommendations if r.sigma_source == "demand_std_fallback"]
    section = _assumptions(report, tmp_path)

    if fallbacks:
        assert "raw demand standard deviation" in section
        assert f"{len(fallbacks)} of {len(report.recommendations)} SKU(s)" in section
    else:
        assert "every SKU was sized on the forecast-error standard deviation" in section
        assert "raw demand standard deviation" not in section


# ---- HIGH: the reorder-point formula the report states -----------------------

def test_the_methodology_states_the_reorder_point_the_job_actually_computes(tmp_path):
    """The job uses mu_d*L + SS (lead only), not mu_x + SS (which is L+R for (R,S))."""
    report = inventory_optimization.run(_demand())
    md = deliverables.write_report_md(report, tmp_path / "r.md").read_text(encoding="utf-8")
    section = md.split("## Methodology")[1].split("## Assumptions")[0]

    assert "`s = mu_x + SS`" not in section
    assert "mu_d * L + SS" in section


def test_the_stated_reorder_point_reproduces_the_number_in_the_table():
    report = inventory_optimization.run(_demand())

    for rec in report.recommendations:
        expected = rec.forecast * rec.lead_periods + rec.safety_stock
        assert rec.reorder_point == pytest.approx(expected)


# ---- HIGH: a blank optional cost leg must not abort the job ------------------

def test_a_blank_optional_cost_leg_does_not_abort_the_landed_cost_job(tmp_path):
    """`require_finite(insurance)` turned a blank optional cell into an internal error."""
    csv = tmp_path / "shipments.csv"
    pd.DataFrame({
        "sku": ["A", "B"],
        "unit_cost": [10.0, 20.0],
        "qty": [100.0, 50.0],
        "freight": [500.0, None],      # blank optional leg
        "insurance": [None, 40.0],
    }).to_csv(csv, index=False)

    report = lcj.run(lcj.prepare(str(csv), {}))

    assert report.n_lines == 2
    assert report.total_freight == pytest.approx(500.0)
    assert report.total_insurance == pytest.approx(40.0)


# ---- MEDIUM: findings/Excel still named Croston for a TSB run ----------------

def test_the_findings_do_not_name_croston_for_a_sku_forecast_with_tsb(tmp_path):
    intermittent = pd.DataFrame([
        {
            "product_id": "SPARE-1",
            "date": pd.Timestamp("2024-01-01") + pd.Timedelta(weeks=t),
            "quantity": float(v),
            "unit_cost": 40.0,
            "lead_time_days": 21.0,
        }
        for t, v in enumerate([0, 0, 5, 0, 0, 0, 8, 0, 0, 6] * 5)
    ])
    report = inventory_optimization.run(intermittent)
    md = deliverables.write_report_md(report, tmp_path / "r.md").read_text(encoding="utf-8")

    used = {r.method for r in report.recommendations}
    if "croston" not in used:
        assert "via Croston" not in md


# ---- MEDIUM: the service level the Assumptions state -------------------------

def test_the_assumptions_do_not_state_one_service_level_when_skus_were_differentiated(tmp_path):
    report = inventory_optimization.run(_demand(), service_levels={"SKU-1": 0.99, "SKU-2": 0.90})
    section = _assumptions(report, tmp_path)

    assert "99.0%" in section and "90.0%" in section


def test_a_single_service_level_is_still_stated_plainly(tmp_path):
    report = inventory_optimization.run(_demand(), service_level=0.95)
    section = _assumptions(report, tmp_path)

    assert "95.0%" in section


# ---- MEDIUM: a NaN disruption magnitude ---------------------------------------

def test_a_non_finite_disruption_magnitude_is_refused():
    """Every sibling knob got require_finite; this one was missed."""
    from jobs import digital_twin_job as dtj

    with pytest.raises(ValueError, match="finite"):
        dtj.prepare({
            "disruption": "demand_surge", "disruption_magnitude": float("nan"),
            "n_stores": 2, "n_products": 1, "periods": 30,
        })


# ---- MEDIUM: cash_to_cash returning None must not crash a formatter ----------

def test_working_capital_refuses_a_non_finite_cycle_input():
    """cash_to_cash now returns None for non-finite; the WorkingCapital formatters
    take a float, so refuse at the door rather than hand them None."""
    with pytest.raises(ValueError, match="finite"):
        working_capital(revenue=1_000_000.0, cogs=600_000.0,
                        dio=float("nan"), dso=45.0, dpo=30.0)


def test_working_capital_still_computes_a_normal_cycle():
    wc = working_capital(revenue=1_000_000.0, cogs=600_000.0, dio=60.0, dso=45.0, dpo=30.0)

    assert wc.cash_conversion_cycle == pytest.approx(75.0)


# ---- MEDIUM/LOW: the blank-denominator count, and the price guard ------------

def test_the_blank_denominator_message_counts_each_bad_row_once():
    from jobs import financial_kpis_job as fkj

    df = pd.DataFrame({
        "product_id": ["A", "B", "C"],
        "cogs": [1000.0, None, 2000.0],          # exactly 1 bad row
        "avg_inventory_value": [250.0, 500.0, 400.0],
    })

    with pytest.raises(ValueError, match=r"^1 row\(s\)"):
        fkj.prepare_records(df)


def test_an_unpriced_supplier_is_not_awarded_the_ideal_cost():
    """`prices.get(supplier, 0.0)` made a missing price the best possible bid."""
    df = pd.DataFrame({
        "supplier": ["Alpha"] * 2 + ["Beta"] * 2,
        "lead_time_days": [7, 8, 12, 11],
        "unit_price": [10.0, 10.0, None, None],   # Beta has no price at all
    })
    cards, prices = srcj.score_suppliers(
        df, supplier_col="supplier", require_all_criteria=False,
        lead_col="lead_time_days", price_col="unit_price")

    assert "Beta" not in prices
    report = srcj.run(cards, prices)
    assert "price" not in {c.name for c in report.criteria}
    assert "price" in report.unmeasured
