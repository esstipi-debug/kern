"""Bilingual SOP templates and deterministic rendering into a client-ready draft.

Domain: an SOP (Standard Operating Procedure -- NOT S&OP) is a document-control header
-- owner, approver, version, effective date, next review -- wrapped around a body in
Job Breakdown Sheet form (the TWI Job Instruction structure): numbered Important Steps,
each with Key Points and the Reason for each key point. A control plan links a subset
of those steps to a characteristic/KPI, a spec, a measurement method, a frequency, a
control method, a reaction plan and an owner role. A JI training timetable maps a role
to a job with a target date computed from the document's effective date.

ISO 9001:2015 7.5.2(c) requires "review and approval for suitability and adequacy"
before a controlled document is used -- so every draft this module renders is born
unapproved (``DocumentControl.approved_at`` stays ``None``, ``status`` stays "draft")
until a human signs off outside this module.

Mirrors ``src/procedures/register.py``'s shape -- module constants, frozen dataclasses,
small pure functions -- applied to rendering instead of auditing. Pure package: no
pandas, no file I/O, no system-clock lookups, no ``scm_agent`` imports. Split into one
module per template (``_replenishment.py``, ``_cycle_count.py``, ``_sop_cycle.py``) plus
this ``__init__`` for the shared dataclasses, functions and public re-exports -- the
bilingual literal content alone does not fit the project's file-size convention in a
single module (see ``rules/ecc/common/coding-style.md``).
``render_draft`` never raises on a bad ``{placeholder}`` -- an unresolved key in
template text is left as ``{key}`` rather than raising ``KeyError`` -- and never
guesses at a missing parameter's value: every parameter named in a template is either
supplied in ``params`` (source "param") or falls back to the template's own default
(source "convention"). This module never sees a "profile" source: the orchestrator
merges a client profile into ``params`` before a render runs, so from here a
profile-sourced value is indistinguishable from one the caller typed in directly.
``ProcedureDraft.outcome`` is always ``None`` here -- Task 4's job builds the
``GuidedOutcome`` once it wraps this render in an actual client interaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from nosilo_core.guided import GuidedOutcome

from src.procedures.models import DocumentControl

TEMPLATE_VERSION = "1"

_VALID_LANGS = ("es", "en")

#: The literal ISO 9001:2015 7.5.2(c) draft-status sentence, exact per language --
#: every rendered draft carries it, since this module never marks anything approved.
ISO_SENTENCE_ES = "BORRADOR - requiere revision y aprobacion para adecuacion y suficiencia (ISO 9001:2015 7.5.2c)"
ISO_SENTENCE_EN = "DRAFT - requires review and approval for suitability and adequacy (ISO 9001:2015 7.5.2c)"
_ISO_SENTENCE = {"es": ISO_SENTENCE_ES, "en": ISO_SENTENCE_EN}

OWNER_EQUALS_APPROVER_WARNING = "owner == approver: la segregacion dueno/aprobador no se cumple"


# ---- dataclasses: template data (per-language dicts) --------------------------------


@dataclass(frozen=True)
class JobBreakdownStep:
    """One numbered Important Step of a Job Breakdown Sheet, in both languages.

    ``key_points`` and ``reasons`` are positional: ``reasons[lang][i]`` is the reason
    for ``key_points[lang][i]`` -- the TWI Job Instruction pairing of "how" and "why".
    """

    n: int
    important_step: dict[str, str]
    key_points: dict[str, tuple[str, ...]]
    reasons: dict[str, tuple[str, ...]]


@dataclass(frozen=True)
class ControlPlanRow:
    """One control-plan row: links a JBS step to what is controlled and how."""

    process_step: int
    characteristic: dict[str, str]
    spec: dict[str, str]
    measurement_method: dict[str, str]
    frequency: dict[str, str]
    control_method: dict[str, str]
    reaction_plan: dict[str, str]
    owner_role: dict[str, str]


@dataclass(frozen=True)
class TrainingRow:
    """One JI training-timetable row: a role trained on a job, due some days after go-live."""

    role: dict[str, str]
    job: dict[str, str]
    target_offset_days: int


@dataclass(frozen=True)
class ProcedureTemplate:
    """One bilingual SOP template: content plus the labelled defaults it can substitute.

    ``parameters`` holds convention defaults keyed by the ``{placeholder}`` name they
    fill -- e.g. ``{"review_period_days": 7}``. ``render_draft`` labels each one "param"
    when the caller supplied it, "convention" when this default was used instead.
    """

    key: str
    title: dict[str, str]
    purpose: dict[str, str]
    scope: dict[str, str]
    steps: tuple[JobBreakdownStep, ...]
    control_plan: tuple[ControlPlanRow, ...]
    training: tuple[TrainingRow, ...]
    controlled_inputs: dict[str, tuple[str, ...]]
    parameters: dict[str, object]


# ---- dataclasses: rendered data (already resolved to one language) ------------------


@dataclass(frozen=True)
class RenderedStep:
    n: int
    important_step: str
    key_points: tuple[str, ...]
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class RenderedControlPlanRow:
    process_step: int
    characteristic: str
    spec: str
    measurement_method: str
    frequency: str
    control_method: str
    reaction_plan: str
    owner_role: str


@dataclass(frozen=True)
class RenderedTraining:
    role: str
    job: str
    target_date: date


@dataclass(frozen=True)
class ProcedureDraft:
    """A single-language, unapproved SOP draft ready to render to markdown.

    ``parameters_used`` is ``(name, value_as_str, source)`` with ``source`` in
    ``{"param", "convention"}`` -- see the module docstring for why "profile" never
    appears here. ``outcome`` stays ``None``: Task 4's job fills it once this render is
    wrapped in an actual client interaction.
    """

    process: str
    lang: str
    control: DocumentControl
    title: str
    purpose: str
    scope: str
    steps: tuple[RenderedStep, ...]
    control_plan: tuple[RenderedControlPlanRow, ...]
    training: tuple[RenderedTraining, ...]
    controlled_inputs: tuple[str, ...]
    parameters_used: tuple[tuple[str, str, str], ...]
    warnings: tuple[str, ...]
    outcome: GuidedOutcome | None = None


# ---- template content: one module per process, re-exported here --------------------

from src.procedures.templates._cycle_count import TEMPLATE as _CYCLE_COUNT  # noqa: E402
from src.procedures.templates._replenishment import TEMPLATE as _REPLENISHMENT  # noqa: E402
from src.procedures.templates._sop_cycle import TEMPLATE as _SOP_CYCLE  # noqa: E402

TEMPLATES: dict[str, ProcedureTemplate] = {
    _REPLENISHMENT.key: _REPLENISHMENT,
    _CYCLE_COUNT.key: _CYCLE_COUNT,
    _SOP_CYCLE.key: _SOP_CYCLE,
}
PROCESS_KEYS = tuple(TEMPLATES)


# ---- functions -----------------------------------------------------------------------


def _require_known_process(process: str) -> None:
    if process not in TEMPLATES:
        raise ValueError(f"unknown process '{process}': choose one of {', '.join(PROCESS_KEYS)}")


def get_template(process: str) -> ProcedureTemplate:
    """The named template, or ``ValueError`` listing ``PROCESS_KEYS`` if it does not exist."""
    _require_known_process(process)
    return TEMPLATES[process]


def resolve_next_review(as_of: date, cadence_days: int) -> date:
    """``as_of + cadence_days`` -- the pure date arithmetic behind a fresh ``DocumentControl``."""
    return as_of + timedelta(days=cadence_days)


class _SafeDict(dict):
    """``str.format_map`` mapping that leaves an unresolved ``{placeholder}`` untouched."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


