"""Audit-grade evidence job: a GL export (+ the subledger it should tie to) -> a sampling
plan, a selection to test, a GL tie-out and a lineage record an independent reviewer can
re-perform.

The engine (``src.audit_evidence``) is pure and clock-free by design: timestamps, identities
and file paths enter here, at the job boundary, exactly like ``Deliverable.prepared``. This
job is where they enter, and where the two things the engine cannot know get decided -- what
the auditor supplied, and what nobody supplied.

**What this job deliberately does NOT produce: the bespoke ``workpaper.xlsx``** (sheets W-1
to W-6 with the audit indexing, tickmark legend and independence framing) described in
``documentation/AUDIT_EVIDENCE_DESIGN.md`` section 2. That layout depends on memo question 2
-- whether Linchpin is the audit firm's own tool or management's preparer of IPE-style
support -- which changes the workpaper wording and whose name belongs in the preparer field.
That is a positioning decision for a practising auditor, not a coding task, and writing the
sheet would commit it in code by accident. Until it is answered this job emits the machine
artifacts (selection CSV, exceptions CSV, ``evidence.json``) and the standard deck, and hands
off. Everything here is re-usable under either answer.

Standards anchors, as the engine cites them: AU-C 530 / PCAOB AS 2315 (sampling), AS 1105
(evidence, and .10 for information produced by the entity), AS 1215 (documentation, the
experienced-auditor and re-performability tests).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from nosilo_core.guided import (
    COVERAGE_PENALTY,
    EscalationPacket,
    GuidedOutcome,
    HandoffPacket,
    Residual,
    as_escalation,
    as_handoff,
    confidence_from_coverage,
)

from src.audit_evidence import (
    FORMULA_VERSION,
    EvidenceRecord,
    GlTieOut,
    InputArtifact,
    IpeAttestation,
    MusPlan,
    MusSelection,
    QaCheck,
    evidence_record,
    evidence_to_dict,
    gl_tie_out,
    hash_file,
    ipe_attestation,
    mus_plan,
    mus_select,
)
from src.deliverable import DataSource, Deliverable, Finding, Kpi, not_measured
from src.export import write_summary_csv

_KEY_COLS = ("account", "acct", "gl_account", "account_code", "sku", "product_id", "item", "Account")
_AMOUNT_COLS = ("amount", "balance", "value", "amt", "book_value", "extended_value", "Amount")

# ── Coverage gaps this job can have, registered from Kern's side ────────────────────────
# `confidence_from_coverage` raises KeyError on a gap name it does not know, on purpose, and
# its table lives in nosilo_core -- which ships here as a built dependency with no source
# checkout on the machines that build Kern. `src/mcdm.py` already established the pattern for
# this exact situation, including why `setdefault` and not assignment: if nosilo_core ever
# ships the gap itself, upstream wins and this line becomes a no-op.
#
# Both penalties are 0.25, level with `value_ranks_on_units`, because both describe a
# conclusion resting on a comparison that never happened -- the most expensive kind of hole
# in this vertical, where the buyer's own reviewer will look for it first.
UNTIED_IPE = "untied_ipe"
COVERAGE_PENALTY.setdefault(UNTIED_IPE, 0.25)

NO_SUBLEDGER = "no_subledger_tie_out"
COVERAGE_PENALTY.setdefault(NO_SUBLEDGER, 0.25)

# The MUS planning size with a non-zero expected misstatement uses the expansion-factor
# method, whose factors the engine itself marks PROVISIONAL pending the pinned AICPA guide
# edition (memo Q1). A plan built on them is usable, but it is not yet table-anchored.
PROVISIONAL_FACTORS = "provisional_expansion_factors"
COVERAGE_PENALTY.setdefault(PROVISIONAL_FACTORS, 0.15)

#: The deck's ceiling before coverage gaps are subtracted. Audit evidence is never a
#: finished product on its own -- the fieldwork and the conclusion are the auditor's -- so it
#: starts below 1.0 even when every comparison was performed.
_DECK_BASE_CONFIDENCE = 0.90

_RESIDUAL = Residual(
    description=(
        "An auditor of record must test the selected items, key the audited values, evaluate "
        "every exception and sign the workpaper. Nothing here is audit evidence until they do."
    ),
    risk_if_skipped=(
        "An unsigned, un-fieldworked selection is a plan, not evidence (AS 1215)."
    ),
)


@dataclass(frozen=True)
class AuditEvidenceReport:
    plan: MusPlan
    selection: MusSelection
    ipe: IpeAttestation
    tie_out: GlTieOut | None
    evidence: EvidenceRecord
    outcome: GuidedOutcome
    coverage_gaps: tuple[str, ...]
    population_value: float
    n_population: int
    summary: str


def _pick_column(df: pd.DataFrame, override: object, candidates: tuple[str, ...]) -> str | None:
    if override:
        return str(override) if str(override) in df.columns else None
    return next((c for c in candidates if c in df.columns), None)


def _read(path: str | Path, params: dict, *, label: str) -> tuple[list[dict], InputArtifact]:
    """Read one CSV, sniff its key/amount columns, and fingerprint it at the file boundary."""
    df = pd.read_csv(path)
    key = _pick_column(df, params.get("key_col"), _KEY_COLS)
    amount = _pick_column(df, params.get("amount_col"), _AMOUNT_COLS)
    missing = [n for n, c in (("key", key), ("amount", amount)) if c is None]
    if missing:
        raise ValueError(
            f"{label}: could not find {', '.join(missing)}; pass key_col/amount_col in params "
            f"(columns seen: {list(df.columns)[:10]})"
        )
    rows = [
        {"key": str(r[key]), "amount": float(r[amount])}
        for _, r in df.iterrows()
        if pd.notna(r[key]) and pd.notna(r[amount])
    ]
    if not rows:
        raise ValueError(f"{label}: no usable rows (need a key and a finite amount)")
    artifact = InputArtifact(
        path_label=Path(path).name,
        sha256=hash_file(path),
        n_rows=len(rows),
        columns=tuple(str(c) for c in df.columns),
        control_total=sum(r["amount"] for r in rows),
    )
    return rows, artifact


def prepare(data_path: str, params: dict | None = None) -> dict:
    """Read the population (and optionally the GL it should tie to), hashing both here.

    ``tolerable_misstatement`` is required and is never defaulted. It is the auditor's
    materiality judgement; deriving it would put the engine in the auditor's chair, and a
    default nobody supplied is not a measurement -- the rule
    ``tests/test_unsupplied_inputs_are_not_measurements.py`` already enforces elsewhere in
    this repo. Without it the tool asks instead of guessing.
    """
    params = params or {}
    if not data_path:
        raise ValueError("a population CSV (key, amount) is required")

    tm = params.get("tolerable_misstatement")
    if tm in (None, ""):
        raise ValueError(
            "tolerable_misstatement is required and is never defaulted: it is the auditor's "
            "materiality judgement, not something this engine may derive. Pass it in params."
        )
    try:
        tm = float(tm)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"tolerable_misstatement must be a number: {exc}") from exc
    if tm <= 0:
        raise ValueError("tolerable_misstatement must be > 0")

    population, pop_artifact = _read(data_path, params, label="population")
    artifacts = [pop_artifact]

    gl_rows = None
    gl_path = params.get("gl_path")
    if gl_path:
        gl_rows, gl_artifact = _read(gl_path, params, label="gl")
        artifacts.append(gl_artifact)

    return {
        "population": population,
        "gl": gl_rows,
        "input_artifacts": artifacts,
        "tolerable_misstatement": tm,
    }


def run(
    payload: dict,
    *,
    risk_of_incorrect_acceptance: float = 0.05,
    expected_misstatement: float = 0.0,
    tolerance: float = 0.0,
    random_start: float | None = None,
    expected_total: float | None = None,
    run_id: str = "",
    produced_at: str = "",
) -> AuditEvidenceReport:
    """Plan the sample, select it, tie out to the GL if one was supplied, and record lineage.

    ``run_id`` and ``produced_at`` are caller-supplied so the run is exactly re-performable
    (AS 1215); the adapter in ``scm_agent/tools.py`` mints them at the orchestrator boundary.
    ``random_start`` likewise: the selection must be reproducible from the record alone, so a
    caller that does not pin it gets a deterministic 0.0 rather than an unrecorded draw.
    """
    population = payload["population"]
    gl = payload.get("gl")
    tm = payload["tolerable_misstatement"]
    artifacts = payload["input_artifacts"]

    population_value = sum(r["amount"] for r in population if r["amount"] > 0)
    if population_value <= 0:
        raise ValueError("population has no positive book value to sample")

    plan = mus_plan(
        population_value=population_value,
        tolerable_misstatement=tm,
        expected_misstatement=expected_misstatement,
        risk_of_incorrect_acceptance=risk_of_incorrect_acceptance,
    )
    start = 0.0 if random_start is None else float(random_start)
    selection = mus_select([(r["key"], r["amount"]) for r in population], plan, random_start=start)

    ipe = ipe_attestation(
        population,
        source_label=artifacts[0].path_label,
        amount_field="amount",
        tied_to="GL control account" if gl else "not tied",
        expected_total=expected_total,
        tolerance=tolerance,
    )

    tie_out = None
    if gl is not None:
        tie_out = gl_tie_out(
            gl, population, key_field="key", amount_field="amount", tolerance=tolerance,
        )

    # ── the coverage the deliverable actually has, named ────────────────────────────────
    gaps: list[str] = []
    if not ipe.tied:
        gaps.append(UNTIED_IPE)
    if tie_out is None:
        gaps.append(NO_SUBLEDGER)
    if expected_misstatement > 0:
        gaps.append(PROVISIONAL_FACTORS)
    confidence = confidence_from_coverage(gaps, base=_DECK_BASE_CONFIDENCE)

    # A positive attestation: what was checked, and what it was checked against. The
    # orchestrator's QA records only failures; a workpaper has to show the review performed.
    checks = (
        QaCheck(
            name="ipe_completeness_accuracy",
            compared=(
                f"listing control total {ipe.control_total:,.2f} vs supplied "
                f"{expected_total:,.2f}" if ipe.tied
                else "NOT PERFORMED: no independent total was supplied (AS 1105.10)"
            ),
            passed=ipe.within_tolerance,
        ),
        QaCheck(
            name="gl_tie_out",
            compared=(
                f"GL {tie_out.gl_total:,.2f} vs subledger {tie_out.subledger_total:,.2f}"
                if tie_out is not None
                else "NOT PERFORMED: no GL export was supplied"
            ),
            passed=bool(tie_out is not None and tie_out.within_tolerance),
        ),
        QaCheck(
            name="sampling_interval_covers_population",
            compared=f"interval {plan.sampling_interval:,.2f} x n {plan.sample_size}",
            passed=abs(plan.sampling_interval * plan.sample_size - population_value) <= 0.01,
        ),
    )

    unexplained = None if tie_out is None else tie_out.unexplained_value
    summary = (
        f"MUS plan over {len(population)} line(s) worth {population_value:,.0f}: sample "
        f"{plan.sample_size} at an interval of {plan.sampling_interval:,.0f}, "
        f"{len(selection.selected)} item(s) selected "
        f"({sum(1 for i in selection.selected if i.is_top_stratum)} top-stratum). "
        + (
            f"GL tie-out leaves {unexplained:,.0f} unexplained."
            if tie_out is not None
            else "No GL export supplied, so nothing was tied out."
        )
    )

    payload_for_hash = {
        "plan": {"n": plan.sample_size, "interval": plan.sampling_interval, "tm": tm},
        "selected": [i.unit_id for i in selection.selected],
        "tie_out": None if tie_out is None else {
            "difference": tie_out.difference, "unexplained": tie_out.unexplained_value,
        },
    }
    evidence = evidence_record_for(
        run_id=run_id,
        artifacts=artifacts,
        params_used={
            "tolerable_misstatement": tm,
            "risk_of_incorrect_acceptance": risk_of_incorrect_acceptance,
            "expected_misstatement": expected_misstatement,
            "tolerance": tolerance,
            "random_start": start,
            "expected_total": expected_total,
        },
        checks=checks,
        output_payload=payload_for_hash,
        produced_at=produced_at,
    )

    outcome = _outcome(plan, selection, tie_out, ipe, gaps, confidence, summary)
    return AuditEvidenceReport(
        plan=plan,
        selection=selection,
        ipe=ipe,
        tie_out=tie_out,
        evidence=evidence,
        outcome=outcome,
        coverage_gaps=tuple(gaps),
        population_value=population_value,
        n_population=len(population),
        summary=summary,
    )


def evidence_record_for(
    *, run_id: str, artifacts, params_used: dict, checks, output_payload, produced_at: str
) -> EvidenceRecord:
    """Thin seam over the engine's ``evidence_record`` so the job's callers cannot forget the
    formula-version tuple (the thing a reviewer needs to re-derive the number years later)."""
    return evidence_record(
        run_id=run_id,
        inputs=artifacts,
        params_used=params_used,
        qa_attestation=checks,
        output_payload=output_payload,
        produced_at=produced_at,
        formula_versions=(("audit_evidence", FORMULA_VERSION),),
    )


def _outcome(plan, selection, tie_out, ipe, gaps, confidence, summary) -> GuidedOutcome:
    """HANDOFF by default, ESCALATED on an adverse finding, and never EXECUTED.

    Decided in ``AUDIT_EVIDENCE_MEMO.md`` section 6 and restated as contract in the design
    doc: only the auditor of record can conclude, sign and file a workpaper, so emitting one
    autonomously would put the engine in the auditor's chair. There is no ``as_executed``
    branch in this function -- ``verify`` rejects the status as well, and
    ``jobs.qa.audit_evidence_gate`` a third time, because a single guard is one edit away
    from being removed by someone who does not know why it is there.
    """
    adverse: list[str] = []
    if tie_out is not None and not tie_out.within_tolerance:
        adverse.append(
            f"GL tie-out leaves {tie_out.unexplained_value:,.2f} unexplained, outside the "
            f"{tie_out.tolerance:,.2f} tolerance"
        )
    if ipe.tied and not ipe.within_tolerance:
        adverse.append(
            f"the listing does not agree to the independent total (difference "
            f"{ipe.tie_difference:,.2f})"
        )

    if adverse:
        return as_escalation(
            summary,
            EscalationPacket(
                reason="; ".join(adverse),
                route_to="engagement partner / concurring reviewer",
                recommendation=(
                    "Resolve the reconciling difference before any sample is tested: a "
                    "selection drawn from a listing that does not tie is not evidence about "
                    "the population the financial statements assert."
                ),
                citations=["PCAOB AS 1105.10", "PCAOB AS 2315", "AU-C 530"],
                sla="before fieldwork begins",
            ),
            confidence=confidence,
            residuals=[_RESIDUAL],
        )

    steps = [
        f"Test the {len(selection.selected)} selected item(s) in sampling_selection.csv "
        f"({sum(1 for i in selection.selected if i.is_top_stratum)} are top-stratum and are "
        "examined 100%).",
        "Key the audited value for each item, then evaluate with src.audit_evidence.mus_evaluate.",
        "Investigate the nature and cause of every difference found (AU-C 530.12-.13).",
        "Conclude and sign; the preparer and reviewer lines are deliberately blank.",
    ]
    if not ipe.tied:
        steps.insert(
            0,
            "FIRST: tie the listing to an independent control total and re-run with "
            "expected_total -- no completeness/accuracy test was performed (AS 1105.10).",
        )
    if tie_out is None:
        steps.insert(
            0,
            "FIRST: supply the GL export via params['gl_path'] -- nothing was tied out.",
        )
    return as_handoff(
        summary,
        [HandoffPacket(
            title="Perform fieldwork on the selection and sign the workpaper",
            steps=steps,
            artifact="sampling_selection.csv",
            data={"sample_size": plan.sample_size, "coverage_gaps": list(gaps)},
            deadline="within the documentation assembly window (AS 1215: 14 days from Dec 2026)",
            risk_if_skipped=(
                "The selection is a plan, not audit evidence, until an auditor of record "
                "tests it, concludes and signs (AS 1215)."
            ),
        )],
        confidence=confidence,
        residuals=[_RESIDUAL],
    )


def verify(report: AuditEvidenceReport) -> list[str]:
    """QA gate: the never-unprotected contract, the arithmetic identities, and the lineage.

    A failure here means the orchestrator produces no deliverable at all (its single
    "QA fails => no deliverable" gate), which is the mechanism that makes the never-EXECUTED
    rule real rather than documented.
    """
    from jobs.qa import audit_evidence_gate

    issues = audit_evidence_gate(report.outcome)

    if report.plan.sample_size < 1:
        issues.append("sample size is below 1")
    if not report.selection.selected:
        issues.append("the plan selected no items")

    # Every item at or above the interval must be in the selection: MUS certainty items are
    # examined 100%, and silently dropping one is how a sample stops covering the top stratum.
    interval = report.plan.sampling_interval
    selected_ids = {i.unit_id for i in report.selection.selected}
    top_ids = {i.unit_id for i in report.selection.selected if i.is_top_stratum}
    if not top_ids <= selected_ids:
        issues.append("a top-stratum item is missing from the selection")
    if abs(interval * report.plan.sample_size - report.population_value) > 0.01:
        issues.append("sampling interval x sample size does not reconstruct the population value")

    t = report.tie_out
    if t is not None:
        identity = t.gl_total - t.subledger_total
        reconciled = sum(i.amount for i in t.reconciling_items) + t.unexplained_value
        if abs(identity - reconciled) > 0.01:
            issues.append(
                "tie-out identity broken: gl - subledger != reconciling items + unexplained"
            )

    ev = report.evidence
    if not ev.inputs or any(not a.sha256 for a in ev.inputs):
        issues.append("evidence record has an input without a SHA-256 fingerprint")
    if not ev.params_used:
        issues.append("evidence record does not echo the params actually used")
    if not ev.formula_versions:
        issues.append("evidence record carries no formula version")
    if not ev.produced_at or not ev.run_id:
        issues.append("evidence record is missing run_id or produced_at")
    if not ev.qa_attestation:
        issues.append("evidence record carries no positive QA attestation")

    return issues


def write_operational(
    report: AuditEvidenceReport, out_dir: str | Path, client: str = "Client"
) -> dict[str, Path]:
    """The machine artifacts. The bespoke audit workpaper is NOT among them -- see module doc."""
    import json

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}

    # The auditor's fieldwork sheet: audited_value / tickmark / exception are deliberately
    # blank. That emptiness is the handoff made visible.
    paths["sampling_selection"] = write_summary_csv(
        [
            {
                "unit_id": i.unit_id,
                "book_value": round(i.book_value, 2),
                "is_top_stratum": "yes" if i.is_top_stratum else "no",
                "audited_value": "",
                "tickmark": "",
                "exception": "",
            }
            for i in report.selection.selected
        ],
        out / "sampling_selection.csv",
    )

    t = report.tie_out
    rows: list[dict] = []
    if t is not None:
        rows += [
            {"key": line.key, "amount": round(line.amount, 2), "side": "gl only"}
            for line in t.unmatched_gl
        ]
        rows += [
            {"key": line.key, "amount": round(line.amount, 2), "side": "subledger only"}
            for line in t.unmatched_subledger
        ]
    if not rows:
        # An empty frame would write a headerless file; keep the columns so a reviewer can
        # tell "tied with no exceptions" from "this file was never produced".
        rows = [{"key": "", "amount": "", "side": "no exceptions"}]
    paths["gl_exceptions"] = write_summary_csv(rows, out / "gl_exceptions.csv")

    evidence_path = out / "evidence.json"
    evidence_path.write_text(
        json.dumps(evidence_to_dict(report.evidence), indent=2, default=str), encoding="utf-8"
    )
    paths["evidence"] = evidence_path
    return paths


def build_deck(
    report: AuditEvidenceReport,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple[str, ...] = (),
    confidence: float | None = None,
) -> Deliverable:
    """Compose the client-facing summary. Confidence comes from the coverage, not the caller.

    The caller's value is a ceiling, never an override: ``scm_agent`` passes a flat number in
    and cannot know which comparisons this particular run was able to perform. A deck built
    on a listing nobody tied to a control total must not print the same confidence as one
    built on a listing that tied -- that is the defect ``fix/confianza-refleja-cobertura``
    was opened for in the sourcing job, and it is worse here, because the buyer of this
    output has a reviewer whose job is to find exactly that.
    """
    derived = confidence_from_coverage(report.coverage_gaps, base=_DECK_BASE_CONFIDENCE)
    effective = derived if confidence is None else min(confidence, derived)

    plan, sel, t, ipe = report.plan, report.selection, report.tie_out, report.ipe
    n_top = sum(1 for i in sel.selected if i.is_top_stratum)

    findings = [
        Finding(
            "Sampling plan and what it covers",
            f"{plan.sample_size} sampling unit(s) at an interval of "
            f"{plan.sampling_interval:,.0f} over a population of "
            f"{report.population_value:,.0f}; {len(sel.selected)} logical item(s) selected, "
            f"{n_top} of them top-stratum and examined 100%. {plan.formula_ref}.",
            impact="the sample size is defensible arithmetic, not a firm rule of thumb",
        ),
        Finding(
            "Items outside the sample by construction",
            f"{sel.excluded_zero_or_negative} zero or negative balance(s) had no chance of "
            "selection. MUS cannot reach them and is weak at detecting understatement.",
            impact="test these as a separate population or the coverage claim is overstated",
        ),
    ]

    if ipe.tied:
        findings.append(Finding(
            "Completeness and accuracy of the listing (AS 1105.10)",
            f"{ipe.row_count} row(s), control total {ipe.control_total:,.2f}, difference to "
            f"the supplied independent total {ipe.tie_difference:,.2f} -- "
            f"{'within' if ipe.within_tolerance else 'OUTSIDE'} tolerance.",
            impact="a sample drawn from an untested listing carries the listing's defects",
        ))
    else:
        findings.append(Finding(
            "Completeness and accuracy of the listing: not tested",
            f"{ipe.row_count} row(s) totalling {ipe.control_total:,.2f} were read, but no "
            "independent figure was supplied to tie them to, so the AS 1105.10 test was not "
            "performed. This is not a pass.",
            impact="supply a control total and re-run before relying on any number here",
        ))

    if t is not None:
        findings.append(Finding(
            "GL tie-out",
            f"GL {t.gl_total:,.2f} vs subledger {t.subledger_total:,.2f}; difference "
            f"{t.difference:,.2f}, {t.unexplained_value:,.2f} unexplained after "
            f"{len(t.reconciling_items)} documented reconciling item(s). "
            f"{len(t.unmatched_gl)} line(s) only in the GL, "
            f"{len(t.unmatched_subledger)} only in the subledger.",
            impact="an unexplained difference is an escalation, not a footnote",
        ))
    else:
        findings.append(Finding(
            "GL tie-out: not performed",
            "No GL export was supplied, so the population was never agreed to a control "
            "account.",
            impact="the sample describes the listing, not the balance the statements assert",
        ))

    kpis = (
        Kpi("Sample size", f"{plan.sample_size}", rationale="MUS: PV x RF0 / TM"),
        Kpi("Sampling interval", f"{plan.sampling_interval:,.0f}",
            rationale="every item at or above this is examined 100%"),
        Kpi("Tolerable misstatement", f"{plan.tolerable_misstatement:,.0f}",
            rationale="supplied by the auditor; never derived here"),
        Kpi("Unexplained difference",
            not_measured() if t is None else f"{t.unexplained_value:,.0f}",
            target="minimize", rationale="what the tie-out could not account for"),
    )

    # Auto-derived from the fingerprints, rather than hand-typed: the point of the lineage
    # record is that the provenance on the face of the deliverable cannot drift from the
    # bytes that were actually read.
    data_sources = tuple(
        DataSource(
            field=a.path_label,
            source=f"SHA-256 {a.sha256[:12]}..., {a.n_rows} row(s)",
            cadence="as supplied for this engagement",
        )
        for a in report.evidence.inputs
    )

    recommendations = [
        "Test the selected items and key the audited values, then evaluate to an upper "
        "misstatement limit before concluding.",
        "Investigate the nature and cause of every difference; do not net them.",
    ]
    if not ipe.tied:
        recommendations.insert(0, "Tie the listing to a control total and re-run.")
    if t is None:
        recommendations.insert(0, "Supply the GL export so the population can be tied out.")

    return Deliverable(
        title="Audit-Grade Evidence: sampling plan, GL tie-out and lineage",
        client=client,
        summary=report.summary,
        findings=tuple(findings),
        kpis=kpis,
        data_sources=data_sources,
        recommendations=tuple(recommendations),
        citations=tuple(citations) or (
            "AU-C 530 / PCAOB AS 2315 (audit sampling)",
            "PCAOB AS 1105 (audit evidence; .10 information produced by the entity)",
            "PCAOB AS 1215 (audit documentation; re-performability)",
        ),
        confidence=effective,
        residual=_RESIDUAL.description,
        prepared=prepared,
    )
