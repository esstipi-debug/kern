# MCP server (Phase A go-to-market)

Linchpin exposes 33 of its 37 registered tools to other AI agents over MCP
(Model Context Protocol), mounted at `/mcp` on the same FastAPI app as the
dashboard. This is **read-only analysis only** — no tool that mutates a
client's system of record (e.g. `odoo_replenishment`, or any writeback path
through `src/writeback.py`) is exposed here. See
[linchpin-monetization-plan] in project memory for the billing/publishing plan
this is Phase A of.

## What's exposed

| MCP tool | Underlying capability |
|---|---|
| `linchpin_inventory_optimize` | `inventory_optimization` |
| `linchpin_classify_abc_xyz` | `abc_xyz` |
| `linchpin_newsvendor_order_quantity` | `newsvendor` |
| `linchpin_forecast_demand` | `forecast` |
| `linchpin_financial_kpis` | `financial_kpis` |
| `linchpin_price_optimize` | `pricing` |
| `linchpin_audit_data_quality` | `data_quality` |
| `linchpin_whatif_sensitivity` | `whatif` |
| `linchpin_cost_to_serve` | `cost_to_serve` |
| `linchpin_landed_cost` | `landed_cost` |
| `linchpin_earned_value` | `earned_value` |
| `linchpin_learning_curve` | `learning_curve` |
| `linchpin_excess_obsolete` | `excess_obsolete` |
| `linchpin_markdown_liquidation` | `markdown_liquidation` |
| `linchpin_fefo_expiry` | `fefo` |
| `linchpin_inventory_record_accuracy` | `reconciliation` |
| `linchpin_cycle_count_plan` | `cycle_count` |
| `linchpin_returns_disposition` | `returns` |
| `linchpin_sop_plan` | `sop` |
| `linchpin_ddmrp_buffers` | `ddmrp` |
| `linchpin_multi_echelon_stock` | `multi_echelon` |
| `linchpin_drp_plan` | `drp` |
| `linchpin_simulate_policy` | `simulation` |
| `linchpin_supplier_sourcing` | `sourcing` |
| `linchpin_acceptance_sampling` | `acceptance_sampling` |
| `linchpin_risk_assessment` | `risk` |
| `linchpin_efficiency_benchmark` | `dea` |
| `linchpin_queuing_staffing` | `queuing` |
| `linchpin_job_sequencing` | `scheduling` |
| `linchpin_transport_mode_select` | `transportation` |
| `linchpin_facility_location` | `facility_location` |
| `linchpin_warehouse_slotting` | `slotting` |
| `linchpin_vehicle_routing` | `vehicle_routing` |

Not exposed (4): `odoo_replenishment` and `excel_replenishment` (writeback —
they mutate a client's system of record and stay behind the direct-client
safety plane), `leadership_chain` and `warehouse_layout` (no tabular input —
the rows→CSV bridge has nothing to feed them).

Every tool takes the same shape: `rows` (tabular data as a list of JSON
objects, like CSV rows), `params` (optional tool-specific overrides), and
`client_label` (cosmetic). See each tool's own MCP description (returned by
`tools/list`) for its exact expected columns — they're also documented in
`webapp/mcp_tool_specs.py`, which is the source of truth for the exposed
surface. A few tools have calling nuances worth knowing up front:
`linchpin_vehicle_routing` requires `params.capacity`; `linchpin_fefo_expiry`
needs a `days_to_expiry` column or `expiry_date` + `params.as_of`;
`linchpin_efficiency_benchmark` expects `input_*`/`output_*`-prefixed columns
(or explicit `input_cols`/`output_cols` params); `linchpin_markdown_liquidation`
prices with its default-markdown heuristic over MCP (the elasticity path needs
an on-disk price-history file).

## How a client connects

Streamable HTTP transport, gated by a per-client API key (not
`LINCHPIN_API_KEY` — that's the operator's own dashboard key, unrelated):

```
POST https://<your-deploy>/mcp/
Headers:
  X-API-Key: <the client's issued key>
  Accept: application/json, text/event-stream
  Content-Type: application/json
```

Any standard MCP client (Claude Desktop/Code, or a custom agent using the MCP
SDK) pointed at that URL with that header works. No key -> `401`. Wrong or
revoked key -> `401`. The existing rate limiter (`LINCHPIN_RATE_LIMIT`) applies
here too, but keyed by each client's own identity once authenticated, not by
source IP - one paying client's usage never throttles (or gets throttled by)
another client or a dashboard user that happens to share an egress IP (NAT, a
corporate proxy, ...). Only unauthenticated traffic (missing/invalid key) falls
back to the default per-IP bucket, since there's no client identity yet.

## Issuing keys (operator side)

Phase A billing is manual (a Stripe Payment Link, then you issue a key by
hand — no self-serve signup yet):

```bash
python examples/issue_mcp_key.py issue "Acme Co"      # prints the plaintext key ONCE
python examples/issue_mcp_key.py list                 # client name, issued/active/last-used - never the key itself
python examples/issue_mcp_key.py revoke lpk_xxxxxxxx   # one key
python examples/issue_mcp_key.py revoke-client "Acme Co"  # every key that client holds
```

Keys are stored hashed (SHA-256) in a local SQLite file, `data/mcp_keys.sqlite3`
by default (override with `$LINCHPIN_MCP_KEYS_PATH`) — gitignored, never
committed. The plaintext is shown exactly once, at issuance; there is no way to
recover it from the store afterward, same as a GitHub/Stripe-style token.

If you deploy with `--workers N` (see [DEPLOYMENT.md](DEPLOYMENT.md)), the key
store is safe to share across those worker processes — SQLite serializes
writers at the file level, the same guarantee `src/writeback_store.py`'s audit
ledger already relies on.

## What's NOT here yet

- No self-serve signup or automated billing (Phase B: Stripe metered
  subscription, triggered once there are real paying clients).
- No pay-per-call/x402 support (Phase C, only if real agent-to-agent payment
  demand shows up).
- No writeback tools. If a client needs `odoo_replenishment` or any other
  tool that touches their live systems, that's a direct-client relationship
  outside this MCP surface, not something to add here.
