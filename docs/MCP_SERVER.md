# MCP server (Phase A go-to-market)

Kern exposes 39 of its 57 registered tools to other AI agents over MCP
(Model Context Protocol), mounted at `/mcp` on the same FastAPI app as the
dashboard. This is **read-only analysis only** — no tool that mutates a
client's system of record (e.g. `odoo_replenishment`, or any writeback path
through `nosilo_core.writeback`) is exposed here. See
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
| `linchpin_flow_map` | `flow_map` |
| `linchpin_org_friction_audit` | `org_friction_audit` |
| `linchpin_procedure_audit` | `procedure_audit` |
| `kern_iso_sop_audit` | `procedure_audit` (alias; ISO SOP-register observation) |
| `kern_audit_evidence` | `audit_evidence` (MUS + GL tie-out workpaper) |
| `kern_iso_scor_audit` | `scor_iso_audit` (SCOR/ISO evidence pack; not a certificate) |

`org_friction_audit` runs here with three of its four detectors: the fourth
(Goodhart / target-ratcheting) needs a second target-vs-actual history file,
passed to the direct tool as `params["history_path"]`. This surface is
anonymous and remote, and `params` is handed straight to the job, so every
`*_path` override is dropped before it gets there
(`strip_filesystem_params` in `webapp/mcp_server.py`) — otherwise a caller
could name any file on the server and read back its existence and column
headers through the job's own "missing required column(s)" error text. Use
the direct tool (or `examples/run_org_friction.py --history`) when you need
the ratchet detector.

Deliberately not exposed: `odoo_replenishment` and `excel_replenishment`
(writeback — they mutate a client's system of record and stay behind the
direct-client safety plane), `leadership_chain` (no tabular input — the
rows→CSV bridge has nothing to feed it), `warehouse_layout` (a
params-driven 3D layout generator that ignores rows entirely; its
deliverable is an HTML viewer, not a data analysis), `procedure_draft`
(same rule as `warehouse_layout` — params-driven, requires_data=False,
ignores rows; `procedure_audit` IS exposed above, since it reads a
client's tabular procedure register). `iso_ops` is also off this surface:
the plant ficha is JSON/params (HTTP `POST /api/detectar` already covers
it) and a conformity-adjacent claim does not belong on the anonymous MCP
rows→CSV bridge. `flow_map`'s own
deliverable also includes a self-contained HTML sankey map alongside the
CSV, but unlike `warehouse_layout`, its *input* is tabular movement rows
and its primary output is a data analysis (measured transit per lane),
with the HTML map as an extra visualization, not the whole deliverable —
that distinction is why it is exposed here and `warehouse_layout` is not.
See `webapp/mcp_tool_specs.py`'s module docstring for the full reasoning.

The remaining registered tools not in the table above (e.g. `digital_twin`,
`disruption_scan`, `hat_tension`, `hat_settlement`, `launch_readiness`,
`network_design`, `price_intelligence`, `price_watch`,
`supplier_management`) are not deliberately excluded by the reasoning
above — they simply have not been added to `webapp/mcp_tool_specs.py` yet.
Adding one is a one-spec change; see that file's module docstring.

Every tool takes the same shape: `rows` (tabular data as a list of JSON
objects, like CSV rows), `params` (optional tool-specific overrides), and
`client_label` (cosmetic). See each tool's own MCP description (returned by
`tools/list`) for its exact expected columns — they're also documented in
`webapp/mcp_tool_specs.py`, which is the source of truth for the exposed
surface. A few tools have calling nuances worth knowing up front:
`linchpin_vehicle_routing` requires `params.capacity`; `kern_audit_evidence`
requires `params.tolerable_misstatement` (auditor judgment; never derived);
`linchpin_fefo_expiry`
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
- `intake_scan` — deliberately excluded, not just not-yet-added. It takes a
  directory path and reads every readable file inside it; exposing that on
  the anonymous remote MCP surface would hand any caller an arbitrary-file-read
  primitive (point it at any path the server process can see, get back what's
  in it). Same class of risk the `org_friction` `*_path` params guard closes
  on its own MCP bridge — a directory/path argument on an anonymous endpoint
  is a path-traversal surface until it is explicitly scoped and validated,
  which this tool has not been. Adding `intake_scan` here would need that
  guard first, not just an `MCPToolSpec` entry.
- `import_case` — same class as `intake_scan`: it reads a caller-supplied
  folder or JSON bundle of extracted shipment documents. Keep it off MCP
  until the path is scoped the same way. The Chile DIN pack is a broker
  handoff, not a remote filing API.
