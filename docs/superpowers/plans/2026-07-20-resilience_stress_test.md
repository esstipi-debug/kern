> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax where present. Part of the CSCP/SCPro gap-closing initiative -- see docs/superpowers/specs/2026-07-20-cscp-scpro-gap-closing-design.md.

# Implementation Plan: `resilience_stress_test` (Quantified Network Resilience)

## Goal
Add a Kern agent tool, `resilience_stress_test`, that runs the existing network digital twin **twice under the same seed** (a clean baseline and an injected `Disruption`), then **measures** resilience from the two per-period traces: time-to-recover (TTR), lost units over the recovery window, financial impact (lost units x unit margin), and a bounded resilience index of measured TTR vs a target time-to-survive (TTS). The measured TTR/TTS are then fed into `src/risk.py::assess()` so the risk engine's TTR>TTS exposure gap (Simchi-Levi) becomes a **computed output of a simulated shock** instead of a hand-typed input. Closes the SCOR AG (agility/resilience) gap.

## Architecture
Follows the repo's "New agent tool recipe" exactly, layered:
- **`src/resilience.py`** — pure engine: frozen dataclass `ResilienceResult` + pure functions (`_fill_trace`, `time_to_recover`, `lost_units`, `resilience_index`, `assess_resilience`). Stdlib only, no pandas, no I/O, no numpy. Operates on plain `Sequence[float]` traces so it is testable against hand-checkable instances independent of the twin.
- **`jobs/resilience_stress_test_job.py`** — the measurement harness on top of the DONE injection engine. `prepare()` reuses `jobs/digital_twin_job.prepare()` to build the network/demand/disruption scenario (forcing a disruption), then augments it with resilience params. `run()` calls `src.digital_twin.simulate_network` twice per product (same seed, `disruptions=()` vs `disruptions=(shock,)`), aggregates network served/demand traces, calls `src.resilience.assess_resilience`, then feeds the measured TTR/TTS into `src.risk.assess` to produce ranked mitigation options as a protected `GuidedOutcome`. Plus `verify()`, `write_operational()`, `build_deck()`.
- **`scm_agent/tools.py`** — `resilience_stress_test_tool()` factory + registration in `build_default_registry()`.
- **`scm_agent/citation_gate.py`** — anchor concepts in `TOOL_CONCEPTS` (resilience/agility nodes verified to exist in the books graph).
- **Tests** — `tests/test_resilience.py` (numeric engine assertions) and `tests/test_resilience_stress_test_tool.py` (job + registration + routing + orchestrator + citation-gate).

## Tech Stack
Python 3.11+, frozen `@dataclass`, type hints on all signatures. Engine module stdlib-only; job uses pandas + numpy (both core deps, top-level import is prod-boot-safe). Tests via `PYTHONPATH=. py -m pytest`. Lint `ruff check src tests`.

## Global Constraints
- ASCII-only in every console-facing string (summaries, print, `Finding`/`Kpi` text) — Windows cp1252. Markdown docstrings utf-8 are fine.
- No new module-level import of any optional-extra dep on the boot chain (`webapp.app` -> `scm_agent` -> `tools` -> `jobs` -> `src`). `src.resilience`, `src.digital_twin`, `src.risk`, `src.guided`, `numpy`, `pandas` are all safe.
- Strict TDD per step: write failing test -> run (fails) -> minimal impl -> run (passes) -> commit.
- Never push to `main`. Feature branch -> draft PR -> CI green on py3.11/3.12/3.13 -> squash-merge.
- `scm_agent/citation_gate.py::TOOL_CONCEPTS` MUST contain the new tool key, or `tests/test_citation_gate.py::test_every_registered_tool_has_a_concept_map` fails (it asserts `registered == set(TOOL_CONCEPTS)`).

### Verified existing signatures this plan builds on
- `src/digital_twin.py`: `simulate_network(nodes, demand, *, periods=364, disruptions=(), seed=42, auto_size_factor=1.5) -> TwinResult`. `TwinResult` exposes `.served: dict[str, np.ndarray]`, `.demand: dict[str, np.ndarray]` (store -> per-period trace). `Disruption(kind, target, start, duration, magnitude=1.0)` with `.active(period)`. `NodeSpec(name, kind, supplier=None, lead_time=2, review_period=1, order_up_to=0.0, initial_on_hand=None, capacity=None)`. `DemandProfile(base=100.0, trend=0.0, season_amplitude=0.0, season_period=52, promo_every=0, promo_length=1, promo_uplift=0.0, noise_std=0.0, zero_prob=0.0)`.
- `jobs/digital_twin_job.py`: `prepare(params: dict | None = None) -> dict` returning keys `nodes, products, periods, demand, disruptions, seed, auto_size_factor`; `products` is a list of `{"product_id","base"}`; `demand` is a dict with keys `trend, season_amplitude, season_period, promo_every, promo_length, promo_uplift, noise_cv, zero_prob`; `disruptions` is a list of `{"kind","target","start","duration","magnitude"}`.
- `src/risk.py`: `RiskFactor(name, category, likelihood, impact_value, exposure=0.0, velocity_days=30.0, detectability_days=7.0, time_to_recover=0.0, time_to_survive=0.0, owner="", mitigations=())`; `assess(risk, *, severity_thresholds=DEFAULT_SEVERITY_THRESHOLDS) -> RiskAssessment` where `RiskAssessment.exposure_gap_days = max(0.0, risk.time_to_recover - risk.time_to_survive)` and `.mitigations: tuple[RankedMitigation, ...]`; `RankedMitigation(name, kind, cost, residual_emv, risk_reduction, net_benefit, roi, recommended=False)`; `MitigationOption(name, kind, cost, likelihood_reduction=0.0, impact_reduction=0.0)`.
- `src/guided.py`: `as_options(summary, options, *, confidence=1.0, residuals=None) -> GuidedOutcome`; `ExecutionOption(label, summary, score=0.0, recommended=False, action="", tradeoffs="")`; `verify_guided(outcome) -> list[str]`; status constant `OPTIONS`.
- `src/deliverable.py`: `Deliverable(title, client, summary, findings=(), kpis=(), data_sources=(), recommendations=(), options=(), citations=(), confidence=None, residual="", prepared="", ...)`; `.write_all(out_dir) -> dict[str, Path]`; `Finding(title, detail, impact="")`, `Kpi(label, value, target="", rationale="")`, `DataSource(name, source, cadence)`.
- `src/export.py`: `write_summary_csv(rows: list[dict], path: Path | str) -> Path`.
- `scm_agent/registry.py`: `Tool(key, title, description, intent_keywords, requires_data, prepare, run, qa, deliver, deck=None, options=None, required_client_params=())`; `Prepared(status, payload=None, messages=[])`; `Produced(report, summary)`.
- `scm_agent/citation_gate.py`: `TOOL_CONCEPTS: dict[str, tuple[str,...]]`; `MIN_CITATIONS=2`, `MAX_HOPS=2`.
- Books-graph anchors verified to EXIST (bare id, `knowledge::` namespace stripped): `risk_forecasting` ("Risk Forecasting and Resilience Planning"), `flexibility_and_agility` ("Flexibility and Agility"), `supply_chain_resilience` ("Supply Chain Resilience"), `chain_resilience_agility`, `supply_chain_agility`, `supply_chain_disruption`.

