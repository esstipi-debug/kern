"""A blank cell in the stock file must not print a SKU's whole value as cash at risk.

`jobs/excess_obsolete_job.prepare_records` read a blank `daily_demand` as `0.0`. In
`src/excess_obsolete.classify_sku` that hits `daily_demand <= 0` -> **DEAD**, whose
`excess_units` is the *full* on-hand quantity - so the SKU's entire inventory value is
reported as cash at risk, and it is recommended for liquidation. The adjacent `on_hand`
column was already refused when blank, so the omission was inconsistent inside one
function; and this runs on `POST /api/demo-price-scan`'s sibling, the **public demo scan**,
which is the first number a prospect ever sees Kern produce.

`days_since_last_sale` had the mirror-image version: blank meant `0.0`, i.e. "sold today",
the reading that most flatters the SKU and can keep a genuinely dead one out of the count.
"""

import pandas as pd
import pytest

from jobs import excess_obsolete_job as eoj
from src.excess_obsolete import DEAD, SkuStock, classify_sku
from webapp import demo_scan

_STOCK = pd.DataFrame({
    "product_id": ["A", "B", "C"],
    "on_hand": [100.0, 200.0, 50.0],
    "daily_demand": [5.0, 1.0, 0.0],
    "unit_cost": [10.0, 20.0, 30.0],
})


# ---- the required columns ----------------------------------------------------

def test_a_blank_daily_demand_is_refused_not_read_as_a_dead_sku():
    """`0.0` demand means DEAD, and DEAD writes off the entire on-hand value."""
    df = _STOCK.copy()
    df.loc[1, "daily_demand"] = None

    with pytest.raises(ValueError, match="daily_demand"):
        eoj.prepare_records(df)


def test_the_refusal_names_the_sku_and_the_consequence():
    df = _STOCK.copy()
    df.loc[1, "daily_demand"] = None

    with pytest.raises(ValueError) as exc:
        eoj.prepare_records(df)

    assert "B" in str(exc.value)
    assert "dead" in str(exc.value).lower()


def test_a_non_numeric_demand_is_refused_with_the_same_message():
    df = _STOCK.copy()
    df["daily_demand"] = df["daily_demand"].astype(object)
    df.loc[1, "daily_demand"] = "n/a"

    with pytest.raises(ValueError, match="daily_demand"):
        eoj.prepare_records(df)


def test_a_non_numeric_on_hand_is_refused_as_clearly_as_a_blank_one():
    df = _STOCK.copy()
    df["on_hand"] = df["on_hand"].astype(object)
    df.loc[0, "on_hand"] = "unknown"

    with pytest.raises(ValueError, match="on_hand"):
        eoj.prepare_records(df)


def test_a_genuinely_zero_demand_sku_is_still_classified_dead():
    """A real zero *is* a measurement. Only the blank was ever the lie."""
    payload = eoj.prepare_records(_STOCK)
    report = eoj.run(payload)

    dead = [ln for ln in report.lines if ln.classification == DEAD]
    assert [ln.product_id for ln in dead] == ["C"]
    assert report.n_dead == 1


# ---- the optional columns ----------------------------------------------------

def test_a_blank_last_sale_date_is_not_read_as_sold_today():
    """`0.0` days idle is the most flattering possible reading of "we don't know"."""
    df = _STOCK.assign(days_since_last_sale=[10.0, None, 400.0])

    stocks = eoj.prepare_records(df)["stocks"]

    assert stocks[1].days_since_last_sale is None


def test_an_absent_last_sale_column_leaves_the_idleness_rule_unused():
    stocks = eoj.prepare_records(_STOCK)["stocks"]

    assert all(s.days_since_last_sale is None for s in stocks)


def test_an_unknown_idle_time_cannot_by_itself_mark_a_sku_dead():
    healthy = classify_sku(
        SkuStock(product_id="A", on_hand=100.0, daily_demand=5.0, unit_cost=10.0,
                 days_since_last_sale=None),
        target_cover_days=90.0, dead_threshold_days=180.0,
    )

    assert healthy.classification != DEAD


def test_a_known_idle_time_past_the_threshold_still_marks_it_dead():
    idle = classify_sku(
        SkuStock(product_id="A", on_hand=100.0, daily_demand=5.0, unit_cost=10.0,
                 days_since_last_sale=400.0),
        target_cover_days=90.0, dead_threshold_days=180.0,
    )

    assert idle.classification == DEAD


def test_a_blank_unit_cost_is_refused_rather_than_valuing_the_sku_at_zero():
    """A zero-valued SKU silently shrinks the headline "cash at risk" the demo sells."""
    df = _STOCK.copy()
    df.loc[2, "unit_cost"] = None

    with pytest.raises(ValueError, match="unit_cost"):
        eoj.prepare_records(df)


def test_an_absent_unit_cost_column_is_still_allowed():
    """No valuation at all is a different thing from a partial one; the QA gate covers it."""
    stocks = eoj.prepare_records(_STOCK.drop(columns=["unit_cost"]))["stocks"]

    assert all(s.unit_cost == 0.0 for s in stocks)


# ---- the public demo ---------------------------------------------------------

def test_the_public_demo_refuses_the_file_instead_of_reporting_phantom_cash_at_risk():
    df = _STOCK.copy()
    df.loc[1, "daily_demand"] = None

    with pytest.raises(ValueError, match="daily_demand"):
        demo_scan.run_demo_scan(df)


def test_the_public_demo_still_scans_a_clean_file():
    result = demo_scan.run_demo_scan(_STOCK)

    assert result.eo.n_skus == 3
    assert result.eo.n_dead == 1          # only the genuinely zero-demand SKU
