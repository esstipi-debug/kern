# Changelog

## [Unreleased]

### Added
- **Client-ready deliverable generator (`src/deliverable.py` + `jobs/inventory_deliverable.py`)** — turns engine output into sellable *documents* (the top hireability gap: "consultants get paid for documents"). Produces a sectioned Markdown report + multi-sheet XLSX with an executive summary, quantified findings, a KPI table with *selection rationale*, a **data-source map** (every figure traceable to its origin + refresh cadence), L3 citations, and a **coverage & handoff** block (confidence + residual human action, honoring the never-unprotected contract). Engine-agnostic — `from_result` composes from any orchestrator `JobResult`; the inventory-optimization adapter turns SKU recommendations + portfolio numbers into the deliverable. Pure/deterministic (prepared date injected, not read from the clock). +11 tests.
- **Operating modes — Inventory vs SCM (`scm_agent/modes.py`)** — two role profiles over one engine. `INVENTORY` mode is the stock-centric subset (reorder points, safety stock, ABC-XYZ, reconciliation, inventory reporting); `SCM` mode is the end-to-end superset (+ S&OP, sourcing, landed cost, logistics, cost-to-serve, risk, sustainability, leadership) and exposes every registered tool. Each mode carries its tool surface, persona, and deliverable/KPI catalogue; `build_registry(mode)` / `orchestrator_for(mode)` scope the orchestrator with no edits to tools. SCM is the default — it never narrows capability unless Inventory is explicitly requested.
- **Operations / strategy / sustainability L3 layer** — ingested four authoritative SCM textbooks into the books knowledge graph (`knowledge/scm-books/`): Vollmann *Manufacturing Planning & Control* (S&OP/MPS/MRP/DRP — the planning/manufacturing spine, 149 nodes), Ivanov *Global Supply Chain & Operations* (sourcing/network/risk/digital, 70 nodes), Christopher *Logistics & SCM* (agility/lead-time/network/3PL, 247 nodes), Grant, Trautrims & Wong *Sustainable Logistics & SCM* (green/reverse logistics/circular economy, 177 nodes), a deepened **Chopra & Meindl** (32 → 312 nodes: strategy, network design, sourcing, transportation, coordination, revenue management), and **Heizer, Render & Munson** *Operations Management* (161 nodes: quality/SPC & Six Sigma, process & layout design, facility location, project management/PERT-CPM, QFD, capacity/TOC, decision analysis). The books graph grew 430 → 1824 nodes / 3640 edges / 122 communities across **23 sources**. Vollmann/Ivanov via the Kimi backend; Christopher/Grant/Chopra/Heizer via host subagents (no API key) — single canonical graph, concepts merged by label into cross-book bridges.
- **Guided Execution Layer (`src/guided.py`)** — the "never leave the user unprotected" contract. A `GuidedOutcome` is either `EXECUTED` or carries an executable path: ranked `ExecutionOption`s, a prepared `HandoffPacket`, or an `EscalationPacket`. `verify_guided()` is a QA gate (same shape as `jobs/qa.py`) that flags any non-executed result with no path as `unprotected`, and any residual without a stated risk. Builders (`as_executed`/`as_options`/`as_handoff`/`as_escalation`) make outcomes protected by construction.
- **Safe-staging writeback (`src/writeback.py`)** — the agent never mutates a system of record directly: `stage()` computes a dry-run `Changeset` (field-level before/after) without writing; risk tiers (read / reversible / irreversible) decide whether a time-boxed `Approval` is required; `apply()` is idempotent on the changeset key; every applied change is audited and `rollback()`-able. Ships an `InMemoryStore` reference connector.
- **Orchestrator guarantee** — `Orchestrator.run()` now attaches a protected `GuidedOutcome` to every `JobResult` at a single boundary (`scm_agent/guided_bridge.py`): ok→executed, needs_clarification→options, needs_data→handoff, qa_failed/error→escalation. New field `JobResult.guided`.
- **ABC-XYZ classification (`src/classification.py`)** — Pareto importance + CV predictability into a 9-cell matrix that assigns a default review policy, cycle-service-level target and buffer distribution per SKU (capability M4).
- **Inventory financial KPIs (`src/financial_kpis.py`)** — inventory turns, DIO, GMROI, sell-through, weeks of supply, inventory-to-sales, cash-to-cash, stockout rate (capability M13 / §4.5).
- **DDMRP buffers + net-flow planning (`src/ddmrp.py`)** — red/yellow/green zones, ADU, TOR/TOY/TOG, net-flow position and planning priority (capability M5).
- **Inventory event detection (`src/alerting.py`)** — stockout-risk / reorder-due / excess / dead-stock detection over a SKU snapshot, surfaced through the Guided Execution Layer as an executable handoff (capability M14, pure core).
- **Forecast accuracy metrics (`src/forecast_metrics.py`)** — MAE/RMSE/bias/MAPE/WAPE/MASE/RMSSE, dependency-free (capability M2).
- **Warehouse space + COI slotting (`src/space.py`)** — cube/m3 sizing, utilization, and class-based slotting by Cube-per-Order Index (capability M7).
- **Inventory reconciliation / IRA (`src/reconciliation.py`)** — system-vs-physical variance, record accuracy, dollar impact, ABC-tiered cycle-count plan (capability M6).
- **Landed-cost / TCO engine (`src/landed_cost.py`)** — Incoterm-aware duty base + freight/insurance/handling/broker, per-unit landed cost (capability M8).
- **Supplier scorecards (`src/supplier_scorecard.py`)** — OTIF/DIFOT, on-time/in-full rates, avg lead time, defect PPM (capability M8).
- **Purchase order (`src/purchase_order.py`)** — immutable PO + guarded state machine (draft→approved→issued→received / cancel) (capability M8).
- **Data-quality core (`src/data_quality.py`)** — GTIN/UPC/EAN check-digit validation, SKU normalization, canonical column mapping (capability M11).
- **SKU deduplication (`src/sku_dedup.py`)** — duplicate detection by shared GTIN + fuzzy name match (rapidfuzz when installed, `difflib` fallback) (capability M11).
- Optional dependency extras: `dataquality` (rapidfuzz, python-stdnum), `mcdm` (pymcdm), `forecast` (statsforecast, utilsforecast) — modules degrade gracefully when absent.
- **Supply-chain leadership grounding (L3)** — ingested Palamariu & Alicke, *From Source to Sold: Stories of Leadership in Supply Chain* (2022) into the books knowledge graph (`knowledge/scm-books/`): +312 leadership concept nodes from 26 leader interviews, plus the book's **CHAIN model** (Collaborative / Holistic / Adaptable / Influential / Narrative) as explicit citable nodes bridged to `supply_chain_strategy`. The books graph grew 430→742 nodes / 763→1384 edges; `scm_agent` `KnowledgeBase.search()` / `explain()` now ground the `leadership_chain` capability with chapter-level citations (previously the CHAIN model was synthesized in code with no L3 source).
- **Voice agent brain (`src/voice/`)** — the credential-free core of capability M16: 7 call playbooks (`playbooks.py`), the outbound-call compliance gate (`compliance.py` — DNC/consent/calling-window/attempt-cap + TCPA/EU-AI-Act AI-disclosure), the 12 logistics document field maps (`doc_schemas.py`), the six-block ElevenLabs system-prompt + agent-config builder (`agent_config.py`), and the curated RAG knowledge base (`knowledge_base/logistics_kb.md`). Only live dialling (ElevenLabs/Twilio) needs credentials.
- Capability Expansion Plan progress: **Fase 0** (foundations) + **Fase 1** + pure cores of **Fases 3-4** (M6/M7/M8/M11/M2-metrics) + first dep-gated module with fallback + **Fase 5 voice-agent brain** (M16, credential-free). See `documentation/CAPABILITY_EXPANSION_PLAN.md`. ~159 new tests; full suite 374 passing, ruff clean.
- **`newsvendor` + `cycle_count` agent tools** — wired the existing single-period newsvendor engine and a standalone ABC-weighted cycle-count schedule (`jobs/newsvendor_job.py`, `jobs/cycle_count_job.py`) into the registry as two more brief-routable capabilities.
- **`multi_echelon` agent tool** — wired the existing serial-GSM safety-stock-placement engine into the registry (`jobs/multi_echelon_job.py`): the cost-minimizing buffer split across a supplier→DC→store network, per-stage order-up-to levels.
- **`transportation` agent tool + `src/logistics/`** — new mode-selection engine (`freight.py`, `modes.py`): cheapest feasible transport mode (parcel/LTL/FTL/intermodal) per shipment from a configurable rate card, offline (no carrier API), wired via `jobs/transportation_job.py`.
- **`fefo` agent tool + `src/lots/`** — new lot-expiry engine (`expiry.py`, `fefo.py`): shelf-life aging report, First-Expired-First-Out issue order, at-risk-of-expiry quantity, markdown-vs-scrap recommendation, wired via `jobs/fefo_job.py`.
- **`slotting` + `simulation` agent tools** — wired the existing cube-per-order-index slotting engine and the Monte-Carlo safety-stock/order-up-to search into the registry (`jobs/slotting_job.py`, `jobs/simulation_job.py`).
- **`excess_obsolete` agent tool + `src/excess_obsolete.py`** — new engine: classify on-hand stock into healthy/excess/dead from days-of-cover and time-since-last-sale, size the cash tied up, recommend a disposition per SKU, wired via `jobs/excess_obsolete_job.py`.
- **`facility_location` agent tool + `src/facility_location.py`** — new engine: single-facility siting (center-of-gravity + Weiszfeld 1-median) vs. the current site, offline straight-line distance, wired via `jobs/facility_location_job.py`.
- **`drp` agent tool + `src/drp.py`** — new engine: time-phased distribution requirements planning grid per branch (gross→net→planned order releases), rolled up as the central DC's gross requirements, wired via `jobs/drp_job.py`.
- Registry now carries **34 agent-routable tools** (`build_default_registry()` is the source of truth); +10 tools shipped across PRs #73-#80 without a matching changelog update at the time - backfilled here. See HANDOFF.md for the audit finding that caught the gap.

