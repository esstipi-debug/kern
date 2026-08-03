"""Intake Scan job -- walk a raw client folder, measure it, run the QA gate.

Reads every readable file in a folder with pandas, maps each file's headers to
the canonical column names ``src.intake_scan`` knows about (reusing the alias
table of ``jobs/intake.py``, extended there for the wider canonical set this
scan needs), measures per-column coverage and the four circuit-breaker counts,
and hands all of that to the pure engine (``src/intake_scan.py``) to classify
files, judge breakers and build the tool-by-tool verdict report.

The sku / product_id resolution: ``maestro`` (the master/catalog file) and
every other profile that identifies a product now require the SAME canonical
name, ``product_id`` -- single source of truth for "identifier of a product",
same discipline as the data contract in Task 1. "sku" is not a distinct
canonical column here; it is a header alias that maps to ``product_id``, via
``jobs.intake.ALIASES["product_id"]`` (already carried "sku" as an alias --
this module did not need to add it, see ``prepare()``'s FolderIntake for what
that means for a master-only folder). Earlier versions of this module treated
"sku" as maestro's own canonical column, distinct from "product_id" -- that
created a false RED: a folder holding only a master file (headers: sku, name,
...) with no second file supplying a literally-named "product_id" reported
``data_quality`` as RED (missing product_id) even though the master file
obviously identifies every product. Unifying the canonical name fixes that;
see ``tests/test_intake_scan_job.py::test_a_master_only_folder_does_not_false_red_data_quality``.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from jobs.intake import ALIASES, _norm
from src.deliverable import DataSource, Deliverable, Finding, Kpi
from src.export import write_summary_csv
from src.intake_scan import (
    GREEN,
    PROFILES,
    RED,
    UNDECLARED,
    Classified,
    FileColumns,
    FileSignals,
    ScanReport,
    Thresholds,
    ToolVerdict,
    blocking_files,
    build_report,
    classify_all,
    run_breakers,
)

# Exceptions a broken/locked file can raise while pandas reads it -- caught
# per file so one bad file (an Excel lock file "~$foo.xlsx", a 0-byte CSV, a
# corrupt workbook) never aborts the scan of an otherwise-readable folder.
# Deliberately broad (Exception, not a specific pandas/openpyxl type): the
# actual exception class differs by engine and by what exactly is wrong with
# the file, and the only thing this scan needs to know is "could not be read".
_UNREADABLE_EXCEPTIONS = (Exception,)

READABLE_SUFFIXES = (".csv", ".xlsx", ".xls")
MAX_UNCLASSIFIED_SHARE = 0.50

# Breaker ids, in the fixed order src.intake_scan.run_breakers always emits them
# for one file -- the files CSV gets one column per breaker in this order.
_BREAKER_COLUMNS = ("impossible_dates", "negative_quantities", "duplicate_rows", "orphan_keys")


@dataclass(frozen=True)
class FolderIntake:
    """Everything read and measured from one folder, before the engine judges it."""

    files: tuple[FileColumns, ...]
    signals: tuple[FileSignals, ...]
    classified: tuple[Classified, ...]
    available: dict[str, set[str]]
    # filename -> canonical columns THAT FILE carries with sufficient coverage.
    # Per-file, never unioned across the folder -- see src.intake_scan.verdicts_for.
    by_file: dict[str, frozenset[str]]
    blocked: frozenset[str]
    ignored: tuple[str, ...]
    # (filename, short reason) for every readable-extension file that failed to
    # parse -- a declared hole in the folder, listed by name, never silently
    # dropped or allowed to abort the scan of the rest of the folder.
    unreadable: tuple[tuple[str, str], ...]
    under_covered: tuple[tuple[str, str, float], ...]
    # Subdirectory names never walked (this scan is one level deep) -- named
    # even on a fully successful scan, not just in the "nothing readable"
    # error path, so a client zip's "2025/" folder next to maestro.csv leaves
    # a trace in the deliverable instead of vanishing without comment.
    skipped_subdirs: tuple[str, ...]


def _canonical_order() -> tuple[str, ...]:
    """Every canonical name any profile cares about, in first-seen order."""
    seen: list[str] = []
    for profile in PROFILES:
        for col in (*profile.required, *profile.optional):
            if col not in seen:
                seen.append(col)
    return tuple(seen)


_CANONICAL_COLUMNS = _canonical_order()


def _map_headers(columns) -> dict[str, str]:
    """Map raw headers to canonical scan columns: exact names win first, then
    aliases; each header is claimed by at most one canonical name.
    """
    by_norm = {_norm(c): c for c in columns}
    consumed: set[str] = set()
    mapping: dict[str, str] = {}

    for canon in _CANONICAL_COLUMNS:
        hit = by_norm.get(_norm(canon))
        if hit is not None and hit not in consumed:
            mapping[canon] = hit
            consumed.add(hit)

    for canon in _CANONICAL_COLUMNS:
        if canon in mapping:
            continue
        for alias in ALIASES.get(canon, (canon,)):
            hit = by_norm.get(_norm(alias))
            if hit is not None and hit not in consumed:
                mapping[canon] = hit
                consumed.add(hit)
                break

    return {canon: mapping[canon] for canon in _CANONICAL_COLUMNS if canon in mapping}


def _read_table(path: Path) -> pd.DataFrame:
    if path.suffix.lower() in (".xlsx", ".xls"):
        return pd.read_excel(path)
    return pd.read_csv(path)


def _coverage(series: pd.Series) -> float:
    n = len(series)
    if n == 0:
        return 0.0
    present = series.notna() & series.astype(str).str.strip().ne("")
    return float(present.sum()) / n


def _thresholds_from_params(params: dict | None) -> Thresholds:
    params = params or {}
    override = params.get("thresholds")
    return override if isinstance(override, Thresholds) else Thresholds()


def _list_dir(data_path: str) -> tuple[Path, list[Path], list[str]]:
    p = Path(data_path)
    if p.is_file():
        raise ValueError(f"expected a directory, got a file: {data_path}")
    if not p.is_dir():
        raise ValueError(f"directory not found: {data_path}")
    entries = list(p.iterdir())
    files = sorted(e for e in entries if e.is_file())
    # Subdirectories are never walked (this scan is one level deep) -- but a
    # future reader diagnosing an empty scan needs to know they were SEEN and
    # skipped, not that the folder looked empty. See prepare()'s "found:" error.
    subdirs = sorted(e.name for e in entries if e.is_dir())
    return p, files, subdirs


def _short_reason(exc: Exception) -> str:
    """A one-line reason for an unreadable file -- the exception type plus its
    own message, never a raw multi-line pandas/openpyxl traceback string."""
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def prepare(data_path: str, params: dict | None = None) -> FolderIntake:
    """Read every readable file in ``data_path``, map headers, measure coverage
    and the circuit-breaker counts, and classify the folder.

    A file with a readable extension that still fails to parse (a locked
    Excel temp file, a 0-byte CSV, a corrupt workbook) never aborts the whole
    scan: it is caught per file, listed by name in ``unreadable`` with a short
    reason, and the rest of the folder is scanned anyway. Only when NOT ONE
    file in the folder is readable does this raise.
    """
    _, entries, subdirs = _list_dir(data_path)

    ignored: list[str] = []
    unreadable: list[tuple[str, str]] = []
    records: list[tuple[str, dict[str, str], pd.DataFrame]] = []
    for entry in entries:
        if entry.suffix.lower() not in READABLE_SUFFIXES:
            ignored.append(entry.name)
            continue
        try:
            df = _read_table(entry)
        except _UNREADABLE_EXCEPTIONS as exc:
            unreadable.append((entry.name, _short_reason(exc)))
            continue
        mapping = _map_headers(df.columns)
        records.append((entry.name, mapping, df))

    if not records:
        parts = []
        if ignored:
            parts.append(f"ignored: {', '.join(ignored)}")
        if unreadable:
            parts.append(f"unreadable: {', '.join(name for name, _ in unreadable)}")
        if subdirs:
            noun = "subdirectory" if len(subdirs) == 1 else "subdirectories"
            parts.append(f"skipped {noun}: {', '.join(subdirs)}")
        found = "; ".join(parts) if parts else "nothing"
        raise ValueError(f"no readable file found in {data_path}; found: {found}")

    files: list[FileColumns] = []
    for filename, mapping, df in records:
        coverage = {canon: _coverage(df[header]) for canon, header in mapping.items()}
        files.append(FileColumns(
            filename=filename,
            columns=tuple(mapping.keys()),
            coverage=coverage,
            n_rows=len(df),
        ))

    classified = classify_all(files)

    thresholds = _thresholds_from_params(params)
    available: dict[str, set[str]] = {}
    by_file: dict[str, frozenset[str]] = {}
    under_covered: list[tuple[str, str, float]] = []
    for fc in files:
        covered_here: set[str] = set()
        for col, cov in fc.coverage.items():
            if cov >= thresholds.column_coverage:
                available.setdefault(col, set()).add(fc.filename)
                covered_here.add(col)
            else:
                under_covered.append((fc.filename, col, cov))
        by_file[fc.filename] = frozenset(covered_here)

    maestro_files = {c.filename for c in classified if c.profile == "maestro"}
    orphans_evaluated = bool(maestro_files)
    maestro_product_ids: set[str] = set()
    if orphans_evaluated:
        for filename, mapping, df in records:
            if filename in maestro_files and "product_id" in mapping:
                col = df[mapping["product_id"]].dropna().astype(str).str.strip()
                maestro_product_ids |= set(v for v in col if v)

    signals: list[FileSignals] = []
    for filename, mapping, df in records:
        n_rows = len(df)

        n_impossible_dates = 0
        date_header = mapping.get("date")
        if date_header is not None:
            raw = df[date_header]
            present = raw.notna() & raw.astype(str).str.strip().ne("")
            parsed = pd.to_datetime(raw, errors="coerce")
            n_impossible_dates = int((present & parsed.isna()).sum())

        n_negative_quantities = 0
        quantity_header = mapping.get("quantity")
        if quantity_header is not None:
            numeric = pd.to_numeric(df[quantity_header], errors="coerce")
            n_negative_quantities = int((numeric < 0).sum())

        n_duplicate_rows = int(df.duplicated().sum())

        n_orphan_keys = 0
        product_id_header = mapping.get("product_id")
        if orphans_evaluated and product_id_header is not None:
            pid = df[product_id_header].dropna().astype(str).str.strip()
            n_orphan_keys = int((~pid.isin(maestro_product_ids)).sum())

        signals.append(FileSignals(
            filename=filename,
            n_rows=n_rows,
            n_impossible_dates=n_impossible_dates,
            n_negative_quantities=n_negative_quantities,
            n_duplicate_rows=n_duplicate_rows,
            n_orphan_keys=n_orphan_keys,
            orphans_evaluated=orphans_evaluated,
        ))

    findings = run_breakers(signals, thresholds)
    blocked = blocking_files(findings)

    return FolderIntake(
        files=tuple(files),
        signals=tuple(signals),
        classified=classified,
        available=available,
        by_file=by_file,
        blocked=blocked,
        ignored=tuple(ignored),
        unreadable=tuple(unreadable),
        under_covered=tuple(under_covered),
        skipped_subdirs=tuple(subdirs),
    )


def run(folder: FolderIntake, params: dict | None = None) -> ScanReport:
    """Judge the folder: recompute breaker findings, evaluate every registered
    tool's verdict, and assemble the audit report."""
    from scm_agent import tools  # inside the function: avoids a circular import at load

    registry_tools = tools.build_default_registry().list()
    thresholds = _thresholds_from_params(params)
    findings = run_breakers(list(folder.signals), thresholds)
    n_unclassified = sum(1 for c in folder.classified if c.profile is None)

    return build_report(
        classified=folder.classified,
        findings=findings,
        tools=registry_tools,
        files=folder.by_file,
        blocked=folder.blocked,
        n_files_read=len(folder.files),
        n_unclassified=n_unclassified,
        under_covered=folder.under_covered,
        unreadable=folder.unreadable,
        skipped_subdirs=folder.skipped_subdirs,
    )


