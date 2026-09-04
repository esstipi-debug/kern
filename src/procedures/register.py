"""Register audit engine -- pure, clock-free detection over a client's SOP register.

Mirrors ``src/org_friction.py``'s shape (frozen dataclasses, module constants, small
private detector functions, one public ``detect`` and one public ``audit_register``),
applied to a different domain: not "is this number being managed", but "is this
procedure current, owned, approved and versioned the way ISO 9001:2015 and common
practice (GDI, SweetProcess) expect".

Three states per check -- ``cumple`` (compliant), ``no_cumple`` (a real gap), ``sin_dato``
(cannot be evaluated). ``sin_dato`` is never reported as a failure, and a missing date is
never filled with a guessed value ("today"). Only ``NO_REVIEW_DATE`` produces ``sin_dato``;
every other code that fires is a fact directly readable off the row (a blank owner, an
unset KPI link...), so it is ``no_cumple`` by construction. A compliant row never appears
in ``findings`` at all -- ``cumple`` is never actually attached to a ``ProcedureFinding``;
it exists as a named state for callers that want to talk about the checks that passed.

A ``status == "retired"`` row skips every check, vigencia (review-date) checks and the
rest alike -- once a procedure is no longer in force, whether it has an owner, a version
number or a KPI link is not actionable.

Never emits ``EXECUTED``: this audits a register, it does not act on anyone's behalf.
See ``audit_register()``'s ``GuidedOutcome``, always ``HANDOFF``.
"""

from __future__ import annotations

from datetime import date, timedelta
from statistics import median

from nosilo_core.guided import GuidedOutcome, HandoffPacket, Residual, as_handoff

from src.procedures.models import ProcedureFinding, ProcedureRecord, RegisterAudit

OVERDUE_REVIEW = "overdue_review"
REVIEW_DUE_SOON = "review_due_soon"
NO_OWNER = "no_owner"
OWNER_IS_APPROVER = "owner_is_approver"
NO_APPROVAL = "no_approval"
NO_KPI_LINK = "no_kpi_link"
UNVERSIONED = "unversioned"
NO_REVIEW_DATE = "no_review_date"
ALL_CODES = (
    OVERDUE_REVIEW,
    REVIEW_DUE_SOON,
    NO_OWNER,
    OWNER_IS_APPROVER,
    NO_APPROVAL,
    NO_KPI_LINK,
    UNVERSIONED,
    NO_REVIEW_DATE,
)

STATE_CUMPLE = "cumple"
STATE_NO_CUMPLE = "no_cumple"
STATE_SIN_DATO = "sin_dato"

RETIRED_STATUS = "retired"

#: ISO 9001:2015 does not fix a review interval; 365 days is the common convention this
#: tool defaults to (GDI, SweetProcess). A row's own ``review_cadence_days`` wins over it.
DEFAULT_REVIEW_CADENCE_DAYS = 365
DUE_SOON_DAYS = 30

_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}


def _evidence_date(value: date | None) -> str:
    """A date rendered for a human-readable ``evidence`` string -- ISO format, or the
    literal ``"sin dato"`` rather than Python's own ``str(None) == "None"``, which
    reads as code inside an otherwise-prose evidence sentence. Does not touch the
    three-state semantics (``sin_dato`` the STATE, vs. this being only how a missing
    date is spelled inside ``evidence``) -- the value columns in
    ``jobs/procedure_audit_job.py::write_operational`` were already blank for a
    missing date; this only fixes the sentence describing it."""
    return value.isoformat() if value is not None else "sin dato"


def _is_retired(record: ProcedureRecord) -> bool:
    """Case/whitespace-insensitive ``status == "retired"`` -- matches every
    neighbouring comparison in this module (``_no_approval``, ``_owner_is_approver``,
    both ``.strip().lower()``). Safe today only because
    ``jobs/procedure_audit_job.py::_normalize_status`` already lowercases ``status``
    before the engine ever sees it, but a direct engine caller passing ``"Retired"``
    must get the same result, not eight checks firing on a retired SOP."""
    return record.status.strip().lower() == RETIRED_STATUS


def _effective_cadence(record: ProcedureRecord, default_cadence_days: int) -> int:
    """The row's own cadence when it is a positive int, else the caller's default."""
    if record.review_cadence_days is not None and record.review_cadence_days > 0:
        return record.review_cadence_days
    return default_cadence_days


