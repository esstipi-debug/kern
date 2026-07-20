> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax where present. Part of the CSCP/SCPro gap-closing initiative -- see docs/superpowers/specs/2026-07-20-cscp-scpro-gap-closing-design.md.

# Implementation Plan: `supplier_management` tool (Kraljic segmentation + strategic SRM)

## Goal
Add Kern's 42nd agent-routable tool, `supplier_management`: read a suppliers CSV (supplier + spend/annual_value + risk-driver performance columns), place each supplier on the Kraljic matrix (profit-impact axis x supply-risk axis), assign a quadrant (strategic / bottleneck / leverage / non-critical) and its SRM strategy, and return a per-supplier segmentation table plus a never-unprotected `GuidedOutcome`. Closes the CSCP "strategic supplier relationship management" coverage gap.

## Architecture
Follow the repo's three-layer tool recipe exactly:
1. **`src/supplier_management.py`** — pure functions + frozen dataclasses, no pandas, no I/O. Mirrors `src/multi_criteria_classification.py`'s "weighted composite -> banding" template and returns a `GuidedOutcome` via `src/guided.py`'s `as_options` builder (same pattern as `src/mcdm.py::award_outcome`).
2. **`jobs/supplier_management_job.py`** — pandas-only `prepare()` that reads its **own** CSV (never `jobs/intake.py`), sniffs/normalizes columns, then `run()`/`verify()`/`write_operational()`/`build_deck()`. Structured exactly like `jobs/sourcing_job.py`.
3. **`scm_agent/tools.py`** — a `supplier_management_tool()` factory + `reg.register(...)` in `build_default_registry()`, plus a `TOOL_CONCEPTS` anchor entry in `scm_agent/citation_gate.py`.

Grounding is already in L3 (node `knowledge::kraljic_matrix`, label "Kraljic Matrix", source `grant-sustainable-logistics-supply-chain.txt`, Ch6). No new L3 source is added.

## Tech Stack
Python 3.11+, `@dataclass(frozen=True)`, type hints on every signature, ASCII-only console prints. numpy/scipy are available but this module needs neither (pure stdlib arithmetic). pandas only inside the job layer. Tests: `PYTHONPATH=. py -m pytest`. Lint: `ruff check src tests examples`.

## Global Constraints
- **No pandas / no I/O in `src/`.** Normalization of risk columns happens in the job's `prepare()`; the pure module receives already-normalized `[0,1]` risk scores.
- **Never-unprotected contract:** the tool's result is a `GuidedOutcome` whose `verify_guided(...)` returns `[]`.
- **ASCII-only** in any `print`/summary/strategy string (Windows cp1252). Markdown deliverables written utf-8 are fine, but keep strategy labels ASCII (`->` not an em dash) since they flow into `Deliverable.to_markdown()` which asserts `.isascii()` in tests.
- **Prod boot safety:** `src/supplier_management.py` sits on the boot chain (`webapp.app -> scm_agent -> tools -> jobs -> src`). It must import only stdlib + `src.guided` at module level (no optional-extra deps). The job layer may import pandas at module top (jobs are only imported transitively, and pandas is a base dep).
- **Citation-gate pool ceiling:** keep the anchor concept list <= 8 (use 4).
- **Branch workflow:** feature branch `feat/supplier-management-kraljic` -> draft PR -> CI green (py3.11/3.12/3.13) -> squash-merge. Never push to `main`.

### Verified existing signatures this plan builds on (read, not guessed)
- `src/guided.py`: `ExecutionOption(label, summary, score=0.0, recommended=False, action="", tradeoffs="")`; `GuidedOutcome(status, summary, confidence=1.0, options=[], handoffs=[], escalation=None, residuals=[])`; `as_options(summary, options, *, confidence=1.0, residuals=None) -> GuidedOutcome` (auto-flags best as recommended, raises on empty); `verify_guided(outcome) -> list[str]`; `Residual(description, owner="human", risk_if_skipped="")`.
- `src/mcdm.py::award_outcome(ranking, *, summary, action_prefix="stage:award:") -> GuidedOutcome` — the `as_options` usage pattern being mirrored.
- `src/multi_criteria_classification.py::classify_multicriteria(items, criteria, weights, *, a_share=0.2, b_share=0.3) -> list[MultiCriteriaClass]` — the banding template.
- `src/risk.py`: `CATEGORIES` (includes `"supply"`, `"concentration"`); `RiskFactor`, `assess(risk, *, severity_thresholds=...) -> RiskAssessment` — its 1-5 rater thresholds inform the risk-band cut points; see Open Questions for why `assess()` itself is not wired.
- `src/supplier_scorecard.py::score_supplier(supplier, deliveries) -> SupplierScore(supplier, deliveries, on_time_rate, in_full_rate, otif, avg_lead_time, ppm)` — optional delivery-derived risk driver (lead time, ppm).
- `jobs/sourcing_job.py`: `_pick_column(df, override, candidates) -> str|None`; `prepare(data_path, params=None) -> dict`; `run(...)`; `verify(report) -> list[str]`; `write_operational(report, out_dir, client="Client") -> dict[str, Path]`; `build_deck(report, *, client="Client", prepared="", citations=(), confidence=0.85) -> Deliverable` — the job template.
- `src/deliverable.py`: `Finding(title, detail, impact="")`; `Kpi(name, value, target="", rationale="")`; `DataSource(field, source, cadence="")`; `Deliverable(title, client, summary, findings=(), kpis=(), data_sources=(), recommendations=(), options=(), citations=(), confidence=None, residual="", prepared="", ...)`; `Deliverable.write_all(out_dir) -> dict[str, Path]`.
- `src/export.py::write_summary_csv(rows, path) -> Path`.
- `scm_agent/registry.py`: `Tool(key, title, description, intent_keywords, requires_data, prepare, run, qa, deliver, deck=None, options=None, required_client_params=())`; `Prepared(status, payload=None, messages=[])`; `Produced(report, summary)`.
- `scm_agent/types.py::JobRequest(brief, data_path=None, job_type=None, params={}, client="Client", strict_params=False)`.
- `scm_agent/tools.py`: import block, `_sourcing_prepare/_sourcing_run/sourcing_tool()` factory shape, `build_default_registry()` register block, `replace` already imported.
- `scm_agent/citation_gate.py::TOOL_CONCEPTS` map (bare concept ids); `tests/test_citation_gate.py::test_every_anchor_concept_exists` validates every id exists.

