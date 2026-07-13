"""Tests for src/pricing_intel/match/adjudicate.py (Linchpin 3.0 PR-14,
plan S6.5 step 4).
"""

from __future__ import annotations

import pytest

from src.pricing_intel.match.adjudicate import (
    ADJUDICATION_BAND,
    AdjudicationRequest,
    LlmAdjudicationResponse,
    adjudicate_pair,
    is_in_adjudication_band,
)
from src.pricing_intel.match.fuzzy import ProductAttributes


def _request(score: float = 0.7) -> AdjudicationRequest:
    our = ProductAttributes("our-1", "Samsung Galaxy S23 Smartphone", "Samsung")
    comp = ProductAttributes("comp-1", "Samsung Galaxy S23 Ultra Smartphone", "Samsung")
    return AdjudicationRequest(our=our, competitor=comp, probabilistic_score=score)


# -- is_in_adjudication_band -----------------------------------------------------


def test_is_in_adjudication_band_matches_plan_literal_range() -> None:
    assert ADJUDICATION_BAND == (0.5, 0.85)
    assert is_in_adjudication_band(0.5) is True
    assert is_in_adjudication_band(0.84999) is True
    assert is_in_adjudication_band(0.85) is False  # upper bound exclusive
    assert is_in_adjudication_band(0.49999) is False
    assert is_in_adjudication_band(0.9484375) is False  # worked "ambiguous" example, above the band


def test_is_in_adjudication_band_rejects_out_of_range_score() -> None:
    with pytest.raises(ValueError):
        is_in_adjudication_band(1.1)


# -- AdjudicationRequest / LlmAdjudicationResponse validation --------------------


def test_adjudication_request_rejects_out_of_range_score() -> None:
    our = ProductAttributes("our-1", "x", "y")
    comp = ProductAttributes("comp-1", "x", "y")
    with pytest.raises(ValueError):
        AdjudicationRequest(our=our, competitor=comp, probabilistic_score=1.5)


def test_llm_adjudication_response_rejects_unknown_verdict() -> None:
    with pytest.raises(ValueError):
        LlmAdjudicationResponse(verdict="confirmed", reason="looks the same to me", confidence=0.8)


def test_llm_adjudication_response_rejects_empty_reason() -> None:
    with pytest.raises(ValueError):
        LlmAdjudicationResponse(verdict="same", reason="   ", confidence=0.8)


def test_llm_adjudication_response_rejects_out_of_range_confidence() -> None:
    with pytest.raises(ValueError):
        LlmAdjudicationResponse(verdict="same", reason="matches on model number", confidence=1.2)


# -- adjudicate_pair: no LLM wired (this PR's shipped default) ------------------


def test_adjudicate_pair_defers_when_no_llm_configured() -> None:
    result = adjudicate_pair(_request())
    assert result.verdict == "deferred"
    assert result.confidence == 0.0
    assert result.reason == "no_llm_provider_configured"
    assert result.extractor == "llm"
    assert result.extractor_version == "unwired"


def test_adjudicate_pair_never_raises_without_a_provider() -> None:
    # Regression guard: a missing LLM provider must degrade cleanly, never
    # raise (golden rule 10's "never silent" is about fabricating a verdict,
    # not about crashing the caller).
    for score in (0.5, 0.6, 0.7, 0.8, 0.84):
        result = adjudicate_pair(_request(score))
        assert result.verdict == "deferred"


# -- adjudicate_pair: LLM wired (injected callable) ------------------------------


def test_adjudicate_pair_uses_injected_llm_response() -> None:
    def fake_llm(request: AdjudicationRequest) -> LlmAdjudicationResponse:
        assert request.our.product_id == "our-1"
        return LlmAdjudicationResponse(verdict="same", reason="identical spec, reworded title", confidence=0.82)

    result = adjudicate_pair(_request(), llm=fake_llm, extractor_version="claude-test-1")
    assert result.verdict == "same"
    assert result.reason == "identical spec, reworded title"
    assert result.confidence == 0.82
    assert result.extractor == "llm"
    assert result.extractor_version == "claude-test-1"


def test_adjudicate_pair_propagates_a_different_verdict() -> None:
    def fake_llm(request: AdjudicationRequest) -> LlmAdjudicationResponse:
        return LlmAdjudicationResponse(verdict="different", reason="different tier, Ultra has more RAM", confidence=0.9)

    result = adjudicate_pair(_request(), llm=fake_llm)
    assert result.verdict == "different"


def test_adjudicate_pair_rejects_a_malformed_llm_return_value() -> None:
    def broken_llm(request: AdjudicationRequest) -> str:
        return "same"  # not an LlmAdjudicationResponse

    with pytest.raises(TypeError):
        adjudicate_pair(_request(), llm=broken_llm)


def test_adjudicate_pair_does_not_catch_provider_exceptions() -> None:
    def failing_llm(request: AdjudicationRequest) -> LlmAdjudicationResponse:
        raise RuntimeError("provider timeout")

    with pytest.raises(RuntimeError):
        adjudicate_pair(_request(), llm=failing_llm)


def test_adjudication_result_never_confirms_a_match_by_itself() -> None:
    # Structural guarantee (module docstring): AdjudicationResult's verdict
    # vocabulary has no "confirmed" value at all -- confirming a match is a
    # sku_map.SkuMap.record() call a caller makes deliberately, never
    # something this module can produce.
    def fake_llm(request: AdjudicationRequest) -> LlmAdjudicationResponse:
        return LlmAdjudicationResponse(verdict="same", reason="matches", confidence=0.99)

    result = adjudicate_pair(_request(), llm=fake_llm)
    assert result.verdict in ("same", "different", "variant", "deferred")
    assert result.verdict != "confirmed"
