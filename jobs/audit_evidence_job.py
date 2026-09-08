"""Audit-evidence playbook: MUS sampling + GL tie-out + IPE + EvidenceRecord.

Job-layer only (clock, identity, file hashes). The engine in ``src/audit_evidence.py``
stays clock-free. Never EXECUTED: the workpaper is not audit evidence until an
auditor of record concludes and signs (AS 1215). ``prepared_by`` is always the
engine identity -- human sign-off lines stay blank by design (the HANDOFF).
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from nosilo_core.escalation import FINANCIAL, escalate
from nosilo_core.guided import (
    EXECUTED,
    HandoffPacket,
    Residual,
    as_handoff,
    confidence_from_coverage,
    verify_guided,
)

from src.audit_evidence import (
    FORMULA_VERSION,
    EvidenceRecord,
    GlTieOut,
    InputArtifact,
    IpeAttestation,
    MusEvaluation,
    MusPlan,
    MusSelection,
    QaCheck,
    evidence_record,
    evidence_to_dict,
    gl_tie_out,
    hash_file,
    ipe_attestation,
    mus_evaluate,
    mus_plan,
    mus_select,
)
from src.deliverable import DataSource, Deliverable, Finding, Kpi, money
from src.export import write_summary_csv
from src.sanitize import defuse_formula

_DECK_BASE_CONFIDENCE = 0.85

ENGINE_IDENTITY = f"linchpin/audit_evidence v{FORMULA_VERSION} (engine)"

_ID_COLS = ("unit_id", "sku", "product_id", "account", "item", "id", "SKU", "Item")
_AMOUNT_COLS = (
    "book_value", "amount", "balance", "value", "inventory_value", "debit", "credit",
)
_SOURCE_COLS = ("source", "ledger", "book", "kind", "dataset")
_AUDITED_COLS = ("audited_value", "audited", "audit_value")

_GL_SOURCE = frozenset({"gl", "general_ledger", "trial_balance", "tb", "libro_mayor"})
_SUB_SOURCE = frozenset({
    "subledger", "sub", "inventory", "population", "listing", "subledger_inventory",
})


@dataclass(frozen=True)
class PreparedEvidence:
    """Hashed inputs + sniffed rows. ``run()`` stays free of file I/O."""

    gl_rows: tuple[dict, ...]
    population_rows: tuple[dict, ...]
    artifacts: tuple[InputArtifact, ...]
    id_field: str
    amount_field: str
    has_tie_out: bool
    audited_by_id: dict[str, float]


@dataclass(frozen=True)
class AuditEvidenceReport:
    plan: MusPlan
    selection: MusSelection
    tie_out: GlTieOut | None
    ipe: IpeAttestation
    evaluation: MusEvaluation | None
    evidence: EvidenceRecord
    outcome: object
    summary: str
    fieldwork_pending: bool
    selection_seed: int
    random_start: float


def _pick_column(df: pd.DataFrame, override: object, candidates: tuple[str, ...]) -> str | None:
    if override:
        name = str(override)
        return name if name in df.columns else None
    return next((c for c in candidates if c in df.columns), None)


def _finite_amount(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _source_kind(value: object) -> str | None:
    text = str(value or "").strip().lower().replace(" ", "_")
    if text in _GL_SOURCE:
        return "gl"
    if text in _SUB_SOURCE:
        return "subledger"
    return None


def _rows_from_frame(df: pd.DataFrame, id_col: str, amount_col: str) -> list[dict]:
    rows: list[dict] = []
    for _, row in df.iterrows():
        unit_id = str(row[id_col]).strip()
        amount = _finite_amount(row[amount_col])
        if not unit_id or unit_id.lower() in {"nan", "none"} or amount is None:
            continue
        rows.append({"unit_id": unit_id, "book_value": amount})
    return rows


def _artifact(path: str | Path, rows: list[dict], amount_field: str) -> InputArtifact:
    p = Path(path)
    return InputArtifact(
        path_label=p.name,
        sha256=hash_file(p),
        n_rows=len(rows),
        columns=tuple(rows[0].keys()) if rows else (),
        control_total=sum(r["book_value"] for r in rows) if rows else 0.0,
    )


def _read_table(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"{path} does not exist")
    return pd.read_excel(p) if p.suffix.lower() in {".xlsx", ".xls"} else pd.read_csv(p)


def _audited_map(df: pd.DataFrame, id_col: str, audited_col: str | None) -> dict[str, float]:
    if not audited_col:
        return {}
    out: dict[str, float] = {}
    for _, row in df.iterrows():
        amount = _finite_amount(row[audited_col])
        if amount is None:
            continue
        out[str(row[id_col]).strip()] = amount
    return out


def prepare(data_path: str, params: dict | None = None) -> PreparedEvidence:
    """Read the GL/population CSV, hash it, optionally split gl vs subledger.

    MCP cannot pass ``subledger_path`` (``*_path`` is stripped). Pass both ledgers
    in one table with a ``source`` column (``gl`` / ``subledger``), or send a
    single population for MUS without tie-out.
    """
    params = params or {}
    df = _read_table(data_path)
    id_col = _pick_column(df, params.get("id_col"), _ID_COLS)
    amount_col = _pick_column(df, params.get("amount_col"), _AMOUNT_COLS)
    missing = [n for n, c in (("unit_id", id_col), ("book_value", amount_col)) if c is None]
    if missing:
        cols = list(df.columns)[:12]
        raise ValueError(
            f"could not find {', '.join(missing)}; pass id_col/amount_col "
            f"(columns seen: {cols})"
        )

    source_col = _pick_column(df, params.get("source_col"), _SOURCE_COLS)
    audited_col = _pick_column(df, params.get("audited_col"), _AUDITED_COLS)
    audited_by_id = _audited_map(df, id_col, audited_col)

    gl_rows: list[dict] = []
    pop_rows: list[dict] = []
    if source_col:
        gl_df = df[df[source_col].map(_source_kind) == "gl"]
        sub_df = df[df[source_col].map(_source_kind) == "subledger"]
        gl_rows = _rows_from_frame(gl_df, id_col, amount_col)
        pop_rows = _rows_from_frame(sub_df, id_col, amount_col)
        if not gl_rows and not pop_rows:
            raise ValueError(
                "source column present but no rows tagged gl or subledger "
                "(accepted: gl, subledger, inventory, population, listing)"
            )
        if not pop_rows:
            pop_rows = gl_rows
            gl_rows = []
    else:
        pop_rows = _rows_from_frame(df, id_col, amount_col)

    subledger_path = params.get("subledger_path")
    artifacts = [_artifact(data_path, pop_rows if not gl_rows else gl_rows + pop_rows, amount_col)]
    if subledger_path:
        sub_df = _read_table(subledger_path)
        sub_id = _pick_column(sub_df, params.get("id_col"), _ID_COLS) or id_col
        sub_amt = _pick_column(sub_df, params.get("amount_col"), _AMOUNT_COLS) or amount_col
        if sub_id not in sub_df.columns or sub_amt not in sub_df.columns:
            raise ValueError("subledger file is missing unit_id/book_value columns")
        pop_rows = _rows_from_frame(sub_df, sub_id, sub_amt)
        if not gl_rows:
            gl_rows = _rows_from_frame(df, id_col, amount_col)
        artifacts.append(_artifact(subledger_path, pop_rows, sub_amt))
        extra_audited = _audited_map(
            sub_df, sub_id, _pick_column(sub_df, params.get("audited_col"), _AUDITED_COLS)
        )
        audited_by_id = {**audited_by_id, **extra_audited}

    if not pop_rows:
        raise ValueError("no usable population rows (need unit_id + book_value)")

    has_tie_out = bool(gl_rows) and bool(pop_rows)
    return PreparedEvidence(
        gl_rows=tuple(gl_rows),
        population_rows=tuple(pop_rows),
        artifacts=tuple(artifacts),
        id_field="unit_id",
        amount_field="book_value",
        has_tie_out=has_tie_out,
        audited_by_id=audited_by_id,
    )


def validate_params(params: dict | None) -> None:
    """Validate TM (and optional knobs) when present. Absence is checked in ``run()``.

    Called from the tool adapter so a malformed TM becomes ``needs_clarification``
    instead of the orchestrator's generic internal error. Missing TM is still
    required at ``run()`` -- the data-contract prepare() test does not pass params.
    """
    params = params or {}
    if "tolerable_misstatement" in params:
        tm = _parse_positive(params, "tolerable_misstatement")
        if tm <= 0:
            raise ValueError("tolerable_misstatement must be > 0")
    for key in ("risk_of_incorrect_acceptance", "expected_misstatement", "tolerance"):
        if key in params:
            _parse_positive(params, key, default=0.0)
    if "selection_seed" in params:
        try:
            int(params["selection_seed"])
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"params['selection_seed'] must be an integer, got {params['selection_seed']!r}"
            ) from exc


def _parse_positive(params: dict, key: str, default: float | None = None) -> float:
    if key not in params:
        if default is None:
            raise ValueError(
                f"params[{key!r}] is required (auditor judgment; Kern never derives it)"
            )
        return float(default)
    try:
        value = float(params[key])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"params[{key!r}] must be a number, got {params[key]!r}") from exc
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"params[{key!r}] must be a finite number >= 0")
    return value


def _random_start(seed: int, interval: float) -> float:
    digest = hashlib.sha256(str(int(seed)).encode("utf-8")).digest()
    unit = int.from_bytes(digest[:8], "big") / 2**64
    return unit * interval


def _qa_checks(
    plan: MusPlan,
    selection: MusSelection,
    tie_out: GlTieOut | None,
    evidence: EvidenceRecord,
    fieldwork_pending: bool,
) -> tuple[QaCheck, ...]:
    pv_ok = abs(plan.sampling_interval * plan.sample_size - plan.population_value) <= max(
        1e-6, abs(plan.population_value) * 1e-9
    )
    checks = [
        QaCheck("sample_size_positive", f"n={plan.sample_size}", plan.sample_size >= 1),
        QaCheck(
            "interval_covers_population",
            f"interval*n={plan.sampling_interval * plan.sample_size} vs PV={plan.population_value}",
            pv_ok,
        ),
        QaCheck(
            "top_stratum_selected",
            "every item >= interval is in the selection",
            all(
                item.unit_id in {s.unit_id for s in selection.selected}
                for item in selection.selected
                if item.is_top_stratum
            ),
        ),
        QaCheck("evidence_has_inputs", f"n_inputs={len(evidence.inputs)}", bool(evidence.inputs)),
        QaCheck(
            "prepared_by_is_engine",
            evidence.prepared_by,
            "linchpin/audit_evidence" in evidence.prepared_by and "engine" in evidence.prepared_by,
        ),
        QaCheck("fieldwork_pending_declared", str(fieldwork_pending), True),
    ]
    if tie_out is not None:
        identity = (tie_out.gl_total - tie_out.subledger_total) - (
            sum(it.amount for it in tie_out.reconciling_items) + tie_out.unexplained_value
        )
        checks.append(QaCheck(
            "tie_out_identity",
            f"gl-subledger - reconciling - unexplained = {identity}",
            abs(identity) <= max(1e-6, tie_out.tolerance, abs(tie_out.difference) * 1e-9),
        ))
    return tuple(checks)


def run(payload: PreparedEvidence, params: dict | None = None) -> AuditEvidenceReport:
    """mus_plan -> mus_select -> optional gl_tie_out -> ipe -> evidence_record."""
    params = params or {}
    tm = _parse_positive(params, "tolerable_misstatement")
    if tm <= 0:
        raise ValueError("tolerable_misstatement must be > 0")
    ria = float(params.get("risk_of_incorrect_acceptance", 0.05))
    expected = float(params.get("expected_misstatement", 0.0))
    tolerance = float(params.get("tolerance", 0.0))
    seed = int(params.get("selection_seed", 0))
    produced_at = str(params.get("produced_at") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    run_id = str(params.get("run_id") or uuid.uuid4())

    pv = sum(r["book_value"] for r in payload.population_rows if r["book_value"] > 0)
    if pv <= 0:
        raise ValueError("population_value must be > 0 (all book values were zero or negative)")

    plan = mus_plan(
        population_value=pv,
        tolerable_misstatement=tm,
        expected_misstatement=expected,
        risk_of_incorrect_acceptance=ria,
    )
    random_start = _random_start(seed, plan.sampling_interval)
    items = [(r["unit_id"], r["book_value"]) for r in payload.population_rows]
    selection = mus_select(items, plan, random_start=random_start)

    tie_out = None
    if payload.has_tie_out:
        tie_out = gl_tie_out(
            list(payload.gl_rows),
            list(payload.population_rows),
            key_field="unit_id",
            amount_field="book_value",
            tolerance=tolerance,
        )

    ipe = ipe_attestation(
        list(payload.population_rows),
        source_label=payload.artifacts[-1].path_label,
        amount_field="book_value",
        tied_to="GL control" if tie_out is not None else "population listing (no GL side)",
        expected_total=tie_out.gl_total if tie_out is not None else None,
        tolerance=tolerance,
    )

    audited_pairs: list[tuple[str, float, float]] = []
    for item in selection.selected:
        if item.unit_id in payload.audited_by_id:
            audited_pairs.append(
                (item.unit_id, item.book_value, payload.audited_by_id[item.unit_id])
            )
    fieldwork_pending = len(audited_pairs) < len(selection.selected)
    evaluation = mus_evaluate(plan, audited_pairs) if audited_pairs and not fieldwork_pending else None

    used = {
        "tolerable_misstatement": tm,
        "risk_of_incorrect_acceptance": ria,
        "expected_misstatement": expected,
        "tolerance": tolerance,
        "selection_seed": seed,
        "random_start": random_start,
        "has_tie_out": payload.has_tie_out,
        "fieldwork_pending": fieldwork_pending,
    }
    payload_for_hash = {
        "plan": asdict(plan),
        "selection": [
            {"unit_id": s.unit_id, "book_value": s.book_value, "is_top_stratum": s.is_top_stratum}
            for s in selection.selected
        ],
        "tie_out": asdict(tie_out) if tie_out is not None else None,
        "ipe": asdict(ipe),
        "evaluation": asdict(evaluation) if evaluation is not None else None,
    }
    stub_evidence = evidence_record(
        run_id=run_id,
        inputs=payload.artifacts,
        params_used=used,
        qa_attestation=(),
        output_payload=payload_for_hash,
        produced_at=produced_at,
        prepared_by=ENGINE_IDENTITY,
    )
    qa = _qa_checks(plan, selection, tie_out, stub_evidence, fieldwork_pending)
    evidence = evidence_record(
        run_id=run_id,
        inputs=payload.artifacts,
        params_used=used,
        qa_attestation=qa,
        output_payload=payload_for_hash,
        produced_at=produced_at,
        prepared_by=ENGINE_IDENTITY,
    )

    escalate_reasons: list[str] = []
    if evaluation is not None and evaluation.conclusion == "do_not_accept":
        escalate_reasons.append(
            f"UML {evaluation.upper_misstatement_limit:.2f} exceeds TM {tm:.2f}"
        )
    if tie_out is not None and not tie_out.within_tolerance:
        escalate_reasons.append(
            f"GL tie-out unexplained {tie_out.unexplained_value:.2f} outside tolerance {tolerance}"
        )

    residual = Residual(
        description=(
            "W-3 audited_value / tickmark columns are blank until the auditor of record "
            "tests the selections. This pack prepares evidence; it does not conclude."
        ),
        risk_if_skipped=(
            "The workpaper is not audit evidence until an auditor of record concludes and signs (AS 1215)."
        ),
    )
    packet = HandoffPacket(
        title="Perform fieldwork on W-3 selections, conclude, and sign W-1..W-6",
        steps=[
            "Test each W-3 selection and key audited_value / tickmark / exception.",
            "Refresh the evaluation (UML vs TM) from the keyed values.",
            "Conclude on the GL tie-out unexplained difference.",
            "Sign Reviewed-by on every sheet; leave engine identity as Prepared-by.",
        ],
        risk_if_skipped=residual.risk_if_skipped,
    )
    if escalate_reasons:
        outcome = replace(
            escalate(
                "; ".join(escalate_reasons),
                FINANCIAL,
                "; ".join(escalate_reasons),
                route_to="engagement partner / finance approver",
                sla="before the documentation-assembly window",
                confidence=0.7,
            ),
            residuals=[residual],
        )
        summary = (
            f"MUS n={plan.sample_size} on PV={pv:.0f}; ESCALATED: " + "; ".join(escalate_reasons)
        )
    else:
        outcome = as_handoff(
            f"MUS sample of {plan.sample_size} from PV {pv:.0f}; fieldwork and sign-off remain.",
            [packet],
            residuals=[residual],
        )
        summary = (
            f"MUS n={plan.sample_size} (interval {plan.sampling_interval:.2f}) on population "
            f"{pv:.0f}; {len(selection.selected)} selections; "
            + ("tie-out within tolerance; " if tie_out is not None and tie_out.within_tolerance else "")
            + ("fieldwork pending on W-3." if fieldwork_pending else "evaluation keyed from audited values.")
        )

    return AuditEvidenceReport(
        plan=plan,
        selection=selection,
        tie_out=tie_out,
        ipe=ipe,
        evaluation=evaluation,
        evidence=evidence,
        outcome=outcome,
        summary=summary,
        fieldwork_pending=fieldwork_pending,
        selection_seed=seed,
        random_start=random_start,
    )


def verify(report: AuditEvidenceReport) -> list[str]:
    """QA gate: identities, evidence completeness, never EXECUTED."""
    issues = list(verify_guided(report.outcome))
    if report.outcome.status == EXECUTED:
        issues.append(
            "outcome must never be EXECUTED: the workpaper is not evidence until a human signs"
        )
    if report.plan.sample_size < 1:
        issues.append("sample_size < 1")
    pv_ok = abs(
        report.plan.sampling_interval * report.plan.sample_size - report.plan.population_value
    ) <= max(1e-6, abs(report.plan.population_value) * 1e-9)
    if not pv_ok:
        issues.append("sampling_interval * n does not cover population_value")
    if not report.evidence.inputs or not report.evidence.inputs[0].sha256:
        issues.append("evidence inputs missing sha256")
    if not report.evidence.params_used:
        issues.append("evidence params_used empty")
    if not report.evidence.formula_versions:
        issues.append("evidence formula_versions empty")
    if not report.evidence.produced_at:
        issues.append("evidence produced_at empty")
    if report.evidence.prepared_by != ENGINE_IDENTITY:
        issues.append(f"prepared_by must be {ENGINE_IDENTITY!r}, never a human name")
    if any(check.passed is False for check in report.evidence.qa_attestation):
        issues.append("qa_attestation contains a failed check")
    if report.tie_out is not None:
        identity = (report.tie_out.gl_total - report.tie_out.subledger_total) - (
            sum(it.amount for it in report.tie_out.reconciling_items) + report.tie_out.unexplained_value
        )
        if abs(identity) > max(1e-6, report.tie_out.tolerance):
            issues.append("GL tie-out identity broken")
    if report.evaluation is not None:
        ev = report.evaluation
        recomputed = ev.basic_precision + ev.projected_misstatement + ev.incremental_allowance
        if abs(recomputed - ev.upper_misstatement_limit) > max(1e-6, abs(ev.upper_misstatement_limit) * 1e-9):
            issues.append("UML is not the sum of its components")
    for h in report.outcome.handoffs:
        if not h.risk_if_skipped.strip():
            issues.append(f"handoff without risk_if_skipped: '{h.title}'")
    return issues


def _signoff_block(ws, *, prepared_by: str, produced_at: str, start_row: int) -> None:
    ws.cell(row=start_row, column=1, value="Prepared by")
    ws.cell(row=start_row, column=2, value=defuse_formula(f"{prepared_by} on {produced_at}"))
    ws.cell(row=start_row + 1, column=1, value="Reviewed by")
    ws.cell(row=start_row + 1, column=2, value="")
    ws.cell(row=start_row + 1, column=3, value="Date")
    ws.cell(row=start_row + 1, column=4, value="")


def _write_workpaper(report: AuditEvidenceReport, path: Path) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    header_fill = PatternFill("solid", fgColor="1F2A44")
    header_font = Font(bold=True, color="FFFFFF")

    def _headers(ws, titles: list[str]) -> None:
        for i, title in enumerate(titles, 1):
            cell = ws.cell(row=1, column=i, value=title)
            cell.fill = header_fill
            cell.font = header_font

    def _kv(ws, rows: list[tuple[str, object]], start: int = 2) -> int:
        r = start
        for key, value in rows:
            ws.cell(row=r, column=1, value=defuse_formula(key))
            ws.cell(row=r, column=2, value=defuse_formula(value))
            r += 1
        return r

    # W-1 Lead / scope
    w1 = wb.active
    w1.title = "W-1 Lead"
    _headers(w1, ["Field", "Value"])
    ipe = report.ipe
    last = _kv(w1, [
        ("population_value", report.plan.population_value),
        ("source", ipe.source_label),
        ("row_count", ipe.row_count),
        ("control_total", ipe.control_total),
        ("tied_to", ipe.tied_to),
        ("tie_difference", ipe.tie_difference),
        ("sha256_prefix", report.evidence.inputs[0].sha256[:16] if report.evidence.inputs else ""),
        ("run_id", report.evidence.run_id),
        ("fieldwork_pending", report.fieldwork_pending),
    ])
    _signoff_block(w1, prepared_by=report.evidence.prepared_by, produced_at=report.evidence.produced_at, start_row=last + 1)

    # W-2 Sampling plan
    w2 = wb.create_sheet("W-2 Sampling plan")
    _headers(w2, ["Field", "Value"])
    last = _kv(w2, [
        ("population_value", report.plan.population_value),
        ("tolerable_misstatement", report.plan.tolerable_misstatement),
        ("expected_misstatement", report.plan.expected_misstatement),
        ("RIA", report.plan.risk_of_incorrect_acceptance),
        ("reliability_factor", report.plan.reliability_factor),
        ("sample_size", report.plan.sample_size),
        ("sampling_interval", report.plan.sampling_interval),
        ("top_stratum_threshold", report.plan.top_stratum_threshold),
        ("exclusions", report.plan.exclusions),
        ("formula_ref", report.plan.formula_ref),
        ("selection_seed", report.selection_seed),
        ("random_start", report.random_start),
    ])
    _signoff_block(w2, prepared_by=report.evidence.prepared_by, produced_at=report.evidence.produced_at, start_row=last + 1)

    # W-3 Selection listing -- audited_value blank unless keyed
    w3 = wb.create_sheet("W-3 Selection")
    _headers(w3, ["unit_id", "book_value", "is_top_stratum", "audited_value", "tickmark", "exception"])
    audited = {p.unit_id: p.audited_value for p in (report.evaluation.per_item if report.evaluation else ())}
    for i, item in enumerate(report.selection.selected, 2):
        w3.cell(row=i, column=1, value=defuse_formula(item.unit_id))
        w3.cell(row=i, column=2, value=item.book_value)
        w3.cell(row=i, column=3, value=item.is_top_stratum)
        w3.cell(row=i, column=4, value=audited.get(item.unit_id))
        w3.cell(row=i, column=5, value="")
        w3.cell(row=i, column=6, value="")
    _signoff_block(
        w3, prepared_by=report.evidence.prepared_by, produced_at=report.evidence.produced_at,
        start_row=len(report.selection.selected) + 3,
    )

    # W-4 Evaluation
    w4 = wb.create_sheet("W-4 Evaluation")
    _headers(w4, ["Field", "Value"])
    if report.evaluation is None:
        last = _kv(w4, [
            ("status", "fieldwork pending -- UML is not computed until audited values are keyed"),
            ("tolerable_misstatement", report.plan.tolerable_misstatement),
        ])
    else:
        ev = report.evaluation
        last = _kv(w4, [
            ("basic_precision", ev.basic_precision),
            ("projected_misstatement", ev.projected_misstatement),
            ("incremental_allowance", ev.incremental_allowance),
            ("upper_misstatement_limit", ev.upper_misstatement_limit),
            ("tolerable_misstatement", ev.tolerable_misstatement),
            ("conclusion", ev.conclusion),
            ("formula_ref", ev.formula_ref),
        ])
    _signoff_block(w4, prepared_by=report.evidence.prepared_by, produced_at=report.evidence.produced_at, start_row=last + 1)

    # W-5 GL tie-out
    w5 = wb.create_sheet("W-5 GL tie-out")
    _headers(w5, ["Field", "Value"])
    if report.tie_out is None:
        last = _kv(w5, [("status", "tie-out not performed (single population; no GL side)")])
    else:
        t = report.tie_out
        last = _kv(w5, [
            ("gl_total", t.gl_total),
            ("subledger_total", t.subledger_total),
            ("difference", t.difference),
            ("matched", t.matched),
            ("unexplained_value", t.unexplained_value),
            ("within_tolerance", t.within_tolerance),
            ("tolerance", t.tolerance),
            ("unmatched_gl", len(t.unmatched_gl)),
            ("unmatched_subledger", len(t.unmatched_subledger)),
        ])
    _signoff_block(w5, prepared_by=report.evidence.prepared_by, produced_at=report.evidence.produced_at, start_row=last + 1)

    # W-6 Lineage
    w6 = wb.create_sheet("W-6 Lineage")
    _headers(w6, ["Field", "Value"])
    evd = report.evidence
    last = _kv(w6, [
        ("run_id", evd.run_id),
        ("produced_at", evd.produced_at),
        ("prepared_by", evd.prepared_by),
        ("output_sha256", evd.output_sha256),
        ("formula_versions", json.dumps(evd.formula_versions)),
        ("params_used", json.dumps(evd.params_used)),
        ("qa_attestation", json.dumps([asdict(c) for c in evd.qa_attestation])),
        ("inputs", json.dumps([asdict(a) for a in evd.inputs])),
    ])
    _signoff_block(w6, prepared_by=evd.prepared_by, produced_at=evd.produced_at, start_row=last + 1)

    for ws in wb.worksheets:
        ws.column_dimensions[get_column_letter(1)].width = 36
        ws.column_dimensions[get_column_letter(2)].width = 48
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def write_operational(report: AuditEvidenceReport, out_dir: str | Path, client: str = "Client") -> dict[str, Path]:
    """CSVs + evidence.json + workpaper.xlsx (W-1..W-6)."""
    del client  # workpaper identity is the engine, not the client label
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    selection_rows = [
        {
            "unit_id": item.unit_id,
            "book_value": item.book_value,
            "is_top_stratum": item.is_top_stratum,
            "audited_value": "",
            "tickmark": "",
            "exception": "",
        }
        for item in report.selection.selected
    ]
    if report.evaluation is not None:
        by_id = {p.unit_id: p for p in report.evaluation.per_item}
        for row in selection_rows:
            item = by_id.get(row["unit_id"])
            if item is not None:
                row["audited_value"] = item.audited_value

    gl_rows: list[dict] = []
    if report.tie_out is not None:
        gl_rows.extend(
            {"side": "gl", "key": line.key, "amount": line.amount}
            for line in report.tie_out.unmatched_gl
        )
        gl_rows.extend(
            {"side": "subledger", "key": line.key, "amount": line.amount}
            for line in report.tie_out.unmatched_subledger
        )

    evidence_path = d / "evidence.json"
    evidence_path.write_text(
        json.dumps(evidence_to_dict(report.evidence), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    workpaper = _write_workpaper(report, d / "workpaper.xlsx")
    return {
        "csv": write_summary_csv(selection_rows, d / "sampling_selection.csv"),
        "gl_exceptions": write_summary_csv(gl_rows, d / "gl_exceptions.csv"),
        "evidence_json": evidence_path,
        "workpaper": workpaper,
    }


def build_deck(
    report: AuditEvidenceReport,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple[str, ...] = (),
    confidence: float | None = None,
) -> Deliverable:
    """Client-facing summary: sampling, tie-out, lineage. Always a human next step.

    ``confidence`` is derived when not supplied: a workpaper without W-5 (GL
    tie-out) or W-3 (keyed fieldwork) is less certain than a complete pack
    (see nosilo_core.guided.confidence_from_coverage).
    """
    if confidence is None:
        gaps: list[str] = []
        if report.tie_out is None:
            gaps.append("ignored_criterion")
        if report.evaluation is None:
            gaps.append("ignored_criterion")
        confidence = confidence_from_coverage(gaps, base=_DECK_BASE_CONFIDENCE)
    findings: list[Finding] = [
        Finding(
            f"MUS sample n={report.plan.sample_size}",
            f"PV {report.plan.population_value:,.0f}; interval {report.plan.sampling_interval:,.2f}; "
            f"seed {report.selection_seed} (re-performable). {report.plan.formula_ref}",
            impact=f"{len(report.selection.selected)} selections",
        ),
    ]
    if report.tie_out is not None:
        findings.append(Finding(
            "GL tie-out " + ("within tolerance" if report.tie_out.within_tolerance else "outside tolerance"),
            f"GL {report.tie_out.gl_total:,.2f} vs subledger {report.tie_out.subledger_total:,.2f}; "
            f"unexplained {report.tie_out.unexplained_value:,.2f}",
            impact=f"unmatched GL {len(report.tie_out.unmatched_gl)}, "
                   f"unmatched subledger {len(report.tie_out.unmatched_subledger)}",
        ))
    else:
        findings.append(Finding(
            "GL tie-out not performed",
            "A single population was supplied (no gl vs subledger split). MUS still ran.",
            impact="add a source column (gl/subledger) to enable W-5",
        ))
    if report.evaluation is not None:
        findings.append(Finding(
            f"Evaluation {report.evaluation.conclusion}",
            f"UML {report.evaluation.upper_misstatement_limit:,.2f} vs TM "
            f"{report.evaluation.tolerable_misstatement:,.2f}",
            impact=report.evaluation.conclusion,
        ))
    else:
        findings.append(Finding(
            "Fieldwork pending on W-3",
            "audited_value is blank by design until the auditor of record keys it.",
            impact="HANDOFF",
        ))

    kpis = (
        Kpi("sample size n", str(report.plan.sample_size),
            rationale="MUS plan size from PV * RF0 / TM"),
        Kpi("sampling interval", f"{report.plan.sampling_interval:,.2f}",
            rationale="PV / n; items >= interval are certainty-selected"),
        Kpi("UML", money(report.evaluation.upper_misstatement_limit if report.evaluation else None),
            rationale="Stringer bound; not measured until fieldwork is keyed"),
        Kpi("unexplained $", money(report.tie_out.unexplained_value if report.tie_out else None),
            rationale="GL minus subledger after reconciling items"),
    )
    data_sources = tuple(
        DataSource(art.path_label, f"sha256={art.sha256[:16]}... n={art.n_rows}", "per run")
        for art in report.evidence.inputs
    )
    recommendations = [
        "Hand the workpaper to the auditor of record: test W-3, conclude, sign Reviewed-by.",
        "Do not treat engine identity on Prepared-by as a human signature.",
        "Re-run with the same selection_seed to re-perform the selection exactly (AS 1215).",
    ]
    return Deliverable(
        title="Inventory audit evidence workpaper (not an audit opinion)",
        client=client,
        summary=report.summary,
        findings=tuple(findings),
        kpis=kpis,
        data_sources=data_sources,
        recommendations=tuple(recommendations),
        citations=citations,
        confidence=confidence,
        residual=(
            "Auditor of record must test selections, conclude, and sign. "
            "Kern prepares re-performable evidence; it does not certify or opine."
        ),
        prepared=prepared or f"{report.evidence.prepared_by} on {report.evidence.produced_at}",
    )