---

## Task 1 — `src/resilience.py`: pure measurement engine + numeric tests

**Files**
- Create `src/resilience.py`
- Create `tests/test_resilience.py`

**Interfaces**
- Produces:
  - `ResilienceResult` (frozen dataclass, fields listed in code).
  - `time_to_recover(baseline_fill: Sequence[float], disrupted_fill: Sequence[float], disruption_start: int, *, tolerance: float = 0.02, min_hold: int = 1) -> tuple[float, bool, int]` returns `(ttr, recovered, recovery_period)`.
  - `lost_units(baseline_served: Sequence[float], disrupted_served: Sequence[float], start: int, end: int) -> float`.
  - `resilience_index(ttr: float, tts: float) -> float` (bounded 0..1).
  - `assess_resilience(baseline_served, disrupted_served, baseline_demand, disrupted_demand, *, disruption_start: int, tolerance: float = 0.02, min_hold: int = 1, tts_target: float, unit_margin: float) -> ResilienceResult`.
- Consumes: nothing (stdlib only). Deliberately decoupled from the twin so it is unit-testable on hand-built traces.

### Steps

1. Write `tests/test_resilience.py` with a hand-checkable instance and run it (fails: module does not exist).

```python
"""Numeric tests for the pure resilience measurement engine (src/resilience.py).

Hand-built served/demand traces with a known-optimum TTR / lost-units / index so
each assertion is arithmetic, independent of the digital twin's RNG.
"""

import pytest

from src.resilience import (
    assess_resilience,
    lost_units,
    resilience_index,
    time_to_recover,
)

# Baseline serves 10/period against demand 10 -> fill 1.0 everywhere.
# Disruption at t=3: served drops (0,0,5) then fully recovers at t=6.
_BASE_SERVED = [10.0] * 10
_BASE_DEMAND = [10.0] * 10
_DIS_SERVED = [10.0, 10.0, 10.0, 0.0, 0.0, 5.0, 10.0, 10.0, 10.0, 10.0]
_DIS_DEMAND = [10.0] * 10


def test_time_to_recover_measures_first_in_band_period():
    base_fill = [1.0] * 10
    dis_fill = [s / 10.0 for s in _DIS_SERVED]
    ttr, recovered, recovery_period = time_to_recover(
        base_fill, dis_fill, disruption_start=3, tolerance=0.05, min_hold=1
    )
    assert ttr == 3.0            # recovers at t=6, shock started at t=3
    assert recovered is True
    assert recovery_period == 6


def test_time_to_recover_censored_when_never_recovers():
    base_fill = [1.0] * 6
    dis_fill = [1.0, 1.0, 0.0, 0.0, 0.0, 0.0]  # dead after t=2, never returns
    ttr, recovered, recovery_period = time_to_recover(
        base_fill, dis_fill, disruption_start=2, tolerance=0.05, min_hold=1
    )
    assert recovered is False
    assert ttr == 4.0            # horizon length (6) - start (2)
    assert recovery_period == 6


def test_min_hold_rejects_a_single_blip():
    base_fill = [1.0] * 8
    # recovers for one period (t=4) then relapses; only truly holds from t=6
    dis_fill = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 1.0, 1.0]
    ttr, recovered, _ = time_to_recover(
        base_fill, dis_fill, disruption_start=1, tolerance=0.05, min_hold=2
    )
    assert ttr == 5.0 and recovered is True  # first 2-in-a-row in-band starts at t=6


def test_lost_units_sums_shortfall_over_window():
    # over [3, 6): (10-0)+(10-0)+(10-5) = 25
    assert lost_units(_BASE_SERVED, _DIS_SERVED, 3, 6) == 25.0
    # a later-starting window that clips negatives to zero
    assert lost_units(_BASE_SERVED, _DIS_SERVED, 6, 10) == 0.0


def test_resilience_index_is_bounded():
    assert resilience_index(0.0, 4.0) == 1.0          # instant recovery
    assert resilience_index(3.0, 4.0) == pytest.approx(0.25)
    assert resilience_index(4.0, 4.0) == 0.0          # TTR == TTS
    assert resilience_index(10.0, 4.0) == 0.0         # TTR > TTS, floored
    assert resilience_index(0.0, 0.0) == 1.0          # degenerate TTS, no lag
    assert resilience_index(2.0, 0.0) == 0.0


def test_assess_resilience_end_to_end_arithmetic():
    result = assess_resilience(
        _BASE_SERVED, _DIS_SERVED, _BASE_DEMAND, _DIS_DEMAND,
        disruption_start=3, tolerance=0.05, min_hold=1,
        tts_target=4.0, unit_margin=2.0,
    )
    assert result.time_to_recover == 3.0
    assert result.recovered is True
    assert result.lost_units == 25.0
    assert result.financial_impact == 50.0            # 25 units * $2 margin
    assert result.resilience_index == pytest.approx(0.25)
    assert result.exposure_gap_days == 0.0            # max(0, 3 - 4)
    assert result.baseline_fill == pytest.approx(1.0)


def test_assess_resilience_flags_ttr_over_tts_gap():
    result = assess_resilience(
        _BASE_SERVED, _DIS_SERVED, _BASE_DEMAND, _DIS_DEMAND,
        disruption_start=3, tolerance=0.05, min_hold=1,
        tts_target=1.0, unit_margin=1.0,
    )
    assert result.exposure_gap_days == 2.0            # max(0, 3 - 1)
    assert result.resilience_index == 0.0             # TTR (3) >= TTS (1)


def test_validation_rejects_bad_inputs():
    with pytest.raises(ValueError):
        time_to_recover([1.0], [1.0, 1.0], 0)          # length mismatch
    with pytest.raises(ValueError):
        time_to_recover([1.0, 1.0], [1.0, 1.0], 5)     # start out of range
    with pytest.raises(ValueError):
        assess_resilience([1.0], [1.0], [1.0], [1.0], disruption_start=0,
                          tts_target=-1.0, unit_margin=1.0)
    with pytest.raises(ValueError):
        assess_resilience([1.0], [1.0], [1.0], [1.0], disruption_start=0,
                          tts_target=1.0, unit_margin=-1.0)
```

Run: `PYTHONPATH=. py -m pytest tests/test_resilience.py -q` -> fails (no module).