def verify(report: ScanReport) -> list[str]:
    """QA gate over the scan report.

    Conditions 1-2 are live: they can genuinely fire against real folders.
    Conditions 3-4 are defensive invariant guards, not live gate conditions --
    after the engine's Task 4 fixes, neither can actually be triggered by
    ``build_report`` output:
      - condition 3 depends on ``verdicts_for`` (src/intake_scan.py) always
        routing a tool to RED via ``blocked_required`` whenever its only
        carrying files are blocked, so a GREEN verdict's ``source_files``
        never contains a blocked file.
      - condition 4 depends on ``rank_gaps`` (src/intake_scan.py) only ever
        walking ``missing_required``, and an UNDECLARED verdict always
        carrying an empty ``missing_required``.
    Both stay here, tested directly against hand-built ``ScanReport``s (see
    tests/test_intake_scan_job.py), as a guard against a future regression in
    those engine invariants -- not as something this scan expects to observe.
    """
    issues: list[str] = []

    if not any(c.profile is not None for c in report.classified):
        issues.append("no file was classified in this folder")

    if report.n_files_read > 0:
        share = report.n_unclassified / report.n_files_read
        if share > MAX_UNCLASSIFIED_SHARE:
            issues.append(
                f"{report.n_unclassified} of {report.n_files_read} readable file(s) are "
                f"unclassified ({share:.0%} > {MAX_UNCLASSIFIED_SHARE:.0%})"
            )

    # Defensive invariant guard: see the condition-3 note above.
    blocked = blocking_files(report.findings)
    for v in report.verdicts:
        if v.state == GREEN and any(f in blocked for f in v.source_files):
            issues.append(
                f"invariant violation: tool '{v.tool_key}' is GREEN but source file(s) "
                f"{sorted(set(v.source_files) & blocked)} are blocked "
                "(verdicts_for should have routed this to RED via blocked_required)"
            )

    # Defensive invariant guard: see the condition-4 note above.
    undeclared_keys = {v.tool_key for v in report.verdicts if v.state == UNDECLARED}
    for gap in report.gaps:
        named = [t for t in gap.tools if t in undeclared_keys]
        if named:
            issues.append(
                f"invariant violation: gap '{gap.column}' names undeclared tool(s) {named} "
                "(rank_gaps should only ever walk missing_required, which is always empty "
                "for an UNDECLARED verdict)"
            )

    return issues


