"""The five demo CLIs must not crash - or lie - about an entity they could not score.

PR #193 made `SupplierScore` fields and the finance KPIs `float | None`. These scripts
were left out of that pass: they format `.otif`/`.ppm`/`.avg_lead_time`/`.per_unit` and
the turns/DIO/GMROI trio directly, sort and `min`/`max` on them, and feed them to TOPSIS.

Every one of those is a TypeError on `None` now. That is an improvement on what they did
before - print `0.0%` and rank the unmeasured entity as the worst performer, or print
`inf` turns - but a demo that crashes on a real export is not the deliverable either.

The contract these tests fix: an entity that could not be scored is **dropped from the
ranking by name**, and whatever is still printed about it says "not measured" rather than
a number. Each scoring block is exercised through a tiny synthetic frame, so the tests run
without the Kaggle datasets the CLIs normally read.
"""

import importlib

import pandas as pd
import pytest

superstore = importlib.import_module("examples.run_scm_superstore")
dataco = importlib.import_module("examples.run_scm_dataco")
procurement = importlib.import_module("examples.run_scm_procurement")
olist = importlib.import_module("examples.run_scm_olist")
new_caps = importlib.import_module("examples.run_new_capabilities")


# ---- run_scm_superstore.py --------------------------------------------------
# A ship mode whose every Ship Date failed to parse yields zero scoreable deliveries.
# `cards.sort(key=lambda c: c.otif)` then raises, and before #193 it sorted that mode
# to the bottom on a 0% OTIF nobody had measured.

def _superstore_frame() -> pd.DataFrame:
    return pd.DataFrame({
        "Ship Mode": ["First Class", "First Class", "Same Day", "Same Day"],
        "lead": [1.0, 2.0, float("nan"), float("nan")],
        "Quantity": [3, 4, 5, 6],
    })


def test_superstore_drops_a_ship_mode_with_no_parseable_lead_times():
    cards, dropped = superstore.carrier_scorecards(_superstore_frame())

    assert [c.supplier for c in cards] == ["First Class"]
    assert dropped == ("Same Day",)


def test_superstore_panel_renders_without_a_number_for_the_unscoreable_mode(capsys):
    cards, dropped = superstore.carrier_scorecards(_superstore_frame())

    superstore.print_panel(cards, dropped)

    out = capsys.readouterr().out
    assert "not measured" in out
    assert "Same Day" in out
    assert "0.0%" not in out


# ---- run_scm_dataco.py ------------------------------------------------------
# Here every delivery row is kept, so on_time is measured but lead time is not: the
# TOPSIS "lead" criterion is the one that has nothing to stand on.

def _dataco_frame() -> pd.DataFrame:
    return pd.DataFrame({
        "Shipping Mode": ["Standard", "Standard", "Express", "Express"],
        "on_time": [True, False, True, True],
        "in_full": [True, True, True, True],
        "real": [3.0, 4.0, float("nan"), float("nan")],
        "Order Item Quantity": [1, 2, 3, 4],
    })


def test_dataco_drops_a_mode_whose_lead_time_was_never_recorded():
    cards, dropped = dataco.carrier_scorecards(_dataco_frame())

    assert [c.supplier for c in cards] == ["Standard"]
    assert dropped == ("Express",)


# ---- run_scm_procurement.py -------------------------------------------------

def _procurement_frame() -> pd.DataFrame:
    return pd.DataFrame({
        "Supplier": ["Acme", "Acme", "Globex", "Globex"],
        "lead": [2.0, 3.0, float("nan"), float("nan")],
        "Order_Status": ["Delivered", "Delivered", "Delivered", "Delivered"],
        "Quantity": [10, 10, 10, 10],
        "Defective_Units": [0, 1, 0, 0],
    })


def test_procurement_drops_a_supplier_whose_lead_time_was_never_recorded():
    cards, dropped = procurement.supplier_scorecards(_procurement_frame(), ["Acme", "Globex"])

    assert [c.supplier for c in cards] == ["Acme"]
    assert dropped == ("Globex",)


# ---- run_scm_olist.py -------------------------------------------------------
# This one carried the lie in the script itself: a missing delivery date became
# `lead_time_days: 0.0`, i.e. the fastest possible seller.

def _olist_frame() -> pd.DataFrame:
    return pd.DataFrame({
        "seller_id": ["aaaaaaaa1", "aaaaaaaa1", "bbbbbbbb2", "bbbbbbbb2"],
        "on_time": [True, True, True, False],
        "lead_days": [5.0, 6.0, float("nan"), float("nan")],
        "review_score": [5.0, 4.0, 5.0, 1.0],
    })


def test_olist_no_longer_reads_a_missing_delivery_date_as_a_zero_day_lead_time():
    cards, dropped = olist.seller_scorecards(_olist_frame(), ["aaaaaaaa1", "bbbbbbbb2"])

    assert [c.supplier for c in cards] == ["aaaaaaaa"]
    assert dropped == ("bbbbbbbb",)


def test_olist_still_scores_a_seller_whose_lead_times_are_genuinely_recorded():
    """A real zero-day lead time is a measurement and must survive."""
    df = _olist_frame()
    df.loc[df["seller_id"] == "bbbbbbbb2", "lead_days"] = 0.0

    cards, dropped = olist.seller_scorecards(df, ["aaaaaaaa1", "bbbbbbbb2"])

    assert dropped == ()
    assert next(c for c in cards if c.supplier == "bbbbbbbb").avg_lead_time == 0.0


# ---- run_new_capabilities.py ------------------------------------------------
# turns / DIO / GMROI are `float | None` since #193; this block formatted all three
# with `:.2f`. Before that they printed `inf`, which is worse than a crash: it is the
# best possible score, reported as a fact, from an absence of inventory.

def test_new_capabilities_prints_the_finance_trio_as_unmeasured_instead_of_crashing(capsys):
    new_caps.print_financial_kpis(annual_cogs=0.0, avg_inventory_cost=0.0, gm_pct=0.35)

    out = capsys.readouterr().out
    assert "not measured" in out
    assert "inf" not in out


def test_new_capabilities_still_prints_the_real_figures_when_they_are_measurable(capsys):
    new_caps.print_financial_kpis(annual_cogs=500_000.0, avg_inventory_cost=100_000.0, gm_pct=0.35)

    out = capsys.readouterr().out
    assert "5.00" in out          # 500k COGS / 100k inventory = 5 turns
    assert "not measured" not in out


# ---- the shared contract ----------------------------------------------------

@pytest.mark.parametrize("module,builder,entities", [
    (superstore, "carrier_scorecards", None),
    (dataco, "carrier_scorecards", None),
    (procurement, "supplier_scorecards", ["Acme", "Globex"]),
    (olist, "seller_scorecards", ["aaaaaaaa1", "bbbbbbbb2"]),
])
def test_every_scoring_block_returns_a_panel_and_the_names_it_dropped(module, builder, entities):
    """One shape across the four CLIs, so none of them can quietly skip the report."""
    fn = getattr(module, builder)
    frame = {
        "run_scm_superstore": _superstore_frame, "run_scm_dataco": _dataco_frame,
        "run_scm_procurement": _procurement_frame, "run_scm_olist": _olist_frame,
    }[module.__name__.rsplit(".", 1)[-1]]()

    cards, dropped = fn(frame) if entities is None else fn(frame, entities)

    assert isinstance(dropped, tuple)
    assert all(isinstance(name, str) for name in dropped)
    assert all(c.otif is not None and c.avg_lead_time is not None for c in cards)