2. Create `src/resilience.py` with the minimal implementation to pass.

```python
"""Quantified network resilience (Kern capability SCOR-AG / agility).

The comparison-and-measurement harness on top of the DONE disruption-injection
engine in ``src/digital_twin.py``. Given the per-period served and demand traces
of a baseline run and a same-seed disrupted run, it MEASURES resilience instead
of hand-typing it:

- time-to-recover (TTR): the first period at/after the shock where the disrupted
  network fill re-enters a tolerance band around the baseline fill and holds;
- lost units: baseline served minus disrupted served, summed over the recovery
  window, times unit margin => financial impact;
- a resilience index: measured TTR scored against a target time-to-survive (TTS).

Pure by construction: stdlib only, frozen dataclass + pure functions, no I/O and
no pandas/numpy - it takes plain float sequences so it is testable on
hand-checkable instances, and the twin's numpy traces are converted to lists at
the job boundary. The measured TTR/TTS feed ``src.risk.assess`` in the job layer,
turning the risk engine's TTR>TTS exposure gap (Simchi-Levi) into a computed
output of a simulated shock.

Grounded in L3: Supply Chain Resilience, Flexibility and Agility, Risk
Forecasting and Resilience Planning (Christopher / Ivanov / Simchi-Levi via the
resilience-and-agility concepts in the books graph).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class ResilienceResult:
    """One quantified stress test: measured recovery plus its financial and index read-out."""

    disruption_start: int
    time_to_recover: float       # measured TTR, periods after the shock starts
    time_to_survive: float       # target TTS the network is meant to ride out, periods
    recovered: bool              # did fill re-enter the band before the horizon ended
    recovery_period: int         # absolute period fill recovered (or the horizon length)
    lost_units: float            # served shortfall over the recovery window
    unit_margin: float
    financial_impact: float      # lost_units * unit_margin
    resilience_index: float      # 0..1: 1.0 == instant recovery, 0.0 == TTR >= TTS
    exposure_gap_days: float     # max(0, TTR - TTS): the Simchi-Levi resilience gap
    baseline_fill: float         # mean network fill rate, baseline run
    disrupted_fill: float        # mean network fill rate, disrupted run


def _fill_trace(served: Sequence[float], demand: Sequence[float]) -> list[float]:
    """Per-period network fill rate; a period with no demand counts as fully served (1.0)."""
    if len(served) != len(demand):
        raise ValueError("served and demand traces must be the same length")
    return [(s / d if d > 0 else 1.0) for s, d in zip(served, demand)]


def time_to_recover(
    baseline_fill: Sequence[float],
    disrupted_fill: Sequence[float],
    disruption_start: int,
    *,
    tolerance: float = 0.02,
    min_hold: int = 1,
) -> tuple[float, bool, int]:
    """First period at/after the shock where disrupted fill re-enters the band and holds.

    The band is ``baseline_fill[t] - tolerance``; the network must sit inside it
    for ``min_hold`` consecutive periods to count as recovered (a single blip does
    not). Returns ``(ttr, recovered, recovery_period)`` where ``ttr`` is measured in
    periods after ``disruption_start``; if it never recovers within the horizon,
    ``recovered`` is False and ``ttr`` is the censored remainder to the horizon end.
    """
    n = len(baseline_fill)
    if len(disrupted_fill) != n:
        raise ValueError("baseline and disrupted fill traces must be the same length")
    if not 0 <= disruption_start < n:
        raise ValueError("disruption_start out of range")
    if min_hold < 1:
        raise ValueError("min_hold must be >= 1")

    within = [disrupted_fill[t] >= baseline_fill[t] - tolerance for t in range(n)]
    for t in range(disruption_start, n):
        if t + min_hold <= n and all(within[t : t + min_hold]):
            return float(t - disruption_start), True, t
    return float(n - disruption_start), False, n


def lost_units(
    baseline_served: Sequence[float],
    disrupted_served: Sequence[float],
    start: int,
    end: int,
) -> float:
    """Sum of per-period served shortfall (baseline - disrupted, floored at 0) over [start, end)."""
    n = len(baseline_served)
    if len(disrupted_served) != n:
        raise ValueError("baseline and disrupted served traces must be the same length")
    lo, hi = max(0, start), min(n, end)
    return float(sum(max(0.0, baseline_served[t] - disrupted_served[t]) for t in range(lo, hi)))


def resilience_index(ttr: float, tts: float) -> float:
    """Bounded 0..1 index: 1.0 at instant recovery, 0.0 once TTR reaches/exceeds TTS."""
    if ttr < 0 or tts < 0:
        raise ValueError("ttr and tts must be >= 0")
    if tts <= 0:
        return 1.0 if ttr <= 0 else 0.0
    return max(0.0, 1.0 - ttr / tts)


def assess_resilience(
    baseline_served: Sequence[float],
    disrupted_served: Sequence[float],
    baseline_demand: Sequence[float],
    disrupted_demand: Sequence[float],
    *,
    disruption_start: int,
    tolerance: float = 0.02,
    min_hold: int = 1,
    tts_target: float,
    unit_margin: float,
) -> ResilienceResult:
    """Measure TTR, lost units, financial impact and the resilience index from two runs."""
    if unit_margin < 0:
        raise ValueError("unit_margin must be >= 0")
    if tts_target < 0:
        raise ValueError("tts_target must be >= 0")

    base_fill = _fill_trace(baseline_served, baseline_demand)
    dis_fill = _fill_trace(disrupted_served, disrupted_demand)
    ttr, recovered, recovery_period = time_to_recover(
        base_fill, dis_fill, disruption_start, tolerance=tolerance, min_hold=min_hold,
    )
    lost = lost_units(baseline_served, disrupted_served, disruption_start, recovery_period)
    n = len(baseline_served)
    return ResilienceResult(
        disruption_start=disruption_start,
        time_to_recover=ttr,
        time_to_survive=float(tts_target),
        recovered=recovered,
        recovery_period=recovery_period,
        lost_units=lost,
        unit_margin=float(unit_margin),
        financial_impact=lost * float(unit_margin),
        resilience_index=resilience_index(ttr, tts_target),
        exposure_gap_days=max(0.0, ttr - float(tts_target)),
        baseline_fill=(sum(base_fill) / n if n else 1.0),
        disrupted_fill=(sum(dis_fill) / n if n else 1.0),
    )
```

Run: `PYTHONPATH=. py -m pytest tests/test_resilience.py -q` -> passes.

3. `ruff check src/resilience.py tests/test_resilience.py` -> clean.

4. Commit: `feat: add pure resilience measurement engine (TTR/lost-units/index)`.

**Deliverable:** a tested, pure `src/resilience.py`.

---