### Fixed
- **World-class audit remediation (P0 + P1, PRs #82/#83)** — writeback `Approval` now requires an HMAC-SHA256 `signature` (env `LINCHPIN_APPROVAL_SECRET`) so an `Approval` can no longer be forged by constructing the dataclass directly; new `src/writeback_store.py::SqliteAuditLedger` for a persistent, restart-surviving audit/idempotency ledger; the Odoo connector's draft-PO write path now routes through the writeback safety plane instead of calling `execute_kw(..., "create")` directly (closes a duplicate-RFQ-on-retry gap); agent tool routing (`scm_agent/registry.py`) now matches on word boundaries with plural tolerance instead of raw substring matching; citation grounding (`scm_agent/knowledge.py`) re-ranks by IDF weight instead of raw token count and gates method-rule triggers to the active tool's own keyword domain; math fixes in `src/eoq.py` (volume-discount EOQ), `src/multi_echelon.py` (serial-GSM coupling), `src/newsvendor.py` (both optimizers), and `src/pricing.py`/`jobs/pricing.py` (`confident` logic).
- **`safety_stock()` regression coverage** — added a test pinned to Vandeput's Table 4.1 worked example that checks the exact signed value through `safety_stock()` itself, closing a gap where the module's own tests didn't catch a sign error in the core formula (only distant integration tests happened to).
- **`_ReorderRuleStore.commit()` / `_DraftPoStore.commit()` partial-failure rollback** — a write failing partway through a multi-change commit to Odoo no longer leaves earlier writes in that same call live with no audit trail; on any exception, everything written so far in that call is compensated (restored/deleted) before the failure propagates, so a retry with the same idempotency key can proceed cleanly.
- **CSV/Excel formula-injection guard (`src/sanitize.py`)** — a `product_id` (or any other string field) starting with `=`, `+`, `-` or `@` survived unescaped into generated `.xlsx`/`.csv` deliverables; openpyxl auto-promotes a leading `=` to a live formula cell, so a malicious upload could execute code when a client opened the file (OWASP CSV injection). New `defuse_formula()` helper prefixes such values with a leading `'` (Excel's standard force-text marker) before every cell/row write in `src/excel_export.py`'s `_write_table()` and the GSM-summary cells in `write_analysis_workbook()`, `jobs/deliverables.py::write_excel()`/`write_pricing_excel()`, and `src/export.py::write_summary_csv()` (which every `jobs/*_job.py` CSV deliverable goes through) — every sink confirmed reachable from an uploaded CSV's unvalidated columns. Checks `value.lstrip()` for the trigger prefix (not just the literal first character) so a payload disguised behind leading whitespace/control characters can't bypass it independent of any caller's own `.strip()`. Found during a security review of an unrelated branch; pre-existing, not specific to that branch's changes.
- **Same formula-injection gap in `src/powerbi_export.py`'s Power BI CSV export** — this module has its own `_write()` CSV sink, not covered by the fix above (it never routed through `src/export.py::write_summary_csv()`). Now sanitizes every cell (`df.apply(lambda col: col.map(defuse_formula))`) before `to_csv()`, closing the same gap for `demand_history`/`product_summary`/`policies`/`cost_optimization`/`fill_rate`/`gsm_nodes`.

### Added
- **Read-only MCP server (Phase A go-to-market, `webapp/mcp_server.py`)** — exposes 8 of the 34 registered tools (inventory optimization, ABC-XYZ, newsvendor, forecast, financial KPIs, pricing, data quality, what-if) to other AI agents over MCP/Streamable HTTP, mounted at `/mcp` alongside the dashboard. Deliberately excludes every writeback-capable tool (`odoo_replenishment` and anything touching `src/writeback.py`) - analysis only. New `src/mcp_keys.py::McpKeyStore`: a SQLite-backed per-client API key store (high-entropy tokens, only the SHA-256 hash ever persisted, issue/validate/revoke), gating the MCP surface independently of the dashboard's single shared `LINCHPIN_API_KEY` (`webapp/mcp_auth.py`). Manual key issuance for now (`examples/issue_mcp_key.py`) - Phase A billing is a Stripe Payment Link followed by a hand-issued key, no self-serve signup or metering yet. Rate limiting (`webapp/security.py::rate_limit`, now accepts an optional `key` override) is identity-aware for authenticated MCP traffic - each client's quota is keyed by their own resolved name, not shared source IP, so distinct clients (or an MCP client and a dashboard user) behind the same NAT/proxy can't throttle each other; caught and fixed during an independent security review before merge. See `docs/MCP_SERVER.md`.

### Changed
- **Renamed the project to Linchpin** — repo, distribution package, agent console, and docs. The GitHub repository moved to `esstipi-debug/linchpin` (the old `supply-chain-optimization` URL redirects automatically). The importable module `scm_agent` and the engine package `src` are unchanged.
- **Reframed the README around multi-source grounding.** The value proposition now leads with the project's knowledge graph of 17 SCM books and the codebase, rather than a single book. Per-module academic citations (Vandeput 2020 and others) in `src/` docstrings and the L3 bridge are unchanged — the engine still maps to Vandeput's chapters where it implements them.

## [2.9.0] - 2026-06-24

### Added
- `warehouse_layout` capability (10th tool) + `warehouse/` core: parametric 3D warehouse
  (building, yard, gates, docks, racks, slots), geometry QA, self-contained Three.js viewer,
  webapp `/api/warehouse` + `/warehouse`. Foundation (capa 1a) of the warehouse spatial twin.

## [2.8.0] - 2026-06-21

### Added
- `scm_agent` orchestrator: routes a free-form brief (+ optional data) to a capability and drives prepare → run → QA → deliver.
- `leadership_chain` capability (CHAIN model): score + radar chart + active directives; `jobs/leadership.py`.
- Pluggable `LLMProvider` (Claude when `ANTHROPIC_API_KEY` is set, rules fallback otherwise).
- CLI `examples/run_agent.py` and `POST /api/jobs` HTTP endpoint with downloadable deliverables.
- Optional `llm` and `web` dependency extras.

### Security
- `POST /api/jobs`: sanitize the uploaded filename to a basename pinned inside the per-job directory (blocks path traversal / arbitrary file write) and cap uploads at 25 MB (413 on exceed).

### Hardening
- `POST /api/jobs`: sweep per-job output directories older than `JOBS_TTL_SECONDS` (1 h) on each request so deliverables/uploads don't accumulate.
- `examples/run_agent.py` self-inserts the repo root on `sys.path`, so the documented commands run from any working directory.
- Orchestrator logs swallowed exceptions at `DEBUG` (`exc_info=True`) without breaking the never-crash contract.

## [2.7.0] - 2026-06-19

### Added
- **Price optimization** (`src/pricing.py`): demand-elasticity estimation
  (log-log fit), profit-maximizing price for constant-elasticity (`p* = c·ε/(ε+1)`)
  and linear demand, markdown/clearance pricing, and `recommend_price` with
  confidence + action. Extends the engine's newsvendor / volume-discount economics.
- **Pricing playbook** (`jobs/pricing.py`): client price/quantity history →
  elasticity → optimal price per SKU with profit-uplift; QA (`qa.verify_pricing`)
  + deliverables (Excel + report) reusing the job-fulfillment pipeline.
- `scripts/generate_pricing_sample.py` + `data/sample_pricing.csv`;
  `examples/run_pricing_job.py`; `jobs/SAMPLE_PRICING_REPORT.md`.
- 12 tests (132 total): pricing engine + pricing playbook.

### Changed
- `src/__init__.py` exports the pricing API; `jobs/README.md` lists the pricing
  job type.

### Added
- **Job-fulfillment layer** (`jobs/`) — package the engine for real supply-chain
  freelance work:
  - `jobs/intake.py`: detect/normalize any client schema (CSV/Excel, ERP or
    Kaggle-style) to the canonical demand schema, aggregated per period
  - `jobs/inventory_optimization.py`: playbook — forecast → (s,Q)/(R,S) policy →
    budget allocation → structured `JobReport`
  - `jobs/qa.py`: automated verification of the report's numbers (nothing ships
    until it passes)
  - `jobs/deliverables.py`: client-ready Excel workbook + Markdown report + CSV
  - `examples/run_inventory_job.py`: end-to-end CLI; `jobs/SAMPLE_REPORT.md`
- 8 job-layer tests (120 total)

### Notes
- Human-in-the-loop by design — automates the analysis/deliverable, not Upwork
  bidding or client comms (ToS).

### Added
- **Inventory Planner web UI** (`webapp/`): a 4-tab dashboard (Portfolio, SKU
  Detail with demand/forecast chart, Budget Planner, Forecast Quality) served by
  FastAPI over the engine — every number comes from `src/`. Vanilla JS, no Node /
  build step; one `uvicorn` command
- `GET /api/portfolio` runs forecasting → policy → constraints with live
  what-if params (service level, lead time, order cost, budget); validated inputs
- `scripts/generate_portfolio.py` seeds an 8-SKU demand portfolio
- `tests/test_webapp.py` (9 tests, 110 total) via FastAPI TestClient
- Screenshots in `webapp/screenshots/`

### Changed
- `requirements-dev.txt`: add fastapi / uvicorn / httpx for the web UI + its tests

### Added
- **`SqlDemandSource`** (`src/sources.py`): live demand from any DB-API 2.0
  connection (SQLite, Postgres, MySQL ...) via a table name or parameterised
  query; validates bare table names as SQL identifiers to avoid injection
- `examples/run_sql_source.py`: live-data demo (SQLite stand-in for an ERP feed)
  running the same source → forecast → policy chain
- 4 SQL-adapter tests (101 total, ~91% coverage)

### Changed
- README: live SQL data path documented; ERP/WMS adapter now a thin layer

## [2.3.0] - 2026-06-19

### Added
- **Pluggable data sources** (`src/sources.py`): `DemandSource` protocol with
  `CsvDemandSource` and `DataFrameDemandSource` adapters — the engine is no
  longer hard-wired to CSV; a SQL/API/ERP adapter just satisfies the protocol
- **Business constraints** (`src/constraints.py`): MOQ, case-pack rounding,
  shelf-life caps, and `allocate_under_budget` — a portfolio budget allocator
  that trims safety stock to fit while preserving cycle-stock economics
- `examples/run_constrained_plan.py`: full chain source → forecast → policy →
  constraints
- 14 tests (97 total, ~91% coverage)

### Changed
- README: "engine → product" section now reflects the assembled AUTO chain

## [2.2.0] - 2026-06-19

### Added
- **Demand forecasting front-end** (`src/forecasting.py`): moving average, simple
  exponential smoothing, and Croston (intermittent demand), with auto method
  selection by ADI classification
- Forecast-error σ_e — the theoretically correct dispersion for safety stock
  (Vandeput 2021, §4.2.5) — exposed via `ForecastResult.to_engine_inputs`
- `examples/run_forecast_to_policy.py`: end-to-end history → forecast → (s,Q) policy
- 13 forecasting tests (83 total, ~90% coverage)

### Changed
- README: forecasting documented; "Future extensions" reframed as the
  engine → product ("AUTO") roadmap

## [2.1.0] - 2026-06-19

### Added
- `pyproject.toml`: PEP 621 packaging + Ruff, pytest and coverage config (pip-installable, no more `PYTHONPATH` hack)
- `requirements-dev.txt` for test/lint tooling (`pytest`, `pytest-cov`, `ruff`)
- Direct unit tests for previously-untested modules: `policies`, `risk_period`, `demand_variability`, `discrete_demand`, `export` (23 new tests, 70 total)
- CI now lints with Ruff and enforces a 80% coverage gate (measured coverage ~90%)

### Fixed
- `tests/test_multi_echelon.py`: `test_gsm_case1_higher_cost_than_optimal` asserted nothing — added the intended cost comparison
- Removed dead code / unused locals and imports flagged by Ruff (`eoq`, `fill_rate`, `policies`, `distributions`, `powerbi_export`, several tests/examples)

### Changed
- `pytest` moved out of runtime `requirements.txt` into dev deps
- Test config moved from `pytest.ini` into `pyproject.toml`

## [2.0.0] - 2026-06-18

### Added
- Product metadata from CSV: `unit_cost`, `lead_time_days` (`src/data_loader.py`)
- EOQ volume discounts §2.5.3 (`compute_eoq_volume_discount`)
- Gamma/auto safety stock in policies (`src/demand_variability.py`)
- Multi-SKU batch analysis (`src/batch.py`, `examples/run_batch.py`)
- Inventory charts (`examples/plot_inventory.py`)
- Sim-opt grid over R and Ss (`optimize_rs_simulation_grid`)
- GitHub Actions CI (Python 3.11–3.13)
- Rewritten FAQ and CONTRIBUTING (removed placeholder marketing)

### Changed
- Power BI export uses per-SKU lead time, unit cost, GSM params
- METHODOLOGY assumptions table updated
- README: CI badge, full project layout

## [1.3.0] - 2026-06-18

### Added
- Power BI CSV dataset export `src/powerbi_export.py`
- `examples/build_powerbi_dataset.py`
- Power Query templates `power-bi/queries/*.pq`
- DAX measures `power-bi/measures.dax`
- Setup guide `power-bi/SETUP.md`
- `--powerbi` flag on `run_complete.py`
- Test for Power BI export (42 total)

## [1.2.0] - 2026-06-18

### Added
- Native Excel export `src/excel_export.py` and `examples/build_excel_workbook.py`
- `--excel` flag on `run_complete.py`
- GSM simulation with customer backorders at demand node
- `lost_sales` on `(s,Q)` simulation
- Agent skills README; skills synced to Claude Code
- Tests: Excel export, GSM backorders (41 total)

### Changed
- `requirements.txt`: openpyxl, pytest
- Excel templates README: live `.xlsx` workflow

## [1.1.0] - 2026-06-18

### Added
- End-to-end script `examples/run_complete.py` with optional CSV export
- CSV export layer `src/export.py` for Excel / Power BI
- GSM discrete simulation `simulate_serial_gsm` (Ch. 10.5)
- Lost sales mode on `(R,S)` simulation (`lost_sales=True`, §5.3.2)
- Tests for GSM simulation and lost sales (39 total)

### Changed
- README: agent skills section, `run_complete` quick start
- Excel templates README: CSV export workflow

## [1.0.0] - 2026-06-18

### Added
- Gamma demand, distribution selection, gamma loss (Ch. 9) — `src/distributions.py`
- Serial multi-echelon GSM allocation (Ch. 10) — `src/multi_echelon.py`
- Newsvendor discrete/continuous (Ch. 11) — `src/newsvendor.py`
- Histogram PMF and KDE discretization (Ch. 12) — `src/discrete_demand.py`
- Simulation-based safety stock optimization (Ch. 13) — `src/simulation_opt.py`
- Example `examples/run_part4.py`
- Tests for chapters 9–13

### Changed
- README and METHODOLOGY: full book coverage (Ch. 1–13) documented
- `src/__init__.py`: exports for all modules

## [0.2.0] - 2026-06-18

### Added
- Stochastic lead time in policies (Ch. 6) — `src/risk_period.py`
- Fill rate + normal loss function (Ch. 7) — `src/fill_rate.py`
- Cost/service optimization for (R,S) and (s,Q) (Ch. 8) — `src/cost_optimization.py`
- Example `examples/run_part3.py`
- Tests for fill rate, cost optimization, lead time

## [0.1.0] - 2026-06-18

### Added
- Python implementation aligned with Vandeput (2020), Part I–II:
  - EOQ model (`src/eoq.py`) — Ch. 2–3
  - Safety stock under normal demand (`src/safety_stock.py`) — Ch. 4
  - Policies `(s,Q)` and `(R,S)` (`src/policies.py`) — Ch. 5
  - Discrete-period simulation with backorders (`src/simulation.py`) — Ch. 5.3
- Sample demand data (`data/sample_demand.csv`)
- Runnable example (`examples/run_part1_part2.py`)
- Unit tests with book numeric example (§2.2.4)
- Documentation rewritten to reference the book

### Changed
- README: removed placeholder marketing; cites Vandeput (2020)
- Excel / Power BI folders marked as planned export layers

### Removed
- Claims of ARIMA/Prophet templates (not in the inventory optimization book)

### Known limitations
- Normal demand assumption only (Ch. 9+ not yet implemented)
- Backorders only (lost sales in Ch. 5.3.2 — planned)
- Single-echelon, single SKU

---

## [2.0.0] - 2026-03-18 (documentation-only release)

Initial markdown structure without executable templates.
