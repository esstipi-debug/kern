"""Procedure register audit job -- intake, as_of/cadence resolution, QA gate, deliverables.

Reads a client's exported SOP (Standard Operating Procedure) register with pandas
directly, the same way ``jobs/org_friction_job.py`` reads its own control-point
export: this data shape (one row per procedure, with document-control columns like
owner/approver/version/review dates) does not fit the canonical demand intake in
``jobs/intake.py``. Feeds ``src/procedures/register.py``'s pure ``audit_register``,
which never touches a clock and never imports pandas (see its own module docstring).

Two rulings from the controller worth knowing before reading this file:

- **R2**: the design brief's own ``prepare()`` signature line said it returns
  ``list[ProcedureRecord]``, but the brief's own body then requires two counters
  (rows dropped for a blank name, rows whose date did not parse). ``PreparedRegister``
  below is the frozen dataclass that resolves that contradiction -- ``run()`` takes
  it, not a bare list.
- **R7**: a row whose ``status`` normalizes to ``"retired"`` skips every check in
  the engine already (``src/procedures/register.py``'s own ``detect()``) -- this
  job does not special-case it further, except in ``write_operational``'s calendar,
  which explicitly excludes retired rows (a retired procedure has no "next review").

``_parse_date`` deviates from the design brief's exact suggested call
(``pd.to_datetime(value, errors="coerce", format="mixed", dayfirst=True)``): verified
directly against this repo's pinned pandas, that call corrupts an already-unambiguous
ISO date (``"2026-06-01"`` parses to 2026-01-06, silently swapping month and day) --
see the function's own docstring for the reproduction and the fix.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

import pandas as pd
from nosilo_core.guided import EXECUTED, Residual, verify_guided

from jobs.intake import _norm
from src.deliverable import DataSource, Deliverable, Finding, Kpi, days, pct
from src.export import write_summary_csv
from src.procedures.models import ProcedureRecord, RegisterAudit
from src.procedures.register import (
    DEFAULT_REVIEW_CADENCE_DAYS,
    DUE_SOON_DAYS,
    NO_APPROVAL,
    NO_KPI_LINK,
    NO_OWNER,
    NO_REVIEW_DATE,
    OVERDUE_REVIEW,
    OWNER_IS_APPROVER,
    RETIRED_STATUS,
    REVIEW_DUE_SOON,
    UNVERSIONED,
    audit_register,
    days_overdue,
    next_review_date,
)

COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "name": ("name", "procedure", "procedurename", "sop", "sopname", "title", "titulo", "nombre", "procedimiento"),
    "owner": ("owner", "ownername", "responsable", "accountable", "dueno", "encargado", "documentowner", "propietario"),
    "approver": ("approver", "approvedby", "aprobador", "aprobadopor", "revisor", "reviewer"),
    "version": ("version", "ver", "rev", "documentversion"),
    "approved_at": ("approvedat", "approvaldate", "fechaaprobacion", "aprobadoel"),
    "last_reviewed_at": (
        "lastreviewedat", "lastreviewed", "lastreview", "reviewdate", "lastreviewdate",
        "ultimarevision", "fechaultimarevision", "fecharevision", "revisadoel",
        "lastupdated", "ultimaactualizacion",
    ),
    "review_cadence_days": ("reviewcadencedays", "reviewcadence", "cadence", "cadencia", "cadenciadias", "frecuenciarevision"),
    "location": ("location", "url", "link", "ubicacion", "enlace"),
    "linked_kpi": ("linkedkpi", "kpi", "indicador", "metrica"),
    "reaction_plan": ("reactionplan", "plandereaccion"),
    "status": ("status", "estado"),
    "procedure_id": ("procedureid", "id", "sopid", "codigo", "clave"),
    "process_area": ("processarea", "area", "proceso", "departamento", "department"),
}
_REQUIRED = ("name", "last_reviewed_at")

_STATUS_MAP = {"aprobado": "approved", "retirado": "retired", "borrador": "draft"}

_ISO_DATE_RE = re.compile(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}")

_CODE_LABELS = {
    OVERDUE_REVIEW: "Revision vencida",
    REVIEW_DUE_SOON: "Revision proxima a vencer",
    NO_OWNER: "Sin dueno asignado",
    OWNER_IS_APPROVER: "Dueno y aprobador son la misma persona",
    NO_APPROVAL: "Aprobado sin aprobador o fecha de aprobacion",
    NO_KPI_LINK: "Sin KPI vinculado",
    UNVERSIONED: "Sin numero de version",
    NO_REVIEW_DATE: "Sin fecha de ultima revision (sin dato)",
}


def _resolve(df: pd.DataFrame, aliases: dict[str, tuple[str, ...]], required: tuple[str, ...], kind: str) -> dict[str, str]:
    """Canonical field -> the client's own column name. Raises naming what is missing."""
    found: dict[str, str] = {}
    for header in df.columns:
        norm = _norm(header)
        for canonical, candidates in aliases.items():
            if canonical not in found and norm in candidates:
                found[canonical] = header
                break
    missing = [c for c in required if c not in found]
    if missing:
        raise ValueError(f"{kind} is missing required column(s): " + ", ".join(missing))
    return found


