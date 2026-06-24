<div align="center">

# 🔗 Linchpin

### The agentic brain for supply-chain decisions — grounded in the field's best models and sources.

**Linchpin** turns a plain-language brief into finished, QA-gated supply-chain deliverables. A Python **engine** implements the field's established models — EOQ, safety stock, `(s,Q)`/`(R,S)` policies, multi-echelon, simulation, forecasting and pricing, plus **ABC-XYZ classification, DDMRP buffers, financial KPIs, reconciliation, slotting and procurement** — and an **orchestrator agent** drives them end to end with a **never-unprotected guarantee** (every result is executed *or* hands you a ready, safe next step) and **safe-staging writeback**. Each result is **grounded** in a knowledge graph of **17 SCM books and the codebase itself**.

[![version](https://img.shields.io/badge/version-2.9.0-5eead4)](CHANGELOG.md)
[![python](https://img.shields.io/badge/python-3.11--3.13-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![tests](https://github.com/esstipi-debug/linchpin/actions/workflows/tests.yml/badge.svg)](https://github.com/esstipi-debug/linchpin/actions/workflows/tests.yml)
[![coverage](https://img.shields.io/badge/coverage-93%25-3fb950)](#)
[![License: MIT](https://img.shields.io/badge/License-MIT-3fb950.svg)](LICENSE)

</div>

![Linchpin console — a brief routed to the leadership_chain capability, returning a CHAIN profile with downloadable chart and report](docs/assets/scm-agent-console.png)

<div align="center"><sub>The live agent console (<code>webapp/static/prototype/</code>) talking to the real <code>POST /api/jobs</code>.</sub></div>

---

## ⚡ What it does

Give it a brief; it **classifies → runs → validates (QA) → delivers**. If QA fails, nothing ships.

```mermaid
flowchart LR
  A["Brief (+ optional data)"] --> C{"Classify intent"}
  C --> I["inventory_optimization"]
  C --> P["pricing"]
  C --> L["leadership_chain"]
  I --> Q{"QA gate"}
  P --> Q
  L --> Q
  Q -->|pass| G["Ground in L3 — source + code citations"]
  G --> D["Deliverables — Excel · report · chart + Fuentes"]
  Q -->|fail| N["no deliverable"]
```

| Capability | Input | Deliverable |
|---|---|---|
| 📦 `inventory_optimization` | demand CSV/Excel | Excel + report + CSV — forecast → `(s,Q)`/`(R,S)` → budget fit |
| 💲 `pricing` | price/qty CSV/Excel | Excel + report — elasticity → margin-maximizing price |
| 🧭 `leadership_chain` | a brief / scores | radar chart + report — CHAIN leadership profile + directives |
| 🏗️ `warehouse_layout` | params / brief | 3D HTML + layout.json + report — navigable warehouse (building, yard, docks, gates, racks) |

Runs **with or without an LLM**: an optional `LLMProvider` (Claude) sharpens routing and the narrative; the deterministic core works on its own. The whole thing is **600+ tests, ~90 % coverage**.

---

## 🧰 Capability toolkit

Beyond the three routed capabilities above, the engine ships a growing set of decision modules — see the [Capability Expansion Plan](documentation/CAPABILITY_EXPANSION_PLAN.md):

| Area | Modules |
|---|---|
| **Planning** | ABC-XYZ classification · DDMRP buffers + net-flow · forecast-accuracy metrics (MAPE/WAPE/RMSSE/MASE) |
| **Control** | reconciliation / IRA + cycle-count plan · stockout / excess / reorder alerting |
| **Procurement** | landed cost (Incoterm-aware) · supplier scorecards (OTIF/PPM) · purchase-order state machine |
| **Warehouse** | cube / m³ sizing + COI slotting |
| **Finance** | inventory turns · DIO · GMROI · cash-to-cash · sell-through |
| **Data quality** | GTIN/UPC check-digit · SKU dedup · canonical column mapping |

Two cross-cutting guarantees keep the agent safe in production:

- **Never-unprotected** — every result is `EXECUTED` or carries an executable path: ranked options, a prepared handoff (pre-filled PO / email / count sheet), or an escalation. No dead ends.
- **Safe-staging writeback** — changes are computed as a dry-run changeset, gated by risk tier + time-boxed approval, applied idempotently, and fully auditable / `rollback()`-able. The agent never mutates a system of record blindly.

---

## 🚀 Quick start

```bash
git clone https://github.com/esstipi-debug/linchpin
cd linchpin
pip install -e ".[web]"          # canonical install (engine + web UI). For the engine only, `pip install -r requirements.txt` also works.

# ── The agent: brief in, deliverable out ───────────────────────────────
python examples/run_agent.py --brief "set up reorder points" --data data/sample_demand_portfolio.csv
python examples/run_agent.py --brief "what price maximizes profit" --data data/sample_pricing.csv
python examples/run_agent.py --brief "evaluate our SC leadership" --scores "3 2 3 1 1" --name "Team"

# ── Web UI + live agent console (deps already installed above) ──────────
python -m uvicorn webapp.app:app --reload
#   dashboard       → http://localhost:8000
#   agent console   → http://localhost:8000/console
```

> Set `ANTHROPIC_API_KEY` (and `pip install -e ".[llm]"`) to enable Claude-assisted parsing and narrative — optional.

<details>
<summary><b>📐 Engine CLIs — the models, hands-on</b></summary>

```bash
# EOQ + policies + simulation on sample data
python examples/run_part1_part2.py --simulate

# Fill rate + optimal service level / review period (Ch. 7-8)
python examples/run_part3.py

# Gamma, GSM, newsvendor, KDE, simulation optimization (Ch. 9-13)
python examples/run_part4.py

# All SKUs in one CSV
python examples/run_batch.py

# Demand chart vs policy levels
python examples/plot_inventory.py --product SKU-A

# Full pipeline + exports
python examples/run_complete.py --simulate --export output/summary.csv --excel excel-templates/analysis.xlsx

# Power BI dataset (CSV star schema) — see power-bi/SETUP.md
python examples/build_powerbi_dataset.py --simulate

# Forecast demand from history, then derive the policy (uses sigma_e)
python examples/run_forecast_to_policy.py

# Full chain: source -> forecast -> policy -> budget/MOQ constraints
python examples/run_constrained_plan.py --budget 20000

# Live data: read demand from a SQL database instead of a CSV
python examples/run_sql_source.py

# Client deliverables from any CSV/Excel -> Excel + written report
python examples/run_inventory_job.py --data client_demand.csv --budget 50000 --client "Acme Co"
python examples/run_pricing_job.py   --data sales.csv --client "Acme Co"
```
</details>

---

## 🧠 The agent (`scm_agent/`)

A **registry-based orchestrator**: every capability is a `Tool` with four stages — `prepare → run → qa → deliver` — that the orchestrator drives, enforcing **"QA fails ⇒ no deliverable"** in one place. Adding a capability is one `register()` call; no routing edits.

```
brief ─▶ intent.classify ─▶ registry.get(tool) ─▶ prepare ─▶ run ─▶ QA ─▶ deliver ─▶ JobResult
              (rules + optional LLM)                inventory · pricing · leadership_chain
```

- **`scm_agent/`** — `types` · `llm` (Claude / rules fallback) · `registry` · `tools` · `intent` · `orchestrator` · `knowledge` (L3 grounding) · `guided_bridge` (never-unprotected contract via `src/guided.py`)
- **Entry points** — CLI `examples/run_agent.py`, HTTP `POST /api/jobs` (multipart, with downloadable deliverables), and the live console under `webapp/static/prototype/`
- **Statuses** — `ok` · `needs_clarification` · `needs_data` · `qa_failed` · `error`

Full reference: [`scm_agent/README.md`](scm_agent/README.md). The `leadership_chain` capability wraps the **CHAIN** model — *síntesis original inspirada en* From Source to Sold *(Palamariu & Alicke, 2022); no reproduce el texto del libro.*

<details>
<summary><b>🔄 Request lifecycle — <code>POST /api/jobs</code></b></summary>

```mermaid
sequenceDiagram
    participant C as Client (console / curl)
    participant API as FastAPI (webapp/app.py)
    participant O as Orchestrator (scm_agent)
    participant T as Tool (prepare→run→qa→deliver)
    participant KB as L3 knowledge graph

    C->>API: multipart: brief, client, params, file
    API->>API: validate params JSON · sanitize client · cap upload 25 MB · pin filename
    API->>O: run(brief, data_path, overrides, out_dir)
    O->>O: intent.classify (rules + optional LLM)
    O->>T: registry.get(tool).prepare → run → qa
    T->>KB: query citations (chapter ↔ src/ function)
    alt QA passes
        T-->>O: deliver() writes Excel + report to job dir
        O-->>API: JobResult(status=ok, deliverables, citations)
    else QA fails
        O-->>API: JobResult(status=qa_failed, no deliverable)
    end
    API-->>C: JSON + /jobs-output/ download URLs
```

Example response (`status: ok`):

```json
{
  "status": "ok",
  "tool": "inventory_optimization",
  "confidence": 0.87,
  "summary": "12 SKUs · $44k budget · 3 flagged high-bias · (s,Q) for 9, (R,S) for 3.",
  "deliverables": {
    "report": "/abs/job/inventory_optimization/report.md",
    "excel": "/abs/job/inventory_optimization/inventory_plan.xlsx"
  },
  "download_urls": {
    "report": "/jobs-output/tmp09dw516z/inventory_optimization/report.md",
    "excel": "/jobs-output/tmp09dw516z/inventory_optimization/inventory_plan.xlsx"
  },
  "qa_issues": [],
  "clarifications": [],
  "citations": [
    {"concept": "Safety Stock", "source": "Vandeput Ch.4", "module": "src/safety_stock.py"}
  ]
}
```

Other statuses return the same envelope with `status` one of
`needs_clarification` · `needs_data` · `qa_failed` · `error` and an empty
`deliverables` map.

</details>

---

## 🧠 L3 — domain knowledge & the theory↔code bridge

Every job is **grounded**: the orchestrator queries a knowledge graph and attaches citations to each result (the **Fuentes** shown in the console). Two graphs, one read-only query surface — [`scm_agent/knowledge.py`](scm_agent/knowledge.py):

- **Books graph** ([`knowledge/scm-books/`](knowledge/scm-books/README.md)) — **17 SCM books** (forecasting, pricing, revenue management, inventory — incl. **Vandeput**): 430 concept nodes with chapter citations. Committed.
- **Code graph** (`graphify-out/`) — the codebase itself, built with `/graphify`. Gitignored (regenerable).

The **bridge** ties them together: for each cited concept it resolves the `src/` module that implements it, so a deliverable cites the chapter **and** the function behind it.

```text
Economic Order Quantity           — Vandeput Ch.2  ->  src/eoq.py
Safety Stock                      — Vandeput Ch.4  ->  src/safety_stock.py
Cost & Service-Level Optimization — Vandeput Ch.8  ->  src/cost_optimization.py
```

Query it directly: `python examples/query_knowledge.py --bridge "newsvendor"` · `--search "fill rate"` · `--explain crostons_method`.

---

## ⚙️ The engine — one question, one lens

The analytical core the agent stands on: a chain of small **pure functions**, each answering one question with one point of view. Every number is simulation-real — no Node, no build step.

<div align="center">

![The engine pipeline: PREDICT (forecasting, distributions) → DECIDE (eoq, policies, safety_stock) → OPTIMIZE (fill_rate, cost_optimization, simulation_opt) → REALITY (multi_echelon, newsvendor, constraints) → a QA-gated plan, every number cross-checked against Monte-Carlo simulation](docs/assets/engine-lenses.svg)

</div>

| The question | Engine | The lens it brings |
|---|---|---|
| *How much do I order?* | `eoq.py` | **Cost balance** — the lot size where ordering cost meets holding cost (+ volume discounts). |
| *When do I reorder?* | `policies.py` | **Trigger & risk** — forecast + costs → `(s,Q)`/`(R,S)`: the reorder point *and* the quantity. |
| *How big a buffer?* | `safety_stock.py` | **Uncertainty** — `z·σ·√τ`, keyed off **forecast error σ_e**, not raw demand spread (the classic mistake). |
| *What service do I actually get?* | `fill_rate.py` | **Realized service** — fill rate β (units served), which stays high even when cycle service α drops. |
| *Is more service worth it?* | `cost_optimization.py` | **Economics** — the service level / review period that minimizes holding + shortage cost, not a rule of thumb. |
| *Spiky or skewed demand?* | `forecasting.py` · `distributions.py` | **Shape-aware** — Croston for intermittent, gamma for skew, exactly where the normal model under-stocks. |
| *Where to hold stock across the network?* | `multi_echelon.py` | **System view** — guaranteed-service placement across stages, not greedy per node. |
| *One perishable shot?* | `newsvendor.py` | **Single period** — the critical ratio `cu/(cu+co)`. |
| *Can't trust the assumptions?* | `simulation_opt.py` | **Empirical** — simulate thousands of demand paths, search `(s,Q,S)` for the real optimum. |
| *Is the plan feasible?* | `constraints.py` | **Operational reality** — MOQ, case packs, shelf-life, and a budget allocator that trims safety stock to fit. |
| *What price maximizes margin?* | `pricing.py` | **Elasticity** — per-SKU price sensitivity → the margin-maximizing markup. |

**Why this beats a spreadsheet**

![simulation validated](https://img.shields.io/badge/policies-simulation--validated-4fd1c5?style=flat-square&labelColor=0d1219)
![forecast error](https://img.shields.io/badge/safety_stock-forecast_error_σ-5eead4?style=flat-square&labelColor=0d1219)
![shape aware](https://img.shields.io/badge/demand-Croston_and_gamma-3fb950?style=flat-square&labelColor=0d1219)
![feasible](https://img.shields.io/badge/plan-MOQ_and_budget_feasible-d4a017?style=flat-square&labelColor=0d1219)
![tests](https://img.shields.io/badge/600%2B_tests-passing-4fd1c5?style=flat-square&labelColor=0d1219)

- **Validated, not assumed** — closed-form policies are cross-checked against Monte-Carlo simulation (`simulation.py`, backorders + lost sales).
- **σ_e, not σ_demand** — safety stock keys off *forecast error*, the only correct dispersion (the #1 inventory mistake in the wild).
- **Shape-aware** — intermittent (Croston) and skewed (gamma) demand are first-class, where a normal-curve sheet silently stocks out.
- **Feasible by construction** — MOQ / budget are constraints, so what ships is buildable, not just mathematically optimal.
- **Pure & composable** — each module is a pure function, the engine validated against the book's own numbers in a 600+ test suite; the orchestrator chains them without surprises.

### 🖥️ How you see it

The same engine output, read through four lenses in the live dashboard (`/`):

<table>
<tr>
<td width="50%"><img src="docs/assets/dashboard-portfolio.png" alt="Portfolio tab — plan investment, budget gauge and per-SKU table"><br><sub><b>Portfolio</b> — the whole plan + budget gauge, over/under cap at a glance.</sub></td>
<td width="50%"><img src="docs/assets/dashboard-detail.png" alt="SKU Detail tab — demand history, forecast, reorder line and live what-if sliders"><br><sub><b>SKU Detail</b> — history · forecast · ±σ_e · reorder line, with live what-if sliders.</sub></td>
</tr>
<tr>
<td width="50%"><img src="docs/assets/dashboard-budget.png" alt="Budget Planner tab — per-SKU cycle vs safety stock allocation"><br><sub><b>Budget Planner</b> — cycle vs. safety allocation as you move the cap.</sub></td>
<td width="50%"><img src="docs/assets/dashboard-forecast.png" alt="Forecast Quality tab — bias spread per SKU"><br><sub><b>Forecast Quality</b> — bias spread per SKU; which forecasts to trust.</sub></td>
</tr>
</table>

Sliders **recompute the policy live**; the **agent console** (`/console`) adds the L3 **Fuentes** — the book chapter *and* the `src/` function behind each number.

<details>
<summary><b>📖 Chapter map (Vandeput 2020) — module by module</b></summary>

> **Primary reference for the inventory engine:** Vandeput (2020) — the modules below map to its chapters. Deliverables are grounded across all **17 books** in the knowledge graph, not a single source. Official companion code: [supchains.com/resources-invopt](https://supchains.com/resources-invopt) (password: `SupChains-IO`).

| Book section | Module | Status |
|--------------|--------|--------|
| Ch. 1 — Inventory policies | `src/policies.py` | `(s,Q)`, `(R,S)` |
| Ch. 2 — EOQ + volume discounts | `src/eoq.py` | ✅ §2.5.3 |
| Ch. 3 — Lead time & review period | `src/data_loader.py`, `src/eoq.py` | ✅ CSV + power-of-2 |
| Ch. 4 — Safety stock | `src/safety_stock.py`, `src/demand_variability.py` | ✅ normal + gamma |
| Ch. 5 — Simulation | `src/simulation.py` | ✅ backorders + lost sales |
| Ch. 6 — Stochastic lead time | `src/risk_period.py`, `src/policies.py` | ✅ |
| Ch. 7 — Fill rate | `src/fill_rate.py` | ✅ |
| Ch. 8 — Cost optimization | `src/cost_optimization.py` | ✅ |
| Ch. 9 — Gamma demand | `src/distributions.py` | ✅ |
| Ch. 10 — Multi-echelon GSM | `src/multi_echelon.py` | ✅ allocation + simulation |
| Ch. 11 — Newsvendor | `src/newsvendor.py` | ✅ |
| Price optimization | `src/pricing.py` | ✅ elasticity / optimal price / markdown |
| Ch. 12 — Histograms / KDE | `src/discrete_demand.py` | ✅ |
| Ch. 13 — Simulation optimization | `src/simulation_opt.py` | ✅ grid R + Ss |
| Batch multi-SKU | `src/batch.py` | ✅ |
| Demand forecasting (front-end) | `src/forecasting.py` | ✅ MA / SES / Croston + σ_e |
| Pluggable data sources | `src/sources.py` | ✅ CSV / DataFrame / SQL (DB-API) |
| Business constraints | `src/constraints.py` | ✅ MOQ / case packs / shelf-life / budget |
| Export | `excel_export`, `powerbi_export` | ✅ |

</details>

<details>
<summary><b>Key formulas (Part I–II)</b></summary>

**EOQ** (eq. 2.2–2.3) — `Q* = sqrt(2 k D / h)`, `C* = sqrt(2 k D h)`

**Safety stock** (eq. 4.3) — `Ss = z_alpha · sigma_d · sqrt(tau)`  ·  `(s,Q)`: τ = L  ·  `(R,S)`: τ = R + L

**Policies** (Ch. 5) — `(s,Q): s = dL + Ss, Q = Q*`  ·  `(R,S): S = dL + dR + Ss`

</details>

<details>
<summary><b>Data format & parameters</b></summary>

`data/sample_demand.csv`:

```csv
date,product_id,quantity,unit_cost,lead_time_days
2024-01-01,SKU-A,100,50,7
```

```bash
python examples/run_part1_part2.py --product SKU-B --lead-time 2 --service-level 0.90 --simulate
```

| Flag | Meaning | Book ref |
|------|---------|----------|
| `--holding-cost` | h (per unit/year) | §2.1 |
| `--order-cost` | k (fixed order cost) | §2.1 |
| `--lead-time` | L (periods) | §3.1, §5.1 |
| `--service-level` | Cycle service level α | §4.1 |
| `--periods-per-year` | Converts weekly data to D | §2.2 |

</details>

---

## 🏗️ From engine to product

The full chain runs end to end (`examples/run_constrained_plan.py`):

```
data source → forecast (σ_e) → (s,Q)/(R,S) policy → MOQ/case packs → budget fit
src/sources.py   src/forecasting.py   src/policies.py   src/constraints.py
```

- **Pluggable data** (`src/sources.py`) — CSV, in-memory DataFrame, or any SQL database via `SqlDemandSource` (SQLite, Postgres, MySQL). New backends just satisfy the `DemandSource` protocol.
- **Forecasting** (`src/forecasting.py`) — MA / SES / Croston, exposing σ_e, the correct safety-stock dispersion (Vandeput 2021, §4.2.5).
- **Constraints** (`src/constraints.py`) — MOQ, case packs, shelf-life caps, and a budget allocator that trims safety stock across the portfolio to fit.
- **Web UI** (`webapp/`) — a 4-tab planner (Portfolio · SKU Detail · Budget Planner · Forecast Quality) + the live agent console, served by FastAPI over the engine. See [webapp/README.md](webapp/README.md).
- **Job-fulfillment layer** (`jobs/`) — turn a client's demand file (any schema) into client-ready Excel + a written report with automated QA. See [jobs/README.md](jobs/README.md).

<details>
<summary><b>Project layout</b></summary>

```
scm_agent/            Orchestrator: brief → classify → tool → QA → deliver
jobs/                 Playbooks (inventory · pricing · leadership) + intake/QA/deliverables
src/                  Core engine (EOQ → simulation optimization → forecasting → pricing)
webapp/               FastAPI dashboard + POST /api/jobs + live agent console (static/prototype/)
examples/             CLI workflows (run_agent, parts 1-4, batch, jobs, plots)
tests/                600+ tests: engine (book numeric examples) + agent + HTTP layer (traversal/upload guards)
data/                 Sample demand + pricing
documentation/        Guides: Getting Started, FAQ, methodology, capability-expansion plan
docs/                 Design briefs, handoff notes, assets
power-bi/             CSV dataset + M queries + DAX + SETUP.md
.cursor/skills/       Agent skills (Cursor / Claude Code)
.github/workflows/    CI (pytest on 3.11–3.13)
```

</details>

---

## 📚 Docs, skills & references

| Document | Content |
|----------|---------|
| [Getting Started](documentation/GETTING_STARTED.md) | Setup and first run |
| [Methodology](documentation/METHODOLOGY.md) | Models, assumptions, glossary |
| [FAQ](documentation/FAQ.md) | Common questions |
| [`scm_agent/README.md`](scm_agent/README.md) | The agent reference |
| [Security](SECURITY.md) | Threat model, enforced controls, hardening checklist |
| [Deployment](docs/DEPLOYMENT.md) | Production hardening: env controls, reverse proxy, load notes |

**Agent skills** (`.cursor/skills/`, synced to `~/.claude/skills/`): `vandeput-inventory-optimization` (overview + decision tree), `…-eoq-policies` (Ch. 2–5), `…-service-cost` (Ch. 6–8), `…-advanced` (Ch. 9–13). Invoke in Claude Code with `/vandeput-inventory-optimization`.

**References**
- Vandeput, N. (2020). *Inventory Optimization: Models and Simulations*. De Gruyter. ISBN 978-3-11-067391-3
- Vandeput, N. (2021). *Data Science for Supply Chain Forecasting* — forecast error σ_e (§4.2.5)
- Community notebooks: [fedinb/Inventory-Optimization](https://github.com/fedinb/Inventory-Optimization)

---

## License

MIT — see [LICENSE](LICENSE). Book content and formulas © Nicolas Vandeput / De Gruyter; this repo implements those models independently for learning and practice.
