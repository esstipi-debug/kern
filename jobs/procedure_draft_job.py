"""Procedure draft job -- turn a bilingual SOP template into a client-ready,
unapproved draft with a document-control header and a prepared approval handoff.

Sibling of ``jobs/procedure_audit_job.py``, mirroring its shape (module constants,
frozen ``*Request``/``prepare``/``run``/``verify``/``write_operational``/``build_deck``),
but this tool reads no client data file at all: ``prepare()`` resolves a
``DraftRequest`` straight from ``params``, and ``run()`` wraps
``src.procedures.templates.render_draft`` in an actual client interaction --
ISO 9001:2015 7.5.2(c) requires "review and approval for suitability and adequacy"
before a controlled document is used, so every draft this job produces is born
UNAPPROVED (``DocumentControl.approved_at is None``, ``status == "draft"``) and its
outcome is always a ``HANDOFF`` ("Aprobar el borrador"), never ``EXECUTED``: a
document that names itself controlled but was never reviewed by a human is worse
than no document at all.

Two rulings worth knowing before reading this file:

- **R3**: ``render_draft`` (Task 2) labels every substituted parameter's source
  "param" or "convention" only -- this job never introduces a "profile" source (see
  ``src/procedures/templates/__init__.py``'s own module docstring for why).
- The never-unprotected contract's ``verify_guided`` (``nosilo_core.guided``) checks
  a ``Residual.risk_if_skipped``, but NOT a ``HandoffPacket``'s -- exactly like
  ``procedure_audit_job.verify`` closes that gap for its own handoffs, ``verify``
  below asserts it for this job's single approval packet too.

``owner == approver`` (or either blank) is reported as a ``Residual`` -- a human
condition to fix before the segregation of duties is real -- never a ``verify()``
failure: the QA gate blocks a draft that lies about its own state (claims
approval, an orphan control-plan row, a training row with no target date), not a
client's staffing gap the agent cannot fix on its own.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

from nosilo_core.guided import EXECUTED, HandoffPacket, Residual, as_handoff, verify_guided
from src.deliverable import DataSource, Deliverable, Finding, Kpi, not_measured
from src.export import write_summary_csv
from src.procedures.models import DocumentControl
from src.procedures.templates import (
    PROCESS_KEYS,
    TEMPLATE_VERSION,
    ProcedureDraft,
    draft_to_markdown,
    get_template,
    render_draft,
    resolve_next_review,
)

DEFAULT_REVIEW_CADENCE_DAYS = 365
DEFAULT_VERSION = "1.0"
_VALID_LANGS = ("es", "en")

#: The literal Residual description used whenever document-control segregation of
#: duties does not hold (owner/approver blank, or the same person) -- exact text so
#: a caller can match on it the same way ``procedure_audit_job``'s own codes are
#: matched on.
OWNER_APPROVER_SEGREGATION = "dueno y aprobador deben ser personas distintas - segregacion"

_APPROVAL_HANDOFF_TITLE = "Aprobar el borrador"
_NO_LOCATION_DEFAULT = "el sistema donde viven los SOP"


@dataclass(frozen=True)
class DraftRequest:
    """Everything ``run()`` needs to turn one template into one client draft.

    ``params`` carries the full, unfiltered dict ``prepare()`` was given -- template
    parameters like ``service_level``/``lead_time_days`` (replenishment) or
    ``counts_per_year`` (cycle_count) live only here, since they are not typed
    fields of this request; ``run()`` passes it straight to ``render_draft``.
    """

    process: str
    owner: str
    approver: str
    version: str
    review_cadence_days: int
    cadence_source: str
    as_of: date
    as_of_source: str
    lang: str
    location: str
    params: dict


def prepare(params: dict | None) -> DraftRequest:
    """Resolve one ``DraftRequest`` from raw params -- no file, no client data.

    ``process`` is the only required key; missing or unknown raises ``ValueError``
    listing ``PROCESS_KEYS``. ``as_of`` is read from ``params["as_of"]`` or
    ``params["effective_date"]`` (ISO ``YYYY-MM-DD``); absent, it defaults to
    ``date.today()`` with ``as_of_source="clock"`` (a ``run()`` residual follows).
    ``lang`` defaults to ``"es"`` and is validated against ``_VALID_LANGS`` right
    here at the tool boundary -- the same place ``process`` and ``as_of`` are
    validated -- rather than left to surface later inside ``render_draft``.
    """
    params = params or {}

    process = params.get("process")
    if not process or process not in PROCESS_KEYS:
        raise ValueError(
            f"params['process'] es obligatorio y debe ser uno de: {', '.join(PROCESS_KEYS)}"
        )

    owner = str(params.get("owner", "") or "").strip()
    approver = str(params.get("approver", "") or "").strip()
    version = str(params.get("version") or DEFAULT_VERSION)

    if "review_cadence_days" in params:
        review_cadence_days = int(params["review_cadence_days"])
        cadence_source = "param"
    else:
        review_cadence_days = DEFAULT_REVIEW_CADENCE_DAYS
        cadence_source = "convention"

    raw_as_of = params.get("as_of") or params.get("effective_date")
    if raw_as_of:
        try:
            as_of = date.fromisoformat(str(raw_as_of))
        except ValueError as exc:
            raise ValueError(
                f"as_of/effective_date debe ser una fecha ISO (YYYY-MM-DD): recibido {raw_as_of!r}"
            ) from exc
        as_of_source = "param"
    else:
        as_of = date.today()
        as_of_source = "clock"

    lang = params.get("lang", "es")
    if lang not in _VALID_LANGS:
        raise ValueError(f"lang desconocido '{lang}': elija uno de {', '.join(_VALID_LANGS)}")
    location = str(params.get("location", "") or "").strip()

    return DraftRequest(
        process=process,
        owner=owner,
        approver=approver,
        version=version,
        review_cadence_days=review_cadence_days,
        cadence_source=cadence_source,
        as_of=as_of,
        as_of_source=as_of_source,
        lang=lang,
        location=location,
        params=params,
    )


def _approval_handoff(request: DraftRequest, control: DocumentControl) -> HandoffPacket:
    """The single prepared human step: review, approve, publish, record, schedule."""
    owner_label = request.owner or "sin asignar"
    approver_label = request.approver or "sin asignar"
    publish_target = request.location or _NO_LOCATION_DEFAULT

    return HandoffPacket(
        title=_APPROVAL_HANDOFF_TITLE,
        steps=[
            f"Revisar el borrador por adecuacion y suficiencia (dueno: {owner_label})",
            f"Aprobar el borrador (aprobador: {approver_label}, debe ser distinto del dueno)",
            f"Publicar el borrador aprobado en {publish_target}",
            "Registrar approved_at y version en el registro de procedimientos",
            f"Agendar la proxima revision para {control.next_review.isoformat()}",
        ],
        data={
            "owner": request.owner,
            "approver": request.approver,
            "due_date": control.next_review.isoformat(),
            "template_version": TEMPLATE_VERSION,
        },
        deadline=control.next_review.isoformat(),
        risk_if_skipped=(
            "Sin revision y aprobacion formal, el borrador no tiene validez de documento "
            "controlado bajo ISO 9001:2015 7.5.2(c) y el proceso puede operar con una "
            "version no verificada."
        ),
    )


def _residuals(request: DraftRequest) -> list[Residual]:
    residuals: list[Residual] = []

    owner_norm = request.owner.strip().lower()
    approver_norm = request.approver.strip().lower()
    if not owner_norm or not approver_norm or owner_norm == approver_norm:
        residuals.append(Residual(
            description=OWNER_APPROVER_SEGREGATION,
            risk_if_skipped=(
                "Si la misma persona redacta y aprueba el SOP (o falta un dueno o un "
                "aprobador), la aprobacion pierde su valor de control y el documento puede "
                "quedar aprobado sin una segunda revision independiente."
            ),
        ))

    if request.as_of_source == "clock":
        residuals.append(Residual(
            description="as_of tomado del reloj del servidor; pase params['as_of'] para fijarlo",
            risk_if_skipped=(
                "Sin un as_of fijo, la fecha de vigencia y la proxima revision del borrador "
                "dependen del dia en que se corrio, lo que hace el documento no reproducible."
            ),
        ))

    return residuals


def run(request: DraftRequest, params: dict | None = None) -> ProcedureDraft:
    """Render the template and wrap it in a HANDOFF approval outcome.

    ``params`` overrides ``request.params`` for the template substitution only
    (defaults to ``request.params`` when omitted, which is what every normal
    caller wants); the document-control fields always come from ``request``.
    """
    effective_params = request.params if params is None else params

    control = DocumentControl(
        owner=request.owner,
        approver=request.approver,
        version=request.version,
        effective_date=request.as_of,
        next_review=resolve_next_review(request.as_of, request.review_cadence_days),
        review_cadence_days=request.review_cadence_days,
        cadence_source=request.cadence_source,
        approved_at=None,
        status="draft",
        template_version=TEMPLATE_VERSION,
    )

    draft = render_draft(
        get_template(request.process), control=control, params=effective_params, lang=request.lang
    )

    outcome = as_handoff(
        f"Borrador de '{draft.title}' listo: requiere revision, aprobacion y publicacion antes de usarse.",
        [_approval_handoff(request, control)],
        residuals=_residuals(request),
    )

    return replace(draft, outcome=outcome)


def verify(draft: ProcedureDraft) -> list[str]:
    """QA gate: the never-unprotected contract holds, plus draft-specific checks."""
    issues = list(verify_guided(draft.outcome))

    if draft.control.approved_at is not None:
        issues.append("draft claims approval: control.approved_at must stay None until a human approves it")
    if draft.control.status != "draft":
        issues.append(f"draft status must stay 'draft', got '{draft.control.status}'")

    if len(draft.steps) < 3:
        issues.append(f"job breakdown needs at least 3 steps, got {len(draft.steps)}")
    for step in draft.steps:
        if not step.key_points or any(not kp.strip() for kp in step.key_points):
            issues.append(f"step {step.n} has an empty key point")
        if not step.reasons or any(not r.strip() for r in step.reasons):
            issues.append(f"step {step.n} has an empty reason")

    step_numbers = {step.n for step in draft.steps}
    for row in draft.control_plan:
        if row.process_step not in step_numbers:
            issues.append(f"control plan row references unknown step {row.process_step}")

    for row in draft.training:
        if row.target_date is None:
            issues.append(f"training row for '{row.role}' has no target_date")

    if draft.outcome.status == EXECUTED:
        issues.append("outcome must never be EXECUTED: a draft always needs human approval")

    # verify_guided checks a Residual's risk_if_skipped, but NOT a HandoffPacket's --
    # ruling: every handoff this job hands back must state the risk of skipping it,
    # so that gap is closed here (see module docstring).
    for h in draft.outcome.handoffs:
        if not h.risk_if_skipped.strip():
            issues.append(f"handoff without risk_if_skipped: '{h.title}'")

    return issues


def _write_xlsx(draft: ProcedureDraft, path: Path, client: str) -> Path:
    """Header/Steps/ControlPlan/Training workbook.

    ``openpyxl`` is imported here, never at module level -- production boots with
    only the ``web,mcp`` extras, and a module-level optional import would crash it
    (see ``src/deliverable.py::to_excel`` for the same pattern).
    """
    from openpyxl import Workbook

    wb = Workbook()

    header = wb.active
    header.title = "Header"
    header.append(["Client", client])
    header.append(["Process", draft.process])
    header.append(["Title", draft.title])
    header.append(["Owner", draft.control.owner])
    header.append(["Approver", draft.control.approver])
    header.append(["Version", draft.control.version])
    header.append(["EffectiveDate", draft.control.effective_date.isoformat()])
    header.append(["ApprovedAt", draft.control.approved_at.isoformat() if draft.control.approved_at else ""])
    header.append(["NextReview", draft.control.next_review.isoformat()])
    header.append(["ReviewCadenceDays", draft.control.review_cadence_days])
    header.append(["CadenceSource", draft.control.cadence_source])
    header.append(["Status", draft.control.status])
    header.append(["TemplateVersion", draft.control.template_version])

    steps_sheet = wb.create_sheet("Steps")
    steps_sheet.append(["n", "important_step", "key_points", "reasons"])
    for step in draft.steps:
        steps_sheet.append([step.n, step.important_step, " | ".join(step.key_points), " | ".join(step.reasons)])

    control_plan_sheet = wb.create_sheet("ControlPlan")
    control_plan_sheet.append([
        "process_step", "characteristic", "spec", "measurement_method",
        "frequency", "control_method", "reaction_plan", "owner_role",
    ])
    for row in draft.control_plan:
        control_plan_sheet.append([
            row.process_step, row.characteristic, row.spec, row.measurement_method,
            row.frequency, row.control_method, row.reaction_plan, row.owner_role,
        ])

    training_sheet = wb.create_sheet("Training")
    training_sheet.append(["role", "job", "target_date"])
    for row in draft.training:
        training_sheet.append([row.role, row.job, row.target_date.isoformat()])

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def write_operational(draft: ProcedureDraft, out_dir: str | Path, client: str = "Client") -> dict[str, Path]:
    """The machine-readable deliverables: the rendered markdown, a workbook with one
    sheet per section, and one CSV per repeating section (steps/control plan/training)."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)

    md_path = d / f"procedure_draft_{draft.process}.md"
    md_path.write_text(draft_to_markdown(draft), encoding="utf-8")

    xlsx_path = _write_xlsx(draft, d / f"procedure_draft_{draft.process}.xlsx", client)

    steps_rows = [
        {
            "n": step.n,
            "important_step": step.important_step,
            "key_points": " | ".join(step.key_points),
            "reasons": " | ".join(step.reasons),
        }
        for step in draft.steps
    ]
    control_plan_rows = [
        {
            "process_step": row.process_step,
            "characteristic": row.characteristic,
            "spec": row.spec,
            "measurement_method": row.measurement_method,
            "frequency": row.frequency,
            "control_method": row.control_method,
            "reaction_plan": row.reaction_plan,
            "owner_role": row.owner_role,
        }
        for row in draft.control_plan
    ]
    training_rows = [
        {"role": row.role, "job": row.job, "target_date": row.target_date.isoformat()}
        for row in draft.training
    ]

    return {
        "md": md_path,
        "xlsx": xlsx_path,
        "steps_csv": write_summary_csv(steps_rows, d / "procedure_draft_steps.csv"),
        "control_plan_csv": write_summary_csv(control_plan_rows, d / "procedure_draft_control_plan.csv"),
        "training_csv": write_summary_csv(training_rows, d / "procedure_draft_training.csv"),
    }


