# Linchpin — Session Handoff

**Date:** 2026-07-03 · **Repo:** `esstipi-debug/linchpin` (now **private**) · **Branch:** `main` @ `e84ca2a` (PRs up to **#98**, plus an unmerged in-progress branch fixing 2 production bugs found live, below)
**Purpose:** pick up Linchpin work in a fresh session without re-deriving context.
**Resume here — THE APP IS LIVE.** Both §3.2 follow-up security gaps are closed (PRs #94, #95). §3.1's deploy host **pivoted from Railway to Fly.io** (the user's Railway trial ran out before a deploy happened) — and this time the deploy actually ran, all the way through, using a Fly API token the user provided: **`https://linchpin.fly.dev` is live right now**, `/api/health` returns real data, `/` is 200, `/mcp` mounts correctly, `POST /api/jobs` correctly 401s without a key. Two real production bugs surfaced and were fixed during the live deploy (not caught by local `docker build`/`docker run` testing, which only ran the container standalone without a Fly Volume attached):

1. **2 uvicorn workers OOM-killed the 512mb VM** in a crash-restart loop within ~10-25s of every boot — each worker independently loads pandas/numpy/scipy + the orchestrator + the L3 graph, so memory cost scales linearly with `--workers`. Fixed: `WEB_CONCURRENCY=1` in `fly.toml`.
2. **Mounting the persistent Volume at `/app/data` shadowed the small static sample CSVs baked into the image at that same path** (a Volume mount hides whatever's already on disk at its destination) — crashed with `FileNotFoundError` on `data/sample_demand_portfolio.csv`. Fixed: Volume now mounts at `/data` instead, `LINCHPIN_MCP_KEYS_PATH=/data/mcp_keys.sqlite3`.

Both fixes are applied live (confirmed stable, health checks passing) but **not yet committed to `main`** as of this write-up — they're sitting on an unmerged branch (see §3.1 below for exact state). **Do not re-run `fly deploy` from a stale `main` checkout without first merging that branch**, or you'll reintroduce both bugs. A separate bug was ALSO caught and fixed earlier the same session in the already-merged `railway.json`/`docs/DEPLOYMENT.md` (PR #98): `pip install -e ".[web]"` alone omits the `mcp` extra, which `webapp/app.py` hard-requires (`ModuleNotFoundError: No module named 'mcp'`) — fixed to `.[web,mcp].` `railway.json` + its docs section remain kept as a demoted "2b. Alternative" in case Railway becomes viable again later. The user's stated objective for this whole project, verbatim: *"el objetivo de este agente es generar dinero"* — default to whatever advances revenue over further engine/backlog polish unless told otherwise. **Immediate next step, now that the server is actually live: register with the MCP registry and list on Glama/Smithery/PulseMCP (§3.1) — the prerequisite that blocked this for two sessions is finally gone.**

> A new Claude Code session in this repo also auto-loads memory: `MEMORY.md` →
> [[linchpin-project]], [[linchpin-priority-monetization]], [[linchpin-monetization-plan]],
> [[linchpin-audit-fixes-2026-07]], [[linchpin-formula-injection-fix]],
> [[linchpin-concurrent-sessions]]. This file is the human-readable, in-repo
> consolidation — memory has the play-by-play and the full research trail
> (real 2026 market data on MCP directories, x402 adoption, Odoo Store pricing).

---

## 1. What Linchpin is

Agentic supply-chain AI: a deterministic Python engine (EOQ, safety stock,
(s,Q)/(R,S), multi-echelon GSM, DDMRP, ABC-XYZ, newsvendor, queuing, scheduling,
facility location, DRP, transportation, FEFO, financial KPIs, supplier
scorecards, MCDM sourcing, landed cost, cost-to-serve, S&OP, reverse logistics,
warehouse layout, voice doc-reader) + an orchestrator agent, grounded in an
**L3 knowledge graph** (24 curated SCM sources) and packaged through a
**client-ready deliverable generator** (md + xlsx, cited). Where it can't act
itself, it hands off a ready-to-execute packet (the "never unprotected" Guided
Execution Layer, `src/guided.py`). Live **Odoo ERP connector** (`src/connectors/odoo.py`)
reads/writes through the safe-staging plane (`src/writeback.py`).

**New as of this session:** Linchpin now also **exposes** an MCP server of its
own (`webapp/mcp_server.py`, mounted at `/mcp`), not just consumes one
(graphify). This is Phase A of an explicit, memory-tracked go-to-market plan —
see [[linchpin-monetization-plan]] for the full ladder (MCP read-only → Stripe
metered → x402 pay-per-call) and where-to-publish research (Odoo Apps Store 70%
split, AI-agent directories, GEO, a portfolio site vs. Upwork/Fiverr commission).

---

## 2. Current state (verified 2026-07-02, after PR #96)

- **Tests:** 1202 passing, 13 skipped (`PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/ -q`). `ruff check src tests examples` clean.
- **Agent surface: still 34 tools** in the registry (`build_default_registry()` — don't trust any doc's number without re-running that check). Of those, **8 are also reachable over MCP** (see below) — the rest remain internal/dashboard-only for now.
- **Repo visibility: private** (the user made it private mid-session; it was public before, and the committed `knowledge/scm-books/graph.json` — 2.2MB of curated 24-source distillation — was exposed the whole time it was public). Not retroactive: any pre-existing clone/fork keeps its copy.
- **MCP server (PR #91, shipped):** `webapp/mcp_server.py`, mounted at `/mcp` on the existing FastAPI app, Streamable HTTP transport. Exposes exactly 8 read-only tools (`linchpin_inventory_optimize`, `linchpin_classify_abc_xyz`, `linchpin_newsvendor_order_quantity`, `linchpin_forecast_demand`, `linchpin_financial_kpis`, `linchpin_price_optimize`, `linchpin_audit_data_quality`, `linchpin_whatif_sensitivity`). `odoo_replenishment` and everything else that can write to a client's system of record is deliberately NOT exposed — traced and confirmed by an independent security review, not just asserted in a comment. Auth: new `src/mcp_keys.py::McpKeyStore` (SQLite, per-client high-entropy keys, hash-only at rest, issue/validate/revoke) + `webapp/mcp_auth.py` middleware — separate from the dashboard's single shared `LINCHPIN_API_KEY`. Rate limiting is **identity-aware** (keyed by resolved client name post-auth, not shared source IP — a real bug the security review caught and this session fixed before merge). Manual key issuance only: `python examples/issue_mcp_key.py issue "<client name>"`. Full reference: `docs/MCP_SERVER.md`.
- **Formula-injection fix (PR #92, shipped) — now fully closed (PR #94, this session):** the MCP security review incidentally surfaced a pre-existing CSV/Excel formula-injection gap (OWASP CSV injection) in the dashboard's regular deliverable exports — a `product_id` starting with `=`/`+`/`-`/`@` survived unescaped into generated `.xlsx`/`.csv` files, and openpyxl auto-promotes a leading `=` string to a live formula. Fixed: new `src/sanitize.py::defuse_formula()`, wired into every confirmed sink (`src/excel_export.py`, `jobs/deliverables.py`, `src/export.py::write_summary_csv()`). **PR #94 closed the one sink PR #92 missed**: `src/powerbi_export.py` has its own `_write()` CSV helper that never routed through `write_summary_csv()` — now sanitized the same way.
- **Unauthenticated deliverable downloads — closed (PR #95, this session):** `GET /jobs-output/*` (serving generated job deliverables) had zero auth of its own, only an unguessable `tempfile.mkdtemp()` dir name — while `POST /api/jobs` was already gated by `LINCHPIN_API_KEY`. New `webapp/security.py::jobs_output_auth_middleware` requires the same key once configured (no-op when unset). Watch the registration order in `webapp/app.py` if you touch this again: it must be registered **before** `security_headers_middleware` so the latter stays the outermost layer — a code-review pass caught (and this session fixed + verified live) that the reverse order silently strips every hardening header off this middleware's own 401 responses. Both §3.2 follow-ups from PR #92 are now closed; no known formula-injection or jobs-output auth gaps remain.
- **Deployment — LIVE on Fly.io as of 2026-07-03.** Railway's `railway.json` (PR #96) is kept as a demoted "2b. Alternative" in `docs/DEPLOYMENT.md`, but the user's Railway trial ran out before anyone actually deployed anything, so Fly.io became the primary path — and this time it went all the way to a real deploy. **App: `linchpin` on Fly, org `personal`, region `iad`, live at `https://linchpin.fly.dev`.** The user provided a Fly API token (pasted directly in chat — flagged to them as now-exposed and worth rotating after the fact) which was used non-interactively: installed `flyctl` (wasn't on this machine before), created the app + a 1GB Volume, staged secrets (`LINCHPIN_API_KEY`/`LINCHPIN_APPROVAL_SECRET`, freshly generated, never printed to any log/output), and ran `fly deploy`. Verified live, not just "deploy succeeded": `/api/health` returns real SKU data, `/` is 200, `/mcp` mounts (307), `POST /api/jobs` correctly 401s without a key, security headers present on every response. **Two real bugs only surfaced at this stage** (local `docker build`/`docker run` testing earlier the same session didn't catch them, since that test ran the container standalone with no Fly Volume attached) — see the top of this file for both (OOM at 2 workers; Volume-mount path shadowing the baked-in sample CSVs). Both are fixed in `fly.toml` **but that fix is not yet merged to `main`** — see §3.1.
- **Bug fixed earlier the same session (PR #98, merged):** `railway.json`'s `buildCommand` and `docs/DEPLOYMENT.md`'s generic "Run it" section both said `pip install -e ".[web]"`, missing the `mcp` extra — but `webapp/app.py` unconditionally imports `webapp.mcp_server`, which hard-imports the `mcp` package, so a fresh install with only `.[web]` crashes at import with `ModuleNotFoundError: No module named 'mcp'`. Verified this failure and the `.[web,mcp]` fix in an isolated venv before committing. This is a DIFFERENT bug from the two found during the live Fly deploy above — three distinct deploy-config bugs surfaced this session in total, none of which the local Docker test or the full pytest suite caught (none of them are Python-level bugs; all three are infra/config-level, a category this repo's tests structurally can't cover).
- **README.md / docs:** already refreshed this cycle (PR #87, #90) — 34-tool capability tables, book/author attribution removed, MIT license section removed from the README text (the actual `LICENSE` file at the repo root was intentionally left untouched — a separate decision the user hasn't made yet).
- **Odoo connector:** unchanged — still needs validation against a REAL Odoo instance (user has none yet; don't treat as urgent unless they say otherwise).

---

## 3. Immediate next steps, in priority order

### 3.1 [explicitly requested by the user] Publish the MCP server on free directories

**The prerequisite that blocked this for two sessions is now gone: the server
is live at `https://linchpin.fly.dev`, verified end-to-end (2026-07-03).** How
it got there: the user pasted a Fly API token directly in chat (flagged as
now-exposed, worth rotating); that token was used to install `flyctl`
non-interactively, create the `linchpin` app + a 1GB Volume in the `personal`
org (region `iad`), stage fresh secrets, and run `fly deploy`. Two real
production bugs surfaced and were fixed live (see top of file + `fly.toml`
comments): `WEB_CONCURRENCY=1` (2 workers OOM'd a 512mb VM) and the Volume
mount moved from `/app/data` to `/data` (it was shadowing baked-in sample
CSVs). **`flyctl` itself is now installed at `C:\Users\<user>\.fly\bin\flyctl.exe`**
on this machine (wasn't before) — a fresh session doesn't need to reinstall it.

**Remaining steps, in order:**

1. **Merge the pending bugfix branch first** (see `git branch`/`gh pr list` for
   its current name/PR number — it fixes `fly.toml`'s `WEB_CONCURRENCY` and
   Volume mount path to match what's actually live) **before running `fly
   deploy` again from `main`** — deploying a stale `main` would silently
   reintroduce the OOM crash-loop and the shadowed-sample-CSVs bug, since
   those fixes currently exist only as live Fly config + an uncommitted/PR'd
   `fly.toml` change, not yet on `main`.
2. **Issue an MCP key for a real client** once one exists —
   `fly ssh console -C "python examples/issue_mcp_key.py issue '<client
   name>'" --app linchpin` (runs inside the deployed environment, against the
   mounted Volume at `/data`). Not done yet — no real paying/trial client to
   issue one for as of this write-up.
3. **Register with the official MCP registry**, then list/claim on the
   directories researched previously (see [[linchpin-monetization-plan]] §5
   for the real 2026 numbers): **Glama** (~37-51k servers indexed), **Smithery**
   (~7k+, app-store UI, hosted remote servers), **PulseMCP** (~12-18k+,
   hand-reviewed) — these are discovery storefronts that read off the official
   registry and let an owner claim their listing; check each one's actual
   current submission process before assuming it's identical across all three,
   it still wasn't independently verified step-by-step, only that they exist
   and are free. **This is now the actual next action** — nothing else blocks it.

`railway.json` + `docs/DEPLOYMENT.md` §2b are kept in case Railway becomes
viable again later (a new account, a new trial) — don't delete them, but
don't default back to Railway without the user saying so. **If a future
session's Fly deploy behaves differently than described here** (Fly's
platform/CLI does evolve), trust `fly logs --app linchpin` and `fly status
--app linchpin` over this document's specifics.

### 3.2 [CLOSED, this session] Two follow-ups from the formula-injection fix (PR #92)

Both fixed and merged this session:

1. `webapp/app.py`'s `/jobs-output` deliverable-download route had zero
   authentication of its own. **Fixed in PR #95** — new
   `webapp/security.py::jobs_output_auth_middleware` gates it behind the
   existing `LINCHPIN_API_KEY`.
2. `src/powerbi_export.py::build_powerbi_dataset()` had the identical
   unfixed formula-injection vulnerability. **Fixed in PR #94** — `_write()`
   now sanitizes every cell via `defuse_formula()` before `to_csv()`.

No known follow-ups remain from PR #92's security review.

### 3.3 The rest of the monetization ladder (see [[linchpin-monetization-plan]] for full detail)

- **Phase B (trigger: >5 paying MCP clients, not before):** Stripe metered subscription billing, replacing manual key issuance.
- **Phase C (trigger: real agent-to-agent payment demand shows up, not before):** x402/pay-per-call. Real but volatile market as of this research (volume down ~77% from a Nov 2025 peak) — don't build this speculatively.
- **Odoo Apps Store packaging:** 5 proposed "agent" bundles regrouping the 34 tools by Odoo-fit (Demand & Inventory Planner flagship, Inventory Control, Procurement & Sourcing, Warehouse & Logistics, Finance & Pricing) + a non-Odoo "Strategy Suite". Corrected pricing after checking real comparables: **$99-299 one-time per Store listing** (NOT the $7-22k a naive hours-of-consulting calculation first produced — that mistake and the correction are recorded in memory, worth reading before quoting a client). Recurring revenue has to live on Linchpin's own webapp, not the Odoo Store (structurally one-time-sale only).
- **GEO** (Generative Engine Optimization — get surfaced in ChatGPT/Claude/Perplexity's own answers) and an **own portfolio site** (0% commission vs. Upwork's 10%/Fiverr's 20%) — both zero-build-cost, positioning/content work, not code.
- **Zero paying clients, zero real-Odoo validation today** — the audit's own read was that the Inventory/Demand-Planner slice (~82%) is genuinely sellable now; don't let more engine work substitute for actually finding the first client. A discounted/free founding-client pilot (in exchange for a case study) was the recommended way to get the first proof point before quoting full price.

### 3.4 Lower-urgency engine/code backlog (unchanged, still real, still not urgent)

Jobs-layer `_pick_column` duplication across ~19 files, two coexisting
deliverable-builder generations for inventory/pricing, unstyled generic deck
XLSX, only `POST /api/jobs` authenticated (besides the new `/mcp` gate — most
other endpoints like `/api/portfolio`/`/api/warehouse` still aren't), a few
engine nits (DEA NaN-on-failure, dead Incoterm branch, AutoETS season-length
heuristic). None of this blocks revenue work; see prior HANDOFF revisions in
git history for the full original list if it's ever needed.

---

## 4. How to run (conventions, updated)

- **Python 3.11+**, `.venv` is uv-managed (no pip): `uv pip install --python .venv/Scripts/python.exe <pkg>`.
- **Tests:** `PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/ -q`. **Lint** (matches CI): `ruff check src tests examples`. ASCII-only in console prints (Windows cp1252).
- **⚠️ CI's real dependency source is `requirements-dev.txt`, NOT `pyproject.toml` extras directly.** `requirements-dev.txt` is a hand-maintained mirror (`pip install -r requirements-dev.txt`, comment says "canonical source is pyproject.toml" but CI never reads pyproject's extras itself). **If you add a new optional dependency to `pyproject.toml`, you MUST also add it to `requirements-dev.txt` or CI silently can't import your new code.** Bit this session (`mcp`, `pytest-asyncio` were added to `pyproject.toml`'s extras first, forgotten in `requirements-dev.txt`, caught only by manually building a throwaway venv from `requirements-dev.txt` and running the suite before opening the PR — do that verification step for any future new dependency).
- **New: async tests.** `pytest-asyncio` is now a dependency (`asyncio_mode = "auto"` in `pyproject.toml`), needed for `tests/test_mcp_server.py`'s `async def test_*` functions (FastMCP's `list_tools()`/`call_tool()` are coroutines).
- **Workflow:** feature branch → draft PR → CI green (3.11/3.12/3.13) → `gh pr ready` if opened as draft (a PR still marked draft fails to merge with "Pull Request is still a draft") → squash-merge. Never push straight to `main`.
- **graphify:** `graphify update .` refreshes the **code** graph (AST-only, gitignored `graphify-out/`). The **books** graph lives in `knowledge/scm-books/` (committed, needs an LLM backend to rebuild).
- **New agent-tool recipe** (unchanged): `jobs/<x>_job.py` with a pandas-only `prepare()` → `run`/`verify`/`build_deck` → a `Tool` in `scm_agent/tools.py` → an `options` builder in `scm_agent/tool_options.py` → add its key to `tests/test_scm_agent.py::test_build_default_registry_tools`.
- **New MCP-tool recipe:** add a job_type string literal + a thin `@mcp.tool`-decorated wrapper in `webapp/mcp_server.py`'s `build_mcp_server()`, following the existing 8 as templates — reuses the shared `_run_analysis_tool_sync` bridge, no new plumbing needed. Only ever add tools that are genuinely read-only/no-writeback; that boundary is the whole trust model of this surface.

---

## 5. Gotchas / warnings (read before committing)

- **Worktree recipe (Windows):** `git worktree add -b <branch> C:/Users/<you>/Music/scm/.wt-<x> origin/main` → edit/test with the **main repo's** `.venv/Scripts/python.exe`, cwd = worktree, absolute `PYTHONPATH` → commit → push → `gh pr create --draft` → CI green → `gh pr ready` → `gh pr merge --squash --delete-branch`.
- **`gh pr merge --delete-branch` reliably fails to delete the LOCAL branch** if a worktree still references it — the remote merge still succeeds regardless; verify with `gh pr view N --json state,mergedAt`. Clean up after: `git worktree remove --force <path>`, `git branch -D <branch>`, `git push origin --delete <branch>` manually.
- **Parallel worktrees editing the same shared file (esp. `CHANGELOG.md`'s `[Unreleased]` section) WILL produce a real merge conflict** even when the underlying code changes don't overlap at all — happened twice this session across unrelated branches. `git merge origin/main` + manually dedupe the resulting duplicate section headers.
- **This also happens with plain SEQUENTIAL PRs in one session, not just parallel worktrees** — PRs #94/#95 (this session) both appended to the same `### Fixed` insertion point; #95 hit a real conflict on `gh pr merge` even though it was branched, developed, and pushed one at a time (no worktrees involved), simply because #94 had merged in the interim. Same fix: `git fetch origin && git merge origin/main`, keep both bullets (order doesn't matter), re-run the full suite, re-push, wait for the fresh CI run before merging.
- **This repo runs genuinely concurrent Claude Code sessions.** Re-fetch and re-read `HANDOFF.md` right before finalizing any PR, not just at session start — main moves fast, and another session's work (a different worktree, a different branch) can land while you're mid-task. This session alone saw two other sessions' PRs land unprompted (#89 idempotency race, #92 formula injection via a spawned task) — neither caused a conflict, but both were only discovered by checking, not assumed.
- **`spawn_task` chips the user starts run as fully separate sessions** — they show up as new worktrees (this session saw one under `.claude/worktrees/<name>`, a different convention than this repo's own `.wt-<x>` sibling-directory habit) and can merge to `main` without this session's direct involvement. Check `gh pr list`/`git log` for surprises before assuming a flagged follow-up is still unstarted.
- **Never read or surface PII** — some datasets (e.g. DataCo) carry customer PII; analysis is aggregate-only.
- **Don't paste secrets** into chat or commits. `LINCHPIN_APPROVAL_SECRET`, `LINCHPIN_API_KEY`, and now `data/mcp_keys.sqlite3` (hashed keys, but still real client identities — gitignored, never commit) are all real secrets/sensitive state.
- `.env`, `data/*.sqlite3`, `graphify-out/`, `deliverables/` are gitignored (the `data/*.sqlite3` entry is new this session — it was a real gap before: `CLAUDE.md` claimed all of `data/` was gitignored, only `data/kaggle/` actually was).
- **A cloud platform's persistent Volume mount hides whatever the image already had at that path** — mounting at `/app/data` (where the image ships small static sample CSVs) silently made those files disappear and crashed the app with `FileNotFoundError`. General lesson beyond Fly specifically: never mount a persistent volume at a path the image itself writes static files into; give persisted state its own dedicated path (`/data`, not `/app/data`).
- **Multi-worker memory cost is NOT amortized across `uvicorn --workers N`** for this app — the orchestrator, forecast cache, and L3 knowledge graph are all loaded independently per worker process (no shared-memory IPC), so RAM scales roughly linearly with worker count. A 512mb VM OOM-killed in a crash loop at `--workers 2`; stable indefinitely at `--workers 1`. Don't raise worker count on a memory-constrained host without proportionally raising memory too.
- **Local `docker build && docker run` testing a container standalone does NOT catch bugs that only manifest with a real persistent Volume attached** (the shadowed-sample-CSVs bug above only appeared once actually deployed on Fly with the Volume mounted — the local Docker smoke-test earlier the same session, with no volume, passed cleanly). Local Docker testing catches import/boot errors; it does NOT substitute for a real deploy when volumes/mounts are part of the config being tested.

---

## 6. Key files (updated)

- **Fly.io deployment — LIVE:** `Dockerfile`, `fly.toml` (app `linchpin`, region `iad`, `WEB_CONCURRENCY=1`, Volume mounted at `/data`), `.dockerignore`, `docs/DEPLOYMENT.md` §2a ("Quick path: Fly.io") — verified against the real running app at `https://linchpin.fly.dev`, not just locally.
- **Railway deployment (PR #96, now §2b alternative):** `railway.json` (build/start/healthcheck — `buildCommand` fixed this session to include the `mcp` extra), `docs/DEPLOYMENT.md` §2b.
- **MCP server:** `webapp/mcp_server.py` (the 8 tools + shared bridge), `webapp/mcp_auth.py` (identity-aware auth+rate-limit middleware), `src/mcp_keys.py` (`McpKeyStore`), `examples/issue_mcp_key.py` (operator CLI), `docs/MCP_SERVER.md` (full reference).
- **Security fixes:** `src/sanitize.py` (`defuse_formula()`), wired into `src/excel_export.py`, `jobs/deliverables.py`, `src/export.py`, and (PR #94) `src/powerbi_export.py`. `webapp/security.py::jobs_output_auth_middleware` (PR #95, gates `/jobs-output`) — mind the registration order in `webapp/app.py` (must come before `security_headers_middleware`).
- Writeback safety: `src/writeback.py`, `src/writeback_store.py` (`SqliteAuditLedger`, now with atomic `claim()`/`release()` for the idempotency race fix, PR #89).
- Odoo connector: `src/connectors/odoo.py` (`_ReorderRuleStore`, `_DraftPoStore` — partial-failure compensation + idempotency claiming, both now fixed).
- Agent routing/grounding: `scm_agent/registry.py`, `scm_agent/intent.py`, `scm_agent/knowledge.py`.
- Agent: `scm_agent/{orchestrator,registry,intent,knowledge,modes,tools,tool_options,guided_bridge,llm,types}.py`
- Deliverable: `src/deliverable.py`, `jobs/deliverables.py`, `jobs/*_deliverable.py`
- Engines: `src/*.py` (34 tools' worth — run `build_default_registry()`, don't trust a static list)
- Knowledge: `knowledge/scm-books/` (L3 books graph, committed, 24 sources), `graphify-out/` (code graph, gitignored)
- Tests: `tests/test_*.py` (1202 passing, includes new `test_mcp_*.py` files)
- Top-level docs: `README.md`, `scm_agent/README.md`, `docs/MCP_SERVER.md`, `docs/DEPLOYMENT.md`
