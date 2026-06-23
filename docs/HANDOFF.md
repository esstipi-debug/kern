# HANDOFF — resume here next session

**Last updated:** 2026-06-23 · **Status:** Fase 0/1 + pure cores (Fase 3/4) + voice-agent brain (Fase 5) shipped to `main`. Loop continues.

## TL;DR for the next session
This session built and **deployed to `main`** the foundation + a large slice of the [Capability Expansion Plan](../documentation/CAPABILITY_EXPANSION_PLAN.md): the Guided Execution Layer ("never-unprotected"), safe-staging writeback, and ~17 capability modules across planning, control, procurement, warehouse, finance, data quality and the **voice-agent brain**. Full suite **374 passing**, ruff clean. Five PRs merged: [#7](https://github.com/esstipi-debug/linchpin/pull/7) #8 #9 #10 #11.

**The user works in an autonomous "loop until done" mode and dislikes turns that end with permission questions.** Keep building module-by-module (TDD → commit → deploy), state boundaries as *facts* not questions. See `~/.claude/.../memory/autonomous-loop-no-asking.md`.

**Next action:** continue the loop with the next slices (see "Next steps" below) — start with the **VoiceCaller dry-run adapter** (closes the voice orchestration loop with no credentials).

## Where the repo is (current state, pushed to origin/main)
- **Engine** `src/` — original models (EOQ, safety stock, (s,Q)/(R,S), fill rate, newsvendor, multi-echelon, simulation, forecasting SES/Croston+σₑ, pricing, constraints, sources) **plus this session's new modules:**
  - `guided.py` (Guided Execution Layer — never-unprotected contract + builders) · `writeback.py` (M15 safe staging: dry-run→approval→idempotent→rollback)
  - `classification.py` (ABC-XYZ, M4) · `ddmrp.py` (M5) · `financial_kpis.py` (M13) · `alerting.py` (M14) · `reconciliation.py` (M6) · `space.py` (M7) · `forecast_metrics.py` (M2)
  - `landed_cost.py` · `supplier_scorecard.py` · `purchase_order.py` (M8) · `data_quality.py` + `sku_dedup.py` (M11)
  - `voice/` — **voice-agent brain (M16, credential-free):** `playbooks.py` (7 call scripts), `compliance.py` (dial gate), `doc_schemas.py` (12 doc field maps), `agent_config.py` (6-block system prompt + config), `knowledge_base/logistics_kb.md` (RAG corpus)
- **Agent** `scm_agent/` — orchestrator now attaches a protected `GuidedOutcome` to every `JobResult` via `guided_bridge.py`. `JobResult.guided` field added.
- **Tests:** **374 passing**, ruff clean, `src` coverage ~93% (gate 80). Demo runner: `examples/run_new_capabilities.py`.
- **Knowledge graph:** `graphify-out/` refreshed (1838 nodes, 105 communities labeled, semantic pass done). Gitignored/regenerable.

## How to work (critical conventions — read before touching anything)
- **venv is uv-managed and has NO pip.** Run tests with `./.venv/Scripts/python.exe -m pytest -q`. Install deps with `uv pip install --python .venv/Scripts/python.exe <pkg>` (rapidfuzz, python-stdnum, pymcdm already installed).
- **ASCII-only in CLI prints** (Windows cp1252 — em dashes render as `�`). Use `-` and `~`, not `—`/`≈`.
- **Deploy flow:** branch → `git add <only your files>` → commit (conventional, **no Co-Authored-By** — attribution disabled) → `git push -u` → `gh pr create` → `gh pr merge --squash --delete-branch`. Verify branch-isolated green by stashing the not-mine files (below) before pushing.
- **gitignored / never commit:** `data/kaggle/` (huge datasets), `graphify-out/`, `deliverables/`.
- **TDD always** (user's CLAUDE.md mandates it): write test → watch RED → implement → GREEN → ruff --fix.
- **Graph refresh after code changes:** `& "$(uv tool dir)/graphifyy/Scripts/graphify.exe" update .` (code-only, no LLM). Full semantic pass = the `/graphify` skill's subagent flow (already done this session).

## ⚠️ Uncommitted, NOT-mine changes left in the working tree (decide what to do)
These were already modified/untracked before this session and were deliberately **left untouched** (excluded from every commit): `jobs/intake.py`, `src/batch.py`, `tests/test_batch.py`, `tests/test_jobs.py`, and untracked `.impeccable/`, `scripts/`, `profile_sample_pricing.py`, `examples/run_benchmark.py`, `docs/superpowers/HANDOFF.md`. The 4 modified files add ~4 tests (full tree = 374; branch-isolated = 370). Next session: review and either commit them in their own PR or discard.

## Next steps (the loop — in order)
1. **VoiceCaller dry-run adapter** (`src/voice/caller.py`): `VoiceCaller` protocol + `DryRunCaller` (logs the call it *would* place) + `ElevenLabsCaller` stub. Then the `linchpin-voice-followup` orchestration: compliance gate → `build_agent_config` → place call → capture `CallOutcome` → sync. **Fully testable with DryRunCaller — no credentials.**
2. **Logistics doc-reader** (`src/voice/doc_reader.py`): wrap the existing optional `LLMProvider` (Claude PDF+citations when `ANTHROPIC_API_KEY` set; deterministic stub/fixture otherwise) → extract to `doc_schemas`. Testable with a stub.
3. **SKILL.md packaging:** the src modules exist but their `SKILL.md` wrappers (per the plan, vandeput-* convention in `.cursor/skills/`) are NOT written yet. Package the new capabilities as skills.
4. **Guided Execution Layer remaining helpers (§2.14):** `decision-options` engine over the analytic modules, `escalation-packet` builder, `coverage-gate` extending `jobs/qa.py`. (Core `guided.py` builders already exist.)
5. **Dep-gated forecasting (M2):** `uv pip install` the `forecast` extra (statsforecast/lightgbm — heavy, numba), then `forecast_auto/intermittent/probabilistic` wrappers with graceful fallback to the existing MA/SES/Croston.
6. **Live connectors (need the CLIENT's credentials):** Shopify Admin GraphQL, Amazon SP-API, ERP/accounting, carriers. Build the connector code + tests against `writeback.InMemoryStore`/mocks now; wire live when keys arrive.

## Boundaries (what genuinely needs the user)
- **Credentials:** live phone dialling (ElevenLabs ConvAI + Twilio), live Claude doc-reading (Anthropic key), and all live commerce/ERP/carrier connectors.
- **Heavy install decision:** the `forecast` extra (statsforecast/lightgbm).
Everything *up to* those seams is buildable and testable now.

## Key pointers
- Plan: [`documentation/CAPABILITY_EXPANSION_PLAN.md`](../documentation/CAPABILITY_EXPANSION_PLAN.md) (§2.13 voice, §2.14 guided layer, §5 roadmap).
- Compliance %: that doc's §4 table (today → with-plan → +guided-layer ≥91%).
- Graph: `graphify-out/GRAPH_REPORT.md` (god nodes, communities) · `graph.html`.
- Memory: `~/.claude/projects/C--Users-Gamer-Music-scm/memory/` — `linchpin-project.md`, `autonomous-loop-no-asking.md`.