---

## Task 1 — Pure engine `src/supplier_management.py` + unit tests

Deliverable: a pure, deterministic Kraljic-segmentation module with numeric assertions against a hand-checkable 4-supplier instance (one supplier in each quadrant). Independently committable.

### Files
- Create `src/supplier_management.py`
- Create `tests/test_supplier_management.py`

### Interfaces
**Produces:**
- `RiskDriver(name: str, weight: float = 1.0)` (frozen)
- `SupplierInput(supplier: str, annual_value: float, risk_scores: dict[str, float])` (frozen) — `risk_scores` already normalized to `[0,1]`, higher = riskier.
- `SupplierSegment(supplier, annual_value, spend_share, profit_impact, impact_band, supply_risk, risk_band, quadrant, strategy)` (frozen)
- `composite_risk(risk_scores: dict[str, float], drivers: list[RiskDriver]) -> float`
- `segment_suppliers(suppliers: list[SupplierInput], drivers: list[RiskDriver], *, impact_pareto: float = 0.8, risk_threshold: float = 0.5) -> list[SupplierSegment]`
- `segment_outcome(segments: list[SupplierSegment], *, summary: str, action_prefix: str = "stage:srm:") -> GuidedOutcome`

**Consumes:** `src.guided.ExecutionOption`, `src.guided.GuidedOutcome`, `src.guided.as_options`.

### Steps

1. **Write the failing unit test** `tests/test_supplier_management.py`:
```python
"""Kraljic supplier segmentation (strategic SRM, CSCP gap) - pure engine tests.

A hand-checkable 4-supplier panel lands exactly one supplier in each Kraljic
quadrant, so every band boundary and strategy mapping is asserted numerically.
"""

import math

import pytest

from src.guided import verify_guided
from src.supplier_management import (
    RiskDriver,
    SupplierInput,
    composite_risk,
    segment_outcome,
    segment_suppliers,
)

_DRIVERS = [RiskDriver("lead", 1.0), RiskDriver("single", 1.0)]


def _panel() -> list[SupplierInput]:
    # spends: A=500, B=300, C=120, D=80  (total 1000)
    # Pareto 0.8 -> A,B are high impact (cum-before < 0.8); C,D low.
    return [
        SupplierInput("A", 500.0, {"lead": 1.0, "single": 1.0}),  # high impact, high risk
        SupplierInput("B", 300.0, {"lead": 0.2, "single": 0.0}),  # high impact, low risk
        SupplierInput("C", 120.0, {"lead": 0.8, "single": 1.0}),  # low impact,  high risk
        SupplierInput("D", 80.0, {"lead": 0.1, "single": 0.0}),   # low impact,  low risk
    ]


def test_composite_risk_is_weighted_average_of_drivers():
    assert composite_risk({"lead": 1.0, "single": 1.0}, _DRIVERS) == pytest.approx(1.0)
    assert composite_risk({"lead": 0.2, "single": 0.0}, _DRIVERS) == pytest.approx(0.1)
    assert composite_risk({"lead": 0.8, "single": 1.0}, _DRIVERS) == pytest.approx(0.9)


def test_composite_risk_honors_unequal_weights():
    drivers = [RiskDriver("lead", 3.0), RiskDriver("single", 1.0)]
    # (3*1.0 + 1*0.0) / 4 = 0.75
    assert composite_risk({"lead": 1.0, "single": 0.0}, drivers) == pytest.approx(0.75)


def test_segment_assigns_one_supplier_to_each_kraljic_quadrant():
    segs = {s.supplier: s for s in segment_suppliers(_panel(), _DRIVERS)}

    assert segs["A"].quadrant == "strategic"
    assert segs["B"].quadrant == "leverage"
    assert segs["C"].quadrant == "bottleneck"
    assert segs["D"].quadrant == "non_critical"

    assert segs["A"].impact_band == "high" and segs["A"].risk_band == "high"
    assert segs["B"].impact_band == "high" and segs["B"].risk_band == "low"
    assert segs["C"].impact_band == "low" and segs["C"].risk_band == "high"
    assert segs["D"].impact_band == "low" and segs["D"].risk_band == "low"


def test_segment_computes_spend_share_and_supply_risk():
    segs = {s.supplier: s for s in segment_suppliers(_panel(), _DRIVERS)}
    assert segs["A"].spend_share == pytest.approx(0.5)
    assert segs["B"].spend_share == pytest.approx(0.3)
    assert segs["A"].supply_risk == pytest.approx(1.0)
    assert segs["C"].supply_risk == pytest.approx(0.9)
    assert segs["A"].profit_impact == pytest.approx(0.5)


def test_strategy_maps_from_quadrant():
    segs = {s.supplier: s for s in segment_suppliers(_panel(), _DRIVERS)}
    assert segs["A"].strategy.startswith("partner")
    assert segs["B"].strategy.startswith("competitive tender")
    assert segs["C"].strategy.startswith("secure supply")
    assert segs["D"].strategy.startswith("simplify")
    for s in segs.values():
        assert s.strategy.isascii()


def test_risk_threshold_is_configurable():
    # Raise the bar so C (0.9) stays high but a 0.85 supplier would flip.
    segs = {s.supplier: s for s in segment_suppliers(_panel(), _DRIVERS, risk_threshold=0.95)}
    assert segs["C"].risk_band == "low"        # 0.9 < 0.95
    assert segs["A"].risk_band == "high"       # 1.0 >= 0.95


def test_segment_outcome_is_a_protected_options_result():
    segs = segment_suppliers(_panel(), _DRIVERS)
    outcome = segment_outcome(segs, summary="Segmented 4 suppliers.")
    assert outcome.status == "options"
    assert verify_guided(outcome) == []
    # exposure = spend_share * supply_risk -> A (0.5) is the top priority.
    assert outcome.options[0].label == "A"
    assert any(o.recommended for o in outcome.options)
    for o in outcome.options:
        assert math.isfinite(o.score)


def test_empty_panel_returns_no_segments():
    assert segment_suppliers([], _DRIVERS) == []


def test_segment_outcome_raises_on_empty_segments():
    with pytest.raises(ValueError):
        segment_outcome([], summary="nothing")
```

