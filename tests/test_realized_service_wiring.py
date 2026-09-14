"""Acceptance tests for realized service reaching the inventory deliverable.

The defect: Kern reported ``service_level`` - the TARGET its policy was sized to -
and nothing else about service. Fed the fill rate a client had actually observed,
the job consumed it to nudge that target and threw the fact away, so no field of
the deliverable ever answered "what did we serve?". A client comparing our deck
against their own numbers found nothing to compare.

These tests pin the fix at the seam the client's data enters: order lines and a
reported fill rate come OUT as realized service, labelled with where they came
from, and the model's own numbers stay in their own differently-named fields.
"""
from __future__ import annotations

import pandas as pd
import pytest

from jobs import inventory_optimization
from src import order_fulfillment as of


def _frame(pid="A", n=24):
    return pd.DataFrame({
        "date": pd.date_range("2024-01-07", periods=n, freq="W"),
        "product_id": [pid] * n,
        "quantity": [10, 12, 9, 11, 13, 10, 12, 11] * (n // 8),
        "unit_cost": [5.0] * n,
        "lead_time_days": [14.0] * n,
    })


def _order_lines(pid="A"):
    return pd.DataFrame([
        {"product_id": pid, "ordered_qty": 100, "delivered_qty": 60,
         "promised_date": "2024-03-04", "delivered_date": "2024-03-04", "unit_price": 9.0},
        {"product_id": pid, "ordered_qty": 100, "delivered_qty": 100,
         "promised_date": "2024-03-11", "delivered_date": "2024-03-15", "unit_price": 9.0},
        {"product_id": pid, "ordered_qty": 100, "delivered_qty": 100,
         "promised_date": "2024-03-18", "delivered_date": "2024-03-18", "unit_price": 9.0},
    ])


def test_a_reported_fill_rate_comes_back_out_as_a_reported_fill_rate():
    report = inventory_optimization.run(_frame(), observed_fill_rates={"A": 0.60})

    service = report.recommendations[0].realized_service
    assert service.fill_rate == pytest.approx(0.60)
    assert service.provenance == of.PROVENANCE_CLIENT_REPORTED
    assert service.lost_sales_units > 0
    assert service.lost_sales_value > 0


def test_a_scalar_fill_rate_never_invents_an_otif_or_a_stockout_day():
    report = inventory_optimization.run(_frame(), observed_fill_rates={"A": 0.60})

    service = report.recommendations[0].realized_service
    assert service.otif is None
    assert service.days_out_of_stock is None


def test_order_lines_produce_an_observed_otif_and_measured_stockout_days():
    report = inventory_optimization.run(_frame(), order_lines=_order_lines())

    service = report.recommendations[0].realized_service
    assert service.provenance == of.PROVENANCE_OBSERVED
    assert service.fill_rate == pytest.approx(260 / 300)
    assert service.otif == pytest.approx(1 / 3)      # one short, one late, one clean
    assert service.days_out_of_stock == 1
    assert service.lost_sales_value == pytest.approx(40 * 9.0)


def test_order_lines_beat_a_reported_rate_and_a_stock_history_beats_the_lower_bound():
    report = inventory_optimization.run(
        _frame(), order_lines=_order_lines(),
        observed_fill_rates={"A": 0.60}, stockout_days={"A": 7},
    )

    service = report.recommendations[0].realized_service
    assert service.provenance == of.PROVENANCE_OBSERVED
    assert service.fill_rate == pytest.approx(260 / 300)   # measured, not the reported 0.60
    assert service.days_out_of_stock == 7


def test_without_any_client_fact_the_service_is_not_measured_not_zero():
    report = inventory_optimization.run(_frame())

    service = report.recommendations[0].realized_service
    assert service.provenance == of.PROVENANCE_NOT_MEASURED
    assert service.fill_rate is None
    assert service.otif is None
    assert service.days_out_of_stock is None
    assert service.lost_sales_value is None


def test_realized_service_and_the_model_target_are_different_fields_with_different_values():
    """The whole point. Three service numbers on one line, none of them confusable:
    the policy's target, the beta that policy implies, and what actually happened."""
    rec = inventory_optimization.run(
        _frame(), service_level=0.95, observed_fill_rates={"A": 0.60}
    ).recommendations[0]

    assert rec.service_level == pytest.approx(0.985)        # target, tuned - a goal
    assert rec.realized_service.fill_rate == pytest.approx(0.60)   # fact
    assert rec.model_implied_beta is not None
    assert rec.model_implied_beta != pytest.approx(rec.realized_service.fill_rate)
    assert rec.model_implied_beta != pytest.approx(rec.service_level)


def test_the_model_implied_beta_is_not_named_like_a_realized_measurement():
    """A column mapping that greps for 'fill_rate' must not pick up a model number."""
    fields = set(inventory_optimization.SkuRecommendation.__dataclass_fields__)
    assert "model_implied_beta" in fields
    assert "fill_rate" not in "model_implied_beta"
    assert of.PROVENANCE_MODEL_IMPLIED == "model_implied"


def test_service_is_still_measured_for_a_sku_the_policy_refused_to_size():
    """Too little history to size a buffer does not unmake last quarter's shortfall."""
    report = inventory_optimization.run(
        _frame(n=8), min_periods=24, observed_fill_rates={"A": 0.60}
    )

    rec = report.recommendations[0]
    assert rec.policy_kind == "insufficient_history"
    assert rec.realized_service.fill_rate == pytest.approx(0.60)
    assert rec.model_implied_beta is None


def test_a_fill_rate_outside_zero_one_is_refused_rather_than_reported():
    with pytest.raises(ValueError, match="fill rate lives in"):
        inventory_optimization.run(_frame(), observed_fill_rates={"A": 1.3})


def test_the_tuning_loop_still_sees_the_observed_fill_rate():
    """Reporting the fact must not stop it from also closing the policy loop."""
    report = inventory_optimization.run(
        _frame(), service_level=0.95, observed_fill_rates={"A": 0.80}, target_fill_rate=0.95
    )

    assert report.recommendations[0].service_level == pytest.approx(0.965)
