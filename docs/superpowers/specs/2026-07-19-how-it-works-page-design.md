# `/how-it-works` page — design spec

**Date:** 2026-07-19
**Status:** approved by user, pending implementation plan
**Branch/worktree:** `feat/how-it-works-page` (`.wt-how-it-works`, off `origin/main` @ `7bb232d`)

## 1. Goal & audience

An interactive, English-language page explaining how Kern works, for an **internal /
onboarding** audience (a new team member, a technical partner) — not a sales page.
No hard CTA. Depth and honesty (including admitted gaps) are preferred over a
polished sales pitch.

It must show, interactively:

1. Kern's capability breadth via pie/donut charts (multiple lenses on the same 41 tools).
2. What Kern does and the functions it covers.
3. How it's "fed" — grounded and made to adapt to different client contexts — to
   justify its outputs.
4. That it spans multiple SCM functions, not a single-purpose tool.
5. How its capabilities relate to recognized SCM standards and certifications.

## 2. Non-goals

- Not a sales/marketing page — no pricing, no lead-capture form, no CTA buttons
  beyond quiet wayfinding links (Home, live console).
- Not a live dashboard — no API calls, no per-request computation. All data is
  static and hand-curated at write time (approved in brainstorming: "Estática,
  curada a mano").
- Does not claim Kern holds any certification or ISO registration. See §6.
- Does not restate `documentation/KERN_NIVEL_REFERENCIA_SCM.md` in full — it
  summarizes and visualizes it, and links to it for the complete detail.

## 3. Architecture

- New file: `webapp/how_it_works_page.py`, exporting `render_how_it_works_html()`.
  Follows the exact convention of `webapp/stocky_alternative_page.py`: an HTML
  string built from `_HEAD`/`_FOOT` constants, same dark/teal design system
  (`--ink/--panel/--accent` tokens, Inter + JetBrains Mono, same header/footer
  shell), no new external dependencies.
- Mounted in `webapp/app.py` as `GET /how-it-works`, same pattern as the existing
  `@app.get("/stocky-alternative")` route (import the render function, return
  `HTMLResponse(render_how_it_works_html())` — no request params, since content
  is fully static).
- **CSP:** no changes needed. Confirmed `webapp/security.py`'s `_BASE_CSP` already
  allows `script-src 'self' 'unsafe-inline'` and `style-src 'self' 'unsafe-inline'
  https://fonts.googleapis.com`, which covers this page's inline `<style>` and
  vanilla-JS interactivity. `/how-it-works` is not `/console` or
  `/static/prototype`, so it gets `_BASE_CSP`, which is sufficient — verified by
  reading `csp_for_path()`.
- **No charting library.** Donuts are hand-built inline SVG (`<circle>` with
  `stroke-dasharray`/`stroke-dashoffset` per segment), matching the site's
  existing zero-external-JS-dependency convention. Interactivity (tab switching,
  hover tooltip, click-to-expand tool list) is vanilla JS.
- **File size:** target ~500-700 lines. If the module threatens the project's
  800-line-per-file ceiling, extract the interactivity script to
  `webapp/static/how_it_works.js` (mirrors the existing `webapp/static/app.js`
  convention) and keep `how_it_works_page.py` focused on markup/content. Decide
  at implementation time based on actual size.
- Nav: add a quiet "How it works" link to the existing nav bars of `/paquetes`
  and `/stocky-alternative` (or leave it link-free / direct-URL-only) — decide at
  implementation time; not a spec-blocking decision either way since the route is
  public but not indexed/promoted regardless.

## 4. Content sections

### 4.1 Hero
One-line positioning + an interactive horizontal stepper for the pipeline:
**Brief → Classify → Run → QA → Deliver**. Click a stage for a one-line
explanation. Replaces the README's static Mermaid diagram with something
explorable (Mermaid itself is not used — it would need a CDN script not
currently allowed by CSP, and the site has no existing Mermaid usage to match).

### 4.2 The 41 capabilities — two real donuts, one toggle
Centerpiece interactive component. A tab control switches the **same 41-tool
dataset** between two lenses, redrawing one donut:

**Lens A — By domain area** (default tab). Verified against
`build_default_registry()` in `scm_agent/tools.py` (41 `register()` calls) —
this corrects the README's stale image caption, which still shows 40 tools
because `launch_readiness` (tool #41) was added after that PNG was generated:

| Domain area | Count |
|---|---|
| Inventory & replenishment | 9 |
| Network & logistics | 7 |
| Inventory control & health | 6 |
| Pricing & finance | 6 |
| Demand & classification | 3 |
| Procurement & sourcing | 3 |
| Returns, risk & benchmarking | 3 |
| Planning cadence & projects | 3 (`sop`, `earned_value`, `launch_readiness`) |
| Leadership | 1 |
| **Total** | **41** |

**Lens B — By SCOR Digital Standard process category.** Ground truth is
`documentation/KERN_NIVE_REFERENCIA_SCM.md` §2 (generated 2026-07-17 from a
direct code read), not an independently-invented mapping — a 45-agent
adversarial-verification workflow run during this design phase cross-checked
that document's categorization against the actual source of each tool and
found strong agreement, with the corrections already folded into the table
below. Two gaps in that document had to be resolved to reach 41:

- `reconciliation`, `odoo_replenishment`, `excel_replenishment` are not given
  their own row in the doc (they're mentioned as supporting modules /
  connector variants of `cycle_count` and `inventory_optimization`) — bundled
  into **Plan** alongside the tool they extend.
- `launch_readiness` (tool #41) postdates that document's table and is not
  listed at all — added to **Plan** (it's a coverage-vs-lead-time planning
  calculation, same family as `sop`). This is a deliberate, code-grounded
  correction, consistent with the document's own opening methodology note that
  "los docs van por detrás del código."
- `leadership_chain` is genuinely not listed anywhere in §2 — it doesn't fit
  any SCOR process (it's an organizational-leadership assessment, not a supply
  chain operation). Shown as its own **"Outside SCOR's scope (by design)"**
  slice rather than silently omitted or force-fit — the honest option.

| SCOR-DS category | Count | Representative tools |
|---|---|---|
| Plan | 15 | `forecast`, `inventory_optimization` (+ its `odoo_replenishment`/`excel_replenishment` writeback variants), `newsvendor`, `multi_echelon`, `ddmrp`, `abc_xyz`, `sop`, `simulation`, `cycle_count` (+ `reconciliation`), `excess_obsolete`, `whatif`, `launch_readiness` |
| Source | 3 | `sourcing`, `landed_cost`, `acceptance_sampling` |
| Transform | 4 | `scheduling`, `queuing`, `learning_curve`, `earned_value` |
| Order/Fulfill | 8 | `warehouse_layout`, `slotting`, `fefo`, `transportation`, `vehicle_routing`, `facility_location`, `drp`, `cost_to_serve` |
| Return | 2 | `returns`, `markdown_liquidation` |
| Orchestrate | 8 | `digital_twin`, `risk`, `dea`, `financial_kpis`, `pricing`, `price_intelligence`, `price_watch`, `data_quality` |
| Outside SCOR scope | 1 | `leadership_chain` |
| **Total** | **41** | |

Click a segment (either lens) to expand the real tool list below the donut.
Each tool maps to exactly one bucket per lens so the chart always totals 41 —
a tool that is genuinely relevant to more than one bucket may say so in its
expanded description, but that never changes the chart's math.

**Caption under Lens B** (near-verbatim from the source document, this is the
honest headline finding, not a footnote): *"Transform (production/manufacturing
execution) is Kern's thinnest SCOR category by design — Kern is a planning and
decision-support engine, not a manufacturing execution system (MES)."*

### 4.3 How it's grounded and adapts to context
Four expandable cards, answering "cómo se alimenta para justificar soluciones y
adaptarse a diferentes contextos":

1. **Knowledge graph** — grounded in **33 curated SCM sources** (books + papers)
   plus the codebase itself; every deliverable carries L3 citations, gated by
   `citation_gate` (min 2 citations, max 2 hops, an `EXCLUDED_CONCEPTS`
   false-friend filter). *(Corrected from the README's stale "25 fuentes" —
   `documentation/KERN_NIVEL_REFERENCIA_SCM.md`'s own verified-counts table
   gives 33 distinct `source_file`s in `knowledge/scm-books/graph.json`, and
   flags the README count as stale. Use 33, consistently, everywhere this page
   mentions the source count.)*

   > **SUPERSEDED 2026-07-31 (PR #200) — the page now says 26, not 33.** Two
   > reasons. (1) *Stale*: under this section's own file-counting methodology
   > the count is now **35**, not 33 — the graph gained the Atlas and Khan
   > resilience since. (2) *Wrong unit*: counting `source_file`s overstates
   > "curated sources" to a prospect, because 9 of those files are single
   > chapters of edited volumes, and `KERN_ATLAS_SOMBREROS_SCM.md` is **Kern's
   > own internal doc** — citing yourself as a curated external source is the
   > overclaim, and it is the same auto-cita problem flagged on #191. The page
   > now claims **26 curated external works** = the 27 sources in
   > `knowledge/scm-books/README.md` minus the Atlas. Guarded by
   > `tests/test_how_it_works_page.py::test_source_count_claim_still_matches_the_books_graph`.
   > Do not restore 33.
2. **Client profiles** — per-client durable cost/capacity parameters (holding
   rate, order cost, service level, lead time, warehouse capacity), persisted
   under `clients/<slug>/profile.json`, merged into every run so the same brief
   produces a client-specific answer instead of a generic one; asked once, then
   remembered.
3. **QA gate** — "QA fails ⇒ no deliverable," enforced in one place by the
   orchestrator; a bad result is refused, not shipped.
4. **Optional LLM layer** — works with or without one; the deterministic engine
   is the core, an optional `LLMProvider` sharpens routing/narrative when
   available.

### 4.4 The never-unprotected contract
Small donut: `EXECUTED` / `OPTIONS` / `HANDOFF` / `ESCALATED`. Caption states
explicitly this is **structural** (the four possible outcome shapes), **not** a
measured run-frequency split — matching the same caveat already in the README,
so this page doesn't introduce a new way to misread it.

### 4.5 Alignment with SCM standards & certifications
Ground truth: `documentation/KERN_NIVEL_REFERENCIA_SCM.md` (confirmed with user
to use in full, not the narrower 3-certification version originally scoped).

1. **SCOR Digital Standard** — restate/link Lens B's donut from §4.2; one line
   noting SCOR DS's own **Orchestrate** category (added for the "digital"
   layer — twins, analytics, agents, resilience) is where Kern's agentic
   guarantees (QA gate, never-unprotected, signed staged writeback) land, ahead
   of what most commercial suites do here per the source doc's own assessment.
2. **Certification coverage — NOT a donut.** The source document's own §3
   frames per-certification coverage as a qualitative level (Alto /
   Medio-alto / Parcial), not a tool count, because a tool can legitimately
   touch more than one certification's body of knowledge at once (e.g.
   `sop` touches CPIM, CSCP, and SCPro simultaneously) — forcing that into an
   exclusive-partition donut would misrepresent it. Render as 5 horizontal
   coverage bars instead:

   | Certification | Body | Coverage (per source doc §3) |
   |---|---|---|
   | CPIM | ASCM | Alto |
   | CLTD | ASCM | Alto |
   | CSCP | ASCM | Medio-alto |
   | SCPro | CSCMP | Medio-alto |
   | CPSM | ISM | Parcial |

   Click a bar to expand its "Cubierto" / "Lagunas" bullet lists, taken
   directly from the source document's §3 (translate to English for this
   page's copy; do not alter the substance).
3. **ISO 9001 / ISO 28000 alignment** — an accordion, one row per clause,
   adapted from §4.2/§4.3 of the source doc (e.g. "8.7 Control de salidas no
   conformes → QA fails ⇒ zero deliverables", "Autorización de cambios → HMAC-
   signed, time-boxed approvals").
4. **Honest gaps** (§6 of the source doc) — a plain list, not hidden: computable
   sustainability/carbon footprint, deep SRM/supplier segmentation, lot-level
   traceability (EPCIS), quantified resilience/stress-testing, network
   optimization beyond single-facility siting. Each gap names which standard
   asks for it. No promised timeline — these are labeled "not yet," not "coming
   in v2.10," since the source doc's own evolutions (E1-E5) are marked
   `PROPUESTA` (proposed, not built) and this page must not imply otherwise.

### 4.6 Footer
- Trademark disclaimer: SCOR® is a framework of ASCM; APICS, CPIM, CSCP, CLTD
  are ASCM certifications; SCPro is a CSCMP certification; CPSM is an ISM
  certification. Kern is not affiliated with, endorsed by, or certified by any
  of these bodies — this page shows how Kern's own capabilities relate to
  those public frameworks. (Same honesty pattern already used in
  `stocky_alternative_page.py`'s Shopify disclaimer.)
- Link to the full technical reference (`documentation/KERN_NIVEL_REFERENCIA_SCM.md`)
  for readers who want the complete detail.
- Quiet nav: Home, live console/demo. No sales CTA.

## 5. Hard content rules (carry into implementation)

- Never write "Kern is ASCM-certified," "Kern is ISO 9001 certified," or any
  phrasing implying Kern itself holds a credential. Always "implements the same
  models taught in...", "aligns with...", "maps to...". This is a hard rule,
  not a style preference.
- Every number on this page must match its most-recently-verified source:
  tool count (code, `scm_agent/tools.py`), curated-source count (code,
  `knowledge/scm-books/graph.json`), domain-area and SCOR-DS tallies as
  tabulated in §4.2 above. If a future edit changes any underlying count, this
  page and its captions must be updated in the same PR — no silently-stale
  numbers, matching the source document's own stated methodology.

  > **UPDATED 2026-07-31 (PR #200).** This rule was stated with the counts of
  > the day inlined (*41 tools, 33 curated sources*) — and both went stale
  > anyway, because a rule written in prose cannot fail a build. The page had
  > drifted to 41-of-49 tools and a stale 33 sources before anyone noticed.
  > The counts are deliberately no longer inlined here; the enforcement lives
  > in tests instead:
  > `tests/test_how_it_works_data.py::test_tools_table_matches_the_registry_exactly`
  > (set equality vs the registry, both directions) and
  > `tests/test_how_it_works_page.py::test_source_count_claim_still_matches_the_books_graph`.
  > Update the guards, not a number in a doc.
- The gaps section (§4.5.4) stays even though it's less flattering than a pure
  capabilities showcase — approved explicitly because the audience is internal/
  onboarding, not sales.

## 6. Testing / acceptance

- Start the dev server (`uvicorn webapp.app:app --reload`) and preview
  `/how-it-works` in-browser: verify both donut lenses render and total 41 on
  hover/click, the certification bars expand, the ISO accordion expands, no
  console errors, no CSP violations (check browser console/network tab).
- Responsive check at 320/768/1440 per the project's web testing convention.
- Confirm the route returns 200 and valid HTML via a request test, following
  the pattern of existing tests for `/stocky-alternative` /
  `/paquetes` (e.g. `tests/test_stocky_alternative_page.py` if one exists —
  check at implementation time) so this route has the same test coverage as
  its sibling pages.

## 7. Open implementation-time decisions (non-blocking)

- Whether to add a nav link to `/how-it-works` from `/paquetes` and
  `/stocky-alternative`, or leave it direct-URL-only.
- Exact final English copy for the ISO clause accordion and the certification
  "Cubierto/Lagunas" bullets (translated from the Spanish source doc; substance
  must not change, wording is free).
- Whether the interactivity script is inlined or extracted to
  `webapp/static/how_it_works.js`, based on final line count.
