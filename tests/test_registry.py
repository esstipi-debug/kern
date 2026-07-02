"""Tests for the capability registry's keyword routing (scm_agent/registry.py).

Regression coverage for the word-boundary matching fix: `match()` used to score a
keyword hit on raw substring containment, so a short keyword like "upc" or "ean"
(both real, intentional data_quality/GTIN keywords) matched inside unrelated words
("upcoming", "ocean") and misrouted briefs that never mentioned the keyword at all.
"""

from __future__ import annotations

from scm_agent.registry import Tool, ToolRegistry
from scm_agent.tools import build_default_registry


def _score_for(ranked: list[tuple[Tool, float]], key: str) -> float:
    return next(score for tool, score in ranked if tool.key == key)


def test_short_keyword_does_not_match_inside_an_unrelated_word_upcoming():
    reg = build_default_registry()
    ranked = reg.match("please schedule the upcoming purchase orders for next week")
    assert _score_for(ranked, "data_quality") == 0.0  # "upc" must not fire on "upcoming"


def test_short_keyword_does_not_match_inside_an_unrelated_word_ocean():
    reg = build_default_registry()
    ranked = reg.match("review our ocean freight costs for the quarter")
    assert _score_for(ranked, "data_quality") == 0.0  # "ean" must not fire on "ocean"


def test_short_keyword_still_matches_as_a_real_standalone_word():
    reg = build_default_registry()
    ranked = reg.match("validate the upc and ean barcodes on our sku master")
    assert _score_for(ranked, "data_quality") >= 2.0  # both "upc" and "ean" as real words


def test_multi_word_phrase_keyword_still_matches():
    reg = build_default_registry()
    ranked = reg.match("what is our optimal price and margin for this sku")
    assert _score_for(ranked, "pricing") >= 1.0


def _tool(key: str, keywords: tuple[str, ...]) -> Tool:
    return Tool(
        key=key, title=key, description="", intent_keywords=keywords, requires_data=False,
        prepare=lambda req, provider: None, run=lambda payload, params: None, qa=lambda report: [],
        deliver=lambda report, out_dir, client: {},
    )


def test_match_is_case_insensitive_and_word_bounded():
    reg = ToolRegistry()
    reg.register(_tool("t1", ("EOQ", "reorder point")))

    ranked = reg.match("What REORDER POINT should we use, given the EOQ?")

    assert _score_for(ranked, "t1") == 2.0


def test_match_does_not_score_a_keyword_embedded_in_a_longer_word():
    reg = ToolRegistry()
    reg.register(_tool("t1", ("cost",)))

    ranked = reg.match("the accosted supplier missed the deadline")  # "cost" inside "accosted"

    assert _score_for(ranked, "t1") == 0.0


def test_match_tolerates_a_simple_trailing_plural():
    """Regression: strict \\b...\\b broke real routing - "financial kpi" no longer
    matched a brief saying "financial KPIs", and "waiting line" no longer matched
    "waiting lines" - both real tests in the suite before this was relaxed."""
    reg = ToolRegistry()
    reg.register(_tool("t1", ("financial kpi", "waiting line", "duplicate box")))

    ranked = reg.match("report the financial KPIs after clearing the waiting lines and duplicate boxes")

    assert _score_for(ranked, "t1") == 3.0


def test_match_plural_tolerance_does_not_reopen_the_substring_bug():
    """The plural suffix must not let a short keyword match inside an unrelated
    word merely because that word happens to end in a vowel + "s"-like pattern."""
    reg = ToolRegistry()
    reg.register(_tool("t1", ("upc", "ean")))

    ranked = reg.match("please schedule the upcoming shipments across the ocean")

    assert _score_for(ranked, "t1") == 0.0
