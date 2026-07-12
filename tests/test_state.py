"""Tests for the system-state snapshot store (Linchpin 3.0 PR-1, F0 -- src/state).

Guarantees under test (plan S4.1 QA invariants):
- snapshot -> latest -> history round-trips exactly what was written;
- history is strictly append-only: two snapshots for the same domain both
  survive, in chronological order;
- cycle_id must increase monotonically per domain -- a snapshot whose
  cycle_id is <= the latest already stored is rejected, nothing written;
- an invalid schema (missing column, wrong dtype, out-of-range value) is
  rejected with a clear error and nothing is written;
- the parquet engine is optional -- the CSV fallback round-trips too.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.state import store as store_module
from src.state.store import DuplicateCycleError, StateStore
from src.state.system_state import (
    DOMAINS,
    CycleOrderError,
    SchemaValidationError,
    UnknownDomainError,
    _cycle_sort_key,
    history,
    latest,
    snapshot,
)


def _store(tmp_path) -> StateStore:
    return StateStore(tmp_path / "state")


def _stock_df(on_hand: float = 100.0) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "product_id": ["SKU-A", "SKU-B"],
            "on_hand": [on_hand, 40.0],
            "reorder_point": [50.0, 20.0],
            "avg_daily_demand": [5.0, 2.0],
        }
    )


def _prices_own_df() -> pd.DataFrame:
    return pd.DataFrame({"product_id": ["SKU-A"], "price": [19.99], "currency": ["USD"]})


# -- known domains ----------------------------------------------------------


def test_known_domains_cover_the_plan_s41_categories():
    assert set(DOMAINS) == {
        "stock",
        "prices_own",
        "prices_competitor",
        "forecast",
        "decisions",
        "outcomes",
    }


# -- round trip: snapshot -> latest -> history -------------------------------


def test_snapshot_then_latest_round_trips_exactly_what_was_written(tmp_path):
    store = _store(tmp_path)
    df = _stock_df()

    snap = snapshot("stock", df, "1", store=store)
    got = latest("stock", store=store)

    assert snap.domain == "stock" and snap.cycle_id == "1"
    assert got is not None
    assert got.cycle_id == "1"
    pd.testing.assert_frame_equal(
        got.payload.reset_index(drop=True), df.reset_index(drop=True), check_dtype=False
    )


def test_latest_returns_none_for_a_domain_with_no_snapshots(tmp_path):
    store = _store(tmp_path)
    assert latest("stock", store=store) is None


def test_history_window_returns_the_right_chronological_slice(tmp_path):
    store = _store(tmp_path)
    snapshot("stock", _stock_df(on_hand=10.0), "1", store=store)
    snapshot("stock", _stock_df(on_hand=20.0), "2", store=store)
    snapshot("stock", _stock_df(on_hand=30.0), "3", store=store)

    full = history("stock", store=store)
    assert [s.cycle_id for s in full] == ["1", "2", "3"]
    assert [s.payload["on_hand"].iloc[0] for s in full] == [10.0, 20.0, 30.0]

    last_two = history("stock", window=2, store=store)
    assert [s.cycle_id for s in last_two] == ["2", "3"]

    none = history("stock", window=0, store=store)
    assert none == []


# -- append-only -------------------------------------------------------------


def test_two_snapshots_for_the_same_domain_both_survive_append_only(tmp_path):
    """A snapshot call NEVER overwrites history -- calling snapshot() twice for
    the same domain with different cycle_id must leave BOTH visible in history."""
    store = _store(tmp_path)
    snapshot("stock", _stock_df(on_hand=10.0), "2026-01-01", store=store)
    snapshot("stock", _stock_df(on_hand=99.0), "2026-01-02", store=store)

    snaps = history("stock", store=store)
    assert len(snaps) == 2
    assert [s.cycle_id for s in snaps] == ["2026-01-01", "2026-01-02"]
    assert snaps[0].payload["on_hand"].iloc[0] == 10.0
    assert snaps[1].payload["on_hand"].iloc[0] == 99.0
    # the first file on disk is untouched -- re-reading it still gives 10.0
    assert history("stock", window=1, store=store)[0].cycle_id == "2026-01-02"


def test_multiple_domains_are_independent_append_only_streams(tmp_path):
    store = _store(tmp_path)
    snapshot("stock", _stock_df(), "1", store=store)
    snapshot("prices_own", _prices_own_df(), "1", store=store)  # same cycle_id, different domain: OK

    assert len(history("stock", store=store)) == 1
    assert len(history("prices_own", store=store)) == 1


# -- monotonic cycle_id -------------------------------------------------------


def test_cycle_id_less_than_or_equal_to_latest_is_rejected(tmp_path):
    store = _store(tmp_path)
    snapshot("stock", _stock_df(), "5", store=store)

    with pytest.raises(CycleOrderError):
        snapshot("stock", _stock_df(), "4", store=store)  # strictly less
    with pytest.raises(CycleOrderError):
        snapshot("stock", _stock_df(), "5", store=store)  # equal

    # neither rejected call wrote anything -- still exactly one snapshot
    assert [s.cycle_id for s in history("stock", store=store)] == ["5"]


def test_cycle_sort_key_orders_numeric_cycle_ids_numerically_not_lexicographically():
    """"10" must sort after "9" -- plain lexicographic order would (wrongly) put
    "10" before "9", since '1' < '9' as characters."""
    assert "10" < "9"  # sanity: lexicographic string compare disagrees with numeric order
    assert _cycle_sort_key("9") < _cycle_sort_key("10")
    assert _cycle_sort_key("10") > _cycle_sort_key("9")


def test_numeric_cycle_ids_beyond_single_digit_are_accepted_in_order(tmp_path):
    store = _store(tmp_path)
    snapshot("stock", _stock_df(), "9", store=store)
    snapshot("stock", _stock_df(), "10", store=store)  # would be rejected under lexicographic order

    assert [s.cycle_id for s in history("stock", store=store)] == ["9", "10"]


# -- schema validation: rejected, nothing written -----------------------------


def test_missing_required_column_is_rejected_and_nothing_written(tmp_path):
    store = _store(tmp_path)
    bad = pd.DataFrame({"product_id": ["SKU-A"], "on_hand": [10.0], "reorder_point": [5.0]})  # no avg_daily_demand

    with pytest.raises(SchemaValidationError):
        snapshot("stock", bad, "1", store=store)

    assert latest("stock", store=store) is None
    assert history("stock", store=store) == []


def test_negative_stock_value_is_rejected(tmp_path):
    store = _store(tmp_path)
    bad = _stock_df(on_hand=-1.0)

    with pytest.raises(SchemaValidationError):
        snapshot("stock", bad, "1", store=store)

    assert latest("stock", store=store) is None


def test_non_positive_price_is_rejected(tmp_path):
    store = _store(tmp_path)
    bad = pd.DataFrame({"product_id": ["SKU-A"], "price": [0.0], "currency": ["USD"]})

    with pytest.raises(SchemaValidationError):
        snapshot("prices_own", bad, "1", store=store)


def test_non_numeric_price_is_rejected(tmp_path):
    store = _store(tmp_path)
    bad = pd.DataFrame({"product_id": ["SKU-A"], "price": ["not-a-number"], "currency": ["USD"]})

    with pytest.raises(SchemaValidationError):
        snapshot("prices_own", bad, "1", store=store)


def test_extra_columns_beyond_the_contract_are_allowed(tmp_path):
    store = _store(tmp_path)
    df = _stock_df()
    df["warehouse"] = ["WH-1", "WH-2"]  # not part of the stock contract

    snap = snapshot("stock", df, "1", store=store)
    assert "warehouse" in snap.payload.columns


def test_snapshot_rejects_a_non_dataframe_payload(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(TypeError):
        snapshot("stock", {"product_id": ["SKU-A"]}, "1", store=store)


def test_snapshot_rejects_an_empty_cycle_id(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(ValueError):
        snapshot("stock", _stock_df(), "", store=store)


def test_unknown_domain_is_rejected_by_all_three_entry_points(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(UnknownDomainError):
        snapshot("not_a_real_domain", _stock_df(), "1", store=store)
    with pytest.raises(UnknownDomainError):
        latest("not_a_real_domain", store=store)
    with pytest.raises(UnknownDomainError):
        history("not_a_real_domain", store=store)


# -- store-level primitives ---------------------------------------------------


def test_store_duplicate_cycle_id_is_rejected_as_defense_in_depth(tmp_path):
    """system_state.snapshot() already catches this via CycleOrderError; the store
    itself also refuses a literal duplicate (domain, cycle_id) insert."""
    store = _store(tmp_path)
    store.append_snapshot("stock", "1", _stock_df())
    with pytest.raises(DuplicateCycleError):
        store.append_snapshot("stock", "1", _stock_df())

    # the failed second write did not leave an orphan file or a second row
    assert len(store.list_records("stock")) == 1


def test_store_falls_back_to_csv_when_no_parquet_engine_is_available(tmp_path, monkeypatch):
    monkeypatch.setattr(store_module, "_HAS_PARQUET_ENGINE", False)
    store = StateStore(tmp_path / "state")

    record = store.append_snapshot("stock", "1", _stock_df())

    assert record.file_format == "csv"
    assert (tmp_path / "state" / record.file_path).suffix == ".csv"
    back = store.load_payload(record)
    pd.testing.assert_frame_equal(
        back.reset_index(drop=True), _stock_df().reset_index(drop=True), check_dtype=False
    )


def test_store_uses_parquet_when_engine_is_available(tmp_path):
    """This repo's ``state`` extra is installed in the test environment, so the
    default path must actually be parquet, not silently fall back to CSV."""
    store = _store(tmp_path)
    record = store.append_snapshot("stock", "1", _stock_df())
    assert record.file_format == "parquet"


def test_default_store_is_a_lazily_constructed_singleton_at_the_configured_base_path(tmp_path, monkeypatch):
    """``DEFAULT_BASE_PATH`` is read from ``LINCHPIN_STATE_PATH`` once at import time
    (same convention as webapp/security.py's env-var constants), so this test
    monkeypatches the resolved constant directly rather than the env var."""
    monkeypatch.setattr(store_module, "_default_store", None)
    monkeypatch.setattr(store_module, "DEFAULT_BASE_PATH", str(tmp_path / "custom_state"))

    got = store_module.default_store()
    again = store_module.default_store()

    assert got.base_path == tmp_path / "custom_state"
    assert got is again  # cached singleton, not rebuilt on every call