## Task 2 — `jobs/resilience_stress_test_job.py`: the twin baseline-vs-disrupted harness + risk wiring

**Files**
- Create `jobs/resilience_stress_test_job.py`
- Create `tests/test_resilience_stress_test_tool.py` (job-level section in this task; registration/routing section added in Task 3)

**Interfaces**
- Consumes: `jobs.digital_twin_job.prepare` (scenario builder), `src.digital_twin.{DemandProfile, Disruption, NodeSpec, simulate_network}`, `src.resilience.{ResilienceResult, assess_resilience}`, `src.risk.{MitigationOption, RiskFactor, RiskAssessment, assess}`, `src.guided.{ExecutionOption, GuidedOutcome, as_options, verify_guided}`, `src.deliverable.{DataSource, Deliverable, Finding, Kpi}`, `src.export.write_summary_csv`.
- Produces:
  - `prepare(params: dict | None = None) -> dict`
  - `run(payload: dict) -> ResilienceJobReport`
  - `verify(report: ResilienceJobReport) -> list[str]`
  - `write_operational(report, out_dir, client="Client") -> dict[str, Path]`
  - `build_deck(report, *, client="Client", prepared="", citations=(), confidence=0.8) -> Deliverable`
  - frozen `ResilienceJobReport`.

### Steps

1. Add the job-level tests to `tests/test_resilience_stress_test_tool.py` and run (fails: module does not exist).

```python
"""Tests for the resilience stress-test tool (jobs/resilience_stress_test_job.py + registration).

The job runs the digital twin twice under one seed (clean baseline vs injected
disruption) and MEASURES recovery; the tool feeds the measured TTR/TTS into the
risk engine and surfaces ranked mitigations as a protected guided outcome.
"""

from pathlib import Path

import pandas as pd
import pytest

from jobs import resilience_stress_test_job as rst
from scm_agent import intent, llm, tools
from scm_agent.orchestrator import Orchestrator
from src.deliverable import Deliverable
from src.guided import OPTIONS, verify_guided

_FAST = {"periods": 60, "n_products": 2, "n_stores": 2,
         "disruption_duration": 12, "disruption_magnitude": 3.0}


# -- prepare ----------------------------------------------------------------------


def test_prepare_forces_a_disruption_by_default():
    payload = rst.prepare(_FAST)
    assert payload["disruptions"], "a stress test must inject a shock"
    assert payload["disruptions"][0]["kind"] == "supplier_outage"
    assert payload["tts_target"] == float(payload["disruptions"][0]["duration"])
    assert payload["unit_margin"] > 0


def test_prepare_respects_resilience_params():
    payload = rst.prepare({**_FAST, "disruption": "lead_time_spike",
                           "tolerance": 0.1, "tts_target": 5.0,
                           "unit_margin": 4.0, "annual_likelihood": 0.2})
    assert payload["disruptions"][0]["kind"] == "lead_time_spike"
    assert payload["tolerance"] == 0.1
    assert payload["tts_target"] == 5.0
    assert payload["unit_margin"] == 4.0
    assert payload["annual_likelihood"] == 0.2


def test_prepare_rejects_bad_params():
    with pytest.raises(ValueError):
        rst.prepare({**_FAST, "unit_margin": -1.0})
    with pytest.raises(ValueError):
        rst.prepare({**_FAST, "annual_likelihood": 2.0})
    with pytest.raises(ValueError):
        rst.prepare({**_FAST, "disruption": "alien_invasion"})


# -- run + verify -------------------------------------------------------------------


def test_run_measures_recovery_and_passes_qa():
    report = rst.run(rst.prepare(_FAST))
    r = report.result
    assert r.time_to_recover >= 0.0
    assert 0.0 <= r.resilience_index <= 1.0
    assert r.financial_impact >= 0.0
    assert r.lost_units >= 0.0
    assert 0.0 <= r.disrupted_fill <= 1.0
    assert rst.verify(report) == []


def test_disruption_costs_service_vs_baseline():
    # A supplier outage must depress the disrupted fill below the baseline fill.
    report = rst.run(rst.prepare({**_FAST, "disruption": "supplier_outage",
                                  "disruption_duration": 20}))
    assert report.result.disrupted_fill < report.result.baseline_fill
    assert report.result.lost_units > 0.0


def test_measured_ttr_flows_into_the_risk_assessment():
    report = rst.run(rst.prepare(_FAST))
    # the RiskAssessment's exposure gap is computed from the MEASURED TTR/TTS,
    # not a hand-typed value.
    assert report.assessment.exposure_gap_days == pytest.approx(
        max(0.0, report.result.time_to_recover - report.result.time_to_survive)
    )


def test_run_is_deterministic():
    a = rst.run(rst.prepare({**_FAST, "seed": 5}))
    b = rst.run(rst.prepare({**_FAST, "seed": 5}))
    assert a.result.financial_impact == b.result.financial_impact
    assert a.result.time_to_recover == b.result.time_to_recover


def test_outcome_is_protected_options():
    report = rst.run(rst.prepare(_FAST))
    assert report.outcome.status == OPTIONS
    assert verify_guided(report.outcome) == []
    assert sum(1 for o in report.outcome.options if o.recommended) == 1


# -- deliverables ------------------------------------------------------------------


def test_write_operational_emits_trace_and_metrics_csvs(tmp_path):
    report = rst.run(rst.prepare(_FAST))
    paths = rst.write_operational(report, tmp_path)
    for key in ("trace", "metrics"):
        assert key in paths and Path(paths[key]).exists()
    trace = pd.read_csv(paths["trace"])
    assert {"period", "baseline_fill", "disrupted_fill", "shortfall"} <= set(trace.columns)
    assert len(trace) == 60
    metrics = pd.read_csv(paths["metrics"])
    assert {"time_to_recover", "time_to_survive", "financial_impact",
            "resilience_index", "exposure_gap_days"} <= set(metrics.columns)


def test_build_deck_is_ascii_deliverable():
    report = rst.run(rst.prepare(_FAST))
    deck = rst.build_deck(report, client="Acme",
                          citations=("Christopher (2016)",), confidence=0.8)
    assert isinstance(deck, Deliverable)
    text = deck.summary + "".join(f.title + f.detail for f in deck.findings)
    assert text.isascii()
    assert len(deck.options) == len(report.outcome.options)
```

Run: `PYTHONPATH=. py -m pytest tests/test_resilience_stress_test_tool.py -q` -> fails (no job module).

2. Create `jobs/resilience_stress_test_job.py`.

