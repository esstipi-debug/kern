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

from dataclasses import dataclass, field

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