2. **Run it — expect failure** (module does not exist):
```
PYTHONPATH=. py -m pytest tests/test_supplier_management.py -q
```
Expected: `ModuleNotFoundError: No module named 'src.supplier_management'`.

3. **Write the minimal implementation** `src/supplier_management.py`:
```python
"""Kraljic supplier segmentation + strategic SRM (CSCP coverage gap).

Places each supplier on the Kraljic (1983) purchasing-portfolio matrix: a
profit-impact axis (share of annual spend, cut by cumulative-spend Pareto) x a
supply-risk axis (a weighted composite of normalized risk drivers). The four
quadrants map to the standard SRM playbook - strategic -> partner/develop,
bottleneck -> secure supply/dual-source, leverage -> competitive tender,
non-critical -> simplify/automate.

Pure (stdlib + src.guided only): mirrors the weighted-composite -> banding
template of src/multi_criteria_classification.py and returns a never-unprotected
GuidedOutcome exactly like src/mcdm.py::award_outcome. Risk drivers arrive
already normalized to [0,1] (higher = riskier); normalization is the job layer's
job (jobs/supplier_management_job.py), keeping this module free of pandas/I/O.

Grounded in L3: knowledge::kraljic_matrix (Grant, Sustainable Logistics &
Supply Chain, Ch6 Sustainable Purchasing and Procurement).
"""

from __future__ import annotations

from dataclasses import dataclass

from src.guided import ExecutionOption, GuidedOutcome, as_options

# (impact_band, risk_band) -> Kraljic quadrant.
_QUADRANT = {
    ("high", "high"): "strategic",
    ("low", "high"): "bottleneck",
    ("high", "low"): "leverage",
    ("low", "low"): "non_critical",
}

# Quadrant -> the SRM strategy it prescribes (ASCII-only for cp1252 safety).
QUADRANT_STRATEGY = {
    "strategic": "partner / develop - joint roadmap, long-term contract, dual capacity",
    "bottleneck": "secure supply / dual-source - buffer stock, qualify alternates, hedge",
    "leverage": "competitive tender - exploit buying power, RFQ/spot, consolidate volume",
    "non_critical": "simplify / automate - catalog buy, P-card, cut transaction cost",
}


@dataclass(frozen=True)
class RiskDriver:
    """One supply-risk axis input and its weight in the composite."""

    name: str
    weight: float = 1.0


@dataclass(frozen=True)
class SupplierInput:
    """A supplier with its annual spend and normalized [0,1] risk driver scores."""

    supplier: str
    annual_value: float
    risk_scores: dict[str, float]


@dataclass(frozen=True)
class SupplierSegment:
    """A fully-placed supplier: both axes, its quadrant and prescribed strategy."""

    supplier: str
    annual_value: float
    spend_share: float          # annual_value / total spend, [0,1]
    profit_impact: float        # == spend_share (the profit-impact axis value)
    impact_band: str            # "high" | "low"
    supply_risk: float          # composite risk, [0,1]
    risk_band: str              # "high" | "low"
    quadrant: str               # strategic | bottleneck | leverage | non_critical
    strategy: str


def composite_risk(risk_scores: dict[str, float], drivers: list[RiskDriver]) -> float:
    """Weighted average of the (already-normalized) driver scores; 0.0 if no weight."""
    total_w = sum(d.weight for d in drivers)
    if total_w <= 0:
        return 0.0
    acc = sum(d.weight * float(risk_scores.get(d.name, 0.0)) for d in drivers)
    return acc / total_w


def segment_suppliers(
    suppliers: list[SupplierInput],
    drivers: list[RiskDriver],
    *,
    impact_pareto: float = 0.8,
    risk_threshold: float = 0.5,
) -> list[SupplierSegment]:
    """Place each supplier on the Kraljic matrix.

    Profit-impact: suppliers are ranked by spend descending; a supplier is "high"
    impact while the cumulative spend share *before* it is below ``impact_pareto``
    (the vital few carrying the top ~80% of spend), "low" otherwise.
    Supply-risk: composite driver score >= ``risk_threshold`` is "high".
    """
    if not suppliers:
        return []
    if not 0.0 < impact_pareto <= 1.0:
        raise ValueError("impact_pareto must be in (0, 1]")

    total = sum(s.annual_value for s in suppliers)
    if total <= 0:
        raise ValueError("total annual spend must be positive")

    ordered = sorted(suppliers, key=lambda s: s.annual_value, reverse=True)
    segments: list[SupplierSegment] = []
    cum_before = 0.0
    for s in ordered:
        share = s.annual_value / total
        impact_band = "high" if cum_before < impact_pareto - 1e-9 else "low"
        cum_before += share

        risk = composite_risk(s.risk_scores, drivers)
        risk_band = "high" if risk >= risk_threshold else "low"

        quadrant = _QUADRANT[(impact_band, risk_band)]
        segments.append(
            SupplierSegment(
                supplier=s.supplier,
                annual_value=s.annual_value,
                spend_share=share,
                profit_impact=share,
                impact_band=impact_band,
                supply_risk=risk,
                risk_band=risk_band,
                quadrant=quadrant,
                strategy=QUADRANT_STRATEGY[quadrant],
            )
        )
    return segments


def segment_outcome(
    segments: list[SupplierSegment],
    *,
    summary: str,
    action_prefix: str = "stage:srm:",
) -> GuidedOutcome:
    """Present a priority action list (highest spend x risk exposure first).

    Exposure = spend_share * supply_risk - the strategic/bottleneck suppliers
    carrying both weight and risk float to the top; the best is auto-recommended.
    """
    if not segments:
        raise ValueError("no segments to build an outcome from")
    ranked = sorted(segments, key=lambda s: s.spend_share * s.supply_risk, reverse=True)
    options = [
        ExecutionOption(
            label=s.supplier,
            summary=f"{s.quadrant}: {s.strategy}",
            score=s.spend_share * s.supply_risk,
            action=f"{action_prefix}{s.quadrant}:{s.supplier}",
            tradeoffs=f"spend share {s.spend_share * 100:.0f}%, supply risk {s.supply_risk:.2f}",
        )
        for s in ranked
    ]
    return as_options(summary, options)
```

