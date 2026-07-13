# Agent UI — Claude Design Prompt

Copy the block under **"PROMPT (paste this)"** into Claude Design. The sections
below it are reference detail (real API contracts, states, visual direction) so
the generated UI matches the backend that already exists.

---

## PROMPT (paste this)

> Design a **single-page web app** for an **agentic supply-chain assistant** called **Kern** (an Inventory Planner dashboard + agent console). It's a professional B2B tool for supply-chain analysts and consultants. The product already has a working backend; you're designing the front end.
>
> **The core interaction is a command console, not a form.** At the top, a single prominent input where the user types a plain-English brief like *"set up reorder points and safety stock for this warehouse"* or *"what price maximizes profit on this catalog"*, with an optional file drop (CSV/Excel) and an optional client name. The user hits run; the agent routes the request to the right capability, computes a result, and returns a **grounded answer with source citations**.
>
> **Three capabilities** the agent can route to: `inventory_optimization`, `pricing`, `leadership_chain`. Show which one was picked and a confidence score.
>
> **The signature feature: every answer cites its sources.** When the agent recommends, say, an inventory plan, it shows the method used AND the academic source behind it (e.g. *"Reorder Point — Chopra & Meindl, Ch. 11"*, *"Croston's Method — Boylan & Syntetos, Ch. 6.7"*). Make these citations a first-class, beautiful element — small source chips under the result, not a footnote. This is what makes the tool trustworthy.
>
> **Result states to design** (the agent returns one of these): `ok` (summary + downloadable deliverables + citations), `needs_clarification` (the brief was ambiguous — show 2-3 capability options to pick), `needs_data` (a data file is required — prompt for upload), `qa_failed` (computed but failed quality checks — show the issues, no deliverable), `error`. Each state needs a distinct, considered visual treatment.
>
> **Plus a results dashboard** for inventory jobs: KPI cards (plan investment, budget headroom, SKUs at risk, intermittent SKUs), a budget-utilization bar, and a SKU table (SKU, method, forecast/week, Q*, reorder point, safety stock, inventory value, status badge: on-track / review / high-bias). Tabs: Portfolio · SKU Detail · Budget Planner · Forecast Quality.
>
> **Visual direction:** dark, data-dense, precise — think Linear meets a Bloomberg terminal, but warmer. Monospaced numerics for all figures (tabular alignment matters). One confident accent color used semantically (not decoratively): green = healthy, amber = review, red = at-risk/over-budget. Real typographic hierarchy: large bold figures in KPI cards, quiet labels. Intentional spacing rhythm, not uniform padding. Subtle depth via surface elevation and hairline borders, no heavy shadows. Designed hover/focus/active states on every interactive element. The citation chips should feel like a refined, signature detail.
>
> **Do not** ship a generic dashboard template: no default card grid with uniform everything, no stock centered hero, no gray-on-white. This should look like an opinionated, real product a supply-chain pro would trust with money decisions.
>
> Deliver: the command console + result states + the inventory dashboard, responsive (1440 / 1024 / 768 / 375), light and dark both intentional if you do both, otherwise commit to dark.

---

## Reference: real API contract (so the UI is buildable)

The backend (FastAPI) already exists. Design against these shapes.

### `POST /api/jobs` — run the agent

Request (multipart form): `brief` (string, required), `client` (string), `job_type` (string, optional — forces a capability), `params` (JSON string), `file` (CSV/Excel, optional).

Response:
```json
{
  "status": "ok | needs_clarification | needs_data | qa_failed | error",
  "tool": "inventory_optimization | pricing | leadership_chain | null",
  "confidence": 0.0,
  "summary": "Analyzed 8 SKUs; recommended inventory investment $46,616 at 95% service level.",
  "deliverables": { "excel": "...", "report": "...", "csv": "..." },
  "download_urls": { "excel": "/jobs-output/<id>/inventory_plan.xlsx", "report": "/jobs-output/<id>/report.md" },
  "citations": [
    "Economic Order Quantity (EOQ) — chopra-meindl-supply-chain-management.pdf",
    "Forecast the Whole LTD Distribution for Stock Control — boylan-syntetos-intermittent-demand-forecasting.pdf Ch.7-8",
    "Reorder Point — chopra-meindl-supply-chain-management.pdf"
  ],
  "qa_issues": [],
  "clarifications": []
}
```
- `needs_clarification` → `clarifications` lists capability options to choose.
- `needs_data` → prompt for a file upload, re-submit.
- `qa_failed` → render `qa_issues`, no deliverables.

### `GET /api/portfolio` — inventory dashboard data

Returns per-SKU rows. Real fields seen in the current UI: SKU, method (`ses` / `croston`), forecast/week, Q* (order quantity), reorder point, safety stock, inventory value, status (`on track` / `review` / `high bias`). Plus portfolio totals: plan investment, budget cap, budget headroom, SKUs at risk, intermittent count.

### `GET /api/health` → `{ "ok": true, "skus": 8 }`

---

## Citation chip — the signature element

Each `citations[]` string is `"<Concept> — <source_file> [<location>]"`. Parse into:
- **concept** (bold): `Economic Order Quantity (EOQ)`
- **source** (book, de-slugged): `Chopra & Meindl` (strip `.pdf`, title-case)
- **location** (optional): `Ch. 7-8`

Render as a small chip: `📖 EOQ · Chopra & Meindl · Ch.11`. Clickable to expand the full source. These come from a knowledge graph of 16 SCM books — treat them as the proof behind every recommendation.

---

## Capabilities (for routing display + icons)

| Key | Label | Needs file | Produces |
|-----|-------|-----------|----------|
| `inventory_optimization` | Inventory Optimization | yes (demand CSV/Excel) | Excel + report + CSV + dashboard |
| `pricing` | Pricing | yes (price/qty CSV/Excel) | Excel + report + CSV |
| `leadership_chain` | SC Leadership (CHAIN) | no (brief or scores) | radar chart + report |

---

## Current aesthetic to extend (screenshot reference)

The existing dashboard is dark, with: a top bar (`σ` logo, "Inventory Planner", `sample-portfolio · 8 SKUs · (s,Q)/(R,S)`, service level + plan investment on the right), 4 KPI cards, a red budget-utilization bar at 112% of cap, and a clean SKU table with colored status dots. Keep this DNA; the new work is **wrapping it in the agent command console** and **adding the citation chips + result states** so the agent's brain finally has a face.

---

## Out of scope (don't design these yet)

Live ERP/WMS connectors (L4), multi-agent roster (L5), quoting/proposals (L6), full conversational chat history (L7). The command console is a single-shot brief→result, not a chat thread — keep it focused.
