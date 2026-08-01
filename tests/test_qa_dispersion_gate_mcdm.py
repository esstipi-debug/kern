"""T1a - dispersion gate on the adjudication path.

A criterion whose values are identical across every alternative cannot discriminate:
its vector-normalized column contributes the same offset to all of them. TOPSIS's own
guards actively hide this -- ``denom[denom == 0] = 1.0`` and ``spread[spread == 0] = 1.0``
turn an all-zero matrix into a clean tie, and ``sorted`` then returns insertion order,
so the "winner" is whoever the dict happened to list first.

Policy: DEGRADE, not refuse. A flat criterion can be legitimate (zero defects across
the whole panel is a good outcome, not a data failure), so refusing there would gate
healthy runs. Instead the non-discriminating criteria are dropped, the remaining
weights renormalized, and the dropped names carried on the result so the deliverable
can say so. Refuse only when nothing discriminates -- then any winner is arbitrary.

Witnesses are real: data/kaggle/olist, delivered orders, top 8 sellers.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from jobs import sourcing_job
from src.mcdm import Criterion, topsis_rank

REPO = Path(__file__).resolve().parents[1]
OLIST = REPO / "data" / "kaggle" / "olist"


@pytest.fixture(scope="module")
def olist_partial_panel():
    """Witness 2': real lead time (discriminating), on-time/in-full/defects/units
    measured for every supplier but held constant.

    All five performance columns resolve, so the fix-3 guard passes and otif/ppm are
    real ranking criteria (not ``unmeasured`` - see jobs/sourcing_job.py::run, which
    only drops a criterion into ``unmeasured`` when at least one supplier never
    recorded it at all) -- but OTIF and PPM come out identically for all eight
    suppliers while still carrying 0.4 + 0.2 of the criteria weight, so TOPSIS's own
    dispersion gate must drop them (``ignored_criteria``), not intake's unmeasured gap.
    """
    if not (OLIST / "olist_order_items_dataset.csv").exists():
        pytest.skip(f"{OLIST} not present - large Kaggle dataset, fetched locally, not versioned")
    items = pd.read_csv(OLIST / "olist_order_items_dataset.csv")
    orders = pd.read_csv(OLIST / "olist_orders_dataset.csv")
    df = items.merge(orders, on="order_id", how="inner")
    df = df[df.order_status == "delivered"].copy()
    for col in ("order_purchase_timestamp", "order_delivered_customer_date"):
        df[col] = pd.to_datetime(df[col])
    df = df.dropna(subset=["order_delivered_customer_date"])
    top = df.seller_id.value_counts().head(8).index
    panel = df[df.seller_id.isin(top)].head(777).copy()
    panel["supplier"] = panel.seller_id
    panel["lead_time_days"] = (
        panel.order_delivered_customer_date - panel.order_purchase_timestamp
    ).dt.days
    panel["on_time"] = True
    panel["in_full"] = True
    panel["defects"] = 0
    panel["units"] = 10
    return panel


def test_witness_panel_is_degenerate_on_two_criteria(olist_partial_panel):
    cards, _prices = sourcing_job.score_suppliers(
        olist_partial_panel, supplier_col="supplier", on_time_col="on_time",
        in_full_col="in_full", lead_col="lead_time_days", units_col="units",
        defects_col="defects", price_col="price",
    )
    assert len(cards) == 8
    assert np.std([c.otif for c in cards]) == 0.0
    assert np.std([c.ppm for c in cards]) == 0.0
    assert np.std([c.avg_lead_time for c in cards]) > 0.0  # lead does discriminate


def test_all_zero_matrix_is_refused_not_silently_tied():
    """Nothing discriminates -> refuse. Today it returns an insertion-order winner."""
    alts = {f"S{i}": {"otif": 0.0, "lead_time": 0.0, "ppm": 0.0} for i in range(4)}
    crit = [Criterion("otif"), Criterion("lead_time", benefit=False),
            Criterion("ppm", benefit=False)]
    with pytest.raises(ValueError, match="discriminate"):
        topsis_rank(alts, crit, {"otif": 0.4, "lead_time": 0.3, "ppm": 0.3})


def test_flat_criterion_is_dropped_and_reported():
    """otif is flat, lead_time is not: rank on lead_time and say otif was dropped."""
    alts = {
        "A": {"otif": 0.0, "lead_time": 10.0},
        "B": {"otif": 0.0, "lead_time": 20.0},
    }
    crit = [Criterion("otif"), Criterion("lead_time", benefit=False)]
    result = topsis_rank(alts, crit, {"otif": 0.5, "lead_time": 0.5})

    assert result.ignored_criteria == ("otif",)
    assert result.best == "A"  # shorter lead time wins on the only live criterion
    assert result.scores["A"] > result.scores["B"]


def test_discriminating_run_is_untouched():
    """The rule must not perturb a panel where every criterion varies."""
    alts = {
        "A": {"otif": 0.95, "lead_time": 10.0},
        "B": {"otif": 0.80, "lead_time": 20.0},
        "C": {"otif": 0.90, "lead_time": 14.0},
    }
    crit = [Criterion("otif"), Criterion("lead_time", benefit=False)]
    weights = {"otif": 0.6, "lead_time": 0.4}
    result = topsis_rank(alts, crit, weights)
    assert result.ignored_criteria == ()
    assert result.best == "A"


def test_deck_discloses_the_dropped_criteria(olist_partial_panel):
    """The discarded criterion must reach the deliverable, not a log."""
    cards, prices = sourcing_job.score_suppliers(
        olist_partial_panel, supplier_col="supplier", on_time_col="on_time",
        in_full_col="in_full", lead_col="lead_time_days", units_col="units",
        defects_col="defects", price_col="price",
    )
    report = sourcing_job.run(cards, prices)
    assert set(report.ranking.ignored_criteria) == {"otif", "ppm"}

    # Degrading must NOT fail QA: the orchestrator returns qa_failed with no
    # deliverables written, which would make the disclosure below unreachable.
    assert sourcing_job.verify(report) == []

    deck = sourcing_job.build_deck(report)
    blob = " ".join(
        [deck.summary, *(f.title + " " + f.detail for f in deck.findings)]
    ).lower()
    assert "otif" in blob and "ppm" in blob
    assert "did not discriminate" in blob or "no discrimina" in blob