def next_review_date(record: ProcedureRecord, default_cadence_days: int) -> date | None:
    """``last_reviewed_at + cadence``; ``None`` when there is no review date to add to."""
    if record.last_reviewed_at is None:
        return None
    cadence = _effective_cadence(record, default_cadence_days)
    return record.last_reviewed_at + timedelta(days=cadence)


def days_overdue(record: ProcedureRecord, as_of: date, default_cadence_days: int) -> int | None:
    """``(as_of - next_review).days``; ``None`` when there is no review date."""
    next_review = next_review_date(record, default_cadence_days)
    if next_review is None:
        return None
    return (as_of - next_review).days


def _vigencia_finding(
    record: ProcedureRecord, *, as_of: date, default_cadence_days: int, due_soon_days: int
) -> ProcedureFinding | None:
    """The one review-date check that can fire for a row: missing / overdue / due soon.

    Mutually exclusive by construction -- a row is missing a date, or it has exactly one
    ``days_overdue`` value, so at most one of the three codes below is ever returned.
    """
    if record.last_reviewed_at is None:
        return ProcedureFinding(
            procedure_id=record.procedure_id,
            name=record.name,
            code=NO_REVIEW_DATE,
            severity="low",
            state=STATE_SIN_DATO,
            detail="no tiene fecha de ultima revision registrada",
            evidence=(
                f"last_reviewed_at={_evidence_date(None)}, "
                f"raw_last_reviewed_at='{record.raw_last_reviewed_at}'",
            ),
            owner=record.owner,
            due_date=None,
            days_overdue=None,
        )

    cadence = _effective_cadence(record, default_cadence_days)
    next_review = next_review_date(record, default_cadence_days)
    do = (as_of - next_review).days
    evidence = (
        f"last_reviewed_at={record.last_reviewed_at.isoformat()}, cadence={cadence}, "
        f"next_review={next_review.isoformat()}, as_of={as_of.isoformat()}",
    )

    if do > 0:
        no_owner = record.owner.strip() == ""
        severity = "high" if (no_owner or do >= 2 * cadence) else "medium"
        return ProcedureFinding(
            procedure_id=record.procedure_id,
            name=record.name,
            code=OVERDUE_REVIEW,
            severity=severity,
            state=STATE_NO_CUMPLE,
            detail=f"revision vencida hace {do} dia(s) (cadencia {cadence} dia(s))",
            evidence=evidence,
            owner=record.owner,
            due_date=next_review,
            days_overdue=do,
        )

    if -due_soon_days <= do <= 0:
        return ProcedureFinding(
            procedure_id=record.procedure_id,
            name=record.name,
            code=REVIEW_DUE_SOON,
            severity="low",
            state=STATE_NO_CUMPLE,
            detail=f"revision vence en {-do} dia(s) (cadencia {cadence} dia(s))",
            evidence=evidence,
            owner=record.owner,
            due_date=next_review,
            days_overdue=do,
        )

    return None


def _no_owner(record: ProcedureRecord) -> ProcedureFinding | None:
    if record.owner.strip():
        return None
    return ProcedureFinding(
        procedure_id=record.procedure_id,
        name=record.name,
        code=NO_OWNER,
        severity="medium",
        state=STATE_NO_CUMPLE,
        detail="no tiene dueno asignado",
        evidence=(f"owner='{record.owner}'",),
        owner=record.owner,
        due_date=None,
        days_overdue=None,
    )


def _owner_is_approver(record: ProcedureRecord) -> ProcedureFinding | None:
    owner = record.owner.strip()
    approver = record.approver.strip()
    if not owner or not approver or owner.lower() != approver.lower():
        return None
    return ProcedureFinding(
        procedure_id=record.procedure_id,
        name=record.name,
        code=OWNER_IS_APPROVER,
        severity="medium",
        state=STATE_NO_CUMPLE,
        detail=f"el dueno y el aprobador son la misma persona ('{record.owner}')",
        evidence=(f"owner='{record.owner}', approver='{record.approver}'",),
        owner=record.owner,
        due_date=None,
        days_overdue=None,
    )


def _no_approval(record: ProcedureRecord) -> ProcedureFinding | None:
    if record.status.strip().lower() != "approved":
        return None
    if record.approver.strip() and record.approved_at is not None:
        return None
    return ProcedureFinding(
        procedure_id=record.procedure_id,
        name=record.name,
        code=NO_APPROVAL,
        severity="high",
        state=STATE_NO_CUMPLE,
        detail="figura como 'approved' pero falta aprobador o fecha de aprobacion",
        evidence=(
            f"status='{record.status}', approver='{record.approver}', "
            f"approved_at={_evidence_date(record.approved_at)}",
        ),
        owner=record.owner,
        due_date=None,
        days_overdue=None,
    )


