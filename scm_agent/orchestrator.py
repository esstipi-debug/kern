"""The orchestrator: brief + optional data -> routed, QA-gated deliverable."""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path

from src import client_profile

from .guided_bridge import to_guided_outcome
from .intent import classify
from .knowledge import KnowledgeBase
from .llm import LLMProvider, get_provider, narrative_rewrite
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
        clients_root: Path | str | None = client_profile.DEFAULT_CLIENTS_ROOT,
        lang: str | None = None,
    ) -> None:
        self.registry = registry if registry is not None else build_default_registry()
        self.provider = provider if provider is not None else get_provider()
        # L3 domain knowledge. Loads the books graph (committed) + code graph
        # (gitignored). Absent graphs degrade gracefully to no citations.
        self.knowledge = knowledge if knowledge is not None else KnowledgeBase()
        # The operating mode's voice for client-facing narration. Empty => the
        # narrative prompt is unchanged (the deterministic output never depends on it).
        self.persona = persona
        # Where durable per-client parameter profiles live (see src/client_profile.py).
        # None disables profile lookup entirely — the right setting for multi-tenant
        # surfaces (webapp/MCP) where `client` is a caller-supplied display label, not
        # an authenticated identity: honoring it would let one tenant pull another's
        # real cost parameters just by naming them.
        self.clients_root = None if clients_root is None else Path(clients_root)
        # Target language for the LLM narrative rewrite below (src/i18n.py's
        # static labels cover the deterministic path; this is the LLM path).
        # None (default) omits the language clause entirely - preserves this
        # class's exact pre-E4 prompt wording for its existing production
        # callers (webapp/app.py, webapp/mcp_server.py, examples/run_agent.py),
        # none of which have a way to opt into a language yet.
        self.lang = lang

    def run(
        self,
        brief: str,
        *,
        data_path: str | None = None,
        overrides: dict | None = None,
        job_type: str | None = None,
        client: str = "Client",
        strict_params: bool = False,
        out_dir: str | Path = "deliverables/agent",
    ) -> JobResult:
        overrides = overrides or {}
        request = JobRequest(brief=brief, data_path=data_path, job_type=job_type,
                             params=dict(overrides), client=client, strict_params=strict_params)
        try:
            result = self._run(request, Path(out_dir))
        except Exception:  # never crash the caller — surface as error status
            logger.error("orchestrator.run failed", exc_info=True)
            result = JobResult(status=STATUS_ERROR, tool=None, confidence=0.0,
                               deliverables={}, summary="An internal error occurred.")
        # Single boundary: every result leaves with a protected, executable path. A tool may
        # supply its own ranked-options outcome on success (set in _run); otherwise derive the
        # protected fallback. Either way, no result is a dead end.
        return replace(result, guided=result.guided or to_guided_outcome(result))

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
        # Client profile fills param gaps only — it never overrides an explicit
        # params/override value (merge_params puts the profile first, params last).
        profile = None
        if self.clients_root is not None:
            try:
                slug = client_profile.slugify_client_id(request.client)
            except ValueError:
                slug = None  # unslugifiable label (punctuation-only, non-Latin) => no profile;
                # the label stays what it always was — display copy on the deliverable.
            if slug is not None:
                try:
                    profile = client_profile.load_profile(slug, root=self.clients_root)
                except ValueError as exc:
                    # A corrupt profile.json must fail loudly and actionably: it is
                    # hand-answered, non-regenerable data, and silently falling back to
                    # generic defaults would give this client wrong numbers.
                    return JobResult(
                        status=STATUS_ERROR, tool=tool.key, confidence=intent.confidence,
                        deliverables={}, summary=str(exc),
                        clarifications=[
                            f"fix or delete clients/{slug}/profile.json, or rewrite it with "
                            "client_profile.upsert_profile(...)",
                        ],
                    )
        params = client_profile.merge_params(params, profile)
        # prepare() must see the same merged params run() will: several tools bake
        # params into their payload at prepare time (multi_echelon service_level,
        # simulation order_cost, inventory lead_time_days).
        request = replace(request, params=params)

        if tool.requires_data and not request.data_path:
            return JobResult(
                status=STATUS_NEEDS_DATA, tool=tool.key, confidence=intent.confidence,
                deliverables={}, summary=f"{tool.title} needs a data file.",
                clarifications=[f"provide a data file for {tool.title}"],
            )

        if request.strict_params and tool.required_client_params:
            missing = [key for key in tool.required_client_params if key not in params]
            if missing:
                return JobResult(
                    status=STATUS_NEEDS_CLARIFICATION, tool=tool.key, confidence=intent.confidence,
                    deliverables={}, summary=f"{tool.title} needs client-specific parameters before it can run.",
                    clarifications=[
                        f"missing '{key}' for client '{request.client}' — provide it once; "
                        "it will be remembered for every future run" for key in missing
                    ],
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
        citations = self._ground(tool, request.brief)
        # Compute the ranked options once: they become JobResult.guided AND the deck's
        # action menu, so the sellable artifact carries the same choices the agent returns.
        guided = tool.options(produced.report) if tool.options else None
        deck_options = list(guided.options) if guided is not None else []
        written = tool.deliver(produced.report, out_dir / tool.key, request.client)
        if tool.deck is not None:
            deck_files = tool.deck(
                produced.report, out_dir / tool.key, request.client, citations,
                intent.confidence, deck_options,
            )
            written.update({f"deck_{name}": path for name, path in deck_files.items()})
        summary = self._narrative(produced.summary, tool.title, citations)
        return JobResult(
            status=STATUS_OK, tool=tool.key, confidence=intent.confidence,
            deliverables={name: str(path) for name, path in written.items()}, summary=summary,
            citations=citations, kb_warnings=self.knowledge.warnings(),
            guided=guided,
        )

    def _ground(self, tool: Tool, brief: str = "") -> list[str]:
        """Cite domain knowledge for the tool's topic, bridged to the implementing code.

        Uses tool keywords, the client brief, and method-advice hits from the L3
        graph. For each cited concept the bridge also resolves the implementing
        source when the code graph is present.
        """
        return self.knowledge.ground_citations(tool.intent_keywords, brief, limit=5)

    def _narrative(self, base_summary: str, tool_title: str, citations: list[str] | None = None) -> str:
        """Optional LLM polish, grounded in the L3 citations when present, in
        ``self.lang``.

        The returned summary is untrusted display text (it echoes the brief and any
        LLM output); escape it at the render site if it is ever shown as HTML.
        """
        return narrative_rewrite(
            self.provider, base_summary, tool_title,
            lang=self.lang, citations=citations, persona=self.persona,
        )
