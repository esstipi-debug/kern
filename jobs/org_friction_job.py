"""End-to-end organizational-friction audit job -- intake, QA gate, deliverables.

Reads a control-point export (one row per KPI/checkpoint under audit) with pandas
directly, the same way ``jobs/flow_map_job.py`` reads a movements export: this
data shape does not fit the canonical demand intake in ``jobs/intake.py``.

Two files, one required, one optional:

  1. **control points** (required) -- one row per KPI/checkpoint: a name, and
     whichever of official_value / shadow_value / owner / workaround_present /
     workaround_classification the client already has. Every one of those is
     optional per row -- this tool audits whatever independent evidence
     already exists; it does not require a client to have instrumented all
     four before asking.
  2. **history** (optional, ``params["history_path"]``) -- one row per
     control-point-period: control_point, period, actual, target. Rows for
     the same control point must appear in chronological order in the file
     (this job does not parse ``period`` as a date -- real exports use labels
     like "Q1" or "Wk 5" alongside ISO months, so no single parse is safe);
     this mirrors how a client's own monthly KPI tracker is already sorted.

Unlike flow_map's dropped-share gate, there is no partial-fields threshold to
tune here: a row is usable the moment it carries a name (every other field
absent just means fewer of the four patterns can fire on that control point,
which ``src.org_friction`` already handles by requiring its own minimum
evidence per detector). Only a row with no name at all is unusable.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from jobs.intake import _norm
from nosilo_core.guided import verify_guided
from src.deliverable import DataSource, Deliverable, Finding, Kpi
from src.export import write_summary_csv
from src.org_friction import (
    ACCOUNTABILITY_VACUUM,
    DEFAULT_SHADOW_DELTA_THRESHOLD,
    ESSENTIAL,
    GOODHART_RATCHET,
    HIDDEN_FACTORY,
    HINDRANCE,
    UNCLASSIFIED_WORKAROUND,
    ControlPoint,
    FrictionReport,
    TargetPeriod,
    analyze,
)

COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "name": ("name", "controlpoint", "kpi", "checkpoint", "nombre", "puntocontrol", "puntodecontrol"),
    "official_value": ("officialvalue", "official", "systemvalue", "valoroficial", "oficial"),
    "shadow_value": ("shadowvalue", "shadow", "independentvalue", "valorsombra", "valorindependiente", "sombra"),
    "owner": ("owner", "ownername", "responsable", "accountable", "dueno", "encargado"),
    "workaround_present": ("workaroundpresent", "hasworkaround", "workaround", "tieneworkaround", "parche", "tieneparche"),
    "workaround_classification": (
        "workaroundclassification", "workaroundclass", "classification", "clasificacion", "tipoworkaround",
    ),
}
_REQUIRED = ("name",)

_HISTORY_ALIASES: dict[str, tuple[str, ...]] = {
    "name": COLUMN_ALIASES["name"],
    "period": ("period", "periodo", "month", "mes"),
    "actual": ("actual", "real", "value", "valor"),
    "target": ("target", "meta", "objetivo", "goal"),
}
_HISTORY_REQUIRED = ("name", "period", "actual", "target")

_TRUE_TOKENS = {"true", "yes", "y", "si", "sí", "1", "x", "ok"}
_ESSENTIAL_TOKENS = {"essential", "esencial"}
_HINDRANCE_TOKENS = {"hindrance", "parche", "defensivo", "deniability"}

_PATTERN_LABELS = {
    HIDDEN_FACTORY: "Hidden Factory (two channels disagree)",
    GOODHART_RATCHET: "Goodhart's Law / ratchet effect",
    ACCOUNTABILITY_VACUUM: "Accountability vacuum",
    UNCLASSIFIED_WORKAROUND: "Unclassified workaround",
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


def _parse_float(value: object) -> float | None:
    parsed = pd.to_numeric(value, errors="coerce")
    return None if pd.isna(parsed) else float(parsed)


def _parse_bool(value: object) -> bool:
    """Lenient boolean parsing: real/1/x/si/yes (any case) -> True; blank/NaN -> False."""
    if isinstance(value, bool):
        return value
    try:
        if pd.isna(value):
            return False
    except (TypeError, ValueError):
        pass
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in _TRUE_TOKENS


def _parse_classification(value: object) -> str:
    """Normalize free-text workaround classification to ESSENTIAL / HINDRANCE / "" (unclassified)."""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    token = str(value).strip().lower()
    if token in _ESSENTIAL_TOKENS:
        return ESSENTIAL
    if token in _HINDRANCE_TOKENS:
        return HINDRANCE
    return ""


def _read_table(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"{path} does not exist")
    return pd.read_excel(p) if p.suffix.lower() in {".xlsx", ".xls"} else pd.read_csv(p)


def _load_history(path: str | Path) -> dict[str, list[TargetPeriod]]:
    """Read the optional history export and group TargetPeriods by control-point name.

    Rows are kept in file order per control point (see the module docstring for why
    ``period`` is never date-parsed): a history file must already list each control
    point's periods chronologically.
    """
    df = _read_table(path)
    if df.empty:
        raise ValueError("the history file has no rows")
    cols = _resolve(df, _HISTORY_ALIASES, _HISTORY_REQUIRED, "history file")

    by_name: dict[str, list[TargetPeriod]] = {}
    for _, row in df.iterrows():
        name = str(row[cols["name"]]).strip() if pd.notna(row[cols["name"]]) else ""
        actual = _parse_float(row[cols["actual"]])
        target = _parse_float(row[cols["target"]])
        if not name or actual is None or target is None:
            continue
        period = str(row[cols["period"]]).strip() if pd.notna(row[cols["period"]]) else ""
        by_name.setdefault(name, []).append(TargetPeriod(period=period, actual=actual, target=target))
    return by_name


def prepare(data_path: str, params: dict | None = None) -> list[ControlPoint]:
    """Load the control points (and, if given, the history export) into ControlPoints."""
    params = params or {}
    df = _read_table(data_path)
    if df.empty:
        raise ValueError("the control points file has no rows")
    cols = _resolve(df, COLUMN_ALIASES, _REQUIRED, "control points file")

    history_by_name: dict[str, list[TargetPeriod]] = {}
    history_path = params.get("history_path")
    if history_path:
        history_by_name = _load_history(history_path)

    control_points: list[ControlPoint] = []
    skipped = 0
    for _, row in df.iterrows():
        name = str(row[cols["name"]]).strip() if pd.notna(row[cols["name"]]) else ""
        if not name:
            skipped += 1
            continue
        official = _parse_float(row[cols["official_value"]]) if "official_value" in cols else None
        shadow = _parse_float(row[cols["shadow_value"]]) if "shadow_value" in cols else None
        owner = (
            str(row[cols["owner"]]).strip()
            if "owner" in cols and pd.notna(row[cols["owner"]]) else ""
        )
        workaround_present = (
            _parse_bool(row[cols["workaround_present"]]) if "workaround_present" in cols else False
        )
        workaround_classification = (
            _parse_classification(row[cols["workaround_classification"]])
            if "workaround_classification" in cols else ""
        )
        control_points.append(ControlPoint(
            name=name,
            official_value=official,
            shadow_value=shadow,
            owner=owner,
            workaround_present=workaround_present,
            workaround_classification=workaround_classification,
            history=tuple(history_by_name.get(name, ())),
        ))
    if not control_points:
        detail = f" ({skipped} row(s) dropped for a blank name)" if skipped else ""
        raise ValueError(f"no usable control points: every row was missing a name{detail}")
    return control_points


def run(control_points: list[ControlPoint], params: dict | None = None) -> FrictionReport:
    """Detect the four friction patterns and rank what to triage first."""
    params = params or {}
    threshold = float(params.get("shadow_delta_threshold", DEFAULT_SHADOW_DELTA_THRESHOLD))
    return analyze(control_points, shadow_delta_threshold=threshold)


def verify(report: FrictionReport) -> list[str]:
    """QA gate: the never-unprotected contract holds, and every finding carries evidence."""
    issues = list(verify_guided(report.outcome))
    if report.n_control_points <= 0:
        issues.append("no control points to audit")
    for f in report.findings:
        if not f.evidence:
            issues.append(f"finding on '{f.control_point}' ({f.pattern}) carries no evidence")
    return issues


def write_operational(report: FrictionReport, out_dir: str | Path, client: str = "Client") -> dict[str, Path]:
    """The machine-readable deliverable: one row per finding, ranked, with its evidence."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "control_point": f.control_point,
            "pattern": f.pattern,
            "severity": f.severity,
            "detail": f.detail,
            "evidence": " | ".join(f.evidence),
        }
        for f in report.findings
    ]
    return {"csv": write_summary_csv(rows, d / "org_friction_findings.csv")}


