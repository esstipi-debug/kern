"""The orchestrator: brief + optional data -> routed, QA-gated deliverable."""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path

from .guided_bridge import to_guided_outcome
from .intent import classify
from .knowledge import KnowledgeBase
from .llm import LLMProvider, get_provider
from .registry import Tool, ToolRegistry
from .tools import build_default_registry
from .types import (
    STATUS_ERROR,
    STATUS_NEEDS_CLARIFICATION,
    STATUS_NEEDS_DATA,
    STATUS_OK,
    STATUS_QA_FAILED,
    JobRequest,
    JobResult,
)

logger = logging.getLogger(__name__)


class Orchestrator:
    def __init__(
        self,
        registry: ToolRegistry | None = None,
        provider: LLMProvider | None = None,
        knowledge: KnowledgeBase | None = None,
        persona: str = "",
    ) -> None:
        self.registry = registry if registry is not None else build_default_registry()
        self.provider = provider if provider is not None else get_provider()
        # L3 domain knowledge. Loads the books graph (committed) + code graph
        # (gitignored). Absent graphs degrade gracefully to no citations.
        self.knowledge = knowledge if knowledge is not None else KnowledgeBase()
        # The operating mode's voice for client-facing narration. Empty => the
        # narrative prompt is unchanged (the deterministic output never depends on it).
        self.persona = persona

    def run(
        self,
        brief: str,
        *,
        data_path: str | None = None,
        overrides: dict | None = None,
        job_type: str | None = None,
        client: str = "Client",
        out_dir: str | Path = "deliverables/agent",
    ) -> JobResult:
        overrides = overrides or {}
        request = JobRequest(brief=brief, data_path=data_path, job_type=job_type,
                             params=dict(overrides), client=client)
        try:
            result = self._run(request, Path(out_dir))
        except Exception:  # never crash the caller — surface as error status
            logger.error("orchestrator.run failed", exc_info=True)
            result = JobResult(status=STATUS_ERROR, tool=None, confidence=0.0,
                               deliverables={}, summary="An internal error occurred.")
        # Single boundary: every result leaves with a protected, executable path.
        return replace(result, guided=to_guided_outcome(result))

    def _run(self, request: JobRequest, out_dir: Path) -> JobResult:
        intent = classify(request.brief, self.registry, self.provider, job_type_override=request.job_type)
        if intent.job_type is None:
            return JobResult(
                status=STATUS_NEEDS_CLARIFICATION, tool=None, confidence=intent.confidence,
                deliverables={}, summary="Ambiguous request — pick a capability.",
                clarifications=intent.candidates,
            )

        tool = self.registry.get(intent.job_type)
        params = {**intent.params, **request.params}

        if tool.requires_data and not request.data_path:
            return JobResult(
                status=STATUS_NEEDS_DATA, tool=tool.key, confidence=intent.confidence,
                deliverables={}, summary=f"{tool.title} needs a data file.",
                clarifications=[f"provide a data file for {tool.title}"],
            )

        prepared = tool.prepare(request, self.provider)
        if prepared.status != STATUS_OK:
            return JobResult(
                status=prepared.status, tool=tool.key, confidence=intent.confidence,
                deliverables={}, summary=f"{tool.title}: {prepared.status}.",
                clarifications=prepared.messages,
            )

        produced = tool.run(prepared.payload, params)
        issues = tool.qa(produced.report)
        if issues:
            return JobResult(
                status=STATUS_QA_FAILED, tool=tool.key, confidence=intent.confidence,
                deliverables={}, summary=f"{tool.title}: QA failed; no deliverables written.",
                qa_issues=issues,
            )

        # Ground first: the premium deck weaves the L3 citations in, so they must
        # be resolved before the deliver path runs.
        citations = self._ground(tool)
        written = tool.deliver(produced.report, out_dir / tool.key, request.client)
        if tool.deck is not None:
            deck_files = tool.deck(
                produced.report, out_dir / tool.key, request.client, citations, intent.confidence
            )
            written.update({f"deck_{name}": path for name, path in deck_files.items()})
        summary = self._narrative(produced.summary, tool.title, citations)
        return JobResult(
            status=STATUS_OK, tool=tool.key, confidence=intent.confidence,
            deliverables={name: str(path) for name, path in written.items()}, summary=summary,
            citations=citations, kb_warnings=self.knowledge.warnings(),
        )

    def _ground(self, tool: Tool) -> list[str]:
        """Cite domain knowledge for the tool's topic, bridged to the implementing code.

        Reuses the tool's own intent_keywords as the books query. For each cited
        concept the L3 bridge also resolves the source that implements it (theory
        -> code), appended as "  -> src/file.py:Lnn". Returns [] when the books graph
        is absent (fresh clone); the code link is simply dropped when the code graph
        is absent or has no match, so grounding degrades one field at a time.

        The separator is ASCII "->" on purpose: citations are printed by the CLI on
        Windows (cp1252), where a "→" glyph would raise UnicodeEncodeError.
        """
        terms = " ".join(tool.intent_keywords)
        if not terms.strip():
            return []
        cites: list[str] = []
        for hit in self.knowledge.search(terms, graph="books", limit=3):
            loc = f" {hit.location}" if hit.location else ""
            cite = f"{hit.label} — {hit.source}{loc}".strip()
            impl = self.knowledge.implements(hit)
            if impl and impl.source:
                impl_loc = f":{impl.location}" if impl.location else ""
                cite += f"  -> {impl.source}{impl_loc}"
            cites.append(cite)
        return cites

    def _narrative(self, base_summary: str, tool_title: str, citations: list[str] | None = None) -> str:
        """Optional LLM polish, grounded in the L3 citations when present.

        The returned summary is untrusted display text (it echoes the brief and any
        LLM output); escape it at the render site if it is ever shown as HTML.
        """
        if not self.provider.available():
            return base_summary
        ground = ""
        if citations:
            ground = "\nGround it in these sources where relevant: " + "; ".join(citations)
        if self.persona:
            instruction = (
                f"You are {self.persona}. Rewrite this {tool_title} result summary in one clear, "
                "client-ready sentence in your voice. Keep every number. Return only the sentence."
            )
        else:
            instruction = (
                f"Rewrite this {tool_title} result summary in one clear, client-ready sentence. "
                "Keep every number. Return only the sentence."
            )
        try:
            text = self.provider.complete(f"{instruction}\n\n{base_summary}{ground}")
        except Exception:
            logger.debug("narrative upgrade failed", exc_info=True)
            return base_summary
        return text.strip() or base_summary