4. **Run the test — expect pass:**
```
PYTHONPATH=. py -m pytest tests/test_supplier_management.py -q
```
5. **Lint:** `ruff check src/supplier_management.py tests/test_supplier_management.py`
6. **Commit:** `feat: Kraljic supplier segmentation pure engine (src/supplier_management.py)`

---

## Task 2 — Job layer `jobs/supplier_management_job.py` + job tests

Deliverable: a pandas `prepare()` that reads its own suppliers CSV, sniffs the spend column + risk-driver columns and normalizes each driver to `[0,1]`, then `run/verify/write_operational/build_deck`. Independently committable.

### Files
- Create `jobs/supplier_management_job.py`
- Create `tests/test_supplier_management_job.py`

### Interfaces
**Produces:**
- `SupplierManagementReport(segments: tuple[SupplierSegment, ...], drivers: tuple[RiskDriver, ...], outcome: GuidedOutcome, quadrant_counts: dict[str, int], summary: str)` (frozen)
- `normalize_drivers(df, *, driver_cols: dict[str, str]) -> dict[str, dict[str, float]]` (supplier-indexed normalized scores)
- `prepare(data_path: str, params: dict | None = None) -> dict`
- `run(suppliers: list[SupplierInput], drivers: list[RiskDriver], *, impact_pareto: float = 0.8, risk_threshold: float = 0.5) -> SupplierManagementReport`
- `verify(report: SupplierManagementReport) -> list[str]`
- `write_operational(report, out_dir, client="Client") -> dict[str, Path]`
- `build_deck(report, *, client="Client", prepared="", citations=(), confidence=0.85) -> Deliverable`

**Consumes:** `pandas`; `src.supplier_management` (Task 1); `src.deliverable.{DataSource, Deliverable, Finding, Kpi}`; `src.export.write_summary_csv`; `src.guided.verify_guided`.

### Steps

