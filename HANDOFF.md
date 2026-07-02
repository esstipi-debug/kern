# Linchpin — Session Handoff

**Date:** 2026-07-02 · **Repo:** `esstipi-debug/linchpin` · **Branch:** `main` @ `aea7c7a` (PRs up to **#87**)
**Purpose:** pick up Linchpin work in a fresh session without re-deriving context.
**Resume here:** the world-class audit (10 sections, multi-agent + adversarial verification, scored ~6.25/10) is now **fully closed out**. All P0 (trust/safety) + P1 (engine correctness) findings from the original pass (PR #82, #83) plus all three items flagged as still-outstanding in the previous handoff are fixed and merged: odoo.py partial-failure rollback (**PR #86**), the `safety_stock()` sign-flip test gap (**PR #85**), and the stale 3-4-capability README/docs (**PR #87**). A **separate, currently-running session** has also just committed a fix for the writeback idempotency check-then-act race (commit `ee65bcc` on branch `fix/writeback-idempotency-race`, in worktree `../.wt-idempotency-race`) but has **not yet opened a PR** — don't duplicate it; check `gh pr list --head fix/writeback-idempotency-race` and that worktree's `git log`/`git status` before starting related work.

> A new Claude Code session in this repo also auto-loads memory: `MEMORY.md` →
> [[linchpin-project]], [[linchpin-verified-audit]], [[linchpin-coverage-roadmap]],
> [[linchpin-audit-fixes-2026-07]] (the audit + all fixes, in full detail).
> This file is the human-readable, in-repo consolidation — memory has the play-by-play.

---

## 1. What Linchpin is

Agentic supply-chain AI: a deterministic Python engine (EOQ, safety stock,
(s,Q)/(R,S), multi-echelon GSM, DDMRP, ABC-XYZ, newsvendor, queuing, scheduling,
facility location, DRP, transportation, FEFO, financial KPIs, supplier
scorecards, MCDM sourcing, landed cost, cost-to-serve, S&OP, reverse logistics,
warehouse layout, voice doc-reader) + an orchestrator agent, grounded in an
**L3 knowledge graph** (24 SCM books/sources) and packaged through a
**client-ready deliverable generator** (md + xlsx, cited). Where it can't act
itself, it hands off a ready-to-execute packet (the "never unprotected" Guided
Execution Layer, `src/guided.py`). Live **Odoo ERP connector** (`src/connectors/odoo.py`)
reads/writes through the safe-staging plane (`src/writeback.py`).
Positioned to win Upwork inventory + SCM gigs (human sells, Linchpin produces 10x)
— see [[linchpin-project]] for the current go-to-market thread (Upwork Project
Catalog packaging, Odoo Apps Store, MCP-server-with-paywall exploration).

---

## 2. Current state (verified 2026-07-02, after PRs #85-#87)

- **Tests:** 1131 passing, 13 skipped (`PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/ -q`). `ruff check src tests examples` clean.
- **Agent surface: 34 tools** (verify with `build_default_registry()` — do not trust any doc's number, including this one, without re-running that check). README.md and `scm_agent/README.md` now list all 34 by area and both point at the registry as the source of truth, so this should stay honest longer than the old hardcoded "3-4 capabilities" framing did.
- **L3 graph** (`knowledge/scm-books/graph.json`): ~1847 nodes, **24 distinct sources** (verified directly from the committed graph's `source_file` field, not from prose — the book-count claims across README/CLAUDE.md had drifted to 17/23 and are now both fixed to 24).
- **Writeback safety plane** (`src/writeback.py`): unchanged this session — `Approval` HMAC-signed, `SqliteAuditLedger` for persistent audit/idempotency. **New this session:** `src/connectors/odoo.py`'s `_ReorderRuleStore.commit()` and `_DraftPoStore.commit()` now roll back (compensate) whatever was already written to Odoo if a later write in the same `commit()` call raises, instead of leaving partial writes live with no audit trail (PR #86). Known residual: a failure *during* the compensation itself is not recoverable by this local-only approach and needs manual reconciliation — documented in a code comment, not fixed further (would need a real distributed transaction).
- **`safety_stock()`** now has a regression test pinned to Vandeput's Table 4.1 worked example, checked through the exact signed value (PR #85) — closes the audit's mutation-testing finding that the module's own tests didn't catch a sign flip in the core formula.
- **Odoo connector** (`src/connectors/odoo.py`): read + both write paths (reorder points, draft POs) route through the writeback plane; bounded timeout + retry-with-backoff for read-only ORM methods. **Still needs**: validation against a REAL Odoo instance (user has none yet — do not treat this as urgent unless they say they have one).
- **README.md / scm_agent/README.md**: fixed this session (PR #87) — now describe all 34 tools by area, source-of-truth pointer to `build_default_registry()`, and corrected several other drifted numbers (books 17→24, tests 600+→1100+, KB concept nodes 430→~1850).

---

## 3. Immediate next steps, in priority order

### Not fixed — the only known HIGH-severity item left

1. **[in progress by another session, do not duplicate] Writeback idempotency check-then-act race.** Branch `fix/writeback-idempotency-race`, worktree `../.wt-idempotency-race` (relative to this repo's parent dir), latest commit `ee65bcc "fix(safety): close writeback idempotency check-then-act race"`, not yet PR'd as of this handoff. Check `git log`/`git status` in that worktree and `gh pr list --head fix/writeback-idempotency-race` before touching `src/writeback.py`, `src/writeback_store.py`, or `src/connectors/odoo.py`'s claim/release methods — if that session has stalled (no new commits, no PR, for a long time), it's fine to pick up where it left off rather than re-deriving from scratch: read its diff first (`git diff main` in that worktree).

### Backlog — real but lower urgency (full detail in [[linchpin-audit-fixes-2026-07]] and the original audit transcript if still available)

- **Jobs layer:** `_pick_column`/column-sniffing boilerplate duplicated verbatim across ~19 `jobs/*_job.py` files (extract a shared helper); two coexisting deliverable-builder generations for inventory/pricing with a visible drift artifact; generic deck XLSX is unstyled/chartless (below the "client-grade" bar the project claims); deck `confidence` values are hardcoded constants in some tools, not computed.
- **Webapp:** only `POST /api/jobs` is authenticated/rate-limited; other compute-doing endpoints (`/api/portfolio`, `/api/warehouse`, etc.) are not. `/console` prototype is unusable once the recommended production auth is on. No app-level body-size limit (proxy-only).
- **Test suite:** CI coverage gate excludes the orchestrator/jobs/webapp layers (engine-only); `jobs/qa.py` (the QA gate itself) is the least-covered core module at 76%.
- **Engine nits:** `DEA` silently emits NaN on LP solver failure instead of raising; an invalid Incoterm string is unreachable dead code in `landed_cost.py`; `AutoETS`'s default season length is `min(52, n_periods//2)` (arbitrary, should derive from frequency); Croston's error-stat initialization leaks a future value into pre-first-demand periods; the inverse-normal-loss polynomial (`fill_rate.py`) is unguarded below ~5e-4 targets.
- **Docs:** `documentation/CAPABILITY_EXPANSION_PLAN.md`'s "Hoy" (today) coverage table is a snapshot frozen at 22 jun 2026 and was NOT re-certified this session (would require re-deriving the audit's original scoring methodology) — a dated note was added pointing at the registry instead of fabricating new percentages. `documentation/GRAPH_LESSONS.md` and other docs may have similarly drifted numbers — spot-check before trusting any specific number in prose that isn't the registry or the graph file itself.

### Not code — the product/business gap (see [[linchpin-project]] for the live thread)

The audit's product-value section (5/10) found: zero real-world validation (every case study runs on public sample data), the Odoo connector has never touched a real Odoo instance, and there's no commercial shell (accounts, persistence, multi-tenant). None of this is fixable by writing more engine code — it needs real pilot clients. This is exactly what the in-progress Upwork/Contra/Odoo-Apps-Store go-to-market conversation is for; see [[linchpin-project]] memory for where that stands. Don't let "the code isn't done" block starting outreach — per the audit's own read, the Inventory/Demand-Planner slice (~82%) is genuinely sellable today.

---

## 4. How to run (conventions)

- **Python 3.11+**, `.venv` is uv-managed (no pip): `uv pip install --python .venv/Scripts/python.exe <pkg>`.
- **Tests:** `PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/ -q`. **Lint** (matches CI): `ruff check src tests examples`. ASCII-only in console prints (Windows cp1252 — em dashes break it; markdown files written utf-8 are fine).
- **Workflow:** feature branch → draft PR → CI green (3.11/3.12/3.13) → squash-merge. Never push straight to `main`.
- **graphify:** `graphify update .` refreshes the **code** graph (AST-only, gitignored `graphify-out/`). The **books** graph lives in `knowledge/scm-books/` (committed, needs an LLM backend to rebuild).
- **New agent-tool recipe** (unchanged, still the pattern): `jobs/<x>_job.py` with a pandas-only `prepare()` (reads its own CSV, not `intake.py`) → `run`/`verify`/`build_deck` → a `Tool` in `scm_agent/tools.py` with distinctive multi-word `intent_keywords` → an `options` builder in `scm_agent/tool_options.py` (a system-wide invariant test asserts every tool has one) → add its key to `tests/test_scm_agent.py::test_build_default_registry_tools`.

---

## 5. Gotchas / warnings (read before committing)

- **Worktree recipe that works reliably on this repo (Windows):** `git worktree add -b <branch> C:/Users/<you>/Music/scm/.wt-<x> origin/main` → edit/test with the **main repo's** `.venv/Scripts/python.exe`, cwd = worktree, `PYTHONPATH=<absolute worktree path>` (a *relative* `PYTHONPATH=.` silently breaks if your shell's cwd didn't actually follow you into the new worktree — always double check `pwd` after creating one) → commit → push → `gh pr create --draft` → wait for CI → `gh pr ready` (drafts can't be merged directly — `gh pr merge` on a draft errors with "Pull Request is still a draft") → `gh pr merge --squash --delete-branch`.
- **`gh pr merge --delete-branch` reliably fails to delete the LOCAL branch** if a worktree still references it ("cannot delete branch ... used by worktree") — **the remote merge still succeeds regardless**; verify with `gh pr view N --json state,mergedAt`, don't assume failure. Clean up after: `git worktree remove --force <path>` (usually fine on this repo now; if it ever hits Windows `Permission denied`, fall back to PowerShell `Remove-Item -Recurse -Force`, then `git worktree prune`), then `git branch -D <branch>` + `git push origin --delete <branch>` manually since the aborted local delete also skips the remote delete.
- **Multiple parallel branches editing the same file WILL conflict at merge time, even when the underlying code changes don't overlap.** This session ran 3 parallel worktrees; two of them both appended a new `### Fixed` section to `CHANGELOG.md`'s `[Unreleased]` block at the same insertion point, so the second PR to merge got a real (not fake) merge conflict. Fix was easy (`git merge origin/main`, git auto-merged everything except CHANGELOG.md, manually deduplicate the resulting triple `### Fixed` headers into one) but budget time for it when running parallel worktrees that all touch a shared file like CHANGELOG.md or a shared registry/routing file.
- **No live parallel autonomous loop was detected in the jobs/intake layer as of 2026-07-02** (checked: `jobs/intake.py`, `src/batch.py`, `tests/test_batch.py`, `tests/test_jobs.py`). There IS a live parallel session in the writeback/odoo layer right now (see §3 item 1) — re-check `git worktree list` and `git status` in any sibling `.wt-*` directories yourself before assuming either way, since this changes session to session.
- **Never read or surface PII** — some datasets (e.g. DataCo) carry customer PII; analysis is aggregate-only.
- **Don't paste secrets** into chat or commits. `LINCHPIN_APPROVAL_SECRET` (signs writeback approvals) joins `LINCHPIN_API_KEY` as a real secret — see `.env.example`/`SECURITY.md`.
- `.env`, `data/`, `graphify-out/`, `deliverables/` are gitignored.

---

## 6. Key files (updated)

- Writeback safety: `src/writeback.py` (`AuditBookkeeping`, `Approval` w/ HMAC signature, `ABSENT` sentinel), `src/writeback_store.py` (`SqliteAuditLedger`).
- Odoo connector: `src/connectors/odoo.py` (`OdooClient` w/ timeout+retry, `_ReorderRuleStore`, `_DraftPoStore` — **both now have partial-failure compensation in `commit()`, via shared `_apply_restore`/`_unlink_all` helpers, PR #86**), `jobs/odoo_job.py`.
- Agent routing/grounding: `scm_agent/registry.py` (`_keyword_matches`, word-boundary + plural tolerance), `scm_agent/intent.py` (LLM-failure fallback), `scm_agent/knowledge.py` (IDF-weighted `ground_citations`, domain-gated `advise`).
- Engine math (fixed in earlier session): `src/eoq.py` (`compute_eoq_volume_discount`), `src/multi_echelon.py` (`simulate_serial_gsm`), `src/newsvendor.py` (both optimizers), `src/pricing.py` + `jobs/pricing.py` (`confident` logic).
- Engine test coverage: `src/safety_stock.py` / `tests/test_safety_stock.py` — **now has an exact-signed-value regression test anchored to Vandeput Table 4.1, PR #85**.
- Agent: `scm_agent/{orchestrator,registry,intent,knowledge,modes,tools,tool_options,guided_bridge,llm,types}.py`
- Deliverable: `src/deliverable.py`, `jobs/deliverables.py`, `jobs/*_deliverable.py`
- Engines: `src/*.py` (34 tools' worth — run `ls src/` or `build_default_registry()`, don't trust a static list in any doc, including this one)
- Knowledge: `knowledge/scm-books/` (L3 books graph, committed, 24 sources), `graphify-out/` (code graph, gitignored)
- Tests: `tests/test_*.py` (1131 passing) · Plan: `documentation/CAPABILITY_EXPANSION_PLAN.md` (has a dated staleness note in its "Hoy" table, see §3)
- Top-level docs: `README.md`, `scm_agent/README.md` — **both rewritten this session (PR #87) to list all 34 tools by area instead of the old 3-4-capability framing.**
