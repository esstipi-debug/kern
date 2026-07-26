"""T1b - dispersion gate on the classification path.

src/classification.py:84 computes ``annual_value = unit_cost * annual_demand``. When
unit cost is identical across the portfolio, annual_value is just units times a
constant: the ABC ranking is a units ranking wearing a value label, and nothing in
the pipeline said so.

Policy: DEGRADE, not refuse. A genuinely uniform-cost catalog makes ABC-by-value and
ABC-by-units the same thing, which is correct arithmetic -- refusing would gate a
legitimate portfolio. What was missing is disclosure, so the engine reports the value
basis and the deck states it.

Witness 1 is the committed artifact: data/kaggle/superstore_canonical.csv carries
unit_cost == 1.0 on all 9,826 rows and produced 1,111 class-A SKUs of 1,862.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from jobs import abc_xyz_job, intake
from src.classification import value_basis

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def degenerate_items():
    """Witness 1: the shipped canonical file, unit_cost flat at 1.0."""
    canon = pd.read_csv(REPO / "data" / "kaggle" / "superstore_canonical.csv")
    assert canon.unit_cost.nunique() == 1
    return abc_xyz_job.aggregate_skus(
        canon, product_col="product_id", demand_col="quantity", cost_col="unit_cost"
    )


@pytest.fixture(scope="module")
def healthy_items():
    """The same source file with real cost recovered from revenue."""
    raw = pd.read_csv(REPO / "data" / "kaggle" / "superstore.csv", encoding="latin-1")
    canon = intake.normalize(raw, intake.detect_columns(raw))
    assert canon.unit_cost.nunique() > 1000
    return abc_xyz_job.aggregate_skus(
        canon, product_col="product_id", demand_col="quantity", cost_col="unit_cost"
    )


def test_value_basis_detects_a_flat_cost_portfolio(degenerate_items):
    basis = value_basis(degenerate_items)
    assert basis.discriminates is False
    assert basis.unit_cost_nunique == 1
    assert basis.unit_cost_std == pytest.approx(0.0)


def test_value_basis_accepts_a_varying_cost_portfolio(healthy_items):
    basis = value_basis(healthy_items)
    assert basis.discriminates is True
    assert basis.unit_cost_nunique > 1000
    assert basis.unit_cost_std > 0.0


def test_report_carries_the_value_basis(degenerate_items, healthy_items):
    bad = abc_xyz_job.run(degenerate_items)
    good = abc_xyz_job.run(healthy_items)
    assert bad.n_a == 1111 and bad.value_basis.discriminates is False
    assert good.n_a == 414 and good.value_basis.discriminates is True


def test_degrading_does_not_fail_qa(degenerate_items):
    """Disclosure, not refusal: qa_failed would write no deck at all."""
    assert abc_xyz_job.verify(abc_xyz_job.run(degenerate_items)) == []


def test_deck_states_the_ranking_is_on_units(degenerate_items):
    deck = abc_xyz_job.build_deck(abc_xyz_job.run(degenerate_items))
    blob = " ".join(
        [deck.summary, *(f.title + " " + f.detail for f in deck.findings)]
    ).lower()
    assert "unit" in blob
    assert "did not discriminate" in blob or "same unit cost" in blob


def test_healthy_deck_carries_no_such_warning(healthy_items):
    """The rule must stay silent on a portfolio with real cost dispersion."""
    deck = abc_xyz_job.build_deck(abc_xyz_job.run(healthy_items))
    blob = " ".join(
        [deck.summary, *(f.title + " " + f.detail for f in deck.findings)]
    ).lower()
    assert "same unit cost" not in blob
    assert "did not discriminate" not in blob
