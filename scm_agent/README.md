# scm_agent — Kern's orchestrator

One entry point that turns a free-form brief (+ optional data) into a finished
deliverable, routing to the right capability.

## Capabilities

**41 registered tools** — source of truth is `build_default_registry()` in
[`tools.py`](tools.py); run it yourself rather than trusting a static count here,
since it changes with every new `feat: wire ... as an agent tool` PR. Every tool
follows the exact same `prepare → run → qa → deliver` contract; adding one is a
single `register()` call, no routing edits.

| Area | Tools |
|---|---|
| Demand & classification | `abc_xyz` · `forecast` · `whatif` |
| Inventory & replenishment | `inventory_optimization` · `newsvendor` · `multi_echelon` · `ddmrp` · `simulation` · `drp` · `odoo_replenishment` · `excel_replenishment` |
| Inventory control & health | `cycle_count` · `reconciliation` · `excess_obsolete` · `markdown_liquidation` · `fefo` · `data_quality` |
| Procurement & sourcing | `sourcing` · `landed_cost` · `acceptance_sampling` |
| Network & logistics | `facility_location` · `transportation` · `vehicle_routing` · `warehouse_layout` · `slotting` · `queuing` · `scheduling` |
| Pricing & finance | `pricing` · `price_intelligence` · `financial_kpis` · `cost_to_serve` · `learning_curve` |
| Returns, risk & benchmarking | `returns` · `risk` · `dea` |
| Planning cadence & projects | `sop` · `earned_value` |
| Leadership | `leadership_chain` |

Three worked examples, one per input/deliverable shape:

| Key | Type | Input | Deliverable |
|---|---|---|---|
| `inventory_optimization` | quantitative | demand CSV/Excel | Excel + report + CSV |
| `pricing` | quantitative | price/quantity CSV/Excel | Excel + report + CSV |
| `leadership_chain` | qualitative | brief / `scores` | radar chart PNG + active report |

## CLI

```bash
py examples/run_agent.py --brief "set up reorder points" --data data/sample_demand_portfolio.csv
py examples/run_agent.py --brief "what price maximizes profit" --data data/sample_pricing.csv
py examples/run_agent.py --brief "evaluate our SC leadership" --scores "3 2 3 1 1" --name "Team"
```

## HTTP

`POST /api/jobs` (multipart: `brief`, optional `file`, `params` JSON) → `JobResult`
JSON + `download_urls`. Needs the `web` extra (`pip install -e ".[web]"`).

## LLM (optional)

Set `ANTHROPIC_API_KEY` and install the `llm` extra to enable Claude-assisted intent
parsing and narrative polish. Without it the deterministic core runs unchanged.

## Design

Registry-based: each capability is a `Tool` with four stages
(`prepare → run → qa → deliver`) the `Orchestrator` drives, enforcing
"QA fails ⇒ no deliverable" centrally. Spec:
`docs/superpowers/specs/2026-06-21-scm-agent-orchestrator-design.md`.

The `leadership_chain` capability wraps the CHAIN model. *Síntesis original
inspirada en el modelo CHAIN de "From Source to Sold" (Palamariu & Alicke, 2022);
no reproduce el texto del libro.*