def _no_kpi_link(record: ProcedureRecord) -> ProcedureFinding | None:
    if record.linked_kpi.strip():
        return None
    return ProcedureFinding(
        procedure_id=record.procedure_id,
        name=record.name,
        code=NO_KPI_LINK,
        severity="low",
        state=STATE_NO_CUMPLE,
        detail="no tiene un KPI vinculado",
        evidence=(f"linked_kpi='{record.linked_kpi}'",),
        owner=record.owner,
        due_date=None,
        days_overdue=None,
    )


def _unversioned(record: ProcedureRecord) -> ProcedureFinding | None:
    if record.version.strip():
        return None
    return ProcedureFinding(
        procedure_id=record.procedure_id,
        name=record.name,
        code=UNVERSIONED,
        severity="low",
        state=STATE_NO_CUMPLE,
        detail="no tiene numero de version",
        evidence=(f"version='{record.version}'",),
        owner=record.owner,
        due_date=None,
        days_overdue=None,
    )


_DETECTORS = (_no_owner, _owner_is_approver, _no_approval, _no_kpi_link, _unversioned)


def _sort_key(finding: ProcedureFinding) -> tuple:
    severity_rank = _SEVERITY_RANK.get(finding.severity, 3)
    overdue_key = (1, 0) if finding.days_overdue is None else (0, -finding.days_overdue)
    return (severity_rank, overdue_key, finding.name)


def detect(
    records: list[ProcedureRecord],
    *,
    as_of: date,
    default_cadence_days: int = DEFAULT_REVIEW_CADENCE_DAYS,
    due_soon_days: int = DUE_SOON_DAYS,
) -> tuple[ProcedureFinding, ...]:
    """Run every check over every non-retired row; rank by severity, then overdue, then name."""
    findings: list[ProcedureFinding] = []
    for record in records:
        if _is_retired(record):
            continue
        vigencia = _vigencia_finding(
            record, as_of=as_of, default_cadence_days=default_cadence_days, due_soon_days=due_soon_days
        )
        if vigencia is not None:
            findings.append(vigencia)
        for finder in _DETECTORS:
            found = finder(record)
            if found is not None:
                findings.append(found)
    findings.sort(key=_sort_key)
    return tuple(findings)


def _overdue_packet(overdue: list[ProcedureFinding]) -> HandoffPacket:
    steps = [
        f"revisar {f.name} - dueno {f.owner.strip() or 'SIN ASIGNAR'} - vence {f.due_date.isoformat()}"
        for f in overdue
    ]
    items = [
        {
            "procedure_id": f.procedure_id,
            "name": f.name,
            "owner": f.owner,
            "due_date": f.due_date.isoformat(),
            "days_overdue": f.days_overdue,
        }
        for f in overdue
    ]
    deadline = min(f.due_date for f in overdue).isoformat()
    return HandoffPacket(
        title=f"Revisar los {len(overdue)} procedimientos vencidos",
        steps=steps,
        data={"items": items},
        deadline=deadline,
        risk_if_skipped=(
            "Un procedimiento vencido puede seguir guiando el trabajo diario con una "
            "version desactualizada sin que nadie lo haya confirmado."
        ),
    )


def _no_owner_packet(no_owner: list[ProcedureFinding]) -> HandoffPacket:
    steps = [f"asignar dueno a {f.name}" for f in no_owner]
    return HandoffPacket(
        title="Asignar dueno",
        steps=steps,
        risk_if_skipped=(
            "Sin un dueno asignado, nadie es responsable de mantener el procedimiento "
            "vigente ni de reaccionar ante un hallazgo de auditoria."
        ),
    )


def _due_soon_packet(due_soon: list[ProcedureFinding]) -> HandoffPacket:
    steps = [f"programar revision de {f.name} - vence {f.due_date.isoformat()}" for f in due_soon]
    return HandoffPacket(
        title="Programar revisiones proximas",
        steps=steps,
        risk_if_skipped=(
            "Si la revision no se programa con anticipacion, el procedimiento puede "
            "pasar a vencido sin aviso previo."
        ),
    )


