# scm_agent — the orchestrator

One entry point that turns a free-form brief (+ optional data) into a finished
deliverable, routing to the right capability.

## Capabilities

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
