# Testing environment — exercise Kern's capabilities and see where it fails

> Purpose: stand up a clean environment where every one of Kern's capabilities
> can be run end-to-end, and get an honest, per-capability signal of what works
> and what breaks. Three layers, cheapest first.

## TL;DR

```bash
bash scripts/setup-test-env.sh                      # 1. reproducible full install
PYTHONPATH=. python examples/run_capability_smoke.py # 2. exercise all 49 tools
PYTHONPATH=. pytest tests/ -q                        # 3. the full 3250+ test suite
PYTHONPATH=. python -m uvicorn webapp.app:app --reload  # 4. poke it by hand
```

Current baseline (2026-07-18, this container): capability smoke **39 PASS / 0
FAIL / 2 network-SKIP**; full suite **3250+ passed** once the environment is
complete. The one thing that actually "fails" out of the box is the environment
setup itself — see below.

---

## 0. The failure you hit first: the environment, not the code

The `SessionStart` hook installs dependencies with `pip install -r
requirements-dev.txt`. That command is **all-or-nothing**, and in this container
two things abort the *entire* transaction — leaving you with **zero** packages
(not "most of them"), which is why `import pandas` fails on a fresh session:

1. `extruct` → `jstyleson` can't build a wheel against the container's old
   setuptools (`AttributeError: install_layout`). Upgrading pip/setuptools/wheel
   first makes it build.
2. Debian-managed packages (`cryptography`, `PyJWT`, `wheel`, `setuptools`) can't
   be cleanly uninstalled by pip (`RECORD file not found`), so any heavier extra
   that wants to upgrade them aborts the run.

`scripts/setup-test-env.sh` fixes both (modern build tooling +
`--ignore-installed` on the system-managed packages), then installs the full dev
set and **verifies the optional extras actually import** — so a half-install
surfaces immediately instead of as a capability that silently degrades later.
The hook now delegates to this script, so future web sessions boot complete.

### On Windows: a deep checkout path fails tests that look like real bugs

`tests/test_price_monitor_job.py` fails two tests with

```
FileNotFoundError: [Errno 2] No such file or directory:
  'data\pricing_intel\ledger\example-retailer.test\date=.../<uuid>-<uuid>.parquet'
```

**when the checkout sits under a long path.** It is not a bug in the ledger, and
not missing local state — `PriceLedger._write_batch` does call
`site_dir.mkdir(parents=True, exist_ok=True)`. It is Windows' **260-character
`MAX_PATH`**: `mkdir` succeeds on the shorter directory path, and then `open()`
on the file blows past the limit and reports the classic misleading
`FileNotFoundError`.

Measured 2026-08-18: from a checkout at 150 chars the full path reached **275**
and the two tests failed; from `C:\Users\Gamer\Music\scm\kern-main` (34 chars)
**all 14 pass**.

The partition path is long by construction — site + `date=YYYY-MM-DD` + a
`<uuid>-<uuid>.parquet` filename is ~110 characters before the repo root. So:

- **Keep the checkout shallow.** The repo's own `Music/scm/.wt-*` worktrees are
  the right pattern; a temp/scratch directory usually is not.
- Before filing a bug on a `FileNotFoundError` under `data/`, **measure the
  path**. If it is near 260, that is the answer.
- Enabling long paths (`HKLM\SYSTEM\CurrentControlSet\Control\FileSystem`
  `LongPathsEnabled=1`) also removes it, but it is a machine-wide change and is
  not required to work on Kern.

This matters beyond the annoyance: a run from a deep path reports two failures
that are indistinguishable, at a glance, from a real regression — and with CI
red for `NOSILO_CORE_TOKEN`, a hand-run suite is the only signal there is.

---

## 1. Reproducible install — `scripts/setup-test-env.sh`

```bash
bash scripts/setup-test-env.sh          # verbose
KERN_SETUP_QUIET=1 bash scripts/setup-test-env.sh   # quiet (what the hook runs)
```

Idempotent and non-interactive. Installs everything CI installs (engine + tests
+ web + every capability extra: pricing-intel/extruct, forecasting, MCDM, state,
Tower, dataquality, matching, elasticity, SEO), then asserts the extras import.
Exit code is non-zero if any key import is missing.

