# Power BI Setup — Vandeput Inventory Optimization

Connect Power BI Desktop to Python-generated CSVs. Computation stays in Python; Power BI is the **reporting layer** (book approach).

---

## 1. Generate the dataset

```bash
pip install -r requirements.txt
python examples/build_powerbi_dataset.py --simulate
```

Output: `power-bi/dataset/` (9 CSV files).

Or from the full pipeline:

```bash
python examples/run_complete.py --simulate --powerbi power-bi/dataset
```

---

## 2. Import in Power BI Desktop

### Option A — Get Data from folder (fastest)

1. Open **Power BI Desktop**
2. **Get data** → **Text/CSV**
3. Select all files in `power-bi/dataset/`
4. Load each table (or use **Combine** for demand only)

### Option B — Power Query M (refreshable)

1. **Transform data** → **New source** → **Blank query** → **Advanced editor**
2. Paste queries from `power-bi/queries/*.pq`
3. Update `RootFolder` to your absolute path to `power-bi/dataset`

---

## 3. Model relationships

Create relationships in **Model view**:

| From | To | Column |
|------|-----|--------|
| `demand_history[product_id]` | `product_summary[product_id]` | Many-to-one |
| `policies[product_id]` | `product_summary[product_id]` | Many-to-one |
| `simulation[product_id]` | `product_summary[product_id]` | Many-to-one |
| `cost_optimization[product_id]` | `product_summary[product_id]` | Many-to-one |
| `fill_rate[product_id]` | `product_summary[product_id]` | Many-to-one |
| `gsm_nodes[product_id]` | `product_summary[product_id]` | Many-to-one |

`parameters` and `newsvendor` are standalone (no product key).

---

## 4. DAX measures

Copy measures from [`measures.dax`](measures.dax) into **Report view** → **New measure**.

Key comparisons (book §5.3, §7.3):

- **Simulated cycle SL** vs **target service_level** (parameters table)
- **Fill rate target** vs **cycle SL at fill target** (fill_rate table)
- **Mean on-hand** vs **S** — S is not average on-hand (§3.3)

---

## 5. Suggested report pages

### Page 1 — Demand history
- Line chart: `date` × `quantity` by `product_id`
- Card: `Total Demand`, `Avg Weekly Demand`

### Page 2 — Policy parameters
- Table: `policies` (Q, s, S, R, safety_stock)
- Matrix: policy type × product

### Page 3 — Simulation health
- Gauge: `simulated_cycle_sl` vs `parameters[service_level]`
- Bar: `mean_on_hand` vs `policies[S]` (show gap — book §5.2 zones)

### Page 4 — Cost & fill rate
- Bar: `cost_optimization` cost components (holding / ordering / backorder)
- Cards: `Target Fill Rate %`, `Cycle SL at Fill Target %`

### Page 5 — Multi-echelon GSM
- Table: `gsm_nodes` (node, x_tau, Ss, holding cost)
- Card: `total_case_cost`

---

## 6. Refresh workflow

After re-running Python:

```bash
python examples/build_powerbi_dataset.py --simulate
```

In Power BI: **Home** → **Refresh** (CSV folder source refreshes automatically if path unchanged).

For production: publish dataset to Power BI Service and schedule refresh on a shared folder or Azure Data Lake copy of the CSVs.

---

## Files

| Path | Purpose |
|------|---------|
| `dataset/*.csv` | Generated model output |
| `queries/*.pq` | Power Query M templates |
| `measures.dax` | DAX measure library |
| [README.md](README.md) | Overview |

---

## Limitations

- No `.pbix` in repo (binary; paths are machine-specific). Build your report once locally.
- Monte Carlo details stay in Python; Power BI shows aggregated simulation metrics only.
- GSM example uses book parameters (L=[4,3,2], D=100, σ=25) — not per-SKU from CSV.
