"""Acceptance test for the demand_distribution wiring (audit fix 4a).

``src/demand_variability.py`` implements an adaptive switch: with
``distribution='auto'`` it fits a gamma instead of a normal when the observed
skewness exceeds the coefficient of variation (Vandeput ch. 9). The whole gamma
family in ``src/distributions.py`` hangs off it. ``jobs/inventory_optimization``
passed neither ``demand_distribution`` nor ``observed_skewness``, so the switch
could never fire and retail demand -- which is right-skewed -- was always sized
on a symmetric normal.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy import stats as sps

from src.policies import continuous_review_sq

REPO = Path(__file__).resolve().parents[1]
PERIODS_PER_YEAR = 52.0
LEAD_PERIODS = 14.0 * PERIODS_PER_YEAR / 365.0
SERVICE_LEVEL = 0.95


@pytest.fixture(scope="module")
def skewed_panel():
    """The audit's panel: SKUs with enough history to estimate skewness."""
    canon = pd.read_csv(REPO / "data" / "kaggle" / "superstore_canonical.csv")
    g = canon.groupby("product_id")["quantity"]
    stats = pd.DataFrame({"mu": g.mean(), "sd": g.std(ddof=1), "n": g.count(),
                          "total": g.sum()}).dropna()
    stats = stats[(stats.n >= 8) & (stats.mu > 0) & (stats.sd > 0)]
    stats["skw"] = g.apply(lambda s: float(sps.skew(s)))
    return stats


def _ss(row, **kwargs):
    return continuous_review_sq(
        annual_demand=float(row["total"]),
        mean_demand_per_period=float(row["mu"]),
        demand_std_per_period=float(row["sd"]),
        holding_cost_per_unit=0.25,
        fixed_order_cost=75.0,
        lead_time_periods=LEAD_PERIODS,
        cycle_service_level=SERVICE_LEVEL,
        **kwargs,
    ).safety_stock.safety_stock


def test_audit_delta_gamma_switch(skewed_panel):
    """The audit's headline: 71.3% of SKUs switch to gamma, +9.6% total buffer."""
    assert len(skewed_panel) == 335

    normal, auto = [], []
    for _pid, row in skewed_panel.iterrows():
        normal.append(_ss(row))
        auto.append(_ss(row, demand_distribution="auto",
                        observed_skewness=float(row["skw"])))
    normal_arr, auto_arr = np.array(normal), np.array(auto)

    switched = int((np.abs(auto_arr - normal_arr) > 1e-9).sum())
    assert switched == 239
    assert switched / len(skewed_panel) == pytest.approx(0.713, abs=0.001)

    pct = (auto_arr.sum() / normal_arr.sum() - 1) * 100
    assert pct == pytest.approx(9.6, abs=0.1)

    on_switched = (auto_arr - normal_arr)[np.abs(auto_arr - normal_arr) > 1e-9]
    base = normal_arr[np.abs(auto_arr - normal_arr) > 1e-9]
    assert np.median(on_switched / base * 100) == pytest.approx(12.0, abs=0.1)


def test_flat_demand_yields_no_skewness_and_no_warning(recwarn):
    """Constant demand is not skewed; scipy must not be asked and must not warn."""
    from jobs.inventory_optimization import _observed_skewness

    assert _observed_skewness([5.0] * 12) is None
    assert _observed_skewness([5.0, 5.0]) is None            # too short
    assert _observed_skewness([5.0, 5.0 + 1e-15, 5.0]) is None  # near-constant
    assert not [w for w in recwarn if "cancellation" in str(w.message)]
    # a genuinely skewed series still reports
    assert _observed_skewness([1, 1, 1, 1, 9]) > 0


def test_job_uses_the_adaptive_distribution():
    """The job must hand the engine a skewness so 'auto' can act on it."""
    from jobs import inventory_optimization

    # a deliberately right-skewed history: mostly small, occasional spike
    base = [2, 3, 2, 4, 3, 2, 3, 2, 4, 3, 2, 3, 2, 3, 4, 2, 3, 2, 3, 40]
    frame = pd.DataFrame({
        "date": pd.date_range("2024-01-07", periods=len(base), freq="W"),
        "product_id": ["SKEWED"] * len(base),
        "quantity": base,
        "unit_cost": [10.0] * len(base),
        "lead_time_days": [14.0] * len(base),
    })
    assert sps.skew(np.array(base, dtype=float)) > 1.0, "fixture must be skewed"

    report = inventory_optimization.run(frame, periods_per_year=PERIODS_PER_YEAR)
    rec = report.recommendations[0]

    # reproduce the same policy call with a symmetric normal; the wired job must differ
    from src.sources import DataFrameDemandSource

    meta = DataFrameDemandSource(frame, periods_per_year=PERIODS_PER_YEAR).metadata("SKEWED")
    from src.forecasting import forecast_demand

    fc = forecast_demand(frame["quantity"].to_numpy(dtype=float))
    inputs = fc.to_engine_inputs(periods_per_year=PERIODS_PER_YEAR)
    normal_ss = continuous_review_sq(
        annual_demand=inputs["annual_demand"],
        mean_demand_per_period=inputs["mean_demand_per_period"],
        demand_std_per_period=inputs["demand_std_per_period"],
        holding_cost_per_unit=max(0.25 * meta.mean_unit_cost, 1e-6),
        fixed_order_cost=75.0,
        lead_time_periods=meta.lead_time_periods,
        cycle_service_level=0.95,
        lead_time_std=meta.lead_time_std_periods,
    ).safety_stock.safety_stock

    assert rec.safety_stock != pytest.approx(normal_ss), (
        "job still sizes the buffer on a symmetric normal"
    )