```python
"""Resilience stress-test agent job: two same-seed twin runs -> quantified recovery.

The measurement harness on top of ``src/digital_twin.py``'s DONE disruption
engine. ``prepare()`` reuses ``jobs/digital_twin_job.prepare`` to build the
network + demand + disruption scenario (forcing a shock - a stress test needs
one), then augments it with resilience params. ``run()`` simulates every product
TWICE under the same seed - a clean baseline (no disruption) and a disrupted run
- aggregates the network served/demand traces, measures TTR / lost units /
financial impact / resilience index via ``src.resilience.assess_resilience``, and
feeds the MEASURED TTR/TTS into ``src.risk.assess`` so the risk engine's TTR>TTS
exposure gap (Simchi-Levi) is a computed output, then surfaces ranked mitigations
as a protected ``GuidedOutcome``.

Reads no client CSV (the twin generates the network + demand, exactly like
jobs/digital_twin_job.py, its sanctioned template): a requires_data=False,
params-driven tool. Mirrors jobs/risk_job.py's guided-options + deck style.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from jobs import digital_twin_job
from src.deliverable import DataSource, Deliverable, Finding, Kpi
from src.digital_twin import DemandProfile, Disruption, NodeSpec, simulate_network
from src.export import write_summary_csv
from src.guided import ExecutionOption, GuidedOutcome, as_options, verify_guided
from src.resilience import ResilienceResult, assess_resilience
from src.risk import MitigationOption, RiskAssessment, RiskFactor, assess

_START_DATE = "2025-01-06"
_RISK_CATEGORY = {
    "supplier_outage": "supply",
    "lead_time_spike": "logistics",
    "demand_surge": "demand",
}


@dataclass(frozen=True)
class ResilienceJobReport:
    """Wraps the measured ResilienceResult, the risk assessment it feeds, and the options outcome."""

    result: ResilienceResult
    assessment: RiskAssessment
    outcome: GuidedOutcome
    disruption_kind: str
    disruption_target: str
    periods: int
    n_products: int
    n_stores: int
    baseline_fill_trace: tuple[float, ...]
    disrupted_fill_trace: tuple[float, ...]
    baseline_served_trace: tuple[float, ...]
    disrupted_served_trace: tuple[float, ...]
    summary: str


def _mitigations(spec: object) -> tuple[MitigationOption, ...]:
    """Optional resilience mitigations for the single shock, as a flat list of dicts in params."""
    if not spec:
        return ()
    return tuple(
        MitigationOption(
            name=str(m["name"]),
            kind=str(m.get("kind", "control")),
            cost=float(m.get("cost", 0.0)),
            likelihood_reduction=float(m.get("likelihood_reduction", 0.0)),
            impact_reduction=float(m.get("impact_reduction", 0.0)),
        )
        for m in spec
    )


def prepare(params: dict | None = None) -> dict:
    """Build the twin scenario (forcing a disruption) + the resilience measurement params."""
    params = params or {}
    scenario_params = dict(params)
    scenario_params.setdefault("disruption", "supplier_outage")  # a stress test needs a shock
    payload = digital_twin_job.prepare(scenario_params)
    if not payload["disruptions"]:
        raise ValueError("resilience stress test requires a disruption")

    duration = float(payload["disruptions"][0]["duration"])
    tolerance = float(params.get("tolerance", 0.02))
    min_hold = int(params.get("min_hold", 2))
    tts_target = float(params.get("tts_target", duration))  # survive at least the shock
    unit_margin = float(params.get("unit_margin", 1.0))
    annual_likelihood = float(params.get("annual_likelihood", 0.1))
    if tolerance < 0:
        raise ValueError("tolerance must be >= 0")
    if min_hold < 1:
        raise ValueError("min_hold must be >= 1")
    if tts_target < 0:
        raise ValueError("tts_target must be >= 0")
    if unit_margin < 0:
        raise ValueError("unit_margin must be >= 0")
    if not 0.0 <= annual_likelihood <= 1.0:
        raise ValueError("annual_likelihood must be in [0, 1]")

    payload["tolerance"] = tolerance
    payload["min_hold"] = min_hold
    payload["tts_target"] = tts_target
    payload["unit_margin"] = unit_margin
    payload["annual_likelihood"] = annual_likelihood
    payload["mitigations"] = params.get("mitigations")
    return payload


def _specs(payload: dict) -> list[NodeSpec]:
    return [
        NodeSpec(
            name=n["name"], kind=n["kind"], supplier=n["supplier"],
            lead_time=int(n["lead_time"]), review_period=int(n["review_period"]),
            capacity=(float(n["capacity"]) if n.get("capacity") is not None else None),
        )
        for n in payload["nodes"]
    ]


def run(payload: dict) -> ResilienceJobReport:
    """Simulate baseline vs disrupted under one seed, measure recovery, feed TTR into risk.assess."""
    specs = _specs(payload)
    d = payload["demand"]
    disruptions = tuple(
        Disruption(kind=x["kind"], target=x["target"], start=int(x["start"]),
                   duration=int(x["duration"]), magnitude=float(x["magnitude"]))
        for x in payload["disruptions"]
    )
    if not disruptions:
        raise ValueError("resilience stress test requires a disruption")
    shock = disruptions[0]
    periods = payload["periods"]

    base_served = np.zeros(periods)
    base_demand = np.zeros(periods)
    dis_served = np.zeros(periods)
    dis_demand = np.zeros(periods)
    for idx, product in enumerate(payload["products"]):
        profile = DemandProfile(
            base=product["base"], trend=d["trend"],
            season_amplitude=d["season_amplitude"], season_period=d["season_period"],
            promo_every=d["promo_every"], promo_length=d["promo_length"],
            promo_uplift=d["promo_uplift"],
            noise_std=product["base"] * d["noise_cv"], zero_prob=d["zero_prob"],
        )
        seed = payload["seed"] + idx
        baseline = simulate_network(
            specs, profile, periods=periods, disruptions=(),
            seed=seed, auto_size_factor=payload["auto_size_factor"],
        )
        disrupted = simulate_network(
            specs, profile, periods=periods, disruptions=disruptions,
            seed=seed, auto_size_factor=payload["auto_size_factor"],
        )
        for store in baseline.served:
            base_served += baseline.served[store]
            base_demand += baseline.demand[store]
            dis_served += disrupted.served[store]
            dis_demand += disrupted.demand[store]

    result = assess_resilience(
        base_served.tolist(), dis_served.tolist(),
        base_demand.tolist(), dis_demand.tolist(),
        disruption_start=shock.start, tolerance=payload["tolerance"],
        min_hold=payload["min_hold"], tts_target=payload["tts_target"],
        unit_margin=payload["unit_margin"],
    )

    # Feed the MEASURED TTR/TTS into the risk engine (TTR/TTS as computed outputs).
    risk_factor = RiskFactor(
        name=f"{shock.kind} at {shock.target}",
        category=_RISK_CATEGORY.get(shock.kind, "supply"),
        likelihood=payload["annual_likelihood"],
        impact_value=result.financial_impact,
        time_to_recover=result.time_to_recover,
        time_to_survive=result.time_to_survive,
        mitigations=_mitigations(payload["mitigations"]),
    )
    assessment = assess(risk_factor)

    base_fill = [
        (float(s) / float(dm) if dm > 0 else 1.0)
        for s, dm in zip(base_served, base_demand)
    ]
    dis_fill = [
        (float(s) / float(dm) if dm > 0 else 1.0)
        for s, dm in zip(dis_served, dis_demand)
    ]

    verdict = "gap" if result.exposure_gap_days > 0 else "within survival"
    summary = (
        f"{shock.kind} at {shock.target}: TTR {result.time_to_recover:.0f}p vs "
        f"TTS {result.time_to_survive:.0f}p ({verdict}); {result.lost_units:,.0f} units lost "
        f"= ${result.financial_impact:,.0f}; resilience index {result.resilience_index:.2f}."
    )

    options = [
        ExecutionOption(
            label=m.name,
            summary=(
                f"residual EMV ${m.residual_emv:,.0f} (cuts ${m.risk_reduction:,.0f}); "
                f"net benefit ${m.net_benefit:,.0f}"
            ),
            score=m.net_benefit,
            recommended=m.recommended,
            action=f"apply '{m.name}' ({m.kind}) against {risk_factor.name}",
            tradeoffs=(f"{m.kind}; ${m.cost:,.0f} spend" if m.cost > 0 else "no spend; risk retained"),
        )
        for m in assessment.mitigations
    ]
    outcome = as_options(summary, options, confidence=0.8)

    n_stores = sum(1 for n in payload["nodes"] if n["kind"] == "store")
    return ResilienceJobReport(
        result=result,
        assessment=assessment,
        outcome=outcome,
        disruption_kind=shock.kind,
        disruption_target=shock.target,
        periods=periods,
        n_products=len(payload["products"]),
        n_stores=n_stores,
        baseline_fill_trace=tuple(base_fill),
        disrupted_fill_trace=tuple(dis_fill),
        baseline_served_trace=tuple(float(x) for x in base_served),
        disrupted_served_trace=tuple(float(x) for x in dis_served),
        summary=summary,
    )


def verify(report: ResilienceJobReport) -> list[str]:
    """QA gate: the options outcome honours the never-unprotected contract + finite, in-range metrics."""
    issues = list(verify_guided(report.outcome))
    r = report.result
    if not math.isfinite(r.financial_impact) or r.financial_impact < 0:
        issues.append("financial impact is not a finite, non-negative value")
    if r.lost_units < 0:
        issues.append("lost units is negative")
    if not 0.0 <= r.resilience_index <= 1.0:
        issues.append(f"resilience index out of [0,1]: {r.resilience_index}")
    if r.time_to_recover < 0:
        issues.append("time-to-recover is negative")
    if r.exposure_gap_days < 0:
        issues.append("exposure gap is negative")
    for label, trace in (("baseline", report.baseline_fill_trace),
                         ("disrupted", report.disrupted_fill_trace)):
        if any(not 0.0 <= f <= 1.0 for f in trace):
            issues.append(f"{label} fill trace has a value out of [0,1]")
    return issues


def _dates(periods: pd.Series) -> pd.Series:
    start = pd.Timestamp(_START_DATE)
    return periods.map(lambda t: (start + pd.Timedelta(weeks=int(t))).date().isoformat())


def write_operational(report: ResilienceJobReport, out_dir: str | Path, client: str = "Client") -> dict[str, Path]:
    """The machine-readable deliverable: the per-period baseline-vs-disrupted trace + the metrics row."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)

    trace = pd.DataFrame({
        "period": range(report.periods),
        "baseline_fill": [round(x, 4) for x in report.baseline_fill_trace],
        "disrupted_fill": [round(x, 4) for x in report.disrupted_fill_trace],
        "baseline_served": [round(x, 2) for x in report.baseline_served_trace],
        "disrupted_served": [round(x, 2) for x in report.disrupted_served_trace],
        "shortfall": [
            round(max(0.0, b - s), 2)
            for b, s in zip(report.baseline_served_trace, report.disrupted_served_trace)
        ],
    })
    trace.insert(0, "date", _dates(trace.pop("period")))
    trace_path = d / "resilience_trace.csv"
    trace.to_csv(trace_path, index=False)

    r = report.result
    metrics_path = write_summary_csv(
        [{
            "disruption": f"{report.disruption_kind} @ {report.disruption_target}",
            "time_to_recover": r.time_to_recover,
            "time_to_survive": r.time_to_survive,
            "recovered": r.recovered,
            "lost_units": round(r.lost_units, 2),
            "unit_margin": r.unit_margin,
            "financial_impact": round(r.financial_impact, 2),
            "resilience_index": round(r.resilience_index, 4),
            "exposure_gap_days": round(r.exposure_gap_days, 2),
            "baseline_fill": round(r.baseline_fill, 4),
            "disrupted_fill": round(r.disrupted_fill, 4),
        }],
        d / "resilience_metrics.csv",
    )
    return {"trace": trace_path, "metrics": metrics_path}


def build_deck(
    report: ResilienceJobReport,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple[str, ...] = (),
    confidence: float = 0.8,
) -> Deliverable:
    """Compose the stress-test study: how hard the shock hit, how fast it recovered, what it cost."""
    r = report.result
    verdict = (
        f"cannot ride the shock out: TTR ({r.time_to_recover:.0f}p) outlasts TTS "
        f"({r.time_to_survive:.0f}p) by {r.exposure_gap_days:.0f}p"
        if r.exposure_gap_days > 0 else
        f"rides the shock out: TTR ({r.time_to_recover:.0f}p) within TTS ({r.time_to_survive:.0f}p)"
    )
    summary = (
        f"Resilience stress test of a {report.n_stores}-store network under a "
        f"{report.disruption_kind} at {report.disruption_target}: the network {verdict}. "
        f"{r.lost_units:,.0f} units lost = ${r.financial_impact:,.0f} impact; "
        f"resilience index {r.resilience_index:.2f} (1.0 = instant recovery)."
    )

    findings = [
        Finding(
            "Measured time-to-recover (TTR)",
            f"The disrupted network fill re-entered a tolerance band around baseline "
            f"{r.time_to_recover:.0f} period(s) after the shock "
            f"({'recovered' if r.recovered else 'did NOT recover within the horizon'}).",
            impact="the recovery speed the risk register can now cite as a computed number",
        ),
        Finding(
            "Financial impact of the shock",
            f"{r.lost_units:,.0f} units of lost service over the recovery window "
            f"x ${r.unit_margin:,.2f} margin = ${r.financial_impact:,.0f}.",
            impact="the loss a resilience investment is buying down",
        ),
        Finding(
            f"Resilience verdict (TTR vs TTS): {report.assessment.zone}",
            f"time-to-recover {r.time_to_recover:.0f}p vs time-to-survive {r.time_to_survive:.0f}p; "
            f"exposure gap {r.exposure_gap_days:.0f} day(s) (Simchi-Levi).",
            impact=("close the gap with pre-positioned buffers or a faster recovery playbook"
                    if r.exposure_gap_days > 0 else
                    "the network survives this shock on the recovery-vs-survival test"),
        ),
    ]

    kpis = (
        Kpi("Time-to-recover (TTR)", f"{r.time_to_recover:.0f} periods", target="minimize",
            rationale="Measured: first period disrupted fill re-enters the baseline band and holds"),
        Kpi("Time-to-survive (TTS) target", f"{r.time_to_survive:.0f} periods",
            rationale="Buffer the network is meant to ride the shock out for"),
        Kpi("Lost units", f"{r.lost_units:,.0f}", target="minimize",
            rationale="Baseline served minus disrupted served over the recovery window"),
        Kpi("Financial impact", f"${r.financial_impact:,.0f}", target="minimize",
            rationale="Lost units x unit margin"),
        Kpi("Resilience index", f"{r.resilience_index:.2f}", target="maximize",
            rationale="1.0 at instant recovery, 0.0 once TTR reaches TTS"),
    )

    data_sources = (
        DataSource("Network topology + policies", "Twin parameters (or defaults)", "per scenario"),
        DataSource("Disruption (kind / target / timing / magnitude)", "Twin parameters", "per scenario"),
    )

    recommendations = (
        "Action the recommended mitigation to shrink the TTR>TTS exposure gap first.",
        "Re-run the test with harsher magnitude/duration to find the network's breaking point.",
        "Track the resilience index and financial impact as the KPIs a buffer/dual-source budget buys down.",
    )

    return Deliverable(
        title="Resilience Stress Test - Quantified Network Recovery",
        client=client,
        summary=summary,
        findings=tuple(findings),
        kpis=kpis,
        data_sources=data_sources,
        recommendations=recommendations,
        options=tuple(report.outcome.options),
        citations=tuple(citations),
        confidence=confidence,
        residual="Synthetic scenario: TTR/lost-units are measured against the twin's own "
                 "same-seed baseline, not a specific client's history. Calibrate base demand, "
                 "lead times, unit margin and disruption assumptions before treating the "
                 "financial impact as a forecast. Under a demand-surge shock the two runs face "
                 "different demand, so lost-units is a served-shortfall proxy rather than pure lost sales.",
        prepared=prepared,
    )
```

