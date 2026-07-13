"""Tests for src/seo/geo_probe.py (Linchpin 3.0 PR-25, S5 GEO visibility /
share-of-voice probing -- the final module of the plan).

No real API call anywhere in this file -- every probe is a hand-written
stub ``Callable[[str], str]``, per the module's own injected-callable
design (see its docstring). Fuzzy-match scores are cross-checked against
the actual installed ``rapidfuzz`` (verified via ``fuzz.partial_ratio``
directly -- see the comment on each hand-verified case).
"""

from __future__ import annotations

import pytest

from src.seo import geo_probe as gp

# -- detect_brand_mention: exact match ---------------------------------------


def test_detect_brand_mention_exact_substring_returns_whole_short_response_as_context() -> None:
    # Text is 70 chars; the module's context radius (120 chars each side)
    # comfortably covers the whole string, so mention_context == the
    # (already-clean, no leading/trailing whitespace) response text itself.
    text = "Acme Supply Co offers reliable industrial shelving with fast delivery."
    mentioned, context = gp.detect_brand_mention(text, ["Acme Supply Co"])
    assert mentioned is True
    assert context == text


def test_detect_brand_mention_exact_match_is_case_insensitive() -> None:
    text = "we love acme supply co for our warehouse needs."
    mentioned, context = gp.detect_brand_mention(text, ["Acme Supply Co"])
    assert mentioned is True
    assert context == text


def test_detect_brand_mention_checks_terms_in_caller_order_first_hit_wins() -> None:
    text = "Acme is a solid pick, and Acme Supply Co ships fast too."
    # "Acme" (first in the list) is a substring of the response at index 0,
    # so it wins over "Acme Supply Co" even though the latter also appears.
    mentioned, context = gp.detect_brand_mention(text, ["Acme", "Acme Supply Co"])
    assert mentioned is True
    assert context == text  # whole text captured either way (short string)


# -- detect_brand_mention: fuzzy match (no exact substring present) ---------


def test_detect_brand_mention_fuzzy_catches_close_paraphrase() -> None:
    # Hand-verified via rapidfuzz.fuzz.partial_ratio directly:
    #   partial_ratio("acmesupply is often recommended for industrial "
    #                  "shelving needs.", "acme supply") == 95.238...
    # "Acme Supply" (with a space) is NOT a literal substring of
    # "AcmeSupply" (no space) -- the exact pass must fail before the fuzzy
    # pass is even tried.
    text = (
        "For industrial shelving, consider Boltek or Framewell. "
        "AcmeSupply is often recommended for industrial shelving needs."
    )
    mentioned, context = gp.detect_brand_mention(text, ["Acme Supply"])
    assert mentioned is True
    assert context == "AcmeSupply is often recommended for industrial shelving needs."


def test_detect_brand_mention_fuzzy_score_below_threshold_is_not_a_mention() -> None:
    # Hand-verified: partial_ratio(
    #   "we recommend boltek shelving or framewell racks for warehouse "
    #   "storage.", "acme supply co") == 42.857... well under the 85.0
    # threshold, and "Acme Supply Co" is nowhere in the text as a literal
    # substring either.
    text = "We recommend Boltek shelving or Framewell racks for warehouse storage."
    mentioned, context = gp.detect_brand_mention(text, ["Acme Supply Co"])
    assert mentioned is False
    assert context is None


# -- detect_brand_mention: edge cases ----------------------------------------


def test_detect_brand_mention_empty_response_text_is_not_a_mention() -> None:
    assert gp.detect_brand_mention("", ["Acme"]) == (False, None)
    assert gp.detect_brand_mention("   ", ["Acme"]) == (False, None)


def test_detect_brand_mention_empty_brand_terms_is_not_a_mention() -> None:
    assert gp.detect_brand_mention("Acme Supply Co is great.", []) == (False, None)
    assert gp.detect_brand_mention("Acme Supply Co is great.", ["", "   "]) == (False, None)


# -- run_probe: stub callable, no network ------------------------------------


def _stub_probe(responses: dict[str, str]):
    def _probe(query: str) -> str:
        return responses[query]

    return _probe


def test_run_probe_detects_mention_with_stub_callable() -> None:
    probe = _stub_probe({"best warehouse racking": "Acme Supply Co is our top recommendation."})
    result = gp.run_probe(
        "best warehouse racking", probe, engine="claude", brand_terms=["Acme Supply Co"],
    )
    assert isinstance(result, gp.ProbeResult)
    assert result.query == "best warehouse racking"
    assert result.engine == "claude"
    assert result.response_text == "Acme Supply Co is our top recommendation."
    assert result.brand_mentioned is True
    assert result.mention_context == "Acme Supply Co is our top recommendation."
    assert result.probed_at  # non-empty ISO timestamp


def test_run_probe_reports_non_mention_with_stub_callable() -> None:
    probe = _stub_probe({"best warehouse racking": "We recommend Boltek or Framewell for this."})
    result = gp.run_probe(
        "best warehouse racking", probe, engine="claude", brand_terms=["Acme Supply Co"],
    )
    assert result.brand_mentioned is False
    assert result.mention_context is None


def test_run_probe_raises_on_non_string_response() -> None:
    probe = lambda q: 12345  # noqa: E731
    with pytest.raises(TypeError):
        gp.run_probe("query", probe, engine="claude", brand_terms=["Acme"])


def test_run_probe_raises_on_empty_query() -> None:
    with pytest.raises(ValueError):
        gp.run_probe("", _stub_probe({}), engine="claude", brand_terms=["Acme"])


# -- run_probe_set: batch, explicit per-query error handling ----------------