---

## 2. Capability smoke harness — `examples/run_capability_smoke.py`

The "see where it fails" tool. For each of the 41 registered tools it forces
`job_type=<key>` (so routing never hides a tool), feeds a representative brief +
a minimal, schema-valid fixture (synthetic CSVs generated at runtime — nothing
committed), runs the full `prepare → run → QA → deliver` pipeline, and buckets
the result:

| Bucket | Meaning | Statuses |
|---|---|---|
| **PASS** | produced a deliverable | `ok` |
| **GATED** | ran, then protectively asked for input (by design) | `needs_data`, `needs_clarification` |
| **FAIL** | actually broken — look here | `qa_failed`, `error`, uncaught exception |
| **SKIP** | network tool, offline | (only `price_watch`, `price_intelligence`) |

The PASS/GATED split is deliberate: a tool that asks for a missing capacity or a
second input is *working* (the never-unprotected contract), not failing. Only
the FAIL bucket is red, and the script's exit code is non-zero iff any tool
FAILs — so it drops straight into CI or a pre-push check.

```bash
PYTHONPATH=. python examples/run_capability_smoke.py                 # all tools, matrix + reports
PYTHONPATH=. python examples/run_capability_smoke.py --only queuing dea sop
PYTHONPATH=. python examples/run_capability_smoke.py --include-network   # also try the 2 network tools
```

Writes `deliverables/capability_smoke/report.{md,json}` (gitignored) plus every
tool's actual deliverables under `deliverables/capability_smoke/<key>/`, so you
can open the real Excel/report/chart each capability produced.

Adding a new tool? Add its `key` to `_BRIEF` and a fixture (`_SAMPLE` for an
existing dataset, `_CSV` for an inline one, or a `_write_*` helper) — the harness
picks it up automatically from the registry.

### Robustness pass — `--stress`

The default run is the happy path (does each capability *work*). `--stress` is
the other half of "where does it fail": it feeds every data tool three
degenerate inputs derived from its fixture — **empty** (headers, no rows),
**wrong-schema** (none of the expected columns), **garbage** (right columns,
non-numeric junk) — and buckets each tool by its worst outcome:

| Outcome | Meaning |
|---|---|
| **CRASH** | an uncaught exception escaped the tool — a real robustness bug |
| **SUSPECT** | returned `ok` on degenerate input — a deliverable built from garbage |
| **GRACEFUL** | clean protective status with a message (`needs_data`/`error`/`qa_failed`) |

```bash
PYTHONPATH=. python examples/run_capability_smoke.py --stress
```

Exit code is non-zero iff any tool CRASHes. Baseline (2026-07-18): **0 CRASH /
2 SUSPECT / 32 GRACEFUL** across 34 CSV-driven tools — nothing breaks on bad
input. The 2 SUSPECTs are by-design: `data_quality` *audits* junk (reporting
0% quality is the correct answer), and `forecast` coerces non-numeric demand to
NaN and returns a hedged "review" signal rather than a number. Treat SUSPECT as
"look and confirm it's intentional", CRASH as "fix it".

---

## 3. Full test suite

```bash
PYTHONPATH=. pytest tests/ -q            # ~5 min, 3250+ tests
ruff check src tests examples            # lint scope CI enforces
```

The suite is the deep signal: engine numbers checked against the source books,
agent routing, HTTP guards, connector contracts. If the environment is complete
(step 1) it is green; the 27 failures you see on a bare container are **all**
missing-dependency errors in the `pricing_intel` modules (`bs4`/`extruct`), not
logic bugs — they disappear after `setup-test-env.sh`.

---

## 4. Interactive environment — the webapp

```bash
PYTHONPATH=. python -m uvicorn webapp.app:app --reload    # http://localhost:8000
```

- `/` — the operator dashboard.
- `/static/prototype/index.html` — the live agent console (type a brief, watch
  it classify → run → QA → deliver).
- `POST /api/jobs` — the same pipeline over HTTP (`-F 'brief=...'` plus an
  optional `-F 'data=@file.csv'`), returning the `JobResult` JSON.

