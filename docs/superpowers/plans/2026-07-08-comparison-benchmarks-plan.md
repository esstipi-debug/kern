# Old-Method vs Linchpin Comparison Benchmarks — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add four real, reproducible "old method vs Linchpin" comparisons (safety stock, EOQ, multi-echelon, forecasting) as new exercises in `case-studies/CASE_STUDIES.md`, backed by real computed numbers — one small new engine function, one new pure comparison module, and one new data-backed backtest script.

**Architecture:** Three of the four comparisons need zero new engine code — `src/eoq.py` and `src/multi_echelon.py` already expose the functions needed to score an arbitrary (non-optimal) choice against the optimal one. Safety stock needs one new analytic-inverse function. Forecasting needs a genuinely new backtest against real M5 competition data, split into a pure/testable comparison function (`src/benchmarks.py`) and a data-loading script (`scripts/benchmark_forecast_m5.py`) that CI cannot run (the data isn't committed) but a human can, locally.

**Tech Stack:** Python 3.11+, pytest, scipy (`norm`), pandas (script only), numpy.

## Global Constraints

- All file paths are relative to `C:\Users\Gamer\Music\scm\.wt-benchmarks\` — a git worktree of `supply-chain-optimization` on branch `feat/benchmark-old-vs-linchpin`, based on `origin/main` @ `433b3c0`.
- **Never push straight to `main`.** Workflow is feature branch → draft PR → CI green (py3.11/3.12/3.13) → `gh pr ready` → squash-merge, per this repo's own `CLAUDE.md`.
- Tests: `pytest tests/ -q` with `PYTHONPATH=.` from the worktree root. Lint (CI-matched scope): `ruff check src tests` — `scripts/` is not in CI's lint scope, but format it the same way anyway (no reason to ship worse code there).
- **Environment note (this worktree has no venv of its own):** wherever a "Run:" step below invokes bare `python` or `ruff` as the plan-execution command (not inside a fenced block destined for `CASE_STUDIES.md`, which stays as plain `python` for end users with their own environment), use the main checkout's interpreter instead: `"C:\Users\Gamer\Music\scm\supply-chain-optimization\.venv\Scripts\python.exe"` (confirmed to have scipy/pandas/pytest installed) and `"C:\Users\Gamer\Music\scm\supply-chain-optimization\.venv\Scripts\ruff.exe"` if bare `ruff` isn't on `PATH`. Keep `PYTHONPATH` pointed at this worktree root (`C:\Users\Gamer\Music\scm\.wt-benchmarks`), not the main checkout, so imports resolve to the code actually being changed.
- Type annotations on all new function signatures (project + user convention).
- `src/` stays pure functions, no file I/O — anything touching `pandas`/file paths goes in `scripts/`, matching this repo's existing `src/` vs `jobs/` vs `scripts/` separation.
- ASCII-only in any new console `print()` output (Windows cp1252 gotcha, per this repo's `CLAUDE.md`).
- No fabricated numbers anywhere: every "Expected:" value in the new `CASE_STUDIES.md` exercises must be the actual output of running the real code, captured during implementation — not asserted in advance. The illustrative values below (Φ(0.8)≈0.7881, EOQ ratio≈1.29x, GSM 520 vs 485) are hand/spec-verified starting expectations, not final truth; confirm each by actually running the code and use the real captured output in the committed exercise text.
- `data/kaggle/` is gitignored everywhere in this repo — never attempt to commit any M5 CSV.
- `scripts/benchmark_forecast_m5.py` is a deliberate exception to this repo's TDD/coverage convention: its data-loading/orchestration logic has no direct unit test because the M5 data it needs is never present in CI (gitignored, Kaggle-sourced, same as every other external dataset here). This is not a coverage gap to flag — the actual comparison math it calls (`compare_forecast_methods()`) IS fully unit tested in `tests/test_benchmarks.py` (Task 2), which is the risk surface that matters; the script itself is thin pandas plumbing on top of it.
- Do not touch agent tool routing/registry (`scm_agent/tools.py`, `scm_agent/registry.py`) — this feature is documentation + scripts, not a new agent-routable capability.

---

### Task 1: `achieved_service_level()` in `src/safety_stock.py`

**Files:**
- Modify: `src/safety_stock.py`
- Test: `tests/test_safety_stock.py`

**Interfaces:**
- Consumes: `scipy.stats.norm` (already imported in `src/safety_stock.py:7`), `safety_stock()` and `service_level_factor()` (already defined in the same file, used for the round-trip test).
- Produces: `achieved_service_level(safety_stock_qty: float, demand_std_per_period: float, risk_periods: float = 1.0) -> float`, consumed by Task 4 (the new Exercise 7 in `CASE_STUDIES.md` calls this directly).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_safety_stock.py` (extend the existing `from src.safety_stock import (...)` block to also import `achieved_service_level`; `norm` is already imported at the top of this file):

```python
def test_achieved_service_level_independent_formula():
    """Cross-check via a direct norm.cdf call, avoiding tautology with the
    function under test — same pattern as test_cycle_service_level_inverse."""
    expected = float(norm.cdf(20 / 25))
    assert achieved_service_level(20, 25, 1) == pytest.approx(expected)


def test_achieved_service_level_naive_20pct_rule():
    """The common 'hold 20% of average demand as safety stock' heuristic,
    mu=100 sigma=25 (the Table 4.1 scenario) -- only ~79% service level
    against a 95% target, despite looking like a reasonable buffer."""
    result = achieved_service_level(20, 25, 1)
    assert result == pytest.approx(0.7881, abs=0.001)


def test_achieved_service_level_round_trips_with_safety_stock():
    """Round-trips with safety_stock()/service_level_factor() the same way
    test_cycle_service_level_inverse round-trips cycle_service_level_from_inventory()."""
    target_sl = 0.95
    ss = safety_stock(25, target_sl, 1).safety_stock
    assert achieved_service_level(ss, 25, 1) == pytest.approx(target_sl, abs=0.001)


def test_achieved_service_level_zero_std():
    assert achieved_service_level(5, 0, 1) == 1.0
    assert achieved_service_level(-5, 0, 1) == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "C:\Users\Gamer\Music\scm\.wt-benchmarks" && PYTHONPATH=. python -m pytest tests/test_safety_stock.py -v`
Expected: FAIL — `ImportError: cannot import name 'achieved_service_level'` (or `NameError`/collection error to the same effect).

- [ ] **Step 3: Implement `achieved_service_level()`**

Add to `src/safety_stock.py`, immediately after `cycle_service_level_from_inventory()` (after line 72):

```python
def achieved_service_level(
    safety_stock_qty: float,
    demand_std_per_period: float,
    risk_periods: float = 1.0,
) -> float:
    """alpha = Phi(Ss / (sigma_d * sqrt(tau))) -- analytic inverse of safety_stock()."""
    if demand_std_per_period == 0:
        return 1.0 if safety_stock_qty >= 0 else 0.0
    return float(norm.cdf(safety_stock_qty / (demand_std_per_period * (risk_periods**0.5))))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "C:\Users\Gamer\Music\scm\.wt-benchmarks" && PYTHONPATH=. python -m pytest tests/test_safety_stock.py -v`
Expected: PASS, all tests including the 4 new ones.

- [ ] **Step 5: Lint**

Run: `cd "C:\Users\Gamer\Music\scm\.wt-benchmarks" && ruff check src/safety_stock.py tests/test_safety_stock.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
cd "C:\Users\Gamer\Music\scm\.wt-benchmarks"
git add src/safety_stock.py tests/test_safety_stock.py
git commit -m "feat: add achieved_service_level, the analytic inverse of safety_stock()"
```

---

### Task 2: `src/benchmarks.py::compare_forecast_methods()`

**Files:**
- Create: `src/benchmarks.py`
- Create: `tests/test_benchmarks.py`

**Interfaces:**
- Consumes: `mae(actual, forecast) -> float` and `wape(actual, forecast) -> float` from `src/forecast_metrics.py` (confirmed signatures: both take array-likes positionally, `actual` first).
- Produces: `ForecastComparison` dataclass (`naive_mae`, `naive_wape`, `smart_mae`, `smart_wape`, `improvement_pct`, all `float`) and `compare_forecast_methods(actuals: list[float], naive_forecast: list[float], smart_forecast: list[float]) -> ForecastComparison`, consumed by Task 3's `scripts/benchmark_forecast_m5.py`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_benchmarks.py`:

```python
"""Tests for old-method vs Linchpin comparison helpers."""

import pytest

from src.benchmarks import compare_forecast_methods


def test_compare_forecast_methods_naive_worse_than_smart():
    """Synthetic case: a flat naive forecast is clearly worse than a
    near-perfect smart forecast."""
    actuals = [10, 12, 8, 15, 9, 11]
    naive_forecast = [10, 10, 10, 10, 10, 10]
    smart_forecast = [10, 12, 8, 15, 9, 11]
    result = compare_forecast_methods(actuals, naive_forecast, smart_forecast)
    assert result.smart_mae < result.naive_mae
    assert result.smart_wape < result.naive_wape
    assert result.improvement_pct == pytest.approx(100.0, abs=0.01)


def test_compare_forecast_methods_identical_forecasts_zero_improvement():
    actuals = [10, 12, 8, 15, 9, 11]
    forecast = [9, 11, 9, 14, 10, 10]
    result = compare_forecast_methods(actuals, forecast, forecast)
    assert result.improvement_pct == pytest.approx(0.0, abs=0.01)
    assert result.naive_mae == result.smart_mae


def test_compare_forecast_methods_hand_computed_mae():
    """Hand-computed MAE cross-check, independent of mae()/wape() themselves."""
    actuals = [10, 20, 30]
    naive_forecast = [12, 18, 33]  # |errors| = 2, 2, 3 -> MAE = 7/3
    smart_forecast = [10, 20, 30]  # perfect -> MAE = 0
    result = compare_forecast_methods(actuals, naive_forecast, smart_forecast)
    assert result.naive_mae == pytest.approx(7 / 3, abs=0.01)
    assert result.smart_mae == pytest.approx(0.0, abs=0.01)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd "C:\Users\Gamer\Music\scm\.wt-benchmarks" && PYTHONPATH=. python -m pytest tests/test_benchmarks.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.benchmarks'`.

- [ ] **Step 3: Implement `src/benchmarks.py`**

```python
"""Old-method vs Linchpin comparison benchmarks -- pure functions, no I/O.

Data loading and scripting for real-dataset backtests lives in scripts/, not
here (see scripts/benchmark_forecast_m5.py) -- this module only computes
comparison metrics from already-loaded arrays.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.forecast_metrics import mae, wape


@dataclass(frozen=True)
class ForecastComparison:
    """Naive vs smart forecast accuracy, scored against the same actuals."""

    naive_mae: float
    naive_wape: float
    smart_mae: float
    smart_wape: float
    improvement_pct: float


def compare_forecast_methods(
    actuals: list[float],
    naive_forecast: list[float],
    smart_forecast: list[float],
) -> ForecastComparison:
    """improvement_pct is the WAPE reduction of smart_forecast over naive_forecast."""
    naive_mae_v = mae(actuals, naive_forecast)
    naive_wape_v = wape(actuals, naive_forecast)
    smart_mae_v = mae(actuals, smart_forecast)
    smart_wape_v = wape(actuals, smart_forecast)
    improvement = 0.0
    if naive_wape_v > 0:
        improvement = (naive_wape_v - smart_wape_v) / naive_wape_v * 100
    return ForecastComparison(
        naive_mae=naive_mae_v,
        naive_wape=naive_wape_v,
        smart_mae=smart_mae_v,
        smart_wape=smart_wape_v,
        improvement_pct=improvement,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd "C:\Users\Gamer\Music\scm\.wt-benchmarks" && PYTHONPATH=. python -m pytest tests/test_benchmarks.py -v`
Expected: PASS, all 3 tests.

- [ ] **Step 5: Lint**

Run: `cd "C:\Users\Gamer\Music\scm\.wt-benchmarks" && ruff check src/benchmarks.py tests/test_benchmarks.py`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
cd "C:\Users\Gamer\Music\scm\.wt-benchmarks"
git add src/benchmarks.py tests/test_benchmarks.py
git commit -m "feat: add compare_forecast_methods, a pure naive-vs-smart accuracy comparator"
```

---

### Task 3: `scripts/benchmark_forecast_m5.py` — real M5 backtest

**Files:**
- Create: `scripts/benchmark_forecast_m5.py`
- Local-only (NOT committed, gitignored): `data/kaggle/m5/m5/datasets/sales_train_evaluation.csv`, `data/kaggle/m5/m5/datasets/calendar.csv`

**Interfaces:**
- Consumes: `compare_forecast_methods()` from `src/benchmarks.py` (Task 2), `moving_average()` and `forecast_demand()` from `src/forecasting.py` (both already exist, confirmed signatures below).
- Produces: console output (WAPE improvement %, MAE for both methods) that Task 4 copies verbatim into the new Exercise 10 in `CASE_STUDIES.md` as the "Expected:" value.

**Confirmed real M5 file structure** (verified directly against
`C:\Users\Gamer\Music\scm\supply-chain-optimization\data\kaggle\m5\m5\datasets\sales_train_evaluation.csv`,
the sibling main checkout, in this session): 1946 columns —
`item_id, dept_id, cat_id, store_id, state_id` followed by `d_1` through
`d_1941` (no `id` column in this file). `d_1`-`d_1913` is training history;
`d_1914`-`d_1941` is the real 28-day M5 evaluation holdout with genuine
actuals (not synthetic, not masked). `calendar.csv` maps `d_` codes to real
dates but is not strictly needed for this script's logic (day-index math is
enough) — copy it anyway per the spec, in case a later iteration wants
calendar features.

**Confirmed function signatures** (verified directly against
`src/forecasting.py` in this worktree):
- `moving_average(history: object, window: int = 3) -> ForecastResult` — `ForecastResult.forecast` is a single `float`, the next-period point forecast. `moving_average(history, window=1).forecast` is mathematically identical to naive-persistence (repeats the last observed value) — this is the "old way" baseline, no new function needed.
- `forecast_demand(history: object, method: str = "auto", **kwargs) -> ForecastResult` — `method="auto"` is Linchpin's real routing logic (Croston's method when `is_intermittent()`, else SES or a modern AutoETS/TSB method if the optional `statsforecast` extra is installed) — this is "Linchpin's way."
- Both forecasts are single next-period point estimates; this script projects each flatly across the 28-day holdout (`[forecast] * 28`) to score against the real daily actuals — a deliberate, documented simplification (repeat-forward is the standard way to extend a one-step forecast over a multi-day horizon for a persistence-style baseline; scoring both methods the same way keeps the comparison apples-to-apples even though neither produces a genuine 28-day-ahead curve).

- [ ] **Step 1: Copy the M5 data locally (not committed)**

```bash
mkdir -p "C:\Users\Gamer\Music\scm\.wt-benchmarks\data\kaggle\m5\m5\datasets"
cp "C:\Users\Gamer\Music\scm\supply-chain-optimization\data\kaggle\m5\m5\datasets\sales_train_evaluation.csv" "C:\Users\Gamer\Music\scm\.wt-benchmarks\data\kaggle\m5\m5\datasets\sales_train_evaluation.csv"
cp "C:\Users\Gamer\Music\scm\supply-chain-optimization\data\kaggle\m5\m5\datasets\calendar.csv" "C:\Users\Gamer\Music\scm\.wt-benchmarks\data\kaggle\m5\m5\datasets\calendar.csv"
```

- [ ] **Step 2: Verify `data/` is actually gitignored in this worktree before proceeding**

Run: `cd "C:\Users\Gamer\Music\scm\.wt-benchmarks" && git check-ignore -v data/kaggle/m5/m5/datasets/sales_train_evaluation.csv`
Expected: prints the `.gitignore` rule matching the path (confirms this 500MB+ file will never be accidentally staged). If this prints nothing, STOP — do not proceed until `data/` is confirmed ignored (check `.gitignore` for a `data/` or `data/kaggle/` entry before continuing).

- [ ] **Step 3: Write `scripts/benchmark_forecast_m5.py`**

```python
"""Backtest: naive persistence vs Linchpin's classification-routed forecast,
scored against REAL M5 competition held-out actuals (d_1914-d_1941).

Requires local M5 data (not committed -- see case-studies/CASE_STUDIES.md
Exercise 10 for how to obtain it):
    data/kaggle/m5/m5/datasets/sales_train_evaluation.csv
    data/kaggle/m5/m5/datasets/calendar.csv

Usage:
    python scripts/benchmark_forecast_m5.py [--sample-size 100] [--seed 42]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.benchmarks import compare_forecast_methods
from src.forecasting import forecast_demand, moving_average

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "kaggle" / "m5" / "m5" / "datasets"
TRAIN_END_DAY = 1913
TEST_DAYS = [f"d_{d}" for d in range(1914, 1942)]


def load_sample(sample_size: int, seed: int) -> pd.DataFrame:
    path = DATA_DIR / "sales_train_evaluation.csv"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Copy sales_train_evaluation.csv and calendar.csv "
            "from the main checkout's data/kaggle/m5/m5/datasets/ into this worktree "
            "before running this script (see case-studies/CASE_STUDIES.md Exercise 10)."
        )
    df = pd.read_csv(path)
    return df.sample(n=min(sample_size, len(df)), random_state=seed)


def run_benchmark(sample_size: int = 100, seed: int = 42) -> None:
    df = load_sample(sample_size, seed)
    day_cols = [f"d_{d}" for d in range(1, TRAIN_END_DAY + 1)]

    naive_maes: list[float] = []
    naive_wapes: list[float] = []
    smart_maes: list[float] = []
    smart_wapes: list[float] = []
    skipped = 0

    for _, row in df.iterrows():
        history = row[day_cols].astype(float).to_numpy()
        actuals = row[TEST_DAYS].astype(float).to_numpy()
        if history.sum() == 0 or actuals.sum() == 0:
            # actuals.sum() == 0 matters here, not just history.sum(): wape()'s
            # denominator is sum(|actual|), so an all-zero 28-day evaluation
            # window (plausible for a slow-moving real SKU) makes wape() return
            # inf, which then makes compare_forecast_methods()'s improvement_pct
            # come out as nan and silently corrupt the aggregate average below.
            skipped += 1
            continue

        naive_daily_rate = moving_average(history, window=1).forecast
        naive_forecast = [naive_daily_rate] * len(actuals)

        smart_result = forecast_demand(history, method="auto")
        smart_forecast = [smart_result.forecast] * len(actuals)

        comparison = compare_forecast_methods(list(actuals), naive_forecast, smart_forecast)
        naive_maes.append(comparison.naive_mae)
        naive_wapes.append(comparison.naive_wape)
        smart_maes.append(comparison.smart_mae)
        smart_wapes.append(comparison.smart_wape)

    n = len(naive_maes)
    if n == 0:
        print("No scorable SKUs in this sample (all-zero history). Try a different --seed.")
        return

    avg_naive_mae = sum(naive_maes) / n
    avg_naive_wape = sum(naive_wapes) / n
    avg_smart_mae = sum(smart_maes) / n
    avg_smart_wape = sum(smart_wapes) / n
    improvement = 0.0
    if avg_naive_wape > 0:
        improvement = (avg_naive_wape - avg_smart_wape) / avg_naive_wape * 100

    print(f"SKUs scored: {n} (skipped {skipped} all-zero-history SKUs)")
    print(f"Naive (last-value persistence):  MAE={avg_naive_mae:.3f}  WAPE={avg_naive_wape:.3f}")
    print(f"Linchpin (classification+auto):  MAE={avg_smart_mae:.3f}  WAPE={avg_smart_wape:.3f}")
    print(f"WAPE improvement: {improvement:.1f} percent")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-size", type=int, default=100)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    run_benchmark(args.sample_size, args.seed)
```

- [ ] **Step 4: Run it and capture the real output**

Run: `cd "C:\Users\Gamer\Music\scm\.wt-benchmarks" && PYTHONPATH=. python scripts/benchmark_forecast_m5.py --sample-size 100 --seed 42`
Expected: prints `SKUs scored: <n>`, both methods' MAE/WAPE, and a `WAPE improvement: <X>.<Y> percent` line. **Record the exact printed numbers verbatim** — they become the real "Expected:" value for Exercise 10 in Task 4. If `improvement` comes out negative or implausible, do not silently discard the result or reshape the script to force a nicer number — investigate why (e.g. sample too small, `statsforecast` extra installed/not installed changing `method="auto"`'s behavior) and report the real finding; the whole point of this feature is an honest number, not a flattering one.

- [ ] **Step 5: Lint**

Run: `cd "C:\Users\Gamer\Music\scm\.wt-benchmarks" && ruff check scripts/benchmark_forecast_m5.py`
Expected: no errors (not CI-enforced for `scripts/`, but keep it clean anyway per Global Constraints).

- [ ] **Step 6: Commit the script only (never the data)**

```bash
cd "C:\Users\Gamer\Music\scm\.wt-benchmarks"
git status --short  # confirm no data/ files are staged before adding
git add scripts/benchmark_forecast_m5.py
git commit -m "feat: add real M5 backtest script (naive persistence vs Linchpin auto-routing)"
```

---

### Task 4: Append Exercises 7-10 to `case-studies/CASE_STUDIES.md`

**Files:**
- Modify: `case-studies/CASE_STUDIES.md`

**Interfaces:**
- Consumes: `achieved_service_level()` (Task 1), the real WAPE-improvement number captured in Task 3 Step 4, and the existing (unmodified) `src/eoq.py` / `src/multi_echelon.py` functions.
- Produces: the four new exercises other humans (and the later portfolio-site follow-up task) cite directly.

- [ ] **Step 1: Compute and verify the EOQ comparison by actually running it**

Run:
```bash
cd "C:\Users\Gamer\Music\scm\.wt-benchmarks" && PYTHONPATH=. python -c "
from src.eoq import compute_eoq, total_cost, cost_ratio_vs_optimal
opt = compute_eoq(1000, 1.75, 50)
naive = total_cost(500, 1000, 1.75, 50)
ratio = cost_ratio_vs_optimal(500, opt.order_quantity)
print(f'Q*={opt.order_quantity:.0f}, C*={opt.optimal_total_cost:.0f}')
print(f'Naive Q=500 cost={naive:.0f}, ratio={ratio:.3f}')
"
```
Expected (hand-verified in the spec): `Q*≈239, C*≈418`; naive cost≈537, ratio≈1.29 (i.e. the round-lot choice costs ~29% more). **Record the exact printed numbers** for Exercise 8 below — use the real output, not the hand-verified estimate, if they differ even slightly.

- [ ] **Step 2: Compute and verify the multi-echelon comparison by actually running it**

Run:
```bash
cd "C:\Users\Gamer\Music\scm\.wt-benchmarks" && PYTHONPATH=. python -c "
from src.multi_echelon import evaluate_serial_allocation, optimize_serial_gsm
naive = evaluate_serial_allocation((0,0,10), [4,3,2], 100, 25, [1,2,4], 0.95, 1.0, case_id=4)
best = optimize_serial_gsm([4,3,2], 100, 25, [1,2,4], 0.95, 1.0)
print(f'Naive (all-downstream) risk_periods={naive.risk_periods}, cost={round(naive.total_holding_cost)}')
print(f'GSM-optimal risk_periods={best.risk_periods}, cost={round(best.total_holding_cost)}')
print(f'Reduction: {(1 - best.total_holding_cost/naive.total_holding_cost)*100:.1f} percent')
```
Expected (already pinned by the existing `test_gsm_case4_all_downstream_cost` and `test_gsm_optimal_allocation_section_10_4` tests): naive≈520, optimal≈485, reduction≈7%. **Record the exact printed numbers.**

- [ ] **Step 3: Compute and verify the safety-stock comparison by actually running it**

Run:
```bash
cd "C:\Users\Gamer\Music\scm\.wt-benchmarks" && PYTHONPATH=. python -c "
from src.safety_stock import achieved_service_level, safety_stock
naive_ss = 0.20 * 100
achieved = achieved_service_level(naive_ss, 25, 1)
correct_ss = safety_stock(25, 0.95, 1).safety_stock
print(f'Naive 20-percent-of-demand rule: Ss={naive_ss:.0f}, achieves {achieved*100:.1f} percent service level (target 95 percent)')
print(f'Correct formula: Ss={correct_ss:.0f}')
"
```
Expected: naive Ss=20, achieves≈78.8% vs 95% target; correct Ss≈41. **Record the exact printed numbers.**

- [ ] **Step 4: Append the four exercises to `case-studies/CASE_STUDIES.md`**

Insert immediately before the `## Further reading in the book` heading (currently the last section of the file), using the real numbers captured in Steps 1-3 and Task 3 Step 4 (the placeholders `<...>` below MUST be replaced with the actual printed output, not left as-is):

```markdown
## Exercise 7 — Safety stock: naive % rule vs statistical formula (§4.2-4.3)

**Given:** mu_d = 100, sigma_d = 25 (the Table 4.1 scenario), target cycle service level = 95%.

```bash
python -c "
from src.safety_stock import achieved_service_level, safety_stock
naive_ss = 0.20 * 100
achieved = achieved_service_level(naive_ss, 25, 1)
correct_ss = safety_stock(25, 0.95, 1).safety_stock
print(f'Naive 20-percent-of-demand rule: Ss={naive_ss:.0f}, achieves {achieved*100:.1f} percent service level (target 95 percent)')
print(f'Correct formula: Ss={correct_ss:.0f}')
"
```

**Expected:** <exact output from Step 3 above> -- the common "hold 20% of average demand" rule of thumb looks like a reasonable buffer but only achieves <X>% service level against a 95% target.

---

## Exercise 8 — EOQ: round-lot vs economic order quantity (Section 2.2.4, 2.4)

**Given:** D = 1000 units/year, k = 50 EUR/order, h = 1.75 EUR/unit/year (the Exercise 1 scenario). Naive: order in a round lot of 500 units.

```bash
python -c "
from src.eoq import compute_eoq, total_cost, cost_ratio_vs_optimal
opt = compute_eoq(1000, 1.75, 50)
naive = total_cost(500, 1000, 1.75, 50)
ratio = cost_ratio_vs_optimal(500, opt.order_quantity)
print(f'Q*={opt.order_quantity:.0f}, C*={opt.optimal_total_cost:.0f}')
print(f'Naive Q=500 cost={naive:.0f}, ratio={ratio:.3f}')
"
```

**Expected:** <exact output from Step 1 above> -- ordering in a round lot instead of the EOQ-optimal quantity costs ~<X>% more per year for identical demand and cost parameters.

---

## Exercise 9 — Multi-echelon: no risk-pooling vs GSM-optimized (Section 10.4)

**Given:** the Exercise 6 scenario (lead times [4,3,2], mu=100, sigma=25, holding costs [1,2,4], review periods [1,2,4], 95% service level). Naive: all safety stock held at the customer-facing node (no risk-pooling across the chain) -- already pinned by this repo's own `test_gsm_case4_all_downstream_cost`.

```bash
python -c "
from src.multi_echelon import evaluate_serial_allocation, optimize_serial_gsm
naive = evaluate_serial_allocation((0,0,10), [4,3,2], 100, 25, [1,2,4], 0.95, 1.0, case_id=4)
best = optimize_serial_gsm([4,3,2], 100, 25, [1,2,4], 0.95, 1.0)
print(f'Naive (all-downstream) risk_periods={naive.risk_periods}, cost={round(naive.total_holding_cost)}')
print(f'GSM-optimal risk_periods={best.risk_periods}, cost={round(best.total_holding_cost)}')
print(f'Reduction: {(1 - best.total_holding_cost/naive.total_holding_cost)*100:.1f} percent')
"
```

**Expected:** <exact output from Step 2 above> -- pooling risk across the chain instead of stacking it all at the customer-facing node cuts holding cost by ~<X>%.

---

## Exercise 10 — Forecasting: naive persistence vs classification-routed method, backtested on real M5 data (Ch. 9; Makridakis, Spiliotis & Assimakopoulos 2022)

Requires local M5 competition data (not committed to this repo -- large,
Kaggle-sourced, same convention as every other external dataset here). Copy
`sales_train_evaluation.csv` and `calendar.csv` into
`data/kaggle/m5/m5/datasets/` (from a Kaggle download, or from another local
checkout that already has them) before running:

```bash
python scripts/benchmark_forecast_m5.py --sample-size 100 --seed 42
```

**Expected:** <exact output from Task 3 Step 4 above> -- backtested against the real 28-day M5 evaluation holdout (d_1914-d_1941), not synthetic data.

---
```

- [ ] **Step 5: Verify every new exercise is copy-paste runnable exactly as written**

Run each of the four `python -c` / `python scripts/...` commands in
Exercises 7-10 exactly as they now appear in the committed markdown (copy
them out of the file, don't retype from memory), and confirm the printed
output matches the "Expected:" line word-for-word. This catches any
transcription slip between Steps 1-4 above and what actually landed in the
file.

- [ ] **Step 6: Commit**

```bash
cd "C:\Users\Gamer\Music\scm\.wt-benchmarks"
git add case-studies/CASE_STUDIES.md
git commit -m "docs: add 4 old-method-vs-Linchpin comparison exercises with real numbers"
```

---

### Task 5: Full verification pass

**Files:** none (verification only; modify only if a genuine regression is found).

**Interfaces:** consumes everything from Tasks 1-4.

- [ ] **Step 1: Run the full test suite**

Run: `cd "C:\Users\Gamer\Music\scm\.wt-benchmarks" && PYTHONPATH=. python -m pytest tests/ -q`
Expected: all tests pass (the pre-existing suite plus the new tests from Tasks 1-2), zero failures, zero errors.

- [ ] **Step 2: Run the CI-matched lint scope**

Run: `cd "C:\Users\Gamer\Music\scm\.wt-benchmarks" && ruff check src tests`
Expected: no errors.

- [ ] **Step 3: Confirm no data files were ever staged**

Run: `cd "C:\Users\Gamer\Music\scm\.wt-benchmarks" && git log --stat feat/benchmark-old-vs-linchpin ^origin/main | grep -i "data/kaggle" || echo "clean: no data/kaggle files in any commit on this branch"`
Expected: `clean: no data/kaggle files in any commit on this branch`. If this ever prints a match instead, STOP -- a large binary/CSV was accidentally committed; do not push, fix it first (e.g. `git rebase` to drop it from history) before continuing to Task 6.

- [ ] **Step 4: Final sanity check**

Run: `cd "C:\Users\Gamer\Music\scm\.wt-benchmarks" && git log --oneline origin/main..HEAD && git status`
Expected: 4 new commits (Tasks 1, 2, 3, 4) on top of `origin/main`, working tree clean.

- [ ] **Step 5: No commit needed for this task** (verification only). If Steps 1-2 found a real regression, fix it, re-run, and commit the fix with an explicit `fix:` message before proceeding to Task 6.

---

### Task 6: Push and open a draft PR

**Files:** none.

**This task pushes to a remote and opens a public PR — confirm with the user before executing it, even though the plan itself is approved.** Everything through Task 5 is local and reversible; this step is not.

- [ ] **Step 1: Re-check for concurrent-session drift before pushing**

Run: `cd "C:\Users\Gamer\Music\scm\.wt-benchmarks" && git fetch origin --quiet && git log --oneline HEAD..origin/main`
If this prints any commits, `origin/main` has moved since this worktree was branched — rebase or merge before opening the PR (`git merge origin/main`, resolve any conflicts, re-run Task 5's full suite) rather than opening a PR against a stale base. This repo explicitly documents running genuinely concurrent sessions; re-checking immediately before finalizing is its own stated convention, not optional caution.

- [ ] **Step 2: Push**

```bash
cd "C:\Users\Gamer\Music\scm\.wt-benchmarks"
git push -u origin feat/benchmark-old-vs-linchpin
```

- [ ] **Step 3: Open a draft PR**

```bash
cd "C:\Users\Gamer\Music\scm\.wt-benchmarks"
gh pr create --draft --title "feat: old-method vs Linchpin comparison benchmarks" --body "$(cat <<'EOF'
## Summary
- Adds achieved_service_level() (analytic inverse of safety_stock()) to src/safety_stock.py
- Adds src/benchmarks.py::compare_forecast_methods(), a pure naive-vs-smart forecast accuracy comparator
- Adds scripts/benchmark_forecast_m5.py, a real M5-competition backtest (naive persistence vs Linchpin's classification-routed forecast_demand) -- requires local M5 data, not run in CI
- Appends 4 new reproducible exercises (7-10) to case-studies/CASE_STUDIES.md: safety stock, EOQ, multi-echelon, and forecasting, each contrasting a named naive/manual baseline against the real Linchpin engine output

## Test plan
- [x] pytest tests/ -q -- full suite green locally
- [x] ruff check src tests -- clean
- [x] Every new CASE_STUDIES.md exercise re-run copy-paste from the committed file and confirmed to match its "Expected:" line
- [ ] CI green on py3.11/3.12/3.13 (Exercise 10's script is excluded from CI -- the M5 data is gitignored, same as every other Kaggle-sourced dataset in this repo)
EOF
)"
```

- [ ] **Step 4: Wait for CI, then report the PR URL back to the user.** Do not merge, do not run `gh pr ready`, without a separate explicit go-ahead -- this is a shared repo with other concurrent sessions and its own human maintainer workflow.

---

## Self-Review

**Spec coverage:** §3.1 (safety stock) -> Task 1. §3.2 (EOQ, no new code) -> Task 4 Step 1/Exercise 8. §3.3 (multi-echelon, no new code, cites the existing `test_gsm_case4_all_downstream_cost`) -> Task 4 Step 2/Exercise 9. §3.4 (forecasting, new module + script) -> Tasks 2-3. §4 (data) -> Task 3 Steps 1-2. §5 (testing plan) -> Tasks 1-2's TDD steps + Task 5. §6 (CASE_STUDIES.md deliverable) -> Task 4. §7 (workflow: worktree already created, draft PR, CI, squash-merge) -> Task 6. §8 (out of scope: portfolio-site update and i18n are explicitly NOT in this plan) -> confirmed absent from every task above.

**Placeholder scan:** no "TBD"/"TODO" in any task step. The `<exact output from Step N above>` markers in Task 4's markdown are intentional -- they're filled in with real captured output during Task 4 itself, not left as placeholders in the final committed file (Step 5 explicitly re-verifies this).

**Type/interface consistency:** `compare_forecast_methods()`'s parameter names and return type match between its definition (Task 2 Step 3) and its only caller (Task 3 Step 3's script). `achieved_service_level()`'s signature matches between its definition (Task 1 Step 3) and its uses in Task 4 Step 3 and the new Exercise 7. All `src/eoq.py`/`src/multi_echelon.py` function calls in Task 4 were verified against the actual current file contents in this worktree, not assumed from the design spec's paraphrase.
