import numpy as np
import pandas as pd
import pytest

from jobs.intake import prepare_tracked
from src.simworld.lens import apply_dirt, export_history, history_frame
from src.simworld.world import advance, generate_world, initial_state


def _run_weeks(n_weeks=8, seed=5, n_skus=4):
    w = generate_world(seed=seed, n_skus=n_skus, n_suppliers=2, weeks=n_weeks,
                       regime="seasonal_trend")
    st = initial_state(w)
    outs = []
    for _ in range(n_weeks):
        st, out = advance(w, st, {})
        outs.append(out)
    return w, outs


def test_level_zero_has_the_canonical_columns_intake_expects():
    w, outs = _run_weeks()
    df = history_frame(w, outs)
    for col in ("date", "product_id", "quantity", "unit_cost", "lead_time_days"):
        assert col in df.columns


def test_level_zero_row_count_is_skus_times_weeks():
    w, outs = _run_weeks(n_weeks=8, n_skus=4)
    assert len(history_frame(w, outs)) == 4 * 8


def test_level_zero_quantity_is_the_sold_quantity():
    w, outs = _run_weeks(n_weeks=3, n_skus=2)
    df = history_frame(w, outs)
    total = sum(float(o.sold.sum()) for o in outs)
    assert df["quantity"].sum() == pytest.approx(total)


def test_export_writes_a_readable_csv(tmp_path):
    w, outs = _run_weeks()
    p = export_history(w, outs, tmp_path / "h.csv", level=0)
    assert p.exists()
    back = pd.read_csv(p)
    assert len(back) == len(history_frame(w, outs))


def test_lens_never_mutates_the_world():
    w, outs = _run_weeks()
    before = w.demand.copy()
    history_frame(w, outs)
    assert np.array_equal(w.demand, before)


def test_unknown_level_raises(tmp_path):
    w, outs = _run_weeks()
    with pytest.raises(ValueError, match="nivel"):
        export_history(w, outs, tmp_path / "h.csv", level=9)


def test_intake_reads_level_zero_without_dropping_rows(tmp_path):
    w, outs = _run_weeks(n_weeks=10, n_skus=5)
    p = export_history(w, outs, tmp_path / "clean.csv", level=0)
    frame, quality = prepare_tracked(p)
    assert quality.n_dropped_bad_date == 0
    assert quality.n_dropped_bad_quantity == 0
    assert quality.synthesized == ()
    assert not frame.empty


# --- Tests added beyond the brief, closing gaps found by the discrimination
# question ("against which incorrect implementation does this test fail?").
# See task-3-report.md for the per-test rationale.


def test_level_zero_values_match_world_truth_not_just_column_names():
    """The brief's column-presence test would pass even if every cell were
    garbage (zeros, swapped fields, wrong SKU). This pins the actual values."""
    w, outs = _run_weeks(n_weeks=4, n_skus=3)
    df = history_frame(w, outs)
    cost_by_sku = {s.sku: s.unit_cost for s in w.skus}
    lead_by_sku = {s.sku: float(w.suppliers[s.supplier_idx].promised_lead_weeks * 7) for s in w.skus}
    for _, row in df.iterrows():
        assert row["unit_cost"] == pytest.approx(cost_by_sku[row["product_id"]])
        assert row["lead_time_days"] == pytest.approx(lead_by_sku[row["product_id"]])
        assert row["quantity"] >= 0.0
    # una fecha compartida por periodo (una por semana, no una por SKU)
    assert df["date"].nunique() == 4


def test_lead_time_days_is_promised_never_real_even_when_they_differ():
    """Guards the resolved ambiguity: lead_time_days must stay the supplier's
    promise even for the exact seed/shape known to produce real != promised
    (mirrors test_promised_lead_time_differs_from_real_at_least_once in
    test_simworld_world.py). A history_frame that leaked real_lead_weeks in
    would still pass every other test in this file."""
    w = generate_world(seed=3, n_skus=12, n_suppliers=4, weeks=52, regime="seasonal_trend")
    promised = np.array([w.suppliers[s.supplier_idx].promised_lead_weeks for s in w.skus])
    assert (w.real_lead_weeks != promised[:, None]).any()  # sanity: seed still produces a gap

    st = initial_state(w)
    outs = []
    for _ in range(w.weeks):
        st, out = advance(w, st, {})
        outs.append(out)

    df = history_frame(w, outs)
    expected = {s.sku: float(w.suppliers[s.supplier_idx].promised_lead_weeks * 7) for s in w.skus}
    for product_id, group in df.groupby("product_id"):
        assert (group["lead_time_days"] == expected[product_id]).all()


def test_export_round_trip_preserves_values_exactly(tmp_path):
    """The brief's CSV test only checks row COUNT, so it would pass even if
    export_history wrote an extra index column or corrupted values on the way
    out. This compares the written-and-reread frame cell-for-cell."""
    w, outs = _run_weeks(n_weeks=5, n_skus=3)
    df = history_frame(w, outs)
    p = export_history(w, outs, tmp_path / "h.csv", level=0)
    back = pd.read_csv(p)
    assert list(back.columns) == list(df.columns)
    pd.testing.assert_frame_equal(
        back.sort_values(["product_id", "date"]).reset_index(drop=True),
        df.sort_values(["product_id", "date"]).reset_index(drop=True),
        check_dtype=False,
    )


def test_lens_never_mutates_outcomes_or_real_lead_weeks(tmp_path):
    """The brief's mutation test only snapshots world.demand. history_frame
    also reads world.real_lead_weeks and every outcome's ndarrays (sold,
    demand, ...) -- an implementation that rounds/sorts/clips one of those
    in place would sail through the brief's test alone."""
    w, outs = _run_weeks(n_weeks=6, n_skus=3)
    demand_before = w.demand.copy()
    real_lead_before = w.real_lead_weeks.copy()
    sold_before = [o.sold.copy() for o in outs]
    demand_out_before = [o.demand.copy() for o in outs]

    history_frame(w, outs)
    export_history(w, outs, tmp_path / "h.csv", level=0)

    assert np.array_equal(w.demand, demand_before)
    assert np.array_equal(w.real_lead_weeks, real_lead_before)
    for out, before in zip(outs, sold_before):
        assert np.array_equal(out.sold, before)
    for out, before in zip(outs, demand_out_before):
        assert np.array_equal(out.demand, before)


def test_apply_dirt_level_zero_returns_an_independent_copy():
    """apply_dirt(level=0) must be safe for later dirt injectors (Tasks 4-5)
    to build on: returning the SAME frame object would let a level-1/2/3
    injector mutate the caller's level-0 frame in place. Content equality
    alone (what a naive test would check) does not catch that."""
    w, outs = _run_weeks(n_weeks=2, n_skus=2)
    df = history_frame(w, outs)
    dirty = apply_dirt(df, level=0)
    assert dirty is not df
    pd.testing.assert_frame_equal(dirty, df)


def test_apply_dirt_unknown_level_raises():
    w, outs = _run_weeks(n_weeks=2, n_skus=2)
    df = history_frame(w, outs)
    with pytest.raises(ValueError, match="nivel"):
        apply_dirt(df, level=9)
