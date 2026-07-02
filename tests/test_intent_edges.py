"""Edge cases for intent classification (scm_agent/intent.py).

Complements the happy-path routing tests in test_scm_agent.py with the
*defensive* branches: unknown / empty overrides, degenerate briefs, and a rogue
LLM guess that isn't a real capability. These lock in the "ask, don't crash"
contract the orchestrator relies on.
"""

from __future__ import annotations

from scm_agent import intent, tools


class _Provider:
    """Minimal LLMProvider stub: toggle availability and the extracted job_type."""

    def __init__(self, *, available: bool = False, job_type: str | None = None):
        self._available = available
        self._job_type = job_type

    def available(self) -> bool:
        return self._available

    def extract(self, prompt, schema):
        return {"job_type": self._job_type} if self._job_type is not None else {}

    def complete(self, prompt):  # unused by classify(); present for the Protocol
        return ""


def _keys() -> set[str]:
    return {t.key for t in tools.build_default_registry().list()}


def test_unknown_override_asks_for_clarification_without_crashing():
    # An override that isn't a registered key must not blow up registry.get later.
    reg = tools.build_default_registry()
    res = intent.classify("anything", reg, _Provider(available=False), job_type_override="does_not_exist")
    assert res.job_type is None
    assert res.confidence == 0.0
    assert set(res.candidates) == _keys()  # offers the real options


def test_empty_override_string_is_ignored_and_rules_run():
    # "" is falsy, so the override is skipped and rule matching still routes.
    reg = tools.build_default_registry()
    res = intent.classify("what price maximizes profit", reg, _Provider(available=False), job_type_override="")
    assert res.job_type == "pricing"


def test_empty_brief_returns_candidates_not_a_guess():
    reg = tools.build_default_registry()
    res = intent.classify("", reg, _Provider(available=False))
    assert res.job_type is None
    assert res.candidates


def test_whitespace_only_brief_is_treated_as_empty():
    reg = tools.build_default_registry()
    res = intent.classify("   \t  ", reg, _Provider(available=False))
    assert res.job_type is None
    assert res.candidates


def test_rogue_llm_guess_outside_registry_is_rejected():
    # When rules are ambiguous and the LLM returns a key that doesn't exist,
    # the classifier must drop it and fall back to clarification candidates.
    reg = tools.build_default_registry()
    prov = _Provider(available=True, job_type="totally_made_up_tool")
    res = intent.classify("help me with my supply chain", reg, prov)
    assert res.job_type is None
    assert res.candidates


class _FlakyProvider:
    """An LLMProvider that IS available but whose call raises (a network blip,
    an expired key mid-session, a rate limit, ...)."""

    def available(self) -> bool:
        return True

    def extract(self, prompt, schema):
        raise ConnectionError("upstream LLM call failed")

    def complete(self, prompt):
        raise ConnectionError("upstream LLM call failed")


def test_llm_failure_degrades_to_clarification_not_a_crash():
    """Regression: an LLM call that raises used to propagate all the way out of
    classify(), through the orchestrator's generic handler, surfacing as an opaque
    'An internal error occurred.' with zero candidates - worse than having no LLM
    key at all. It must degrade to the same candidate-based clarification."""
    reg = tools.build_default_registry()
    res = intent.classify("help me with my supply chain", reg, _FlakyProvider())
    assert res.job_type is None
    assert res.candidates  # still offers real options, exactly like the no-key path
