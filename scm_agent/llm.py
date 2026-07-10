"""Pluggable LLM layer. Claude when a key is available; an inert rules fallback
otherwise. The deterministic core never requires a provider."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

DEFAULT_MODEL = "claude-opus-4-8"

logger = logging.getLogger(__name__)

_LANG_NAMES = {"es": "Spanish", "en": "English"}


@runtime_checkable
class LLMProvider(Protocol):
    def available(self) -> bool: ...
    def complete(self, prompt: str) -> str: ...
    def extract(self, prompt: str, schema: dict) -> dict: ...


def parse_json_object(text: str) -> dict:
    """Return the first balanced top-level JSON object in `text`, or {}.

    Tolerant of code fences and surrounding prose — scans for the first '{'
    and matches braces (ignoring those inside strings)."""
    start = text.find("{")
    if start == -1:
        return {}
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return {}
                return obj if isinstance(obj, dict) else {}
    return {}


class RulesFallback:
    """Always-available no-op provider. `available()` is False so callers take
    the deterministic path."""

    def available(self) -> bool:
        return False

    def complete(self, prompt: str) -> str:
        return ""

    def extract(self, prompt: str, schema: dict) -> dict:
        return {}


class ClaudeProvider:
    """Anthropic-backed provider. Imports the SDK lazily on first network use."""

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL) -> None:
        self._api_key = api_key
        self._model = model
        self._client = None

    def available(self) -> bool:
        return True

    def _ensure_client(self):
        if self._client is None:
            import anthropic  # lazy: optional dependency

            self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    def complete(self, prompt: str) -> str:
        client = self._ensure_client()
        msg = client.messages.create(
            model=self._model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(
            block.text for block in msg.content if getattr(block, "type", None) == "text"
        )

    def extract(self, prompt: str, schema: dict) -> dict:
        instruction = (
            f"{prompt}\n\nRespond with ONLY a single JSON object matching this schema "
            f"(no prose, no code fence):\n{json.dumps(schema)}"
        )
        return parse_json_object(self.complete(instruction))


def get_provider(api_key: str | None = None, model: str | None = None) -> LLMProvider:
    """Factory: ClaudeProvider when a key + SDK are present, else RulesFallback."""
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return RulesFallback()
    try:
        import anthropic  # noqa: F401  (probe only)
    except ImportError:
        return RulesFallback()
    return ClaudeProvider(key, model=model or DEFAULT_MODEL)


def narrative_rewrite(
    provider: LLMProvider,
    base_summary: str,
    tool_title: str,
    *,
    lang: str | None = None,
    citations: Sequence[str] | None = None,
    persona: str = "",
) -> str:
    """Optional LLM polish of a tool's result summary, in the target language.

    Shared by the Orchestrator's single-tool path and the commercial-package
    runner (``scm_agent/packages.py``) - the only place ``lang`` reaches an
    LLM-generated narrative (see ``src/i18n.py`` for the static-label path
    used everywhere a provider is not configured). Always optional decoration
    over the engine's own deterministic summary: falls back to
    ``base_summary`` unchanged when no provider is available or the call
    fails, never load-bearing.

    ``lang=None`` (the default) omits the language clause entirely, producing
    the exact prompt wording this function had before it grew a ``lang``
    parameter -- required so the Orchestrator's existing production callers
    (``webapp/app.py``, ``webapp/mcp_server.py``, ``examples/run_agent.py``,
    none of which pass a language today) keep their current output language
    unchanged. Only callers that KNOW they want a specific language pass one
    explicitly -- the commercial-package runner does, via ``PackageSpec.lang``.
    """
    if not provider.available():
        return base_summary
    lang_clause = f" in {_LANG_NAMES.get(lang, _LANG_NAMES['es'])}" if lang else ""
    ground = ""
    if citations:
        ground = "\nGround it in these sources where relevant: " + "; ".join(citations)
    if persona:
        voice_clause = (f"{lang_clause}, in your voice" if lang_clause else " in your voice")
        instruction = (
            f"You are {persona}. Rewrite this {tool_title} result summary in one clear, "
            f"client-ready sentence{voice_clause}. Keep every number. "
            "Return only the sentence."
        )
    else:
        instruction = (
            f"Rewrite this {tool_title} result summary in one clear, client-ready sentence"
            f"{lang_clause}. Keep every number. Return only the sentence."
        )
    try:
        text = provider.complete(f"{instruction}\n\n{base_summary}{ground}")
    except Exception:
        logger.debug("narrative upgrade failed", exc_info=True)
        return base_summary
    return text.strip() or base_summary
