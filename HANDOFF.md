# Linchpin — Session Handoff

**Date:** 2026-06-23 · **Repo:** `esstipi-debug/linchpin` · **Branch:** `main` (HEAD `d296775`)
**Purpose:** pick up Linchpin work in a fresh session without re-deriving context.

> A new Claude Code session in this repo also auto-loads memory: `MEMORY.md` →
> [[linchpin-project]], [[linchpin-verified-audit]], [[scm-test-datasets]],
> [[autonomous-loop-no-asking]]. This file is the human-readable consolidation.

---

## 1. What Linchpin is

Agentic supply-chain AI: a deterministic Python engine (forecasting, EOQ, safety
stock, (s,Q)/(R,S), multi-echelon, DDMRP, ABC-XYZ, financial KPIs, supplier
scorecards, MCDM sourcing, landed cost, cost-to-serve, reconciliation, voice
doc-reader) + an orchestrator agent, grounded in an **L3 knowledge graph** (23
SCM books) and packaged through a **client-ready deliverable generator**. It
**calculates the decision, recommends ranked actions, and emits a cited,
auditable report (md + xlsx)** — and where it can't act itself, hands off a
ready-to-execute packet (the "never unprotected" Guided Execution Layer).
Positioned to win Upwork inventory + SCM gigs (human sells, Linchpin produces 10x).

---

## 2. Current state (verified)