def _read_table(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"{path} does not exist")
    return pd.read_excel(p) if p.suffix.lower() in {".xlsx", ".xls"} else pd.read_csv(p)


def _parse_date(value: object) -> date | None:
    """One register cell -> a date, or ``None`` when blank/unparseable.

    Day-first is the right default for a Spanish-locale SOP export (DD/MM is the
    common convention) -- that is what the design brief's suggested
    ``pd.to_datetime(value, errors="coerce", format="mixed", dayfirst=True)`` was
    going for. But verified directly against this repo's pinned pandas (3.0.5),
    that exact call also corrupts an already-unambiguous ISO string:

        >>> pd.to_datetime("2026-06-01", format="mixed", dayfirst=True)
        Timestamp('2026-01-06 00:00:00')   # June 1st read as January 6th

    even though "2026" can only be the year and the format has no ambiguity left
    to resolve -- ``dayfirst`` reaches into the two trailing numeric groups
    regardless of whether the leading group already fixed the year. This is the
    same class of defect ``jobs/intake.py``'s own ``_parse_dates`` documents and
    deliberately avoids (see its docstring) for a whole-column ``dayfirst=True``
    retry, but here it reproduces per-value, on a single scalar.

    The fix applied here: ``dayfirst`` is only passed for a value that is NOT
    already ISO-shaped (``YYYY-MM-DD``/``YYYY/MM/DD``). An ISO-shaped value parses
    with pandas' own default (``dayfirst=False``), which reproduces it exactly;
    a non-ISO value (the slash-delimited Spanish-locale case this exists for)
    gets day-first preference for a genuinely ambiguous day/month pair.
    """
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    if not text or text.lower() in ("nan", "nat", "none"):
        return None
    is_iso_shaped = bool(_ISO_DATE_RE.match(text))
    parsed = pd.to_datetime(text, errors="coerce", format="mixed", dayfirst=not is_iso_shaped)
    if pd.isna(parsed):
        return None
    return parsed.date()


def _parse_cadence(value: object) -> int | None:
    parsed = pd.to_numeric(value, errors="coerce")
    return None if pd.isna(parsed) else int(parsed)


def _normalize_status(raw: str) -> str:
    text = raw.strip().lower()
    return _STATUS_MAP.get(text, text)


def _text(row: pd.Series, cols: dict[str, str], key: str) -> str:
    if key not in cols:
        return ""
    value = row[cols[key]]
    return str(value).strip() if pd.notna(value) else ""


def _cell(value: object) -> object:
    """``None`` -> an empty CSV cell; everything else (including ``0``) passes through."""
    return "" if value is None else value


@dataclass(frozen=True)
class PreparedRegister:
    """Rows loaded from the client's register export, plus what got dropped/unparsed.

    See the module docstring's ruling R2 for why this exists instead of a bare list.
    """

    records: list[ProcedureRecord]
    n_dropped_blank_name: int
    n_dates_unparsed: int


