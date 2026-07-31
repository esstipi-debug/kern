"""Raw-folder intake audit -- what the client sent, and what it can actually run.

Pure and dependency-free: the job layer reads the files and hands this module
column names, coverage fractions and row counts. Nothing here touches disk, and
nothing here calls an LLM -- the layer that audits the data has to return the
same verdict twice for the same input.

Two rules run through the whole module. A file that matches no profile, or two,
is reported UNCLASSIFIED rather than resolved to the closest guess. A tool that
has not declared its columns is reported as undeclared rather than as runnable.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from src.guided import ExecutionOption, GuidedOutcome, Residual, as_executed, as_options

UNCLASSIFIED = "unclassified"
AMBIGUOUS = "ambiguous"


@dataclass(frozen=True)
class DatasetProfile:
    """One kind of file we know how to recognise, by the columns it must carry."""

    name: str
    required: tuple[str, ...]
    optional: tuple[str, ...] = ()


# Derived from the PackageInput contracts of the "Diagnostico de Arranque" package
# (scm_agent/package_specs.py) -- the four files a real first engagement asks for.
PROFILES: tuple[DatasetProfile, ...] = (
    DatasetProfile("maestro", ("sku",), ("name", "gtin", "unit_cost")),
    DatasetProfile("ventas", ("date", "product_id", "quantity"), ("unit_cost",)),
    DatasetProfile("stock", ("product_id", "on_hand", "daily_demand"),
                   ("unit_cost", "days_since_last_sale")),
    DatasetProfile("finanzas", ("product_id", "cogs", "avg_inventory_value"),
                   ("gross_margin", "units_sold", "units_on_hand", "net_sales")),
)


@dataclass(frozen=True)
class FileColumns:
    """One readable file, reduced to what the engine needs to judge it."""

    filename: str
    columns: tuple[str, ...]
    coverage: dict[str, float] = field(default_factory=dict)
    n_rows: int = 0


@dataclass(frozen=True)
class Classified:
    """What a file turned out to be -- or why we refused to decide."""

    filename: str
    profile: str | None
    matched: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    reason: str = ""


def classify_file(file: FileColumns) -> Classified:
    """Match a file against every profile. No match or several -> no decision."""
    present = set(file.columns)
    hits = [p for p in PROFILES if set(p.required) <= present]

    if len(hits) == 1:
        profile = hits[0]
        matched = tuple(c for c in (*profile.required, *profile.optional) if c in present)
        return Classified(file.filename, profile.name, matched=matched)

    if len(hits) > 1:
        names = ", ".join(sorted(p.name for p in hits))
        return Classified(
            file.filename, None, reason=f"{AMBIGUOUS}: matches {names}; not guessed"
        )

    # No profile matched: report the closest one's gap, chosen deterministically.
    # Closest = fewest missing required columns. Ties broken alphabetically by profile name.
    def _gap(profile: DatasetProfile) -> tuple[int, str]:
        return (len([c for c in profile.required if c not in present]), profile.name)

    closest = min(PROFILES, key=_gap)
    missing = tuple(c for c in closest.required if c not in present)
    return Classified(
        file.filename, None, missing=missing,
        reason=f"{UNCLASSIFIED}: closest profile '{closest.name}' needs {', '.join(missing)}",
    )


def classify_all(files: list[FileColumns]) -> tuple[Classified, ...]:
    """Classify every file, sorted by filename so two runs agree."""
    return tuple(classify_file(f) for f in sorted(files, key=lambda f: f.filename))


@dataclass(frozen=True)
class Thresholds:
    """Fractions above which a circuit breaker blocks. All configurable per run."""

    impossible_dates: float = 0.01
    negative_quantities: float = 0.01
    duplicate_rows: float = 0.05
    orphan_keys: float = 0.05
    column_coverage: float = 0.50  # not a breaker: feeds the presence rule


@dataclass(frozen=True)
class FileSignals:
    """Counts the job layer measured on one file. The engine judges, never counts."""

    filename: str
    n_rows: int = 0
    n_impossible_dates: int = 0
    n_negative_quantities: int = 0
    n_duplicate_rows: int = 0
    n_orphan_keys: int = 0
    orphans_evaluated: bool = True


@dataclass(frozen=True)
class BreakerFinding:
    """One breaker on one file: how many, out of how many, and whether it blocks."""

    filename: str
    breaker: str
    n_affected: int
    n_total: int
    fraction: float
    blocking: bool
    detail: str = ""
    evaluated: bool = True


def _fraction(n_affected: int, n_total: int) -> float:
    return (n_affected / n_total) if n_total > 0 else 0.0


def run_breakers(
    signals: list[FileSignals], thresholds: Thresholds | None = None
) -> tuple[BreakerFinding, ...]:
    """One finding per breaker per file, sorted by (filename, breaker)."""
    limits = thresholds or Thresholds()
    out: list[BreakerFinding] = []
    for s in sorted(signals, key=lambda x: x.filename):
        checks = (
            ("impossible_dates", s.n_impossible_dates, limits.impossible_dates,
             "rows whose date could not be parsed or fell outside the accepted window"),
            ("negative_quantities", s.n_negative_quantities, limits.negative_quantities,
             "rows carrying a negative quantity"),
            ("duplicate_rows", s.n_duplicate_rows, limits.duplicate_rows,
             "exactly duplicated rows"),
            ("orphan_keys", s.n_orphan_keys, limits.orphan_keys,
             "product ids absent from the master file"),
        )
        for name, affected, limit, detail in checks:
            if name == "orphan_keys" and not s.orphans_evaluated:
                out.append(BreakerFinding(
                    s.filename, name, 0, s.n_rows, 0.0, False,
                    "not evaluated: no master file was classified in this folder",
                    evaluated=False,
                ))
                continue
            fraction = _fraction(affected, s.n_rows)
            out.append(BreakerFinding(
                s.filename, name, affected, s.n_rows, fraction, fraction > limit, detail,
            ))
    return tuple(sorted(out, key=lambda f: (f.filename, f.breaker)))


def blocking_files(findings: tuple[BreakerFinding, ...]) -> frozenset[str]:
    """Files with at least one breaker over its threshold; they cannot support a green."""
    return frozenset(f.filename for f in findings if f.blocking)


GREEN = "verde"
YELLOW = "amarillo"
RED = "rojo"
UNDECLARED = "no_declarado"

_TOP_GAPS = 3


@dataclass(frozen=True)
class ToolVerdict:
    """Whether one tool could run on this folder, and what stops it if not.

    RED has two distinct causes that must never be conflated: a required column
    that is genuinely absent from the folder (`missing_required` -- asking the
    client for it would help), and a required column that IS present but only in
    files a circuit breaker has blocked (`blocked_required` -- asking for the
    same column again buys nothing; the file needs to be fixed instead).
    """

    tool_key: str
    state: str
    missing_required: tuple[str, ...] = ()
    blocked_required: tuple[str, ...] = ()
    missing_optional: tuple[str, ...] = ()
    blocked_by: tuple[str, ...] = ()
    source_files: tuple[str, ...] = ()


@dataclass(frozen=True)
class ColumnGap:
    """One genuinely missing column, and how much asking for it would buy."""

    column: str
    unlocks: int
    tools: tuple[str, ...]


@dataclass(frozen=True)
class BlockedFileGap:
    """One blocked file, and how many tools cleaning it up alone would unblock."""

    filename: str
    unlocks: int
    tools: tuple[str, ...]


def verdicts_for(
    available: dict[str, set[str]], blocked: frozenset[str], tools: list
) -> tuple[ToolVerdict, ...]:
    """One verdict per tool, sorted by key. Undeclared is checked first, always."""
    out: list[ToolVerdict] = []
    for tool in sorted(tools, key=lambda t: t.key):
        if not tool.required_columns:
            out.append(ToolVerdict(tool.key, UNDECLARED))
            continue

        missing_required: list[str] = []
        blocked_required: list[str] = []
        blocked_by: set[str] = set()
        sources: set[str] = set()
        for column in tool.required_columns:
            files = available.get(column, set())
            usable = files - blocked
            if usable:
                sources |= usable
            elif files:
                # Present, but every file carrying it is blocked: a data-quality
                # problem, not an absent column. Never lump this into missing_required.
                blocked_required.append(column)
                blocked_by |= files
            else:
                missing_required.append(column)

        if missing_required or blocked_required:
            out.append(ToolVerdict(
                tool.key, RED, tuple(missing_required), tuple(blocked_required), (),
                tuple(sorted(blocked_by)), tuple(sorted(sources)),
            ))
            continue

        missing_optional = tuple(
            c for c in tool.optional_columns if not (available.get(c, set()) - blocked)
        )
        state = YELLOW if missing_optional else GREEN
        out.append(ToolVerdict(
            tool.key, state, (), (), missing_optional, (), tuple(sorted(sources))
        ))
    return tuple(out)


def rank_gaps(verdicts: tuple[ToolVerdict, ...]) -> tuple[ColumnGap, ...]:
    """Genuinely missing required columns, ranked by how many tools each unlocks.

    A column that is merely blocked (present, but only in disqualified files) is
    excluded -- asking for it again would not unlock anything. Ties break on the
    column name so two runs over the same folder agree.
    """
    by_column: dict[str, list[str]] = {}
    for v in verdicts:
        for column in v.missing_required:
            by_column.setdefault(column, []).append(v.tool_key)
    gaps = [
        ColumnGap(column, len(keys), tuple(sorted(keys)))
        for column, keys in by_column.items()
    ]
    return tuple(sorted(gaps, key=lambda g: (-g.unlocks, g.column)))


def rank_blocked_fixes(verdicts: tuple[ToolVerdict, ...]) -> tuple[BlockedFileGap, ...]:
    """Blocked files, ranked by how many tools fixing each one alone would unblock.

    A tool only counts toward a file's total if that file is the tool's sole
    obstacle -- if the tool is ALSO missing a column that was never sent, cleaning
    up the blocked file would not unblock it by itself, so it is excluded here.
    Ties break on the filename so two runs over the same folder agree.
    """
    by_file: dict[str, list[str]] = {}
    for v in verdicts:
        if v.missing_required:
            continue
        for filename in v.blocked_by:
            by_file.setdefault(filename, []).append(v.tool_key)
    fixes = [
        BlockedFileGap(filename, len(keys), tuple(sorted(keys)))
        for filename, keys in by_file.items()
    ]
    return tuple(sorted(fixes, key=lambda g: (-g.unlocks, g.filename)))


@dataclass(frozen=True)
class ScanReport:
    """The whole audit: what we read, what we found, what can run, what to ask for."""

    classified: tuple
    findings: tuple
    verdicts: tuple[ToolVerdict, ...]
    gaps: tuple[ColumnGap, ...]
    blocked_fixes: tuple[BlockedFileGap, ...]
    n_files_read: int
    n_unclassified: int
    n_tools_evaluated: int
    n_tools_total: int
    outcome: GuidedOutcome
    summary: str


def _top_ask(
    gaps: tuple[ColumnGap, ...], fixes: tuple[BlockedFileGap, ...]
) -> str:
    """One line describing the highest-value ask, whichever class it belongs to."""
    candidates = [(-g.unlocks, g.column, f"asking for '{g.column}' unlocks {g.unlocks}") for g in gaps]
    candidates += [
        (-f.unlocks, f.filename, f"fixing '{f.filename}' unlocks {f.unlocks}") for f in fixes
    ]
    if not candidates:
        return "nothing to request"
    candidates.sort(key=lambda c: (c[0], c[1]))
    return candidates[0][2]


def _outcome(
    gaps: tuple[ColumnGap, ...], fixes: tuple[BlockedFileGap, ...], verdicts: tuple[ToolVerdict, ...]
) -> GuidedOutcome:
    residual = Residual(
        description=(
            "Tools that have not declared their data contract are reported as undeclared, "
            "not as runnable; this scan cannot speak for them."
        ),
        risk_if_skipped=(
            "Treating an undeclared tool as runnable is exactly the false green this scan exists "
            "to prevent."
        ),
    )
    if not gaps and not fixes:
        if not verdicts:
            return as_executed(
                "No tools were declared for this scan; there is nothing to evaluate.",
                residuals=[residual],
            )
        n_green = sum(1 for v in verdicts if v.state == GREEN)
        return as_executed(
            f"The folder supports every declared tool ({n_green} green); nothing to request.",
            residuals=[residual],
        )

    # Two distinct kinds of ask, ranked together in one menu: requesting an
    # absent column, and fixing a blocked file. Ties break on the name so two
    # runs over the same folder agree on the order.
    column_entries = [
        (
            -g.unlocks, g.column,
            ExecutionOption(
                label=f"Request '{g.column}' from the client",
                summary=f"Adding '{g.column}' unlocks {g.unlocks} tool(s): {', '.join(g.tools)}.",
                score=float(g.unlocks),
                action=f"request_column:{g.column}",
                tradeoffs="One column, one ask; the client can usually export it from the same system.",
            ),
        )
        for g in gaps
    ]
    file_entries = [
        (
            -f.unlocks, f.filename,
            ExecutionOption(
                label=f"Fix the data quality of '{f.filename}'",
                summary=f"Fixing '{f.filename}' unblocks {f.unlocks} tool(s): {', '.join(f.tools)}.",
                score=float(f.unlocks),
                action=f"fix_file:{f.filename}",
                tradeoffs="Requires re-cleaning or re-exporting the file; no new column is needed.",
            ),
        )
        for f in fixes
    ]
    ranked = sorted(column_entries + file_entries, key=lambda e: (e[0], e[1]))[:_TOP_GAPS]
    options = [
        replace(option, recommended=(rank == 0)) for rank, (_, __, option) in enumerate(ranked)
    ]
    return as_options(
        "Missing columns and blocked files, ranked by how many tools each fix unlocks.",
        options,
        residuals=[residual],
    )


def build_report(
    *, classified, findings, tools, available, blocked, n_files_read, n_unclassified,
) -> ScanReport:
    """Assemble the audit. Evaluated-vs-total travels in the summary, always."""
    verdicts = verdicts_for(available, blocked, tools)
    gaps = rank_gaps(verdicts)
    fixes = rank_blocked_fixes(verdicts)
    evaluated = sum(1 for v in verdicts if v.state != UNDECLARED)
    total = len(verdicts)
    counts = {state: sum(1 for v in verdicts if v.state == state) for state in (GREEN, YELLOW, RED)}
    top = _top_ask(gaps, fixes)
    summary = (
        f"Read {n_files_read} file(s), {n_unclassified} unclassified; evaluated "
        f"{evaluated} of {total} registered tool(s): {counts[GREEN]} green, "
        f"{counts[YELLOW]} yellow, {counts[RED]} red; {top}."
    )
    return ScanReport(
        classified=tuple(classified), findings=tuple(findings), verdicts=verdicts, gaps=gaps,
        blocked_fixes=fixes, n_files_read=n_files_read, n_unclassified=n_unclassified,
        n_tools_evaluated=evaluated, n_tools_total=total,
        outcome=_outcome(gaps, fixes, verdicts), summary=summary,
    )