def _join(values: tuple[str, ...]) -> str:
    """Empty tuple -> empty cell, never a placeholder; several values -> ';'-joined."""
    return ";".join(values)


# The four independent RED causes a ToolVerdict can carry at once, paired with
# a human-readable label. Order matches ToolVerdict's own field order.
_CAUSE_LABELS: tuple[tuple[str, str], ...] = (
    ("missing_required", "a genuinely missing column"),
    ("blocked_required", "a blocked file"),
    ("under_covered_required", "an under-populated column"),
    ("split_required", "required columns split across files"),
)


def _active_causes(v: ToolVerdict) -> list[str]:
    """Which of the four RED causes are non-empty on this verdict, labeled."""
    return [label for field, label in _CAUSE_LABELS if getattr(v, field)]


def _tool_rows(report: ScanReport) -> list[dict[str, str]]:
    """One row per tool verdict. ``missing_required``, ``blocked_required``,
    ``under_covered_required`` and ``split_required`` are kept as SEPARATE
    columns -- never collapsed -- because each names a different problem with
    a different fix: a genuinely absent column (ask the client for it), a
    column present only in a file a circuit breaker blocked (fix that file's
    data quality), a column present but too sparsely populated (populate it,
    do not re-ask for it), or required columns that exist but never together
    in one file (consolidate them; the data is already there)."""
    return [
        {
            "tool_key": v.tool_key,
            "state": v.state,
            "missing_required": _join(v.missing_required),
            "blocked_required": _join(v.blocked_required),
            "under_covered_required": _join(v.under_covered_required),
            "split_required": _join(v.split_required),
            "missing_optional": _join(v.missing_optional),
            "blocked_by": _join(v.blocked_by),
            "split_files": _join(v.split_files),
            "source_files": _join(v.source_files),
        }
        for v in report.verdicts
    ]