1. **Write the failing job test** `tests/test_supplier_management_job.py`:
```python
"""Kraljic supplier-segmentation job: suppliers CSV -> normalized drivers -> deck."""

from pathlib import Path

import pandas as pd
import pytest

from jobs import supplier_management_job as smj
from src.deliverable import Deliverable


def _suppliers_df() -> pd.DataFrame:
    return pd.DataFrame({
        "supplier": ["A", "B", "C", "D"],
        "annual_spend": [500.0, 300.0, 120.0, 80.0],
        "lead_time_days": [40, 8, 34, 5],       # min 5, max 40 -> min-max normalized
        "single_source": [1, 0, 1, 0],
        "defect_ppm": [3000, 100, 2500, 50],
    })


def test_normalize_drivers_min_max_scales_to_unit_interval():
    df = _suppliers_df()
    norm = smj.normalize_drivers(
        df, driver_cols={"lead": "lead_time_days", "single": "single_source", "ppm": "defect_ppm"}
    )
    # lead: A=40 -> 1.0 (max), D=5 -> 0.0 (min)
    assert norm["A"]["lead"] == pytest.approx(1.0)
    assert norm["D"]["lead"] == pytest.approx(0.0)
    # boolean single-source passes through as 0/1
    assert norm["A"]["single"] == pytest.approx(1.0)
    assert norm["B"]["single"] == pytest.approx(0.0)


def test_normalize_constant_column_is_zero_risk():
    df = pd.DataFrame({"supplier": ["X", "Y"], "annual_spend": [1.0, 1.0], "geo": [3, 3]})
    norm = smj.normalize_drivers(df, driver_cols={"geo": "geo"})
    assert norm["X"]["geo"] == pytest.approx(0.0)
    assert norm["Y"]["geo"] == pytest.approx(0.0)


def test_prepare_reads_csv_and_builds_supplier_inputs(tmp_path):
    csv = tmp_path / "sup.csv"
    _suppliers_df().to_csv(csv, index=False)
    payload = smj.prepare(str(csv), {})
    assert {s.supplier for s in payload["suppliers"]} == {"A", "B", "C", "D"}
    assert {d.name for d in payload["drivers"]}  # at least one driver detected


def test_prepare_errors_without_a_supplier_column(tmp_path):
    csv = tmp_path / "bad.csv"
    pd.DataFrame({"foo": [1], "annual_spend": [10]}).to_csv(csv, index=False)
    with pytest.raises(ValueError, match="supplier"):
        smj.prepare(str(csv), {})


def test_prepare_errors_without_a_spend_column(tmp_path):
    csv = tmp_path / "bad.csv"
    pd.DataFrame({"supplier": ["A"], "lead_time_days": [10]}).to_csv(csv, index=False)
    with pytest.raises(ValueError, match="spend"):
        smj.prepare(str(csv), {})


def test_run_places_each_supplier_and_counts_quadrants(tmp_path):
    csv = tmp_path / "sup.csv"
    _suppliers_df().to_csv(csv, index=False)
    payload = smj.prepare(str(csv), {})
    report = smj.run(payload["suppliers"], payload["drivers"])

    by = {s.supplier: s for s in report.segments}
    assert by["A"].quadrant == "strategic"      # top spend + long lead + single-source
    assert by["D"].quadrant == "non_critical"   # low spend + low risk
    assert sum(report.quadrant_counts.values()) == 4
    assert smj.verify(report) == []


def test_write_operational_emits_one_row_per_supplier(tmp_path):
    csv = tmp_path / "sup.csv"
    _suppliers_df().to_csv(csv, index=False)
    payload = smj.prepare(str(csv), {})
    report = smj.run(payload["suppliers"], payload["drivers"])
    out = smj.write_operational(report, tmp_path, client="Acme")
    df = pd.read_csv(out["csv"])
    assert len(df) == 4
    assert {"supplier", "quadrant", "spend_share", "supply_risk", "strategy"} <= set(df.columns)


def test_build_deck_is_an_ascii_deliverable_naming_the_quadrants(tmp_path):
    csv = tmp_path / "sup.csv"
    _suppliers_df().to_csv(csv, index=False)
    payload = smj.prepare(str(csv), {})
    report = smj.run(payload["suppliers"], payload["drivers"])
    deck = smj.build_deck(report, client="Acme", citations=("Kraljic - purchasing portfolio",))
    assert isinstance(deck, Deliverable)
    md = deck.to_markdown()
    assert md.isascii()
    assert "strategic" in md and "## Coverage & handoff" in md
```

2. **Run it — expect failure** (`ModuleNotFoundError: jobs.supplier_management_job`).

