"""Tests for src/pricing_intel/ledger.py (Linchpin 3.0 PR-10).

Guarantees under test (plan S6.3 + rule 8, "estado append-only"):
- append() -> latest_by_sku()/history_for_sku() round-trips exactly what was
  written, including Decimal precision, through a real parquet file on disk;
- append-only: two observations for the same (site, competitor_sku_ref) key
  both survive in history_for_sku -- the first file is never edited;
- a "correction" (is_correction=True) is a brand-new row, not an edit -- the
  row it corrects stays readable, and the latest pointer only moves to it
  because it is flagged, not just because it exists;
- an out-of-order (older observed_at) append never regresses the latest
  pointer, but is still recorded in history;
- latest_for_product answers "for this client SKU, what's each competitor's
  latest price" -- one row per site, via the (matched_product_id, site) index;
- the ledger degrades to CSV (same monkeypatch convention as
  tests/test_state.py) without losing Decimal precision -- the entire reason
  this ledger doesn't reuse StateStore.load_payload() as-is;
- a duplicate batch_id for the same site is rejected and leaves no orphan file.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.pricing_intel.ledger import (
    DEFAULT_BASE_PATH,
    DuplicateBatchError,
    PriceLedger,
    default_ledger,
)
from src.pricing_intel.models import CompetitorOffer
from src.state import store as state_store


def _offer(**overrides: object) -> CompetitorOffer:
    fields: dict[str, object] = dict(
        observed_at=datetime(2026, 7, 12, 10, 0, 0, tzinfo=timezone.utc),
        site="example.com",
        competitor_sku_ref="ref-1",
        matched_product_id="SKU-A",
        match_confidence=0.95,
        price=Decimal("19.99"),
        currency="USD",
        price_normalized=Decimal("19.99"),
        shipping=Decimal("4.50"),
        availability="InStock",
        promo_flag=False,
        list_price=Decimal("24.99"),
        acquisition_tier="L1",
        extractor="jsonld",
        extractor_version="extruct==0.18.0",
        extraction_confidence=0.98,
    )
    fields.update(overrides)
    return CompetitorOffer(**fields)


def _ledger(tmp_path) -> PriceLedger:
    return PriceLedger(tmp_path / "ledger")


# -- basic append / latest round trip -----------------------------------------


def test_append_then_latest_by_sku_round_trips_exactly(tmp_path):
    ledger = _ledger(tmp_path)
    offer = _offer(price=Decimal("19.99"), shipping=Decimal("0.00"))

    result = ledger.append([offer])
    got = ledger.latest_by_sku("example.com", "ref-1")

    assert result.rows_written == 1
    assert result.rows_became_latest == 1
    assert len(result.batches) == 1
    assert got is not None
    assert got.offer == offer
    assert repr(got.offer.price) == "Decimal('19.99')"
    assert got.is_correction is False


def test_latest_by_sku_returns_none_for_an_unknown_key(tmp_path):
    ledger = _ledger(tmp_path)
    assert ledger.latest_by_sku("example.com", "nope") is None


def test_append_rejects_an_empty_sequence(tmp_path):
    ledger = _ledger(tmp_path)
    with pytest.raises(ValueError):
        ledger.append([])


def test_append_across_multiple_sites_writes_one_batch_per_site(tmp_path):
    ledger = _ledger(tmp_path)
    offers = [
        _offer(site="example.com", competitor_sku_ref="ref-1"),
        _offer(site="other.example", competitor_sku_ref="ref-9"),
    ]
    result = ledger.append(offers)

    assert {b.site for b in result.batches} == {"example.com", "other.example"}
    assert ledger.latest_by_sku("example.com", "ref-1") is not None
    assert ledger.latest_by_sku("other.example", "ref-9") is not None


# -- append-only invariant -----------------------------------------------------


def test_two_observations_for_the_same_key_both_survive_append_only(tmp_path):
    """append() NEVER overwrites history -- appending twice for the same key
    with different observed_at must leave BOTH visible in history_for_sku."""
    ledger = _ledger(tmp_path)
    first = _offer(observed_at=datetime(2026, 7, 12, 10, 0, 0, tzinfo=timezone.utc), price=Decimal("19.99"))
    second = _offer(observed_at=datetime(2026, 7, 12, 11, 0, 0, tzinfo=timezone.utc), price=Decimal("21.00"))

    ledger.append([first])
    ledger.append([second])

    history = ledger.history_for_sku("example.com", "ref-1")
    assert len(history) == 2
    assert history[0].offer == first
    assert history[1].offer == second
    # the first batch file is untouched -- re-reading it still gives 19.99
    assert history[0].offer.price == Decimal("19.99")

    latest = ledger.latest_by_sku("example.com", "ref-1")
    assert latest.offer == second


def test_out_of_order_append_does_not_regress_the_latest_pointer(tmp_path):
    """A late-arriving OLDER observation must still be recorded in history, but
    must never become the latest pointer once a newer one is already stored."""
    ledger = _ledger(tmp_path)
    newer = _offer(observed_at=datetime(2026, 7, 12, 11, 0, 0, tzinfo=timezone.utc), price=Decimal("21.00"))
    older = _offer(observed_at=datetime(2026, 7, 12, 9, 0, 0, tzinfo=timezone.utc), price=Decimal("18.00"))

    r1 = ledger.append([newer])
    r2 = ledger.append([older])

    assert r1.rows_became_latest == 1
    assert r2.rows_became_latest == 0  # older row recorded, but did not advance latest

    assert ledger.latest_by_sku("example.com", "ref-1").offer == newer
    history = [r.offer for r in ledger.history_for_sku("example.com", "ref-1")]
    assert history == [older, newer]  # both present, oldest first


# -- corrections ---------------------------------------------------------------


def test_correction_writes_a_new_row_and_the_old_row_stays_readable(tmp_path):
    """A correction is a NEW row with is_correction=True, never an edit."""
    ledger = _ledger(tmp_path)
    at = datetime(2026, 7, 12, 10, 0, 0, tzinfo=timezone.utc)
    original = _offer(observed_at=at, price=Decimal("19.99"))
    corrected = _offer(observed_at=at, price=Decimal("18.49"))  # same instant, fixed price

    ledger.append([original])
    result = ledger.append([corrected], is_correction=True)

    assert result.rows_became_latest == 1  # tie-break: a correction wins at an equal timestamp

    history = ledger.history_for_sku("example.com", "ref-1")
    assert len(history) == 2  # the original row was never deleted or edited
    assert history[0].offer.price == Decimal("19.99")
    assert history[0].is_correction is False
    assert history[1].offer.price == Decimal("18.49")
    assert history[1].is_correction is True

    latest = ledger.latest_by_sku("example.com", "ref-1")
    assert latest.offer.price == Decimal("18.49")
    assert latest.is_correction is True


def test_a_normal_repeat_observation_is_not_flagged_as_a_correction(tmp_path):
    """Revisiting the same key on a later fetch cycle is NOT a correction --
    only an explicit is_correction=True call is."""
    ledger = _ledger(tmp_path)
    ledger.append([_offer(observed_at=datetime(2026, 7, 12, 10, 0, 0, tzinfo=timezone.utc))])
    ledger.append([_offer(observed_at=datetime(2026, 7, 12, 11, 0, 0, tzinfo=timezone.utc))])

    history = ledger.history_for_sku("example.com", "ref-1")
    assert [r.is_correction for r in history] == [False, False]


# -- product-centric lookup (PR-13's access pattern) --------------------------


def test_latest_for_product_returns_one_row_per_site(tmp_path):
    ledger = _ledger(tmp_path)
    ledger.append(
        [
            _offer(site="siteA.com", competitor_sku_ref="a-1", matched_product_id="SKU-X", price=Decimal("10.00")),
            _offer(site="siteB.com", competitor_sku_ref="b-1", matched_product_id="SKU-X", price=Decimal("12.00")),
            _offer(site="siteC.com", competitor_sku_ref="c-1", matched_product_id="SKU-OTHER", price=Decimal("5.00")),
        ]
    )

    rows = ledger.latest_for_product("SKU-X")
    by_site = {r.offer.site: r.offer.price for r in rows}
    assert by_site == {"siteA.com": Decimal("10.00"), "siteB.com": Decimal("12.00")}


def test_latest_for_product_returns_empty_for_an_unmatched_product(tmp_path):
    ledger = _ledger(tmp_path)
    ledger.append([_offer(matched_product_id="SKU-A")])
    assert ledger.latest_for_product("SKU-NOPE") == []


# -- duplicate batch handling ---------------------------------------------------


def test_duplicate_batch_id_for_the_same_site_is_rejected_without_orphan_files(tmp_path):
    ledger = _ledger(tmp_path)
    ledger.append([_offer()], batch_id="cycle-1")
    with pytest.raises(DuplicateBatchError):
        ledger.append([_offer(competitor_sku_ref="ref-2")], batch_id="cycle-1")

    # the failed second write left no orphan file or extra batch row
    history = ledger.history_for_sku("example.com", "ref-2")
    assert history == []


def test_same_batch_id_is_fine_across_different_sites(tmp_path):
    ledger = _ledger(tmp_path)
    result = ledger.append(
        [
            _offer(site="example.com", competitor_sku_ref="ref-1"),
            _offer(site="other.example", competitor_sku_ref="ref-9"),
        ],
        batch_id="cycle-1",
    )
    assert len(result.batches) == 2


# -- CSV fallback (no parquet engine) -----------------------------------------


def test_ledger_falls_back_to_csv_without_losing_decimal_precision(tmp_path, monkeypatch):
    """This repo's ``state`` extra is installed, so the CSV path is only
    exercised by forcing the flag, same convention as test_state.py's
    ``test_store_falls_back_to_csv_when_no_parquet_engine_is_available``."""
    monkeypatch.setattr(state_store, "_HAS_PARQUET_ENGINE", False)
    ledger = _ledger(tmp_path)
    offer = _offer(price=Decimal("123.456000"), price_normalized=Decimal("123.456000"))

    result = ledger.append([offer])

    assert result.batches[0].file_format == "csv"
    assert (tmp_path / "ledger" / result.batches[0].file_path).suffix == ".csv"

    got = ledger.latest_by_sku("example.com", "ref-1")
    assert got.offer == offer
    assert repr(got.offer.price) == "Decimal('123.456000')"  # exact scale preserved, not rounded


def test_ledger_uses_parquet_when_engine_is_available(tmp_path):
    ledger = _ledger(tmp_path)
    result = ledger.append([_offer()])
    assert result.batches[0].file_format == "parquet"


# -- default_ledger singleton --------------------------------------------------


def test_default_ledger_is_a_lazily_constructed_singleton_at_the_configured_base_path(tmp_path, monkeypatch):
    import src.pricing_intel.ledger as ledger_module

    monkeypatch.setattr(ledger_module, "_default_ledger", None)
    monkeypatch.setattr(ledger_module, "DEFAULT_BASE_PATH", str(tmp_path / "custom_ledger"))

    got = default_ledger()
    again = default_ledger()

    assert got.base_path == tmp_path / "custom_ledger"
    assert got is again


def test_default_base_path_constant_has_a_sane_fallback():
    assert DEFAULT_BASE_PATH  # non-empty either way (env override or repo default)
