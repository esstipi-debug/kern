"""``nan > 0`` is False, so a NaN silently takes the degenerate branch.

A repo-wide audit of the 78 ``value = num / den if den > 0 else FALLBACK`` sites found
12 where a NaN can actually reach the guard *and* the fallback is then rendered to a
user as a measured fact. Two are covered elsewhere (src/forecasting.py:57 in
tests/test_forecasting_nan_degradation.py, src/supplier_scorecard.py:36 in
tests/test_supplier_scorecard_evaluability.py); the rest are here.

The fallbacks were not neutral. They were the number a well-run operation reports:
100% fill rate, 0% defects, zero cost per unit, infinite inventory turns. That is what
makes the degradation silent - nothing downstream looks wrong.

The fix everywhere is the same: test ``math.isfinite`` explicitly, and refuse a value
that is not a number instead of letting it choose the flattering branch. A *genuine*
zero still takes the degenerate branch, because that is a real measurement.
"""

import math

import numpy as np
import pandas as pd
import pytest

from jobs import digital_twin_job as dtj
from jobs import facility_location_job as flj
from jobs import landed_cost_job as lcj
from src.classification import classify_portfolio
from src.digital_twin import DemandProfile, NodeSpec, simulate_network
from src.landed_cost import landed_cost
from src.multi_echelon import evaluate_serial_allocation
from src.reverse_logistics import ReturnLine, recovery_rate

NAN = float("nan")


# ---- src/classification.py:80 -----------------------------------------------
# A blank demand cell made cv=inf -> class 'Z', and the NaN annual value poisoned
# the cumulative share so every later SKU fell to 'C'. The deck then printed
# "hold nan% of annual value" and recommended discontinuing the SKU.

def test_a_blank_demand_cell_is_refused_instead_of_classifying_the_sku_as_erratic():
    items = [
        {"product_id": "A", "unit_cost": 5.0, "demand": [50.0, NAN, 55.0]},
        {"product_id": "B", "unit_cost": 5.0, "demand": [10.0, 12.0, 11.0]},
    ]

    with pytest.raises(ValueError, match="finite"):
        classify_portfolio(items)


def test_a_blank_unit_cost_is_refused_instead_of_poisoning_every_abc_share():
    items = [{"product_id": "A", "unit_cost": NAN, "demand": [50.0, 52.0, 55.0]}]

    with pytest.raises(ValueError, match="finite"):
        classify_portfolio(items)


def test_a_genuinely_zero_demand_sku_still_classifies_as_erratic():
    """A real all-zero history *is* infinitely variable. That branch must survive."""
    items = [
        {"product_id": "DEAD", "unit_cost": 5.0, "demand": [0.0, 0.0, 0.0]},
        {"product_id": "LIVE", "unit_cost": 5.0, "demand": [10.0, 12.0, 11.0]},
    ]

    result = {c.product_id: c for c in classify_portfolio(items)}

    assert result["DEAD"].xyz == "Z"
    assert math.isinf(result["DEAD"].cv)


# ---- src/multi_echelon.py:84,86 ---------------------------------------------
# A NaN risk period made safety stock 0.0 at that echelon: no buffer, no warning.

def test_a_non_finite_risk_period_is_refused_not_silently_buffered_to_zero():
    with pytest.raises(ValueError, match="finite"):
        evaluate_serial_allocation(
            risk_periods=(2.0, NAN),
            lead_times=[1.0, 2.0],
            mean_demand_per_period=100.0,
            demand_std_per_period=20.0,
            holding_costs=[1.0, 2.0],
            cycle_service_level=0.95,
        )


def test_a_genuinely_zero_risk_period_still_carries_no_safety_stock():
    """An echelon with no exposure legitimately needs no buffer."""
    result = evaluate_serial_allocation(
        risk_periods=(0.0, 3.0),
        lead_times=[1.0, 2.0],
        mean_demand_per_period=100.0,
        demand_std_per_period=20.0,
        holding_costs=[1.0, 2.0],
        cycle_service_level=0.95,
    )

    assert result.nodes[0].safety_stock == pytest.approx(0.0)
    assert result.nodes[1].safety_stock > 0


# ---- src/landed_cost.py:52 + jobs/landed_cost_job.py:120 --------------------
# A NaN quantity made the per-unit landed cost 0.00 - a free import.

