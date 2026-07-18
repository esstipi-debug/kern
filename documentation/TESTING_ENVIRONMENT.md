# Testing environment — exercise Kern's capabilities and see where it fails

> Purpose: stand up a clean environment where every one of Kern's capabilities
> can be run end-to-end, and get an honest, per-capability signal of what works
> and what breaks. Three layers, cheapest first.

## TL;DR

```bash
bash scripts/setup-test-env.sh                      # 1. reproducible full install
PYTHONPATH=. python examples/run_capability_smoke.py # 2. exercise all 41 tools
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

## Where to look when something is red

- **`import` errors / half the suite red on a fresh session** → step 1, the
  environment. Re-run `scripts/setup-test-env.sh`.
- **A capability FAILs in the smoke matrix** → run it alone with `--only <key>`;
  the `detail` column carries the QA issue or exception. Then
  `graphify affected "<module>.<fn>"` to see the blast radius.
- **A number looks wrong** → the book-grounded test in `tests/` is the oracle;
  `python examples/query_knowledge.py --bridge "<topic>"` maps it to chapter+code.
