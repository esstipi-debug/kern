# Design Spec — Old-Method vs Linchpin Comparison Benchmarks

> Date: 2026-07-08
> Status: approved for implementation
> Author: collaborative brainstorming (user + agent)
> Branch: `feat/benchmark-old-vs-linchpin` (worktree `.wt-benchmarks`, based on `origin/main` @ `433b3c0`)
> Sub-project: Phase 1 of 2 in the portfolio-site "make it credible" expansion (see
> `portfolio-site/docs/superpowers/specs/2026-07-08-portfolio-site-design.md` for
> context). Phase 2 (translate the portfolio into es/pt/zh/ja) is a separate,
> later spec in the `portfolio-site` repo and does not start until this ships.

## 1. Problem and goal

The portfolio site (built this session) shows four Linchpin case studies as
worked examples — real numbers, but only "here's what Linchpin computes," not
"here's how much better that is than how this is normally done." The user
asked for hard, computed numbers contrasting Linchpin's method against the
common naive/manual approach for each of the four capabilities already
showcased (forecasting, safety stock, EOQ, multi-echelon), to raise the
portfolio's credibility.

This spec covers **only the Linchpin-engine side**: four new, reproducible,
citable comparisons — old method vs Linchpin — added to
`case-studies/CASE_STUDIES.md` the same way the existing six exercises work.
Feeding the resulting numbers into the portfolio site's English case studies
is a separate follow-up task after this merges, not part of this spec.

## 2. Scope v1

Four comparisons, each anchored to a real Linchpin engine function against a
clearly-named naive baseline:

1. Safety stock — flat "20% of average demand" heuristic vs the statistical
   z-factor formula.
2. EOQ — an arbitrary round-lot order size vs the EOQ-optimal quantity.
3. Multi-echelon — no risk-pooling (all safety stock held at the
   customer-facing node) vs GSM-optimized placement.
4. Forecasting — naive persistence vs Linchpin's classification-routed
   method, backtested on **real** M5 competition data with genuine held-out
   actuals (a toy/textbook number isn't credible for an accuracy claim the
   way it is for a cost-formula worked example).

**Out of scope:** any change to agent tool routing/registry (this is
benchmarking/reporting, not a new agent-routable capability), the portfolio
site itself, i18n.

## 3. Grounding — what already exists vs what's new

Investigated directly against this worktree's checkout (`src/safety_stock.py`,
`src/eoq.py`, `src/multi_echelon.py`, `src/forecasting.py`,
`src/forecast_metrics.py`, and their tests) before writing this spec, so the
scope below is accurate to real code, not assumed:

| Comparison | New code needed? |
|---|---|
| Safety stock | **One new function**, `achieved_service_level()` in `src/safety_stock.py` (see §3.1) |
| EOQ | **None** — `src/eoq.py` already exposes `total_cost(order_quantity, ...)` and `cost_ratio_vs_optimal(order_quantity, optimal_quantity)` for arbitrary Q |
| Multi-echelon | **None** — `src/multi_echelon.py` already exposes `evaluate_serial_allocation(risk_periods, ...)` for an arbitrary (non-optimized) allocation, alongside `optimize_serial_gsm(...)` |
| Forecasting | **New module** `src/benchmarks.py` (one pure, testable function) + **new script** `scripts/benchmark_forecast_m5.py` (data loading/backtest orchestration — see §3.4) |

### 3.1 Safety stock — new `achieved_service_level()`

`src/safety_stock.py` already has `safety_stock()` (target service level →
safety stock quantity) and `cycle_service_level_from_inventory()` (inventory
position → achieved service level), but nothing that takes a **safety-stock
quantity directly** (not total inventory position) and returns the service
level it achieves. Add, matching the file's existing one-liner +
docstring-with-equation-reference style:

```python
def achieved_service_level(
    safety_stock_qty: float,
    demand_std_per_period: float,
    risk_periods: float = 1.0,
) -> float:
    """alpha = Phi(Ss / (sigma_d * sqrt(tau))) — analytic inverse of safety_stock()."""
    if demand_std_per_period == 0:
        return 1.0 if safety_stock_qty >= 0 else 0.0
    return float(norm.cdf(safety_stock_qty / (demand_std_per_period * (risk_periods ** 0.5))))
```

This is the exact analytic inverse of `safety_stock()` — round-trips with it
the same way `test_cycle_service_level_inverse` already round-trips
`cycle_service_level_from_inventory()` (`tests/test_safety_stock.py:45-49`),
which is the test pattern to follow (§5).

**The comparison:** using the same μ=100, σ=25 scenario already in
`CASE_STUDIES.md` Exercise 2 (95% target, `safety_stock()` gives Ss≈41): a
common naive rule of thumb is "hold safety stock equal to 20% of average
demand" → `SS_naive = 0.20 × 100 = 20`. `achieved_service_level(20, 25, 1)`
computes what cycle service level that actually buys — hand-checkable
closed-form: `Φ(20/25) = Φ(0.8) ≈ 0.7881`, i.e. **the naive rule only
achieves ≈79% service level against a 95% target**, despite looking like a
reasonable buffer on paper. This is the sharper, more honest story than a
capital-efficiency framing (the naive rule here under-protects, it doesn't
over-spend) — exact value confirmed by running the code, not asserted here
as final.

