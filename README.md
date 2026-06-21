# Supply Chain Optimization

Python implementation of inventory models from **Nicolas Vandeput**, *Inventory Optimization: Models and Simulations* (De Gruyter, 2020).

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Tests](https://github.com/esstipi-debug/supply-chain-optimization/actions/workflows/tests.yml/badge.svg)](https://github.com/esstipi-debug/supply-chain-optimization/actions/workflows/tests.yml)

This repository turns the book’s models into runnable code: EOQ, safety stock, inventory policies `(s,Q)` and `(R,S)`, and discrete-period simulations to validate cycle service levels.

> **Source of truth:** Vandeput (2020). Official book code: [supchains.com/resources-invopt](https://supchains.com/resources-invopt) (password: `SupChains-IO`).

---

## Quick start

```bash
git clone <this-repo>
cd supply-chain-optimization
pip install -r requirements.txt

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

# Pre-built workbook template
python examples/build_excel_workbook.py

# Power BI dataset (CSV star schema)
python examples/build_powerbi_dataset.py --simulate
# See power-bi/SETUP.md for Desktop import

# Forecast demand from history, then derive the policy (uses sigma_e)
python examples/run_forecast_to_policy.py

# Full chain: source -> forecast -> policy -> budget/MOQ constraints
python examples/run_constrained_plan.py --budget 20000

# Live data: read demand from a SQL database instead of a CSV
python examples/run_sql_source.py

# Web UI — interactive dashboard over the engine (FastAPI, no Node)
pip install -r webapp/requirements.txt
python scripts/generate_portfolio.py
python -m uvicorn webapp.app:app --reload   # http://localhost:8000

# Fulfill a client job from any CSV/Excel -> Excel + report deliverables
python examples/run_inventory_job.py --data client_demand.csv --budget 50000 --client "Acme Co"
```

Expected output includes `Q*`, reorder point `s`, order-up-to level `S`, safety stock, and simulated service levels.

---

## What is implemented

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
| Ch. 12 — Histograms / KDE | `src/discrete_demand.py` | ✅ |
| Ch. 13 — Simulation optimization | `src/simulation_opt.py` | ✅ grid R + Ss |
| Batch multi-SKU | `src/batch.py` | ✅ |
| Demand forecasting (front-end) | `src/forecasting.py` | ✅ MA / SES / Croston + σ_e |
| Pluggable data sources | `src/sources.py` | ✅ CSV / DataFrame / SQL (DB-API) |
| Business constraints | `src/constraints.py` | ✅ MOQ / case packs / shelf-life / budget |
| Export | `excel_export`, `powerbi_export` | ✅ |

---

## Project layout

```
src/                  Core models (EOQ → simulation optimization)
examples/             CLI workflows (part1-4, batch, complete, plots)
tests/                45+ tests with book numeric examples
data/                 Sample demand (SKU-A, SKU-B)
documentation/        Guides, FAQ, methodology
excel-templates/      Generated .xlsx workbooks
power-bi/             CSV dataset + M queries + DAX + SETUP.md
.cursor/skills/       Agent skills (Cursor / Claude Code)
.github/workflows/    CI (pytest on 3.11–3.13)
```

---

## Data format

`data/sample_demand.csv`:

```csv
date,product_id,quantity,unit_cost,lead_time_days
2024-01-01,SKU-A,100,50,7
```

Run for a specific SKU:

```bash
python examples/run_part1_part2.py --product SKU-B --lead-time 2 --service-level 0.90 --simulate
```

Parameters:

| Flag | Meaning | Book ref |
|------|---------|----------|
| `--holding-cost` | h (per unit/year) | §2.1 |
| `--order-cost` | k (fixed order cost) | §2.1 |
| `--lead-time` | L (periods) | §3.1, §5.1 |
| `--service-level` | Cycle service level α | §4.1 |
| `--periods-per-year` | Converts weekly data to D | §2.2 |

---

## Key formulas (Part I–II)

**EOQ** (eq. 2.2–2.3):

```
Q* = sqrt(2 k D / h)
C* = sqrt(2 k D h)
```

**Safety stock** (eq. 4.3):

```
Ss = z_alpha * sigma_d * sqrt(tau)
```

- `(s,Q)`: tau = L  
- `(R,S)`: tau = R + L  

**Policies** (Ch. 5):

```
(s,Q):  s = dL + Ss,   Q = Q*
(R,S):  S = dL + dR + Ss
```

---

## Documentation

| Document | Content |
|----------|---------|
| [Getting Started](documentation/GETTING_STARTED.md) | Setup and first run |
| [Methodology](documentation/METHODOLOGY.md) | Models, assumptions, glossary |
| [FAQ](documentation/FAQ.md) | Common questions |

---

## From engine to product ("the AUTO")

The analytical **engine** (Ch. 1–13) now has the chassis around it. The full
chain runs end to end — `examples/run_constrained_plan.py`:

```
data source → forecast (σ_e) → (s,Q)/(R,S) policy → MOQ/case packs → budget fit
src/sources.py   src/forecasting.py   src/policies.py   src/constraints.py
```

- **Pluggable data** (`src/sources.py`): CSV, in-memory DataFrame, or any SQL
  database via `SqlDemandSource` (any DB-API connection — SQLite, Postgres,
  MySQL). New backends just satisfy the `DemandSource` protocol.
- **Forecasting** (`src/forecasting.py`): MA / SES / Croston, exposing σ_e — the
  correct safety-stock dispersion (Vandeput 2021, §4.2.5).
- **Constraints** (`src/constraints.py`): MOQ, case packs, shelf-life caps, and a
  budget allocator that trims safety stock across the portfolio to fit.
- **Web UI** (`webapp/`): a 4-tab planner dashboard (Portfolio · SKU Detail ·
  Budget Planner · Forecast Quality) served by FastAPI over the engine — every
  number is real, no Node/build step. See [webapp/README.md](webapp/README.md).
- **Job-fulfillment layer** (`jobs/`): turn a client's demand file (any schema)
  into client-ready deliverables — Excel + a written report with policy
  recommendations, findings, methodology — with automated QA. Built for real
  supply-chain freelance work. See [jobs/README.md](jobs/README.md).

Live data already works via `SqlDemandSource` (see `examples/run_sql_source.py`).
Still open for a fully turnkey system:

- A vendor-specific ERP/WMS adapter (auth + their schema) on top of `DemandSource`
- Capacity/volume constraints and supplier lead-time variability from live data
- General supply networks (beyond serial GSM)
- Advanced forecasting (seasonality, Holt-Winters, ML models)

## Agent skills (Cursor + Claude Code)

Four skills in `.cursor/skills/` — synced to `~/.claude/skills/`:

| Skill | Chapters |
|-------|----------|
| `vandeput-inventory-optimization` | Overview + decision tree |
| `vandeput-inventory-eoq-policies` | 2–5 |
| `vandeput-inventory-service-cost` | 6–8 |
| `vandeput-inventory-advanced` | 9–13 |

See [.cursor/skills/README.md](.cursor/skills/README.md). Invoke in Claude Code with `/vandeput-inventory-optimization`.

---

## References

- Vandeput, N. (2020). *Inventory Optimization: Models and Simulations*. De Gruyter. ISBN 978-3-11-067391-3
- Vandeput, N. (2021). *Data Science for Supply Chain Forecasting* — for forecast error σ_e (§4.2.5)
- Community notebooks: [fedinb/Inventory-Optimization](https://github.com/fedinb/Inventory-Optimization)

---

## License

MIT — see [LICENSE](LICENSE). Book content and formulas © Nicolas Vandeput / De Gruyter; this repo implements those models independently for learning and practice.