def _file_rows(report: ScanReport) -> list[dict[str, object]]:
    """One row per file read, PLUS one row per unreadable file, PLUS one row
    per skipped subdirectory: profile (empty when unclassified, unreadable,
    or a subdirectory), reason, row count, and one column per breaker holding
    its fraction rounded to 4 places. A breaker that never ran
    (``evaluated=False``, e.g. orphan_keys with no master file classified in
    the folder) gets an empty cell, never a 0 -- a 0 would read as "checked
    and clean", which is not what happened. An unreadable file or a skipped
    subdirectory is a declared hole in the folder, not a silent absence --
    each gets its own row, with its reason prefixed so it reads distinctly
    from a merely-unclassified file."""
    findings_by_file: dict[str, dict[str, object]] = {}
    n_rows_by_file: dict[str, int] = {}
    for f in report.findings:
        bucket = findings_by_file.setdefault(f.filename, {})
        bucket[f.breaker] = "" if not f.evaluated else round(f.fraction, 4)
        n_rows_by_file[f.filename] = f.n_total

    rows: list[dict[str, object]] = []
    for c in report.classified:
        row: dict[str, object] = {
            "filename": c.filename,
            "profile": c.profile or "",
            "reason": c.reason,
            "n_rows": n_rows_by_file.get(c.filename, ""),
        }
        breakers = findings_by_file.get(c.filename, {})
        for breaker in _BREAKER_COLUMNS:
            row[breaker] = breakers.get(breaker, "")
        rows.append(row)

    for filename, reason in report.unreadable:
        row = {
            "filename": filename,
            "profile": "",
            "reason": f"unreadable: {reason}",
            "n_rows": "",
        }
        for breaker in _BREAKER_COLUMNS:
            row[breaker] = ""
        rows.append(row)

    for name in report.skipped_subdirs:
        row = {
            "filename": name,
            "profile": "",
            "reason": "skipped subdirectory: this scan is one level deep and does not walk into it",
            "n_rows": "",
        }
        for breaker in _BREAKER_COLUMNS:
            row[breaker] = ""
        rows.append(row)
    return rows