def prepare(data_path: str, params: dict | None = None) -> PreparedRegister:
    """Load the register export into ``ProcedureRecord``s.

    A blank ``name`` cell drops the row (counted, never guessed). A blank or
    unparseable ``last_reviewed_at`` cell becomes ``None`` -- the engine reports
    that as the explicit ``sin_dato`` state ``NO_REVIEW_DATE``, never "today". If
    literally every remaining row's date failed to parse, that is very likely the
    wrong column mapped (or a garbled export), not a client with zero review
    history -- raised as its own error rather than silently auditing a register
    with no dates at all.
    """
    params = params or {}
    df = _read_table(data_path)
    if df.empty:
        raise ValueError("the procedure register file has no rows")
    cols = _resolve(df, COLUMN_ALIASES, _REQUIRED, "procedure register file")

    records: list[ProcedureRecord] = []
    n_dropped_blank_name = 0
    n_dates_unparsed = 0
    for _, row in df.iterrows():
        name = _text(row, cols, "name")
        if not name:
            n_dropped_blank_name += 1
            continue

        raw_date = row[cols["last_reviewed_at"]] if "last_reviewed_at" in cols else None
        last_reviewed_at = _parse_date(raw_date)
        if last_reviewed_at is None:
            n_dates_unparsed += 1
        raw_date_text = "" if raw_date is None or pd.isna(raw_date) else str(raw_date).strip()

        records.append(ProcedureRecord(
            name=name,
            procedure_id=_text(row, cols, "procedure_id"),
            process_area=_text(row, cols, "process_area"),
            owner=_text(row, cols, "owner"),
            approver=_text(row, cols, "approver"),
            version=_text(row, cols, "version"),
            approved_at=_parse_date(row[cols["approved_at"]]) if "approved_at" in cols else None,
            last_reviewed_at=last_reviewed_at,
            review_cadence_days=(
                _parse_cadence(row[cols["review_cadence_days"]]) if "review_cadence_days" in cols else None
            ),
            location=_text(row, cols, "location"),
            linked_kpi=_text(row, cols, "linked_kpi"),
            reaction_plan=_text(row, cols, "reaction_plan"),
            status=_normalize_status(_text(row, cols, "status")),
            raw_last_reviewed_at=raw_date_text,
        ))

    if not records:
        detail = f" ({n_dropped_blank_name} row(s) dropped for a blank name)" if n_dropped_blank_name else ""
        raise ValueError(f"no usable procedures: every row was missing a name{detail}")
    if n_dates_unparsed == len(records):
        raise ValueError("ninguna fila trae una fecha de ultima revision parseable")

    return PreparedRegister(
        records=records, n_dropped_blank_name=n_dropped_blank_name, n_dates_unparsed=n_dates_unparsed
    )


def _parse_as_of(params: dict) -> tuple[date, str]:
    """``params['as_of']`` -> ``(as_of, source)``; the clock when absent (``source ==
    "clock"``). Shared by ``run()`` and ``validate_params()`` so both raise the exact
    same ``ValueError`` for the exact same bad input -- see that function's own
    docstring for why two callers need the identical guard."""
    if "as_of" not in params:
        return date.today(), "clock"
    raw_as_of = params["as_of"]
    try:
        return date.fromisoformat(str(raw_as_of)), "param"
    except ValueError as exc:
        raise ValueError(
            f"params['as_of'] debe ser una fecha ISO (YYYY-MM-DD): recibido {raw_as_of!r}"
        ) from exc


def _parse_positive_int(params: dict, key: str, default: int, default_source: str) -> tuple[int, str]:
    """``params[key]`` -> ``(value, "param")`` when present and a positive integer,
    else ``(default, default_source)``. ``ValueError`` naming ``key`` when the value is
    present but not parseable, or parses to zero or a negative number -- an unchecked
    ``review_cadence_days=0`` used to be accepted silently and made
    ``days_overdue >= 2 * cadence`` trivially true for every overdue row, flipping the
    severity mix on a nonsense parameter instead of refusing it."""
    if key not in params:
        return default, default_source
    raw = params[key]
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"params['{key}'] debe ser un numero entero: recibido {raw!r}") from exc
    if value <= 0:
        raise ValueError(f"params['{key}'] debe ser mayor que 0: recibido {value}")
    return value, "param"


def validate_params(params: dict | None) -> None:
    """Validate ``as_of``/``review_cadence_days``/``due_soon_days`` at the tool
    boundary, before any file is read or ``run()`` is called.

    ``scm_agent/tools.py::_procedure_audit_prepare`` calls this so a malformed value
    (a non-ISO ``as_of``, a non-numeric or non-positive ``review_cadence_days`` or
    ``due_soon_days``) surfaces as ``Prepared(status="needs_data", ...)`` with this
    job's own message, instead of escaping ``run()`` as an uncaught ``ValueError`` that
    the orchestrator's blanket exception handler can only report as "An internal error
    occurred." (``scm_agent/orchestrator.py``). ``run()`` below calls the same two
    helpers itself and does not skip its own check just because this function exists --
    a caller that invokes the job directly, bypassing ``prepare()``, still gets the
    identical guard.
    """
    params = params or {}
    _parse_as_of(params)
    _parse_positive_int(params, "review_cadence_days", DEFAULT_REVIEW_CADENCE_DAYS, "convention")
    _parse_positive_int(params, "due_soon_days", DUE_SOON_DAYS, "convention")


