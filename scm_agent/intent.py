"""Intent classification — rules first, LLM only when rules are ambiguous."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .llm import LLMProvider
from .registry import ToolRegistry

logger = logging.getLogger(__name__)

_INTENT_SCHEMA = {
    "type": "object",
    "properties": {"job_type": {"type": "string"}},
    "required": ["job_type"],
}


@dataclass(frozen=True)
class IntentResult:
    job_type: str | None
    confidence: float
    params: dict = field(default_factory=dict)
    candidates: list[str] = field(default_factory=list)


def _llm_classify(provider: LLMProvider, brief: str, registry: ToolRegistry) -> str | None:
    keys = [t.key for t in registry.list()]
    catalog = "\n".join(f"- {t.key}: {t.description}" for t in registry.list())
    prompt = (
        "Pick the single best capability for this request. Respond with the exact key only.\n\n"
        f"Capabilities:\n{catalog}\n\nRequest:\n{brief}"
    )
    obj = provider.extract(prompt, _INTENT_SCHEMA)
    guess = str(obj.get("job_type", "")).strip()
    return guess if guess in keys else None


def classify(
    brief: str,
    registry: ToolRegistry,
    provider: LLMProvider,
    *,
    job_type_override: str | None = None,
) -> IntentResult:
    if job_type_override:
        keys = [t.key for t in registry.list()]
        if job_type_override in keys:
            return IntentResult(job_type=job_type_override, confidence=1.0)
        # Unknown override -> ask for clarification instead of crashing on registry.get
        return IntentResult(job_type=None, confidence=0.0, candidates=keys)

    ranked = registry.match(brief)
    top_tool, top_score = ranked[0] if ranked else (None, 0.0)
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0
    total = sum(score for _, score in ranked)

    if top_tool is not None and top_score >= 1 and top_score > second_score:
        confidence = top_score / total if total else 0.0
        return IntentResult(job_type=top_tool.key, confidence=confidence)

    if provider.available():
        try:
            guess = _llm_classify(provider, brief, registry)
        except Exception:
            # A flaky LLM call must degrade to the same candidate-based
            # clarification path as "no key at all" - never worse. Left
            # uncaught, this exception used to escape all the way to the
            # orchestrator's generic handler and surface as an opaque
            # "An internal error occurred." with no candidates at all.
            logger.warning("LLM intent classification failed; falling back to keyword ranking", exc_info=True)
            guess = None
        if guess:
            return IntentResult(job_type=guess, confidence=0.6)

    candidates = [t.key for t, score in ranked if score > 0][:3] or [t.key for t in registry.list()]
    confidence = top_score / total if total else 0.0
    return IntentResult(job_type=None, confidence=confidence, candidates=candidates)
