# Upwork / Fiverr positioning — Linchpin

Working draft for landing inventory + supply-chain optimization gigs. Grounded
only in what Linchpin actually does today (verified against the codebase, not
aspirational) — see [[linchpin-verified-audit]] in project memory before
promising anything not listed here.

## Profile headline (pick one, A/B if the platform allows it)

- "Inventory optimization that shows its work — EOQ, safety stock, ABC-XYZ, forecasting, delivered as a report your finance team will actually trust"
- "I turn your sales history into a reorder policy, not a black-box number"
- "Supply-chain analyst + AI agent — 34 inventory/SCM models, one deliverable, same day"

## Bio / summary

I build and operate **Linchpin**, an AI-powered inventory and supply-chain
analysis engine — not a chatbot wrapper, a deterministic Python engine
(forecasting, EOQ, safety stock, ABC-XYZ, DDMRP, multi-echelon, pricing,
facility location, and 25+ more models) grounded in 24 published SCM
textbooks and papers, so every number in the deliverable traces back to a
formula and a citation, not a guess.

What you get: send me your sales/demand history (a CSV export from your ERP
or spreadsheet is fine), and within the same engagement you get back a
client-ready Excel workbook + written report with your reorder points, safety
stock targets, ABC-XYZ classification, and a forecast — with the methodology
shown, not hidden.

I also build direct **Odoo integration** — I can read your product/stock/
sales data straight from Odoo and (with your explicit approval on anything
that writes back) update reorder points or stage draft purchase orders,
through a safety-audited approval flow, not a blind script.

## Service packages

Map to Linchpin's actual tool registry — quote against these, not generic
"consulting hours":

| Package | What's included | Tools used |
|---|---|---|
| **Inventory Health Check** (entry point, cheapest, fastest) | ABC-XYZ classification + data-quality audit on your product master | `abc_xyz`, `data_quality` |
| **Demand & Reorder Policy** (flagship) | Forecast + safety stock + reorder point/quantity per SKU, budget-fit | `forecast`, `inventory_optimization`, `newsvendor`, `multi_echelon` |
| **Procurement & Sourcing** | Supplier scoring, landed cost, sampling plans | `sourcing`, `landed_cost`, `acceptance_sampling` |
| **Warehouse & Logistics** | Facility location, slotting, transportation routing | `facility_location`, `slotting`, `transportation`, `warehouse_layout` |
| **Finance & Pricing** | KPI rollup (turns, GMROI, DIO), price elasticity | `financial_kpis`, `pricing`, `cost_to_serve` |
| **Odoo Connector Setup** | Live read + safe-staged writeback into your Odoo instance | `odoo_replenishment` |

Pricing anchor (already calibrated against real comparables, not a
consulting-hours guess — see [[linchpin-monetization-plan]] §4 in project
memory for the sourcing): pay-per-report **$9-29** for a single one-off
analysis (low-commitment trial), fixed-scope package **$500-3,000** for a
multi-tool engagement with a real client dataset, ongoing/subscription work
quoted direct once there's a track record.

## Proposal template

Use this as a starting skeleton, always customize the first two lines to
reference something specific from the actual job post (never send this
verbatim — a generic-sounding proposal is the #1 way to lose to a competing
bid):

```
Hi [name] — [one sentence referencing something specific from their post:
their product category, their stated pain point, a number they mentioned].

I run Linchpin, an inventory-optimization engine covering [the 1-2 relevant
capabilities from the package table above]. For a [SKU count]-SKU catalog
like yours, here's what I'd actually deliver: [1-2 concrete outputs, e.g.
"a reorder point + safety stock target per SKU, plus an ABC-XYZ 9-cell
classification so you know which SKUs to review weekly vs quarterly"].

Every number comes with the formula behind it — [safety stock method /
forecast method / whatever's relevant] — so your team isn't taking a black
box's word for it.

Fastest path to a quote: send me a CSV export of [X months] of sales/demand
history and I'll turn around a sample analysis on your real data before you
commit to anything.

[Your name]
```

## Portfolio proof points (only claim what's actually true today)

- Deterministic Python engine, 34 agent-routable tools, grounded in a
  24-source curated knowledge graph (not just "AI-powered" marketing copy —
  can show the actual citation trail on request).
- Live, production-deployed MCP server other AI agents can already call
  (`https://linchpin.fly.dev`) — demonstrates this isn't a one-off script.
- Sample client-ready deliverable: `jobs/SAMPLE_REPORT.md` (renders a real
  worked analysis end to end) and `jobs/SAMPLE_PRICING_REPORT.md`.
- Dashboard screenshots: `docs/assets/dashboard-*.png` — the same numbers a
  client sees, not a mockup.
- Odoo integration is real and tested (not vaporware): reads live
  product/stock/sales data, writes back only through an approval-gated,
  reversible staging plane — worth mentioning explicitly to prospects who've
  been burned by a script that touched their ERP without asking.

## What NOT to claim yet

- No live Shopify/Amazon SP-API connectors (planned, not built) — don't
  promise these to a prospect who asks.
- No self-serve signup or dashboard for the client to log into themselves —
  every engagement today is still hands-on/direct, not a SaaS product a
  client operates unassisted.
- No track record / case studies with a real named client yet (first 1-3
  clients are the ones who'll create these) — don't fabricate a testimonial
  or a "trusted by X companies" claim.