3. **Write the implementation** `jobs/supplier_management_job.py`:
```python
"""Kraljic supplier-segmentation job: suppliers CSV -> normalized risk drivers -> deck.

The data-prep + deck half of the supplier_management tool. Reads a suppliers CSV
with pandas directly (deliberately NOT via jobs/intake.py), sniffs the spend
column and any present risk-driver columns, min-max normalizes each driver to
[0,1] (higher = riskier), then segments on the Kraljic matrix via the pure
src/supplier_management engine and composes the deck inline.

``run``/``verify``/``build_deck`` are deterministic; ``prepare`` reads a file.
Column names are sniffed and overridable via params.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from src.deliverable import DataSource, Deliverable, Finding, Kpi
from src.export import write_summary_csv
from src.guided import GuidedOutcome, verify_guided
from src.supplier_management import (
    RiskDriver,
    SupplierInput,
    SupplierSegment,
    segment_outcome,
    segment_suppliers,
)

_SUPPLIER_COLS = ("supplier", "Supplier", "vendor", "Vendor", "supplier_name", "supplier_id")
_SPEND_COLS = ("annual_value", "annual_spend", "spend", "spend_usd", "annual_value_usd", "value", "Spend")

# risk driver key -> candidate column names (all "higher = riskier").
_DRIVER_COLS: dict[str, tuple[str, ...]] = {
    "lead": ("lead_time_days", "lead_time", "Lead Time", "lead"),
    "single": ("single_source", "sole_source", "single_sourced", "sole_sourced"),
    "quality": ("defect_ppm", "ppm", "defect_rate", "reject_rate"),
    "financial": ("financial_risk", "credit_risk", "supplier_financial_risk"),
    "geo": ("geo_risk", "country_risk", "geopolitical_risk"),
}


@dataclass(frozen=True)
class SupplierManagementReport:
    segments: tuple[SupplierSegment, ...]
    drivers: tuple[RiskDriver, ...]
    outcome: GuidedOutcome
    quadrant_counts: dict[str, int]
    summary: str


def _pick_column(df: pd.DataFrame, override: object, candidates: tuple[str, ...]) -> str | None:
    if override:
        return str(override) if str(override) in df.columns else None
    return next((c for c in candidates if c in df.columns), None)


def _resolve_driver_cols(df: pd.DataFrame, params: dict) -> dict[str, str]:
    """Map each risk-driver key to a present column (params override the sniffing)."""
    overrides: dict[str, str] = params.get("risk_cols", {}) or {}
    resolved: dict[str, str] = {}
    for key, candidates in _DRIVER_COLS.items():
        col = _pick_column(df, overrides.get(key), candidates)
        if col is not None:
            resolved[key] = col
    return resolved


def normalize_drivers(df: pd.DataFrame, *, driver_cols: dict[str, str]) -> dict[str, dict[str, float]]:
    """Min-max scale each driver column to [0,1] (constant column -> all 0.0).

    Returns supplier-index -> {driver_key: normalized score}. The supplier index is
    the DataFrame's positional index; callers join it back by row order.
    """
    normed: dict[str, dict[str, float]] = {}
    for key, col in driver_cols.items():
        series = pd.to_numeric(df[col], errors="coerce").fillna(0.0).astype(float)
        lo, hi = float(series.min()), float(series.max())
        span = hi - lo
        scaled = (series - lo) / span if span > 1e-12 else series * 0.0
        for idx, val in scaled.items():
            normed.setdefault(str(idx), {})[key] = float(val)
    return normed


def prepare(data_path: str, params: dict | None = None) -> dict:
    """Read a suppliers CSV and build normalized SupplierInputs + the driver set."""
    params = params or {}
    df = pd.read_csv(data_path)

    supplier_col = _pick_column(df, params.get("supplier_col"), _SUPPLIER_COLS)
    if supplier_col is None:
        raise ValueError(
            f"could not find a supplier column; pass supplier_col in params "
            f"(columns seen: {list(df.columns)[:10]})"
        )
    spend_col = _pick_column(df, params.get("spend_col"), _SPEND_COLS)
    if spend_col is None:
        raise ValueError(
            f"could not find an annual spend/value column; pass spend_col in params "
            f"(columns seen: {list(df.columns)[:10]})"
        )

    driver_cols = _resolve_driver_cols(df, params)
    df = df.reset_index(drop=True)
    normed = normalize_drivers(df, driver_cols=driver_cols)

    suppliers: list[SupplierInput] = []
    for idx, row in df.iterrows():
        suppliers.append(
            SupplierInput(
                supplier=str(row[supplier_col]),
                annual_value=float(pd.to_numeric(row[spend_col], errors="coerce") or 0.0),
                risk_scores=normed.get(str(idx), {}),
            )
        )
    drivers = [RiskDriver(key, 1.0) for key in driver_cols]
    return {"suppliers": suppliers, "drivers": drivers}


def run(
    suppliers: list[SupplierInput],
    drivers: list[RiskDriver],
    *,
    impact_pareto: float = 0.8,
    risk_threshold: float = 0.5,
) -> SupplierManagementReport:
    """Segment suppliers on the Kraljic matrix and present the SRM action list."""
    segments = segment_suppliers(
        suppliers, drivers, impact_pareto=impact_pareto, risk_threshold=risk_threshold
    )
    counts = {q: 0 for q in ("strategic", "bottleneck", "leverage", "non_critical")}
    for s in segments:
        counts[s.quadrant] += 1
    summary = (
        f"Segmented {len(segments)} suppliers on the Kraljic matrix: "
        f"{counts['strategic']} strategic, {counts['bottleneck']} bottleneck, "
        f"{counts['leverage']} leverage, {counts['non_critical']} non-critical."
    )
    outcome = (
        segment_outcome(segments, summary=summary)
        if segments
        else GuidedOutcome(status="executed", summary=summary)
    )
    return SupplierManagementReport(
        segments=tuple(segments),
        drivers=tuple(drivers),
        outcome=outcome,
        quadrant_counts=counts,
        summary=summary,
    )


def verify(report: SupplierManagementReport) -> list[str]:
    """QA gate: a usable segmentation honors the contract and placed suppliers."""
    issues = list(verify_guided(report.outcome))
    if not report.segments:
        issues.append("no suppliers segmented")
    return issues


def write_operational(
    report: SupplierManagementReport, out_dir: str | Path, client: str = "Client"
) -> dict[str, Path]:
    """The machine-readable deliverable: one row per supplier with its quadrant."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "supplier": s.supplier,
            "annual_value": round(s.annual_value, 2),
            "spend_share": round(s.spend_share, 4),
            "impact_band": s.impact_band,
            "supply_risk": round(s.supply_risk, 4),
            "risk_band": s.risk_band,
            "quadrant": s.quadrant,
            "strategy": s.strategy,
        }
        for s in report.segments
    ]
    return {"csv": write_summary_csv(rows, d / "supplier_segmentation.csv")}


def build_deck(
    report: SupplierManagementReport,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple[str, ...] = (),
    confidence: float = 0.85,
) -> Deliverable:
    """Compose the Kraljic segmentation study: quadrant mix + the priority actions."""
    counts = report.quadrant_counts
    strategic = [s for s in report.segments if s.quadrant == "strategic"]
    bottleneck = [s for s in report.segments if s.quadrant == "bottleneck"]

    findings = [
        Finding(
            "Kraljic quadrant mix",
            f"{counts['strategic']} strategic, {counts['bottleneck']} bottleneck, "
            f"{counts['leverage']} leverage, {counts['non_critical']} non-critical.",
            impact="each quadrant gets a distinct SRM playbook, not one-size-fits-all",
        )
    ]
    if strategic:
        names = ", ".join(s.supplier for s in strategic)
        findings.append(Finding(
            "Strategic suppliers - partner and develop",
            f"High spend AND high supply risk: {names}. These carry the business; "
            "manage them as long-term partnerships with joint planning and dual capacity.",
            impact="protect continuity of the highest-exposure spend",
        ))
    if bottleneck:
        names = ", ".join(s.supplier for s in bottleneck)
        findings.append(Finding(
            "Bottleneck suppliers - secure supply",
            f"Low spend but high supply risk: {names}. Small money, big disruption risk; "
            "buffer stock, qualify alternates and hedge the single points of failure.",
            impact="cut the tail risk that low spend hides",
        ))

    kpis = (
        Kpi("Suppliers segmented", str(len(report.segments)), rationale="Panel breadth"),
        Kpi("Strategic", str(counts["strategic"]), rationale="High impact + high risk"),
        Kpi("Bottleneck", str(counts["bottleneck"]), rationale="Low impact + high risk"),
        Kpi("Leverage", str(counts["leverage"]), rationale="High impact + low risk"),
        Kpi("Non-critical", str(counts["non_critical"]), rationale="Low impact + low risk"),
    )

    data_sources = (
        DataSource("Annual spend by supplier", "AP / procurement spend cube", "quarterly"),
        DataSource("Supply-risk drivers (lead time, single-source, quality, financial, geo)",
                   "supplier master / scorecards", "quarterly"),
    )

    recommendations = [
        "Partner and develop the strategic suppliers; lock long-term contracts with dual capacity.",
        "Dual-source or buffer every bottleneck supplier before its single point of failure bites.",
        "Run competitive tenders on leverage suppliers to convert buying power into savings.",
        "Automate non-critical spend (catalog / P-card) to reclaim buyer time.",
    ]

    return Deliverable(
        title="Supplier Portfolio Segmentation (Kraljic)",
        client=client,
        summary=report.summary,
        findings=tuple(findings),
        kpis=kpis,
        data_sources=data_sources,
        recommendations=tuple(recommendations),
        citations=tuple(citations),
        confidence=confidence,
        residual="Quadrant placement is analytical; the actual partnership, dual-sourcing and "
                 "tender decisions are commercial calls - this prepares the segmentation and the "
                 "prioritized action list; a human owns each supplier move.",
        prepared=prepared,
    )
```

