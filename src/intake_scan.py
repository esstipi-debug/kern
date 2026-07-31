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
    def _gap(profile: DatasetProfile) -> int:
        matches = len([c for c in profile.required if c in present])
        return -matches

    closest = min(PROFILES, key=_gap)
    missing = tuple(c for c in closest.required if c not in present)
    return Classified(
        file.filename, None, missing=missing,
        reason=UNCLASSIFIED,
    )


def classify_all(files: list[FileColumns]) -> tuple[Classified, ...]:
    """Classify every file, sorted by filename so two runs agree."""
    return tuple(classify_file(f) for f in sorted(files, key=lambda f: f.filename))
