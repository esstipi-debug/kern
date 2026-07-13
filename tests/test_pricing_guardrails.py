"""Tests for src/pricing_guardrails.py (Linchpin 3.0 PR-17, plan section 7 P5).

Hand-verified reference scenarios (see individual test docstrings for the
worked numbers):
- EU/UK Omnibus HARD gate: a discount % calculated against a higher,
  non-30-day price is BLOCKED even though the correct 30-day-lowest price
  is independently known and recorded in the evidence trail (the CJEU
  C-330/23 *Aldi Sud* fact pattern).
- CL/MX/CO SOFT gate: the identical mismatch WARNS (with the same evidence
  trail) instead of blocking; an inflate-then-discount pattern is flagged
  the same way.
- US MAP: an alert never blocks; the SAME numbers render different label
  text in the US ("MAP violation") vs EU/UK ("dispersion de precios /
  inteligencia de canal").
- Central gate: a changeset missing either a human-legible explanation or
  surviving citations is rejected.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pandas as pd
import pytest

from scm_agent.knowledge import GroundedCitation
from src.pricing_guardrails import (
    DEFAULT_MARKETS_CONFIG_DIR,
    DiscountComplianceError,
    InflateThenDiscountWarning,
    MarketNotConfiguredError,
    MarketRules,
    detect_inflate_then_discount,
    detect_map_alert,
    gate_price_changeset,
    load_market_rules,
    prior_price_30d_lowest,
    validate_discount_reference,
)
from src.state.store import StateStore
from src.state.system_state import snapshot
from src.writeback import Change, Changeset

MARKETS_DIR = "config/markets"


def _store(tmp_path) -> StateStore:
    return StateStore(tmp_path / "state")


def _prices_own(product_id: str, price: float, *, channel: str | None = None) -> pd.DataFrame:
    data = {"product_id": [product_id], "price": [price], "currency": ["USD"]}
    if channel is not None:
        data["channel"] = [channel]
    return pd.DataFrame(data)


def _seed(store: StateStore, cycle: str, product_id: str, price: float, when: datetime, *, channel=None):
    snapshot("prices_own", _prices_own(product_id, price, channel=channel), cycle, store=store, now=when)


# ---- market profile loading --------------------------------------------------


def test_eu_profile_is_hard_gate_with_dispersion_label():
    rules = load_market_rules("eu", config_dir=MARKETS_DIR)
    assert rules.discount_reference_gate == "hard"
    assert rules.map_alert_label == "dispersion de precios / inteligencia de canal"


def test_uk_profile_is_hard_gate_with_dispersion_label():
    rules = load_market_rules("uk", config_dir=MARKETS_DIR)
    assert rules.discount_reference_gate == "hard"
    assert rules.map_alert_label == "dispersion de precios / inteligencia de canal"


def test_us_profile_is_none_gate_with_violation_label():
    rules = load_market_rules("us", config_dir=MARKETS_DIR)
    assert rules.discount_reference_gate == "none"
    assert rules.map_alert_label == "MAP violation"


@pytest.mark.parametrize("market", ["cl", "mx", "co"])
def test_latam_profiles_are_soft_gate(market):
    rules = load_market_rules(market, config_dir=MARKETS_DIR)
    assert rules.discount_reference_gate == "soft"


def test_unknown_market_is_rejected():
    with pytest.raises(MarketNotConfiguredError):
        load_market_rules("zz", config_dir=MARKETS_DIR)


def test_market_rules_rejects_an_invalid_gate_value():
    with pytest.raises(ValueError):
        MarketRules(market="xx", discount_reference_gate="strict", discount_tolerance_pct=Decimal("0.5"),
                    map_alert_label="x")


def test_default_markets_config_dir_points_at_config_markets():
    assert str(DEFAULT_MARKETS_CONFIG_DIR).replace("\\", "/").endswith("config/markets")


# ---- prior_price_30d_lowest ----------------------------------------------------


def test_prior_price_30d_lowest_excludes_points_outside_the_window(tmp_path):
    """100 (60 days before as_of) is outside the 30-day window and must be
    excluded; 80 (22 days before) and 95 (7 days before) are inside it --
    the lowest of the two IN-window points, 80, is the answer."""
    store = _store(tmp_path)
    as_of = date(2026, 7, 12)
    _seed(store, "1", "SKU-1", 100.0, datetime(2026, 5, 1, tzinfo=timezone.utc))
    _seed(store, "2", "SKU-1", 80.0, datetime(2026, 6, 20, tzinfo=timezone.utc))
    _seed(store, "3", "SKU-1", 95.0, datetime(2026, 7, 5, tzinfo=timezone.utc))

    result = prior_price_30d_lowest("SKU-1", as_of, store=store)

    assert result == Decimal("80")


def test_prior_price_30d_lowest_returns_none_without_any_history(tmp_path):
    store = _store(tmp_path)
    assert prior_price_30d_lowest("SKU-NONE", date(2026, 7, 12), store=store) is None


def test_prior_price_30d_lowest_channel_filter(tmp_path):
    store = _store(tmp_path)
    as_of = date(2026, 7, 12)
    _seed(store, "1", "SKU-1", 80.0, datetime(2026, 7, 1, tzinfo=timezone.utc), channel="web")
    _seed(store, "2", "SKU-1", 60.0, datetime(2026, 7, 2, tzinfo=timezone.utc), channel="store-1")

    assert prior_price_30d_lowest("SKU-1", as_of, store=store, channel="web") == Decimal("80")
    assert prior_price_30d_lowest("SKU-1", as_of, store=store, channel="store-1") == Decimal("60")
    assert prior_price_30d_lowest("SKU-1", as_of, store=store) == Decimal("60")  # no filter: min across channels


def test_prior_price_30d_lowest_rejects_a_nonpositive_window():
    with pytest.raises(ValueError):
        prior_price_30d_lowest("SKU-1", date(2026, 7, 12), window_days=0)


# ---- EU/UK Omnibus hard gate: the Aldi Sud reproduction ------------------------


def test_aldi_sud_mismatched_percentage_is_blocked_in_eu(tmp_path):
    """The Aldi Sud fact pattern: a discount is announced as "30% off",
    calculated against some higher (non-30-day) reference, while the real
    30-day lowest price was 80. new_price=70 against prior_price_30d_lowest
    of 80 computes to round(100*(1-70/80), 2) = 12.50%, not 30% -- the
    stated percentage does not survive validation even though the CORRECT
    30-day-lowest value (80) is independently known and recorded in the
    evidence trail. This is exactly the CJEU C-330/23 holding: showing the
    right reference price elsewhere does not cure a percentage claim that
    was not calculated against it.
    """
    store = _store(tmp_path)
    as_of = date(2026, 7, 12)
    _seed(store, "1", "SKU-1", 100.0, datetime(2026, 5, 1, tzinfo=timezone.utc))  # outside window
    _seed(store, "2", "SKU-1", 80.0, datetime(2026, 6, 20, tzinfo=timezone.utc))  # true 30d low
    _seed(store, "3", "SKU-1", 95.0, datetime(2026, 7, 5, tzinfo=timezone.utc))

    with pytest.raises(DiscountComplianceError) as exc_info:
        validate_discount_reference(
            product_id="SKU-1", new_price=Decimal("70"), stated_discount_pct=Decimal("30"),
            market="eu", as_of=as_of, store=store, markets_dir=MARKETS_DIR,
        )

    validation = exc_info.value.validation
    assert validation.gate == "hard"
    assert validation.passed is False
    assert validation.prior_price_30d_low == Decimal("80")  # correctly computed and recorded...
    assert validation.computed_discount_pct == Decimal("12.50")
    assert validation.stated_discount_pct == Decimal("30")  # ...but the STATED 30% still doesn't match it
    assert any("80" in line for line in validation.evidence_trail)  # the 30d-low IS in the evidence trail


def test_matching_percentage_passes_the_eu_hard_gate(tmp_path):
    """Same 30-day-low (80) and new_price (70) as the Aldi Sud test, but the
    stated percentage now correctly matches the computed 12.50% -- passes,
    no exception."""
    store = _store(tmp_path)
    as_of = date(2026, 7, 12)
    _seed(store, "1", "SKU-1", 80.0, datetime(2026, 6, 20, tzinfo=timezone.utc))

    result = validate_discount_reference(
        product_id="SKU-1", new_price=Decimal("70"), stated_discount_pct=Decimal("12.5"),
        market="eu", as_of=as_of, store=store, markets_dir=MARKETS_DIR,
    )

    assert result.passed is True
    assert result.computed_discount_pct == Decimal("12.50")
    assert result.prior_price_30d_low == Decimal("80")


def test_eu_hard_gate_blocks_when_there_is_no_30day_history_at_all(tmp_path):
    """Golden Rule 14 (no silent caps): missing evidence is treated as a
    failed hard-gate validation, never a silent pass."""
    store = _store(tmp_path)
    with pytest.raises(DiscountComplianceError) as exc_info:
        validate_discount_reference(
            product_id="SKU-NEW", new_price=Decimal("70"), stated_discount_pct=Decimal("12.5"),
            market="eu", as_of=date(2026, 7, 12), store=store, markets_dir=MARKETS_DIR,
        )
    assert "no own-price history" in exc_info.value.validation.reason


# ---- CL/MX/CO soft gate: warn, never block -------------------------------------


def test_cl_soft_gate_warns_on_the_same_mismatch_the_eu_gate_blocks(tmp_path):
    store = _store(tmp_path)
    as_of = date(2026, 7, 12)
    _seed(store, "1", "SKU-1", 80.0, datetime(2026, 6, 20, tzinfo=timezone.utc))

    result = validate_discount_reference(  # must NOT raise
        product_id="SKU-1", new_price=Decimal("70"), stated_discount_pct=Decimal("30"),
        market="cl", as_of=as_of, store=store, markets_dir=MARKETS_DIR,
    )

    assert result.gate == "soft"
    assert result.passed is False
    assert result.computed_discount_pct == Decimal("12.50")
    assert result.reason is not None
    assert len(result.evidence_trail) > 0


def test_us_none_gate_never_blocks_regardless_of_mismatch(tmp_path):
    store = _store(tmp_path)
    as_of = date(2026, 7, 12)
    _seed(store, "1", "SKU-1", 80.0, datetime(2026, 6, 20, tzinfo=timezone.utc))

    result = validate_discount_reference(
        product_id="SKU-1", new_price=Decimal("70"), stated_discount_pct=Decimal("30"),
        market="us", as_of=as_of, store=store, markets_dir=MARKETS_DIR,
    )

    assert result.gate == "none"
    assert result.passed is True


# ---- CL/MX/CO inflate-then-discount pattern ------------------------------------


def test_detect_inflate_then_discount_flags_a_recent_raise(tmp_path):
    """Price raised from 50 to 65 (a +30% raise) 7 days before the proposed
    discount to 55 -- flagged even though 55 is still above the original 50
    baseline, because the discount is calculated off the artificially
    raised 65 peak."""
    store = _store(tmp_path)
    as_of = date(2026, 7, 12)
    _seed(store, "1", "SKU-2", 50.0, datetime(2026, 6, 30, tzinfo=timezone.utc))
    _seed(store, "2", "SKU-2", 65.0, datetime(2026, 7, 5, tzinfo=timezone.utc))

    warning = detect_inflate_then_discount("SKU-2", as_of, Decimal("55"), lookback_days=14, store=store)

    assert warning is not None
    assert warning.raised_from == Decimal("50")
    assert warning.raised_to == Decimal("65")
    assert warning.new_price == Decimal("55")
    assert len(warning.evidence_trail) > 0


def test_detect_inflate_then_discount_returns_none_when_price_never_rose(tmp_path):
    store = _store(tmp_path)
    as_of = date(2026, 7, 12)
    _seed(store, "1", "SKU-3", 70.0, datetime(2026, 6, 30, tzinfo=timezone.utc))
    _seed(store, "2", "SKU-3", 60.0, datetime(2026, 7, 5, tzinfo=timezone.utc))  # declining, no raise

    assert detect_inflate_then_discount("SKU-3", as_of, Decimal("55"), lookback_days=14, store=store) is None


def test_detect_inflate_then_discount_returns_none_with_insufficient_history(tmp_path):
    store = _store(tmp_path)
    as_of = date(2026, 7, 12)
    _seed(store, "1", "SKU-4", 50.0, datetime(2026, 7, 5, tzinfo=timezone.utc))  # only one point

    assert detect_inflate_then_discount("SKU-4", as_of, Decimal("45"), lookback_days=14, store=store) is None


def test_cl_market_scenario_warns_with_evidence_trail_and_never_blocks(tmp_path):
    """The QA scenario from the plan: a CL/MX/CO inflate-then-discount
    pattern produces a warning with the evidence trail attached, and does
    NOT block the changeset (validate_discount_reference raises nothing for
    a soft-gated market, even alongside an inflate-then-discount signal)."""
    store = _store(tmp_path)
    as_of = date(2026, 7, 12)
    _seed(store, "1", "SKU-2", 50.0, datetime(2026, 6, 30, tzinfo=timezone.utc))
    _seed(store, "2", "SKU-2", 65.0, datetime(2026, 7, 5, tzinfo=timezone.utc))

    # 30-day-lowest for SKU-2 is 50 (both points are within the 30-day window);
    # new_price=55 vs 50 -> computed = round(100*(1 - 55/50), 2) = -10.00%.
    validation = validate_discount_reference(
        product_id="SKU-2", new_price=Decimal("55"), stated_discount_pct=Decimal("-10"),
        market="cl", as_of=as_of, store=store, markets_dir=MARKETS_DIR,
    )
    warning = detect_inflate_then_discount("SKU-2", as_of, Decimal("55"), lookback_days=14, store=store)

    assert validation.passed is True  # the % itself matches -- no block
    assert isinstance(warning, InflateThenDiscountWarning)  # the SEPARATE raise-then-discount signal still fires
    assert warning.raised_from == Decimal("50") and warning.raised_to == Decimal("65")
    assert len(warning.evidence_trail) > 0


# ---- MAP: observe-and-alert only, jurisdiction-keyed label ---------------------


def test_map_alert_uses_violation_language_in_us():
    alert = detect_map_alert("SKU-1", Decimal("45"), Decimal("50"), market="us", markets_dir=MARKETS_DIR)
    assert alert is not None
    assert alert.label == "MAP violation"
    assert "violation" in alert.message.lower()
    assert alert.severity == "alert"


def test_map_alert_uses_dispersion_language_in_eu_for_the_identical_numbers():
    alert = detect_map_alert("SKU-1", Decimal("45"), Decimal("50"), market="eu", markets_dir=MARKETS_DIR)
    assert alert is not None
    assert alert.label == "dispersion de precios / inteligencia de canal"
    assert "violation" not in alert.message.lower()
    assert alert.severity == "alert"


def test_map_alert_text_differs_by_market_for_the_same_underlying_signal():
    us_alert = detect_map_alert("SKU-1", Decimal("45"), Decimal("50"), market="us", markets_dir=MARKETS_DIR)
    eu_alert = detect_map_alert("SKU-1", Decimal("45"), Decimal("50"), market="eu", markets_dir=MARKETS_DIR)

    assert us_alert.observed_price == eu_alert.observed_price
    assert us_alert.map_price == eu_alert.map_price
    assert us_alert.shortfall_pct == eu_alert.shortfall_pct == Decimal("10.00")
    assert us_alert.message != eu_alert.message
    assert us_alert.label != eu_alert.label


def test_map_alert_returns_none_when_price_is_at_or_above_map():
    assert detect_map_alert("SKU-1", Decimal("50"), Decimal("50"), market="us", markets_dir=MARKETS_DIR) is None
    assert detect_map_alert("SKU-1", Decimal("55"), Decimal("50"), market="us", markets_dir=MARKETS_DIR) is None


def test_map_alert_never_carries_a_blocking_severity():
    """Every MapAlert -- in every market -- must always be 'alert', never a
    'block'/'violation_letter' status; this module never builds a
    retailer-facing communication (Colgate doctrine)."""
    for market in ("us", "eu", "uk", "cl", "mx", "co"):
        alert = detect_map_alert("SKU-1", Decimal("10"), Decimal("50"), market=market, markets_dir=MARKETS_DIR)
        assert alert.severity == "alert"


# ---- central gate: explanation + citations, or the changeset does not ship ----


class _FakeKB:
    """Duck-typed stand-in for scm_agent.knowledge.KnowledgeBase (mirrors
    tests/test_citation_gate.py's own fixture) -- no real graph file I/O."""

    def __init__(self, existing: set[str]):
        self._existing = existing

    def node_exists(self, node_id: str, graph: str = "books") -> bool:
        return node_id in self._existing

    def concept_distance(self, from_id: str, to_id: str, *, graph: str = "books", max_hops: int = 2):
        if from_id not in self._existing or to_id not in self._existing:
            return None
        return 0 if from_id == to_id else None


def _cite(node_id: str) -> GroundedCitation:
    return GroundedCitation(text=f"Citation for {node_id}", node_id=node_id, graph="books")


def _changeset(*, reason: str) -> Changeset:
    return Changeset(
        target="test_target",
        changes=(Change(entity_id="SKU-1", field="price", before=100.0, after=70.0),),
        risk_tier="reversible",
        idempotency_key="test-key-1",
        reason=reason,
    )


def test_gate_blocks_a_changeset_missing_an_explanation():
    kb = _FakeKB({"basic_pricing_theory"})
    result = gate_price_changeset(
        _changeset(reason=""), kb=kb, candidate_citations=[_cite("basic_pricing_theory")],
    )
    assert result.approved is False
    assert "explanation" in result.reason.lower()


def test_gate_blocks_a_changeset_with_a_blank_explanation():
    kb = _FakeKB({"basic_pricing_theory"})
    result = gate_price_changeset(
        _changeset(reason="   "), kb=kb, candidate_citations=[_cite("basic_pricing_theory")],
    )
    assert result.approved is False


def test_gate_blocks_a_changeset_with_no_surviving_citations():
    kb = _FakeKB({"basic_pricing_theory"})
    result = gate_price_changeset(
        _changeset(reason="Raising SKU-1 to close a margin gap vs landed cost."),
        kb=kb, candidate_citations=[],
    )
    assert result.approved is False
    assert "citation" in result.reason.lower()


def test_gate_approves_a_changeset_with_explanation_and_surviving_citations():
    kb = _FakeKB({"basic_pricing_theory", "price_sensitivity_measurement"})
    result = gate_price_changeset(
        _changeset(reason="Raising SKU-1 to close a margin gap vs landed cost."),
        kb=kb,
        candidate_citations=[_cite("basic_pricing_theory"), _cite("price_sensitivity_measurement")],
    )
    assert result.approved is True
    assert result.reason is None
    assert len(result.citations) == 2
