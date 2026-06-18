# Supply Chain Optimization

Python implementation of inventory models from **Nicolas Vandeput**, *Inventory Optimization: Models and Simulations* (De Gruyter, 2020).

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

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

# Full pipeline + optional CSV export for Excel
python examples/run_complete.py --simulate --export output/summary.csv --excel excel-templates/analysis.xlsx

# Pre-built workbook template
python examples/build_excel_workbook.py

# Power BI dataset (CSV star schema)
python examples/build_powerbi_dataset.py --simulate
# See power-bi/SETUP.md for Desktop import
```

Expected output includes `Q*`, reorder point `s`, order-up-to level `S`, safety stock, and simulated service levels.

---

## What is implemented

| Book section | Module | Status |
|--------------|--------|--------|
| Ch. 1 — Inventory policies | `src/policies.py` | `(s,Q)`, `(R,S)` |
| Ch. 2 — EOQ | `src/eoq.py` | ✅ |
| Ch. 3 — Lead time & review period | `src/eoq.py`, `src/policies.py` | ✅ power-of-2 rounding |
| Ch. 4 — Safety stock | `src/safety_stock.py` | ✅ normal demand |
| Ch. 5 — Simulation | `src/simulation.py` | ✅ backorders + lost sales |
| Ch. 6 — Stochastic lead time | `src/risk_period.py`, `src/policies.py` | ✅ |
| Ch. 7 — Fill rate | `src/fill_rate.py` | ✅ |
| Ch. 8 — Cost optimization | `src/cost_optimization.py` | ✅ |
| Ch. 9 — Gamma demand | `src/distributions.py` | ✅ |
| Ch. 10 — Multi-echelon GSM | `src/multi_echelon.py` | ✅ allocation + simulation |
| Ch. 11 — Newsvendor | `src/newsvendor.py` | ✅ |
| Ch. 12 — Histograms / KDE | `src/discrete_demand.py` | ✅ |
| Ch. 13 — Simulation optimization | `src/simulation_opt.py` | ✅ |
| Excel / Power BI | `excel-templates/`, `power-bi/` | ✅ `.xlsx` + CSV dataset |

---

## Project layout

```
src/                  Core models (EOQ, safety stock, policies, simulation)
data/                 Sample demand CSV
examples/             Runnable workflows
tests/                Unit tests aligned with book examples
documentation/        Guides mapped to book chapters
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

## Roadmap

1. **Power BI `.pbix` template** — build report locally; dataset CSVs provided
2. **GSM simulation** — `simulate_serial_gsm` with echelon backorders (§10.5) ✅
3. **Lost sales** — `(R,S)` and `(s,Q)` with `lost_sales=True` (§5.3.2) ✅

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
