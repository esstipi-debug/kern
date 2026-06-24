"""Request/result DTOs for the orchestrator."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.guided import GuidedOutcome

STATUS_OK = "ok"
STATUS_NEEDS_CLARIFICATION = "needs_clarification"
STATUS_NEEDS_DATA = "needs_data"
STATUS_QA_FAILED = "qa_failed"
STATUS_ERROR = "error"


@dataclass(frozen=True)
class JobRequest:
    """A unit of work: a free-form brief, optional data, optional explicit routing."""

    brief: str
    data_path: str | None = None
    job_type: str | None = None
    params: dict = field(default_factory=dict)
    client: str = "Client"


@dataclass(frozen=True)
class JobResult:
    """The outcome the orchestrator returns for a request."""

    status: str
    tool: str | None
    confidence: float
    deliverables: dict[str, str]
    summary: str
    qa_issues: list[str] = field(default_factory=list)
    clarifications: list[str] = field(default_factory=list)
    citations: list[str] = field(default_factory=list)  # L3 domain grounding (book/chapter)
    kb_warnings: list[str] = field(default_factory=list)  # surfaced when a knowledge graph is degraded
    guided: GuidedOutcome | None = None  # never-unprotected contract (Guided Execution Layer)
