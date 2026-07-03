# Linchpin — Session Handoff

**Date:** 2026-07-02 · **Repo:** `esstipi-debug/linchpin` (now **private**) · **Branch:** `main` @ `79cc536` (PRs up to **#96**)
**Purpose:** pick up Linchpin work in a fresh session without re-deriving context.
**Resume here:** both §3.2 follow-up security gaps are now closed (PRs #94, #95), and the Railway deployment groundwork for §3.1 is prepared and merged (PR #96 — `railway.json` + a `docs/DEPLOYMENT.md` "Quick path: Railway" section). **What's NOT done yet: nobody has actually run `railway login`/`init`/`up` — that needs the user's own interactive browser session (the Railway CLI is installed locally but its OAuth token had expired; a non-interactive session cannot complete that flow).** So the very next step is still "get a live public URL", just with the busywork (build/start commands, health check, the Volume-mount + env-var checklist) already written down. The user's stated objective for this whole project, verbatim: *"el objetivo de este agente es generar dinero"* — default to whatever advances revenue over further engine/backlog polish unless told otherwise.

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
- **Railway deployment groundwork (PR #96, this session, NOT yet executed):** `railway.json` (build/start commands, `/api/health` healthcheck) + a new `docs/DEPLOYMENT.md` "2a. Quick path: Railway" section + `.env.example` documents the previously-undocumented `LINCHPIN_MCP_KEYS_PATH`. This is prep only — **no `railway login`/`init`/`up` has actually been run**, so there is still no live public URL. The Railway CLI is installed on the user's machine but its OAuth token had expired (`railway whoami` fails); re-auth needs the user's own interactive browser session, which a non-interactive Claude Code session cannot do. See §3.1 for the exact remaining steps.
- **README.md / docs:** already refreshed this cycle (PR #87, #90) — 34-tool capability tables, book/author attribution removed, MIT license section removed from the README text (the actual `LICENSE` file at the repo root was intentionally left untouched — a separate decision the user hasn't made yet).
- **Odoo connector:** unchanged — still needs validation against a REAL Odoo instance (user has none yet; don't treat as urgent unless they say otherwise).

---

## 3. Immediate next steps, in priority order

### 3.1 [explicitly requested by the user] Publish the MCP server on free directories

This is the cheapest, highest-leverage next move — no billing complexity, no
new code, pure distribution. **The prerequisite (no public deployment yet) is
now half-solved**: PR #96 (this session) added `railway.json` + a
`docs/DEPLOYMENT.md` "2a. Quick path: Railway" section, and the user chose
**Railway** as the host (no prior preference was recorded before this
session). What's left is the part only the user can do:

1. **Run the actual deploy** — this needs the user's own interactive
   terminal, not a Claude Code session:
   ```bash
   railway login                 # opens a browser
   railway init                  # or `railway link` if a project exists
   railway up
   ```
   Then in the dashboard: add a **Volume at `/app/data`** (so
   `data/mcp_keys.sqlite3` + `data/writeback_ledger.sqlite3` survive a
   redeploy — Railway's filesystem is otherwise ephemeral), set the env vars
   from `docs/DEPLOYMENT.md` §1 (generate real `LINCHPIN_API_KEY` /
   `LINCHPIN_APPROVAL_SECRET` — don't reuse examples from the doc), and
   generate a public domain (Settings → Networking). Full steps in
   `docs/DEPLOYMENT.md` §2a.
2. **Issue an MCP key against the deployed instance** —
   `railway run python examples/issue_mcp_key.py issue "<client name>"` (runs
   inside the deployed environment, against the mounted Volume).
3. **Register with the official MCP registry**, then list/claim on the
   directories researched this session (see [[linchpin-monetization-plan]] §5
   for the real 2026 numbers): **Glama** (~37-51k servers indexed), **Smithery**
   (~7k+, app-store UI, hosted remote servers), **PulseMCP** (~12-18k+,
   hand-reviewed) — these are discovery storefronts that read off the official
   registry and let an owner claim their listing; check each one's actual
   current submission process before assuming it's identical across all three,
   it wasn't independently verified step-by-step this session, only that they
   exist and are free.

Zero cost either way, but don't report this as "done" until there's an actual
live URL a directory (or a client) can hit — an unauthenticated `localhost`
server obviously isn't listable. **If a future session finds `railway whoami`
still failing** (stale/expired OAuth token, confirmed the state as of this
session), that's the signal the user hasn't done step 1 yet — don't attempt
to work around it (no non-interactive re-auth path exists), just state it as
the blocker and ask the user to run `railway login` themselves.

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

---

## 6. Key files (updated)

- **Railway deployment (new, PR #96):** `railway.json` (build/start/healthcheck), `docs/DEPLOYMENT.md` §2a ("Quick path: Railway").
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