def build_deck(
    draft: ProcedureDraft,
    *,
    client: str,
    prepared: str | None = None,
    citations: tuple[str, ...] = (),
    confidence: float = 0.6,
    lang: str = "es",
) -> Deliverable:
    """The client-facing draft summary: who must review/approve, by when, and what
    the procedure actually says, with the prepared approval handoff as the residual."""
    findings = tuple(
        Finding(
            title=f"Paso {step.n}: {step.important_step}",
            detail="; ".join(step.key_points),
            impact="; ".join(step.reasons),
        )
        for step in draft.steps
    )

    kpis = (
        Kpi("Dueno", draft.control.owner or not_measured(lang),
            rationale="Responsable de revisar adecuacion y suficiencia del borrador"),
        Kpi("Aprobador", draft.control.approver or not_measured(lang),
            rationale="Responsable de aprobar el borrador, distinto del dueno"),
        Kpi("Version", draft.control.version, rationale="Version del documento borrador"),
        Kpi("Proxima revision", draft.control.next_review.isoformat(),
            rationale="Fecha limite para volver a revisar este SOP"),
        Kpi("Cadencia de revision", f"{draft.control.review_cadence_days} dias ({draft.control.cadence_source})",
            rationale="Frecuencia con la que este SOP debe revisarse"),
    )

    params_summary = "; ".join(
        f"{name}={value} ({source})" for name, value, source in draft.parameters_used
    ) or "(sin parametros)"
    data_sources = (
        DataSource(f"Plantilla {draft.process}", f"v{TEMPLATE_VERSION}", "por corrida"),
        DataSource("Parametros usados", params_summary, "por corrida"),
    )

    residual = " ".join(
        f"{r.description} {r.risk_if_skipped}".strip() for r in draft.outcome.residuals
    ) or "Sin residual declarado."

    summary = (
        f"Borrador de '{draft.title}' generado con fecha de vigencia "
        f"{draft.control.effective_date.isoformat()}, version {draft.control.version}. "
        "Aun no aprobado: ver Cobertura y traspaso para los pasos pendientes."
    )

    return Deliverable(
        title=draft.title,
        client=client,
        summary=summary,
        findings=findings,
        kpis=kpis,
        data_sources=data_sources,
        options=tuple(draft.outcome.options),
        citations=tuple(citations),
        confidence=confidence,
        residual=residual,
        prepared=prepared or "",
        lang=lang,
    )
