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
    # "sku" is not a distinct canonical name here -- it is the same real-world
    # concept as "product_id" (single source of truth, same discipline as the
    # data contract in Task 1). A file whose header literally says "sku" maps
    # to canonical "product_id" via the job layer's alias table.
    DatasetProfile("maestro", ("product_id",), ("name", "gtin", "unit_cost")),
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

    # A hit whose required set is a strict subset of another hit's required set is
    # not genuinely ambiguous -- it is trivially satisfied by the less demanding
    # profile as a side effect of satisfying the more specific one. "Ambiguous" has
    # to mean "could genuinely be either", not "also happens to satisfy the least
    # demanding profile" (e.g. a complete ventas file also satisfies maestro's sole
    # required column, product_id -- that makes it MORE specific than maestro, not
    # unclear which one it is). Order-independent: computed from set containment,
    # not from PROFILES' iteration order.
    if len(hits) > 1:
        hits = [
            p for p in hits
            if not any(
                other is not p and set(p.required) < set(other.required) for other in hits
            )
        ]

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

    RED has FOUR distinct causes that must never be conflated -- each one has a
    different fix, and telling the client the wrong one wastes their time:

    - `missing_required`: a required column genuinely absent from the whole
      folder -- asking the client for it would help.
    - `blocked_required`: a required column IS present with good coverage, but
      every file carrying it is disqualified by a circuit breaker -- asking
      for the same column again buys nothing; the file needs to be fixed.
    - `under_covered_required`: a required column IS present -- the client
      already sent it -- but never populated densely enough anywhere to count
      (see `under_covered_detail` for the file and the measured fraction).
      Asking the client to send the column again buys nothing either; it needs
      to be POPULATED, not re-sent.
    - `split_required`: every required column individually lives in some
      unblocked, sufficiently-covered file, but never all together in ONE
      file. `prepare()` always reads exactly one file per tool, so a tool
      cannot actually run just because the union of the folder happens to
      cover every required name -- see `split_files` for which files
      collectively (never jointly) carry them.
    """

    tool_key: str
    state: str
    missing_required: tuple[str, ...] = ()
    blocked_required: tuple[str, ...] = ()
    under_covered_required: tuple[str, ...] = ()
    under_covered_detail: tuple[tuple[str, str, float], ...] = ()  # (column, filename, coverage)
    split_required: tuple[str, ...] = ()
    split_files: tuple[str, ...] = ()
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


@dataclass(frozen=True)
class SplitColumnGap:
    """Required columns that all exist somewhere in the folder, but never
    together in one file -- consolidating them into a single export would
    unlock every tool listed."""

    columns: tuple[str, ...]
    files: tuple[str, ...]
    unlocks: int
    tools: tuple[str, ...]


@dataclass(frozen=True)
class UnderCoveredGap:
    """One required column that IS present in a file -- the client already
    sent it -- but too sparsely populated to count. Populating it (never
    re-sending it) would unlock every tool listed."""

    column: str
    filename: str
    coverage: float
    unlocks: int
    tools: tuple[str, ...]


def _best_file_for(
    tool, files: dict[str, frozenset[str]], blocked: frozenset[str]
) -> tuple[str, tuple[str, ...]] | None:
    """The single unblocked file that carries every required column together,
    choosing the one with fewest missing optional columns (ties broken by
    filename so two runs over the same folder agree). None if no single file
    carries every required column -- ``prepare()`` always reads exactly one
    file, so a tool can never actually run on the union of several.
    """
    required = frozenset(tool.required_columns)
    candidates = []
    for filename, cols in files.items():
        if filename in blocked:
            continue
        if required <= cols:
            missing_opt = tuple(c for c in tool.optional_columns if c not in cols)
            candidates.append((len(missing_opt), filename, missing_opt))
    if not candidates:
        return None
    candidates.sort(key=lambda c: (c[0], c[1]))
    _, filename, missing_opt = candidates[0]
    return filename, missing_opt


def verdicts_for(
    files: dict[str, frozenset[str]],
    blocked: frozenset[str],
    tools: list,
    under_covered: tuple[tuple[str, str, float], ...] = (),
) -> tuple[ToolVerdict, ...]:
    """One verdict per tool, sorted by key. Undeclared is checked first, always.

    ``files`` maps filename -> the canonical columns that file carries with
    sufficient coverage (never a folder-wide union): GREEN and YELLOW require
    ONE unblocked file to carry every required column together, matching how
    every declared tool's own ``prepare()`` actually reads data (a single
    file). ``under_covered`` carries every (filename, column, coverage
    fraction) the job layer measured below the coverage threshold, so a
    column the client already sent -- just too sparsely populated -- is never
    reported as if it had never arrived.
    """
    available: dict[str, set[str]] = {}
    for filename, cols in files.items():
        for c in cols:
            available.setdefault(c, set()).add(filename)

    under_covered_by_col: dict[str, list[tuple[str, float]]] = {}
    for filename, column, frac in under_covered:
        under_covered_by_col.setdefault(column, []).append((filename, frac))

    out: list[ToolVerdict] = []
    for tool in sorted(tools, key=lambda t: t.key):
        if not tool.required_columns:
            out.append(ToolVerdict(tool.key, UNDECLARED))
            continue

        missing_required: list[str] = []
        blocked_required: list[str] = []
        under_covered_required: list[str] = []
        under_covered_detail: list[tuple[str, str, float]] = []
        blocked_by: set[str] = set()
        sources: set[str] = set()
        for column in tool.required_columns:
            column_files = available.get(column, set())
            usable = column_files - blocked
            if usable:
                sources |= usable
            elif column_files:
                # Present with sufficient coverage, but every file carrying it is
                # blocked: a data-quality problem, not an absent or sparse column.
                blocked_required.append(column)
                blocked_by |= column_files
            elif column in under_covered_by_col:
                # Present -- the client already sent it -- but never populated
                # densely enough anywhere to clear the coverage threshold.
                # Never lump this into missing_required: re-asking for the
                # column buys nothing, it needs to be POPULATED, not re-sent.
                # Deterministic pick when the column is under-covered in more
                # than one file: the highest coverage (closest to usable),
                # ties broken by filename.
                best_file, best_frac = sorted(
                    under_covered_by_col[column], key=lambda oc: (-oc[1], oc[0])
                )[0]
                under_covered_required.append(column)
                under_covered_detail.append((column, best_file, best_frac))
            else:
                missing_required.append(column)

        if missing_required or blocked_required or under_covered_required:
            out.append(ToolVerdict(
                tool.key, RED,
                missing_required=tuple(missing_required),
                blocked_required=tuple(blocked_required),
                under_covered_required=tuple(under_covered_required),
                under_covered_detail=tuple(under_covered_detail),
                blocked_by=tuple(sorted(blocked_by)),
                source_files=tuple(sorted(sources)),
            ))
            continue

        candidate = _best_file_for(tool, files, blocked)
        if candidate is None:
            # Every required column individually lives in some unblocked,
            # sufficiently-covered file, but never all together in ONE file.
            # prepare() reads a single file, so this tool cannot actually run
            # despite every column technically being "available" in the
            # folder's union -- the exact false green this fixes.
            out.append(ToolVerdict(
                tool.key, RED,
                split_required=tuple(tool.required_columns),
                split_files=tuple(sorted(sources)),
            ))
            continue

        filename, missing_optional = candidate
        state = YELLOW if missing_optional else GREEN
        out.append(ToolVerdict(
            tool.key, state, missing_optional=missing_optional, source_files=(filename,),
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

    A tool only counts toward a file's total if that file is its SOLE obstacle:
    no genuinely missing column (fixing a file can't manufacture a column that
    was never sent), and exactly one blocking file. A tool blocked by two or more
    files is credited to none of them individually -- fixing just one still
    leaves it red, so crediting any single file's option would promise an unblock
    that will not happen. Ties break on the filename so two runs over the same
    folder agree.
    """
    by_file: dict[str, list[str]] = {}
    for v in verdicts:
        # A tool only counts if the blocked file is its SOLE obstacle: no
        # genuinely missing column, no under-covered column (fixing the file's
        # breaker does not populate a sparse column elsewhere), no split
        # (fixing one file cannot conjure the required columns together), and
        # exactly one blocking file.
        if (v.missing_required or v.under_covered_required or v.split_required
                or len(v.blocked_by) != 1):
            continue
        (filename,) = v.blocked_by
        by_file.setdefault(filename, []).append(v.tool_key)
    fixes = [
        BlockedFileGap(filename, len(keys), tuple(sorted(keys)))
        for filename, keys in by_file.items()
    ]
    return tuple(sorted(fixes, key=lambda g: (-g.unlocks, g.filename)))


