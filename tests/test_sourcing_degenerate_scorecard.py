"""Acceptance test for the degenerate-scorecard guard (audit fix 3).

``score_suppliers`` took each performance column as ``str | None`` and, when a
column was not found, substituted a literal: ``False`` for on_time/in_full and
``0.0`` for lead/units/defects. So a real PO file whose columns the sniffer did
not recognise produced a scorecard where every supplier scored OTIF 0.0,
lead 0.0, PPM 0.0 -- and TOPSIS still ranked them (on price alone, the one
criterion that did resolve) and awarded a winner at confidence 1.0.

Witness case, reproduced below from data/kaggle/olist: 777 delivered POs across
8 sellers. On-time is recoverable from the same rows
(order_delivered_customer_date vs order_estimated_delivery_date), but those are
not names the sniffer knows.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from jobs import sourcing_job

REPO = Path(__file__).resolve().parents[1]
OLIST = REPO / "data" / "kaggle" / "olist"


@pytest.fixture(scope="module")
def po_extract_777():
    """A realistic PO extract: delivered order lines, the dataset's own column names."""
    if not (OLIST / "olist_order_items_dataset.csv").exists():
        pytest.skip(f"{OLIST} not present - large Kaggle dataset, fetched locally, not versioned")
    items = pd.read_csv(OLIST / "olist_order_items_dataset.csv")
    orders = pd.read_csv(OLIST / "olist_orders_dataset.csv")
    df = items.merge(orders, on="order_id", how="inner")
    df = df[df.order_status == "delivered"].copy()
    for col in ("order_purchase_timestamp", "order_delivered_customer_date",
                "order_estimated_delivery_date"):
        df[col] = pd.to_datetime(df[col])
    df = df.dropna(subset=["order_delivered_customer_date"])
    top = df.seller_id.value_counts().head(8).index
    return df[df.seller_id.isin(top)].head(777).copy()


def test_extract_is_the_witness_case(po_extract_777):
    assert len(po_extract_777) == 777
    assert po_extract_777.seller_id.nunique() == 8
    # the supplier and price columns DO sniff; none of the performance ones do
    assert "seller_id" in sourcing_job._SUPPLIER_COLS
    assert "price" in sourcing_job._PRICE_COLS
    cols = set(po_extract_777.columns)
    for candidates in (sourcing_job._ON_TIME_COLS, sourcing_job._IN_FULL_COLS,
                       sourcing_job._LEAD_COLS, sourcing_job._DEFECTS_COLS):
        assert not (cols & set(candidates))


def test_prepare_raises_instead_of_awarding_on_zero_scorecard(po_extract_777, tmp_path):
    """777 real POs with no recognisable performance column, and no date pair the
    sniffer knows how to derive lead time from, must fail loudly - not silently
    score a zero scorecard. prepare() now derives lead_time/on_time/in_full from
    dates before falling back to score_suppliers' own column-resolution guard, so
    this fixture (whose date columns are all Olist-specific names) refuses at the
    lead-time derivation step instead - still a loud ValueError naming a way out,
    just an earlier one in the pipeline."""
    path = tmp_path / "olist_po_extract.csv"
    po_extract_777.to_csv(path, index=False)

    with pytest.raises(ValueError) as exc:
        sourcing_job.prepare(str(path))

    msg = str(exc.value)
    # the message has to name a way out, whichever refusal fired first
    assert "lead_col" in msg or "on_time_col" in msg


def test_score_suppliers_raises_when_no_performance_column_resolves():
    df = pd.DataFrame({"supplier": ["S1", "S1", "S2"], "price": [10.0, 11.0, 9.0]})
    with pytest.raises(ValueError, match="columns that decide the award"):
        sourcing_job.score_suppliers(df, supplier_col="supplier", price_col="price")


def test_one_resolved_performance_column_is_enough(po_extract_777):
    """The guard must not block a file that does carry usable performance data."""
    wired = po_extract_777.rename(columns={"seller_id": "supplier"})
    wired["on_time"] = (
        wired.order_delivered_customer_date <= wired.order_estimated_delivery_date
    )
    cards, prices = sourcing_job.score_suppliers(
        wired, supplier_col="supplier", on_time_col="on_time", price_col="price",
        require_all_criteria=False,
    )
    assert len(cards) == 8
    assert any(c.on_time_rate > 0 for c in cards)
    report = sourcing_job.run(cards, prices)
    assert report.recommended in {c.supplier for c in cards}