4. **Run — expect pass:** `PYTHONPATH=. py -m pytest tests/test_supplier_management_job.py -q`
5. **Lint:** `ruff check jobs/supplier_management_job.py tests/test_supplier_management_job.py`
6. **Commit:** `feat: supplier_management job layer (prepare/run/verify/deck)`

---

## Task 3 — Tool wiring, citation anchor, orchestrator tests

Deliverable: `supplier_management` registered in the default registry, routable by distinctive keywords, grounded in the citation gate, with a routing + end-to-end orchestrator test. Independently committable.

### Files
- Modify `scm_agent/tools.py` (import, `_supplier_management_prepare`, `_supplier_management_run`, `supplier_management_tool()`, register call)
- Modify `scm_agent/citation_gate.py` (`TOOL_CONCEPTS` entry)
- Create `tests/test_supplier_management_tool.py`

### Interfaces
**Produces:** `supplier_management_tool() -> Tool` (key `"supplier_management"`); a `TOOL_CONCEPTS["supplier_management"]` anchor tuple.
**Consumes:** `jobs.supplier_management_job`; `scm_agent.registry.{Prepared, Produced, Tool}`; `scm_agent.types.JobRequest`; `scm_agent.orchestrator.Orchestrator`; `scm_agent.{intent, llm, tools}`.

### Steps

1. **Write the failing tool test** `tests/test_supplier_management_tool.py`:
```python
"""supplier_management tool: routing + orchestrator end-to-end + citation anchor."""

from pathlib import Path

import pandas as pd

from scm_agent import citation_gate, intent, llm, tools
from scm_agent.orchestrator import Orchestrator


def _suppliers_csv(path: Path) -> Path:
    pd.DataFrame({
        "supplier": ["A", "B", "C", "D"],
        "annual_spend": [500.0, 300.0, 120.0, 80.0],
        "lead_time_days": [40, 8, 34, 5],
        "single_source": [1, 0, 1, 0],
        "defect_ppm": [3000, 100, 2500, 50],
    }).to_csv(path, index=False)
    return path


def test_supplier_management_is_registered():
    reg = tools.build_default_registry()
    assert reg.get("supplier_management").key == "supplier_management"


def test_brief_routes_to_supplier_management():
    reg = tools.build_default_registry()
    res = intent.classify(
        "segment our suppliers on the kraljic matrix by profit impact and supply risk",
        reg, llm.RulesFallback(),
    )
    assert res.job_type == "supplier_management"


def test_supplier_management_keywords_do_not_steal_the_sourcing_brief():
    reg = tools.build_default_registry()
    p = llm.RulesFallback()
    assert intent.classify(
        "select the best supplier / sourcing award by OTIF and price", reg, p
    ).job_type == "sourcing"


def test_citation_anchor_is_registered_and_exists():
    assert "supplier_management" in citation_gate.TOOL_CONCEPTS
    assert "kraljic_matrix" in citation_gate.TOOL_CONCEPTS["supplier_management"]


def test_orchestrator_runs_supplier_management_and_emits_the_deck(tmp_path):
    csv = _suppliers_csv(tmp_path / "sup.csv")
    orch = Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback())
    res = orch.run(
        "segment suppliers on the kraljic matrix by profit impact and supply risk",
        data_path=str(csv), client="Acme", out_dir=tmp_path,
    )
    assert res.status == "ok"
    assert res.tool == "supplier_management"
    assert "csv" in res.deliverables
    deck = Path(res.deliverables["deck_report"])
    assert deck.exists()
    assert "strategic" in deck.read_text(encoding="utf-8")
```

2. **Run it — expect failure** (`KeyError`/no such tool `supplier_management`; missing citation anchor).

3. **Add the citation anchor** in `scm_agent/citation_gate.py` — insert into `TOOL_CONCEPTS` (e.g. after the `"sourcing"` entry):
```python
    "supplier_management": (
        "kraljic_matrix", "supplier_relationship_management",
        "supplier_development", "procurement",
    ),
```
(4 anchors, all verified present in `knowledge/scm-books/graph.json`; under the <= 8 pool ceiling.)

4. **Wire the tool** in `scm_agent/tools.py`.

   a. Add `supplier_management_job` to the `from jobs import (...)` block (alphabetical, after `sourcing_job` or near it):
```python
    supplier_management_job,
```

   b. Add the factory + prepare/run helpers (place after the `sourcing_tool()` block, before `# ---- ddmrp`):