def build_deck(
    report: FrictionReport,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple[str, ...] = (),
    confidence: float = 0.6,
    lang: str = "en",
) -> Deliverable:
    """The client-facing audit: which control points show managed, not reported, numbers."""
    findings: list[Finding] = []
    if report.findings:
        top = report.findings[0]
        findings.append(Finding(
            f"{top.control_point}: {_PATTERN_LABELS.get(top.pattern, top.pattern)}",
            f"{top.detail} Evidence: {'; '.join(top.evidence)}.",
            impact="confirm with the control point's owner before concluding anything",
        ))
        for f in report.findings[1:5]:
            findings.append(Finding(
                f"{f.control_point}: {_PATTERN_LABELS.get(f.pattern, f.pattern)}",
                f.detail,
                impact=f"severity: {f.severity}",
            ))
    else:
        findings.append(Finding(
            "No pattern could be triangulated from the evidence given",
            "Every control point audited was missing the second, independent channel "
            "(a shadow reading, target history, or an owner/workaround classification) "
            "that any of the four patterns needs before it can fire.",
            impact="gather one independent reading per control point, then re-run",
        ))

    by_pattern: dict[str, int] = {}
    for f in report.findings:
        by_pattern[f.pattern] = by_pattern.get(f.pattern, 0) + 1
    kpis = (
        Kpi("Control points audited", f"{report.n_control_points}", rationale="Rows read from the intake"),
        Kpi("Control points flagged", f"{report.n_flagged}", target="0",
            rationale="Control points carrying at least one triangulated pattern"),
        Kpi("Hidden Factory", f"{by_pattern.get(HIDDEN_FACTORY, 0)}",
            rationale="Official vs. independently-sourced value disagree beyond threshold"),
        Kpi("Goodhart ratchet", f"{by_pattern.get(GOODHART_RATCHET, 0)}",
            rationale="A target was raised right after the prior one was met"),
        Kpi("Accountability vacuum", f"{by_pattern.get(ACCOUNTABILITY_VACUUM, 0)}",
            rationale="No owner assigned to the control point"),
        Kpi("Unclassified workaround", f"{by_pattern.get(UNCLASSIFIED_WORKAROUND, 0)}",
            rationale="An informal channel runs in parallel, uncategorised"),
    )
    data_sources = (
        DataSource("Control points (official / shadow value, owner, workaround)", "client-supplied", "per run"),
        DataSource("Target history (actual vs. target per period)", "client-supplied, optional", "per run"),
    )
    recommendations = [
        "Triage the top-ranked control point first: it is a question for the owner, not a verdict.",
        "Where no owner is assigned, assign one before the next run -- an unowned control point "
        "cannot be triangulated even when the numbers disagree.",
        "Re-run after each review cycle: the ratchet and hidden-factory patterns only become "
        "visible once fresh target and shadow-channel data exist to compare against.",
    ]
    return Deliverable(
        title="Organizational Friction Audit",
        client=client,
        summary=report.summary,
        findings=tuple(findings),
        kpis=kpis,
        data_sources=data_sources,
        recommendations=tuple(recommendations),
        options=tuple(report.outcome.options),
        citations=tuple(citations),
        confidence=confidence,
        residual=(
            "Every pattern here is a triangulation over the evidence given, never a verdict on a "
            "person: confirm each finding with the control point's owner (or assign one first) "
            "before concluding anything is being gamed, hidden, or postponed."
        ),
        prepared=prepared,
        lang=lang,
    )
