"""The orchestrator: brief + optional data -> routed, QA-gated deliverable."""

from __future__ import annotations

import logging
from pathlib import Path

from .intent import classify
from .llm import LLMProvider, get_provider
from .registry import ToolRegistry
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
    def __init__(self, registry: ToolRegistry | None = None, provider: LLMProvider | None = None) -> None:
        self.registry = registry if registry is not None else build_default_registry()
        self.provider = provider if provider is not None else get_provider()

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
            return self._run(request, Path(out_dir))
        except Exception as exc:  # never crash the caller — surface as error status
            logger.debug("orchestrator.run failed: %s", exc, exc_info=True)
            return JobResult(status=STATUS_ERROR, tool=None, confidence=0.0,
                             deliverables={}, summary=f"{type(exc).__name__}: {exc}")

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

        written = tool.deliver(produced.report, out_dir / tool.key, request.client)
        summary = self._narrative(produced.summary, tool.title)
        return JobResult(
            status=STATUS_OK, tool=tool.key, confidence=intent.confidence,
            deliverables={name: str(path) for name, path in written.items()}, summary=summary,
        )

    def _narrative(self, base_summary: str, tool_title: str) -> str:
        """Optional LLM polish. Falls back silently to the deterministic summary.

        The returned summary is untrusted display text (it echoes the brief and any
        LLM output); escape it at the render site if it is ever shown as HTML.
        """
        if not self.provider.available():
            return base_summary
        try:
            text = self.provider.complete(
                f"Rewrite this {tool_title} result summary in one clear, client-ready sentence. "
                f"Keep every number. Return only the sentence.\n\n{base_summary}"
            )
        except Exception:
            logger.debug("narrative upgrade failed", exc_info=True)
            return base_summary
        return text.strip() or base_summary