_LANG_KEYS = frozenset({"es", "en"})


def _resolve_convention_default(name: str, default: object, lang: str) -> object:
    """Resolve one parameter's convention default for a given language.

    A default that is itself a per-language dict (``{"es": ..., "en": ...}``) is
    resolved to that language's string -- the same pattern every other piece of
    template content uses -- so a convention default never leaks the wrong language
    into a rendered draft. Before this fix, a plain-string default (e.g. "no declarado")
    was substituted unchanged regardless of ``lang``, so an ``en`` render could ship
    Spanish text whenever a parameter was left at its convention default.

    ``counts_per_year`` is a special case: its default is the raw ABC-class -> count
    dict imported directly from ``src.cycle_count.DEFAULT_POLICY`` (the same object,
    never copied -- see ``test_cycle_count_template_imports_default_policy_not_a_copy``),
    formatted here into a bilingual, human-readable string rather than a raw dict repr.
    """
    if isinstance(default, dict) and set(default) == _LANG_KEYS:
        return default[lang]
    if name == "counts_per_year" and isinstance(default, dict):
        return _format_counts_per_year(default, lang)
    return default


def _format_counts_per_year(counts: dict[str, int], lang: str) -> str:
    """``{"A": 12, "B": 4, "C": 1}`` -> ``"A: 12/anio, B: 4/anio, C: 1/anio"`` (``/yr`` in en)."""
    unit = "anio" if lang == "es" else "yr"
    return ", ".join(f"{cls}: {n}/{unit}" for cls, n in counts.items())


