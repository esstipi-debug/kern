# Job-fulfillment layer (`jobs/`)

Packages the engine to fulfill real supply-chain freelance work (e.g. Upwork
"set up reorder points / safety stock" jobs). You bring a client's demand file
and the parameters; this produces client-ready deliverables.

## The model (human-in-the-loop)

> You win the job and deliver; the agents do the heavy analytical lifting; QA is
> automated, with your final review before sending.

Fully autonomous bidding / client communication is **against Upwork's ToS** and
out of scope. This layer automates the *analysis and deliverable*, not the
client relationship.

## Pipeline

```
client file (CSV/Excel, any columns)
  → intake.py            detect columns + normalize to canonical schema (weekly)
  → inventory_optimization.run()   forecast (σₑ) → (s,Q)/(R,S) policy → budget allocation
  → qa.verify()          check the numbers (investment math, feasibility, ranges)
  → deliverables.write_all()   Excel workbook + Markdown report + CSV
```

## Run

```bash
python examples/run_inventory_job.py --data client_demand.csv --budget 50000 --client "Acme Co"
# CSV or Excel input; columns auto-detected (date/sku/qty/price/lead-time aliases)
python examples/run_inventory_job.py --data orders.xlsx --service-level 0.97 --period D
```

Outputs to `deliverables/`: `inventory_plan.xlsx`, `report.md`, `summary.csv`.
If QA fails, nothing is written and the issues are printed — you never ship bad numbers.

## What each job type maps to

| Upwork job phrasing | Playbook | Deliverable |
|---|---|---|
| "set up reorder points / safety stock" | `inventory_optimization` | policy per SKU + report |
| "EOQ / how much to order" | `inventory_optimization` | Q*, order-up-to |
| "fit my inventory to a budget" | `inventory_optimization` (`--budget`) | scaled allocation + feasibility |

See [SAMPLE_REPORT.md](SAMPLE_REPORT.md) for an example deliverable.

## Intake — handling any client schema

`intake.detect_columns` maps the client's headers to `date, product_id,
quantity, unit_cost, lead_time_days` by alias (e.g. `InvoiceDate`/`Order Date` →
`date`, `SKU`/`StockCode`/`item_id` → `product_id`, `Qty`/`sales`/`demand` →
`quantity`). Missing cost/lead-time fall back to defaults. Pass `overrides` or
`--period` when auto-detection needs help. This is the same seam used for
Kaggle-style datasets.

## Extending: add a new playbook

1. Add `jobs/<job>.py` that consumes canonical demand and returns a report dataclass.
2. Add QA invariants in `jobs/qa.py`.
3. Add a deliverable writer (or reuse `deliverables.py`).
4. Wire a CLI in `examples/` and tests in `tests/test_jobs.py`.

The engine (`src/`) never changes — playbooks compose it.
