# Getting Started

**Run inventory models from Vandeput (2020) in under 10 minutes.**

Based on: *Inventory Optimization: Models and Simulations* — Nicolas Vandeput, De Gruyter 2020.

---

## Prerequisites

- Python 3.10+
- pip

Optional: Excel or Power BI for viewing exported results (not required for Part I–II).

---

## 1. Install

```bash
git clone https://github.com/esstipi-debug/kern.git
cd kern
pip install -r requirements.txt
```

---

## 2. Run the example

```bash
python examples/run_part1_part2.py --simulate
```

This will:

1. Load `data/sample_demand.csv` for `SKU-A`
2. Compute **EOQ** (Chapter 2)
3. Build **(s,Q)** and **(R,S)** policies with safety stock (Chapters 4–5)
4. **Simulate** inventory levels and cycle service level (Chapter 5.3)

Part III and IV:

```bash
python examples/run_part3.py    # fill rate + cost optimization (Ch. 7-8)
python examples/run_part4.py    # gamma, GSM, newsvendor, KDE, sim-opt (Ch. 9-13)
python examples/build_powerbi_dataset.py --simulate   # Power BI CSVs
```

See [power-bi/SETUP.md](../power-bi/SETUP.md) for Power BI Desktop import.

---

## 3. Use your data

CSV columns:

```csv
date,product_id,quantity,unit_cost,lead_time_days
2024-01-01,SKU-A,100,50,7
```

Run:

```bash
python examples/run_part1_part2.py \
  --data path/to/demand.csv \
  --product YOUR-SKU \
  --holding-cost 1.75 \
  --order-cost 50 \
  --lead-time 2 \
  --service-level 0.95 \
  --simulate
```

| Parameter | Book symbol | Typical source |
|-----------|-------------|----------------|
| `--holding-cost` | h | Finance / warehouse (§2.1) |
| `--order-cost` | k | Procurement (§2.1) |
| `--lead-time` | L | Supplier master data (§3.1) |
| `--service-level` | α | Business target (§4.1) |

---

## 4. Interpret results

### EOQ output

- **Q*** — economic order quantity (eq. 2.2)
- **Optimal yearly cost** — minimum of holding + ordering (eq. 2.3)

### (s, Q) — continuous review

- **s** = demand over lead time + safety stock
- Order **Q** whenever net inventory ≤ **s**

### (R, S) — periodic review

- **S** = order-up-to level (not average on-hand!)
- Review every **R** periods; order `S − net inventory`
- Safety stock uses risk period **R + L** (§5.1.2)

### Simulation

Compare **simulated cycle service level** to your target α. Large gaps often mean:

- Demand is not normal (see Ch. 9)
- Review period omitted from safety stock (§5.1)
- Confusing fill rate with cycle service level (§4.1, Ch. 7)

---

## 5. Next steps

| Goal | Read |
|------|------|
| Formulas and assumptions | [METHODOLOGY.md](METHODOLOGY.md) |
| Common pitfalls | [FAQ.md](FAQ.md) |
| Fill rate, gamma demand, newsvendor | Ch. 7–13 — `examples/run_part3.py`, `run_part4.py` |

Official book Python snippets: [supchains.com/resources-invopt](https://supchains.com/resources-invopt) (password: `SupChains-IO`).

---

## 6. Run tests

```bash
pytest
```

Tests include the book’s EOQ numeric example (§2.2.4: D=1000, k=50, h=1.75 → Q*≈239).
