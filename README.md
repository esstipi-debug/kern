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

This repo is the analytical **engine**. `src/forecasting.py` adds the demand
front-end (history → forecast → σ_e → policy; see `examples/run_forecast_to_policy.py`).
What still separates it from a turnkey planning system:

- Live data integration (ERP/WMS connector instead of CSV loads)
- Business constraints (budget, warehouse capacity, MOQ, shelf-life)
- General supply networks (beyond serial GSM)
- Policies driven end-to-end from KDE/discrete PMF (Ch. 12 → 5)
- Advanced forecasting (seasonality, trend/Holt-Winters, ML models)

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
