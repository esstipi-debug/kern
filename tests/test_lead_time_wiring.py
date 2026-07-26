"""Acceptance test for the lead_time_std wiring (audit fix 1).

``ProductMetadata.lead_time_std_periods`` existed but was never written and never
read: ``_metadata_from_frame`` built the metadata without it, intake never carried
the column, and ``jobs.inventory_optimization`` never passed it to the policy. The
engine math (``src/risk_period.py``: sigma_x^2 = tau*sigma_d^2 + sigma_LT^2*mu_d^2)
was already correct and already tested (tests/test_lead_time.py) — only the wiring
was missing.

The numbers below are the audit's, measured on the real files:
  data/kaggle/superstore.csv           -> per-SKU sigma_LT from Order/Ship dates
  data/kaggle/superstore_canonical.csv -> per-SKU demand mean/std
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from jobs import intake
from src.policies import continuous_review_sq
from src.sources import DataFrameDemandSource

REPO = Path(__file__).resolve().parents[1]
PERIODS_PER_YEAR = 52.0
HOLDING_COST = 0.25
ORDER_COST = 75.0
SERVICE_LEVEL = 0.95


@pytest.fixture(scope="module")
def real_portfolio():
    """The audit's panel: canonical demand joined to sigma_LT from the raw ship dates."""
    raw = pd.read_csv(REPO / "data" / "kaggle" / "superstore.csv", encoding="latin-1")
    raw["Order Date"] = pd.to_datetime(raw["Order Date"], format="%m/%d/%Y")
    raw["Ship Date"] = pd.to_datetime(raw["Ship Date"], format="%m/%d/%Y")
    raw["lead_days"] = (raw["Ship Date"] - raw["Order Date"]).dt.days

    g = raw.groupby("Product ID")["lead_days"]
    lt = pd.DataFrame({"mean_days": g.mean(), "std_days": g.std(ddof=1), "n_orders": g.count()})
    lt = lt[lt["n_orders"] >= 3].dropna()

    canon = pd.read_csv(REPO / "data" / "kaggle" / "superstore_canonical.csv")
    d = canon.groupby("product_id")["quantity"]
    stats = pd.DataFrame({"mu": d.mean(), "sd": d.std(ddof=1), "total": d.sum()}).dropna()

    joined = stats.join(lt, how="inner")

    # the demand frame as it would arrive from intake once the column is carried:
    # one lead_time_days / lead_time_std value per SKU, in days
    frame = canon[canon.product_id.isin(joined.index)].copy()
    frame["lead_time_days"] = frame.product_id.map(joined["mean_days"])
    frame["lead_time_std"] = frame.product_id.map(joined["std_days"])
    return frame, joined


def _safety_stock(meta, annual_demand, *, lead_time_std):
    return continuous_review_sq(
        annual_demand=annual_demand,
        mean_demand_per_period=meta.mean_demand_per_period,
        demand_std_per_period=meta.demand_std_per_period,
        holding_cost_per_unit=max(HOLDING_COST * meta.mean_unit_cost, 1e-6),
        fixed_order_cost=ORDER_COST,
        lead_time_periods=meta.lead_time_periods,
        cycle_service_level=SERVICE_LEVEL,
        lead_time_std=lead_time_std,
    ).safety_stock.safety_stock


def test_intake_carries_lead_time_std():
    """The canonical schema must transport sigma_LT, or the chain dies at the door."""
    assert "lead_time_std" in intake.CANONICAL_OPTIONAL
    df = pd.DataFrame(
        {
            "date": ["2024-01-01", "2024-01-08"],
            "product_id": ["A", "A"],
            "quantity": [10, 12],
            "sigma_lead": [1.5, 1.5],  # an alias, as launch_readiness_job already accepts
        }
    )
    mapping = intake.detect_columns(df)
    assert mapping.mapping.get("lead_time_std") == "sigma_lead"
    out = intake.normalize(df, mapping)
    assert "lead_time_std" in out.columns
    assert out["lead_time_std"].iloc[0] == pytest.approx(1.5)


def test_source_populates_lead_time_std_periods(real_portfolio):
    """_metadata_from_frame must write the field, converted to periods."""
    frame, joined = real_portfolio
    source = DataFrameDemandSource(frame, periods_per_year=PERIODS_PER_YEAR)
    pid = str(joined.index[0])
    meta = source.metadata(pid)
    expected = float(joined.loc[pid, "std_days"]) * PERIODS_PER_YEAR / 365.0
    assert meta.lead_time_std_periods == pytest.approx(expected)
    assert meta.lead_time_std_periods > 0


def test_audit_delta_safety_stock_totals(real_portfolio):
    """The audit's headline: +18.5% total safety stock over 1,600 real SKUs."""
    frame, joined = real_portfolio
    source = DataFrameDemandSource(frame, periods_per_year=PERIODS_PER_YEAR)
    assert len(joined) == 1600, "the audit panel is 1,600 SKUs"

    today, fixed = [], []
    for pid, row in joined.iterrows():
        meta = source.metadata(str(pid))
        annual = float(row["total"])
        today.append(_safety_stock(meta, annual, lead_time_std=0.0))
        fixed.append(_safety_stock(meta, annual, lead_time_std=meta.lead_time_std_periods))

    today_arr, fixed_arr = np.array(today), np.array(fixed)
    assert today_arr.sum() == pytest.approx(4137.8, abs=0.5)
    assert fixed_arr.sum() == pytest.approx(4905.3, abs=0.5)

    pct = (fixed_arr.sum() / today_arr.sum() - 1) * 100
    assert pct == pytest.approx(18.5, abs=0.1)

    # SKUs whose safety stock is 0 today have no defined % change (0/0); the audit
    # excluded them, so the test does too.
    per_sku_pct = np.where(
        today_arr > 0, (fixed_arr - today_arr) / np.where(today_arr > 0, today_arr, 1) * 100, np.nan
    )
    assert np.nanmedian(per_sku_pct) == pytest.approx(16.3, abs=0.1)
    assert int(np.nansum(per_sku_pct > 10)) == 1073


def test_job_passes_lead_time_std_to_the_policy(real_portfolio):
    """End to end: the job's safety stock must rise once the column is present."""
    from jobs import inventory_optimization

    frame, joined = real_portfolio
    sample = list(joined.index[:60])
    with_std = frame[frame.product_id.isin(sample)].copy()
    without_std = with_std.drop(columns=["lead_time_std"])

    rep_with = inventory_optimization.run(with_std, periods_per_year=PERIODS_PER_YEAR)
    rep_without = inventory_optimization.run(without_std, periods_per_year=PERIODS_PER_YEAR)

    ss_with = sum(r.safety_stock for r in rep_with.recommendations)
    ss_without = sum(r.safety_stock for r in rep_without.recommendations)
    assert ss_with > ss_without, "sigma_LT must raise the buffer once it is wired through"