Boots on the `[web,mcp]` extras only (the `prod-boot` CI job guards this), so if
the dashboard imports here it imports in production.

---

## 5. Scoreboard against known truth — `examples/run_world_sim.py`

Layers 1-4 answer "does it run". This one answers **"does it decide well"**.

`src/simworld/` generates a seeded replenishment world (`world.py`) — true
weekly demand and true lead times are drawn once at generation time, never
during the run, so every runner faces the identical realization — then runs
three runners over the same world for N weeks:

| Runner | What it is |
|---|---|
| Floor | Fixed reorder point (`5 x` the last 4 weeks' average demand), computed once after `NAIVE_LOOKBACK_WEEKS=4` weeks of warmup and **genuinely frozen** thereafter — never revised again for the rest of the run. What a real client's own unmanaged process does. |
| Kern | The full `Orchestrator.run` pipeline (brief -> intent -> prepare -> run -> QA -> deliver), replanning every `--replan-every` weeks (default 4) |
| Ceiling | An oracle that sees the true future demand and true lead times, never the lens |

```bash
PYTHONPATH=. python examples/run_world_sim.py                             # small default: 8 SKUs, 24 weeks
PYTHONPATH=. python examples/run_world_sim.py --no-kern                    # floor vs ceiling only, cheap
PYTHONPATH=. python examples/run_world_sim.py --skus 120 --weeks 52 --levels 0 1 2 3   # production scale, several minutes
```

**The oracle is exact, not a heuristic window** — a per-SKU, cost-minimizing
MILP (Wagner-Whitin-style lot-sizing with a fixed order cost and a lost-sales
option, `scipy.optimize.milp` via HiGHS) that optimizes the *same* cost
function `scorer.py` uses to judge every runner (procurement + holding +
shortage + fixed order cost), so the ceiling and the ruler that measures it
never disagree. This is a hard-won, adversarially verified property, not
marketing language: it went through multiple rounds of adversarial review,
including an independent from-scratch dynamic-program re-derivation checked
against 214 hand-built test instances with zero mismatches. It is also
expensive — one MILP solve per SKU per week-call, ~50-60ms via HiGHS — so a
full production-scale grid (120 SKUs x 52 weeks x 4 dirt levels) is estimated
at roughly 15-20 minutes (documented, not yet exhaustively measured
end-to-end). That cost is accepted, not yet optimized: solving each SKU's
full-horizon plan once instead of re-solving it every week-call is a known
future improvement, not implemented today.

**Captured score:** `(floor_cost - kern_cost) / (floor_cost - oracle_cost)`.
0 is no better than the frozen floor, 1 is matching the oracle; a negative
score (worse than the floor) is reported with its sign, never clipped. If
Kern's score comes out above 1 the CLI raises instead of printing a number —
beating a clairvoyant oracle means information leaked from the world into the
lens, not a real win.

**Staged dirt.** Kern never reads the world directly — only an exported CSV,
at level 0 (canonical, what `jobs/intake.py` expects verbatim), 1 (cosmetic:
whitespace/case, mixed date formats, thousands separators), 2 (typical
SME mess: one SKU split across two spellings, mixed units, negative-quantity
returns, missing lead times) or 3 (chaotic: duplicate rows, zeroed costs, a
mid-history supplier rename, an unmarked promo spike). Levels accumulate
(3 includes 2 includes 1). The score is always computed against the clean
truth, even when Kern only ever saw the dirty export.

**Announced degradation** — the number no published benchmark measures. For
each dirt level (against that level's own lane at level 0) the report carries
three headline figures — how much the dirt cost (`drop`), how much of that
Kern announced (`announced`, via its own status, QA issues, a blocking
`GuidedOutcome`, or `IntakeQuality` crossing its own declared threshold), and
the difference (`silent`) — plus a "largest single week" figure, since one
outlier week can dominate the total. Two honest, deliberate biases in this
metric, stated rather than hidden:
- "Silent" only reflects the *same replan week's* announced flag. A week
  governed by an earlier warning (from a prior replan) still counts as silent
  here, even though a real operator would still be acting on that earlier
  warning. This biases the number conservatively, against Kern — it
  under-credits Kern for warnings it already gave.
- The metric can be dominated by a single outlier week, which is exactly why
  the CLI's report prints that "largest single week" figure next to the three
  headline scalars.

**Kern can escalate rather than guess — but the fixes below mean it no longer
needs to just to survive normal-looking dirt.** Before the 5 fixes documented
next, even cosmetic-only dirt (level 1) drove *every* replan on the harness's
production-scale grid (120 SKUs, 52 weeks) to `ESCALATED` — not because Kern's
judgment was too conservative, but because two real intake bugs (mixed date
formats, mixed US/LATAM number formats) were silently dropping enough rows to
cross Kern's own data-quality threshold on data that was actually fine. With
those bugs fixed, the same grid shows **zero escalations across all 4 dirt
levels** — Kern operates normally everywhere. The capture numbers that used to
be quoted here (-6.8% at level 3 up to +5.0% at level 2, "a genuinely graduated
curve") are **withdrawn as of 2026-09-18**: that curve was an artifact of the
cost function, which valued a lost sale at `margin` while `procurement` already
charged `unit_cost` -- subtracting the unit cost twice per unit served, so
serving was accounting-worse than not serving for 42.86% of the catalogue and
the metric rewarded under-serving. Fixed in `7aa08c5`; the 25-world baseline and
its full reading live in
[`KERN_CAPTURA_BAJO_DISRUPCION.md`](KERN_CAPTURA_BAJO_DISRUPCION.md). What still
stands from this paragraph is the escalation finding, which does not go through
`capture` at all. The escalation mechanism itself is still
real and still exercised elsewhere in the suite — a `GuidedOutcome` blocked on
truly missing or contradictory data still routes to `ESCALATED`/`HANDOFF`,
never a crash, and the harness still prices a genuine escalation at full cost
like any other week. The harness's own early runs were themselves a case
study in the exact silent-degradation failure mode Kern is supposed to catch,
one level removed.

This harness has surfaced **5 real bugs** — 4 in Kern's core production code,
1 in the harness's own dirt injector, all fixed on this branch:
- `jobs/intake.py`'s date parser used to corrupt or drop rows on a genuinely
  mixed-format date column.
- `jobs/intake.py`'s number parser didn't handle mixed US/LATAM thousand-
  separator conventions — a single cosmetic-level injector alone dropped up
  to 31% of rows on data that was perfectly valid, just formatted differently.
- `jobs/inventory_optimization.py` used to let one SKU with zero aggregate
  demand crash the entire portfolio's run instead of being isolated, flagged,
  and skipped while the rest of the portfolio kept processing normally.
- `jobs/inventory_optimization.py` (via `src/sources.py`) used to let a
  single `$0` unit cost — a cost lost on load, not a genuinely free item —
  silently explode the EOQ formula through its holding-cost floor: one bad
  SKU drove a full production-scale grid run to a capture score of **-14.04**
  (**$29.6M** cost against a **$7.4M** floor), with no crash and no QA flag.
  The most dramatic finding of the five — exactly the class of confident-but-
  wrong decision this harness exists to catch.
- `src/simworld/lens.py`'s own `inject_returns_as_negatives` produced an
  undetectable `-0.0` (`-0.0 < 0` is `False` in IEEE 754) instead of a real
  negative value, silently weakening its own level-2 dirt signal.

Writes `deliverables/simworld/report.{md,json}` (gitignored).

**Never register this as an agent capability.** `src/simworld/` is a test
harness, not a client-facing tool — there is no `register()` call for it in
`scm_agent/tools.py`, and there must never be one.

---

## Where to look when something is red

- **`import` errors / half the suite red on a fresh session** → step 1, the
  environment. Re-run `scripts/setup-test-env.sh`.
- **A capability FAILs in the smoke matrix** → run it alone with `--only <key>`;
  the `detail` column carries the QA issue or exception. Then
  `graphify affected "<module>.<fn>"` to see the blast radius.
- **A number looks wrong** → the book-grounded test in `tests/` is the oracle;
  `python examples/query_knowledge.py --bridge "<topic>"` maps it to chapter+code.
