"""Capability registry — tools self-describe and the orchestrator drives their
four stages (prepare -> run -> qa -> deliver). Adding a capability = registering
a Tool; no routing edits."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

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
        """Rank tools by keyword-hit count against the lowercased brief."""
        text = brief.lower()
        scored = [
            (tool, float(sum(1 for kw in tool.intent_keywords if kw.lower() in text)))
            for tool in self._tools.values()
        ]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored
