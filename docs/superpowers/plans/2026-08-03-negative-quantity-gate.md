# Negative-Quantity Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a negative quantity in a client data file produce a visible objection instead of a silently-delivered plan, across the five tools that ingest quantities.

**Architecture:** A new shared, side-effect-free counter (`jobs/quantity_audit.py`) is computed at `prepare()` time, carried on the payload into `run()`, hung on the report object, and gated in each tool's `qa()`. That routes the signal into `qa_issues`, the only channel the orchestrator's "QA fails => no deliverable" gate already respects — so **no orchestrator change is needed** for the blocking behavior. Sub-threshold negatives stay non-blocking but become visible via `guided.residuals` plus a new CLI print.

**Tech Stack:** Python 3.11+, pandas, pytest. `PYTHONPATH=.` required for every test command.

## Global Constraints

- Spec of record: `docs/superpowers/specs/2026-08-03-negative-quantity-gate-design.md`.
- **ASCII-only in console prints** (Windows cp1252 breaks on em dashes). Markdown files written with `encoding="utf-8"` may use non-ASCII.
- **Never add a module-level import of an optional-extra dep** to anything on the app boot chain. `jobs/quantity_audit.py` imports only `dataclasses`, `math` and `pandas` — all core deps.
- Tests run with `PYTHONPATH=.`; lint scope is `ruff check src tests examples` (note: `jobs/` and `scm_agent/` are outside the lint scope, but match surrounding style anyway).
- Immutability: every new dataclass is `@dataclass(frozen=True)`.
- Workflow: feature branch -> draft PR -> CI green -> squash-merge. Never push straight to `main`.
- Default threshold value: `0.02`. Override key: `params["negative_quantity_threshold"]`.
- **Concurrency warning:** other sessions share this worktree. Run `git status --short` immediately before every `git add`, and **always `git add` with an explicit pathspec** — never `git add -A` or a bare `git commit -a`.

## Pre-flight

Before Task 1, create the branch:

```bash
git checkout -b feat/negative-quantity-gate
```

## File Structure

| File | Responsibility |
|---|---|
| `jobs/quantity_audit.py` | **New.** The `QuantityAudit` dataclass + `audit_quantities()`. Counts, never mutates. |
| `tests/test_quantity_audit.py` | **New.** Unit tests for the module above. |
| `tests/test_negative_quantity_gate.py` | **New.** Per-tool + end-to-end regression tests. |
| `jobs/qa.py:45` | Inventory gate — reads the existing `report.intake_quality`. |
| `jobs/deliverables.py:297` | Inventory `report.md` — new "Data quality" section. |
| `jobs/inventory_optimization.py:64` | `JobReport` gains `negative_quantity_threshold`. |
| `jobs/abc_xyz_job.py` | Count in `aggregate_skus`, hang on `AbcXyzReport`, gate in `verify`. |
| `jobs/forecast_job.py` | Count in `prepare_records` **before** the clip, hang on `ForecastJobReport`, gate in `verify`. |
| `jobs/excess_obsolete_job.py` | Count `on_hand` with `kind="level"`, hang on `EOReport`, gate in `verify`. |
| `jobs/ddmrp_job.py:119` | Name the negative ADU in the existing issue message. |
| `scm_agent/tool_options.py` | New shared `_apply_quantity_audit()`; wired into 4 options builders. |
| `scm_agent/tools.py` | Payload plumbing for 4 tools; `forecast` options lambda rewired. |
| `examples/run_agent.py:66` | Print `guided.residuals` + the escalation. |

**Already correct, do not change:** `webapp/app.py:680` already serializes the whole `guided` object (residuals and escalation included) via `asdict`. The spec's §4.4 item 3 is already satisfied; no webapp work in this plan.

---

### Task 1: The `QuantityAudit` module

**Files:**
- Create: `jobs/quantity_audit.py`
- Test: `tests/test_quantity_audit.py`

**Interfaces:**
- Consumes: nothing (leaf module).
- Produces: `QuantityAudit` (frozen dataclass) and `audit_quantities(df, *, quantity_col, product_col=None, kind="flow", threshold=0.02) -> QuantityAudit`. Fields: `column: str`, `kind: str`, `n_rows: int`, `n_negative: int`, `negative_total: float`, `n_entities_net_negative: int`, `threshold: float`. Properties: `negative_fraction: float`, `is_material: bool`. Methods: `issues() -> list[str]`, `disclosure() -> str`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_quantity_audit.py`:

```python
"""Unit tests for the shared negative-quantity counter (jobs/quantity_audit.py)."""

import pandas as pd

from jobs.quantity_audit import QuantityAudit, audit_quantities


def _df(quantities, products=None):
    products = products or ["A"] * len(quantities)
    return pd.DataFrame({"product_id": products, "quantity": quantities})


def test_clean_flow_data_is_not_material():
    audit = audit_quantities(_df([10, 20, 30]), quantity_col="quantity", product_col="product_id")
    assert audit.n_negative == 0
    assert audit.negative_fraction == 0.0
    assert audit.is_material is False
    assert audit.issues() == []


def test_flow_below_threshold_is_counted_but_not_material():
    # 1 negative in 200 rows = 0.5%, under the 2% default.
    quantities = [10.0] * 199 + [-5.0]
    audit = audit_quantities(_df(quantities), quantity_col="quantity", product_col="product_id")
    assert audit.n_negative == 1
    assert audit.negative_total == -5.0
    assert round(audit.negative_fraction, 4) == 0.005
    assert audit.is_material is False
    assert audit.issues() == []


def test_flow_above_threshold_is_material():
    # 3 negatives in 60 rows = 5%, over the 2% default.
    quantities = [10.0] * 57 + [-5.0, -5.0, -5.0]
    audit = audit_quantities(_df(quantities), quantity_col="quantity", product_col="product_id")
    assert audit.is_material is True
    assert len(audit.issues()) == 1
    assert "negative" in audit.issues()[0]


def test_flow_net_negative_sku_is_material_even_below_threshold():
    # 1 negative in 200 rows (0.5%, under threshold) but it sinks SKU B's total.
    quantities = [10.0] * 199 + [-500.0]
    products = ["A"] * 199 + ["B"]
    audit = audit_quantities(
        _df(quantities, products), quantity_col="quantity", product_col="product_id"
    )
    assert audit.n_entities_net_negative == 1
    assert audit.is_material is True


def test_level_data_any_negative_is_material():
    # A negative stock LEVEL admits no "return" reading: one is enough.
    quantities = [10.0] * 199 + [-5.0]
    audit = audit_quantities(
        _df(quantities), quantity_col="quantity", product_col="product_id", kind="level"
    )
    assert audit.negative_fraction < 0.02
    assert audit.is_material is True


