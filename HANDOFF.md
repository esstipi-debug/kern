# Linchpin — Session Handoff

**Date:** 2026-06-23 · **Repo:** `esstipi-debug/linchpin` · **Branch:** `main` (this session shipped PRs #21–#34; a parallel loop merged #32/#35/#37/#38 — run `git log --oneline` for the live HEAD)
**Purpose:** pick up Linchpin work in a fresh session without re-deriving context.
**Resume here:** the agent-tool backlog (3→9 tools) and the offline connector subsystem are **done**; the only open work is externally blocked — see "Status for the next session" at the bottom (live SDK adapters need client API keys; Ivanov L3 needs Kimi tokens).

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

- **Tests:** 648 passing, ~95% coverage (`.venv/Scripts/python.exe -m pytest`).
- **L3 graph** (`knowledge/scm-books/graph.json`): **1824 nodes / 3640 edges / 122 communities, 23 sources** (forecasting, pricing/revenue, SCM, inventory, manufacturing planning, operations mgmt, logistics, sustainability, leadership). Queried via `scm_agent/knowledge.py` (`search`/`explain`), cited by chapter.
- **Operating modes** (`scm_agent/modes.py`): `INVENTORY` (stock subset) vs `SCM` (superset, all tools) — each with persona + deliverable/KPI catalogue. `get_mode()`, `build_registry(mode)`, `orchestrator_for(mode)`.
- **Deliverable generator** (`src/deliverable.py` + `jobs/inventory_deliverable.py`): engine output → Markdown + XLSX with exec summary, quantified findings, KPI table w/ rationale, data-source map, L3 citations, coverage/handoff block.
- **S&OP/IBP cadence** (`src/sop.py` + `jobs/sop_deliverable.py`, gap #2, shipped PR #21): monthly demand→supply→reconciliation→exec workflow. Chase/level/hybrid aggregate-planning strategies → inventory-balance projection → cost/service/working-capital evaluation → `run_sop_cycle` emits a protected ranked OPTIONS outcome → the "S&OP/IBP deck" SCM mode advertises. Demo: `examples/run_sop_cycle.py`. Library + deliverable only — **not yet an agent tool**.
- **Connector simulator** (`src/connectors/`, gap #5 offline unblock, PR #28): `InventorySource` protocol + canonical DTOs (`Product`/`InventoryLevel`/`Order`) + **`SimulatedStore`** — an in-memory storefront with a `demand_frame()` bridge into the engines and restock through the existing safe-staging writeback plane (`src/writeback.py`). The whole connector pipeline runs **offline, no API keys** (`examples/run_connector_sim.py`). Decision (2026-06-23): build connectors against a simulated space now; real Shopify/SP-API/ERP adapters implement the same protocol when keys arrive. **HTTP emulator added (PR #29):** `src/connectors/emulator.py` (FastAPI, commerce-shaped `/admin/*` endpoints over a `SimulatedStore`) + `src/connectors/http_client.py` (`StoreApiClient`, an `InventorySource` over any httpx-style client) — round-trips offline via `TestClient`, so the live clients are testable before keys. **Canonical ledger added (PR #30):** `src/connectors/ledger.py` (`CanonicalLedger` merges N `InventorySource` channels — simulated or HTTP — into one SKU-indexed view: union catalog, total + per-channel inventory, combined demand into the engines). **Replenishment loop added (PR #31):** `src/connectors/replenish.py` (`plan_replenishment` reads any source → forecasts → restock-to-cover → guided never-unprotected packet; stages a dry-run via the writeback plane when the store is writable) + `examples/run_replenishment.py`. **Connector subsystem is now complete & fully offline** (simulator → emulator/client → ledger → replenishment). Remaining is only the *live* SDK adapters (real Shopify/SP-API/ERP calls) — they implement `InventorySource` and need client keys.
- **Cost-to-serve + working capital** (`src/cost_to_serve.py` + `src/working_capital.py` + `jobs/cost_to_serve_deliverable.py`, gap #3, shipped PR #22): activity-based CTS (product/fulfillment/returns/overhead → net-to-serve margin + whale curve) and the cash-to-cash / cash-release lens. Works **without** a precomputed profit column. Composes `landed_cost` + `financial_kpis.cash_to_cash`. Demo: `examples/run_cost_to_serve.py`. Library + deliverable only — **not yet an agent tool**.
- **Agent surface:** the orchestrator now wires **9 tools** — `inventory_optimization`, `pricing`, `leadership_chain`, **`cost_to_serve`** (#24), **`sop`** (#25), **`abc_xyz`** (#26), **`sourcing`** (#27), **`ddmrp`** (#33), **`landed_cost`** (#34). (Was 3; the agent-tool backlog is complete.) The original "only 3 tools" audit caveat is **closed**; the new SCM/inventory deliverables are reachable end-to-end. The remaining ~15 `src/` modules are tested library cores + CLI/skills, not yet agent tools. The agent also **narrates in each mode's persona** and **emits the premium "artifacts that sell" deck** (KPI rationale + L3 citations + coverage/handoff) via the optional `Tool.deck` hook (PR #23), wired for `inventory_optimization` / `cost_to_serve` / `sop`. New-tool recipe: a `jobs/<x>_job.py` with a `prepare()` that reads its own CSV (pandas, **not** `intake.py`), `run`/`verify`, then a `Tool` in `tools.py` with distinctive multi-word `intent_keywords`.

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
| `ee156ea` (PR #22) | **Gap #3 — cost-to-serve + working-capital module.** `src/cost_to_serve.py`, `src/working_capital.py`, `jobs/cost_to_serve_deliverable.py`, `examples/run_cost_to_serve.py`, 24 tests. Purely additive. Green on py3.11/3.12/3.13. |
| `c6a702e` (PR #23) | **Item 4 — orchestrator wire-ups (unblocked half).** Mode persona threaded into `_narrative`; optional `Tool.deck` hook emits the premium deck alongside operational files (wired for `inventory_optimization`). `scm_agent/{orchestrator,modes,registry,tools}.py` + 8 tests. Backward-compatible. |
| `0f7180f` (PR #24) | **Item 3 — cost-to-serve as the 4th agent tool.** `jobs/cost_to_serve_job.py` (order-line CSV -> segment activity, pandas only, no `intake`) + `cost_to_serve_tool`. 10 tests incl. routing + end-to-end. |
| `a837b30` (PR #25) | **Item 3 — S&OP as the 5th agent tool.** `jobs/sop_job.py` (demand history -> monthly horizon) + `sop_tool`. 7 tests. **3-tools audit caveat closed.** |
| `09aa50e` (PR #26) | **ABC-XYZ as the 6th agent tool** (autonomous loop). `jobs/abc_xyz_job.py` (per-SKU history -> 9-cell matrix, inline deck) + `abc_xyz_tool`. 9 tests. |
| `d28956d` (PR #27) | **Sourcing/MCDM as the 7th agent tool** (loop). `jobs/sourcing_job.py` (delivery records -> OTIF scorecards -> TOPSIS award, inline deck) + `sourcing_tool`. 8 tests. |
| `df00e07` (PR #28) | **Offline connector simulator** (Gap #5 unblock). `src/connectors/` (`InventorySource` + `SimulatedStore` + demand bridge + safe-staging restock) + `examples/run_connector_sim.py`. 8 tests. Whole connector pipeline runs with no API keys. |
| `8031bd8` (PR #29) | **Offline HTTP store emulator** (loop). `src/connectors/emulator.py` (FastAPI commerce endpoints) + `http_client.py` (`StoreApiClient`). 6 tests. Real HTTP client testable in-process via TestClient. |
| `ddc6075` (PR #30) | **Canonical multichannel ledger** (loop). `src/connectors/ledger.py` — merges N channels (simulated/HTTP) into one SKU-indexed inventory+demand view. 6 tests. |
| `69f945d` (PR #31) | **Connector-backed replenishment loop** (loop). `src/connectors/replenish.py` — read → forecast → restock-to-cover → guided staged packet (+ example). 5 tests. Connector subsystem complete & offline. |
| `d42bee5` (PR #33) | **DDMRP as the 8th agent tool** (loop). `jobs/ddmrp_job.py` (parts CSV → red/yellow/green buffers + net-flow signal, inline deck) + `ddmrp_tool`. 8 tests. (#32 was the parallel loop's legibility/security PR.) |
| `4d7ebcc` (PR #34) | **landed_cost as the 9th agent tool** (loop). `jobs/landed_cost_job.py` (shipment CSV → Incoterm-aware landed cost, inline deck) + `landed_cost_tool`. 8 tests. **Agent-tool backlog complete (3→9 tools).** |

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
2. ~~**Gap #3 — Cost-to-serve + working-capital/cash-release module**~~ ✅ **DONE** (PR #22, `ee156ea`). `src/cost_to_serve.py` + `src/working_capital.py` + `jobs/cost_to_serve_deliverable.py` + `examples/run_cost_to_serve.py`.
3. ~~**Wire-ups: register `run_sop_cycle` + cost-to-serve as orchestrator tools**~~ ✅ **DONE** (PRs #24, #25). Decoupled from `intake.py` by giving each tool its own pandas `prepare()`. The 3-tools audit caveat is closed (7 tools now). **Autonomous loop in progress** registering the rest, same recipe — done: `abc_xyz` (#26), `sourcing` (#27); queued: `ddmrp`, `landed_cost`.
4. ~~**Other wire-ups** (persona into `_narrative`; deliverable generator in the `deliver` path)~~ ✅ **DONE** (PR #23, `c6a702e`). `Tool.deck` hook + per-mode persona.
5. **Gap #5 — connectors:** ✅ **offline subsystem COMPLETE** (PRs #28-#31 — simulator + HTTP emulator/client + canonical ledger + replenishment execution loop, all no-key). The full read→decide→act loop runs offline. Only the *live* SDK adapters (real Shopify/SP-API/ERP calls) still need client API keys; each just implements `InventorySource`.
6. **Finish Ivanov L3 coverage** (~70 nodes, partial): **blocked** — Kimi daily-token limit. Re-run when budget resets or via host-subagent extraction.

> **Status for the next session — autonomous loop COMPLETE:** this session shipped, all squash-merged to main, all CI-green on py3.11/3.12/3.13: Gaps #2 & #3 (libraries + decks), orchestrator wire-ups (persona + `Tool.deck`), the **agent-tool backlog 3→9 tools** (+cost_to_serve/sop/abc_xyz/sourcing/ddmrp/landed_cost), and the **complete offline connector subsystem** (#28 simulator → #29 emulator+client → #30 canonical ledger → #31 replenishment execution loop). 506→648 tests. Everything offline; the parallel loop's `intake.py`/`batch.py`/`test_{batch,jobs}.py` never touched. **The only remaining work is externally blocked:** (a) the *live* SDK adapters (real Shopify/SP-API/ERP/accounting/carrier calls) — each just implements `InventorySource` / writes through `src/writeback.py`, and needs client API keys; (b) Ivanov L3 completion — needs Kimi token budget (or the no-key host-subagent extraction path). Next internal options if desired: register more `src/` modules as tools (space/slotting, financial_kpis dashboard, alerting, reconciliation/cycle-count), or the scenario/what-if engine (audit roadmap #4).

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
- Deliverable: `src/deliverable.py`, `jobs/inventory_deliverable.py`, `jobs/sop_deliverable.py`, `jobs/cost_to_serve_deliverable.py`
- S&OP/IBP cadence: `src/sop.py` (engine + `run_sop_cycle`), `examples/run_sop_cycle.py`
- Cost-to-serve / working capital: `src/cost_to_serve.py`, `src/working_capital.py`, `examples/run_cost_to_serve.py`
- Agent-tool data-prep (decoupled, pandas-only): `jobs/{cost_to_serve,sop,abc_xyz,sourcing}_job.py`; tools wired in `scm_agent/tools.py`
- Connectors (offline): `src/connectors/__init__.py` (protocol+DTOs), `src/connectors/simulator.py` (`SimulatedStore`), `examples/run_connector_sim.py`; write side reuses `src/writeback.py`
- Engines: `src/*.py` (eoq, safety_stock, policies, forecasting, classification, ddmrp, financial_kpis, supplier_scorecard, mcdm, landed_cost, reconciliation, simulation_opt, guided, writeback, voice/*)
- Knowledge: `knowledge/scm-books/` (L3 books graph), `graphify-out/` (code graph, gitignored)
- Tests: `tests/test_*.py` (506) · Examples: `examples/run_*.py` · Plan: `documentation/CAPABILITY_EXPANSION_PLAN.md`