### 3.2 EOQ — reuses existing `total_cost()` / `cost_ratio_vs_optimal()`

Same D=1000/yr, k=€50/order, h=€1.75/unit/yr scenario as `CASE_STUDIES.md`
Exercise 1 (Q*≈239, C*≈418). Naive baseline: ordering in a round lot of 500
units (a common "convenient batch size" choice). `total_cost(500, 1000, 1.75,
50)` vs `compute_eoq(1000, 1.75, 50).optimal_total_cost`, or directly
`cost_ratio_vs_optimal(500, 239)`. Hand-checkable: `TC(500) = 50·1000/500 +
1.75·500/2 = 100 + 437.5 = 537.5` vs `TC(239) ≈ 418.3` → **the round-lot
choice costs ≈29% more per year** than the EOQ-optimal quantity, for
identical demand and cost parameters. No new `src/` code — this exercise is
a direct application of functions that already ship in the product.

### 3.3 Multi-echelon — reuses existing `evaluate_serial_allocation()` / `optimize_serial_gsm()`

Naive baseline is **not** "each node covers only its own lead time"
(that specific allocation isn't necessarily a feasible/meaningful GSM point,
and hand-deriving its cost is error-prone without running the real
constraint-checked code — deliberately not asserted here). Instead, use the
**already-existing, already-tested** all-downstream case: every bit of
safety stock held at the single customer-facing node — the textbook "naive
default" of not pooling risk across a network at all. `tests/test_multi_echelon.py:39-53`
(`test_gsm_case4_all_downstream_cost`) already pins this exact scenario at
risk periods `(0, 0, 10)`, cost≈520, against the same lead
times/demand/holding-costs/service-level as `CASE_STUDIES.md` Exercise 6
(whose GSM-optimized answer is `(4, 0, 6)`, cost≈485). The comparison is
therefore: **520 (no pooling) vs 485 (GSM-optimized) ≈ 7% holding-cost
reduction** from optimizing risk-pooling across the chain — grounded in a
test that already exists and already passes, not a new derivation. No new
`src/` code.

### 3.4 Forecasting — new `src/benchmarks.py` + `scripts/benchmark_forecast_m5.py`

**Naive baseline:** `moving_average(history, window=1)` — mathematically
identical to naive persistence (repeat the last observed value), already
present in `src/forecasting.py`, no new function needed for the naive side.

**Linchpin's method:** `forecast_demand(history, method="auto")` — routes to
Croston's method for intermittent SKUs (ADI ≥ 1.32) or SES otherwise, per
existing logic.

**Scoring:** `src/forecast_metrics.py`'s `mae`/`wape` functions
(dependency-free, already used elsewhere in the repo for accuracy reporting).

**New pure function** in `src/benchmarks.py` (unit-testable with synthetic
arrays, no file I/O — follows this repo's convention of keeping `src/` pure
and pushing data concerns to `scripts/`/`jobs/`):

```python
@dataclass(frozen=True)
class ForecastComparison:
    naive_mae: float
    naive_wape: float
    smart_mae: float
    smart_wape: float
    improvement_pct: float  # (naive_wape - smart_wape) / naive_wape * 100

def compare_forecast_methods(
    actuals: list[float],
    naive_forecast: list[float],
    smart_forecast: list[float],
) -> ForecastComparison:
    ...
```

**Data — real M5, not synthetic:** genuine M5 competition data is already
present locally on this machine (not in this worktree, gitignored everywhere
it exists — see §4), specifically `sales_train_evaluation.csv` +
`calendar.csv`, which include the real, known actuals for the last 28 days
(`d_1914`–`d_1941`, the official M5 evaluation holdout). Train each method on
history through `d_1913`, score both against the real `d_1914`–`d_1941`
actuals on a sample of SKUs spanning steady/intermittent demand (mirroring
the existing `deliverables/portfolio` benchmark's sampling approach — cap the
sample size for runtime, e.g. ~50–100 SKUs across a few store/department
slices).

**New script** `scripts/benchmark_forecast_m5.py`: loads
`sales_train_evaluation.csv` + `calendar.csv`, builds each sampled SKU's
history, computes both forecasts, calls `compare_forecast_methods()` per SKU,
aggregates, prints a summary. Follows the existing `scripts/fetch_dataco.py`
/ `scripts/generate_portfolio.py` pattern (a runnable script, not a pure
`src/` module) since it needs pandas + file I/O, mirroring how `jobs/`
separates data-prep from the pure engine.