def test_run_probe_set_runs_every_query_and_never_drops_a_failure_silently() -> None:
    def _probe(query: str) -> str:
        if query == "boom":
            raise RuntimeError("simulated API failure")
        return f"Acme Supply Co answers: {query}"

    queries = ["q1", "boom", "q2"]
    results, errors = gp.run_probe_set(queries, _probe, engine="claude", brand_terms=["Acme Supply Co"])

    assert [r.query for r in results] == ["q1", "q2"]
    assert all(r.brand_mentioned for r in results)
    assert [e.query for e in errors] == ["boom"]
    assert errors[0].engine == "claude"
    assert "simulated API failure" in errors[0].error


# -- aggregate_share_of_voice: hand-constructed percentage ------------------


def _result(query: str, mentioned: bool, engine: str = "claude") -> gp.ProbeResult:
    return gp.ProbeResult(
        query=query,
        engine=engine,
        response_text="some response",
        brand_mentioned=mentioned,
        mention_context="some response" if mentioned else None,
        probed_at="2026-07-13T00:00:00+00:00",
    )


def test_aggregate_share_of_voice_computes_hand_verified_percentage() -> None:
    # 5 results, 2 mentioned -> 2/5 = 40.0%
    results = [
        _result("q1", True),
        _result("q2", False),
        _result("q3", True),
        _result("q4", False),
        _result("q5", False),
    ]
    summary = gp.aggregate_share_of_voice(results)
    assert summary.n_queries == 5
    assert summary.n_mentioned == 2
    assert summary.mention_rate_pct == 40.0
    assert summary.engines == ("claude",)
    assert summary.n_errors == 0
    assert "2/5" in summary.summary
    assert "40.0%" in summary.summary


def test_aggregate_share_of_voice_rounds_to_one_decimal() -> None:
    # 3 results, 1 mentioned -> 1/3 = 33.333...% -> rounds to 33.3
    results = [_result("q1", True), _result("q2", False), _result("q3", False)]
    summary = gp.aggregate_share_of_voice(results)
    assert summary.mention_rate_pct == 33.3


def test_aggregate_share_of_voice_reports_errors_without_counting_them() -> None:
    results = [_result("q1", True), _result("q2", False)]
    errors = [gp.ProbeError(query="boom", engine="claude", error="timeout", probed_at="2026-07-13T00:00:00+00:00")]
    summary = gp.aggregate_share_of_voice(results, errors)
    assert summary.n_queries == 2  # errors never counted toward n_queries
    assert summary.n_errors == 1
    assert summary.mention_rate_pct == 50.0
    assert "1 probe(s) failed" in summary.summary


def test_aggregate_share_of_voice_raises_on_empty_results() -> None:
    with pytest.raises(ValueError):
        gp.aggregate_share_of_voice([])


def test_aggregate_share_of_voice_engines_sorted_and_deduplicated() -> None:
    results = [_result("q1", True, engine="claude"), _result("q2", False, engine="claude"), _result("q3", True, engine="claude")]
    summary = gp.aggregate_share_of_voice(results)
    assert summary.engines == ("claude",)


# -- ProbeResult invariants ---------------------------------------------------


def test_probe_result_rejects_mentioned_true_without_context() -> None:
    with pytest.raises(ValueError):
        gp.ProbeResult(
            query="q", engine="claude", response_text="text",
            brand_mentioned=True, mention_context=None, probed_at="2026-07-13T00:00:00+00:00",
        )


def test_probe_result_rejects_mentioned_false_with_context() -> None:
    with pytest.raises(ValueError):
        gp.ProbeResult(
            query="q", engine="claude", response_text="text",
            brand_mentioned=False, mention_context="text", probed_at="2026-07-13T00:00:00+00:00",
        )


def test_probe_result_rejects_empty_query() -> None:
    with pytest.raises(ValueError):
        gp.ProbeResult(
            query="", engine="claude", response_text="text",
            brand_mentioned=False, mention_context=None, probed_at="2026-07-13T00:00:00+00:00",
        )


# -- build_claude_probe: no real API call, injected stub model --------------


class _StubModel:
    def __init__(self, available: bool, response: str = "") -> None:
        self._available = available
        self._response = response

    def available(self) -> bool:
        return self._available

    def complete(self, prompt: str) -> str:
        return self._response


def test_build_claude_probe_raises_when_model_unavailable() -> None:
    with pytest.raises(RuntimeError):
        gp.build_claude_probe(_StubModel(available=False))


def test_build_claude_probe_wraps_complete_when_available() -> None:
    probe = gp.build_claude_probe(_StubModel(available=True, response="Acme Supply Co is great."))
    assert probe("any query") == "Acme Supply Co is great."


def test_build_claude_probe_result_integrates_with_run_probe() -> None:
    probe = gp.build_claude_probe(_StubModel(available=True, response="Acme Supply Co ships fast."))
    result = gp.run_probe("best supplier", probe, engine=gp.CLAUDE_ENGINE_LABEL, brand_terms=["Acme Supply Co"])
    assert result.brand_mentioned is True
    assert result.engine == "claude"


# -- the single-sanctioned-engine caveat must not silently disappear --------


def test_single_sanctioned_engine_caveat_constant_names_claude_and_forbids_scraping() -> None:
    caveat = gp.SINGLE_SANCTIONED_ENGINE_CAVEAT
    assert "Claude" in caveat
    assert "official" in caveat.lower()
    assert "scrape" in caveat.lower()
    assert "ToS" in caveat or "terms of service" in caveat.lower()


def test_module_docstring_states_the_single_engine_limitation() -> None:
    doc = gp.__doc__ or ""
    assert "Claude" in doc
    assert "official" in doc.lower()
    assert "scrape" in doc.lower() or "scraper" in doc.lower()
