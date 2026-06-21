"""Pluggable LLM layer. Claude when a key is available; an inert rules fallback
otherwise. The deterministic core never requires a provider."""

from __future__ import annotations

import json
import os
from typing import Protocol, runtime_checkable

DEFAULT_MODEL = "claude-opus-4-8"


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
