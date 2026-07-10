"""Tests for scm_agent/llm.py's narrative_rewrite(): the shared LLM-polish
helper behind both the Orchestrator's single-tool path and the commercial
package runner's per-step narrative (see scm_agent/packages.py::_run_step)."""
from __future__ import annotations

from scm_agent.llm import narrative_rewrite


class _RecordingProvider:
    def __init__(self, available: bool = True, response: str = "polished"):
        self._available = available
        self._response = response
        self.prompts: list[str] = []

    def available(self) -> bool:
        return self._available

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self._response

    def extract(self, prompt: str, schema: dict) -> dict:
        return {}


class _RaisingProvider:
    def available(self) -> bool:
        return True

    def complete(self, prompt: str) -> str:
        raise RuntimeError("boom")

    def extract(self, prompt: str, schema: dict) -> dict:
        return {}


def test_unavailable_provider_returns_base_summary_unchanged():
    provider = _RecordingProvider(available=False)
    result = narrative_rewrite(provider, "base text", "Some Tool", lang="en")
    assert result == "base text"
    assert provider.prompts == []


def test_lang_none_omits_the_language_clause_entirely():
    # The safe default: reproduces this function's exact pre-E4 wording, for
    # callers (Orchestrator's existing production call sites) that never
    # asked for a language and must not have their output silently translated.
    provider = _RecordingProvider()
    narrative_rewrite(provider, "base", "Some Tool", lang=None)
    instruction = provider.prompts[0].split("\n\n")[0]
    assert "Spanish" not in instruction and "English" not in instruction
    assert instruction == (
        "Rewrite this Some Tool result summary in one clear, client-ready "
        "sentence. Keep every number. Return only the sentence."
    )


def test_lang_es_asks_for_spanish():
    provider = _RecordingProvider()
    narrative_rewrite(provider, "base", "Some Tool", lang="es")
    assert "Spanish" in provider.prompts[0]


def test_lang_en_asks_for_english():
    provider = _RecordingProvider()
    narrative_rewrite(provider, "base", "Some Tool", lang="en")
    assert "English" in provider.prompts[0]


def test_unrecognized_lang_falls_back_to_spanish_instruction():
    provider = _RecordingProvider()
    narrative_rewrite(provider, "base", "Some Tool", lang="fr")
    assert "Spanish" in provider.prompts[0]


def test_persona_is_included_when_set():
    provider = _RecordingProvider()
    narrative_rewrite(provider, "base", "Some Tool", lang="es", persona="a consultant")
    assert "You are a consultant" in provider.prompts[0]


def test_persona_with_no_lang_matches_the_exact_pre_e4_wording():
    provider = _RecordingProvider()
    narrative_rewrite(provider, "base", "Some Tool", lang=None, persona="a consultant")
    instruction = provider.prompts[0].split("\n\n")[0]
    assert instruction == (
        "You are a consultant. Rewrite this Some Tool result summary in one clear, "
        "client-ready sentence in your voice. Keep every number. Return only the sentence."
    )


def test_persona_with_lang_joins_both_clauses_with_a_comma():
    provider = _RecordingProvider()
    narrative_rewrite(provider, "base", "Some Tool", lang="en", persona="a consultant")
    instruction = provider.prompts[0].split("\n\n")[0]
    assert instruction == (
        "You are a consultant. Rewrite this Some Tool result summary in one clear, "
        "client-ready sentence in English, in your voice. Keep every number. "
        "Return only the sentence."
    )


def test_no_persona_omits_the_you_are_prefix():
    provider = _RecordingProvider()
    narrative_rewrite(provider, "base", "Some Tool", lang="es")
    assert "You are" not in provider.prompts[0]


def test_citations_are_grounded_when_present():
    provider = _RecordingProvider()
    narrative_rewrite(provider, "base", "Some Tool", lang="es", citations=["Source A", "Source B"])
    assert "Source A" in provider.prompts[0]
    assert "Source B" in provider.prompts[0]


def test_no_citations_omits_the_grounding_clause():
    provider = _RecordingProvider()
    narrative_rewrite(provider, "base", "Some Tool", lang="es")
    assert "Ground it" not in provider.prompts[0]


def test_provider_exception_falls_back_to_base_summary():
    result = narrative_rewrite(_RaisingProvider(), "base text", "Some Tool", lang="en")
    assert result == "base text"


def test_empty_llm_response_falls_back_to_base_summary():
    provider = _RecordingProvider(response="   ")
    result = narrative_rewrite(provider, "base text", "Some Tool", lang="en")
    assert result == "base text"


def test_successful_rewrite_returns_the_stripped_llm_output():
    provider = _RecordingProvider(response="  Polished sentence.  ")
    result = narrative_rewrite(provider, "base text", "Some Tool", lang="en")
    assert result == "Polished sentence."