Run: `PYTHONPATH=. py -m pytest tests/test_resilience_stress_test_tool.py -q` -> the job-level tests pass. (Registration/routing/orchestrator tests will be added in Task 3; if they were pre-written they fail until Task 3 registers the tool — keep them out of this file until Task 3, or expect those specific ones to fail.)

3. `ruff check src jobs tests` (scoped to the new files) -> clean.

4. Commit: `feat: add resilience stress-test job (baseline-vs-disrupted twin harness)`.

**Deliverable:** a tested job that runs the twin twice, measures resilience, and feeds measured TTR/TTS into the risk engine.

---

## Task 3 — Register the tool, add citation anchors, wire the agent-level tests

**Files**
- Modify `scm_agent/tools.py` (import + prepare/run wrappers + factory + `build_default_registry()` registration)
- Modify `scm_agent/citation_gate.py` (`TOOL_CONCEPTS` entry)
- Modify `tests/test_citation_gate.py` (reachability regression test)
- Modify `tests/test_resilience_stress_test_tool.py` (add registration + routing + orchestrator section)

**Interfaces**
- Consumes: `scm_agent.registry.{Tool, Prepared, Produced}`, `jobs.resilience_stress_test_job`, `scm_agent.tools.build_default_registry`.
- Produces: `resilience_stress_test_tool() -> Tool`; a registered `"resilience_stress_test"` key routable by intent.

