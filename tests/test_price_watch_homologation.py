"""Tests for jobs/price_watch.py's Task 5 additions (Discovery-Assisted Price
Intel plan, PR-5): wiring ``homologate()`` into the playbook after discovery,
persisting confirmed/suspect rows to the versioned ``sku_map`` (never
rejected/unmatched rows), and publishing the homologation table deliverable.

Every probabilistic worked example reused below is copied verbatim from
``tests/test_pricing_intel_homologate.py`` (same titles/brands/catalog, same
hand-verified scores) -- this file does not invent new numbers, it only
proves the NEW persistence/publishing wiring built on top of ``homologate()``.

Guarantees under test (mirrors the task brief's risk callouts):
- only ``confirmed``/``suspect`` rows are ever written to ``sku_map`` --
  ``rejected``/unmatched rows are reported in the table's outputs but never
  persisted;
- ``confirmed_by`` is set ONLY for a genuine auto-confirm row; a suspect
  row's persisted entry always carries ``confirmed_by=None``;
- a re-run never overwrites a prior ``sku_map`` version -- the SAME pair
  homologated twice adds a NEW version, verified via ``SkuMap.history``;
- the shared ``default_sku_map()`` singleton is never closed by
  ``run_homologation`` -- mirrors ``jobs.price_monitor.run_price_monitor_cycle``'s
  own discipline;
- every string cell in the written CSV passes through
  ``src.sanitize.defuse_formula`` before it reaches disk;
- ``HomologationReport.unmatched`` gets its own CSV, never silently absent.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from jobs import price_watch as pw
from src.pricing_intel.discover import DiscoveredProduct
from src.pricing_intel.match.fuzzy import ProductAttributes
from src.pricing_intel.match.sku_map import AUTO_CONFIRMED_BY, SkuMap

NOW = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 7, 15, 9, 0, tzinfo=timezone.utc)
VALID_EAN13 = "4006381333931"

# Verbatim from tests/test_pricing_intel_homologate.py's own OUR_CATALOG --
# this file reuses the same hand-verified worked examples, not new numbers.
OUR_CATALOG = (
    ProductAttributes("our-coke", "Coca-Cola Bottle 2L", "Coca-Cola", {"pack_size": "2l"}),
    ProductAttributes(
        "our-sony-xm5",
        "Sony WH-1000XM5 Wireless Noise Cancelling Headphones",
        "Sony",
        {"model": "xm5"},
    ),
    ProductAttributes("our-samsung-s23", "Samsung Galaxy S23 Smartphone", "Samsung"),
    ProductAttributes("our-acme", "Acme Widget Pro 3000 Deluxe Edition", "Acme"),
)


def _discovered(
    *,
    url: str = "https://competitor.test/p/1",
    site: str = "competitor.test",
    title: str | None,
    brand: str | None,
    gtin: str | None = None,
    offers: tuple[dict, ...] = (),
) -> DiscoveredProduct:
    return DiscoveredProduct(
        url=url, site=site, title=title, brand=brand, gtin=gtin, price_hint=None, offers=offers
    )


@pytest.fixture
def sku_map(tmp_path: Path) -> SkuMap:
    store = SkuMap(tmp_path / "sku_map")
    yield store
    store.close()


# -- persistence: confirmed rows ---------------------------------------------


def test_confirmed_rows_persisted_to_sku_map(sku_map: SkuMap) -> None:
    discovered = _discovered(
        title=None, brand=None, gtin=VALID_EAN13, url="https://competitor.test/p/coke"
    )
    payload = {
        "discovered": [discovered],
        "our_catalog": OUR_CATALOG,
        "our_gtins": {"our-coke": VALID_EAN13},
    }

    report = pw.run_homologation(payload, sku_map=sku_map, now=NOW)

    assert report.n_confirmed == 1
    confirmed = sku_map.list_all_confirmed()
    assert len(confirmed) == 1
    entry = confirmed[0]
    assert entry.our_product_id == "our-coke"
    assert entry.competitor_sku_ref == "https://competitor.test/p/coke"
    assert entry.status == "confirmed"
    assert entry.confirmed_by == AUTO_CONFIRMED_BY
    assert entry.confirmed_at == NOW


def test_rejected_and_unmatched_rows_never_persisted(sku_map: SkuMap) -> None:
    # Missing title/brand/gtin -- homologate() reports this honestly as
    # unmatched (our_product_id is None); it must never reach sku_map.
    discovered = _discovered(title=None, brand=None, url="https://competitor.test/p/unknown")
    payload = {"discovered": [discovered], "our_catalog": OUR_CATALOG}

    report = pw.run_homologation(payload, sku_map=sku_map, now=NOW)

    assert report.n_unmatched == 1
    assert sku_map.list_all_confirmed() == []
    assert (
        sku_map.history("our-coke", "https://competitor.test/p/unknown", "competitor.test") == []
    )


# -- persistence: suspect rows (recorded, never confirmed) ------------------


def test_suspect_rows_recorded_but_not_confirmed(sku_map: SkuMap) -> None:
    # Hand-verified worked example 3 from test_pricing_intel_homologate.py:
    # score == 0.9484375, SUSPECT_THRESHOLD <= score < CONFIRM_THRESHOLD.
    discovered = _discovered(
        title="Samsung Galaxy S23 Ultra Smartphone",
        brand="Samsung",
        url="https://competitor.test/p/samsung",
    )
    payload = {"discovered": [discovered], "our_catalog": OUR_CATALOG}

    report = pw.run_homologation(payload, sku_map=sku_map, now=NOW)

    row = report.rows[0]
    assert row.status == "suspect"  # sanity check on the reused worked example
    assert row.our_product_id == "our-samsung-s23"

    assert sku_map.list_all_confirmed() == []  # never counted as confirmed
    history = sku_map.history("our-samsung-s23", "https://competitor.test/p/samsung", "competitor.test")
    assert len(history) == 1
    entry = history[0]
    assert entry.status == "suspect"
    assert entry.confirmed_by is None  # the one invariant this task protects
    assert entry.confirmed_at is None


# -- golden rule 8: append-only, versioned -- a re-run never overwrites ------


def test_rerun_adds_a_new_version_never_overwrites(sku_map: SkuMap) -> None:
    discovered = _discovered(
        title="Samsung Galaxy S23 Ultra Smartphone",
        brand="Samsung",
        url="https://competitor.test/p/samsung",
    )
    payload = {"discovered": [discovered], "our_catalog": OUR_CATALOG}

    pw.run_homologation(payload, sku_map=sku_map, now=NOW)
    pw.run_homologation(payload, sku_map=sku_map, now=LATER)

    history = sku_map.history("our-samsung-s23", "https://competitor.test/p/samsung", "competitor.test")
    assert len(history) == 2
    assert [entry.version for entry in history] == [1, 2]
    assert history[0].recorded_at == NOW
    assert history[1].recorded_at == LATER
    # the first version is still intact -- never overwritten in place
    assert history[0].status == "suspect"
    assert history[0].confirmed_by is None


# -- the shared singleton is never closed -------------------------------------


def test_shared_sku_map_singleton_not_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = SkuMap(tmp_path / "shared_sku_map")

    def _raise_if_closed() -> None:
        raise AssertionError("run_homologation must never close the shared sku_map singleton")

    monkeypatch.setattr(store, "close", _raise_if_closed)
    monkeypatch.setattr(pw, "default_sku_map", lambda: store)

    discovered = _discovered(title=None, brand=None, gtin=VALID_EAN13)
    payload = {
        "discovered": [discovered],
        "our_catalog": OUR_CATALOG,
        "our_gtins": {"our-coke": VALID_EAN13},
    }

    report = pw.run_homologation(payload, now=NOW)  # sku_map=None -> must resolve via default_sku_map()

    assert report.n_confirmed == 1
    # the singleton is still open and usable -- proves close() was never called.
    # (store.close() is intentionally never invoked in this test: it is patched
    # to raise, and a real teardown isn't needed for a tmp_path-scoped sqlite
    # file in a single test process.)
    assert len(store.list_all_confirmed()) == 1


# -- _persist_row's own independent guard (review-fix, Task 5 round 1) -------


def test_persist_row_rejects_non_confirmed_row_with_confirmed_by(sku_map: SkuMap) -> None:
    """The safety-critical invariant -- a non-``confirmed`` row must never
    carry a non-``None`` ``confirmed_by`` -- is already enforced by
    ``HomologationRow.__post_init__`` for any row built through the normal
    constructor (see ``test_suspect_rows_recorded_but_not_confirmed`` above).
    This test proves ``_persist_row`` ALSO independently enforces it, rather
    than relying solely on that upstream guard: it builds a violating
    ``HomologationRow`` by bypassing ``__init__``/``__post_init__`` entirely
    (``object.__new__`` + ``object.__setattr__`` on the frozen dataclass --
    the only way to construct one, since the normal constructor structurally
    refuses this combination), then calls ``_persist_row`` directly and
    asserts it raises ``ValueError`` *before* anything reaches ``sku_map``.
    This is the scenario the review flagged: a future refactor of
    ``homologate.py``/``adjudicate.py``, or a new caller building a row some
    other way, that lets a non-``None`` ``confirmed_by`` slip onto a
    ``suspect``/``rejected`` row would sail straight through this function
    into durable storage with no local guard catching it.
    """
    violating_row = object.__new__(pw.HomologationRow)
    object.__setattr__(violating_row, "our_product_id", "our-samsung-s23")
    object.__setattr__(violating_row, "competitor_sku_ref", "https://competitor.test/p/violating")
    object.__setattr__(violating_row, "site", "competitor.test")
    object.__setattr__(violating_row, "method", "probabilistic")
    object.__setattr__(violating_row, "score", 0.6)
    object.__setattr__(violating_row, "status", "suspect")
    object.__setattr__(violating_row, "reason", "bypassed-post-init-validation")
    object.__setattr__(violating_row, "confirmed_by", "auto")  # the violation under test

    with pytest.raises(ValueError, match="confirmed_by"):
        pw._persist_row(sku_map, violating_row, now=NOW)

    # nothing reached the store -- the guard fired before sku_map.record().
    assert (
        sku_map.history(
            "our-samsung-s23", "https://competitor.test/p/violating", "competitor.test"
        )
        == []
    )


# -- publishing: the homologation table + unmatched CSV ----------------------


def test_table_written_with_all_columns_and_unmatched_sheet(tmp_path: Path, sku_map: SkuMap) -> None:
    confirmed_discovered = _discovered(
        title=None, brand=None, gtin=VALID_EAN13, url="https://competitor.test/p/coke"
    )
    unmatched_discovered = _discovered(
        title=None, brand=None, url="https://competitor.test/p/unknown"
    )
    payload = {
        "discovered": [confirmed_discovered, unmatched_discovered],
        "our_catalog": OUR_CATALOG,
        "our_gtins": {"our-coke": VALID_EAN13},
    }
    report = pw.run_homologation(payload, sku_map=sku_map, now=NOW)
    assert report.n_unmatched == 1  # sanity check before asserting the deliverable

    written = pw.write_homologation(report, tmp_path / "deliverables", client="Acme Corp")

    assert "csv" in written
    assert "unmatched_csv" in written
    assert written["csv"].exists()
    assert written["unmatched_csv"].exists()

    table = pd.read_csv(written["csv"])
    assert list(table.columns) == ["my_sku", "competitor_product", "method", "confidence", "status"]
    assert len(table) == 1
    assert table.iloc[0]["my_sku"] == "our-coke"
    assert table.iloc[0]["competitor_product"] == "https://competitor.test/p/coke"
    assert table.iloc[0]["status"] == "confirmed"
    # the unmatched product never silently appears in the matched table
    assert "https://competitor.test/p/unknown" not in table["competitor_product"].tolist()

    unmatched = pd.read_csv(written["unmatched_csv"])
    assert len(unmatched) == 1
    assert unmatched.iloc[0]["competitor_product"] == "https://competitor.test/p/unknown"
    assert unmatched.iloc[0]["reason"] == "missing_title_or_brand"


def test_empty_report_still_writes_both_files_with_headers(tmp_path: Path) -> None:
    from src.pricing_intel.homologate import HomologationReport

    empty_report = HomologationReport(rows=(), n_confirmed=0, n_suspect=0, n_unmatched=0, unmatched=())

    written = pw.write_homologation(empty_report, tmp_path / "deliverables")

    table = pd.read_csv(written["csv"])
    assert list(table.columns) == ["my_sku", "competitor_product", "method", "confidence", "status"]
    assert len(table) == 0

    unmatched = pd.read_csv(written["unmatched_csv"])
    assert list(unmatched.columns) == ["competitor_product", "site", "reason"]
    assert len(unmatched) == 0


# -- formula-injection guard --------------------------------------------------


def test_formula_injection_defused_in_table(tmp_path: Path, sku_map: SkuMap) -> None:
    malicious_ref = "=1+1"
    discovered = _discovered(title=None, brand=None, gtin=VALID_EAN13, url=malicious_ref)
    payload = {
        "discovered": [discovered],
        "our_catalog": OUR_CATALOG,
        "our_gtins": {"our-coke": VALID_EAN13},
    }
    report = pw.run_homologation(payload, sku_map=sku_map, now=NOW)

    written = pw.write_homologation(report, tmp_path / "deliverables")

    raw_text = written["csv"].read_text(encoding="utf-8")
    assert "\n=1+1" not in raw_text  # never a bare, unescaped formula in the file
    assert ",=1+1" not in raw_text

    table = pd.read_csv(written["csv"], dtype=str)
    cell = table.iloc[0]["competitor_product"]
    assert cell == "'" + malicious_ref  # leading single quote neutralizes it
    assert not cell.lstrip().startswith(("=", "+", "-", "@"))


def test_formula_injection_defused_in_unmatched_csv(tmp_path: Path, sku_map: SkuMap) -> None:
    malicious_ref = "=2+2"
    discovered = _discovered(title=None, brand=None, url=malicious_ref)
    payload = {"discovered": [discovered], "our_catalog": OUR_CATALOG}
    report = pw.run_homologation(payload, sku_map=sku_map, now=NOW)
    assert report.n_unmatched == 1

    written = pw.write_homologation(report, tmp_path / "deliverables")

    raw_text = written["unmatched_csv"].read_text(encoding="utf-8")
    assert "\n=2+2" not in raw_text
    assert ",=2+2" not in raw_text

    table = pd.read_csv(written["unmatched_csv"], dtype=str)
    cell = table.iloc[0]["competitor_product"]
    assert cell == "'" + malicious_ref