```python
# ---- supplier_management (Kraljic segmentation / strategic SRM) ---------------

def _supplier_management_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    if not request.data_path:
        return Prepared(
            status="needs_data",
            messages=["a suppliers CSV (supplier, annual spend, plus risk-driver columns "
                      "like lead time / single-source / quality) is required"],
        )
    try:
        payload = supplier_management_job.prepare(request.data_path, request.params)
    except (ValueError, FileNotFoundError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    if not payload["suppliers"]:
        return Prepared(status="needs_data", messages=["no suppliers found in the data"])
    return Prepared(status="ok", payload=payload)


def _supplier_management_run(payload: object, params: dict) -> Produced:
    report = supplier_management_job.run(
        payload["suppliers"], payload["drivers"],
        impact_pareto=params.get("impact_pareto", 0.8),
        risk_threshold=params.get("risk_threshold", 0.5),
    )
    return Produced(report=report, summary=report.summary)


def supplier_management_tool() -> Tool:
    return Tool(
        key="supplier_management",
        title="Supplier Portfolio Segmentation (Kraljic)",
        description="Segment suppliers on the Kraljic matrix (profit impact x supply risk) into "
                    "strategic / bottleneck / leverage / non-critical quadrants and map each to a "
                    "strategic-SRM playbook.",
        intent_keywords=(
            "kraljic matrix", "kraljic", "supplier segmentation", "supplier portfolio",
            "purchasing portfolio", "strategic supplier relationship", "supplier relationship management",
            "srm", "supply risk segmentation", "supplier quadrant", "categorize suppliers",
        ),
        requires_data=True,
        options=lambda report: report.outcome,
        prepare=_supplier_management_prepare,
        run=_supplier_management_run,
        qa=lambda report: supplier_management_job.verify(report),
        deliver=lambda report, out_dir, client: supplier_management_job.write_operational(
            report, out_dir, client
        ),
        deck=lambda report, out_dir, client, citations, confidence, options: replace(
            supplier_management_job.build_deck(
                report, client=client, citations=tuple(citations), confidence=confidence
            ),
            options=tuple(options),
        ).write_all(out_dir),
    )
```

   c. Register it in `build_default_registry()` (add after `reg.register(sourcing_tool())`):
```python
    reg.register(supplier_management_tool())
```

5. **Run the tool test — expect pass:** `PYTHONPATH=. py -m pytest tests/test_supplier_management_tool.py -q`
6. **Run the full pure+job+tool trio and the citation-gate + intent suites** (guard against regressions):
```
PYTHONPATH=. py -m pytest tests/test_supplier_management.py tests/test_supplier_management_job.py tests/test_supplier_management_tool.py tests/test_citation_gate.py -q
```
7. **Lint the changed scope:** `ruff check src tests examples` (note: CI lints `src tests examples`; also run `ruff check scm_agent jobs` locally for the touched non-CI modules).
8. **Commit:** `feat: register supplier_management tool + Kraljic citation anchor`

---

## Final verification (before marking the PR ready)
1. Full suite: `PYTHONPATH=. py -m pytest tests/ -q` (must stay green — the intent-collision test guards other tools' routing).
2. Confirm keyword non-collision explicitly: the `sourcing` tool owns "supplier selection / supplier scorecard / supplier performance / procurement / vendor selection"; `supplier_management` owns "kraljic / supplier segmentation / purchasing portfolio / SRM / supplier quadrant". The shared word "supplier" is fine because `intent.classify` scores multi-word keyword overlap; the two keyword sets share no full phrase.
3. ASCII check already asserted in the deck tests (`md.isascii()`).
4. Open the draft PR; ensure CI passes on py3.11/3.12/3.13 including the `prod-boot` job (no module-level optional-extra import was added).

## Task dependency / ordering
Task 1 -> Task 2 -> Task 3 (strict; Task 2 imports Task 1's module, Task 3 imports Task 2's job). Each task is independently committable and leaves the suite green.

## Open questions / decisions flagged during drafting

- Supply-risk axis design: this plan builds the risk axis as a normalized weighted composite (mirroring src/multi_criteria_classification.py, the recipe's cited template) rather than wiring src/risk.py::assess(). Reason: assess() consumes likelihood(0..1) + impact_value($) per RiskFactor, which per-supplier risk-driver columns (lead time, single-source flag, quality ppm) do not naturally provide, and forcing that mapping would be an unauditable stretch. src/risk.py's CATEGORIES ('supply'/'concentration') and its 1-5 rater thresholds are honored conceptually (the [0,1] composite + 0.5 band cut). Confirm this is acceptable, or specify the exact likelihood/impact mapping if genuine risk.assess() reuse is required.
- Profit-impact banding method: chosen = cumulative-spend Pareto (default impact_pareto=0.8 -> the vital few carrying the top ~80% of spend are 'high impact'). Alternatives considered: (a) per-supplier spend-share threshold, (b) classic ABC count-band via classify_multicriteria. Pareto is the most standard Kraljic reading and hand-checkable; confirm the 0.8 default and the 'crossing supplier stays high' inclusive rule.
- risk_threshold default = 0.5 on the normalized [0,1] composite is a reasonable midpoint but arbitrary. A percentile-based split (e.g. median of the panel) would be self-calibrating but less predictable across small panels. Confirm the fixed 0.5 default or switch to median.
- GuidedOutcome shape: chosen = as_options priority action list ranked by exposure (spend_share * supply_risk), best auto-recommended. Alternative = as_handoff with one prepared packet per strategic supplier. Options keeps parity with sourcing_job's award_outcome and satisfies verify_guided; confirm this over a handoff.
- Default risk-driver column set (lead / single-source / quality-ppm / financial / geo) and equal weights. Real client CSVs vary; params['risk_cols'] and params weights can override, but confirm the auto-detected default drivers and that equal weighting (not, e.g., a BWM elicitation via src/mcdm.bwm_weights) is acceptable for v1.
