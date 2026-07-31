# intake_scan Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `intake_scan`, a tool that reads the client's raw folder, classifies each file by its column signature, runs circuit breakers, and emits a per-tool traffic light plus the missing columns ranked by how many tools each one unlocks.

**Architecture:** `src/intake_scan.py` is a pure engine (no I/O, no pandas): dataset profiles, classification, circuit breakers, the four-state verdict and the ranking. `jobs/intake_scan_job.py` walks the folder, reads files with pandas, runs the QA gate and writes deliverables. Separately, `scm_agent/registry.py`'s `Tool` dataclass gains `required_columns` / `optional_columns`, and the four Diagnóstico tools declare theirs — the first time a Kern tool states its data contract as data rather than as prose buried in `prepare()`.

**Tech Stack:** Python 3.11+, stdlib + `pandas` (file reading only). No new dependencies. No LLM anywhere in this feature.

**Spec:** `docs/superpowers/specs/2026-07-31-intake-scan-design.md`

## Global Constraints

- **Worktree:** `C:\Users\Gamer\Music\scm\.wt-intake-scan`, branch `feat/intake-scan`, based on `origin/main` @ 96dc6f9.
- **Test interpreter:** `../supply-chain-optimization/.venv/Scripts/python.exe`. A bare `python` cannot import `openpyxl`, and importing `src.*` pulls in `src/__init__.py`. Do not install packages.
- **Do not run the full suite while iterating** — it takes ~9 minutes. Run the focused tests each task names; run the full suite once, in Task 7.
- **ASCII only** in source, comments and commit messages. Deck copy is English; `src/i18n.py` carries the es/en pair, unaccented (`"Preparacion para el Lanzamiento"` is the house style).
- **No new pip dependencies.** If a step seems to need one, stop and report.
- **Determinism:** no `set` iteration, no reliance on dict insertion order anywhere that reaches output. Sort explicitly.
- **The honesty rule, which this whole feature exists to enforce:** a declared hole beats an invented number. A tool that has not declared its columns is `no_declarado`, never `verde` by omission. A missing value is an empty cell, never a `0`.
- **Numbers are derived, never assumed.** Verified against `origin/main` @ 96dc6f9: **48 tools registered, 43 with `requires_data=True`, 0 declaring columns, 34 MCP specs**. Re-derive at Task 7 rather than trusting these.
- **`intake_scan` does NOT go on the MCP surface** (it reads a directory; exposing it anonymously is an arbitrary-file-read primitive). Task 7 documents the exclusion instead of adding a spec.

---

### Task 1: The data contract as data — declarations, completeness, anti-drift

**Files:**
- Modify: `scm_agent/registry.py` (two fields on `Tool`)
- Modify: `scm_agent/tools.py` (declarations on four tools)
- Test: `tests/test_tool_data_contract.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `Tool.required_columns: tuple[str, ...] = ()` and `Tool.optional_columns: tuple[str, ...] = ()`; declarations on `data_quality`, `abc_xyz`, `excess_obsolete`, `financial_kpis`; `UNDECLARED_TOOLS: frozenset[str]` in the test module.

**The declarations, verified against each `prepare()` — do not change these without re-reading the job:**

| Tool | `required_columns` | `optional_columns` | Verified in |
|---|---|---|---|
| `data_quality` | `("product_id",)` | `("name", "gtin", "unit_cost")` | `jobs/data_quality_job.py:76` — only the product column raises |
| `abc_xyz` | `("product_id", "quantity", "unit_cost")` | `()` | `jobs/abc_xyz_job.py:57` — all three are in `missing` |
| `excess_obsolete` | `("product_id", "on_hand", "daily_demand")` | `("unit_cost", "days_since_last_sale")` | `jobs/excess_obsolete_job.py:72` |
| `financial_kpis` | `("product_id", "cogs", "avg_inventory_value")` | `("gross_margin", "units_sold", "units_on_hand", "net_sales")` | `jobs/financial_kpis_job.py:103` |

Note `abc_xyz` requires `unit_cost` while the `ventas` profile treats it as optional. That is correct and stays: a sales file without cost yields a legitimate `rojo` for `abc_xyz`. Task 4 has a test for exactly that case.

- [ ] **Step 1: Write the failing test**

Create `tests/test_tool_data_contract.py`:

```python
"""The data contract every tool declares, and the guard that it stays true.

Two properties: (1) a tool that needs data eventually declares which columns it
needs, tracked against an explicit shrinking list; (2) a declared required column
is actually required -- remove it and the tool's own prepare() must refuse.
"""
import csv

import pytest

from scm_agent import llm, tools
from scm_agent.types import JobRequest

# Tools with requires_data=True that have NOT yet declared their columns.
# This list starts long and shrinks; it never grows. Removing a name here without
# adding a declaration makes test_every_data_tool_declares_its_columns fail.
UNDECLARED_TOOLS = frozenset({
    # populated in Step 3 from the real registry
})

DECLARED = ("data_quality", "abc_xyz", "excess_obsolete", "financial_kpis")

# One canonical value per column name we know how to synthesize.
_SAMPLE = {
    "product_id": "SKU-1", "name": "Widget", "gtin": "07350053850019",
    "unit_cost": "5.0", "quantity": "10", "on_hand": "100",
    "daily_demand": "2.5", "days_since_last_sale": "30",
    "cogs": "1000.0", "avg_inventory_value": "500.0", "gross_margin": "0.3",
    "units_sold": "80", "units_on_hand": "20", "net_sales": "1500.0",
    "date": "2026-01-01",
}


def _registry():
    return tools.build_default_registry()


def _write_csv(path, columns):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(columns)
        for i in range(12):
            writer.writerow([_SAMPLE[c] if c != "product_id" else f"SKU-{i}" for c in columns])
    return path


def test_declared_tools_carry_both_tuples():
    reg = _registry()
    for key in DECLARED:
        tool = reg.get(key)
        assert tool.required_columns, key
        assert isinstance(tool.required_columns, tuple), key
        assert isinstance(tool.optional_columns, tuple), key