def _confirm_cadence_packet() -> HandoffPacket:
    return HandoffPacket(
        title="Confirmar la cadencia declarada y re-correr en 30 dias",
        steps=[
            "Confirmar que la cadencia de revision (declarada o el default) sigue siendo "
            "la correcta para cada area de proceso.",
            "Re-correr esta auditoria en 30 dias para detectar procedimientos que se "
            "acerquen a su proxima revision.",
        ],
        risk_if_skipped=(
            "Sin una confirmacion periodica, un cambio de cadencia no declarado puede "
            "pasar desapercibido hasta que un procedimiento ya este vencido."
        ),
    )


def _build_outcome(findings: tuple[ProcedureFinding, ...]) -> GuidedOutcome:
    """Build the D5 verdict: one packet per condition that applies, always >=1 packet.

    Only three conditions get their own packet (overdue, no-owner, due-soon); when none
    of them applies -- whether because nothing at all fired, or only a lower-signal check
    like UNVERSIONED did -- the confirm-and-rerun packet is the safe, always-available
    fallback, so this never asks ``as_handoff`` to build a HANDOFF with zero packets.
    """
    overdue = [f for f in findings if f.code == OVERDUE_REVIEW]
    no_owner = [f for f in findings if f.code == NO_OWNER]
    due_soon = [f for f in findings if f.code == REVIEW_DUE_SOON]

    packets: list[HandoffPacket] = []
    if overdue:
        packets.append(_overdue_packet(overdue))
    if no_owner:
        packets.append(_no_owner_packet(no_owner))
    if due_soon:
        packets.append(_due_soon_packet(due_soon))
    if not packets:
        packets.append(_confirm_cadence_packet())

    residual = Residual(
        description=(
            "Esta auditoria revisa los metadatos del registro (fechas, duenos, version, "
            "vinculos) -- no verifica que el contenido del procedimiento siga reflejando "
            "como se trabaja en la realidad."
        ),
        risk_if_skipped=(
            "Un procedimiento puede verse conforme en el papel mientras describe un "
            "proceso que ya nadie sigue."
        ),
    )
    summary = (
        f"{len(overdue)} procedimiento(s) vencido(s), {len(due_soon)} por vencer, "
        f"{len(no_owner)} sin dueno asignado."
    )
    return as_handoff(summary, packets, residuals=[residual])


def audit_register(
    records: list[ProcedureRecord],
    *,
    as_of: date,
    default_cadence_days: int = DEFAULT_REVIEW_CADENCE_DAYS,
    due_soon_days: int = DUE_SOON_DAYS,
    cadence_source: str = "convention",
) -> RegisterAudit:
    """Audit a client's procedure register as of a given date. Never touches a clock.

    ``cadence_source`` is decided by the caller ("param" when it passed its own cadence,
    "convention" for the ISO-silent default) -- this function only transports it into the
    result; it has no way to know where its own ``default_cadence_days`` came from.
    """
    if not records:
        raise ValueError("empty register")

    findings = detect(
        records, as_of=as_of, default_cadence_days=default_cadence_days, due_soon_days=due_soon_days
    )

    n_procedures = len(records)
    n_retired = sum(1 for r in records if _is_retired(r))
    n_overdue = sum(1 for f in findings if f.code == OVERDUE_REVIEW)
    n_due_soon = sum(1 for f in findings if f.code == REVIEW_DUE_SOON)
    n_without_owner = sum(1 for f in findings if f.code == NO_OWNER)

    days_since_review = [
        (as_of - r.last_reviewed_at).days
        for r in records
        if not _is_retired(r) and r.last_reviewed_at is not None
    ]
    median_days_since_review = float(median(days_since_review)) if days_since_review else None

    outcome = _build_outcome(findings)
    summary = (
        f"{n_procedures} procedimiento(s) en el registro ({n_retired} retirado(s)): "
        f"{n_overdue} vencido(s), {n_due_soon} por vencer, {n_without_owner} sin dueno."
    )

    return RegisterAudit(
        as_of=as_of,
        n_procedures=n_procedures,
        n_retired=n_retired,
        n_overdue=n_overdue,
        n_due_soon=n_due_soon,
        n_without_owner=n_without_owner,
        median_days_since_review=median_days_since_review,
        findings=findings,
        outcome=outcome,
        summary=summary,
        default_cadence_days=default_cadence_days,
        cadence_source=cadence_source,
    )
