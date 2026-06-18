# Power BI Dashboards

Reporting layer on Python outputs — **Vandeput (2020), Ch. 5 visualization**.

## Quick start

```bash
python examples/build_powerbi_dataset.py --simulate
```

Then follow **[SETUP.md](SETUP.md)** to import CSVs into Power BI Desktop.

## Dataset tables

| CSV | Content | Book ref |
|-----|---------|----------|
| `demand_history` | Weekly demand by SKU | — |
| `product_summary` | μ, σ, distribution | Ch. 4, 9 |
| `policies` | EOQ, (s,Q), (R,S) | Ch. 2, 5 |
| `simulation` | Cycle SL, on-hand | §5.3 |
| `cost_optimization` | Optimal R, costs | Ch. 8 |
| `fill_rate` | β target vs α | Ch. 7 |
| `gsm_nodes` | Serial GSM allocation | Ch. 10 |
| `newsvendor` | Muffins example | Ch. 11 |
| `parameters` | Global inputs | — |

## Report pages (recommended)

See [SETUP.md](SETUP.md) §5 — demand, policies, simulation, cost/fill rate, GSM.

## Assets

- `queries/` — Power Query M (update `RootFolder` path)
- `measures.dax` — DAX measure library

## Note on `.pbix`

Report files are not committed (binary, local paths). Generate data with Python, build visuals once in Desktop, save your `.pbix` locally.