def test_every_data_tool_declares_its_columns():
    """A requires_data tool either declares its columns or is on the undeclared list."""
    reg = _registry()
    missing = sorted(
        t.key for t in reg.list()
        if t.requires_data and not t.required_columns and t.key not in UNDECLARED_TOOLS
    )
    assert missing == [], f"undeclared and not listed: {missing}"


def test_undeclared_list_has_no_stale_entries():
    """A tool that declared its columns must be removed from the undeclared list."""
    reg = _registry()
    stale = sorted(k for k in UNDECLARED_TOOLS if reg.get(k) and reg.get(k).required_columns)
    assert stale == [], f"declared but still listed as undeclared: {stale}"


@pytest.mark.parametrize("key", DECLARED)
def test_every_declared_required_column_is_really_required(key, tmp_path):
    """Drop one required column at a time; the tool's own prepare() must refuse.

    This is the anti-drift guard. It covers the dangerous direction: a column the
    tool truly needs but nobody declared would produce a false 'verde'.
    """
    reg = _registry()
    tool = reg.get(key)
    provider = llm.RulesFallback()
    full = list(tool.required_columns) + list(tool.optional_columns)

    complete = _write_csv(tmp_path / f"{key}_full.csv", full)
    ok = tool.prepare(JobRequest(brief="scan", data_path=str(complete)), provider)
    assert ok.status == "ok", f"{key}: the complete fixture should prepare cleanly: {ok.messages}"

    for column in tool.required_columns:
        reduced = [c for c in full if c != column]
        path = _write_csv(tmp_path / f"{key}_no_{column}.csv", reduced)
        result = tool.prepare(JobRequest(brief="scan", data_path=str(path)), provider)
        assert result.status == "needs_data", (
            f"{key} declares '{column}' as required but prepare() accepted a file without it"
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_tool_data_contract.py -v`
Expected: FAIL — `Tool` has no attribute `required_columns`.

- [ ] **Step 3: Add the fields and the declarations**

In `scm_agent/registry.py`, add to the `Tool` dataclass, after `required_client_params`:

```python
    # The tool's data contract, stated as data rather than buried in prepare().
    # `required_columns` are canonical names whose absence makes prepare() refuse;
    # `optional_columns` improve the result when present. Both empty means the tool
    # has not declared yet -- `intake_scan` reports such a tool as `no_declarado`,
    # never as runnable. Declaring is done in batches; see
    # tests/test_tool_data_contract.py::UNDECLARED_TOOLS.
    required_columns: tuple[str, ...] = ()
    optional_columns: tuple[str, ...] = ()
```

In `scm_agent/tools.py`, add the two keyword arguments to each of the four `Tool(...)` constructions, using the table above verbatim. For example, in `data_quality_tool()`:

```python
        required_columns=("product_id",),
        optional_columns=("name", "gtin", "unit_cost"),
```

Then populate `UNDECLARED_TOOLS` in the test by deriving it, not by hand:

```bash
python -c "from scm_agent import tools; r=tools.build_default_registry(); print(sorted(t.key for t in r.list() if t.requires_data and not t.required_columns))"
```

Paste that list into the frozenset, one name per line, sorted. Record the count in the comment above it (expected 39 with 43 data tools and 4 declared — verify, do not assume).

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_tool_data_contract.py -v`
Expected: PASS. If `test_every_declared_required_column_is_really_required` fails for a tool, the DECLARATION is wrong, not the test — re-read that job's `prepare()` and fix the tuple. Do not weaken the assertion.

- [ ] **Step 5: Confirm nothing else broke**

Run: `python -m pytest tests/test_scm_agent.py tests/test_capability_smoke.py -v`
Expected: PASS. Adding defaulted fields to `Tool` should not disturb any existing tool.

- [ ] **Step 6: Commit**

```bash
git add scm_agent/registry.py scm_agent/tools.py tests/test_tool_data_contract.py
git commit -m "feat(intake_scan): declare the data contract on the Tool, with an anti-drift guard"
```

---

### Task 2: Dataset profiles and classification by column signature

**Files:**
- Create: `src/intake_scan.py`
- Test: `tests/test_intake_scan.py`

**Interfaces:**
- Consumes: `detect_columns` / `ALIASES` conventions from `jobs/intake.py` (read it; do not import pandas into `src/intake_scan.py`).
- Produces: `DatasetProfile` (frozen: `name: str`, `required: tuple[str, ...]`, `optional: tuple[str, ...]`); `PROFILES: tuple[DatasetProfile, ...]`; `FileColumns` (frozen: `filename: str`, `columns: tuple[str, ...]`, `coverage: dict[str, float]`, `n_rows: int`); `Classified` (frozen: `filename: str`, `profile: str | None`, `matched: tuple[str, ...]`, `missing: tuple[str, ...]`, `reason: str`); `UNCLASSIFIED: str`; `classify_file(file: FileColumns) -> Classified`; `classify_all(files) -> tuple[Classified, ...]`.

`coverage` maps a canonical column name to the fraction of non-empty cells (0.0-1.0). The job layer computes it; the engine only consumes it.

- [ ] **Step 1: Write the failing test**

Create `tests/test_intake_scan.py`:

```python
"""Tests for the pure intake_scan engine: profiles, circuit breakers, verdict."""
from src.intake_scan import UNCLASSIFIED, FileColumns, classify_all, classify_file


def _file(name, columns, coverage=None, n_rows=100):
    cov = coverage or {c: 1.0 for c in columns}
    return FileColumns(filename=name, columns=tuple(columns), coverage=cov, n_rows=n_rows)


def test_file_matching_all_required_columns_gets_its_profile():
    result = classify_file(_file("ventas.csv", ["date", "product_id", "quantity", "unit_cost"]))
    assert result.profile == "ventas"
    assert result.missing == ()


def test_file_missing_one_required_column_is_unclassified_with_the_reason():
    result = classify_file(_file("algo.csv", ["date", "product_id"]))
    assert result.profile is None
    assert result.reason == UNCLASSIFIED
    assert "quantity" in result.missing


def test_ambiguous_file_matching_two_profiles_is_not_guessed():
    """A file carrying every column of two profiles is reported, never resolved."""
    both = ["sku", "product_id", "on_hand", "daily_demand"]
    result = classify_file(_file("mezcla.csv", both))
    assert result.profile is None
    assert "ambiguous" in result.reason


def test_classification_is_deterministic_and_sorted_by_filename():
    files = [_file("z.csv", ["sku"]), _file("a.csv", ["product_id", "cogs", "avg_inventory_value"])]
    first = classify_all(files)
    second = classify_all(list(reversed(files)))
    assert [c.filename for c in first] == ["a.csv", "z.csv"]
    assert first == second
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_intake_scan.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.intake_scan'`.

- [ ] **Step 3: Write the implementation**

Create `src/intake_scan.py`:

```python
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
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_intake_scan.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/intake_scan.py tests/test_intake_scan.py
git commit -m "feat(intake_scan): dataset profiles and classification by column signature"
```

---

### Task 3: Circuit breakers with declared thresholds

**Files:**
- Modify: `src/intake_scan.py` (append)
- Test: `tests/test_intake_scan.py` (append)

**Interfaces:**
- Consumes: `FileColumns`, `Classified` from Task 2.
- Produces: `Thresholds` (frozen: `impossible_dates: float = 0.01`, `negative_quantities: float = 0.01`, `duplicate_rows: float = 0.05`, `orphan_keys: float = 0.05`, `column_coverage: float = 0.50`); `BreakerFinding` (frozen: `filename: str`, `breaker: str`, `n_affected: int`, `n_total: int`, `fraction: float`, `blocking: bool`, `detail: str`); `FileSignals` (frozen: `filename: str`, `n_impossible_dates: int`, `n_negative_quantities: int`, `n_duplicate_rows: int`, `n_orphan_keys: int`, `n_rows: int`, `orphans_evaluated: bool`); `run_breakers(signals, thresholds) -> tuple[BreakerFinding, ...]`; `blocking_files(findings) -> frozenset[str]`.

The engine takes counts, not data. The job layer computes the counts with pandas; this keeps the thresholds and the blocking rule testable without a filesystem.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_intake_scan.py`:

```python
from src.intake_scan import (
    BreakerFinding,
    FileSignals,
    Thresholds,
    blocking_files,
    run_breakers,
)


def _signals(**kwargs):
    base = dict(
        filename="ventas.csv", n_impossible_dates=0, n_negative_quantities=0,
        n_duplicate_rows=0, n_orphan_keys=0, n_rows=1000, orphans_evaluated=True,
    )
    base.update(kwargs)
    return FileSignals(**base)


def test_findings_carry_a_count_and_a_fraction_not_a_boolean():
    (finding,) = [f for f in run_breakers([_signals(n_duplicate_rows=3)], Thresholds())
                  if f.breaker == "duplicate_rows"]
    assert finding.n_affected == 3
    assert finding.n_total == 1000
    assert round(finding.fraction, 4) == 0.003
    assert finding.blocking is False


def test_a_breaker_over_its_threshold_blocks():
    findings = run_breakers([_signals(n_impossible_dates=20)], Thresholds())
    dates = next(f for f in findings if f.breaker == "impossible_dates")
    assert dates.fraction == 0.02
    assert dates.blocking is True
    assert blocking_files(findings) == frozenset({"ventas.csv"})


def test_thresholds_are_configurable():
    findings = run_breakers([_signals(n_impossible_dates=20)], Thresholds(impossible_dates=0.05))
    assert next(f for f in findings if f.breaker == "impossible_dates").blocking is False


def test_orphans_not_evaluated_are_declared_not_reported_as_zero():
    findings = run_breakers([_signals(orphans_evaluated=False)], Thresholds())
    orphans = next(f for f in findings if f.breaker == "orphan_keys")
    assert orphans.blocking is False
    assert "not evaluated" in orphans.detail
    assert orphans.n_affected == 0
    assert orphans.fraction == 0.0


def test_empty_file_never_divides_by_zero():
    findings = run_breakers([_signals(n_rows=0)], Thresholds())
    assert all(f.fraction == 0.0 for f in findings)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_intake_scan.py -v`
Expected: FAIL — `ImportError: cannot import name 'run_breakers'`.

- [ ] **Step 3: Write the implementation**

Append to `src/intake_scan.py`:

```python
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
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_intake_scan.py -v`
Expected: PASS (9 tests).

- [ ] **Step 5: Commit**

```bash
git add src/intake_scan.py tests/test_intake_scan.py
git commit -m "feat(intake_scan): circuit breakers with declared, configurable thresholds"
```

---

### Task 4: The four-state verdict, the ranking and the outcome

**Files:**
- Modify: `src/intake_scan.py` (append)
- Test: `tests/test_intake_scan.py` (append)

**Interfaces:**
- Consumes: everything from Tasks 2-3, plus `Tool.required_columns` / `optional_columns` from Task 1.
- Produces: `GREEN = "verde"`, `YELLOW = "amarillo"`, `RED = "rojo"`, `UNDECLARED = "no_declarado"`; `ToolVerdict` (frozen: `tool_key: str`, `state: str`, `missing_required: tuple[str, ...]`, `missing_optional: tuple[str, ...]`, `blocked_by: tuple[str, ...]`, `source_files: tuple[str, ...]`); `ColumnGap` (frozen: `column: str`, `unlocks: int`, `tools: tuple[str, ...]`); `ScanReport` (frozen: `classified`, `findings`, `verdicts`, `gaps`, `n_files_read`, `n_unclassified`, `n_tools_evaluated`, `n_tools_total`, `outcome`, `summary`); `verdicts_for(available, blocked, tools) -> tuple[ToolVerdict, ...]`; `rank_gaps(verdicts) -> tuple[ColumnGap, ...]`; `build_report(...) -> ScanReport`.

`available` is `dict[str, set[str]]`: canonical column -> the filenames that carry it **with coverage at or above the threshold**. Columns present but under-covered are excluded by the job layer and reported separately, per the spec's "presence is not having the column" rule.

**A tool's state, precisely:**
- `UNDECLARED` when `required_columns` is empty. Checked first — it can never fall through to green.
- `RED` when a required column is unavailable, or when every file carrying one of its required columns is blocked. `blocked_by` names the files.
- `YELLOW` when all required are available and at least one optional is not.
- `GREEN` otherwise.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_intake_scan.py`:

```python
from src.guided import EXECUTED, OPTIONS
from src.intake_scan import (
    GREEN,
    RED,
    UNDECLARED,
    YELLOW,
    build_report,
    rank_gaps,
    verdicts_for,
)


class _FakeTool:
    def __init__(self, key, required=(), optional=(), requires_data=True):
        self.key = key
        self.required_columns = tuple(required)
        self.optional_columns = tuple(optional)
        self.requires_data = requires_data


def test_the_four_states():
    tools_ = [
        _FakeTool("green_tool", ("product_id",), ("unit_cost",)),
        _FakeTool("yellow_tool", ("product_id",), ("gtin",)),
        _FakeTool("red_tool", ("product_id", "cogs")),
        _FakeTool("undeclared_tool"),
    ]
    available = {"product_id": {"maestro.csv"}, "unit_cost": {"maestro.csv"}}
    states = {v.tool_key: v.state for v in verdicts_for(available, frozenset(), tools_)}
    assert states == {
        "green_tool": GREEN, "yellow_tool": YELLOW,
        "red_tool": RED, "undeclared_tool": UNDECLARED,
    }


def test_an_undeclared_tool_is_never_green_even_with_every_column_present():
    (verdict,) = verdicts_for({"product_id": {"a.csv"}}, frozenset(), [_FakeTool("x")])
    assert verdict.state == UNDECLARED


def test_a_blocked_file_cannot_support_a_green():
    tools_ = [_FakeTool("t", ("product_id",))]
    (verdict,) = verdicts_for({"product_id": {"malo.csv"}}, frozenset({"malo.csv"}), tools_)
    assert verdict.state == RED
    assert verdict.blocked_by == ("malo.csv",)


def test_abc_xyz_style_tool_goes_red_without_unit_cost():
    """The real case: a sales file without cost is a legitimate red for abc_xyz."""
    tools_ = [_FakeTool("abc_xyz", ("product_id", "quantity", "unit_cost"))]
    available = {"product_id": {"ventas.csv"}, "quantity": {"ventas.csv"}}
    (verdict,) = verdicts_for(available, frozenset(), tools_)
    assert verdict.state == RED
    assert verdict.missing_required == ("unit_cost",)


def test_gaps_rank_by_how_many_tools_each_column_unlocks():
    verdicts = verdicts_for(
        {"product_id": {"a.csv"}},
        frozenset(),
        [
            _FakeTool("t1", ("product_id", "unit_cost")),
            _FakeTool("t2", ("product_id", "unit_cost")),
            _FakeTool("t3", ("product_id", "cogs")),
        ],
    )
    gaps = rank_gaps(verdicts)
    assert [g.column for g in gaps] == ["unit_cost", "cogs"]
    assert gaps[0].unlocks == 2
    assert gaps[0].tools == ("t1", "t2")


def test_ties_break_by_column_name_so_two_runs_agree():
    verdicts = verdicts_for(
        {},
        frozenset(),
        [_FakeTool("t1", ("zeta",)), _FakeTool("t2", ("alfa",))],
    )
    assert [g.column for g in rank_gaps(verdicts)] == ["alfa", "zeta"]


def test_report_with_gaps_offers_options():
    tools_ = [_FakeTool("t", ("product_id", "unit_cost"))]
    report = build_report(
        classified=(), findings=(), tools=tools_,
        available={"product_id": {"a.csv"}}, blocked=frozenset(),
        n_files_read=1, n_unclassified=0,
    )
    assert report.outcome.status == OPTIONS
    assert "unit_cost" in report.outcome.options[0].label


def test_report_with_nothing_to_ask_for_is_executed_not_an_empty_menu():
    tools_ = [_FakeTool("t", ("product_id",))]
    report = build_report(
        classified=(), findings=(), tools=tools_,
        available={"product_id": {"a.csv"}}, blocked=frozenset(),
        n_files_read=1, n_unclassified=0,
    )
    assert report.outcome.status == EXECUTED


def test_report_counts_evaluated_against_total():
    tools_ = [_FakeTool("declared", ("product_id",)), _FakeTool("undeclared")]
    report = build_report(
        classified=(), findings=(), tools=tools_,
        available={"product_id": {"a.csv"}}, blocked=frozenset(),
        n_files_read=1, n_unclassified=0,
    )
    assert (report.n_tools_evaluated, report.n_tools_total) == (1, 2)
    assert "1 of 2" in report.summary
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_intake_scan.py -v`
Expected: FAIL — `ImportError: cannot import name 'verdicts_for'`.

- [ ] **Step 3: Write the implementation**

Add to the imports at the top of `src/intake_scan.py`:

```python
from src.guided import ExecutionOption, GuidedOutcome, Residual, as_executed, as_options
```

Verify both helper signatures before using them — `as_options` takes a summary as its FIRST positional argument and raises on an empty options list:

```bash
python -c "import inspect, src.guided as g; print(inspect.signature(g.as_options)); print(inspect.signature(g.as_executed))"
```

Then append:

```python
GREEN = "verde"
YELLOW = "amarillo"
RED = "rojo"
UNDECLARED = "no_declarado"

_TOP_GAPS = 3


@dataclass(frozen=True)
class ToolVerdict:
    """Whether one tool could run on this folder, and what stops it if not."""

    tool_key: str
    state: str
    missing_required: tuple[str, ...] = ()
    missing_optional: tuple[str, ...] = ()
    blocked_by: tuple[str, ...] = ()
    source_files: tuple[str, ...] = ()


@dataclass(frozen=True)
class ColumnGap:
    """One missing column, and how much asking for it would buy."""

    column: str
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
        blocked_by: set[str] = set()
        sources: set[str] = set()
        for column in tool.required_columns:
            files = available.get(column, set())
            usable = files - blocked
            if not usable:
                missing_required.append(column)
                blocked_by |= (files & blocked)
            else:
                sources |= usable

        if missing_required:
            out.append(ToolVerdict(
                tool.key, RED, tuple(missing_required), (), tuple(sorted(blocked_by)),
                tuple(sorted(sources)),
            ))
            continue

        missing_optional = tuple(
            c for c in tool.optional_columns if not (available.get(c, set()) - blocked)
        )
        state = YELLOW if missing_optional else GREEN
        out.append(ToolVerdict(
            tool.key, state, (), missing_optional, (), tuple(sorted(sources))
        ))
    return tuple(out)


def rank_gaps(verdicts: tuple[ToolVerdict, ...]) -> tuple[ColumnGap, ...]:
    """Missing required columns, ranked by how many tools each one would unlock.

    Ties break on the column name so two runs over the same folder agree.
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


@dataclass(frozen=True)
class ScanReport:
    """The whole audit: what we read, what we found, what can run, what to ask for."""

    classified: tuple
    findings: tuple
    verdicts: tuple[ToolVerdict, ...]
    gaps: tuple[ColumnGap, ...]
    n_files_read: int
    n_unclassified: int
    n_tools_evaluated: int
    n_tools_total: int
    outcome: GuidedOutcome
    summary: str


def _outcome(gaps: tuple[ColumnGap, ...], verdicts: tuple[ToolVerdict, ...]) -> GuidedOutcome:
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
    if not gaps:
        n_green = sum(1 for v in verdicts if v.state == GREEN)
        return as_executed(
            f"The folder supports every declared tool ({n_green} green); nothing to request.",
            residuals=[residual],
        )
    options = [
        ExecutionOption(
            label=f"Request '{g.column}' from the client",
            summary=(
                f"Adding '{g.column}' unlocks {g.unlocks} tool(s): {', '.join(g.tools)}."
            ),
            score=float(g.unlocks),
            recommended=rank == 0,
            action=f"request_column:{g.column}",
            tradeoffs="One column, one ask; the client can usually export it from the same system.",
        )
        for rank, g in enumerate(gaps[:_TOP_GAPS])
    ]
    return as_options(
        "Missing columns, ranked by how many tools each one unlocks.",
        options,
        residuals=[residual],
    )


def build_report(
    *, classified, findings, tools, available, blocked, n_files_read, n_unclassified,
) -> ScanReport:
    """Assemble the audit. Evaluated-vs-total travels in the summary, always."""
    verdicts = verdicts_for(available, blocked, tools)
    gaps = rank_gaps(verdicts)
    evaluated = sum(1 for v in verdicts if v.state != UNDECLARED)
    total = len(verdicts)
    counts = {state: sum(1 for v in verdicts if v.state == state) for state in (GREEN, YELLOW, RED)}
    top = f"asking for '{gaps[0].column}' unlocks {gaps[0].unlocks}" if gaps else "nothing to request"
    summary = (
        f"Read {n_files_read} file(s), {n_unclassified} unclassified; evaluated "
        f"{evaluated} of {total} registered tool(s): {counts[GREEN]} green, "
        f"{counts[YELLOW]} yellow, {counts[RED]} red; {top}."
    )
    return ScanReport(
        classified=tuple(classified), findings=tuple(findings), verdicts=verdicts, gaps=gaps,
        n_files_read=n_files_read, n_unclassified=n_unclassified,
        n_tools_evaluated=evaluated, n_tools_total=total,
        outcome=_outcome(gaps, verdicts), summary=summary,
    )
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_intake_scan.py -v`
Expected: PASS (18 tests). If `as_executed` has a different signature than assumed, adapt the call — do not modify `src/guided.py`.

- [ ] **Step 5: Commit**

```bash
git add src/intake_scan.py tests/test_intake_scan.py
git commit -m "feat(intake_scan): four-state verdict and the ranked column gaps"
```

---

### Task 5: The job — walk the folder, measure, gate

**Files:**
- Create: `jobs/intake_scan_job.py`
- Test: `tests/test_intake_scan_job.py`

**Interfaces:**
- Consumes: everything in `src/intake_scan.py`.
- Produces: `prepare(data_path, params=None) -> FolderIntake`; `run(folder, params=None) -> ScanReport`; `verify(report) -> list[str]`; `FolderIntake` (frozen: `files: tuple[FileColumns, ...]`, `signals: tuple[FileSignals, ...]`, `classified: tuple[Classified, ...]`, `available: dict[str, set[str]]`, `blocked: frozenset[str]`, `ignored: tuple[str, ...]`, `under_covered: tuple[tuple[str, str, float], ...]`); `READABLE_SUFFIXES`; `MAX_UNCLASSIFIED_SHARE = 0.50`.

`prepare` reads each file, maps its headers to canonical names with the alias table (reuse the normalisation approach of `jobs/intake.py`'s `detect_columns`; do not duplicate its alias table — import it if it is importable, otherwise extend it there), measures coverage per canonical column, computes the four breaker counts, and builds `available` from columns whose coverage reaches the threshold. A column present but under-covered goes into `under_covered` and is NOT in `available`.

**QA gate** — `verify` returns an issue when:
1. no file was classified;
2. more than `MAX_UNCLASSIFIED_SHARE` of readable files are unclassified;
3. a tool is `GREEN` while one of its `source_files` is in `blocked`;
4. the ranked gaps name a tool whose verdict is `UNDECLARED`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_intake_scan_job.py`:

```python
"""Tests for the intake_scan job: folder walk, coverage, the QA gate."""
import pandas as pd
import pytest

from jobs import intake_scan_job
from src.intake_scan import GREEN, RED, UNDECLARED


def _folder(tmp_path, **frames):
    d = tmp_path / "carpeta"
    d.mkdir()
    for name, frame in frames.items():
        pd.DataFrame(frame).to_csv(d / name, index=False)
    return d


def _maestro(n=20):
    return {"sku": [f"SKU-{i}" for i in range(n)], "name": ["x"] * n, "unit_cost": [5.0] * n}


def _ventas(n=20):
    return {
        "date": ["2026-01-01"] * n,
        "product_id": [f"SKU-{i}" for i in range(n)],
        "quantity": [10] * n,
        "unit_cost": [5.0] * n,
    }


def test_prepare_classifies_the_folder(tmp_path):
    folder = _folder(tmp_path, **{"maestro.csv": _maestro(), "ventas.csv": _ventas()})
    intake = intake_scan_job.prepare(str(folder))
    assert {c.profile for c in intake.classified} == {"maestro", "ventas"}
    assert "product_id" in intake.available


def test_prepare_refuses_a_folder_with_nothing_readable(tmp_path):
    d = tmp_path / "vacia"
    d.mkdir()
    (d / "notas.txt").write_text("hola", encoding="utf-8")
    with pytest.raises(ValueError, match="notas.txt"):
        intake_scan_job.prepare(str(d))


def test_a_column_present_but_empty_is_not_available(tmp_path):
    frame = _maestro()
    frame["unit_cost"] = [None] * len(frame["sku"])
    folder = _folder(tmp_path, **{"maestro.csv": frame})
    intake = intake_scan_job.prepare(str(folder))
    assert "unit_cost" not in intake.available
    assert any(col == "unit_cost" for _, col, _ in intake.under_covered)


def test_gate_blocks_when_nothing_classified(tmp_path):
    folder = _folder(tmp_path, **{"raro.csv": {"alfa": [1, 2], "beta": [3, 4]}})
    report = intake_scan_job.run(intake_scan_job.prepare(str(folder)))
    assert any("no file" in i for i in intake_scan_job.verify(report))


def test_gate_blocks_when_most_files_are_unclassified(tmp_path):
    folder = _folder(tmp_path, **{
        "maestro.csv": _maestro(),
        "raro1.csv": {"alfa": [1]}, "raro2.csv": {"beta": [1]}, "raro3.csv": {"gama": [1]},
    })
    report = intake_scan_job.run(intake_scan_job.prepare(str(folder)))
    assert any("unclassified" in i for i in intake_scan_job.verify(report))


def test_healthy_folder_passes_the_gate_and_evaluates_the_declared_tools(tmp_path):
    folder = _folder(tmp_path, **{"maestro.csv": _maestro(), "ventas.csv": _ventas()})
    report = intake_scan_job.run(intake_scan_job.prepare(str(folder)))
    assert intake_scan_job.verify(report) == []
    states = {v.tool_key: v.state for v in report.verdicts}
    assert states["data_quality"] == GREEN
    assert states["abc_xyz"] == GREEN
    assert states["excess_obsolete"] == RED  # no stock file in this folder
    assert states["risk"] == UNDECLARED
    assert report.n_tools_evaluated == 4


def test_report_always_carries_evaluated_over_total(tmp_path):
    folder = _folder(tmp_path, **{"maestro.csv": _maestro(), "ventas.csv": _ventas()})
    report = intake_scan_job.run(intake_scan_job.prepare(str(folder)))
    assert f"{report.n_tools_evaluated} of {report.n_tools_total}" in report.summary
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_intake_scan_job.py -v`
Expected: FAIL — `ImportError: cannot import name 'intake_scan_job'`.

- [ ] **Step 3: Write the implementation**

Create `jobs/intake_scan_job.py`. Follow the five-stage shape of `jobs/disruption_scan_job.py`; in THIS task write only `prepare` / `run` / `verify` (deliverables land in Task 6). Key points the implementer must honour:

- `prepare(data_path)` requires `data_path` to be an existing directory; a file path raises `ValueError` naming what was passed.
- Readable suffixes: `.csv`, `.xlsx`, `.xls`. Every other file goes into `ignored` **by name** — never silently skipped.
- If no readable file exists, raise `ValueError` listing what WAS found (the test asserts the ignored filename appears in the message).
- Canonical-name mapping reuses the alias/normalisation convention of `jobs/intake.py`. Do not copy its alias table into this module — import it, or extend it there and import.
- Coverage per canonical column = non-null, non-blank cells / row count. A column at or above `Thresholds.column_coverage` goes into `available`; below, into `under_covered` as `(filename, column, coverage)`.
- The four breaker counts are computed here with pandas and handed to the engine as `FileSignals`. Orphans are only evaluated when a `maestro` file was classified; otherwise `orphans_evaluated=False`.
- `run` calls `build_report` with the registry's tools: `from scm_agent import tools; tools.build_default_registry().list()`. Import inside the function to avoid a circular import at module load.
- `verify` implements the four gate conditions listed in this task's Interfaces block.

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_intake_scan_job.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Run the engine tests too**

Run: `python -m pytest tests/test_intake_scan.py tests/test_intake_scan_job.py tests/test_tool_data_contract.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add jobs/intake_scan_job.py tests/test_intake_scan_job.py
git commit -m "feat(intake_scan): folder walk, coverage measurement and the QA gate"
```

---

### Task 6: Deliverables and measured L3 anchors

**Files:**
- Modify: `jobs/intake_scan_job.py` (append `write_operational`, `build_deck`)
- Modify: `scm_agent/citation_gate.py` (one `TOOL_CONCEPTS` entry, with measured numbers)
- Modify: `tests/test_packages_citations.py` (only if the measurement says intake_scan is a documented zero)
- Test: `tests/test_intake_scan_job.py` (append)

**Interfaces:**
- Produces: `write_operational(report, out_dir, client="Client") -> dict[str, Path]` with keys `"csv"` (one row per tool verdict) and `"files_csv"` (one row per file: profile, rows, and each breaker's fraction); `build_deck(report, *, client, prepared, citations, confidence, lang="en") -> Deliverable`.

- [ ] **Step 1: Measure the candidate anchors**

Same discipline as `flow_map`: measure before fixing, write the measured numbers into the comment in the house style of the `risk` and `disruption_scan` entries. Reject any anchor whose 2-hop closure exceeds ~40% of the graph (the shared-book-hub loophole documented at the `risk` entry, where `supply_chain_risk` reached 46.3% and was rejected).

Candidates to check for existence, in order of preference: `master_data_management`, `data_quality_dimensions`, `information_sharing`, `step_product_data_standard`. Note `data_quality` already anchors on `step_product_data_standard` and its comment documents that it degrades to zero on every real run — read that comment before reusing the anchor.

Measure with the real grounding call, using the title and keywords Task 7 will register:
```bash
python -c "from scm_agent.knowledge import KnowledgeBase; kb=KnowledgeBase(); [print(a, kb.node_exists(a)) for a in ('master_data_management','data_quality_dimensions','information_sharing','step_product_data_standard')]"
```

If no candidate both exists and stays under the closure limit, do NOT invent one: leave `intake_scan` out of `TOOL_CONCEPTS`, add it to `_CORRECTLY_ZERO_TOOLS` in `tests/test_packages_citations.py` with a one-line reason, and say so in the commit message. `data_quality` sets this precedent.

**Write the measured numbers — node counts and percentages — into the comment. Not adjectives.**

- [ ] **Step 2: Write the failing deliverable test**

Append to `tests/test_intake_scan_job.py`:

```python
def test_write_operational_emits_both_csvs_with_the_undeclared_rows(tmp_path):
    folder = _folder(tmp_path, **{"maestro.csv": _maestro(), "ventas.csv": _ventas()})
    report = intake_scan_job.run(intake_scan_job.prepare(str(folder)))
    paths = intake_scan_job.write_operational(report, tmp_path / "out", client="Acme")
    assert paths["csv"].exists() and paths["files_csv"].exists()
    body = paths["csv"].read_text(encoding="utf-8")
    assert "no_declarado" in body
    assert "data_quality" in body


def test_deck_states_the_coverage_as_a_fraction_not_as_a_verdict(tmp_path):
    folder = _folder(tmp_path, **{"maestro.csv": _maestro(), "ventas.csv": _ventas()})
    report = intake_scan_job.run(intake_scan_job.prepare(str(folder)))
    deck = intake_scan_job.build_deck(report, client="Acme", citations=("Fuente X",))
    assert deck.client == "Acme"
    assert deck.options == tuple(report.outcome.options)
    assert any(str(report.n_tools_total) in k.value or str(report.n_tools_total) in k.rationale
               for k in deck.kpis)
    assert "undeclared" in " ".join(f.detail for f in deck.findings).lower()
```

- [ ] **Step 3: Run it to verify it fails**

Run: `python -m pytest tests/test_intake_scan_job.py -v`
Expected: FAIL — `write_operational` does not exist.

- [ ] **Step 4: Write `write_operational` and `build_deck`**

`write_operational` writes two CSVs via `src.export.write_summary_csv`:
- `intake_scan_tools.csv` — one row per tool: `tool_key`, `state`, `missing_required`, `missing_optional`, `blocked_by`, `source_files`. Undeclared tools appear with `state=no_declarado` and empty cells, never zeros.
- `intake_scan_files.csv` — one row per file: `filename`, `profile` (empty when unclassified), `reason`, `n_rows`, and one column per breaker holding its fraction rounded to 4 places.

`build_deck` mirrors `jobs/disruption_scan_job.py`'s structure and must contain, non-negotiably:
- a finding naming the top-ranked gap and how many tools it unlocks;
- a finding naming the undeclared tools as a declared limit of the scan, with the count;
- a finding per blocking breaker, with its count and fraction;
- KPIs carrying **evaluated over total** (e.g. `"4 of 48"`), files read, files unclassified;
- a `residual` stating that the scan judges the shape of the data, not its truth: a perfectly-shaped file can still hold wrong numbers.

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/test_intake_scan_job.py tests/test_citation_gate.py tests/test_packages_citations.py -v`
Expected: PASS. Note `test_citation_gate.py::test_every_registered_tool_has_a_concept_map` asserts exact set equality between `tools.py` keys and `TOOL_CONCEPTS`; anchoring here before registering in Task 7 turns it RED in the interim. That is expected and Task 7 closes it — record it, do not "fix" it by deleting the anchor.

- [ ] **Step 6: Commit**

```bash
git add jobs/intake_scan_job.py scm_agent/citation_gate.py tests/test_intake_scan_job.py tests/test_packages_citations.py
git commit -m "feat(intake_scan): deliverables and measured L3 anchors"
```

---

### Task 7: Wiring, counts, and the declared MCP exclusion

**Files:**
- Modify: `scm_agent/tools.py` (register `intake_scan`)
- Modify: `src/i18n.py`, `README.md`, `CLAUDE.md`, `docs/MCP_SERVER.md`
- Create: `examples/run_intake_scan.py`
- Test: `tests/test_intake_scan_tool.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_intake_scan_tool.py` covering: the tool is registered; a folder brief routes to `intake_scan`; a `data_quality` brief is NOT stolen (assert `job_type != "intake_scan"` AND that it still routes to `data_quality`); `prepare` without data returns `needs_data` mentioning a folder; and an end-to-end `prepare → run → qa → deliver` over a temp folder that asserts `qa == []` and both CSVs exist.

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_intake_scan_tool.py -v`
Expected: FAIL — `reg.get("intake_scan")` is None.

- [ ] **Step 3: Register the tool**

Add `intake_scan_tool()` to `scm_agent/tools.py` following the file's section-comment style, with `requires_data=True`, `prepare` returning `needs_data` when `request.data_path` is absent or is not a directory, `qa=lambda r: intake_scan_job.verify(r)`, `deliver=...write_operational`, a `deck=` lambda, and `options=lambda r: r.outcome`. Then `reg.register(intake_scan_tool())`.

Intent keywords: `"intake"`, `"data audit"`, `"auditoria de datos"`, `"que puedo correr"`, `"what can i run"`, `"escanear la carpeta"`, `"scan the folder"`, `"aptitud del dato"`, `"data readiness"`, `"que datos necesito"`, `"what data do i need"`, `"missing columns"`. **Do not add generic terms like `"data"` or `"quality"`** — `data_quality` owns those and stealing its briefs is a regression the test pins.

Note `intake_scan` declares no `required_columns` of its own (it consumes a folder, not a canonical table), so add its key to `UNDECLARED_TOOLS` in `tests/test_tool_data_contract.py` with a comment explaining that this tool's input is a directory, not a table — it is an intentional permanent entry, not a pending one.

- [ ] **Step 4: i18n, example, and the MCP exclusion**

- `src/i18n.py`: `"intake_scan": {"es": "Escaneo de Aptitud del Dato", "en": "Data Readiness Scan"}` — ASCII, unaccented.
- `examples/run_intake_scan.py`: takes a folder path, prints the summary, the per-tool table and the ranked gaps, exits 1 when the gate blocks (mirror `examples/run_flow_map.py`).
- `docs/MCP_SERVER.md`: add `intake_scan` to the "What's NOT here yet" section under the **deliberately excluded** group, with the reason: it takes a directory path and reads every file in it, so exposing it on the anonymous remote surface would be an arbitrary-file-read primitive. Cross-reference the `org_friction` `*_path` guard. **Do not add an `MCPToolSpec`.** The surface count stays at whatever `len(TOOL_SPECS)` currently is.

- [ ] **Step 5: Update every count, derived not guessed**

```bash
python -c "from scm_agent import tools; r=tools.build_default_registry(); print('tools', len(r.list())); print('data', sum(1 for t in r.list() if t.requires_data)); print('declared', sum(1 for t in r.list() if t.required_columns))"
python -c "from webapp.mcp_tool_specs import TOOL_SPECS; print('mcp', len(TOOL_SPECS))"
```

Update `README.md` (the "N agent-routable capabilities" line, "the N registered tools", the "All N capabilities" heading, the donut alt text **and its per-area breakdown, whose parts must sum to the total**), `CLAUDE.md`, and `docs/MCP_SERVER.md`'s "N of M" ratio. Add `intake_scan` to the README capability table. Also bump the hard-coded registry counts in `tests/test_scm_agent.py`, `tests/test_price_watch_tool.py` and `tests/test_capability_smoke.py` (which needs a bucket row for the new tool). Never relax an exact count to `>=`.

- [ ] **Step 6: Run the full suite — once**

Run: `python -m pytest -q`
Expected: green. `test_every_registered_tool_has_a_concept_map` must now pass; it was RED after Task 6 by design.

- [ ] **Step 7: Lint**

Run: `python -m ruff check src/intake_scan.py jobs/intake_scan_job.py scm_agent/tools.py scm_agent/registry.py examples/run_intake_scan.py tests/test_intake_scan.py tests/test_intake_scan_job.py tests/test_intake_scan_tool.py tests/test_tool_data_contract.py`
Expected: no findings.

- [ ] **Step 8: Commit**

```bash
git add -u && git add examples/run_intake_scan.py tests/test_intake_scan_tool.py
git commit -m "feat(intake_scan): register the tool, declare the MCP exclusion, refresh counts"
```

---

## Follow-ups, declared not done

- Declaring `required_columns` for the remaining ~39 data tools. Batches, each its own PR; the shrinking `UNDECLARED_TOOLS` list is the tracker.
- Repairing data. `intake_scan` diagnoses and prioritises; it does not clean.
- Exposing `intake_scan` over MCP (would need a path guard first).
- Profiles beyond the Diagnóstico four (movements, purchases, suppliers).

## Self-Review Notes

Checked against `docs/superpowers/specs/2026-07-31-intake-scan-design.md`:

- Declaration on the `Tool`, by batches, with an explicit undeclared list — Task 1.
- The anti-drift guard the spec calls the defence of the whole design — Task 1, Step 1, `test_every_declared_required_column_is_really_required`. It goes through `Tool.prepare(JobRequest)`, uniform across all four tools, rather than each job's private helpers.
- The four declarations were read out of each `prepare()` and are recorded with their file:line — Task 1's table. **`abc_xyz` requires `unit_cost` while the `ventas` profile treats it as optional; that asymmetry is real, is preserved, and has its own test in Task 4.**
- Classification by signature, no LLM, ambiguity and no-match both refused — Task 2.
- Five circuit breakers with counts and fractions, configurable thresholds, orphans declared as not-evaluated when there is no master — Task 3. Coverage is deliberately not a breaker; it feeds the presence rule in Task 5.
- Four states with `no_declarado` checked first — Task 4, `test_an_undeclared_tool_is_never_green_even_with_every_column_present`.
- The ranking, its deterministic tie-break, and the executed-not-empty-menu case — Task 4.
- Presence ≠ having the column — Task 5, `test_a_column_present_but_empty_is_not_available`.
- All four gate conditions — Task 5.
- Evaluated-over-total travelling in the summary, the CSV and the deck — Tasks 4, 5 and 6.
- Anchors measured before fixed, with the documented-zero fallback — Task 6.
- The MCP exclusion declared rather than silently omitted — Task 7, Step 4.
- Counts derived, breakdown made to sum — Task 7, Step 5.