def test_a_non_finite_quantity_is_refused_not_costed_at_zero_per_unit():
    with pytest.raises(ValueError, match="finite"):
        landed_cost(unit_cost=10.0, qty=NAN, freight=500.0, duty_rate=0.05)


def test_a_non_finite_unit_cost_is_refused():
    with pytest.raises(ValueError, match="finite"):
        landed_cost(unit_cost=NAN, qty=100.0, freight=500.0)


def test_a_genuinely_zero_quantity_lands_at_zero_per_unit():
    result = landed_cost(unit_cost=10.0, qty=0.0, freight=0.0)

    assert result.per_unit == pytest.approx(0.0)


def test_a_blank_quantity_in_the_csv_is_refused_before_the_uplift_is_reported(tmp_path):
    """`landed_uplift_pct` of 0.0% reads as "importing costs us nothing extra"."""
    csv = tmp_path / "shipments.csv"
    pd.DataFrame({
        "sku": ["A", "B"],
        "unit_cost": [10.0, 20.0],
        "qty": [100.0, None],
        "freight": [500.0, 300.0],
    }).to_csv(csv, index=False)

    with pytest.raises(ValueError, match="finite|qty"):
        lcj.run(lcj.prepare(str(csv), {}))


# ---- src/reverse_logistics.py:111 -------------------------------------------

def test_a_non_finite_return_value_is_refused_not_reported_as_zero_recovery():
    lines = [ReturnLine(product_id="A", returned_units=NAN, reason="damaged", unit_cost=25.0, resale_value=18.0)]

    with pytest.raises(ValueError, match="finite"):
        recovery_rate([], lines)


# ---- src/digital_twin.py:377,389 + jobs/digital_twin_job.py:216,219 ---------
# The worst fallback in the repo: a NaN demand trace reported a 100% fill rate.

def test_a_non_finite_demand_parameter_is_refused_not_simulated_at_a_perfect_fill_rate():
    with pytest.raises(ValueError, match="finite"):
        simulate_network(
            nodes=[
                NodeSpec(name="SUP", kind="supplier", supplier=None),
                NodeSpec(name="DC", kind="dc", supplier="SUP"),
                NodeSpec(name="STORE", kind="store", supplier="DC"),
            ],
            demand=DemandProfile(base=100.0, trend=NAN),
            periods=20,
            seed=1,
        )


def test_a_non_finite_twin_parameter_is_refused_by_the_job_too():
    with pytest.raises(ValueError, match="finite"):
        dtj.prepare({"trend": NAN, "n_stores": 2, "n_products": 1, "periods": 30})


def test_a_healthy_twin_still_reports_a_real_fill_rate():
    payload = dtj.prepare({"n_stores": 2, "n_products": 1, "periods": 30})
    report = dtj.run(payload)

    assert 0.0 <= report.network_fill_rate <= 1.0
    assert math.isfinite(report.network_fill_rate)


# ---- jobs/facility_location_job.py:99 ---------------------------------------
# A NaN current site reported a 0% saving: "your warehouse is already optimal".

def test_a_non_finite_current_site_is_refused_not_reported_as_a_zero_percent_saving():
    df = pd.DataFrame({"name": ["A", "B"], "x": [0.0, 10.0], "y": [0.0, 10.0], "weight": [1.0, 1.0]})

    with pytest.raises(ValueError, match="finite"):
        flj.prepare_records(df, {"current_x": NAN, "current_y": 0.0})


def test_a_blank_coordinate_is_refused():
    df = pd.DataFrame({"name": ["A", "B"], "x": [0.0, None], "y": [0.0, 10.0], "weight": [1.0, 1.0]})

    with pytest.raises(ValueError, match="finite"):
        flj.prepare_records(df, {})


def test_a_real_current_site_still_reports_its_saving():
    df = pd.DataFrame({"name": ["A", "B"], "x": [0.0, 10.0], "y": [0.0, 10.0], "weight": [1.0, 1.0]})

    report = flj.run(flj.prepare_records(df, {"current_x": 100.0, "current_y": 100.0}))

    assert report.saving_pct is not None and report.saving_pct > 0


# ---- the guard itself --------------------------------------------------------

def test_nan_really_does_lose_the_greater_than_zero_test():
    """The premise of every case above, asserted once so the reason is on the record."""
    assert (NAN > 0) is False
    assert (NAN <= 0) is False
    assert not math.isfinite(NAN)
    assert not np.isfinite(NAN)