### Steps

1. Add the agent-level tests to `tests/test_resilience_stress_test_tool.py` and run (fails: tool not registered).

```python
# -- registration + routing (appended to tests/test_resilience_stress_test_tool.py) --


def test_registry_exposes_resilience_stress_test():
    reg = tools.build_default_registry()
    tool = reg.get("resilience_stress_test")
    assert tool.requires_data is False
    assert "resilience stress test" in tool.intent_keywords
    assert tool.options is not None            # surfaces the guided OPTIONS outcome


def test_intent_routes_resilience_briefs_and_leaves_twin_alone():
    reg = tools.build_default_registry()
    provider = llm.RulesFallback()
    res = intent.classify(
        "run a resilience stress test of my supply network: measure time to recover "
        "and the financial impact of a supplier outage",
        reg, provider,
    )
    assert res.job_type == "resilience_stress_test"
    twin = intent.classify(
        "arma un digital twin de mi red de suministro para generar escenarios",
        reg, provider,
    )
    assert twin.job_type == "digital_twin"


def test_orchestrator_runs_resilience_end_to_end(tmp_path):
    orch = Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback())
    res = orch.run(
        "resilience stress test: cuantifica la resiliencia de la red y el tiempo de recuperacion",
        overrides=dict(_FAST), client="Acme", out_dir=tmp_path,
    )
    assert res.status == "ok" and res.tool == "resilience_stress_test"
    assert Path(res.deliverables["metrics"]).exists()
    assert res.guided is not None and res.guided.status == OPTIONS
    assert sum(1 for o in res.guided.options if o.recommended) == 1
```

Run: `PYTHONPATH=. py -m pytest tests/test_resilience_stress_test_tool.py -q` -> the new registration/routing/orchestrator tests fail.

2. In `scm_agent/tools.py`, add `resilience_stress_test_job` to the `from jobs import (...)` block (alphabetical position, after `reconciliation_job`):

```python
    reconciliation_job,
    resilience_stress_test_job,
    returns_job,
```

3. In `scm_agent/tools.py`, add the wrappers + factory (place near `digital_twin_tool`, after the `digital_twin` block):

