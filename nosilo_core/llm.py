"""Pluggable LLM layer. Claude when a key is available; an inert rules fallback
otherwise. The deterministic core never requires a provider.

Typed decisions (pick one of N options, with probabilities) have their own seat,
``DecisionProvider``, backed by Jev when ``TYPESAFE_API_KEY`` is set."""

from __future__ import annotations

import json
import logging
import math
import os
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
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


# ---- typed decisions (Jev) --------------------------------------------------
#
# A second seat next to ``LLMProvider``, for the calls that are a DECISION and not
# text: pick one of N named options, with a probability per option. Jev (TypeSafe
# AI, launched 2026-09-15) is a model built for exactly that and returns no prose,
# so it cannot back ``complete`` / ``extract`` and is not shoehorned into them.
#
# Same rules as the provider above: optional, off without a key, and never on the
# deterministic path. Setting ``TYPESAFE_API_KEY`` is the opt-in, and it sends the
# text being decided on to a third party, exactly like ``ANTHROPIC_API_KEY`` does.

JEV_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
JEV_DEFAULT_MODEL = "jev-latest"
JEV_TIMEOUT_S = 5.0
_JEV_QUESTION = "decision"

#: ``(url, headers, body, timeout_s) -> response bytes``. Injected in tests.
Transport = Callable[[str, Mapping[str, str], bytes, float], bytes]


@dataclass(frozen=True)
class Decision:
    """One typed choice: the option picked, how sure, and the full distribution."""

    choice: str
    confidence: float
    probabilities: Mapping[str, float]

    def ranked(self) -> list[str]:
        """Option ids by probability, highest first."""
        return sorted(self.probabilities, key=lambda k: (-self.probabilities[k], k))


@runtime_checkable
class DecisionProvider(Protocol):
    def available(self) -> bool: ...
    def choose(self, state: str, instructions: str, options: Mapping[str, str]) -> Decision: ...


def _urllib_transport(url: str, headers: Mapping[str, str], body: bytes, timeout_s: float) -> bytes:
    request = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
    with urllib.request.urlopen(request, timeout=timeout_s) as response:  # noqa: S310 (fixed https URL)
        return response.read()


def _unit_interval(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"Jev {name}: not a finite number: {value!r}")
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"Jev {name}: outside [0, 1]: {value!r}")
    return float(value)


def parse_jev_choice(payload: Mapping, options: Mapping[str, str]) -> Decision:
    """Validate a ``/v1/systemone`` response for one choice question.

    Strict on purpose: a choice that is not one of the options, or a probability
    that is not a number, raises instead of being coerced. The caller treats a
    raise like a missing provider and falls back to the deterministic path.
    """
    try:
        answer = payload["answers"][_JEV_QUESTION]
    except (KeyError, TypeError):
        raise ValueError("Jev response has no answer for the question asked") from None
    choice = answer.get("choice")
    if choice not in options:
        raise ValueError(f"Jev chose {choice!r}, which is not one of the options offered")
    probabilities = {
        str(k): _unit_interval(f"probability[{k}]", v) for k, v in (answer.get("probabilities") or {}).items()
    }
    unknown = set(probabilities) - set(options)
    if unknown:
        raise ValueError(f"Jev returned probabilities for options never offered: {sorted(unknown)}")
    return Decision(
        choice=choice,
        confidence=_unit_interval("confidence", answer.get("confidence")),
        probabilities=probabilities,
    )


class JevDecider:
    """``DecisionProvider`` over TypeSafe's HTTP API. stdlib only, like the rest of the core."""

    def __init__(
        self,
        api_key: str,
        model: str = JEV_DEFAULT_MODEL,
        *,
        endpoint: str = JEV_ENDPOINT,
        timeout_s: float = JEV_TIMEOUT_S,
        transport: Transport | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._endpoint = endpoint
        self._timeout_s = timeout_s
        self._transport = transport or _urllib_transport

    def available(self) -> bool:
        return True

    def choose(self, state: str, instructions: str, options: Mapping[str, str]) -> Decision:
        if len(options) < 2:
            raise ValueError("a decision needs at least two options")
        body = {
            "state": state,
            "model": self._model,
            "questions": {
                _JEV_QUESTION: {"type": "choice", "instructions": instructions, "criteria": dict(options)},
            },
        }
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        raw = self._transport(self._endpoint, headers, json.dumps(body).encode("utf-8"), self._timeout_s)
        return parse_jev_choice(json.loads(raw), options)


def get_decider(api_key: str | None = None, model: str | None = None) -> DecisionProvider | None:
    """Factory: ``JevDecider`` when ``TYPESAFE_API_KEY`` is set, else ``None``."""
    key = api_key or os.environ.get("TYPESAFE_API_KEY")
    if not key:
        return None
    return JevDecider(key, model=model or os.environ.get("TYPESAFE_MODEL") or JEV_DEFAULT_MODEL)


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
