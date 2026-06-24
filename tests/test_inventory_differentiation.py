"""Safety stock differentiated per SKU and self-correcting (pending points 1, 3, 4).

1. Service level per ABC-XYZ class -> per-SKU safety stock (A higher, C lower).
3. Observed lead time (e.g. from supplier scorecards) -> per-SKU risk period.
4. Stockout feedback: nudge the service level toward the target fill rate (closed loop).
"""

import pandas as pd

from jobs.inventory_optimization import run
from src.classification import classify_portfolio, service_levels
from src.safety_stock import tune_service_level
from src.supplier_scorecard import lead_times_by_supplier, lead_times_for_skus, score_supplier


def _demand_df() -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=16, freq="W")
    hi = [90, 110, 80, 120, 95, 105, 85, 115, 100, 100, 90, 110, 95, 105, 100, 100]
    lo = [4, 6, 3, 7, 5, 5, 4, 6, 5, 5, 4, 6, 5, 5, 4, 6]
    rows = []
    for d, h, low in zip(dates, hi, lo):
        rows.append({"date": d, "product_id": "HIGH", "quantity": float(h), "unit_cost": 50.0})
        rows.append({"date": d, "product_id": "LOW", "quantity": float(low), "unit_cost": 1.0})
    return pd.DataFrame(rows)


# -- Point 1: service level per class ------------------------------------------


def test_service_levels_from_classification_maps_by_abc():
    items = [
        {"product_id": "HIGH", "unit_cost": 50.0, "demand": [100] * 12},
        {"product_id": "LOW", "unit_cost": 1.0, "demand": [5] * 12},
    ]
    sl = service_levels(classify_portfolio(items))
    assert sl["HIGH"] == 0.98   # A class
    assert sl["LOW"] == 0.90    # C class


def test_run_differentiates_safety_stock_by_class():
    rep = run(_demand_df(), differentiate_by_class=True)
    by = {r.product_id: r for r in rep.recommendations}

    assert by["HIGH"].service_level == 0.98
    assert by["LOW"].service_level == 0.90
    assert by["HIGH"].z_factor > by["LOW"].z_factor      # higher service -> bigger buffer


def test_run_uses_global_service_level_by_default():
    rep = run(_demand_df())
    assert all(r.service_level == 0.95 for r in rep.recommendations)


# -- Point 3: observed lead time -----------------------------------------------


def test_lead_times_helpers_from_scorecards():
    cards = [score_supplier("ACME", [{"lead_time_days": 10.0}]),
             score_supplier("BETA", [{"lead_time_days": 4.0}])]
    assert lead_times_by_supplier(cards) == {"ACME": 10.0, "BETA": 4.0}
    per_sku = lead_times_for_skus(cards, {"HIGH": "ACME", "LOW": "BETA", "X": "UNKNOWN"})
    assert per_sku == {"HIGH": 10.0, "LOW": 4.0}          # unknown supplier dropped


def test_run_applies_per_sku_lead_time_override():
    rep = run(_demand_df(), lead_times={"HIGH": 9.0})
    by = {r.product_id: r for r in rep.recommendations}
    assert by["HIGH"].lead_periods == 9.0
    assert by["LOW"].lead_periods != 9.0                  # LOW keeps the default


# -- Point 4: stockout feedback loop -------------------------------------------


def test_tune_service_level_raises_when_understocked():
    assert tune_service_level(0.95, 0.80, 0.95) > 0.95    # observed below target -> raise


def test_tune_service_level_lowers_when_overstocked():
    assert tune_service_level(0.95, 0.99, 0.95) < 0.95    # observed above target -> relax


def test_tune_service_level_is_bounded():
    assert tune_service_level(0.95, 0.0, 0.95) <= 0.999
    assert tune_service_level(0.95, 1.0, 0.95) >= 0.50


def test_run_tunes_service_level_from_observed_fill():
    rep = run(_demand_df(), observed_fill_rates={"HIGH": 0.80}, target_fill_rate=0.95)
    by = {r.product_id: r for r in rep.recommendations}
    assert by["HIGH"].service_level > 0.95                # chronically short -> more buffer
    assert by["LOW"].service_level == 0.95                # no signal -> unchanged