def render_draft(
    template: ProcedureTemplate, *, control: DocumentControl, params: dict[str, object], lang: str
) -> ProcedureDraft:
    """Resolve one template to a single-language, unapproved ``ProcedureDraft``.

    Every key in ``template.parameters`` is looked up in ``params`` first (source
    "param"); when it is absent the template's own default is used (source
    "convention") -- see the module docstring for why "profile" is never emitted here.
    Text substitution never raises: an unresolved ``{placeholder}`` is left as-is.
    """
    if lang not in _VALID_LANGS:
        raise ValueError(f"unknown lang '{lang}': choose one of {', '.join(_VALID_LANGS)}")

    resolved: list[tuple[str, object, str]] = []
    for name, default in template.parameters.items():
        if name in params:
            resolved.append((name, params[name], "param"))
        else:
            resolved.append((name, _resolve_convention_default(name, default, lang), "convention"))

    sub_map = {name: str(value) for name, value, _source in resolved}
    parameters_used = tuple((name, str(value), source) for name, value, source in resolved)

    def sub(text: str) -> str:
        return text.format_map(_SafeDict(sub_map))

    steps = tuple(
        RenderedStep(
            n=step.n,
            important_step=sub(step.important_step[lang]),
            key_points=tuple(sub(kp) for kp in step.key_points[lang]),
            reasons=tuple(sub(r) for r in step.reasons[lang]),
        )
        for step in template.steps
    )

    control_plan = tuple(
        RenderedControlPlanRow(
            process_step=row.process_step,
            characteristic=sub(row.characteristic[lang]),
            spec=sub(row.spec[lang]),
            measurement_method=sub(row.measurement_method[lang]),
            frequency=sub(row.frequency[lang]),
            control_method=sub(row.control_method[lang]),
            reaction_plan=sub(row.reaction_plan[lang]),
            owner_role=sub(row.owner_role[lang]),
        )
        for row in template.control_plan
    )

    training = tuple(
        RenderedTraining(
            role=sub(row.role[lang]),
            job=sub(row.job[lang]),
            target_date=control.effective_date + timedelta(days=row.target_offset_days),
        )
        for row in template.training
    )

    controlled_inputs = tuple(sub(text) for text in template.controlled_inputs[lang])

    warnings: list[str] = []
    owner = control.owner.strip()
    approver = control.approver.strip()
    if owner and owner.lower() == approver.lower():
        warnings.append(OWNER_EQUALS_APPROVER_WARNING)

    return ProcedureDraft(
        process=template.key,
        lang=lang,
        control=control,
        title=sub(template.title[lang]),
        purpose=sub(template.purpose[lang]),
        scope=sub(template.scope[lang]),
        steps=steps,
        control_plan=control_plan,
        training=training,
        controlled_inputs=controlled_inputs,
        parameters_used=parameters_used,
        warnings=tuple(warnings),
        outcome=None,
    )


_LABELS: dict[str, dict[str, str]] = {
    "control_header": {"es": "Control documental", "en": "Document control"},
    "owner": {"es": "Dueno", "en": "Owner"},
    "approver": {"es": "Aprobador", "en": "Approver"},
    "version": {"es": "Version", "en": "Version"},
    "effective_date": {"es": "Vigente desde", "en": "Effective date"},
    "approved_at": {"es": "Aprobado el", "en": "Approved at"},
    "next_review": {"es": "Proxima revision", "en": "Next review"},
    "cadence": {"es": "Cadencia de revision (dias, fuente)", "en": "Review cadence (days, source)"},
    "status": {"es": "Estado", "en": "Status"},
    "template_version": {"es": "Version de plantilla", "en": "Template version"},
    "purpose": {"es": "Proposito", "en": "Purpose"},
    "scope": {"es": "Alcance", "en": "Scope"},
    "procedure": {"es": "Procedimiento (Hoja de Desglose de Trabajo)", "en": "Procedure (Job Breakdown Sheet)"},
    "step_n": {"es": "#", "en": "#"},
    "step_important": {"es": "Paso importante", "en": "Important step"},
    "step_keypoints": {"es": "Puntos clave", "en": "Key points"},
    "step_reasons": {"es": "Razones", "en": "Reasons"},
    "control_plan": {"es": "Plan de control", "en": "Control plan"},
    "cp_step": {"es": "Paso", "en": "Step"},
    "cp_characteristic": {"es": "Caracteristica", "en": "Characteristic"},
    "cp_spec": {"es": "Especificacion", "en": "Spec"},
    "cp_method": {"es": "Metodo de medicion", "en": "Measurement method"},
    "cp_frequency": {"es": "Frecuencia", "en": "Frequency"},
    "cp_control_method": {"es": "Metodo de control", "en": "Control method"},
    "cp_reaction": {"es": "Plan de reaccion", "en": "Reaction plan"},
    "cp_owner_role": {"es": "Rol responsable", "en": "Owner role"},
    "training": {"es": "Entrenamiento (JI)", "en": "Training (JI)"},
    "tr_role": {"es": "Rol", "en": "Role"},
    "tr_job": {"es": "Tarea", "en": "Job"},
    "tr_target": {"es": "Fecha objetivo", "en": "Target date"},
    "controlled_inputs": {"es": "Insumos controlados", "en": "Controlled inputs"},
    "parameters_used": {"es": "Parametros usados", "en": "Parameters used"},
    "param_name": {"es": "Parametro", "en": "Parameter"},
    "param_value": {"es": "Valor", "en": "Value"},
    "param_source": {"es": "Fuente", "en": "Source"},
    "warnings": {"es": "Advertencias", "en": "Warnings"},
    "no_params": {"es": "(sin parametros)", "en": "(no parameters)"},
    "no_warnings": {"es": "(sin advertencias)", "en": "(no warnings)"},
}


