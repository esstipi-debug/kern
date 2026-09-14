# AGENTS.md

Project-wide agent guidance lives in [`CLAUDE.md`](CLAUDE.md) (architecture, dev
conventions, gotchas). Read it first. This file adds only Cloud-agent-specific
environment notes.

## Cursor Cloud specific instructions

Kern is a single Python product: a deterministic supply-chain **engine** (`src/`)
under an **orchestrator agent** (`scm_agent/`, `jobs/`), served by one
long-running service — the FastAPI **webapp** (`webapp/app.py`, which also mounts
a read-only MCP server at `/mcp` in-process). There is no database/Redis/broker;
state is file-based. Standard lint/test/run commands live in `CLAUDE.md` and
`documentation/TESTING_ENVIRONMENT.md` — this section only records the non-obvious
caveats.

### The one hard blocker: the private `nosilo-core` control plane
`scm_agent`, `jobs`, and therefore `webapp.app` **hard-import `nosilo_core`**
(`from nosilo_core.guided import ...`, `from nosilo_core.llm import ...`). That
package is a **separate private repo** (`esstipi-debug/nosilo-core`, pinned to tag
`v0.1.0`) and is **not vendored** here. The repo's default GitHub token cannot read
it. Consequences when it is absent:
- The **webapp will not boot** and the **agent/orchestrator layer cannot import**.
- In the test suite, ~205 files fail to collect and ~113 tests fail — **all of it
  traces to `ModuleNotFoundError: No module named 'nosilo_core'`**. The pure
  **engine** still runs fully (~2593 tests pass): the math path never imports
  `nosilo_core` (see `docs/NOSILO_CORE_FALLBACK.md`).

To unblock the full product, add one of these as a Cloud secret (see
`.github/actions/authenticate-core/action.yml` and `docs/DEPLOYMENT.md` §0):
- `NOSILO_CORE_TOKEN` — fine-grained PAT, `Contents: Read` on `esstipi-debug/nosilo-core`, or
- `NOSILO_CORE_SSH_KEY` — read-only deploy-key private key (does not expire; preferred).

When `NOSILO_CORE_TOKEN` is present, the update script installs `.[core]`
automatically (it rewrites only the `nosilo-core` git URL for that one pip call, so
it never disturbs the repo's default GitHub credential). To install core by hand
once the secret exists:
```bash
GIT_CONFIG_COUNT=1 \
  GIT_CONFIG_KEY_0="url.https://x-access-token:${NOSILO_CORE_TOKEN}@github.com/esstipi-debug/nosilo-core.insteadOf" \
  GIT_CONFIG_VALUE_0="https://github.com/esstipi-debug/nosilo-core" \
  python3 -m pip install -e ".[core]"
```

### Running things
- Always run with `PYTHONPATH=.` (webapp/scm_agent/jobs are imported off the source
  tree, not packaged). Pytest already sets this via `pyproject.toml`.
- `pip` installs console scripts to `~/.local/bin`, which is **not on `PATH`**.
  Invoke tools as modules: `python3 -m pytest`, `python3 -m ruff`, `python3 -m
  uvicorn` (or add `~/.local/bin` to `PATH`).
- Webapp (dev, needs the core secret to boot):
  `PYTHONPATH=. python3 -m uvicorn webapp.app:app --reload` → http://localhost:8000
  (routes `/`, `/console`, `/warehouse`, `/operator`, `POST /api/jobs`, `/mcp`).
- Engine-only smoke (no core needed): `PYTHONPATH=. python3 examples/run_part1_part2.py`,
  `PYTHONPATH=. python3 examples/run_batch.py --output output/batch.csv`.

### Two peripheral test failures unrelated to the app
Independent of the core blocker, these fail only because of environment tooling,
not the product:
- `tests/test_seo_audit_job.py` expects the `scrapy` binary on `PATH` (optional
  `seo` capability); it lives in `~/.local/bin`.
- `tests/test_claude_hooks.py` exercises the `.claude/hooks/uv-guard.sh` command
  guard, which needs its helper tooling available to return the expected exit code.
