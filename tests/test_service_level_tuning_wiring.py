"""Acceptance test for the tune_service_level step (audit fix 4b).

``tune_service_level`` defaults to ``step=0.5``, a half-point of service level per
point of fill-rate gap. The job never passed a step, so the closed loop
overcorrected: a 15-point fill-rate shortfall drove the cycle service level past
1.0 and into the ``hi=0.999`` clamp, which is a 99.9% target for every chronically
short SKU regardless of how large the gap actually was.
"""
from __future__ import annotations

import pandas as pd
import pytest

from jobs import inventory_optimization
from src.safety_stock import tune_service_level


def _frame(pid="A", n=24):
    return pd.DataFrame({
        "date": pd.date_range("2024-01-07", periods=n, freq="W"),
        "product_id": [pid] * n,
        "quantity": [10, 12, 9, 11, 13, 10, 12, 11] * (n // 8),
        "unit_cost": [5.0] * n,
        "lead_time_days": [14.0] * n,
    })


def test_engine_step_behaviour_is_unchanged():
    """The engine keeps its documented default; only the caller's step changes."""
    assert tune_service_level(0.95, 0.80, 0.95) == pytest.approx(0.999)   # clamped
    assert tune_service_level(0.95, 0.80, 0.95, step=0.1) == pytest.approx(0.965)


def test_job_no_longer_slams_service_level_to_the_clamp():
    """A 15-point shortfall must nudge to 0.965, not saturate at 0.999."""
    report = inventory_optimization.run(
        _frame(),
        service_level=0.95,
        observed_fill_rates={"A": 0.80},
        target_fill_rate=0.95,
    )
    rec = report.recommendations[0]
    assert rec.service_level == pytest.approx(0.965)
    assert rec.service_level != pytest.approx(0.999)


def test_job_still_relaxes_when_over_serving():
    """Above target the loop must still free capital, proportionally."""
    report = inventory_optimization.run(
        _frame(),
        service_level=0.95,
        observed_fill_rates={"A": 0.99},
        target_fill_rate=0.95,
    )
    assert report.recommendations[0].service_level == pytest.approx(0.946)


def test_tuning_step_is_a_named_constant():
    assert inventory_optimization.SERVICE_LEVEL_TUNING_STEP == 0.1
