"""Tests for src/pricing_intel/match/sku_map.py (Linchpin 3.0 PR-14, plan
S6.5 point 5 -- the versioned sku_map).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.pricing_intel.match.sku_map import AUTO_CONFIRMED_BY, LLM_CONFIRMED_BY, SkuMap
from src.pricing_intel.models import MatchCandidate

SITE = "example-retailer.test"
COMPETITOR_REF = "https://example-retailer.test/p/123"
OUR_PRODUCT_ID = "SKU-1"
NOW = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)


def _confirmed_candidate(**overrides: object) -> MatchCandidate:
    defaults: dict[str, object] = dict(
        our_product_id=OUR_PRODUCT_ID,
        competitor_sku_ref=COMPETITOR_REF,
        site=SITE,
        method="gtin",
        score=0.99,
        status="confirmed",
        reason="gtin_exact_match:4006381333931",
        confirmed_by=AUTO_CONFIRMED_BY,
        confirmed_at=NOW,
    )
    defaults.update(overrides)
    return MatchCandidate(**defaults)  # type: ignore[arg-type]


def _suspect_candidate(**overrides: object) -> MatchCandidate:
    defaults: dict[str, object] = dict(
        our_product_id=OUR_PRODUCT_ID,
        competitor_sku_ref=COMPETITOR_REF,
        site=SITE,
        method="probabilistic",
        score=0.7,
        status="suspect",
        reason="probabilistic_score_inconclusive",
        confirmed_by=None,
        confirmed_at=None,
    )
    defaults.update(overrides)
    return MatchCandidate(**defaults)  # type: ignore[arg-type]


@pytest.fixture
def sku_map(tmp_path: Path) -> SkuMap:
    store = SkuMap(tmp_path / "sku_map")
    yield store
    store.close()


# -- record / latest -------------------------------------------------------------


def test_record_first_entry_gets_version_1(sku_map: SkuMap) -> None:
    entry = sku_map.record(_confirmed_candidate(), now=NOW)
    assert entry.version == 1
    assert entry.status == "confirmed"
    assert entry.confirmed_by == "auto"
    assert entry.recorded_at == NOW


def test_latest_returns_none_for_an_unrecorded_key(sku_map: SkuMap) -> None:
    assert sku_map.latest(OUR_PRODUCT_ID, COMPETITOR_REF, SITE) is None


def test_latest_reflects_the_most_recently_recorded_entry(sku_map: SkuMap) -> None:
    sku_map.record(_suspect_candidate(), now=NOW)
    latest = sku_map.latest(OUR_PRODUCT_ID, COMPETITOR_REF, SITE)
    assert latest is not None
    assert latest.status == "suspect"
    assert latest.version == 1


# -- append-only / versioned re-review (golden rule 8) --------------------------


def test_a_re_review_appends_a_new_version_never_overwrites(sku_map: SkuMap) -> None:
    first = sku_map.record(_suspect_candidate(), now=NOW)
    assert first.version == 1

    reviewed = _confirmed_candidate(
        method="human", score=1.0, reason="reviewed manually, same product", confirmed_by="reviewer@example.test"
    )
    second = sku_map.record(reviewed, now=NOW)
    assert second.version == 2

    history = sku_map.history(OUR_PRODUCT_ID, COMPETITOR_REF, SITE)
    assert [h.version for h in history] == [1, 2]
    assert history[0].status == "suspect"  # the OLD verdict is still readable, unchanged
    assert history[1].status == "confirmed"
    assert history[1].confirmed_by == "reviewer@example.test"

    latest = sku_map.latest(OUR_PRODUCT_ID, COMPETITOR_REF, SITE)
    assert latest is not None
    assert latest.version == 2
    assert latest.status == "confirmed"


def test_three_successive_reviews_keep_every_version(sku_map: SkuMap) -> None:
    sku_map.record(_suspect_candidate(), now=NOW)
    sku_map.record(_suspect_candidate(reason="second look, still unclear"), now=NOW)
    sku_map.record(_confirmed_candidate(method="human", confirmed_by="reviewer@example.test"), now=NOW)

    history = sku_map.history(OUR_PRODUCT_ID, COMPETITOR_REF, SITE)
    assert [h.version for h in history] == [1, 2, 3]


def test_different_competitor_sku_refs_get_independent_version_sequences(sku_map: SkuMap) -> None:
    sku_map.record(_confirmed_candidate(competitor_sku_ref="https://a.test/p/1"), now=NOW)
    entry = sku_map.record(_confirmed_candidate(competitor_sku_ref="https://b.test/p/1"), now=NOW)
    assert entry.version == 1  # independent key, own version sequence


# -- confirmed_by required for status=confirmed ----------------------------------


def test_record_rejects_confirmed_status_without_confirmed_by(sku_map: SkuMap) -> None:
    # MatchCandidate itself (models.py, PR-10) allows confirmed_by=None --
    # sku_map.record() is where THIS store's own invariant (confirmed
    # entries must say who/what confirmed them) is enforced.
    unconfirmed = MatchCandidate(
        our_product_id=OUR_PRODUCT_ID,
        competitor_sku_ref=COMPETITOR_REF,
        site=SITE,
        method="probabilistic",
        score=0.97,
        status="confirmed",
        reason="high score",
        confirmed_by=None,
    )
    with pytest.raises(ValueError):
        sku_map.record(unconfirmed)


def test_record_accepts_llm_as_confirmed_by_when_a_caller_chooses_to(sku_map: SkuMap) -> None:
    # adjudicate.py itself never writes to sku_map (see its module
    # docstring) -- but a caller who explicitly accepts an LLM's proposal as
    # sufficient basis may record it that way, and that value must round-trip.
    entry = sku_map.record(_confirmed_candidate(method="llm", confirmed_by=LLM_CONFIRMED_BY), now=NOW)
    assert entry.confirmed_by == "llm"


# -- to_match_candidate round trip -----------------------------------------------


def test_entry_round_trips_to_a_match_candidate(sku_map: SkuMap) -> None:
    original = _confirmed_candidate()
    sku_map.record(original, now=NOW)
    entry = sku_map.latest(OUR_PRODUCT_ID, COMPETITOR_REF, SITE)
    assert entry is not None
    reconstructed = entry.to_match_candidate()
    assert reconstructed.our_product_id == original.our_product_id
    assert reconstructed.competitor_sku_ref == original.competitor_sku_ref
    assert reconstructed.site == original.site
    assert reconstructed.method == original.method
    assert reconstructed.score == original.score
    assert reconstructed.status == original.status
    assert reconstructed.confirmed_by == original.confirmed_by
    assert reconstructed.confirmed_at == original.confirmed_at


# -- latest_confirmed_for_product (the QA invariant plan S6.5 states) -----------


def test_latest_confirmed_for_product_only_returns_confirmed_latest_rows(sku_map: SkuMap) -> None:
    sku_map.record(_confirmed_candidate(competitor_sku_ref="https://a.test/p/1", site="a.test"), now=NOW)
    sku_map.record(_suspect_candidate(competitor_sku_ref="https://b.test/p/1", site="b.test"), now=NOW)
    # a third key that WAS confirmed but got demoted by a later review --
    # latest_confirmed_for_product must reflect the CURRENT state, not history.
    sku_map.record(_confirmed_candidate(competitor_sku_ref="https://c.test/p/1", site="c.test"), now=NOW)
    sku_map.record(
        MatchCandidate(
            our_product_id=OUR_PRODUCT_ID,
            competitor_sku_ref="https://c.test/p/1",
            site="c.test",
            method="human",
            score=0.2,
            status="rejected",
            reason="reviewer determined this was a different product after all",
            confirmed_by=None,
        ),
        now=NOW,
    )

    confirmed = sku_map.latest_confirmed_for_product(OUR_PRODUCT_ID)
    sites = {c.site for c in confirmed}
    assert sites == {"a.test"}  # b.test never confirmed; c.test demoted to rejected


def test_latest_confirmed_for_product_returns_empty_for_unknown_product(sku_map: SkuMap) -> None:
    assert sku_map.latest_confirmed_for_product("SKU-NONE") == []


# -- list_all_confirmed (Linchpin 3.0 PR-15 -- continuous monitoring's read path) --


def test_list_all_confirmed_spans_every_product_reflects_current_state(sku_map: SkuMap) -> None:
    sku_map.record(
        _confirmed_candidate(our_product_id="SKU-1", competitor_sku_ref="MLA111", site="api.mercadolibre.com"),
        now=NOW,
    )
    sku_map.record(
        _confirmed_candidate(our_product_id="SKU-2", competitor_sku_ref="MLA222", site="api.mercadolibre.com"),
        now=NOW,
    )
    sku_map.record(
        _suspect_candidate(our_product_id="SKU-3", competitor_sku_ref="MLA333", site="api.mercadolibre.com"),
        now=NOW,
    )
    # SKU-4 was confirmed, then demoted by a later review -- list_all_confirmed
    # must reflect the CURRENT (rejected) state, not the earlier confirmed one.
    sku_map.record(
        _confirmed_candidate(our_product_id="SKU-4", competitor_sku_ref="MLA444", site="api.mercadolibre.com"),
        now=NOW,
    )
    sku_map.record(
        MatchCandidate(
            our_product_id="SKU-4", competitor_sku_ref="MLA444", site="api.mercadolibre.com",
            method="human", score=0.1, status="rejected",
            reason="reviewer determined this was a different product after all", confirmed_by=None,
        ),
        now=NOW,
    )

    confirmed = sku_map.list_all_confirmed()
    product_ids = {e.our_product_id for e in confirmed}
    assert product_ids == {"SKU-1", "SKU-2"}


def test_list_all_confirmed_empty_store_returns_empty_list(sku_map: SkuMap) -> None:
    assert sku_map.list_all_confirmed() == []


# -- latest_confirmed_for_competitor_ref (the REVERSE lookup, PR-15's L2 receiver) --


def test_latest_confirmed_for_competitor_ref_finds_the_matching_product(sku_map: SkuMap) -> None:
    sku_map.record(_confirmed_candidate(), now=NOW)  # OUR_PRODUCT_ID / COMPETITOR_REF / SITE
    entry = sku_map.latest_confirmed_for_competitor_ref(COMPETITOR_REF, SITE)
    assert entry is not None
    assert entry.our_product_id == OUR_PRODUCT_ID
    assert entry.status == "confirmed"


def test_latest_confirmed_for_competitor_ref_returns_none_for_unknown_ref(sku_map: SkuMap) -> None:
    assert sku_map.latest_confirmed_for_competitor_ref("https://unseen.test/p/1", "unseen.test") is None


def test_latest_confirmed_for_competitor_ref_returns_none_when_only_suspect(sku_map: SkuMap) -> None:
    sku_map.record(_suspect_candidate(), now=NOW)
    assert sku_map.latest_confirmed_for_competitor_ref(COMPETITOR_REF, SITE) is None


def test_latest_confirmed_for_competitor_ref_reflects_current_state_after_demotion(sku_map: SkuMap) -> None:
    sku_map.record(_confirmed_candidate(), now=NOW)
    sku_map.record(
        MatchCandidate(
            our_product_id=OUR_PRODUCT_ID, competitor_sku_ref=COMPETITOR_REF, site=SITE,
            method="human", score=0.1, status="rejected", reason="demoted on review", confirmed_by=None,
        ),
        now=NOW,
    )
    assert sku_map.latest_confirmed_for_competitor_ref(COMPETITOR_REF, SITE) is None
