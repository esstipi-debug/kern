# Contributing

Thank you for improving **Kern** — an agentic supply-chain engine grounded in the
SCM literature.

Read [`.specify/memory/constitution.md`](.specify/memory/constitution.md) first.
It is short, and it is where the rules that actually block a merge live: the
eight fixed places a new capability touches, the QA gate, the data contract, and
the boot-chain import rule. This file is the practical how-to; the constitution
supersedes it wherever they disagree.

## Setup

```bash
git clone https://github.com/esstipi-debug/kern.git
cd kern
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
PYTHONPATH=. pytest tests/ -q
```

Two things that will bite otherwise:

- **`nosilo_core` is a private dependency, not a directory in this repo.** The
  full install needs credentials for it. Without them, set `KERN_CI_NO_CORE=1`
  and the suite skips the modules that import it — that is the same path CI takes
  when the token is missing.
- **A stray `nosilo_core/` folder at the repo root silently corrupts every local
  `ruff` run**, including the write hook, because ruff's isort then treats it as
  first-party and demands the opposite import grouping from the one CI wants.
  Symptom: dozens of `I001` locally that a clean checkout does not have.
  Fix: `rm -rf nosilo_core`.

## Layout

| Path | What lives there |
|---|---|
| `src/` | The engine. Pure functions, one module per question (`eoq.py`, `safety_stock.py`, `forecasting.py`, …) |
| `jobs/` | One `<x>_job.py` per capability: a pandas-only `prepare()` that reads its own CSV, plus `run`, `verify`, and the deliverable builders |
| `scm_agent/` | The orchestrator, the tool definitions and the registry assembly |
| `webapp/` | FastAPI dashboard, `/api/jobs`, the agent console |
| `packs/` | Domain packs |
| `tests/` | Flat, one file per subject. Several are META-tests that fail when CI config, counts or contracts drift |
| `examples/` | Runnable CLIs |

## Adding a capability

It is **not** one `register()` call, whatever the README once said. The eight
fixed places are enumerated in the constitution, with the reason each one exists.
Read them before you start; every one of them has been forgotten at least once.

The short version: a `jobs/<x>_job.py`, three functions plus an import and a
`register()` line in `scm_agent/tools.py`, an entry in `TOOL_CONCEPTS`
(`scm_agent/citation_gate.py`) and in `TOOL_TITLES` (`src/i18n.py`, Spanish and
English must differ), and two pinned counts in `tests/`.

If the tool consumes a data file it must also declare `required_columns` — or
stay listed in `tests/test_tool_data_contract.py::UNDECLARED_TOOLS` **with a
written reason naming the code that makes it undeclarable**. That set only ever
shrinks. Declare from the job's own alias tuples, not from its error labels, and
never declare two names that are aliases of each other: the guard drops one
required column at a time and the other would stand in for it.

## Before you open the PR

```bash
ruff check .
PYTHONPATH=. pytest tests/ -q --cov=packs --cov=src --cov=scm_agent --cov=jobs --cov-fail-under=90
```

`make lint` and `make test` are aliases for exactly those two commands, and
`tests/test_ci_lint_and_coverage_scope.py` fails if they ever stop matching what
CI runs. They had drifted before that guard existed, and a convenience alias that
runs a weaker check than CI is worse than no alias at all.

## PR checklist

- [ ] The failing test landed before the code that makes it pass
- [ ] `ruff check .` clean and the suite green at the 90% floor
- [ ] Console output is ASCII-only (Windows cp1252 raises on em dashes)
- [ ] No module-level import of an optional-extra dependency anywhere on
      `webapp.app → scm_agent → tools → jobs → src` — production installs only
      `.[core,web,mcp,pricing-intel,dataquality]`, so the full suite stays green
      while the app fails to boot
- [ ] Dependency changes went through `uv`, never bare `pip install` — `uv.lock`
      is what CI and the container resolve from
- [ ] Any count you wrote is derived from `build_default_registry()` or pinned to
      a commit SHA in the same sentence
- [ ] `CHANGELOG.md` updated when behaviour changed
- [ ] No secrets, no `.env`, no `output/`, and nothing that surfaces client PII

## Workflow

Feature branch → **draft PR** → CI green on py3.11/3.12/3.13 → **squash-merge**
to `main`. Never push straight to `main`.

Specs and plans for new work live in `specs/<NNN>-<slug>/`. The artifacts under
`docs/superpowers/` are the historical record and stay where they are.

## Questions

Open a GitHub issue with reproduction steps and your Python version.