def rank_split_fixes(verdicts: tuple[ToolVerdict, ...]) -> tuple[SplitColumnGap, ...]:
    """Required columns that exist in the folder but never together in one
    file, ranked by how many tools consolidating them into a single export
    would unlock. Grouped by the exact required-column set a tool needs
    together, since that is exactly what one consolidated file would have to
    carry. Ties break on the column tuple so two runs over the same folder
    agree.
    """
    by_key: dict[tuple[str, ...], tuple[set[str], list[str]]] = {}
    for v in verdicts:
        if not v.split_required:
            continue
        key = tuple(sorted(v.split_required))
        bucket = by_key.setdefault(key, (set(), []))
        bucket[0].update(v.split_files)
        bucket[1].append(v.tool_key)
    gaps = [
        SplitColumnGap(columns, tuple(sorted(files)), len(keys), tuple(sorted(keys)))
        for columns, (files, keys) in by_key.items()
    ]
    return tuple(sorted(gaps, key=lambda g: (-g.unlocks, g.columns)))


def rank_undercovered_fixes(verdicts: tuple[ToolVerdict, ...]) -> tuple[UnderCoveredGap, ...]:
    """Required columns present but too sparsely populated, ranked by how many
    tools populating each one (in its named file) would unlock. Ties break on
    (column, filename) so two runs over the same folder agree.

    Same exclusion discipline as rank_blocked_fixes (see its docstring): a
    tool only counts toward a column's "populate" credit if that column is
    its SOLE obstacle. A tool that is ALSO red for a genuinely missing column,
    a blocked file, or a split-across-files column cannot be unblocked by
    populating this one column alone -- crediting it here would promise an
    unlock that will not happen (populating daily_demand does nothing for a
    tool still missing on_hand entirely). Symmetric with the two-blocking-
    files case too: a tool under-covered on TWO OR MORE columns is credited
    to none of them individually -- populating just one still leaves it red.
    """
    by_key: dict[tuple[str, str], list[str]] = {}
    coverage_by_key: dict[tuple[str, str], float] = {}
    for v in verdicts:
        if (v.missing_required or v.blocked_required or v.split_required
                or len(v.under_covered_detail) != 1):
            continue
        for column, filename, frac in v.under_covered_detail:
            key = (column, filename)
            by_key.setdefault(key, []).append(v.tool_key)
            coverage_by_key[key] = frac
    gaps = [
        UnderCoveredGap(column, filename, coverage_by_key[(column, filename)], len(keys), tuple(sorted(keys)))
        for (column, filename), keys in by_key.items()
    ]
    return tuple(sorted(gaps, key=lambda g: (-g.unlocks, g.column, g.filename)))


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
    split_fixes: tuple[SplitColumnGap, ...] = ()
    undercovered_fixes: tuple[UnderCoveredGap, ...] = ()
    # Every (filename, column, coverage fraction) the job layer measured below
    # the coverage threshold -- travels all the way to the deck/CSVs so a
    # column the client already sent, just too sparsely, is never reported as
    # if it had never arrived (see ToolVerdict.under_covered_required).
    under_covered: tuple[tuple[str, str, float], ...] = ()
    # (filename, short reason) for every readable-extension file that failed
    # to parse -- a declared hole in the folder, never silently dropped.
    unreadable: tuple[tuple[str, str], ...] = ()
    # Subdirectory names sitting alongside the scanned files -- this scan is
    # one level deep and never walks into them, but a successful scan that
    # silently drops them leaves a client zip's "2025/" folder (or similar)
    # with no trace anywhere in the deliverable. Named here the same way an
    # unreadable file is, not just in the "nothing readable" error path.
    skipped_subdirs: tuple[str, ...] = ()