def write_operational(
    report: ScanReport, out_dir: str | Path, client: str = "Client",
) -> dict[str, Path]:
    """The machine-readable deliverable: one CSV per tool verdict, one per file.

    ``client`` is accepted for signature parity with every other job's
    ``write_operational`` (see ``jobs/disruption_scan_job.py``); this scan's
    CSVs carry no client-specific numbers so it is not otherwise used.
    """
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    return {
        "csv": write_summary_csv(_tool_rows(report), d / "intake_scan_tools.csv"),
        "files_csv": write_summary_csv(_file_rows(report), d / "intake_scan_files.csv"),
    }


_SHAPE_NOT_TRUTH_RESIDUAL = (
    "This scan judges the SHAPE of the client's data -- which columns exist, how "
    "complete they are, and whether a handful of circuit breakers trip -- never "
    "its truth. A file with every required column, full coverage and no breaker "
    "tripped can still hold wrong numbers (a bad unit cost, a mis-typed date that "
    "still parses); this scan cannot detect that and does not claim to."
)


def build_deck(
    report: ScanReport,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple[str, ...] = (),
    confidence: float = 0.9,
    lang: str = "en",
) -> Deliverable:
    """Compose the Data Readiness Scan deck: what the folder supports today,
    what the declared-limit of the scan is, and the single highest-value ask."""
    findings: list[Finding] = []

    # 1) The top-ranked ask (whichever class it belongs to: request a column,
    # or fix a blocked file) -- reuse the guided outcome's own ranking and
    # labels so the deck never re-derives, and can never disagree with, what
    # the options menu already ranked #1.
    options = list(report.outcome.options)
    if options:
        top = options[0]
        findings.append(Finding(
            f"Top opportunity: {top.label}",
            top.summary,
            impact=f"unlocks {top.score:.0f} tool(s)" if top.score else "",
        ))
    else:
        findings.append(Finding(
            "Nothing to request",
            report.outcome.summary,
            impact="",
        ))

    # 2) Undeclared tools are a declared LIMIT of this scan, not a silent gap --
    # never let an undeclared tool read as "not evaluated because it's fine".
    # Scoped to tools that actually consume file data: a tool registered with
    # requires_data=False could never declare a column contract in the first
    # place (it has none to declare), so counting it as "has not declared one
    # YET" misstates a category mismatch as a pending gap and inflates the
    # denominator (49 registered tools total, but only ~44 ever read a file).
    from scm_agent import tools as _tools_mod  # inside the function: avoids a circular import at load

    data_tool_keys = {t.key for t in _tools_mod.build_default_registry().list() if t.requires_data}
    n_data_tools = len(data_tool_keys)
    undeclared = sorted(
        v.tool_key for v in report.verdicts if v.state == UNDECLARED and v.tool_key in data_tool_keys
    )
    if undeclared:
        findings.append(Finding(
            "Declared limit of this scan",
            f"{len(undeclared)} of {n_data_tools} tool(s) that consume file data have not "
            f"declared a data contract yet, so this scan cannot judge them: "
            f"{', '.join(undeclared)}. They are reported as undeclared, never as runnable.",
            impact="excluded from every verdict above; not the same as a red or a green",
        ))

    # 3) A tool can be red for MORE THAN ONE independent reason at once (any
    # combination of the four RED causes: a genuinely missing column, a
    # blocked file, an under-populated column, or required columns split
    # across files) -- fixing only one still leaves it red, and the client
    # must be told about every named cause, not just whichever one a check
    # happened to name first. Generalized over all four causes (not just
    # missing+blocked) so a tool that is e.g. missing one column AND has a
    # second column under-populated elsewhere is never silently promised a
    # single-fix unblock it will not get.
    multi_cause = [(v, _active_causes(v)) for v in report.verdicts]
    multi_cause = [(v, causes) for v, causes in multi_cause if len(causes) >= 2]
    if multi_cause:
        lines = [f"{v.tool_key} ({' AND '.join(causes)})" for v, causes in multi_cause]
        findings.append(Finding(
            "Tools blocked for two or more distinct reasons",
            f"{len(multi_cause)} tool(s) are red for MORE THAN ONE independent reason at "
            f"once: {'; '.join(lines)}. Fixing only one of the named causes will not turn "
            "these green -- every named cause needs its own fix.",
            impact="every named ask is required; fixing only one leaves the tool red",
        ))

    # 3b) Tools red because their required columns exist in the folder, but
    # never together in one file -- prepare() reads exactly one file, so this
    # is a real block, not a false green.
    split_tools = [v for v in report.verdicts if v.split_required]
    if split_tools:
        lines = [
            f"{v.tool_key} needs {', '.join(v.split_required)} together, but no single file "
            f"carries all of them (seen split across {', '.join(v.split_files)})"
            for v in split_tools
        ]
        findings.append(Finding(
            "Tools blocked because required columns are split across files",
            "; ".join(lines),
            impact=(
                "consolidating the columns into one export unlocks these tools; the data "
                "already exists, it just needs to land in one file"
            ),
        ))

    # 3c) Tools red because a required column is present but too sparsely
    # populated -- the client already sent it; re-asking for it buys nothing.
    undercovered_tools = [v for v in report.verdicts if v.under_covered_required]
    if undercovered_tools:
        lines = [
            f"{v.tool_key}: '{column}' in '{filename}' is only {frac:.0%} filled"
            for v in undercovered_tools
            for column, filename, frac in v.under_covered_detail
        ]
        findings.append(Finding(
            "Tools blocked because a required column is present but under-populated",
            "; ".join(lines),
            impact="the column needs to be POPULATED, not re-sent -- it is already there",
        ))

    # 3d) Files that could not be read at all are a declared hole in the
    # folder, never a silent absence -- a locked Excel temp file or a corrupt
    # workbook must never look identical to "the client sent nothing".
    if report.unreadable:
        names = ", ".join(f"{name} ({reason})" for name, reason in report.unreadable)
        findings.append(Finding(
            "Some files could not be read",
            f"{len(report.unreadable)} file(s) could not be read and were skipped; the rest "
            f"of the folder was scanned anyway: {names}.",
            impact="treat as an unscanned hole in the folder, not as an absence of data",
        ))

    # 3e) Subdirectories sitting next to the scanned files are never walked
    # (this scan is one level deep) -- the common case of a client zip with a
    # dated subfolder ("2025/") beside maestro.csv. Named even on a fully
    # successful scan, same discipline as unreadable files, so the client
    # knows there may be more data one level down that this run never saw.
    if report.skipped_subdirs:
        names = ", ".join(report.skipped_subdirs)
        findings.append(Finding(
            "Some subdirectories were not scanned",
            f"{len(report.skipped_subdirs)} subdirectory(ies) sit next to the scanned files "
            f"and were not walked into: {names}. This scan is one level deep only.",
            impact="there may be more client data one level down that this run never saw",
        ))

    # 4) One finding per breaker that actually blocked a file, with its count
    # and fraction. A breaker whose evaluated=False is never `blocking` (see
    # src.intake_scan.run_breakers), so it never lands here -- but see the
    # dedicated not-evaluated finding right below, which reads `evaluated`
    # directly rather than string-matching `detail`.
    blocking = [f for f in report.findings if f.blocking]
    for f in blocking:
        findings.append(Finding(
            f"{f.filename}: {f.breaker} over threshold",
            f"{f.n_affected} of {f.n_total} row(s) ({f.fraction:.1%}) -- {f.detail}.",
            impact=f"blocks every tool whose only source for a column is {f.filename}",
        ))
    if not blocking:
        findings.append(Finding(
            "No blocking data-quality issue",
            "No file breached a circuit-breaker threshold on the rows read.",
            impact="",
        ))

    # A breaker that could not run (typically orphan_keys with no master file
    # classified in the folder) must never read as a clean pass -- decided by
    # the typed `evaluated` field, never by matching words in `detail`.
    not_evaluated = [f for f in report.findings if not f.evaluated]
    if not_evaluated:
        files = ", ".join(sorted({f.filename for f in not_evaluated}))
        breakers = ", ".join(sorted({f.breaker for f in not_evaluated}))
        findings.append(Finding(
            "Some checks could not run",
            f"{breakers} could not be evaluated for: {files}. This is not a clean pass -- "
            "it means the check never ran, most likely because no master file was "
            "classified in this folder.",
            impact="treat these as unknown, not as clear",
        ))

    n_green = sum(1 for v in report.verdicts if v.state == GREEN)
    n_red = sum(1 for v in report.verdicts if v.state == RED)
    kpis = (
        # Evaluated-over-total travels here (and in the summary/residual below)
        # exactly as it does in the engine report -- a deck that dropped the
        # denominator would reintroduce the false-coverage claim already fixed
        # in src/intake_scan.py's build_report.
        Kpi("Tools evaluated", f"{report.n_tools_evaluated} of {report.n_tools_total}",
            rationale="Declared tools judged, out of every tool registered in Kern"),
        Kpi("Files read", f"{report.n_files_read}", rationale="Readable files found in the folder"),
        Kpi("Files unclassified", f"{report.n_unclassified}", target="0",
            rationale="Files that matched no known profile, or matched more than one"),
        Kpi("Files unreadable", f"{len(report.unreadable)}", target="0",
            rationale="Readable-extension files that failed to parse (locked/corrupt); "
                       "scanned separately, never dropped silently"),
        Kpi("Subdirectories skipped", f"{len(report.skipped_subdirs)}", target="0",
            rationale="Subdirectories next to the scanned files; this scan is one level "
                       "deep and never walks into them"),
        Kpi("Tools ready to run (green)", f"{n_green}",
            rationale="Declared tools with every required column available and unblocked"),
        Kpi("Tools blocked (red)", f"{n_red}", target="0",
            rationale="Declared tools missing a required column, or blocked by a dirty file"),
    )

    data_sources = (
        DataSource("Folder contents (CSV/XLSX headers, coverage, row counts)",
                    "client-supplied", "per run"),
    )

    recommendations = [
        "Act on the top-ranked ask first -- it unlocks the most tools per unit of effort.",
        "Treat every undeclared tool as not-yet-available, not as a silent green.",
        "Re-run this scan after any fix or new file to confirm the breaker actually cleared.",
    ]

    return Deliverable(
        title="Data Readiness Scan",
        client=client,
        summary=report.summary,
        findings=tuple(findings),
        kpis=kpis,
        data_sources=data_sources,
        recommendations=tuple(recommendations),
        options=tuple(report.outcome.options),
        citations=tuple(citations),
        confidence=confidence,
        residual=_SHAPE_NOT_TRUTH_RESIDUAL,
        prepared=prepared,
        lang=lang,
    )
