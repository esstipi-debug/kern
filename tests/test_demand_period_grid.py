"""The demand series must live on a complete period grid, zero-filled.

The defect this pins: a transactional export carries a row only where something
happened, so `_series_from_frame` used to hand the engine only the SKU's
demand-days. A spare part that sold three times in two years arrived as
`[5, 4, 6]` -- three CONSECUTIVE periods as far as every downstream model could
tell.

Two independent consequences, both silent:

  1. `average_demand_interval` is `size / count_nonzero`. With no zero rows it
     returns exactly 1.0 for every SKU in every real export, so `is_intermittent`
     is always False and the Croston/TSB branch is unreachable from production
     data. The Syntetos-Boylan segmentation collapses: every SKU reads "smooth".
  2. The per-period mean is computed over demand-days only, so
     `to_engine_inputs`' `forecast * periods_per_year` overstates annual demand
     by roughly the SKU's true ADI -- ~17x for the case above.

Both are worse than a crash: the output looks completely ordinary.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.forecastability import classify_series
from src.forecasting import average_demand_interval, is_intermittent
from src.sources import DataFrameDemandSource

_WEEKS = pd.date_range("2026-01-04", periods=12, freq="W")


def _frame(rows: list[tuple[int, str, float]]) -> pd.DataFrame:
    """rows = (week_index, product_id, quantity)."""
    return pd.DataFrame([
        {"date": _WEEKS[i], "product_id": pid, "quantity": q, "unit_cost": 10.0}
        for i, pid, q in rows
    ])


def test_missing_periods_are_zero_filled_not_dropped():
    df = _frame([(0, "SPARE", 5.0), (5, "SPARE", 4.0), (11, "SPARE", 6.0),
                 (0, "DENSE", 1.0), (11, "DENSE", 1.0)])

    series = DataFrameDemandSource(df, period_freq="W").demand_series("SPARE")

    assert len(series) == 12, "the grid must span the file, not the SKU's demand-days"
    assert list(series) == [5.0, 0, 0, 0, 0, 4.0, 0, 0, 0, 0, 0, 6.0]


def test_intermittent_demand_is_finally_visible_as_intermittent():
    """The headline regression: without zeros, ADI is 1.0 for everything."""
    df = _frame([(0, "SPARE", 5.0), (5, "SPARE", 4.0), (11, "SPARE", 6.0)])

    series = DataFrameDemandSource(df, period_freq="W").demand_series("SPARE")

    assert average_demand_interval(series) == pytest.approx(12 / 3)
    assert is_intermittent(series)
    assert classify_series("SPARE", series).quadrant in {"intermittent", "lumpy"}


def test_annual_demand_is_no_longer_overstated_by_the_adi():
    """3 sales totalling 15 units over 12 weeks is ~1.25 units/week, not ~5."""
    df = _frame([(0, "SPARE", 5.0), (5, "SPARE", 4.0), (11, "SPARE", 6.0)])

    series = DataFrameDemandSource(df, period_freq="W").demand_series("SPARE")

    assert float(np.mean(series)) == pytest.approx(15 / 12)


def test_grid_starts_at_the_skus_first_sale_not_the_files_first_date():
    """A SKU that started selling in week 8 did not have 'zero demand' in week 1 --
    it did not exist yet. Padding backwards would invent absence and drag the mean
    down, understating a genuinely new product."""
    df = _frame([(0, "OLD", 3.0), (11, "OLD", 3.0),
                 (8, "NEW", 10.0), (9, "NEW", 10.0)])

    series = DataFrameDemandSource(df, period_freq="W").demand_series("NEW")

    assert len(series) == 4                      # weeks 8..11, not 0..11
    assert list(series) == [10.0, 10.0, 0.0, 0.0]


def test_trailing_silence_after_the_last_sale_is_kept_as_real_zero_demand():
    """The mirror of the rule above: a SKU that stopped selling in week 2 really
    did sell zero in weeks 3-11. Truncating there would hide a dying SKU."""
    df = _frame([(0, "DYING", 8.0), (2, "DYING", 8.0), (11, "ALIVE", 1.0)])

    series = DataFrameDemandSource(df, period_freq="W").demand_series("DYING")

    assert len(series) == 12
    assert list(series[-3:]) == [0.0, 0.0, 0.0]


def test_same_date_rows_are_summed_into_one_period():
    """A raw CSV (CsvDemandSource) can carry two transactions on one day. Those are
    one period's demand, not two periods."""
    df = pd.DataFrame([
        {"date": _WEEKS[0], "product_id": "X", "quantity": 3.0, "unit_cost": 10.0},
        {"date": _WEEKS[0], "product_id": "X", "quantity": 4.0, "unit_cost": 10.0},
        {"date": _WEEKS[1], "product_id": "X", "quantity": 5.0, "unit_cost": 10.0},
    ])

    series = DataFrameDemandSource(df, period_freq="W").demand_series("X")

    assert list(series) == [7.0, 5.0]


def test_a_dense_series_is_completely_unchanged():
    """The no-op case that must stay a no-op: when every period already has a row,
    the grid changes nothing. This is what keeps the fix from silently moving every
    well-behaved SKU's numbers."""
    df = _frame([(i, "DENSE", float(i + 1)) for i in range(12)])

    series = DataFrameDemandSource(df, period_freq="W").demand_series("DENSE")

    assert list(series) == [float(i + 1) for i in range(12)]
    assert average_demand_interval(series) == 1.0
    assert not is_intermittent(series)
