"""Intake Scan job -- walk a raw client folder, measure it, run the QA gate.

Reads every readable file in a folder with pandas, maps each file's headers to
the canonical column names ``src.intake_scan`` knows about (reusing the alias
table of ``jobs/intake.py``, extended there for the wider canonical set this
scan needs), measures per-column coverage and the four circuit-breaker counts,
and hands all of that to the pure engine (``src/intake_scan.py``) to classify
files, judge breakers and build the tool-by-tool verdict report.

The sku / product_id wrinkle: ``maestro`` (the master/catalog file) requires
canonical ``sku``; several other profiles and most registry tools require
canonical ``product_id`` -- the same real-world concept under a different
column name. This module does NOT alias one to the other. Header-to-canonical
mapping is first-match-wins (see ``_map_headers``): a header is claimed by at
most one canonical name, so a master file whose only identifier column is
literally named "sku" does NOT also satisfy "product_id" -- even though
``jobs.intake.ALIASES["product_id"]`` lists "sku" as an alias for the
single-file demand-intake use case. A folder with a master file (headers:
sku, name, ...) and a sales file (headers: date, product_id, quantity, ...)
gets a GREEN ``data_quality`` verdict sourced from the SALES file, not the
master -- see the module's report for what happens when there is no second
file to supply a literally-named "product_id" column.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from jobs.intake import ALIASES, _norm
from src.intake_scan import (
    GREEN,
    PROFILES,
    UNDECLARED,
    Classified,
    FileColumns,
    FileSignals,
    ScanReport,
    Thresholds,
    blocking_files,
    build_report,
    classify_all,
    run_breakers,
)

READABLE_SUFFIXES = (".csv", ".xlsx", ".xls")
MAX_UNCLASSIFIED_SHARE = 0.50


@dataclass(frozen=True)
class FolderIntake:
    """Everything read and measured from one folder, before the engine judges it."""

    files: tuple[FileColumns, ...]
    signals: tuple[FileSignals, ...]
    classified: tuple[Classified, ...]
    available: dict[str, set[str]]
    blocked: frozenset[str]
    ignored: tuple[str, ...]
    under_covered: tuple[tuple[str, str, float], ...]


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
    aliases; each header is claimed by at most one canonical name. See the
    module docstring for why this matters for sku vs product_id.
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


def _list_dir(data_path: str) -> tuple[Path, list[Path]]:
    p = Path(data_path)
    if p.is_file():
        raise ValueError(f"expected a directory, got a file: {data_path}")
    if not p.is_dir():
        raise ValueError(f"directory not found: {data_path}")
    return p, sorted(e for e in p.iterdir() if e.is_file())


def prepare(data_path: str, params: dict | None = None) -> FolderIntake:
    """Read every readable file in ``data_path``, map headers, measure coverage
    and the circuit-breaker counts, and classify the folder."""
    _, entries = _list_dir(data_path)

    ignored: list[str] = []
    records: list[tuple[str, dict[str, str], pd.DataFrame]] = []
    for entry in entries:
        if entry.suffix.lower() not in READABLE_SUFFIXES:
            ignored.append(entry.name)
            continue
        df = _read_table(entry)
        mapping = _map_headers(df.columns)
        records.append((entry.name, mapping, df))

    if not records:
        found = ", ".join(ignored) if ignored else "nothing"
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
    under_covered: list[tuple[str, str, float]] = []
    for fc in files:
        for col, cov in fc.coverage.items():
            if cov >= thresholds.column_coverage:
                available.setdefault(col, set()).add(fc.filename)
            else:
                under_covered.append((fc.filename, col, cov))

    maestro_files = {c.filename for c in classified if c.profile == "maestro"}
    orphans_evaluated = bool(maestro_files)
    maestro_skus: set[str] = set()
    if orphans_evaluated:
        for filename, mapping, df in records:
            if filename in maestro_files and "sku" in mapping:
                col = df[mapping["sku"]].dropna().astype(str).str.strip()
                maestro_skus |= set(v for v in col if v)

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
            n_orphan_keys = int((~pid.isin(maestro_skus)).sum())

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
        blocked=blocked,
        ignored=tuple(ignored),
        under_covered=tuple(under_covered),
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
        available=folder.available,
        blocked=folder.blocked,
        n_files_read=len(folder.files),
        n_unclassified=n_unclassified,
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
