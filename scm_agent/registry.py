"""Capability registry — tools self-describe and the orchestrator drives their
four stages (prepare -> run -> qa -> deliver). Adding a capability = registering
a Tool; no routing edits."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from src.guided import GuidedOutcome

from .llm import LLMProvider
from .types import JobRequest


@dataclass(frozen=True)
class Prepared:
    """Output of Tool.prepare. status 'ok' lets the orchestrator proceed to run;
    'needs_data'/'needs_clarification' short-circuit with `messages`."""

    status: str
    payload: object = None
    messages: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Produced:
    """Output of Tool.run: the report object + a human summary line."""

    report: object
    summary: str


@dataclass(frozen=True)
class Tool:
    key: str
    title: str
    description: str
    intent_keywords: tuple[str, ...]
    requires_data: bool
    prepare: Callable[[JobRequest, LLMProvider], Prepared]
    run: Callable[[object, dict], Produced]
    qa: Callable[[object], list[str]]
    deliver: Callable[[object, Path, str], dict[str, Path]]
    # Optional premium deck (the "artifacts that sell" Deliverable), emitted alongside
    # the operational files. Receives the L3 citations + confidence + the ranked options
    # the orchestrator computed (rendered as the deck's action menu). None => operational only.
    deck: Callable[[object, Path, str, list, float, list], dict[str, Path]] | None = None
    # Optional ranked, executable options on SUCCESS (the Guided Execution Layer's OPTIONS
    # outcome). When set, the orchestrator surfaces this GuidedOutcome as JobResult.guided
    # instead of the generic "executed" — so the tool offers >=2 ranked choices to act with
    # a recommended default. None => the result is reported as executed (deliverables only).
    options: Callable[[object], GuidedOutcome] | None = None
    # Client-profile param keys (see src/client_profile.py) this tool cares enough about to
    # ask for once, rather than silently take the engine's generic default — only enforced
    # when the caller opts in via JobRequest.strict_params. Empty => never blocks (default).
    required_client_params: tuple[str, ...] = ()


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.key in self._tools:
            raise ValueError(f"tool already registered: {tool.key}")
        self._tools[tool.key] = tool

    def get(self, key: str) -> Tool:
        return self._tools[key]

    def list(self) -> list[Tool]:
        return list(self._tools.values())

    def match(self, brief: str) -> list[tuple[Tool, float]]:
        """Rank tools by keyword-hit count against the lowercased brief.

        Matches on whole-word/phrase boundaries, not raw substring containment:
        a plain ``in`` check let "upc" match inside "upcoming" and "ean" match
        inside "ocean", misrouting briefs that never mentioned the keyword's
        actual word at all.
        """
        text = brief.lower()
        scored = [
            (tool, float(sum(1 for kw in tool.intent_keywords if _keyword_matches(kw, text))))
            for tool in self._tools.values()
        ]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored


_PLURAL_SUFFIX = r"(?:e?s)?"  # tolerate a trailing simple English plural ("kpi"/"kpis")


def _keyword_matches(keyword: str, lowered_text: str) -> bool:
    """Whether ``keyword`` appears in ``lowered_text`` as a whole word/phrase.

    Word-bounded on both sides (so "upc" cannot match inside "upcoming" and "ean"
    cannot match inside "ocean"), but tolerant of a trailing simple plural, since
    keywords are written in a base form ("waiting line", "financial kpi") while a
    brief just as naturally pluralizes ("waiting lines", "financial KPIs") - a raw
    ``\\b...\\b`` would silently stop matching those without every keyword needing
    a hand-duplicated plural entry.
    """
    pattern = r"\b" + re.escape(keyword.lower()) + _PLURAL_SUFFIX + r"\b"
    return re.search(pattern, lowered_text) is not None