def _label(key: str, lang: str) -> str:
    return _LABELS[key][lang]


def _markdown_table(headers: tuple[str, ...], rows: list[tuple[object, ...]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return lines


def draft_to_markdown(draft: ProcedureDraft) -> str:
    """Render a ``ProcedureDraft`` to a deterministic, single-language markdown document.

    ``ValueError`` (listing ``PROCESS_KEYS``) if ``draft.process`` is not a known
    template key. Never touches a clock and iterates only over the draft's own tuples,
    so the same draft always produces byte-identical markdown.
    """
    _require_known_process(draft.process)
    lang = draft.lang

    def L(key: str) -> str:
        return _label(key, lang)

    lines: list[str] = [f"# {draft.title}", "", f"**{_ISO_SENTENCE[lang]}**", "", f"## {L('control_header')}", ""]

    approved_at = draft.control.approved_at.isoformat() if draft.control.approved_at is not None else "-"
    control_rows: list[tuple[object, ...]] = [
        (L("owner"), draft.control.owner),
        (L("approver"), draft.control.approver),
        (L("version"), draft.control.version),
        (L("effective_date"), draft.control.effective_date.isoformat()),
        (L("approved_at"), approved_at),
        (L("next_review"), draft.control.next_review.isoformat()),
        (L("cadence"), f"{draft.control.review_cadence_days} ({draft.control.cadence_source})"),
        (L("status"), draft.control.status),
        (L("template_version"), draft.control.template_version),
    ]
    lines.extend(_markdown_table((L("param_name"), L("param_value")), control_rows))
    lines.append("")

    lines.append(f"## {L('purpose')}")
    lines.append("")
    lines.append(draft.purpose)
    lines.append("")

    lines.append(f"## {L('scope')}")
    lines.append("")
    lines.append(draft.scope)
    lines.append("")

    lines.append(f"## {L('procedure')}")
    lines.append("")
    step_rows: list[tuple[object, ...]] = [
        (step.n, step.important_step, "; ".join(step.key_points), "; ".join(step.reasons)) for step in draft.steps
    ]
    lines.extend(
        _markdown_table((L("step_n"), L("step_important"), L("step_keypoints"), L("step_reasons")), step_rows)
    )
    lines.append("")

    lines.append(f"## {L('control_plan')}")
    lines.append("")
    cp_rows: list[tuple[object, ...]] = [
        (
            row.process_step,
            row.characteristic,
            row.spec,
            row.measurement_method,
            row.frequency,
            row.control_method,
            row.reaction_plan,
            row.owner_role,
        )
        for row in draft.control_plan
    ]
    lines.extend(
        _markdown_table(
            (
                L("cp_step"),
                L("cp_characteristic"),
                L("cp_spec"),
                L("cp_method"),
                L("cp_frequency"),
                L("cp_control_method"),
                L("cp_reaction"),
                L("cp_owner_role"),
            ),
            cp_rows,
        )
    )
    lines.append("")

    lines.append(f"## {L('training')}")
    lines.append("")
    training_rows: list[tuple[object, ...]] = [
        (row.role, row.job, row.target_date.isoformat()) for row in draft.training
    ]
    lines.extend(_markdown_table((L("tr_role"), L("tr_job"), L("tr_target")), training_rows))
    lines.append("")

    lines.append(f"## {L('controlled_inputs')}")
    lines.append("")
    lines.extend(f"- {item}" for item in draft.controlled_inputs)
    lines.append("")

    lines.append(f"## {L('parameters_used')}")
    lines.append("")
    if draft.parameters_used:
        lines.extend(
            _markdown_table(
                (L("param_name"), L("param_value"), L("param_source")),
                list(draft.parameters_used),
            )
        )
    else:
        lines.append(L("no_params"))
    lines.append("")

    lines.append(f"## {L('warnings')}")
    lines.append("")
    if draft.warnings:
        lines.extend(f"- {w}" for w in draft.warnings)
    else:
        lines.append(L("no_warnings"))

    return "\n".join(lines)
