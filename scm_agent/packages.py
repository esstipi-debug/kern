"""Commercial package runner - several registered tools, one QA-gated deliverable.

A *package* is a commercial bundle (see ``documentation/paquetes/``): a fixed set
of registry tools run together for one client, producing every tool's own
deliverable PLUS one consolidated package deck. The runner reuses each Tool's
``prepare -> run -> qa -> deliver`` callbacks verbatim (the same ones the
orchestrator drives) - no job logic is duplicated; only ``intent.classify`` is
skipped because the operator explicitly chose the package.

The per-tool guarantee "QA fails => no deliverable" is preserved at PACKAGE
level by running in two phases:

1. **Compute** every step (prepare/run/qa) with nothing written to the output
   directory. Derived inputs (e.g. the cycle-count list built from the ABC-XYZ
   classification) go to a throwaway scratch dir.
2. **Write** deliverables only if every step that executed passed QA. One
   failing step - required or optional - means nothing is written at all: a
   package is a single coherent deliverable, not a folder of partial results.

Optional steps whose input file is absent (or whose gate says no, e.g. Odoo
without credentials) are *skipped*, recorded in the coverage table, and do not
block the package.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from src import client_profile
from src.deliverable import DEFAULT_BRANDING, Branding

from . import citation_gate
from .knowledge import KnowledgeBase
from .llm import LLMProvider, get_provider, narrative_rewrite
from .registry import ToolRegistry
from .tools import build_default_registry
from .types import (
    STATUS_ERROR,
    STATUS_NEEDS_CLARIFICATION,
    STATUS_NEEDS_DATA,
    STATUS_OK,
    STATUS_QA_FAILED,
    JobRequest,
)

STEP_SKIPPED = "skipped"


@dataclass(frozen=True)
class PackageInput:
    """One intake file the client (or operator) supplies, by well-known name."""

    slot: str            # short id, e.g. "ventas"
    filename: str        # expected file inside the intake dir, e.g. "ventas.csv"
    description: str     # what it is, for the operator and needs_data messages
    columns: str         # human summary of the required columns


@dataclass(frozen=True)
class PackageStep:
    """One tool of the package: which registry tool, fed from which intake slot."""

    tool_key: str
    input_slot: str | None = None          # None => the tool needs no file (e.g. odoo)
    required: bool = True
    cadence: str = "mensual"               # shown in the coverage table
    params: dict = field(default_factory=dict)  # step defaults; caller params win
    # Synthesizes this step's input from earlier reports (dict tool_key -> report).
    # The frame is written to a scratch CSV and fed through tool.prepare unchanged.
    derive: Callable[[dict[str, object]], pd.DataFrame] | None = None
    # Fallback input when the slot file is absent: the step still runs, on a
    # standard template, and the coverage table says so.
    fallback: Callable[[], pd.DataFrame] | None = None
    fallback_note: str = ""
    # Returns "" to run, or a human skip-reason (e.g. no Odoo credentials).
    gate: Callable[[dict], str] | None = None
    # For tools whose real input is a PARAMETER, not a CSV column (e.g.
    # leadership_chain takes params["scores"], not a data_path): given the
    # resolved input file, returns the params overrides to merge in before
    # calling tool.prepare. None => the file is passed through as data_path,
    # unchanged, the normal way.
    params_from_input: Callable[[Path], dict] | None = None
    # For a tool that optionally reads a SECOND file from a DIFFERENT intake
    # slot as a params path (e.g. markdown_liquidation's own data_path is the
    # "stock" slot, but it also optionally reads params["price_history_path"]
    # from the "ventas" slot): {param_key: slot_name}. Resolved against the
    # SAME intake dir as the step's own input, before tool.prepare runs; the
    # param is simply omitted (never a hard error) when that slot's file is
    # absent, matching every other optional-input degrade in this runner.
    extra_input_params: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PackageSpec:
    """A sellable package: fixed scope, fixed inputs, fixed price anchor.

    ``price`` and scope mirror documentation/MONETIZATION_BRIEF.md (the source
    of truth) - if they diverge, fix the brief or this spec in the same PR.
    """

    key: str
    title: str
    price: str
    cadence: str
    audience: str
    inputs: tuple[PackageInput, ...]
    steps: tuple[PackageStep, ...]
    # Deck language (see src/i18n.py) - "title"/"price"/"cadence"/"audience"
    # above are brand/commercial copy and stay as-is regardless; this controls
    # the deck's own generated labels/headers and each step's tool title.
    # PackageSpec instances are shared module-level singletons (one per
    # package, reused for every client) - select a client's language with
    # ``dataclasses.replace(spec, lang="en")`` before calling run_package,
    # never by mutating the shared constant.
    lang: str = "es"

    def input_for(self, slot: str) -> PackageInput:
        match = next((i for i in self.inputs if i.slot == slot), None)
        if match is None:
            raise KeyError(f"package {self.key}: unknown input slot '{slot}'")
        return match

    def tool_keys(self) -> tuple[str, ...]:
        return tuple(s.tool_key for s in self.steps)


@dataclass(frozen=True)
class StepOutcome:
    """What happened to one step - fed to the coverage table of the package deck."""

    tool_key: str
    title: str
    status: str                 # ok | skipped | needs_data | needs_clarification | qa_failed | error
    cadence: str
    source: str                 # where its input came from (file / derived / connector)
    required: bool
    summary: str = ""
    qa_issues: tuple[str, ...] = ()
    messages: tuple[str, ...] = ()
    skip_reason: str = ""
    report: object | None = None
    guided: object | None = None       # GuidedOutcome when the tool ranks options
    citations: tuple[str, ...] = ()


@dataclass(frozen=True)
class PackageResult:
    """The package-level outcome. ``deliverables`` is empty unless status is ok."""

    status: str
    package: str
    client: str
    steps: tuple[StepOutcome, ...]
    deliverables: dict[str, str]
    summary: str
    missing_inputs: tuple[str, ...] = ()
    qa_issues: tuple[str, ...] = ()


def _resolve_input(
    spec: PackageSpec, step: PackageStep, intake: Path | None, scratch: Path,
    reports: dict[str, object],
) -> tuple[str | None, str, str]:
    """Resolve a step's data file: (path or None, source label, note). Raises
    FileNotFoundError with the intake checklist line when a required file is absent."""
    if step.derive is not None:
        frame = step.derive(reports)
        path = scratch / f"derived_{step.tool_key}.csv"
        frame.to_csv(path, index=False)
        return str(path), "derivado de un paso anterior del paquete", ""
    if step.input_slot is None:
        return None, "conector (sin archivo)", ""
    slot = spec.input_for(step.input_slot)
    candidate = (intake / slot.filename) if intake is not None else None
    if candidate is not None and candidate.exists():
        return str(candidate), slot.filename, ""
    if step.fallback is not None:
        frame = step.fallback()
        path = scratch / f"fallback_{step.tool_key}.csv"
        frame.to_csv(path, index=False)
        return str(path), f"plantilla estandar ({slot.filename} no provisto)", step.fallback_note
    raise FileNotFoundError(
        f"{slot.filename} - {slot.description}. Columnas: {slot.columns}"
    )


def missing_required_inputs(spec: PackageSpec, intake_dir: str | Path | None) -> list[str]:
    """The intake checklist lines still unmet - what to ask the client for."""
    intake = Path(intake_dir) if intake_dir is not None else None
    missing: list[str] = []
    for step in spec.steps:
        if not step.required or step.input_slot is None or step.derive or step.fallback:
            continue
        slot = spec.input_for(step.input_slot)
        present = intake is not None and (intake / slot.filename).exists()
        line = f"{slot.filename} - {slot.description}. Columnas: {slot.columns}"
        if not present and line not in missing:
            missing.append(line)
    return missing


def run_package(
    spec: PackageSpec,
    intake_dir: str | Path | None,
    *,
    client: str = "Client",
    params: dict | None = None,
    out_dir: str | Path = "deliverables/paquetes",
    registry: ToolRegistry | None = None,
    provider: LLMProvider | None = None,
    knowledge: KnowledgeBase | None = None,
    clients_root: Path | str | None = client_profile.DEFAULT_CLIENTS_ROOT,
    strict_params: bool = False,
    prepared: str = "",
    branding: Branding | None = None,
) -> PackageResult:
    """Run every step of ``spec`` and, only if all executed steps pass QA, write
    the per-tool deliverables plus the consolidated package deck under
    ``out_dir/<spec.key>``.

    ``branding`` (see ``src/deliverable.py``) resolves explicit call-site
    override > the loaded client's ``profile.branding`` > Linchpin's own
    default - only the CONSOLIDATED package deck carries it (mirrors how
    ``lang`` is scoped; each individual tool's own deck under this package
    keeps rendering Linchpin's default, unchanged - a deliberate, narrow
    integration point, not every deck in the bundle)."""
    registry = registry if registry is not None else build_default_registry()
    provider = provider if provider is not None else get_provider()
    knowledge = knowledge if knowledge is not None else KnowledgeBase()
    params = dict(params or {})
    intake = Path(intake_dir) if intake_dir is not None else None
    out_root = Path(out_dir) / spec.key

    profile, profile_error = _load_profile(client, clients_root)
    if profile_error:
        return _failed(spec, client, STATUS_ERROR, profile_error)

    missing = missing_required_inputs(spec, intake_dir)
    if missing:
        return PackageResult(
            status=STATUS_NEEDS_DATA, package=spec.key, client=client, steps=(),
            deliverables={}, missing_inputs=tuple(missing),
            summary=(f"{spec.title}: faltan {len(missing)} archivo(s) de intake; "
                     "no se escribio ningun entregable."),
        )

    # ---- Phase 1: compute everything; write nothing to out_root. -------------
    outcomes: list[StepOutcome] = []
    reports: dict[str, object] = {}
    with tempfile.TemporaryDirectory(prefix="linchpin_pkg_") as scratch_str:
        scratch = Path(scratch_str)
        for step in spec.steps:
            tool = registry.get(step.tool_key)
            merged = client_profile.merge_params({**step.params, **params}, profile)
            outcome = _run_step(spec, step, tool, intake, scratch, reports,
                                merged, client, provider, knowledge, strict_params)
            outcomes.append(outcome)
            if outcome.status == STATUS_OK:
                reports[step.tool_key] = outcome.report
            elif outcome.status != STEP_SKIPPED and step.required:
                return PackageResult(
                    status=outcome.status, package=spec.key, client=client,
                    steps=tuple(outcomes), deliverables={},
                    qa_issues=tuple(f"{tool.title}: {i}" for i in outcome.qa_issues),
                    summary=(f"{spec.title}: el paso requerido '{tool.title}' fallo "
                             f"({outcome.status}); no se escribio ningun entregable."),
                )

        # ---- Package QA gate: any executed step failing QA blocks everything.
        failed = [o for o in outcomes if o.status == STATUS_QA_FAILED]
        if failed:
            issues = tuple(f"{o.title}: {i}" for o in failed for i in o.qa_issues)
            return PackageResult(
                status=STATUS_QA_FAILED, package=spec.key, client=client,
                steps=tuple(outcomes), deliverables={}, qa_issues=issues,
                summary=(f"{spec.title}: QA fallo en {len(failed)} paso(s); "
                         "no se escribio ningun entregable."),
            )
        errored = [o for o in outcomes if o.status not in (STATUS_OK, STEP_SKIPPED)]
        if errored:
            return PackageResult(
                status=STATUS_ERROR, package=spec.key, client=client,
                steps=tuple(outcomes), deliverables={},
                summary=(f"{spec.title}: {len(errored)} paso(s) opcional(es) fallaron; "
                         "quitalos del intake o corregi el dato - no se escribio "
                         "ningun entregable."),
            )

    # ---- Phase 2: every executed step passed QA - now (and only now) write. --
    from jobs import package_deliverable  # local import: jobs also imports scm_agent-free code

    written: dict[str, str] = {}
    for outcome in outcomes:
        if outcome.status != STATUS_OK:
            continue
        tool = registry.get(outcome.tool_key)
        step_dir = out_root / outcome.tool_key
        files = tool.deliver(outcome.report, step_dir, client)
        if tool.deck is not None:
            deck_options = list(outcome.guided.options) if outcome.guided is not None else []
            deck_files = tool.deck(outcome.report, step_dir, client,
                                   list(outcome.citations), 1.0, deck_options)
            files.update({f"deck_{name}": path for name, path in deck_files.items()})
        written.update({f"{outcome.tool_key}_{name}": str(path) for name, path in files.items()})

    resolved_branding = branding
    if resolved_branding is None:
        resolved_branding = profile.branding if profile is not None and profile.branding is not None else DEFAULT_BRANDING
    deck = package_deliverable.build(spec, outcomes, client=client, prepared=prepared, branding=resolved_branding)
    package_files = deck.write_all(out_root)
    written.update({f"paquete_{name}": str(path) for name, path in package_files.items()})

    executed = sum(1 for o in outcomes if o.status == STATUS_OK)
    skipped = sum(1 for o in outcomes if o.status == STEP_SKIPPED)
    return PackageResult(
        status=STATUS_OK, package=spec.key, client=client, steps=tuple(outcomes),
        deliverables=written,
        summary=(f"{spec.title}: {executed} analisis ejecutados y QA-aprobados"
                 + (f", {skipped} omitidos por falta de insumo" if skipped else "")
                 + f"; {len(written)} archivo(s) entregados."),
    )


def _run_step(
    spec: PackageSpec, step: PackageStep, tool, intake: Path | None, scratch: Path,
    reports: dict[str, object], params: dict, client: str,
    provider: LLMProvider, knowledge: KnowledgeBase, strict_params: bool,
) -> StepOutcome:
    base = dict(tool_key=step.tool_key, title=tool.title, cadence=step.cadence,
                required=step.required)
    if step.gate is not None:
        reason = step.gate(params)
        if reason:
            return StepOutcome(**base, status=STEP_SKIPPED, source="conector",
                               skip_reason=reason)
    try:
        data_path, source, note = _resolve_input(spec, step, intake, scratch, reports)
    except FileNotFoundError as exc:
        # Required-with-file steps were pre-checked; this is an optional step.
        return StepOutcome(**base, status=STEP_SKIPPED, source="(no provisto)",
                           skip_reason=f"falta el archivo: {exc}")
    except Exception as exc:  # a derive over a missing/failed source report
        return StepOutcome(**base, status=STATUS_ERROR, source="derivado",
                           messages=(str(exc),))
    base["source"] = source

    for param_key, slot_name in step.extra_input_params.items():
        slot = spec.input_for(slot_name)
        candidate = (intake / slot.filename) if intake is not None else None
        if candidate is not None and candidate.exists():
            params = {**params, param_key: str(candidate)}

    if step.params_from_input is not None and data_path is not None:
        try:
            params = {**params, **step.params_from_input(Path(data_path))}
        except Exception as exc:
            return StepOutcome(**base, status=STATUS_ERROR, messages=(str(exc),))

    if strict_params and tool.required_client_params:
        missing = [k for k in tool.required_client_params if k not in params]
        if missing:
            return StepOutcome(
                **base, status=STATUS_NEEDS_CLARIFICATION,
                messages=tuple(f"falta el parametro '{k}' del cliente" for k in missing),
            )

    request = JobRequest(brief=f"{spec.title}: {tool.title}", data_path=data_path,
                         job_type=step.tool_key, params=params, client=client)
    try:
        prep = tool.prepare(request, provider)
        if prep.status != STATUS_OK:
            return StepOutcome(**base, status=prep.status, messages=tuple(prep.messages))
        produced = tool.run(prep.payload, params)
        qa_issues = tool.qa(produced.report)
    except Exception as exc:
        return StepOutcome(**base, status=STATUS_ERROR, messages=(str(exc),))
    if qa_issues:
        return StepOutcome(**base, status=STATUS_QA_FAILED, qa_issues=tuple(qa_issues))

    guided = tool.options(produced.report) if tool.options else None
    # Citation-grounding gate (E5): a ranked candidate must resolve to a real
    # graph node within citation_gate.MAX_HOPS of this tool's own anchor
    # concepts before it reaches the deck - see scm_agent/citation_gate.py.
    # This is a content filter, not a QA veto: a step with zero surviving
    # citations still ships (its methodology section just has none), the
    # package's only hard veto stays the data QA gate above.
    candidates = knowledge.ground_citations_detailed(tool.intent_keywords, request.brief, limit=3)
    gate_result = citation_gate.filter_citations(knowledge, step.tool_key, candidates)
    citations = gate_result.kept
    # Optional LLM polish in the package's target language (src/i18n.py's
    # static labels cover the deterministic path around this; this is the
    # only place a package step's own narrative gets translated - see
    # scm_agent.llm.narrative_rewrite). Skipped without a provider, matching
    # the Orchestrator's equivalent single-tool behavior exactly.
    rewritten = narrative_rewrite(
        provider, produced.summary, tool.title, lang=spec.lang, citations=list(citations),
    )
    summary = rewritten + (f" [{note}]" if note else "")
    return StepOutcome(**base, status=STATUS_OK, summary=summary,
                       report=produced.report, guided=guided, citations=citations)


def _load_profile(client: str, clients_root: Path | str | None):
    """Mirror the orchestrator's profile semantics: fill gaps only; corrupt fails loudly."""
    if clients_root is None:
        return None, ""
    try:
        slug = client_profile.slugify_client_id(client)
    except ValueError:
        return None, ""
    try:
        return client_profile.load_profile(slug, root=Path(clients_root)), ""
    except ValueError as exc:
        return None, (f"perfil de cliente corrupto: {exc} - corregi o borra "
                      f"clients/{slug}/profile.json")


def _failed(spec: PackageSpec, client: str, status: str, summary: str) -> PackageResult:
    return PackageResult(status=status, package=spec.key, client=client,
                         steps=(), deliverables={}, summary=summary)