def test_threshold_is_overridable():
    quantities = [10.0] * 90 + [-5.0] * 10   # 10%
    audit = audit_quantities(
        _df(quantities), quantity_col="quantity", product_col="product_id", threshold=0.25
    )
    assert audit.is_material is False


def test_disclosure_is_written_even_when_immaterial():
    quantities = [10.0] * 199 + [-5.0]
    audit = audit_quantities(_df(quantities), quantity_col="quantity", product_col="product_id")
    text = audit.disclosure()
    assert "1" in text and "quantity" in text
    assert audit.is_material is False


def test_disclosure_is_empty_when_there_are_no_negatives():
    audit = audit_quantities(_df([10, 20]), quantity_col="quantity", product_col="product_id")
    assert audit.disclosure() == ""


def test_non_numeric_values_do_not_crash_and_are_not_counted_negative():
    df = pd.DataFrame({"product_id": ["A", "A"], "quantity": ["abc", -5]})
    audit = audit_quantities(df, quantity_col="quantity", product_col="product_id")
    assert audit.n_negative == 1


def test_empty_frame_is_inert():
    df = pd.DataFrame({"product_id": [], "quantity": []})
    audit = audit_quantities(df, quantity_col="quantity", product_col="product_id")
    assert audit.n_rows == 0
    assert audit.negative_fraction == 0.0
    assert audit.is_material is False


def test_audit_is_frozen():
    audit = QuantityAudit(column="quantity", kind="flow", n_rows=1, n_negative=0,
                          negative_total=0.0, n_entities_net_negative=0)
    try:
        audit.n_negative = 5
    except Exception as exc:
        assert "frozen" in str(exc).lower()
    else:
        raise AssertionError("QuantityAudit must be frozen")


def test_audit_does_not_mutate_the_input_frame():
    df = _df([10.0, -5.0])
    before = df.copy()
    audit_quantities(df, quantity_col="quantity", product_col="product_id")
    pd.testing.assert_frame_equal(df, before)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=. python -m pytest tests/test_quantity_audit.py -q`
Expected: FAIL, collection error `ModuleNotFoundError: No module named 'jobs.quantity_audit'`.

- [ ] **Step 3: Write the implementation**

Create `jobs/quantity_audit.py`:

```python
"""Shared negative-quantity counter for every tool that ingests quantities.

Counts, never mutates. The five quantity-ingesting tools each read their own
CSV (a deliberate convention -- only ``inventory_optimization`` goes through
``jobs/intake.py``), so the shared piece cannot be a single intake step: it is
this audit, computed at ``prepare()`` time, carried on the payload into
``run()``, hung on the report, and gated in the tool's ``qa()``.

Routing the signal into ``qa()`` is what makes it bite: ``qa_issues`` is the
only channel the orchestrator's "QA fails => no deliverable" gate respects.
An escalation raised later, in ``tool.options()``, runs AFTER that gate
(``scm_agent/orchestrator.py``: qa at line 156, options at line 169) and so can
never block a deliverable.

Kern cannot tell a legitimate return from an inverted sign from a mis-mapped
column, so it does not guess: negatives are reported, not reinterpreted.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

#: Share of negative rows above which a FLOW column stops being explainable as
#: ordinary returns. Override per run with params["negative_quantity_threshold"].
DEFAULT_NEGATIVE_THRESHOLD = 0.02

FLOW = "flow"
LEVEL = "level"


@dataclass(frozen=True)
class QuantityAudit:
    """How many negative quantities a source file carried, and whether that is material.

    ``kind`` decides the materiality rule, because the two kinds of column fail
    differently:

    - ``FLOW`` (demand, sales, usage): an isolated negative row is a normal
      return. Material when the negative share exceeds ``threshold``, or when
      any entity's aggregate goes negative (which no volume of returns
      explains -- that is an inverted sign or a mis-mapped column).
    - ``LEVEL`` (on-hand stock): a negative level admits no "return" reading at
      all. It means the stock ledger is broken, so ANY negative is material.
    """

    column: str
    kind: str
    n_rows: int
    n_negative: int
    negative_total: float          # sum of the negative values; <= 0
    n_entities_net_negative: int
    threshold: float = DEFAULT_NEGATIVE_THRESHOLD

    @property
    def negative_fraction(self) -> float:
        return self.n_negative / self.n_rows if self.n_rows else 0.0

    @property
    def is_material(self) -> bool:
        if not self.n_negative:
            return False
        if self.kind == LEVEL:
            return True
        return self.negative_fraction > self.threshold or self.n_entities_net_negative > 0

    def issues(self) -> list[str]:
        """QA issues. Empty when there is nothing material to block on."""
        if not self.is_material:
            return []
        if self.kind == LEVEL:
            return [
                f"{self.n_negative} of {self.n_rows} row(s) carry a negative {self.column} "
                f"(total {self.negative_total:,.1f}) - a negative stock level is a broken "
                "ledger, not a return; correct the source file before planning on it"
            ]
        if self.n_entities_net_negative:
            return [
                f"{self.n_entities_net_negative} SKU(s) have a negative aggregate "
                f"{self.column} - returns cannot exceed sales, so this is an inverted "
                "sign or a mis-mapped column; correct the source file before planning on it"
            ]
        return [
            f"{self.n_negative} of {self.n_rows} row(s) carry a negative {self.column} "
            f"({self.negative_fraction * 100:.1f}%, over the "
            f"{self.threshold * 100:.0f}% tolerance) - too many to read as returns; "
            "verify the sign convention and the column mapping"
        ]

    def disclosure(self) -> str:
        """One line for the deliverable/residual, written even when immaterial.

        The immaterial case is the one that used to vanish entirely, so it is
        the case this exists for. Empty string when there is nothing to say.
        """
        if not self.n_negative:
            return ""
        return (
            f"{self.n_negative} of {self.n_rows} source row(s) carried a negative "
            f"{self.column} (total {self.negative_total:,.1f}). Kern cannot tell a "
            "return from an inverted sign, so these rows were not reinterpreted."
        )


def audit_quantities(
    df: pd.DataFrame,
    *,
    quantity_col: str,
    product_col: str | None = None,
    kind: str = FLOW,
    threshold: float = DEFAULT_NEGATIVE_THRESHOLD,
) -> QuantityAudit:
    """Count negatives in ``quantity_col`` without touching ``df``.

    Non-numeric cells are coerced to NaN and counted as neither negative nor
    net-negative -- they are a different defect, already handled by each tool's
    own column validation.
    """
    values = pd.to_numeric(df[quantity_col], errors="coerce")
    negative_mask = values < 0
    n_negative = int(negative_mask.sum())
    negative_total = float(values[negative_mask].sum()) if n_negative else 0.0
    if not math.isfinite(negative_total):
        negative_total = 0.0

    n_entities_net_negative = 0
    if product_col is not None and n_negative and kind == FLOW:
        totals = values.groupby(df[product_col]).sum()
        n_entities_net_negative = int((totals < 0).sum())

    return QuantityAudit(
        column=quantity_col,
        kind=kind,
        n_rows=int(len(df)),
        n_negative=n_negative,
        negative_total=negative_total,
        n_entities_net_negative=n_entities_net_negative,
        threshold=threshold,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `PYTHONPATH=. python -m pytest tests/test_quantity_audit.py -q`
Expected: PASS, 12 passed.

- [ ] **Step 5: Commit**

```bash
git status --short
git add jobs/quantity_audit.py tests/test_quantity_audit.py
git commit -m "feat(qa): shared negative-quantity audit"
```

---

### Task 2: Gate `inventory_optimization`

The counts already exist (`IntakeQuality.n_dropped_negative_quantity`, produced by `jobs/intake.py:271`). This task moves the signal from `guided.residuals` into `qa_issues` and adds the immaterial disclosure to `report.md`.

**Files:**
- Modify: `jobs/inventory_optimization.py:64-92` (add a field to `JobReport`)
- Modify: `jobs/qa.py:45-82` (`verify`)
- Modify: `jobs/deliverables.py:297-355` (`write_report_md`)
- Modify: `scm_agent/tools.py:110-127` (`_inventory_run`)
- Test: `tests/test_negative_quantity_gate.py`

**Interfaces:**
- Consumes: `jobs.quantity_audit.DEFAULT_NEGATIVE_THRESHOLD` from Task 1.
- Produces: `JobReport.negative_quantity_threshold: float = 0.02`. Consumed by `jobs.qa.verify` and `jobs.deliverables.write_report_md`.

**Accepted limitation (document, don't fake):** intake drops negative rows *before* the `groupby` (`jobs/intake.py:271-272` vs `:291`), so a SKU can never surface as net-negative here. This gate is fraction-only. Say so in the docstring.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_negative_quantity_gate.py`:

```python
"""Regression tests for the negative-quantity gate.