This script **cannot run in CI** (the M5 data isn't committed — same
constraint every Kaggle-sourced dataset in this repo already has) and
**cannot be literally re-fetched from Kaggle in this session** (no Kaggle
credentials available in this environment). It runs against the data that already exists locally at
`data/kaggle/m5/m5/datasets/` in the main checkout
(`C:\Users\Gamer\Music\scm\supply-chain-optimization`, sibling to this
worktree) — copy `sales_train_evaluation.csv` and `calendar.csv` into this
worktree's own gitignored `data/kaggle/m5/` before running it locally (a
local setup step, not something the plan can automate portably, and not
something CI will ever exercise). Document this constraint explicitly in the
script's docstring and in the new CASE_STUDIES.md exercise, exactly as
`.gitignore`'s existing "large external datasets... fetched locally, not
versioned" comment already frames every other Kaggle-sourced script in this
repo.

## 4. Data

| Path | Status | Role |
|---|---|---|
| `data/kaggle/m5/m5/datasets/sales_train_evaluation.csv` | Exists locally in the main checkout, gitignored, NOT in this worktree yet | Train history (`d_1`–`d_1913`) + real evaluation actuals (`d_1914`–`d_1941`) |
| `data/kaggle/m5/m5/datasets/calendar.csv` | Same as above | Maps `d_` day codes to real calendar dates |

Both must be copied from the main checkout into this worktree's local
(gitignored) `data/kaggle/m5/` before running `scripts/benchmark_forecast_m5.py`
— a one-time local file copy, not a code change, not committed.

## 5. Testing plan

Follows this repo's established convention exactly (confirmed by reading
`tests/test_safety_stock.py`, `tests/test_eoq.py`, `tests/test_multi_echelon.py`):
pytest, flat `test_*.py`, assertions against known values via
`pytest.approx` (`rel=` or `abs=` tolerance as appropriate), one test
docstring citing what the test pins.

- `tests/test_safety_stock.py`: add a test for `achieved_service_level()`
  that round-trips against `safety_stock()`/`service_level_factor()` using
  an *independent* formula (direct `norm.cdf` call, not calling
  `safety_stock()` itself — avoiding the tautology `test_cycle_service_level_inverse`
  already avoids), plus the concrete 20-units-at-σ=25 case
  (`achieved_service_level(20, 25, 1) == pytest.approx(0.7881, abs=0.001)`).
- `tests/test_benchmarks.py` (new): unit tests for `compare_forecast_methods()`
  using small synthetic `actuals`/`naive_forecast`/`smart_forecast` arrays
  with hand-computed expected MAE/WAPE — no file I/O, runs in CI like every
  other test in the suite.
- `scripts/benchmark_forecast_m5.py` is **not** unit tested directly (no
  data to test against in CI) — its only logic beyond data loading is a call
  into the already-tested `compare_forecast_methods()`, so the risk surface
  left uncovered by CI is deliberately just pandas plumbing, not the actual
  comparison math.
- Full suite (`pytest tests/ -q` with `PYTHONPATH=.`) must stay green;
  `ruff check src tests scripts` must stay clean.

## 6. Deliverable — `case-studies/CASE_STUDIES.md` Exercises 7–10

Appended before "## Further reading in the book" (matching the exact
existing format: `## Exercise N — <Title> (§chapter)`, a fenced `python -c`
block or script invocation, a `**Expected:**` line, a `---` separator):

- **Exercise 7 — Safety stock: naive % rule vs statistical formula (§4.2–4.3)**
- **Exercise 8 — EOQ: round-lot vs economic order quantity (§2.2.4)**
- **Exercise 9 — Multi-echelon: no risk-pooling vs GSM-optimized (§10.4)**
- **Exercise 10 — Forecasting: naive persistence vs classification-routed method, backtested on real M5 data (Ch. 9; Makridakis, Spiliotis & Assimakopoulos 2022, already cited in `documentation/CAPABILITY_EXPANSION_PLAN.md`)** — this one documents the local-data prerequisite inline (§3.4) since it can't be a bare `python -c` one-liner.

## 7. Workflow

Already on the right rails per this repo's conventions
(`git worktree add -b feat/benchmark-old-vs-linchpin ... origin/main`, done):
implement with TDD → `pytest tests/ -q` + `ruff check src tests scripts`
green → commit → push → `gh pr create --draft` → CI green (3.11/3.12/3.13,
Exercise 10's script excluded from CI per §3.4/§5, everything else covered)
→ `gh pr ready` → squash-merge. Never push straight to `main`. Re-check
`HANDOFF.md` and `git status`/`gh pr list` immediately before finalizing the
PR — this repo runs genuinely concurrent sessions (per its own documented
gotcha), and this worktree was branched from `origin/main` at a single point
in time that may have moved since.

## 8. Out of scope / follow-ups

- Updating the portfolio site's English case studies with these numbers
  (Phase 1b — separate task in the `portfolio-site` repo, after this PR
  merges).
- Translating the portfolio into es/pt/zh/ja (Phase 2 — separate spec,
  starts only after Phase 1b ships final English content).
- Any new agent-routable "benchmark" tool/capability — this is documentation
  and scripts, not a registry addition.
