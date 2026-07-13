# UI Design Brief — Kern

> Instructions for building the front-end ("the cabin") on top of the engine,
> using Claude (claude.ai artifacts or Claude Code). This brief is written to be
> pasted **directly** into a prompt — it carries the data shapes, screens, visual
> direction, and API contract Claude needs to produce something specific, not a
> generic template.

---

## 0. TL;DR — pick a path

- **Fast mockup (no backend):** copy the prompt in [§7A](#7a--claudeai-artifact-prompt) into a new claude.ai chat → interactive single-file React artifact with mock data.
- **Real app:** run the scaffold prompt in [§7B](#7b--claude-code-scaffold-prompt) inside Claude Code → React app wired to a thin FastAPI backend that calls `src/`.

If you have the design skills installed, invoke `/impeccable` or the
`ui-ux-pro-max` skill first and feed it this brief — they raise the visual bar.

---

## 1. What you're building

A **planner-facing inventory dashboard** — the visual cabin over the AUTO chain:

```
data source → forecast (σ_e) → (s,Q)/(R,S) policy → MOQ/budget constraints
```

The user pulls demand (CSV/DataFrame/SQL), and the UI shows, per SKU and across
the portfolio: the **forecast**, the recommended **inventory policy**, and how the
plan **fits a budget**. It is a decision-support tool, not a transactional system —
optimize for *clarity of recommendation* and *trust* (show the math behind a number).

---

## 2. Who uses it (persona)

**Inventory / supply planner.** Spends the day deciding *how much to order and when*
for dozens–hundreds of SKUs. Numerate, time-pressured, skeptical of black boxes.
Needs to (a) spot SKUs at risk, (b) understand *why* a number is recommended,
(c) run "what if I change the service level / budget" and see the impact.

Design implication: **data-dense but legible**, every recommendation traceable to
its inputs, fast what-if interaction. No marketing fluff, no oversized hero.

---

## 3. The data it shows (engine contract)

These are the **real return shapes** from the engine. Use them verbatim as the UI
data model. Example values are the actual sample-data output.

### Per-SKU forecast — `ForecastResult` (`src/forecasting.py`)
```json
{
  "method": "ses",
  "forecast": 95.78,
  "demand_mean": 96.1,
  "demand_std": 24.0,
  "error_std": 24.04,
  "bias": -0.01,
  "mae": 19.24,
  "n_periods": 52,
  "is_intermittent": false
}
```

### Per-SKU policy — `PolicyResult` (`src/policies.py`)
```json
{
  "policy": "(s, Q)",
  "order_quantity": 199.6,
  "reorder_point": 135.0,
  "order_up_to_level": null,
  "review_period": null,
  "safety_stock": { "safety_stock": 39.5, "service_level_factor": 1.645, "cycle_service_level": 0.95 },
  "mean_demand_risk_period": 95.5,
  "demand_std_risk_period": 24.0
}
```

### Product metadata — `ProductMetadata` (`src/data_loader.py`)
```json
{ "product_id": "SKU-A", "mean_demand_per_period": 96.1, "demand_std_per_period": 24.0,
  "periods": 52, "mean_unit_cost": 50.0, "lead_time_periods": 1.0 }
```

### Portfolio budget plan — `BudgetAllocation` (`src/constraints.py`)
```json
{
  "feasible": true,
  "safety_stock_scale": 1.0,
  "requested_investment": 15158,
  "final_investment": 15158,
  "items": [
    { "product_id": "SKU-A", "order_quantity": 199.6, "safety_stock": 39.5, "unit_cost": 50, "investment": 6965 },
    { "product_id": "SKU-B", "order_quantity": 455.2, "safety_stock": 100.2, "unit_cost": 10, "investment": 8193 }
  ]
}
```

> When `feasible` is `false`, cycle stock alone exceeds budget — surface this as a
> hard warning, not a silent clamp.

---

## 4. Screens

### 4.1 Portfolio Overview (home)
- **Purpose:** triage. Which SKUs need attention, is the plan within budget.
- **Key elements:** dense SKU table (id, forecast/period, Q*, reorder point,
  safety stock, inventory value, status chip); a budget gauge (requested vs cap);
  a few KPI stats (total investment, # SKUs over/under, # intermittent).
- **Status logic:** color a SKU by health — e.g. green ok, amber low service-level
  headroom, red infeasible-under-budget or `is_intermittent` needing review.
- **Primary action:** open a SKU; adjust global budget/service level.

### 4.2 SKU Detail
- **Purpose:** justify the recommendation; let the planner trust it.
- **Key elements:** **demand history line chart with the forecast overlaid** and a
  ±σ_e band; the policy parameters as labeled stats (Q*, s or S, safety stock) each
  with a tooltip citing the formula/book section; forecast quality (method, bias, MAE);
  a what-if panel (sliders for service level, lead time, order cost) that re-renders s/S.
- **Primary action:** change assumptions → see policy update live.

### 4.3 Budget & Constraints Planner
- **Purpose:** fit the whole portfolio under a budget and order rules.
- **Key elements:** a **budget slider**; on change, re-run `allocate_under_budget`
  and animate each SKU's safety stock shrinking; MOQ / case-pack / shelf-life inputs;
  a clear feasible/infeasible banner with the cycle-stock floor.
- **Primary action:** commit a plan; export (CSV — reuse `src/export.py`).

### 4.4 Forecast Quality
- **Purpose:** trust the forecasts. Bias/MAE per SKU, intermittent flagging.
- **Key elements:** sortable table or small-multiples; highlight high-bias SKUs and
  ones auto-routed to Croston.

---

## 5. Visual direction (anti-template — read this)

**Pick a specific direction. Do NOT ship a default shadcn card grid on gray-on-white.**

**Primary direction: "Operations control-room."** Calm, data-dense, high signal-to-noise.
Think Linear/Vercel-dashboard precision crossed with a trading terminal's information
density — but quiet, not loud. Color is **semantic** (stock health, budget status),
never decorative. Hierarchy comes from **scale + weight + a single accent**, not from
boxes everywhere.

| Token | Light | Dark (default for this tool) |
|---|---|---|
| Surface | `oklch(98% 0 0)` | `oklch(20% 0.01 260)` |
| Elevated | `oklch(100% 0 0)` | `oklch(24% 0.012 260)` |
| Text | `oklch(20% 0 0)` | `oklch(94% 0 0)` |
| Muted text | `oklch(55% 0 0)` | `oklch(66% 0 0)` |
| Accent | `oklch(62% 0.17 250)` | `oklch(70% 0.16 250)` |
| OK / green | `oklch(64% 0.16 150)` | `oklch(72% 0.16 150)` |
| Warn / amber | `oklch(72% 0.16 80)` | `oklch(80% 0.15 85)` |
| Risk / red | `oklch(60% 0.20 25)` | `oklch(68% 0.19 25)` |

- **Typography:** UI in a precise grotesque (Inter / Geist). **All numbers in a
  monospaced tabular font** (e.g. `IBM Plex Mono`, `font-variant-numeric: tabular-nums`)
  so columns align — this is a numbers tool. Strong scale contrast: small dense labels,
  large confident KPI figures.
- **Layout:** a real grid / bento, not uniform padding everywhere. Charts are first-class
  citizens of the design system, not afterthoughts. Use overlap/elevation sparingly for
  depth.
- **Motion:** purposeful only — animate the safety-stock bars when the budget changes,
  cross-fade chart series. Compositor-friendly props (`transform`, `opacity`). Respect
  `prefers-reduced-motion`.
- **States:** design loading (skeletons), empty (no SKUs / no data source), and error
  (infeasible budget, query failed) — not just the happy path.

**Banned:** centered gradient hero + CTA, identical cards with uniform shadow, decorative
accent-color-only styling, charts in default library colors.

---

## 6. Tech stack

- **Frontend:** React + Vite + TypeScript, Tailwind CSS, shadcn/ui (as a base to
  *customize*, not ship raw), **Recharts** for charts.
- **State:** TanStack Query for server state; URL params for active SKU / budget /
  service level (shareable views).
- **Backend (real app):** thin **FastAPI** wrapping `src/` — no business logic in the UI.
- **Budgets:** landing/app page targets from the global web rules (JS < 300kb gzip,
  LCP < 2.5s, CLS < 0.1).

---

## 7. Prompts for Claude

### 7A — claude.ai artifact prompt
Paste this into a new claude.ai chat for an interactive mockup:

```
Build an interactive React artifact: an inventory-planning dashboard for a supply
chain optimization engine. Use the "Operations control-room" direction — dark,
data-dense, calm, semantic color, tabular-numeric monospaced figures, strong scale
hierarchy. NOT a generic shadcn card grid.

Screens (tabs): (1) Portfolio Overview — dense SKU table + budget gauge + KPIs;
(2) SKU Detail — demand history line chart with forecast overlay and ±sigma_e band,
policy params (Q*, reorder point, safety stock) with formula tooltips, and what-if
sliders (service level, lead time) that update the policy live.

Use this data model and mock 8 SKUs from it: [paste §3 JSON shapes]. Color SKUs by
health (ok/warn/risk). Include loading, empty, and infeasible-budget states. Respect
prefers-reduced-motion. Charts via Recharts with a custom palette.
```

### 7B — Claude Code scaffold prompt
Run this inside Claude Code in the repo root:

```
Scaffold a web UI for this inventory engine in a new ui/ folder.

Backend: a thin FastAPI app (api/) that imports from src/ and exposes the endpoints
in documentation/UI_DESIGN_BRIEF.md §8. No business logic in the API — only call the
engine (sources -> forecasting -> policies -> constraints) and serialize the dataclasses.

Frontend: React + Vite + TS + Tailwind + shadcn/ui + Recharts in ui/, implementing the
4 screens and the "Operations control-room" visual direction from §5. Use TanStack Query
against the FastAPI endpoints and put active SKU / budget / service level in the URL.

Follow the design tokens in §5, write the screen states (loading/empty/error), and add a
Playwright smoke test that loads the overview and opens a SKU. Keep components small.
```

---

## 8. API contract (FastAPI backend)

Endpoints the backend should expose (all read-only; the engine is pure):

| Method | Path | Returns |
|---|---|---|
| `GET` | `/api/products` | `[ProductMetadata]` for the active data source |
| `GET` | `/api/products/{id}/forecast?method=auto` | `ForecastResult` + demand history array |
| `GET` | `/api/products/{id}/policy?service_level=0.95&lead_time=...` | `PolicyResult` |
| `GET` | `/api/plan?budget=20000&service_level=0.95&moq=0` | `BudgetAllocation` |
| `POST` | `/api/source` | switch source (csv path / sql conn) — returns product list |

Serialize dataclasses with `dataclasses.asdict`. Validate query params (service level
in (0,1), budget ≥ 0) and return 400 with a clear message — never a silent default.

---

## 9. Quality checklist (before "done")

- [ ] Doesn't look like a default Tailwind/shadcn template (see §5 banned list)
- [ ] Every recommended number is traceable to its inputs (tooltip / detail)
- [ ] Numbers are tabular-aligned and monospaced
- [ ] Loading, empty, and error/infeasible states all designed
- [ ] Keyboard navigable; visible focus states; AA contrast in dark mode
- [ ] `prefers-reduced-motion` honored; motion only on `transform`/`opacity`
- [ ] Charts have explicit dimensions (no layout shift); custom palette
- [ ] Budget infeasibility surfaced as a warning, not a silent clamp
- [ ] No business logic in the UI — all math comes from `src/` via the API

---

## 10. Starter design tokens (`tokens.css`)

```css
:root {
  --surface: oklch(20% 0.01 260);
  --elevated: oklch(24% 0.012 260);
  --text: oklch(94% 0 0);
  --text-muted: oklch(66% 0 0);
  --accent: oklch(70% 0.16 250);
  --ok: oklch(72% 0.16 150);
  --warn: oklch(80% 0.15 85);
  --risk: oklch(68% 0.19 25);

  --font-ui: "Inter", system-ui, sans-serif;
  --font-mono: "IBM Plex Mono", ui-monospace, monospace;

  --text-kpi: clamp(1.75rem, 1.2rem + 2vw, 2.75rem);
  --space-gutter: clamp(1rem, 0.5rem + 1.5vw, 2rem);
  --radius: 10px;
  --ease-out-expo: cubic-bezier(0.16, 1, 0.3, 1);
}
```

---

_This brief pairs with the engine in `src/` and the end-to-end example
`examples/run_constrained_plan.py`. Keep the UI a thin layer over that engine._
