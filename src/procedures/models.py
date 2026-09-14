"""Dataclasses for the procedures engine: register rows, findings, and the audit result."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from nosilo_core.guided import GuidedOutcome


@dataclass(frozen=True)
class ProcedureRecord:
    """One row of a client's procedure register, as reported -- never normalized further.

    ``raw_last_reviewed_at`` preserves whatever string a source cell held when it could
    not be parsed into ``last_reviewed_at`` (Task 3's job fills it), so a ``NO_REVIEW_DATE``
    finding's evidence can show what was actually there instead of a silent ``None``.
    """

    name: str
    procedure_id: str = ""
    process_area: str = ""
    owner: str = ""
    approver: str = ""
    version: str = ""
    approved_at: date | None = None
    last_reviewed_at: date | None = None
    review_cadence_days: int | None = None
    location: str = ""
    linked_kpi: str = ""
    reaction_plan: str = ""
    status: str = ""
    raw_last_reviewed_at: str = ""


@dataclass(frozen=True)
class ProcedureFinding:
    """One detected compliance gap (or explicit data gap) for one procedure.

    ``evidence`` always carries at least one literal fragment -- a finding with no
    stated evidence would be indistinguishable from an opinion.
    """

    procedure_id: str
    name: str
    code: str
    severity: str  # high | medium | low
    state: str  # cumple | no_cumple | sin_dato
    detail: str
    evidence: tuple[str, ...]
    owner: str
    due_date: date | None
    days_overdue: int | None


@dataclass(frozen=True)
class RegisterAudit:
    """Everything the job layer needs from one audit run: counts, findings, the verdict.

    ``as_of_source`` and ``records`` default empty here: ``audit_register`` always
    receives an explicit ``as_of`` (this engine never reads a clock), so from its own
    point of view the date is always "param"-sourced. The job layer (Task 3) is the one
    that can tell a user-supplied ``as_of`` apart from one it defaulted to the clock
    itself, and it already holds the original records list -- so it fills both fields
    with ``dataclasses.replace`` rather than this module guessing on its behalf.
    """

    as_of: date
    n_procedures: int
    n_retired: int
    n_overdue: int
    n_due_soon: int
    n_without_owner: int
    median_days_since_review: float | None
    findings: tuple[ProcedureFinding, ...]
    outcome: GuidedOutcome
    summary: str
    default_cadence_days: int
    cadence_source: str
    as_of_source: str = "param"
    records: tuple[ProcedureRecord, ...] = ()


@dataclass(frozen=True)
class DocumentControl:
    """The document-control header Task 2's draft templates stamp onto a rendered SOP."""

    owner: str
    approver: str
    version: str
    effective_date: date
    next_review: date
    review_cadence_days: int
    cadence_source: str
    approved_at: date | None = None
    status: str = "draft"
    template_version: str = "1"