def _top_ask(
    gaps: tuple[ColumnGap, ...],
    fixes: tuple[BlockedFileGap, ...],
    split_fixes: tuple[SplitColumnGap, ...] = (),
    undercovered_fixes: tuple[UnderCoveredGap, ...] = (),
) -> str:
    """One line describing the highest-value ask, whichever class it belongs to."""
    candidates = [(-g.unlocks, g.column, f"asking for '{g.column}' unlocks {g.unlocks}") for g in gaps]
    candidates += [
        (-f.unlocks, f.filename, f"fixing '{f.filename}' unlocks {f.unlocks}") for f in fixes
    ]
    candidates += [
        (
            -s.unlocks, ",".join(s.columns),
            f"consolidating {', '.join(s.columns)} into one file unlocks {s.unlocks}",
        )
        for s in split_fixes
    ]
    candidates += [
        (
            -u.unlocks, f"{u.column}:{u.filename}",
            f"populating '{u.column}' in '{u.filename}' unlocks {u.unlocks}",
        )
        for u in undercovered_fixes
    ]
    if not candidates:
        return "nothing to request"
    candidates.sort(key=lambda c: (c[0], c[1]))
    return candidates[0][2]


def _outcome(
    gaps: tuple[ColumnGap, ...],
    fixes: tuple[BlockedFileGap, ...],
    split_fixes: tuple[SplitColumnGap, ...],
    undercovered_fixes: tuple[UnderCoveredGap, ...],
    verdicts: tuple[ToolVerdict, ...],
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
    if not gaps and not fixes and not split_fixes and not undercovered_fixes:
        n_evaluated = sum(1 for v in verdicts if v.state != UNDECLARED)
        if n_evaluated == 0:
            # Either no tools were passed in at all, or every one of them is
            # UNDECLARED -- the common case in production. Neither is "the
            # folder supports everything"; nothing was actually evaluated.
            return as_executed(
                "No declared tool was evaluated; there is nothing to claim coverage over.",
                residuals=[residual],
            )
        n_green = sum(1 for v in verdicts if v.state == GREEN)
        return as_executed(
            f"The folder supports every declared tool ({n_green} green); nothing to request.",
            residuals=[residual],
        )

    # Four distinct kinds of ask, ranked together in one menu: requesting an
    # absent column, fixing a blocked file, consolidating split columns into
    # one file, and populating an under-covered column. Ties break on the
    # name so two runs over the same folder agree on the order.
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
    split_entries = [
        (
            -s.unlocks, ",".join(s.columns),
            ExecutionOption(
                label=f"Consolidate {', '.join(s.columns)} into one file",
                summary=(
                    f"'{', '.join(s.columns)}' all exist in the folder ({', '.join(s.files)}) "
                    f"but never together in one file; consolidating them unlocks {s.unlocks} "
                    f"tool(s): {', '.join(s.tools)}."
                ),
                score=float(s.unlocks),
                action=f"consolidate_columns:{'+'.join(s.columns)}",
                tradeoffs="No new column is needed; the same data just needs to land in one export.",
            ),
        )
        for s in split_fixes
    ]
    undercovered_entries = [
        (
            -u.unlocks, f"{u.column}:{u.filename}",
            ExecutionOption(
                label=f"Populate '{u.column}' in '{u.filename}'",
                summary=(
                    f"'{u.column}' in '{u.filename}' is currently {u.coverage:.0%} filled; "
                    f"populating it unlocks {u.unlocks} tool(s): {', '.join(u.tools)}."
                ),
                score=float(u.unlocks),
                action=f"populate_column:{u.filename}:{u.column}",
                tradeoffs="The column is already there; only the missing values need to be filled in.",
            ),
        )
        for u in undercovered_fixes
    ]
    ranked = sorted(
        column_entries + file_entries + split_entries + undercovered_entries,
        key=lambda e: (e[0], e[1]),
    )[:_TOP_GAPS]
    options = [
        replace(option, recommended=(rank == 0)) for rank, (_, __, option) in enumerate(ranked)
    ]
    return as_options(
        "Missing columns, blocked files, split columns and under-populated columns, ranked by "
        "how many tools each fix unlocks.",
        options,
        residuals=[residual],
    )


def build_report(
    *, classified, findings, tools, files, blocked, n_files_read, n_unclassified,
    under_covered=(), unreadable=(), skipped_subdirs=(),
) -> ScanReport:
    """Assemble the audit. Evaluated-vs-total travels in the summary, always.

    ``files`` maps filename -> the canonical columns that file carries with
    sufficient coverage (see ``verdicts_for``'s own docstring for why this is
    per-file, never a folder-wide union).
    """
    verdicts = verdicts_for(files, blocked, tools, tuple(under_covered))
    gaps = rank_gaps(verdicts)
    fixes = rank_blocked_fixes(verdicts)
    split_fixes = rank_split_fixes(verdicts)
    undercovered_fixes = rank_undercovered_fixes(verdicts)
    evaluated = sum(1 for v in verdicts if v.state != UNDECLARED)
    total = len(verdicts)
    counts = {state: sum(1 for v in verdicts if v.state == state) for state in (GREEN, YELLOW, RED)}
    top = _top_ask(gaps, fixes, split_fixes, undercovered_fixes)
    summary = (
        f"Read {n_files_read} file(s), {n_unclassified} unclassified; evaluated "
        f"{evaluated} of {total} registered tool(s): {counts[GREEN]} green, "
        f"{counts[YELLOW]} yellow, {counts[RED]} red; {top}."
    )
    return ScanReport(
        classified=tuple(classified), findings=tuple(findings), verdicts=verdicts, gaps=gaps,
        blocked_fixes=fixes, n_files_read=n_files_read, n_unclassified=n_unclassified,
        n_tools_evaluated=evaluated, n_tools_total=total,
        outcome=_outcome(gaps, fixes, split_fixes, undercovered_fixes, verdicts), summary=summary,
        split_fixes=split_fixes, undercovered_fixes=undercovered_fixes,
        under_covered=tuple(under_covered), unreadable=tuple(unreadable),
        skipped_subdirs=tuple(skipped_subdirs),
    )