Spec: docs/superpowers/specs/2026-08-03-negative-quantity-gate-design.md
Every test here fails against the pre-gate code.
"""

import pandas as pd
import pytest

from scm_agent import Orchestrator


def _write_demand_csv(path, n_rows, negative_indexes, qty=100.0):
    rows = ["date,product_id,quantity,unit_cost,lead_time_days"]
    day = pd.Timestamp("2023-01-02")
    for i in range(n_rows):
        value = -qty if i in negative_indexes else qty
        rows.append(f"{(day + pd.Timedelta(weeks=i)).date()},SKU-A,{value},50,7")
    path.write_text("\n".join(rows), encoding="utf-8")
    return path


def test_material_negative_demand_blocks_the_deliverable(tmp_path):
    """10 negatives in 200 rows = 5%, over the 2% tolerance."""
    data = _write_demand_csv(tmp_path / "dirty.csv", 200, {10, 20, 30, 40, 50, 60, 70, 80, 90, 100})
    result = Orchestrator(clients_root=None).run(
        "set reorder points", data_path=str(data), job_type="inventory_optimization",
        client="Demo", out_dir=str(tmp_path / "out"),
    )
    assert result.status == "qa_failed"
    assert result.deliverables == {}
    assert any("negative" in issue for issue in result.qa_issues)


def test_immaterial_negative_demand_still_delivers_but_discloses(tmp_path):
    data = _write_demand_csv(tmp_path / "slightly_dirty.csv", 200, {33})
    out = tmp_path / "out"
    result = Orchestrator(clients_root=None).run(
        "set reorder points", data_path=str(data), job_type="inventory_optimization",
        client="Demo", out_dir=str(out),
    )
    assert result.status == "ok"
    assert result.deliverables
    report_md = (out / "inventory_optimization" / "report.md").read_text(encoding="utf-8")
    assert "Data quality" in report_md
    assert "negative" in report_md.lower()


def test_clean_file_has_no_data_quality_section(tmp_path):
    data = _write_demand_csv(tmp_path / "clean.csv", 60, set())
    out = tmp_path / "out"
    result = Orchestrator(clients_root=None).run(
        "set reorder points", data_path=str(data), job_type="inventory_optimization",
        client="Demo", out_dir=str(out),
    )
    assert result.status == "ok"
    assert result.qa_issues == []
    report_md = (out / "inventory_optimization" / "report.md").read_text(encoding="utf-8")
    assert "Data quality" not in report_md


def test_threshold_is_overridable_per_run(tmp_path):
    data = _write_demand_csv(tmp_path / "dirty.csv", 200, set(range(0, 100, 10)))
    result = Orchestrator(clients_root=None).run(
        "set reorder points", data_path=str(data), job_type="inventory_optimization",
        client="Demo", out_dir=str(tmp_path / "out"),
        overrides={"negative_quantity_threshold": 0.5},
    )
    assert result.status == "ok"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=. python -m pytest tests/test_negative_quantity_gate.py -q`
Expected: FAIL. `test_material_...` fails with `assert 'ok' == 'qa_failed'`; `test_immaterial_...` fails with `assert 'Data quality' in report_md`.

- [ ] **Step 3: Add the threshold field to `JobReport`**

In `jobs/inventory_optimization.py`, after the `intake_quality_threshold: float = 0.20` line (~line 82):

```python
    # Share of source rows that may be negative before the negative-quantity gate
    # (jobs/quantity_audit.py) blocks the deliverable. Distinct from
    # intake_quality_threshold above, which covers ALL drop reasons together.
    negative_quantity_threshold: float = 0.02
```

Add the matching keyword argument to `run()` — in the signature after `intake_quality_threshold: float = 0.20,`:

```python
    negative_quantity_threshold: float = 0.02,
```

and in the returned `JobReport(...)` after `intake_quality_threshold=intake_quality_threshold,`:

```python
        negative_quantity_threshold=negative_quantity_threshold,
```

- [ ] **Step 4: Add the gate to `jobs/qa.py`**

In `jobs/qa.py`, inside `verify()`, immediately before the final `return issues` (currently line 82):

```python
    # Negative demand is not a valid quantity: a mis-loaded return, an inverted sign,
    # or a mis-mapped column. jobs/intake.py already drops and counts those rows; this
    # is what makes the count BLOCK instead of only decorating guided.residuals, which
    # the orchestrator computes after this gate and so can never act on.
    #
    # Fraction-only by construction: intake drops negatives BEFORE the groupby
    # (jobs/intake.py:271 vs :291), so a SKU can never surface here as net-negative.
    quality = report.intake_quality
    if quality is not None and quality.n_dropped_negative_quantity:
        threshold = report.negative_quantity_threshold
        share = quality.n_dropped_negative_quantity / quality.n_raw if quality.n_raw else 0.0
        if share > threshold:
            issues.append(
                f"{quality.n_dropped_negative_quantity} of {quality.n_raw} source row(s) "
                f"carry a negative quantity ({share * 100:.1f}%, over the "
                f"{threshold * 100:.0f}% tolerance) - too many to read as returns; "
                "verify the sign convention and the column mapping"
            )
```

- [ ] **Step 5: Add the "Data quality" section to `report.md`**

In `jobs/deliverables.py`, inside `write_report_md`, immediately before `lines.extend(_methodology_lines(report))` (currently line 348):

```python
    quality = report.intake_quality
    if quality is not None and quality.n_dropped_negative_quantity:
        lines.append("## Data quality\n")
        lines.append(
            f"- **{quality.n_dropped_negative_quantity} of {quality.n_raw} source row(s)** "
            "carried a negative quantity and were excluded from this analysis. Kern cannot "
            "tell a legitimate return from an inverted sign or a mis-mapped column, so "
            "those rows were not reinterpreted. The demand above is therefore gross of "
            "returns, not net."
        )
        lines.append("")
```

- [ ] **Step 6: Thread the param through `scm_agent/tools.py`**

In `_inventory_run` (`scm_agent/tools.py`), after the `intake_quality_threshold=...` argument:

```python
        negative_quantity_threshold=params.get("negative_quantity_threshold", 0.02),
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `PYTHONPATH=. python -m pytest tests/test_negative_quantity_gate.py -q`
Expected: PASS, 4 passed.

- [ ] **Step 8: Run the existing suite for regressions**

Run: `PYTHONPATH=. python -m pytest tests/test_jobs.py tests/test_tool_options.py tests/test_agent.py -q`
Expected: PASS. If a test fails because its fixture carries negatives, read the fixture before touching the threshold — a genuinely dirty fixture is the gate working.

- [ ] **Step 9: Commit**

```bash
git status --short
git add jobs/inventory_optimization.py jobs/qa.py jobs/deliverables.py scm_agent/tools.py tests/test_negative_quantity_gate.py
git commit -m "feat(qa): block inventory_optimization on material negative demand"
```

---

### Task 3: Make residuals visible in the CLI

Without this, the sub-threshold disclosure exists on `JobResult.guided` and is printed nowhere. `webapp/app.py:680` already serializes it; only `examples/run_agent.py` is blind.

**Files:**
- Modify: `examples/run_agent.py:66-80`
- Test: `tests/test_negative_quantity_gate.py`

**Interfaces:**
- Consumes: `JobResult.guided` (a `nosilo_core.guided.GuidedOutcome` with `.residuals: list[Residual]` and `.escalation: EscalationPacket | None`).
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_negative_quantity_gate.py`:

```python
def test_cli_prints_residuals_and_escalation(tmp_path, capsys):
    from examples import run_agent

    # 1 negative in 2 rows: 50% dropped, which trips intake's own 20% escalation
    # threshold while staying a single row.
    data = tmp_path / "tiny.csv"
    data.write_text(
        "date,product_id,quantity,unit_cost,lead_time_days\n"
        "2024-01-01,SKU-A,100,50,7\n"
        "2024-01-08,SKU-A,-40,50,7\n",
        encoding="utf-8",
    )
    run_agent.main([
        "--brief", "demo", "--job", "inventory_optimization",
        "--data", str(data), "--out", str(tmp_path / "out"), "--client", "Demo",
    ])
    printed = capsys.readouterr().out
    assert "Residuals" in printed
    assert "negative quantity" in printed
    assert "Escalated" in printed
    assert "operations owner" in printed
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `PYTHONPATH=. python -m pytest tests/test_negative_quantity_gate.py::test_cli_prints_residuals_and_escalation -q`
Expected: FAIL with `assert 'Residuals' in printed`.

- [ ] **Step 3: Add the print block**

In `examples/run_agent.py`, after the existing `if result.clarifications:` block (which ends at line 80) and before the function's `return`:

```python
    # The guided outcome is where a data-quality objection lives when it is not
    # severe enough to fail QA outright. Printing it is the difference between an
    # objection Kern made and an objection the operator ever sees.
    guided = result.guided
    if guided is not None and guided.residuals:
        print("Residuals (human side):")
        for r in guided.residuals:
            print("  - " + r.description)
            if r.risk_if_skipped:
                print("      risk if skipped: " + r.risk_if_skipped)
    if guided is not None and guided.escalation is not None:
        e = guided.escalation
        print(f"Escalated -> {e.route_to} (SLA: {e.sla})")
        print("  reason: " + e.reason)
```

ASCII only in these prints — no em dashes.

- [ ] **Step 4: Run the test to verify it passes**

Run: `PYTHONPATH=. python -m pytest tests/test_negative_quantity_gate.py::test_cli_prints_residuals_and_escalation -q`
Expected: PASS.

- [ ] **Step 5: Verify the original repro by hand**

```bash
PYTHONPATH=. python examples/run_agent.py --brief demo --job inventory_optimization --data /tmp/broken.csv --out output/_probe/broken --client Demo
```

Expected: the run still succeeds (50% of 2 rows is 1 row, and 50% > 2% so it now **fails QA** — confirm you see `[qa_failed]` and no deliverables). Delete `output/_probe` afterwards; it is gitignored but leave the tree clean.

- [ ] **Step 6: Commit**

```bash
git status --short
git add examples/run_agent.py tests/test_negative_quantity_gate.py
git commit -m "feat(cli): print guided residuals and escalation in run_agent"
```

---

### Task 4: Gate `abc_xyz`

Today a SKU with `100/100/100/-300` lands at `annual_value=0`, class **C/Z**, `cv=inf`, with `verify()` returning `[]` — a real A-item presented as a discontinuation candidate.

**Files:**
- Modify: `jobs/abc_xyz_job.py:37-45` (`AbcXyzReport`), `:54-65` (`aggregate_skus`), `:68-80` (`prepare`), `:83-103` (`run`), `:104-112` (`verify`)
- Modify: `scm_agent/tools.py:396-419` (`_abc_xyz_prepare`, `_abc_xyz_run`)
- Modify: `scm_agent/tool_options.py` (`abc_xyz_options`)
- Test: `tests/test_negative_quantity_gate.py`

**Interfaces:**
- Consumes: `audit_quantities`, `QuantityAudit` from Task 1.
- Produces: `AbcXyzReport.quantity_audit: QuantityAudit | None = None`; `abc_xyz_job.prepare()` now returns `dict` with keys `"items"` and `"audit"` instead of a bare `list[dict]`; `aggregate_skus()` signature is unchanged. `scm_agent.tool_options._apply_quantity_audit(outcome, report) -> GuidedOutcome` (created here, reused by Tasks 5 and 6).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_negative_quantity_gate.py`:

```python
def test_abc_xyz_blocks_on_a_net_negative_sku():
    from jobs import abc_xyz_job

    df = pd.DataFrame({
        "product_id": ["A"] * 4 + ["B"] * 4,
        "period": [1, 2, 3, 4] * 2,
        "quantity": [100, 100, 100, -300, 100, 100, 100, 100],
        "unit_cost": [10] * 8,
    })
    payload = abc_xyz_job.prepare_records(df)
    report = abc_xyz_job.run(payload)
    issues = abc_xyz_job.verify(report)
    assert any("negative" in issue for issue in issues)


def test_abc_xyz_clean_data_passes():
    from jobs import abc_xyz_job

    df = pd.DataFrame({
        "product_id": ["A"] * 4 + ["B"] * 4,
        "period": [1, 2, 3, 4] * 2,
        "quantity": [100, 90, 110, 100, 10, 12, 8, 10],
        "unit_cost": [10] * 8,
    })
    report = abc_xyz_job.run(abc_xyz_job.prepare_records(df))
    assert abc_xyz_job.verify(report) == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `PYTHONPATH=. python -m pytest tests/test_negative_quantity_gate.py -k abc_xyz -q`
Expected: FAIL with `AttributeError: module 'jobs.abc_xyz_job' has no attribute 'prepare_records'`.

- [ ] **Step 3: Add `prepare_records` and thread the audit**

In `jobs/abc_xyz_job.py`, add the import at the top with the other `src`/`jobs` imports:

```python
from jobs.quantity_audit import QuantityAudit, audit_quantities
```

Add the field to `AbcXyzReport`, after `value_basis`:

```python
    # Negative quantities seen in the source file (jobs/quantity_audit.py). None when
    # the caller built the report from raw items rather than through prepare_records.
    quantity_audit: QuantityAudit | None = None
```

Replace `prepare()` (currently lines 68-80) with a `prepare_records` + `prepare` pair:

```python
def prepare_records(df: pd.DataFrame, params: dict | None = None) -> dict:
    """Sniff the columns, audit the quantities, and aggregate to classifier items.

    Returns ``{"items": [...], "audit": QuantityAudit}``. The audit is counted on the
    RAW frame, before aggregation: once summed, a SKU whose sign was inverted looks
    merely small, not wrong.
    """
    params = params or {}
    product = _pick_column(df, params.get("product_col"), _PRODUCT_COLS)
    demand = _pick_column(df, params.get("demand_col"), _DEMAND_COLS)
    cost = _pick_column(df, params.get("cost_col"), _COST_COLS)
    missing = [n for n, c in (("product_col", product), ("demand_col", demand), ("cost_col", cost)) if c is None]
    if missing:
        cols = list(df.columns)[:10]
        raise ValueError(f"could not find {', '.join(missing)}; pass them in params (columns seen: {cols})")
    audit = audit_quantities(
        df, quantity_col=demand, product_col=product,
        threshold=float(params.get("negative_quantity_threshold", 0.02)),
    )
    items = aggregate_skus(df, product_col=product, demand_col=demand, cost_col=cost)
    return {"items": items, "audit": audit}


def prepare(data_path: str, params: dict | None = None) -> dict:
    """Read a per-SKU demand CSV and aggregate it to classifier items + a quantity audit."""
    return prepare_records(pd.read_csv(data_path), params)
```

Change `run()` to accept the payload dict. Replace its signature and first line:

```python
def run(
    payload: dict | list[dict],
    *,
    abc_thresholds: tuple[float, float] = (0.80, 0.95),
    cv_cuts: tuple[float, float] = (0.5, 1.0),
) -> AbcXyzReport:
    """Classify the portfolio into the ABC-XYZ matrix and roll up the headline counts.

    Accepts either the ``prepare_records`` payload or a bare item list (the older
    shape, still used by examples/ and tests that build items by hand).
    """
    if isinstance(payload, dict):
        items, audit = payload["items"], payload.get("audit")
    else:
        items, audit = payload, None
```

and add `quantity_audit=audit,` to the returned `AbcXyzReport(...)`.

Add the gate to `verify()`, before its `return issues`:

```python
    if report.quantity_audit is not None:
        issues.extend(report.quantity_audit.issues())
```

- [ ] **Step 4: Update the tool wiring**

In `scm_agent/tools.py`, `_abc_xyz_prepare`: replace the `items` handling so the payload is the dict.

```python
def _abc_xyz_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    if not request.data_path:
        return Prepared(status="needs_data", messages=["a per-SKU demand CSV (product, demand, unit cost) is required"])
    try:
        payload = abc_xyz_job.prepare(request.data_path, request.params)
    except (ValueError, FileNotFoundError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    if not payload["items"]:
        return Prepared(status="needs_data", messages=["no SKUs found in the data"])
    return Prepared(status="ok", payload=payload)
```

`_abc_xyz_run` needs no change: it already forwards `payload` to `abc_xyz_job.run`.

- [ ] **Step 5: Add the shared residual helper**

In `scm_agent/tool_options.py`, after `_apply_intake_quality` (which ends at line 101):

```python
def _apply_quantity_audit(outcome: GuidedOutcome, report: object) -> GuidedOutcome:
    """State a residual whenever the source file carried ANY negative quantity.

    Sibling of ``_apply_intake_quality``, for the four tools that read their own CSV
    instead of going through jobs/intake.py. Disclosure only: the MATERIAL case is
    already blocked upstream by the tool's qa() (jobs/quantity_audit.py), so anything
    that reaches here is the sub-threshold case -- the one that used to vanish
    entirely. A no-op when the report carries no audit or the file was clean.
    """
    audit = getattr(report, "quantity_audit", None)
    if audit is None:
        return outcome
    text = audit.disclosure()
    if not text:
        return outcome
    return replace(outcome, residuals=[*outcome.residuals, Residual(
        description=text,
        risk_if_skipped="demand is counted gross of returns, so the plan may be sized "
                        "slightly high; confirm the sign convention of the source column.",
    )])
```

Wire it into `abc_xyz_options` by wrapping its return value: find the `return` at the end of `abc_xyz_options` and change `return <expr>` to `return _apply_quantity_audit(<expr>, report)`.

- [ ] **Step 6: Run the tests**

Run: `PYTHONPATH=. python -m pytest tests/test_negative_quantity_gate.py -q && PYTHONPATH=. python -m pytest tests/ -q -k "abc"`
Expected: PASS. Any existing test calling `abc_xyz_job.prepare(...)` and indexing the result as a list must be updated to `["items"]`; `run()` still accepts a bare list, so item-building tests need no change.

- [ ] **Step 7: Commit**

```bash
git status --short
git add jobs/abc_xyz_job.py scm_agent/tools.py scm_agent/tool_options.py tests/test_negative_quantity_gate.py
git commit -m "feat(qa): block abc_xyz on material negative demand"
```

---

### Task 5: Gate `forecast`

Today `forecast_job.py:110` does `.clip(lower=0.0)` and turns a `-400` into a `0` with no count and no disclosure. The clip stays — flooring is right once declared — but it must count first.

**Files:**
- Modify: `jobs/forecast_job.py:72-83` (`ForecastJobReport`), `:91-125` (`prepare_records`/`prepare`), `:212-278` (`run`), `:280-288` (`verify`)
- Modify: `scm_agent/tools.py:1301-1322` (`_forecast_prepare`, `_forecast_run`), and the `forecast_tool()` `options=` lambda
- Modify: `scm_agent/tool_options.py` (new `forecast_options`)
- Test: `tests/test_negative_quantity_gate.py`

**Interfaces:**
- Consumes: `audit_quantities`, `QuantityAudit` (Task 1); `_apply_quantity_audit` (Task 4).
- Produces: `ForecastJobReport.quantity_audit: QuantityAudit | None = None`; `forecast_job.prepare_records()` returns `dict` with keys `"series"` and `"audit"`; `scm_agent.tool_options.forecast_options(report) -> GuidedOutcome`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_negative_quantity_gate.py`:

```python
def test_forecast_counts_negatives_before_flooring_them():
    from jobs import forecast_job

    df = pd.DataFrame({
        "product_id": ["A"] * 6,
        "period": range(6),
        "quantity": [100, 100, 100, -400, 100, 100],
    })
    payload = forecast_job.prepare_records(df)
    assert payload["audit"].n_negative == 1
    assert payload["audit"].negative_total == -400.0
    # The clip stays: the SERIES is still floored, it is just no longer silent.
    assert payload["series"]["A"] == [100.0, 100.0, 100.0, 0.0, 100.0, 100.0]

    report = forecast_job.run(payload)
    assert any("negative" in issue for issue in forecast_job.verify(report))


def test_forecast_clean_data_passes():
    from jobs import forecast_job

    df = pd.DataFrame({
        "product_id": ["A"] * 6,
        "period": range(6),
        "quantity": [100, 105, 95, 100, 102, 98],
    })
    report = forecast_job.run(forecast_job.prepare_records(df))
    assert forecast_job.verify(report) == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `PYTHONPATH=. python -m pytest tests/test_negative_quantity_gate.py -k forecast -q`
Expected: FAIL with `TypeError: string indices must be integers` (prepare_records still returns the bare series dict).

- [ ] **Step 3: Implement**

In `jobs/forecast_job.py`, add the import:

```python
from jobs.quantity_audit import QuantityAudit, audit_quantities
```

Add to `ForecastJobReport`, after `summary: str`:

```python
    # Negative quantities seen in the source file, counted BEFORE the clip in
    # prepare_records floors them (jobs/quantity_audit.py). None when the caller
    # passed a bare series dict.
    quantity_audit: QuantityAudit | None = None
```

In `prepare_records`, insert the audit immediately **before** the clip line (`work[qty_col] = pd.to_numeric(...).clip(lower=0.0)`), and change the return:

```python
    audit = audit_quantities(
        work, quantity_col=qty_col, product_col=product_col,
        threshold=float(params.get("negative_quantity_threshold", 0.02)),
    )
    work[qty_col] = pd.to_numeric(work[qty_col], errors="coerce").fillna(0.0).clip(lower=0.0)
```

and at the end of the function replace `return series_by_name` with:

```python
    return {"series": series_by_name, "audit": audit}
```

Update the docstring's return description to say it returns the payload dict, and keep the existing "Missing demand -> 0; negatives floored" sentence, appending "and counted (see ``quantity_audit``)".

Change `prepare`'s annotation to `-> dict` (body unchanged — it delegates).

In `run()`, change the signature's first parameter and unpack:

```python
def run(
    payload: dict,
    *,
    holdout_fraction: float = 0.25,
    min_backtest_periods: int = 4,
) -> ForecastJobReport:
    """Segment, auto-forecast + backtest each SKU, and present ranked forecasting policies.

    Accepts either the ``prepare_records`` payload (``{"series", "audit"}``) or a bare
    ``{name: series}`` dict, the older shape still used by examples/ and hand-built tests.
    """
    if "series" in payload and isinstance(payload.get("series"), dict):
        series_by_name, audit = payload["series"], payload.get("audit")
    else:
        series_by_name, audit = payload, None
```

Add `quantity_audit=audit,` to the returned `ForecastJobReport(...)`.

Add the gate to `verify()`, before its `return issues`:

```python
    if report.quantity_audit is not None:
        issues.extend(report.quantity_audit.issues())
```

- [ ] **Step 4: Update the tool wiring**

In `scm_agent/tools.py`, `_forecast_prepare`: replace the emptiness check.

```python
    if not payload["series"]:
        return Prepared(status="needs_data", messages=["no demand series found in the data"])
    return Prepared(status="ok", payload=payload)
```

(rename the local `series` to `payload` on the `forecast_job.prepare(...)` line).

In `forecast_tool()`, replace `options=lambda report: report.outcome,` with:

```python
        options=tool_options.forecast_options,
```

In `scm_agent/tool_options.py`, add:

```python
def forecast_options(report: object) -> GuidedOutcome:
    """The forecast job builds its own ranked outcome inside run(); this only adds the
    intake-side residual the tool cannot know about from its own numbers."""
    return _apply_quantity_audit(report.outcome, report)
```

- [ ] **Step 5: Run the tests**

Run: `PYTHONPATH=. python -m pytest tests/test_negative_quantity_gate.py -q && PYTHONPATH=. python -m pytest tests/ -q -k "forecast"`
Expected: PASS. Existing callers passing a bare series dict still work via the fallback branch in `run()`.

- [ ] **Step 6: Commit**

```bash
git status --short
git add jobs/forecast_job.py scm_agent/tools.py scm_agent/tool_options.py tests/test_negative_quantity_gate.py
git commit -m "feat(qa): count negatives before flooring them in forecast"
```

---

### Task 6: Gate `excess_obsolete` (level semantics)

Today `on_hand=-500` is classified **healthy** with `verify()` returning `[]`. A negative stock level admits no "return" reading, so `kind="level"` makes any negative material.

**Files:**
- Modify: `jobs/excess_obsolete_job.py:30-45` (`EOReport`), `:72-118` (`prepare_records`), `:124-149` (`run`), `:151-163` (`verify`)
- Modify: `scm_agent/tool_options.py` (`excess_obsolete_options`)
- Test: `tests/test_negative_quantity_gate.py`

**Interfaces:**
- Consumes: `audit_quantities`, `QuantityAudit`, `LEVEL` (Task 1); `_apply_quantity_audit` (Task 4).
- Produces: `EOReport.quantity_audit: QuantityAudit | None = None`; `prepare_records` payload gains the `"audit"` key.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_negative_quantity_gate.py`:

```python
def test_excess_obsolete_blocks_on_any_negative_on_hand():
    from jobs import excess_obsolete_job as eo

    df = pd.DataFrame({
        "product_id": ["A", "B"],
        "on_hand": [-500.0, 100.0],
        "daily_demand": [10.0, 10.0],
        "unit_cost": [20.0, 20.0],
    })
    report = eo.run(eo.prepare_records(df))
    issues = eo.verify(report)
    assert any("negative" in issue and "on_hand" in issue for issue in issues)


def test_excess_obsolete_clean_stock_passes():
    from jobs import excess_obsolete_job as eo

    df = pd.DataFrame({
        "product_id": ["A", "B"],
        "on_hand": [500.0, 100.0],
        "daily_demand": [10.0, 10.0],
        "unit_cost": [20.0, 20.0],
    })
    report = eo.run(eo.prepare_records(df))
    assert eo.verify(report) == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `PYTHONPATH=. python -m pytest tests/test_negative_quantity_gate.py -k excess_obsolete -q`
Expected: FAIL with `assert any(...)` on an empty issue list.

- [ ] **Step 3: Implement**

In `jobs/excess_obsolete_job.py`, add the import:

```python
from jobs.quantity_audit import LEVEL, QuantityAudit, audit_quantities
```

Add to `EOReport`, after `summary: str`:

```python
    # Negative on-hand levels seen in the source file (jobs/quantity_audit.py).
    # kind=LEVEL: any negative is material, because a negative stock level is a
    # broken ledger, not a return.
    quantity_audit: QuantityAudit | None = None
```

In `prepare_records`, add to the returned dict:

```python
        "audit": audit_quantities(df, quantity_col=on_hand, product_col=product, kind=LEVEL),
```

In `run()`, add to the returned `EOReport(...)`:

```python
        quantity_audit=payload.get("audit"),
```

Add the gate to `verify()`, before its `return issues`:

```python
    if report.quantity_audit is not None:
        issues.extend(report.quantity_audit.issues())
```

- [ ] **Step 4: Wire the residual**

In `scm_agent/tool_options.py`, wrap `excess_obsolete_options`' return value: change its final `return <expr>` to `return _apply_quantity_audit(<expr>, report)`.

- [ ] **Step 5: Run the tests**

Run: `PYTHONPATH=. python -m pytest tests/test_negative_quantity_gate.py -q && PYTHONPATH=. python -m pytest tests/ -q -k "excess or obsolete"`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git status --short
git add jobs/excess_obsolete_job.py scm_agent/tool_options.py tests/test_negative_quantity_gate.py
git commit -m "feat(qa): block excess_obsolete on any negative on-hand level"
```

---

### Task 7: Name the negative ADU in `ddmrp`

`ddmrp` already blocks (`invalid buffer zones for P1`), but the message describes the symptom, not the cause. An operator reading it has no reason to look at the ADU column.

**Files:**
- Modify: `jobs/ddmrp_job.py:119-128` (`verify`)
- Test: `tests/test_negative_quantity_gate.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_negative_quantity_gate.py`:

```python
def test_ddmrp_names_the_negative_adu():
    from jobs import ddmrp_job

    df = pd.DataFrame({"part_id": ["P1"], "adu": [-50.0], "dlt": [10.0]})
    report = ddmrp_job.run(ddmrp_job.prepare_records(df))
    issues = ddmrp_job.verify(report)
    assert any("negative average daily usage" in issue for issue in issues)
    assert any("P1" in issue for issue in issues)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `PYTHONPATH=. python -m pytest tests/test_negative_quantity_gate.py -k ddmrp -q`
Expected: FAIL — the only issue says `invalid buffer zones for P1`.

- [ ] **Step 3: Implement**

Replace the loop body in `jobs/ddmrp_job.py`'s `verify()`:

```python
    for p in report.parts:
        z = p.zones
        # Check the CAUSE before the symptom: a negative ADU makes the zones
        # invalid, and "invalid buffer zones" alone gives the operator no reason
        # to go look at the usage column.
        if z.adu < 0:
            issues.append(
                f"negative average daily usage for {p.part_id} ({z.adu:,.1f}) - "
                "usage cannot be negative; verify the sign convention and the column mapping"
            )
            continue
        if not (z.tog >= z.toy >= z.tor >= 0):
            issues.append(f"invalid buffer zones for {p.part_id}")
```

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=. python -m pytest tests/test_negative_quantity_gate.py -q && PYTHONPATH=. python -m pytest tests/ -q -k "ddmrp"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git status --short
git add jobs/ddmrp_job.py tests/test_negative_quantity_gate.py
git commit -m "fix(ddmrp): name the negative ADU instead of only its symptom"
```

---

### Task 8: Full suite, docs, and the ROADMAP item

**Files:**
- Modify: `documentation/ROADMAP.md:118-123` (item 6)
- Modify: `CLAUDE.md` (Gotchas section)
- Test: full suite

**Interfaces:**
- Consumes: everything from Tasks 1-7.
- Produces: nothing.

- [ ] **Step 1: Run the whole suite**

Run: `PYTHONPATH=. python -m pytest tests/ -q`
Expected: PASS. Investigate every failure individually. A fixture that trips the new gate is the gate working — fix the fixture (or assert the new behavior), do not weaken the threshold to make a test green.

- [ ] **Step 2: Run lint**

Run: `ruff check src tests examples`
Expected: clean.

- [ ] **Step 3: Confirm the demo still refuses correctly**

Run: `make demo`
Expected: exit 0. Step 4 (the missing-columns brief) must still end without a deliverable.

- [ ] **Step 4: Close the ROADMAP finding**

In `documentation/ROADMAP.md`, replace the sentence *"Ver tambien el hallazgo abierto: hoy una cantidad negativa **no** levanta ninguna objecion."* with:

```markdown
**Hecho (2026-08-03):** el hallazgo de la cantidad negativa esta cerrado. Una
cantidad negativa material ahora falla QA y no produce entregable, en las cinco
tools que ingieren cantidades (`inventory_optimization`, `abc_xyz`, `forecast`,
`excess_obsolete`, `ddmrp`); una inmaterial se declara en el entregable y en los
residuals, que el CLI ahora imprime. Ver
`docs/superpowers/specs/2026-08-03-negative-quantity-gate-design.md`.
```

- [ ] **Step 5: Document the convention in CLAUDE.md**

In `CLAUDE.md`, add to the **Gotchas** list:

```markdown
- **Toda tool que ingiere cantidades audita los negativos** (`jobs/quantity_audit.py`),
  y el gate vive en su `qa()`, no en `options()`: `tool.qa()` corre en
  `orchestrator.py:156` y `tool.options()` en la `169`, asi que una escalacion nacida
  en `options` nunca puede bloquear un entregable. Si agregas una tool con columna de
  cantidad, contala en `prepare()`, colgala del report y llama a
  `audit.issues()` desde `verify()`.
```

- [ ] **Step 6: Commit and open the draft PR**

```bash
git status --short
git add documentation/ROADMAP.md CLAUDE.md
git commit -m "docs: close the negative-quantity finding in the roadmap"
git push -u origin feat/negative-quantity-gate
gh pr create --draft --title "feat(qa): negative-quantity gate across the five quantity-ingesting tools" --body "$(cat <<'EOF'
Cierra el hallazgo abierto del item 6 de `documentation/ROADMAP.md`.

Spec: `docs/superpowers/specs/2026-08-03-negative-quantity-gate-design.md`

## Que cambia

Una cantidad negativa material ahora falla QA y no produce entregable. Una
inmaterial se declara en el entregable y en `guided.residuals`, que el CLI ahora
imprime.

| tool | antes | ahora |
|---|---|---|
| `inventory_optimization` | descartaba y contaba, pero solo en un residual invisible | falla QA sobre el 2%; disclosure en `report.md` por debajo |
| `abc_xyz` | sumaba la negativa: un A-item 100/100/100/-300 salia clase C/Z con `qa` limpio | falla QA |
| `forecast` | `clip(lower=0)` fabricaba un 0 sin contarlo | cuenta antes de pisar; falla QA |
| `excess_obsolete` | `on_hand=-500` salia "healthy" con `qa` limpio | falla QA (cualquier negativo) |
| `ddmrp` | `invalid buffer zones for P1` | nombra el ADU negativo |

## Por que en `qa()` y no en el intake

Cuatro de las cinco tools leen su propio CSV a proposito, asi que el intake no
alcanza. Y `data_quality` audita un maestro de productos (sku/GTIN/costo), nunca
una columna transaccional.

La causa raiz era de orden: `tool.qa()` corre en `orchestrator.py:156` y
`tool.options()` --quien fabricaba la escalacion-- en la `169`. Una escalacion
nacida en `options` estructuralmente nunca podia bloquear un entregable.

## Fuera de alcance, declarado

- El deck premium (`deliverable.md`) sigue sin llevar residuals: exige cambiar la
  firma de `Tool.deck` en las 49 tools. Hoy ademas imprime "No residual actions"
  mientras `guided` lleva residuals (`src/i18n.py:142`).
- `JobResult.status`/`confidence` coherentes con `guided.status`.

## Test plan

- [ ] `PYTHONPATH=. python -m pytest tests/ -q`
- [ ] `ruff check src tests examples`
- [ ] `make demo` (el paso 4 debe seguir refusando)
EOF
)"
```

---

## Self-Review

**Spec coverage.** §4.1 -> Task 1. §4.2 (flow/level rule, overridable threshold) -> Task 1 steps 1&3, exercised per-tool in Tasks 2/4/5/6. §4.3 table -> Tasks 2 (inventory), 4 (abc_xyz), 5 (forecast), 6 (excess_obsolete), 7 (ddmrp). §4.3's accepted limitation -> Task 2 step 4 docstring. §4.4 item 1 (report.md) -> Task 2 step 5. §4.4 item 2 (CLI) -> Task 3. §4.4 item 3 (webapp) -> **already satisfied at `webapp/app.py:680`**; recorded in File Structure rather than given a task. §4.4 out-of-scope items -> carried verbatim into the PR body, Task 8 step 6. §4.5 test table -> Tasks 2, 4, 5, 6, 7 (per-tool) and the clean-sample non-regression at Task 2 step 1 / Task 8 step 1. §5 risks -> the "read the fixture before weakening the threshold" instruction at Task 2 step 8 and Task 8 step 1; the double-counting risk is structurally answered by Task 2 step 4 reading `IntakeQuality` rather than recounting.

**Type consistency.** `QuantityAudit` field and method names are identical across Tasks 1, 2, 4, 5, 6, 7. Every tool hangs the audit on the same attribute name `quantity_audit`, which is what `_apply_quantity_audit` reads via `getattr`. Payload key names are `"items"`/`"audit"` (abc_xyz), `"series"`/`"audit"` (forecast), `"stocks"`/`"audit"` (excess_obsolete) — each read back with the same literal in its own task. `inventory_optimization` alone uses `intake_quality` (pre-existing) rather than `quantity_audit`, which is why Task 2's gate lives in `jobs/qa.py` and reads `IntakeQuality` directly; `_apply_quantity_audit` correctly no-ops on it because `getattr(report, "quantity_audit", None)` is `None` there and the existing `_apply_intake_quality` already covers that path.

**Known ordering constraint.** `_apply_quantity_audit` is created in Task 4 but used again in Tasks 5 and 6. Tasks 4-7 must run in order; Tasks 2 and 3 are independent of them and of each other except that Task 3's manual check in step 5 assumes Task 2 has landed.
