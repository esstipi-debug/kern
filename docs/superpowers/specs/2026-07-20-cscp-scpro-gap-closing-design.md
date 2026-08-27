# Closing Kern's CSCP / SCPro coverage gaps — design spec

**Date:** 2026-07-20
**Status:** design, pending approval
**Branch/worktree:** `feat/cscp-scpro-gap-plan` (`.wt-cscp-scpro-gaps`, off `origin/main` @ `7bb232d`)

## 1. What this is (and the honesty framing that bounds it)

The `/how-it-works` page (PR #173) rates Kern's coverage of two SCM certification
bodies of knowledge as **Medium-high**: CSCP (ASCM) and SCPro (CSCMP). This spec
is the plan to raise both toward **High**, grounded in what the codebase actually
has today.

**The framing that bounds every claim here:** "High" is the coverage level the
internal reference document
`documentation/KERN_NIVEL_REFERENCIA_SCM.md` assigns to how much of a
certification's *published syllabus* Kern's engine covers. It is **not** an ASCM
or CSCMP score, and shipping these tools does **not** make Kern "certified,"
"accredited," or "endorsed." Kern is software; certifications are held by people.
Every user-facing string that results from this work must keep the existing
discipline: "implements the models taught in...", "covers the syllabus area
of...", "aligns with..." — never "certified" or "accredited." This is a hard
rule, inherited from the `/how-it-works` page's own constraints.

## 2. What the gaps are, verified against the code

The gap lists come from `webapp/how_it_works_data.py`'s `CERTIFICATIONS`. A
verification pass (a workflow that read the actual source of each cited module,
2026-07-20) confirmed every gap is real and, more usefully, found that **most of
the machinery to close them already exists** — these are mostly composition
tasks, not from-scratch builds.

**CSCP (ASCM) — 4 gaps:**
1. Computable sustainability — no carbon/GHG calculation engine exists (only L3 book knowledge + an unbacked persona promise in `scm_agent/modes.py`).
2. Network/chain design at MILP scale — `src/facility_location.py` is single-facility only (center-of-gravity + Weiszfeld 1-median); no p-median/MILP.
3. Customer collaboration / CRM — genuinely absent, and **not** closed by this spec (see §5).
4. Strategic supplier relationship management — `src/supplier_scorecard.py` is OTIF/DIFOT + PPM only; no Kraljic matrix, segmentation, or strategy mapping.

**SCPro (CSCMP) — 3 gaps:**
1. Network analysis at scale — same p-median/MILP gap as CSCP #2 (shared fix).
2. Live external data integration — a family of credential-gated connectors; **large, mostly infra**, and **not** closed by this spec (see §5).
3. Sustainability — same engine gap as CSCP #1 (shared fix).

## 3. The buildable capabilities (what this spec covers)

Four new capabilities close 3 of 4 CSCP gaps and 2 of 3 SCPro gaps. Each follows
the repo's standard tool recipe (`src/<x>.py` pure functions → `jobs/<x>_job.py`
pandas `prepare()` → `register()` in `scm_agent/tools.py` → `citation_gate`
anchor → `GuidedOutcome` → tests) and gets its own implementation plan
(`docs/superpowers/plans/2026-07-20-<name>.md`).

### 3.1 `supplier_management` — Kraljic segmentation + strategic SRM
- **Closes:** CSCP "strategic supplier relationship management."
- **Computes:** per supplier, a profit-impact axis (spend share / ABC-value band) and a supply-risk axis (composite), a Kraljic quadrant (strategic / bottleneck / leverage / non-critical), and a quadrant→strategy mapping.
- **Reuses (verified ready):** `src/mcdm.py` (`bwm_weights`, `topsis_rank`, `award_outcome`), `src/multi_criteria_classification.py::classify_multicriteria` as the compositor template, `src/supplier_scorecard.py::score_supplier` for the delivery dimension, `jobs/sourcing_job.py` as the job template. The supply-risk axis is a normalized weighted composite honoring `src/risk.py`'s band thresholds *conceptually* (its `CATEGORIES` already has `supply`/`concentration`) rather than calling `risk.assess()` directly — the plan flags this: `assess()` consumes likelihood(0..1)+impact($) per `RiskFactor`, which per-supplier driver columns (lead time, single-source, ppm) don't naturally supply, so forcing that mapping would be an unauditable stretch.
- **Grounding:** Kraljic is **already in L3** — `knowledge::kraljic_matrix` node (Grant, Ch6). No new source needed. *(This corrects the reference doc, which stales it as "add Kraljic to L3.")*
- **Effort:** small (~1 session). Every hard component already exists and is tested.

### 3.2 `network_design` — multi-facility p-median (MILP)
- **Closes:** CSCP "network/chain design at MILP scale" **and** SCPro "network analysis at scale" (one tool, both gaps).
- **Computes:** given demand points, candidate sites, and a target count `p`, chooses which `p` sites to open and assigns each demand point to its nearest open site to minimize total weighted distance, via `scipy.optimize.milp`.
- **New tool, not an extension** of `facility_location` (that one answers "where is the single best site"; p-median answers "which `p` of these sites, and who serves whom" — a distinct question). The implementation plan confirms this against the current registration.
- **Reuses (verified ready):** `src/facility_location.py` (`total_weighted_distance`, `DemandPoint`/`Location` dataclasses), the `scipy.optimize` HiGHS pattern already used in `src/dea.py` and `src/mcdm.py` — **zero new dependencies** (`scipy>=1.10` pinned; `milp` ships since 1.9).
- **Grounding:** already in L3 — Chopra & Meindl "Facility Location and Capacity Allocation" / "Network Design in the Supply Chain." No new source needed.
- **Effort:** small (~1 session). QA against small known-optimum instances solvable by enumeration.