- **Tests:** 534 passing, ~95% coverage (`.venv/Scripts/python.exe -m pytest`).
- **L3 graph** (`knowledge/scm-books/graph.json`): **1824 nodes / 3640 edges / 122 communities, 23 sources** (forecasting, pricing/revenue, SCM, inventory, manufacturing planning, operations mgmt, logistics, sustainability, leadership). Queried via `scm_agent/knowledge.py` (`search`/`explain`), cited by chapter.
- **Operating modes** (`scm_agent/modes.py`): `INVENTORY` (stock subset) vs `SCM` (superset, all tools) — each with persona + deliverable/KPI catalogue. `get_mode()`, `build_registry(mode)`, `orchestrator_for(mode)`.
- **Deliverable generator** (`src/deliverable.py` + `jobs/inventory_deliverable.py`): engine output → Markdown + XLSX with exec summary, quantified findings, KPI table w/ rationale, data-source map, L3 citations, coverage/handoff block.
- **S&OP/IBP cadence** (`src/sop.py` + `jobs/sop_deliverable.py`, gap #2, shipped PR #21): monthly demand→supply→reconciliation→exec workflow. Chase/level/hybrid aggregate-planning strategies → inventory-balance projection → cost/service/working-capital evaluation → `run_sop_cycle` emits a protected ranked OPTIONS outcome → the "S&OP/IBP deck" SCM mode advertises. Demo: `examples/run_sop_cycle.py`. Library + deliverable only — **not yet an agent tool**.
- **Agent surface caveat:** the orchestrator wires only **3 tools** (`inventory_optimization`, `pricing`, `leadership_chain`); the other ~15 SCM modules are tested library cores + CLI/skills, not yet agent tools. (See [[linchpin-verified-audit]].)

---

## 3. This session's shipped work (commits `ff31baf` → `d296775`, all on main, pushed)

| Commit | What |
|---|---|
| `ff31baf` | L3 → 22 books (+From Source to Sold leadership/CHAIN, +Vollmann/Ivanov/Christopher/Grant) + `modes.py` |
| `0f255d4` | deliverable generator (gap #1) |
| `468cc99` | deepened Chopra 32→312, added Heizer operations layer (→23 sources, 1824 nodes) |
| `bf2c316` | coverage tests (safety_stock 79→100%, simulation_opt 78→97%) |
| `57d0d63` | SCM test harness — Superstore |
| `4b58118` | SCM test harness — Olist (+ `scripts/fetch_olist.py`) |
| `2b3140d` | SCM test harness — Procurement KPI (5 competing suppliers) |
| `d296775` | SCM test harness — DataCo 180k (+ `scripts/fetch_dataco.py`) |

**Follow-up session (PR-based, CI-gated):**

| Commit / PR | What |
|---|---|
| `32ead63` (PR #21) | **Gap #2 — S&OP/IBP cadence engine + deck.** `src/sop.py`, `jobs/sop_deliverable.py`, `examples/run_sop_cycle.py`, 28 tests. Also cleared pre-existing ruff debt (`src/deliverable.py` unused `field`, `examples/run_scm_olist.py` dead vars) that CI surfaced. Tests green on py3.11/3.12/3.13. |

---

## 4. How to run (conventions)

- **Python 3.11**, `.venv` is uv-managed (no pip): `uv pip install --python .venv/Scripts/python.exe <pkg>`.
- **Tests:** `.venv/Scripts/python.exe -m pytest -q`. ASCII-only in console prints (Windows cp1252 — em dashes break it; markdown files written utf-8 are fine).
- **graphify:** `graphify update .` refreshes the **code** graph (AST-only, gitignored `graphify-out/`). The **books** graph lives in `knowledge/scm-books/` (committed). uv-tool graphify can break on Windows reparse points — reinstall with `uv tool install "graphifyy[kimi]"`; `pypdf` is required for PDF text extraction.
- **SCM test harnesses** (real data; `data/` is gitignored):
  - `examples/run_new_capabilities.py --data <canonical.csv>` — ABC-XYZ, DDMRP, KPIs, alerting, orchestrator
  - `examples/run_scm_superstore.py` · `run_scm_olist.py` · `run_scm_procurement.py` · `run_scm_dataco.py`
- **Kaggle:** token at `~/.kaggle/access_token` (KGAT); `kaggle`+`kagglehub` installed. Headless: `scripts/fetch_dataco.py`, `scripts/fetch_olist.py`, or `.venv/Scripts/kaggle.exe datasets download -d <slug> -p <dir> --unzip`.

**Local datasets** (gitignored, `data/kaggle/`): m5, online_retail, superstore, olist, **dataco (180k)**, procurement.

---

## 5. Next steps (research-backed roadmap, prioritized)

1. ~~**Gap #2 — S&OP/IBP cadence orchestration**~~ ✅ **DONE** (PR #21, `32ead63`). `src/sop.py` + `jobs/sop_deliverable.py` + `examples/run_sop_cycle.py`.
2. **Gap #3 — Cost-to-serve + working-capital/cash-release module** (CFO lens): allocate landed+fulfillment+returns to customer/SKU; cash-to-cash simulation. (Currently computed ad-hoc in the harnesses — promote to a `src/` module.) Natural next vertical: composes with the S&OP working-capital surface (`PlanEvaluation.peak_inventory` / `average_inventory`) and `financial_kpis.cash_to_cash`.
3. **Wire-up: register `run_sop_cycle` as the 4th orchestrator tool** (deferred from PR #21 to avoid coupling `prepare()` to the parallel loop's actively-changing `jobs/intake.py`). Do this once `intake.py` settles — gives the agent its first end-to-end SCM (non-inventory) deliverable and starts closing the 3-tools gap. Tool contract: `scm_agent/tools.py` (prepare/run/qa/deliver); qa via `guided.verify_guided(review.outcome)`; deliver via `jobs/sop_deliverable.build(...).write_all(...)`.
4. **Other wire-ups** (low-risk, additive): inject each mode's `persona` into `orchestrator._narrative`; call the deliverable generator from the agent's `deliver` path; register more `src/` modules as agent tools.
5. **Gap #5 — Live connectors** (Shopify → Amazon SP-API → ERP): the execution unlock; needs the client's API keys per engagement.
6. **Finish Ivanov L3 coverage** (currently ~70 nodes, partial — Kimi daily-token limit). Re-run when budget resets or via subagents.

---

## 6. Gotchas / warnings (read before committing)

- **Parallel autonomous loop is active on this same repo/main.** It owns and leaves uncommitted: `jobs/intake.py`, `src/batch.py`, `tests/test_batch.py`, `tests/test_jobs.py`. **Do NOT commit those** — stage only your own files. It also maintains `linchpin-project` memory + a deploy convention (branch → stash-not-mine → PR-merge-squash).
- **ROTATE two secrets** that landed in this session's transcript: the **Kaggle KGAT token** (`~/.kaggle/access_token`) and the **Kimi `MOONSHOT_API_KEY`** (`.env`). Both are gitignored/local but were pasted in chat.
- **Kimi backend limits are tight** (org concurrency 3, RPM 20, **TPD 1.5M**) — bulk L3 ingestion repeatedly 429'd. Reliable pattern: host-subagent extraction (no API key) for big books; Kimi only for small/medium with `max_concurrency` 1-2, `max_retry_depth=0`.
- **DataCo CSV contains customer PII** (email/name/password/street) — analysis is aggregate-only; never read or surface PII.
- `.env` and `data/` and `graphify-out/` and `deliverables/` are gitignored.

---

## 7. Key files

- Agent: `scm_agent/{orchestrator,registry,intent,knowledge,modes,tools,guided_bridge,llm,types}.py`
- Deliverable: `src/deliverable.py`, `jobs/inventory_deliverable.py`, `jobs/sop_deliverable.py`
- S&OP/IBP cadence: `src/sop.py` (engine + `run_sop_cycle`), `examples/run_sop_cycle.py`
- Engines: `src/*.py` (eoq, safety_stock, policies, forecasting, classification, ddmrp, financial_kpis, supplier_scorecard, mcdm, landed_cost, reconciliation, simulation_opt, guided, writeback, voice/*)
- Knowledge: `knowledge/scm-books/` (L3 books graph), `graphify-out/` (code graph, gitignored)
- Tests: `tests/test_*.py` (506) · Examples: `examples/run_*.py` · Plan: `documentation/CAPABILITY_EXPANSION_PLAN.md`