def run(prepared: PreparedRegister, params: dict | None = None) -> RegisterAudit:
    """Resolve ``as_of``/cadence from params (or their labelled defaults) and audit."""
    params = params or {}

    as_of, as_of_source = _parse_as_of(params)
    default_cadence_days, cadence_source = _parse_positive_int(
        params, "review_cadence_days", DEFAULT_REVIEW_CADENCE_DAYS, "convention"
    )
    due_soon_days, _due_soon_source = _parse_positive_int(
        params, "due_soon_days", DUE_SOON_DAYS, "convention"
    )

    audit = audit_register(
        prepared.records,
        as_of=as_of,
        default_cadence_days=default_cadence_days,
        due_soon_days=due_soon_days,
        cadence_source=cadence_source,
    )

    extra_residuals: list[Residual] = []
    if as_of_source == "clock":
        extra_residuals.append(Residual(
            description="as_of tomado del reloj del servidor; pase params['as_of'] para fijarlo",
            risk_if_skipped=(
                "Sin un as_of fijo, dos corridas en dias distintos pueden marcar el mismo "
                "procedimiento como vencido o vigente segun cuando se corrio, lo que hace la "
                "auditoria no reproducible."
            ),
        ))
    if prepared.n_dropped_blank_name > 0:
        extra_residuals.append(Residual(
            description=(
                f"{prepared.n_dropped_blank_name} fila(s) del registro se descartaron "
                "por no traer nombre de procedimiento"
            ),
            risk_if_skipped=(
                "Un procedimiento real puede haber quedado fuera de esta auditoria sin "
                "que nadie lo note."
            ),
        ))

    outcome = audit.outcome
    if extra_residuals:
        outcome = replace(outcome, residuals=list(outcome.residuals) + extra_residuals)

    return replace(audit, as_of_source=as_of_source, records=tuple(prepared.records), outcome=outcome)


def verify(report: RegisterAudit) -> list[str]:
    """QA gate: the never-unprotected contract holds, plus register-specific checks."""
    issues = list(verify_guided(report.outcome))

    if report.outcome.status == EXECUTED:
        issues.append("outcome must never be EXECUTED: procedure_audit reads a register, it never acts on it")

    for f in report.findings:
        if not f.evidence:
            issues.append(f"finding without evidence: '{f.name}' ({f.code})")
        if f.code in (OVERDUE_REVIEW, REVIEW_DUE_SOON) and f.due_date is None:
            issues.append(f"overdue/due_soon finding without a parsed date: '{f.name}' ({f.code})")

    if report.n_overdue > 0 and report.outcome.handoffs:
        overdue_packet = report.outcome.handoffs[0]
        if len(overdue_packet.steps) != report.n_overdue:
            issues.append(
                f"handoff steps != n_overdue: {len(overdue_packet.steps)} step(s) for "
                f"{report.n_overdue} overdue finding(s)"
            )

    for r in report.outcome.residuals:
        if not r.risk_if_skipped.strip():
            issues.append(f"residual without risk_if_skipped: '{r.description}'")

    # verify_guided (nosilo_core.guided) checks a Residual's risk_if_skipped, but
    # NOT a HandoffPacket's -- ruling: every handoff this job (or the engine) hands
    # back must state the risk of skipping it, so that gap is closed here.
    for h in report.outcome.handoffs:
        if not h.risk_if_skipped.strip():
            issues.append(f"handoff without risk_if_skipped: '{h.title}'")

    return issues