```python
# ---- resilience_stress_test (quantified network resilience) ------------------

def _resilience_stress_test_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    try:
        payload = resilience_stress_test_job.prepare(request.params)
    except ValueError as exc:
        return Prepared(status="needs_clarification", messages=[str(exc)])
    return Prepared(status="ok", payload=payload)


def _resilience_stress_test_run(payload: object, params: dict) -> Produced:
    report = resilience_stress_test_job.run(payload)
    return Produced(report=report, summary=report.summary)


def resilience_stress_test_tool() -> Tool:
    return Tool(
        key="resilience_stress_test",
        title="Resilience Stress Test - Quantified Network Recovery",
        description="Run the network digital twin twice under the same seed (a clean baseline and "
                    "an injected disruption), measure time-to-recover, lost units and their "
                    "financial impact, and score a resilience index of measured TTR against a "
                    "target time-to-survive - turning TTR/TTS into computed outputs the risk "
                    "engine consumes instead of hand-typed guesses.",
        intent_keywords=(
            "resilience stress test", "resilience stress-test", "network resilience",
            "quantify resilience", "quantified resilience", "resilience index",
            "time to recover", "time-to-recover", "recovery time", "time to survive",
            "prueba de resiliencia", "resiliencia de la red", "cuantificar resiliencia",
            "tiempo de recuperacion", "impacto de disrupcion",
        ),
        requires_data=False,
        prepare=_resilience_stress_test_prepare,
        run=_resilience_stress_test_run,
        qa=lambda report: resilience_stress_test_job.verify(report),
        deliver=lambda report, out_dir, client: resilience_stress_test_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options: replace(
            resilience_stress_test_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options),
        ).write_all(out_dir),
        # The recovery-vs-survival mitigation decision IS a ranked, executable choice set ->
        # surface it as the guided OPTIONS outcome on success (recommended default flagged).
        options=lambda report: report.outcome,
    )
```

4. In `scm_agent/tools.py::build_default_registry`, add the registration (after `reg.register(launch_readiness_tool())` at the end of the block):

```python
    reg.register(resilience_stress_test_tool())
```

5. In `scm_agent/citation_gate.py`, add the anchor entry to `TOOL_CONCEPTS` (after the `launch_readiness` entry). Both ids are verified to exist in `knowledge/scm-books/graph.json`, and both are 2 hops from the nearest book hub (so no whole-book self-validation at `MAX_HOPS=2`):

```python
    # 'Risk Forecasting and Resilience Planning' + 'Flexibility and Agility' - the two
    # resilience/agility concepts closest to a network resilience/TTR measurement that are
    # NOT one hop from a book hub (verified by graph BFS 2026-07: nearest book hub is 2 hops
    # away, so no whole-book self-validation at MAX_HOPS=2). 'Time-to-Recover'/'TTR' is not
    # a graph concept, so the anchor is the resilience-and-agility neighbourhood that IS.
    # 'supply_chain_resilience' is the on-topic 1-hop target both anchors share, but it is a
    # degree-41 hub - if its 2-hop magnets ride in on a real run, add them to EXCLUDED_CONCEPTS.
    "resilience_stress_test": ("risk_forecasting", "flexibility_and_agility"),
```

6. Add a reachability regression test to `tests/test_citation_gate.py` (mirroring `test_forecast_anchors_are_reachable_from_...`), so a future graph regen that drops these ids fails loudly:

```python
def test_resilience_anchors_are_resilience_and_agility_concepts():
    """The resilience tool must anchor on resilience/agility nodes that exist, not TTR (absent)."""
    assert TOOL_CONCEPTS["resilience_stress_test"] == ("risk_forecasting", "flexibility_and_agility")
    assert "supply_chain_resilience" not in TOOL_CONCEPTS["resilience_stress_test"]  # hub, not an anchor
```

(`test_every_registered_tool_has_a_concept_map`, `test_every_anchor_concept_exists_in_the_real_graph`, and `test_no_tool_has_an_empty_anchor_tuple` in the same file now also cover the new entry with no edits.)

7. Run the focused suites:
```
PYTHONPATH=. py -m pytest tests/test_resilience.py tests/test_resilience_stress_test_tool.py tests/test_citation_gate.py -q
```
-> all pass. Then the full suite for regressions:
```
PYTHONPATH=. py -m pytest tests -q
```

8. `ruff check src jobs tests scm_agent` -> clean.

9. Commit: `feat: register resilience_stress_test tool + citation anchors`.

**Deliverable:** `resilience_stress_test` is a fully registered, routable, citation-grounded Kern tool with the guided OPTIONS contract, green across the full suite.

---

## Notes on grounding / SCOR AG closure
- The tool reuses the DONE injection engine (`simulate_network` + `Disruption`) untouched; it adds only the comparison/measurement layer, per the task.
- TTR/TTS become **computed**: `run()` builds a `RiskFactor(time_to_recover=result.time_to_recover, time_to_survive=result.time_to_survive, impact_value=result.financial_impact, ...)` and calls `src.risk.assess`, whose `exposure_gap_days = max(0, TTR - TTS)` is now driven by the simulated shock (asserted by `test_measured_ttr_flows_into_the_risk_assessment`).
- Citation anchor is drawn from resilience/agility concepts that exist in the graph (`risk_forecasting`, `flexibility_and_agility`); literal "Time-to-Recover"/"TTR" is confirmed absent and deliberately not used.

## Open questions / decisions flagged during drafting

- prepare() is params-driven with requires_data=False, mirroring the explicitly-cited digital_twin_job template rather than the recipe's generic 'read your own CSV' rule (the twin GENERATES the network+demand, so there is no client CSV to read). If a client-supplied demand CSV should be accepted instead of/alongside the synthetic scenario, prepare() needs an optional demand_path branch (pandas read of its own file) - not included to keep scope tight.
- Guided OPTIONS mitigations mirror risk_job exactly: with no params['mitigations'] supplied, assess() ranks only the always-present 'Accept / monitor', so the default run surfaces a single recommended option. Richer, ranked choices require passing mitigation economics via params['mitigations'] (flat list of {name,kind,cost,likelihood_reduction,impact_reduction}). If the tool should always synthesize >=2 resilience archetypes (redundancy buffer vs flexibility/dual-source) with heuristic scores, that is an extra design decision (and would weaken the 'numeric, hand-checkable' property).
- lost_units under a demand_surge shock compares baseline_served vs disrupted_served while the two runs face DIFFERENT demand (the surge inflates disrupted demand). Per the task's literal definition this is kept as a served-shortfall proxy (documented in the deck residual). If demand_surge should instead measure unmet demand (disrupted_demand - disrupted_served vs baseline), the lost-units window semantics need a per-kind branch.
- Citation anchors risk_forecasting + flexibility_and_agility are each 2 hops from the nearest book hub (safe) but both are 1 hop from the degree-41 'supply_chain_resilience' hub, whose own 2-hop neighbourhood could pull mild off-topic magnets into a real run. A quick BFS of the anchors' combined 2-hop closure during implementation should confirm whether any magnet needs adding to EXCLUDED_CONCEPTS['resilience_stress_test'] (the plan flags this in the code comment).
- digital_twin_tool already claims the generic keywords 'stress test' and 'prueba de estres'. The new tool uses more-distinctive multi-word phrases to out-score it on resilience briefs; the routing test asserts this, but if intent.classify's LLM/RulesFallback layer (beyond registry.match keyword counting) mis-routes a representative brief, the keyword set may need tuning.
