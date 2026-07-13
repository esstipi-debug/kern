"""Precision test for the match/ pipeline against a hand-authored labeled
set (Linchpin 3.0 PR-14, plan S6.5's closing paragraph: "Meta: precision
>=95% en confirmed").

``tests/fixtures/matching_labeled.csv`` -- 45 hand-authored, unambiguous
synthetic pairs (same/different) spanning five categories (see the CSV's
own rows / this file's ``CATEGORIES`` docstring below):
  A. gtin_exact          (10) -- identical, check-digit-valid GTIN on both
     sides. Routed through ``gtin.match_by_gtin`` -- confirmed by
     construction, never scored probabilistically.
  B. same_reworded       (10) -- same product, no GTIN, matching decisive
     attribute + a near-identical (reordered/reformatted) title.
  C. attribute_conflict  (10) -- similar titles but a decisive attribute
     (pack_size/size/model/capacity/color) genuinely differs -- different
     SKUs, not a match.
  D. clearly_different   (10) -- unrelated products, different brands.
  E. ambiguous_no_attrs   (5) -- same brand, similar wording, but NO
     decisive attribute recorded on either side -- a real product-tier
     variant that text similarity alone cannot resolve (four "different",
     one "same" -- this bucket exists to prove the pipeline sends these to
     ``suspect`` instead of guessing, not to be auto-confirmed either way).

This is NOT sourced from WDC Products or any external dataset (plan S6.5
names WDC as a candidate but flags "[VERIFICAR-EN-PR: disponibilidad/
licencia]" -- sourcing and manually verifying licensing terms for a
third-party labeled corpus is explicitly out of scope for an autonomous PR;
see the PR brief). Every row here is invented for this PR, with an obvious,
independently-checkable correct label.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from src.pricing_intel.match.fuzzy import ProductAttributes
from src.pricing_intel.match.gtin import match_by_gtin
from src.pricing_intel.match.probabilistic import classify_score, score_pair

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "matching_labeled.csv"
MIN_CONFIRMED_PRECISION = 0.95  # plan S6.5: "Meta: precision >=95% en confirmed"


def _parse_attrs(raw: str) -> dict[str, str]:
    if not raw:
        return {}
    out: dict[str, str] = {}
    for pair in raw.split(";"):
        if not pair.strip():
            continue
        key, _, value = pair.partition("=")
        out[key.strip()] = value.strip()
    return out


def _load_rows() -> list[dict[str, str]]:
    with FIXTURE_PATH.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _classify_row(row: dict[str, str]) -> tuple[str, str, float]:
    """Run one fixture row through the real pipeline (GTIN first, falling
    back to the probabilistic scorer) -- returns (status, method, score)."""
    gtin_candidate = match_by_gtin(
        row["our_product_id"],
        row["our_gtin"] or None,
        row["competitor_sku_ref"],
        row["competitor_site"],
        row["competitor_gtin"] or None,
    )
    if gtin_candidate is not None:
        return gtin_candidate.status, "gtin", gtin_candidate.score

    our = ProductAttributes(
        row["our_product_id"], row["our_title"], row["our_brand"], _parse_attrs(row["our_attrs"])
    )
    competitor = ProductAttributes(
        "competitor", row["competitor_title"], row["competitor_brand"], _parse_attrs(row["competitor_attrs"])
    )
    result = score_pair(our, competitor)
    return classify_score(result.score), "probabilistic", result.score


def test_fixture_file_has_between_30_and_60_rows_per_pr_brief() -> None:
    rows = _load_rows()
    assert 30 <= len(rows) <= 60


def test_fixture_covers_the_three_required_categories() -> None:
    rows = _load_rows()
    categories = {row["category"] for row in rows}
    assert "gtin_exact" in categories or "same_reworded" in categories  # exact matches
    assert "attribute_conflict" in categories  # near-duplicates with pack-size-style differences
    assert "clearly_different" in categories  # clearly different products


def test_confirmed_bucket_precision_meets_the_plan_target() -> None:
    rows = _load_rows()
    confirmed = [(row, *_classify_row(row)) for row in rows]
    confirmed = [(row, status, method, score) for row, status, method, score in confirmed if status == "confirmed"]

    assert confirmed, "expected at least one confirmed pair in the labeled fixture"
    correct = sum(1 for row, _status, _method, _score in confirmed if row["label"] == "same")
    precision = correct / len(confirmed)

    assert precision >= MIN_CONFIRMED_PRECISION, (
        f"confirmed-bucket precision {precision:.4f} ({correct}/{len(confirmed)}) "
        f"is below the plan's {MIN_CONFIRMED_PRECISION:.0%} target"
    )


def test_no_attribute_conflict_pair_is_ever_confirmed() -> None:
    """A decisive attribute conflict is positive evidence of a different
    SKU (probabilistic.py's hard ceiling) -- none of category C's 10 rows
    may land in 'confirmed', regardless of how similar their titles read."""
    rows = [r for r in _load_rows() if r["category"] == "attribute_conflict"]
    assert rows  # sanity: the category exists in the fixture
    for row in rows:
        status, _method, _score = _classify_row(row)
        assert status != "confirmed", f"{row['pair_id']} (attribute conflict) was wrongly confirmed"


def test_ambiguous_category_never_lands_in_confirmed() -> None:
    """Category E pairs are deliberately unresolvable from text alone (same
    brand, similar wording, no attribute evidence) -- the QA bar is that
    these are NEVER silently auto-confirmed, whether the true label is
    'same' or 'different'. They may land in 'suspect' (awaiting adjudicate.py
    / human review) or 'rejected', but never 'confirmed'."""
    rows = [r for r in _load_rows() if r["category"] == "ambiguous_no_attrs"]
    assert rows
    for row in rows:
        status, _method, _score = _classify_row(row)
        assert status != "confirmed", f"{row['pair_id']} (genuinely ambiguous) was wrongly auto-confirmed"


def test_a_specific_ambiguous_pair_lands_in_suspect() -> None:
    """Concrete QA example named in the PR brief: 'a genuinely ambiguous
    pair lands in suspect, not silently auto-confirmed'."""
    rows = _load_rows()
    row = next(r for r in rows if r["pair_id"] == "E01")
    assert row["our_title"] == "Samsung Galaxy S23 Smartphone"
    status, method, score = _classify_row(row)
    assert method == "probabilistic"
    assert status == "suspect"
    assert score == pytest.approx(0.9484375)


def test_gtin_rows_are_confirmed_via_the_gtin_method_not_probabilistic() -> None:
    rows = [r for r in _load_rows() if r["category"] == "gtin_exact"]
    assert rows
    for row in rows:
        status, method, score = _classify_row(row)
        assert status == "confirmed"
        assert method == "gtin"
        assert score == 0.99