def write_operational(report: RegisterAudit, out_dir: str | Path, client: str = "Client") -> dict[str, Path]:
    """The machine-readable deliverables: findings (one row per finding) and a
    review calendar (one row per non-retired procedure, whether it has a finding
    or not)."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)

    by_key = {(r.procedure_id, r.name): r for r in report.records}

    finding_rows = []
    for f in report.findings:
        record = by_key.get((f.procedure_id, f.name))
        next_review = next_review_date(record, report.default_cadence_days) if record else None
        finding_rows.append({
            "procedure_id": f.procedure_id,
            "name": f.name,
            "process_area": record.process_area if record else "",
            "code": f.code,
            "severity": f.severity,
            "state": f.state,
            "owner": f.owner,
            "approver": record.approver if record else "",
            "last_reviewed_at": _cell(record.last_reviewed_at.isoformat() if record and record.last_reviewed_at else None),
            "next_review_date": _cell(next_review.isoformat() if next_review else None),
            "due_date": _cell(f.due_date.isoformat() if f.due_date else None),
            "days_overdue": _cell(f.days_overdue),
            "detail": f.detail,
            "evidence": " | ".join(f.evidence),
        })

    calendar_rows = []
    for r in report.records:
        if r.status == RETIRED_STATUS:
            continue
        next_review = next_review_date(r, report.default_cadence_days)
        overdue = days_overdue(r, report.as_of, report.default_cadence_days)
        calendar_rows.append({
            "procedure_id": r.procedure_id,
            "name": r.name,
            "owner": r.owner,
            "next_review_date": _cell(next_review.isoformat() if next_review else None),
            "days_overdue": _cell(overdue),
            "status": r.status,
        })

    return {
        "csv": write_summary_csv(finding_rows, d / "procedure_audit_findings.csv"),
        "calendar_csv": write_summary_csv(calendar_rows, d / "procedure_review_calendar.csv"),
    }


def build_deck(
    report: RegisterAudit,
    *,
    client: str,
    prepared: str | None = None,
    citations: tuple[str, ...] = (),
    confidence: float = 0.6,
    lang: str = "en",
) -> Deliverable:
    """The client-facing audit: what is vencido, sin dueno, or otherwise out of
    document control, ranked, with a prepared handoff."""
    findings: list[Finding] = []
    if report.findings:
        for f in report.findings[:5]:
            impact = f"severidad: {f.severity}"
            if f.due_date is not None:
                impact += f", vence {f.due_date.isoformat()}"
            findings.append(Finding(f"{f.name}: {_CODE_LABELS.get(f.code, f.code)}", f.detail, impact=impact))
    else:
        findings.append(Finding(
            "Ningun hallazgo: el registro esta al dia",
            "Cada procedimiento activo tiene dueno, aprobador distinto, version, KPI "
            "vinculado, y su revision esta dentro de la cadencia declarada.",
            impact="confirmar la cadencia y re-correr en 30 dias",
        ))

    n_active = report.n_procedures - report.n_retired
    pct_overdue = (report.n_overdue / n_active) if n_active else None
    pct_no_owner = (report.n_without_owner / n_active) if n_active else None

    kpis = (
        Kpi("% vencidos", pct(pct_overdue, lang), target="0%",
            rationale="Procedimientos activos cuya revision ya vencio"),
        Kpi("% sin dueno", pct(pct_no_owner, lang), target="0%",
            rationale="Procedimientos activos sin dueno asignado"),
        Kpi("mediana dias desde revision", days(report.median_days_since_review, lang),
            rationale="Mediana de dias desde la ultima revision registrada, sobre procedimientos activos"),
    )
    data_sources = (
        DataSource("Registro de procedimientos (export del sistema del cliente)", "client-supplied", "per run"),
    )
    recommendations = [
        "Revisar primero los procedimientos vencidos que ya tienen dueno asignado: es la "
        "accion mas directa, con alguien que puede ejecutarla hoy.",
        "Donde el dueno y el aprobador son la misma persona, asignar un segundo firmante: "
        "la aprobacion pierde su valor de control cuando quien redacta tambien aprueba.",
        f"La cadencia de revision usada por defecto ({report.default_cadence_days} dia(s)) es "
        f"una convencion ({report.cadence_source}), no un requisito de ISO 9001 -- confirmarla "
        "por area de proceso antes de fijarla como politica.",
        "Para cada procedimiento sin KPI vinculado, incorporarlo a un plan de control (paso, "
        "caracteristica, metodo de medicion, frecuencia, plan de reaccion) en vez de dejarlo "
        "como texto sin metrica asociada.",
    ]
    residual = " ".join(
        f"{r.description} {r.risk_if_skipped}".strip() for r in report.outcome.residuals
    ) or "Sin residual declarado."

    return Deliverable(
        title="Procedure Register Audit",
        client=client,
        summary=report.summary,
        findings=tuple(findings),
        kpis=kpis,
        data_sources=data_sources,
        recommendations=tuple(recommendations),
        options=tuple(report.outcome.options),
        citations=tuple(citations),
        confidence=confidence,
        residual=residual,
        prepared=prepared or "",
        lang=lang,
    )