### 3.3 `carbon_footprint` — computable sustainability
- **Closes:** sustainability for **both** CSCP and SCPro.
- **Computes:** GHG emissions as tonne-km × emission factor per transport mode, plus warehouse energy emissions, bucketed Scope 1/2/3, with an optional carbon cost injectable into landed cost.
- **Reuses (verified ready):** `src/logistics/modes.py` `Shipment` dataclass (tonne-km is a new one-line derivation from its `weight_kg`/`distance_km`, not a reused function) and its `FreightRates` rate-card pattern (an `EmissionFactors` dataclass mirrors it); `src/landed_cost.py` gets a backward-compatible `carbon_cost` field (excluded from `duty_base`).
- **Grounding:** Grant's *Sustainable Logistics* is **already in L3** (177 nodes) with usable anchors. **Open item:** the GLEC Framework (the actual per-mode factor *numbers*) is **not** in L3; adding it needs the LLM-backed books-graph rebuild. Recommended path: ship with documented default factors from a public constant table (provenance in code comments), defer the GLEC L3 add as a separate follow-up.
- **Effort:** medium (~2-3 sessions) — the engine is small, but the L3 grounding half (GLEC rebuild) is its own careful session with the known graphify merge hazards.

### 3.4 `resilience_stress_test` — quantified resilience (bonus)
- **Closes:** SCOR **AG** (agility/resilience) — not one of the CSCP/SCPro gap lines, but the reference doc's evolution E5, and it deepens SCPro problem-solving.
- **Computes:** runs the digital-twin simulation twice with the same seed (baseline vs. disrupted), then measures time-to-recover (first period the disrupted served-trace re-enters a tolerance band around baseline), lost-units × margin = financial impact, and a resilience index (measured TTR vs. target TTS). Feeds the **measured** TTR into `src/risk.py::assess()` so TTR/TTS become computed outputs, not hand-typed inputs.
- **Reuses (verified ready):** `src/digital_twin.py` (`Disruption` dataclass + `simulate_network(..., disruptions=, seed=)` returning full per-period traces — the injection engine is done), `src/risk.py` TTR/TTS fields, `jobs/digital_twin_job.py` as the job template.
- **Grounding:** resilience/agility nodes exist in L3 (Ivanov, Christopher, Simchi-Levi). "Time-to-Recover"/"TTR" is not a literal graph concept, so the citation anchor comes from the existing resilience/agility concepts.
- **Effort:** small (~1 session) — almost pure composition on top of the existing simulator.

## 4. Recommended phasing

- **Phase 1 (highest leverage, ~2 sessions):** `supplier_management` + `network_design`. Both are "small" because the machinery is built and tested; together they move CSCP on 2 of 4 and SCPro on 1 of 3, with zero external/credential dependencies.
- **Phase 2 (~2-3 sessions):** `carbon_footprint`. Closes sustainability for both certifications. The engine ships first with documented default factors; the GLEC L3 grounding follows as its own PR.
- **Bonus (~1 session, any time):** `resilience_stress_test`. Independent of the others; closes SCOR AG.

Each capability is an independent PR (feature branch → draft PR → CI green on
py3.11/3.12/3.13 → squash-merge), per the repo workflow. They do not depend on
each other and can be built in any order, though Phase 1 first maximizes
certification-coverage movement per session.

## 5. What stays honestly open (not closed by this spec)

- **CSCP "Customer collaboration / CRM":** genuinely absent; would need a new collaborative-planning capability (CPFR-style) that isn't designed yet. Left open.
- **SCPro "Live external data integration":** a family of credential-gated connectors (ERP inventory pull, POS/demand feed, freight-rate API, supplier-risk feed), each its own session-plus of schema/auth/compliance work. Project memory already classifies this backlog as credential-gated. A minimal credible slice (one live Odoo inventory pull feeding `inventory_optimization`) is possible but is separate, larger work.

**Consequence for the honest claim:** with Phase 1 + Phase 2 + the bonus, CSCP
reaches 3 of 4 gaps closed and SCPro reaches 2 of 3 — genuinely close to "High,"
but not a full "High" until the two open items above are addressed. The
`/how-it-works` page's coverage bars and gaps list must be updated **in the same
PR** as each capability ships, so the page never overstates what's built. Do not
flip either bar to "High" until its remaining gap line is actually closed.

## 6. Cross-cutting constraints (carry into every plan)

- No "certified/accredited" language anywhere (see §1).
- No new dependencies for `network_design` (`scipy.optimize.milp` already available) — confirm before adding anything for the others.
- `citation_gate` concept pools stay ≤ 8 concepts per tool (the empirically-verified ceiling breaks at ~11-12 — see project memory `kern-packages-citation-fix-pr164`).
- Every tool returns a `GuidedOutcome` (never-unprotected contract).
- ASCII-only in console prints (Windows cp1252); markdown/docs in utf-8 are fine.
- When a capability ships, update `webapp/how_it_works_data.py` (the tally, the domain-area/SCOR bucket for the new tool, and the relevant certification's covered/gaps lists) and the README/CLAUDE.md tool count in the same PR.

## 7. Deliverables of this planning work

- This spec.
- Four implementation plans in `docs/superpowers/plans/2026-07-20-<name>.md`, one per capability, each a complete bite-sized TDD plan grounded in the real existing code.
