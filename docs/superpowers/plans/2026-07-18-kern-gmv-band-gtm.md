# Kern GMV-Band GTM — Design & Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL — use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Status:** DESIGN COMPLETE — ready to build. This document was produced in a design-only phase (no code was written). Three parallel design agents + verified 2026-07-18 competitor/channel research back it. Switch to the build model to execute.

**Goal:** Ship Kern's next GTM increment — a GMV-band-primary pricing model (Stage 2), an English AU/NZ agency landing surface, and an enumerated Cin7/Unleashed channel target list — without violating the positioning guardrails or the decision-layer truth.

**Architecture:** Three independent build tracks. (A) A new pure `src/commercial_pricing.py` module computes a per-visitor GMV-band price with a SKU-count fairness adjuster, reusing the already-shipped floor/block/ceiling arithmetic; the existing `price` display strings and Stripe Payment Links stay untouched. (B) A new English landing route mirrors the live `/stocky-alternative` page exactly. (C) A CSV target list of 42 enumerated AU/NZ firms (14 domain-verified Tier-A, 24 directory-listed pending verification, 4 off-ICP reference) feeds a design-partner rev-share outreach sequence.

**Tech Stack:** Python 3.11+, FastAPI (webapp), pytest (`PYTHONPATH=.`), ruff, frozen dataclasses. No new dependencies. No Stripe SDK.

---

## Global Constraints

Every task's requirements implicitly include this section. These are copied verbatim from the governing docs (`documentation/KERN_AGENCIA_IA_TESIS_COMERCIAL.md` is the governing layer; the ATLAS finding; `CLAUDE.md`).

- **Branch off `origin/main`, never the local branch.** The working tree is on `feat/optimized-replenishment-targets` (pre-reprice, stale). Local `main` is 3 commits behind. Do: `git fetch origin && git checkout -b feat/<name> origin/main`. Building on the current branch would silently reintroduce the old $2,000/$4,000/$7,500 prices.
- **Banned words in customer-facing copy** (Kern holds no credential, 0 paying clients): `certificado`, `audit-grade`, `"cumple/meets ISO"`, `EXCEED`, `10x`, `digital twin`, `"la cadena entera operada" / "operate your whole chain"`. Internal/engine docs may keep "digital twin" as an engine name; the ban is customer-facing copy only.
- **Mechanism, not outcome.** A claim is either a *mechanism* (in the code — stated as fact) or an *outcome* (a client KPI — stated as *expected*, never as a number, until a founding client proves it). "Smooth", "liberate cash", "fewer stockouts" are outcomes → hedge them.
- **Decision/design-layer truth (ATLAS).** Copy may say Kern *decides, plans, coordinates, stages, recommends*. It must never say Kern *executes, ships, buys, or operates* anything physically. "Kern never issues POs, never runs MRP/BOM explosion, never drives a live WMS/TMS ... a human executes." "Coordinate" = coordinate the *decisions into one plan*, never "manage people".
- **Language.** AU/NZ customer-facing surface = **English** (mirror `/stocky-alternative`). Spanish `/paquetes` catalog stays Spanish. Customer brand = **"Kern"**; infra identifiers stay **"linchpin"** (`linchpin.fly.dev`, `LINCHPIN_*`, `linchpin_*` tools) — untouched.
- **Currency (AU/NZ).** Quote and bill in **local currency (AUD/NZD)**, absorbing FX+GST. Do not apply a "USD x1.5" surcharge (thesis §4 kills it).
- **Never a fabricated dollar figure, never a credential we do not hold.** Any `[bracketed]` figure must come from a real run before it ships. In cold outreach, SMB SKU data is not public — use storefront-observable signals as a stated *hypothesis* only.
- **Repo conventions.** Feature branch → draft PR → CI green on py3.11/3.12/3.13 (`ruff check src tests examples`, `pytest tests/ -q`, `--cov-fail-under=80`, `PYTHONPATH=.`) → squash-merge. Never push straight to `main`. ASCII-only in console `print()` (Windows cp1252). New agent tool recipe unchanged. The `prod-boot` job: never add a module-level import of an optional-extra dep on the app boot chain.
- **YAGNI — 0 paying clients.** Do not build dynamic Stripe checkout, do not persist per-visitor picks to `client_profile`, do not author English one-pagers until a client needs them.

---

## Part 0 — Critical current-state correction (READ FIRST)

The design agent verified this against real git state, not the local working tree:

| Fact | Reality |
|---|---|
| Local working tree | `feat/optimized-replenishment-targets` (tip `c1ac602`) — **pre-reprice**, shows OLD prices. Not canonical. |
| Local `main` | `f396f6f` — 3 commits stale (missing #167, #168, #171). Needs fetch/fast-forward. |
| **Canonical state** | **`origin/main` (`7374969`)** — already contains **PR #167** (`0a6e60a`, "sync Anglosphere repricing + 7-tool Starter/Growth reclassification"). |
| Stage 1 | **ALREADY SHIPPED.** Starter `USD 900/mes` (15 tools), Growth `USD 1,500/mes` (26), Scale `USD 3,200/mes` flat, Retainer `USD 4,500/mes` flat — synced across `package_specs.py`, `MONETIZATION_BRIEF.md`, all 12 `paquetes/*.md`, `offers.py`, `tests/test_packages.py`. |
| **The discrepancy that defines this plan** | What shipped is **SKU-count-block pricing with NO GMV/annual-revenue axis** (grep for `GMV`/`annual_revenue`/`revenue band` = zero pricing hits). The GMV-band model is **net-new**. Stage 2 must **reframe** the shipped SKU mechanism as the *secondary fairness adjuster* under a *new GMV-band primary axis* — not rebuild SKU pricing. |
| Also unshipped | The LatAm `USD 250-300` reduced-scope Starter-equivalent — decided, no `PackageSpec`/`Offer` exists (Stage 1.5 below). |
| `price` field | Plain `str` on two frozen dataclasses (`PackageSpec.price`, `Offer.price`), consumed only via `escape(...)` in `webapp/paquetes_page.py` + `webapp/stocky_alternative_page.py`. No runtime code parses it into a number. Stripe = static per-offer Payment Link URL (`STRIPE_LINK_<SLUG>` env var); zero Stripe SDK anywhere. |

---

## Part 1 — Strategy summary (the "why", consolidated)

- **Who we sell to (end client):** retailer/distributor `USD 1-15M`, inventory-heavy, no in-house data science, owner/ops buyer. AU/NZ first (Stocky EOL 2026-08-31 wedge, English-native, category near-absent). Sell the *result*, not the tool.
- **Who runs Kern (channel):** certified operators. Verified sizing (2026-07-18): **Cin7/Unleashed implementation partners = the #1 arranque** (~60-110 firms, publicly enumerable, already own the exact `$2-15M` AU/NZ retail book and already sell inventory retainers). Shopify Partners (~289 public, Stocky-catalyzed) = product-distribution channel. Xero advisors + CPIM = larger but opaque pools, Phase-2. "Masivo" = **multiplier** (1 partner ≈ dozens of ICP retailers), not raw operator volume.
- **Positioning spine:** cold hook **C** ("Stop firefighting stockouts and overstock") → web promise **B** ("Demand, stock, purchasing and pricing — one plan that stops fighting itself") → sales wrapper **A** ("the supply-chain team you can't afford to hire — as a service"). **B is the moat** — the one thing point-tools (Cin7, Prediko, Netstock) structurally cannot claim: cross-domain coordination, not a single slice.
- **Pricing model:** GMV band **primary** (buyer picks their annual-revenue band, self-serve, industry-standard — verified vs Prediko/Inventory Planner/Netstock/Cin7/Katana, none scale primarily on SKU count) + SKU count **secondary** fairness guardrail (à la Inventory Planner). Fair *and* massive.

---

## Part 2 — Track A: GMV-band pricing (Stage 1.5 + Stage 2)

### Design decisions (do not re-litigate)

- **`price` stays a display string on `PackageSpec`/`Offer`.** A GMV-band pick is a per-visitor computation, not a property of the shared package definition. Do not change the field type.
- **New pure module `src/commercial_pricing.py`**, mirroring `src/contingent_fee.py`'s shape (frozen-dataclass result, validated bounds, floor, `render_*` prose function). **Do NOT name it `pricing_model.py` or anything with a bare `pricing`/`repricing` stem** — the repo overloads those for the *client's own product prices* (`src/pricing.py`, `jobs/repricing.py`, `src/pricing_intel/`, `examples/run_repricing.py`). This module is what *Kern charges*.
- **GMV band primary, SKU secondary clamp.** Reuse the shipped floor/block/ceiling arithmetic verbatim, re-scoped from "the whole mechanism" to "the fairness adjuster within a band."
- **No `client_profile.py` change, no Stripe SDK, no dynamic checkout.** Keep the static floor-price Payment Link as the CTA; settle any GMV/SKU delta by manual invoice post-onboarding. (YAGNI, 0 clients.)

### The pricing model (math)

```
base_price   = PACKAGE[package_key].base_price         # PRIMARY: from the package (= its band)
fairness     = ceil(max(0, sku_count - included) / block) * increment   # SECONDARY, banded tiers only
monthly      = min(base_price + fairness, ceiling)     # ceiling = next tier's floor
```

**Primary-axis resolution (RESOLVED — the critic caught this ambiguity).** Package and revenue band are **1:1** (Starter=`1-3M`, Growth=`3-8M`, Scale=`8-15M`). `base_price` and the fairness rule come from **`package_key`, which is the source of truth** — NOT from `annual_revenue`. Proof it must be package-keyed: the LatAm tier is `$250-300` at "any band". `annual_revenue` is used only to (a) SUGGEST the matching package in the UI band-picker and (b) surface a *mismatch* hint (e.g. `quote_price("starter", annual_revenue=10_000_000)` → suggest Growth/Scale, do NOT silently override `base_price`). Below the lowest band (sub-`$1M`) → return `needs_clarification` / route to the LatAm reduced-scope tier; never silently clamp.

Per-tier fairness constants (the already-shipped numbers, reused verbatim):

| Tier | included SKUs | block | increment | ceiling |
|---|---|---|---|---|
| Starter | 500 | 250 | $40 | $1,500 |
| Growth | 2,000 | 500 | $60 | $3,200 |
| Scale | — flat, no fairness — | | | |
| Retainer | — flat, no fairness — | | | |

### ⚠️ REQUIRED INPUT before coding Stage 2: the GMV band cutoffs

The exact annual-revenue band boundaries **are not in the repo** and were not fabricated by the design agent. **DECIDED (Part 5 item 1)** — grounded in the ICP (`USD 1-15M`) + the shipped price anchors + the Prediko/Inventory-Planner benchmark. Hardcode into `RevenueBand` rows exactly as follows:

| Package | Proposed annual-revenue band | base_price |
|---|---|---|
| Starter | `USD 1-3M` | $900 |
| Growth | `USD 3-8M` | $1,500 |
| Scale | `USD 8-15M` | $3,200 (flat) |
| Retainer | upgrade-only, not revenue-banded | $4,500 (flat) |
| (LatAm Starter-equiv.) | reduced scope, any band | $250-300 |

### Concrete signatures — `src/commercial_pricing.py`

```python
@dataclass(frozen=True)
class RevenueBand:
    key: str                          # e.g. "starter_band"
    label: str                        # "USD 1-3M/yr" (display)
    min_annual_revenue: float
    max_annual_revenue: float | None  # None = open-ended top band
    base_price: float                 # GMV-band list price (primary axis)

@dataclass(frozen=True)
class SkuFairnessRule:
    included_skus: int                # covered by base_price before any add-on
    block_size: int                   # SKUs per increment (Starter 250, Growth 500)
    block_increment: float            # USD per block (Starter 40, Growth 60)
    ceiling: float                    # hard cap = next tier's floor (Starter 1500, Growth 3200)

@dataclass(frozen=True)
class PriceQuote:
    package_key: str
    revenue_band_key: str
    base_price: float
    sku_count: int | None
    fairness_adjustment: float
    monthly_price: float
    ceiling_hit: bool
    explanation: str                  # feeds render_price_string

def quote_price(package_key: str, annual_revenue: float, sku_count: int | None = None) -> PriceQuote: ...
def render_price_string(quote: PriceQuote) -> str: ...   # same prose convention already in package_specs.py
```

### Files

- Create: `src/commercial_pricing.py`, `tests/test_commercial_pricing.py`
- Create: `webapp/pricing_quote.py`, `tests/test_webapp_pricing_quote.py`
- Modify (Stage 1.5 only): `scm_agent/package_specs.py`, `webapp/offers.py`, `documentation/MONETIZATION_BRIEF.md`, `documentation/paquetes/*.md`, `tests/test_packages.py`, `tests/test_webapp_offers.py` — add the LatAm `starter_latam` spec/offer, all in ONE PR.
- Modify (Stage 2 UI): `webapp/paquetes_page.py` — additive band-picker widget next to the static price anchor.

### Tasks

#### Task 1 (A1): Stage 1.5 — LatAm reduced-scope Starter (mechanical, its own small PR)

**Files:** Modify `scm_agent/package_specs.py`, `webapp/offers.py`, `documentation/MONETIZATION_BRIEF.md`, matching `documentation/paquetes/*.md`, `tests/test_packages.py`, `tests/test_webapp_offers.py` — **together in one PR** (the "price files change together" convention PR #167 set).

- [ ] **Step 1:** Write the failing test in `tests/test_packages.py` asserting a `starter_latam` `PackageSpec` exists, price string contains `250` and `300`, reduced tool count, `lang`/market marker.
- [ ] **Step 2:** Run `pytest tests/test_packages.py -q` — expect FAIL.
- [ ] **Step 3:** Add the `starter_latam` `PackageSpec` + matching `Offer` + `MONETIZATION_BRIEF.md` row + one-pager, mirroring the existing `starter` shape with reduced scope.
- [ ] **Step 4:** Run the suite — expect PASS.
- [ ] **Step 5:** Commit `feat: add LatAm reduced-scope Starter-equivalent (USD 250-300)`.

#### Task 2 (A2): `commercial_pricing.quote_price` core (TDD)

**Interfaces produced:** `RevenueBand`, `SkuFairnessRule`, `PriceQuote`, `quote_price(...)`, `render_price_string(...)` (signatures above).

- [ ] **Step 1: Write failing tests** in `tests/test_commercial_pricing.py`:

```python
def test_starter_floor_at_band_base():
    q = quote_price("starter", annual_revenue=2_000_000, sku_count=400)
    assert q.monthly_price == 900.0          # <= included SKUs -> no fairness add
    assert q.ceiling_hit is False

def test_starter_fairness_block_added():
    q = quote_price("starter", annual_revenue=2_000_000, sku_count=1_000)
    assert q.fairness_adjustment == 80.0     # ceil((1000-500)/250)=2 blocks * 40
    assert q.monthly_price == 980.0

def test_starter_ceiling_clamps():
    q = quote_price("starter", annual_revenue=2_000_000, sku_count=100_000)
    assert q.monthly_price == 1500.0         # clamped to Starter ceiling
    assert q.ceiling_hit is True

def test_scale_is_flat_no_fairness():
    q = quote_price("scale", annual_revenue=10_000_000, sku_count=50_000)
    assert q.monthly_price == 3200.0
    assert q.fairness_adjustment == 0.0

def test_invalid_package_raises():
    import pytest
    with pytest.raises(ValueError):
        quote_price("nonexistent", annual_revenue=1_000_000)
```

- [ ] **Step 2:** Run `pytest tests/test_commercial_pricing.py -q` — expect FAIL (module missing).
- [ ] **Step 3:** Implement `src/commercial_pricing.py` with the dataclasses, the band/rule tables (using the confirmed cutoffs), `quote_price` (band lookup → fairness clamp → ceiling), and `render_price_string`. No I/O, pure functions.
- [ ] **Step 4:** Run — expect PASS. Add edge cases (band boundary revenue, `sku_count=None`, revenue below the lowest band).
- [ ] **Step 5:** Commit `feat: add commercial_pricing GMV-band quote engine`.

#### Task 3 (A3): `webapp/pricing_quote.py` route adapter (TDD)

- [ ] **Step 1:** Failing HTTP test in `tests/test_webapp_pricing_quote.py`: GET the quote route with `?package=starter&revenue=2000000&skus=1000` returns 200 and a body containing `980`.
- [ ] **Step 2:** Run — expect FAIL.
- [ ] **Step 3:** Implement `webapp/pricing_quote.py` — map offer slug/package key → `commercial_pricing` package key, parse GET params, call `quote_price`, return `render_price_string`. **Verify it imports nothing from an optional extra** (keep it boot-safe — `prod-boot` job).
- [ ] **Step 4:** Run — expect PASS.
- [ ] **Step 5:** Commit `feat: add pricing-quote web adapter`.

#### Task 4 (A4): additive band-picker widget on `/paquetes`

- [ ] **Step 1:** Failing test asserting `/paquetes` renders the band-picker element next to the existing static price.
- [ ] **Step 2:** Run — expect FAIL.
- [ ] **Step 3:** Add the widget to `webapp/paquetes_page.py` — additive HTML, existing `escape(offer.price)` anchor untouched.
- [ ] **Step 4:** Run — expect PASS. Confirm the static "starting at $900/mes" anchor still renders unchanged.
- [ ] **Step 5:** Commit `feat: add GMV-band price picker to paquetes page`. Update `HANDOFF.md` to close out "Stage 2 not started".

---

## Part 3 — Track B: English AU/NZ agency landing surface

### Pattern to mirror (traced in repo)

- **Route:** `webapp/app.py:1598` — `@app.get("/stocky-alternative")` → `HTMLResponse(render_stocky_alternative_html(offer_starter, offer_diagnostico))`; pulls real offers via `get_offer("starter-fundamentos")` / `get_offer("diagnostico-arranque")` and asserts they exist (no pricing invented in the page).
- **Page module:** `webapp/stocky_alternative_page.py` — self-contained: inline CSS tokens (dark/teal, Inter + JetBrains Mono), `_FAQ` tuple, `_faq_jsonld()` emitting `schema.org/FAQPage`, `_cta_buttons(offer)`, `render_*_html()` composer.
- **CTA wiring:** `webapp/offers.py` — `resolve_pagar_cta` / `resolve_agendar_cta` return a Stripe/Calendly link when the env var is set and **degrade to `mailto:` when not** (page works with zero payment config). `is_safe_external_url` gates external hrefs.
- **Tests:** `tests/test_stocky_alternative_page.py`, `test_webapp_offers.py` — the guard pattern to copy.

### Assets (spec — build to these)

- **Asset 1 — Primary English agency landing route (the B page).** New `@app.get("/one-plan")` + new `webapp/one_plan_page.py`, mirroring `stocky_alternative_page.py` exactly (English, own minimal shell, same visual system). Content architecture:
  - **H1 = promise B:** "Demand, stock, purchasing and pricing — one plan that stops fighting itself."
  - **Eyebrow/tension band = hook C:** "Stop firefighting stockouts and overstock."
  - **"How we work" = wrapper A:** fractional-team framing, honest economics — compare to a **loaded full-time planner hire** (~USD 100-120k/yr), NOT to their $100-300/mo app. **Do the ratio per SHIPPED price; do not hardcode a blanket figure.** Under the repriced catalog: Starter `$10.8k/yr ≈ 10%`, Growth `$18k/yr ≈ 15-18%`, Retainer `$54k/yr ≈ 45%`. The "~40-50% of a salary" line is TRUE ONLY for Scale/Retainer — **never state it on a page that CTAs into Starter/Diagnóstico** (the old "~40-50%, never 10-20%" directive was computed on the pre-#167 `$4k` Growth and is now false).
  - **"What we do / what we don't" block:** state the ATLAS frontier in words (we decide & stage; your ERP/MES executes; a human signs).
  - Reuse the **same two offers** the stocky page uses — `get_offer("diagnostico-arranque")` + `get_offer("starter-fundamentos")` via `_cta_buttons`, so **no new pricing is authored**. Mechanism-not-outcome throughout; zero banned words.
- **Asset 2 — Cold-outreach copy (hook C).** Plain-text email variants added to `documentation/GTM_OUTREACH_TEMPLATES.md` (plain text, one prospect at a time, no attachments, no AI name-dropping). Hook C day-0; the store-signal *hypothesis* (labeled a hypothesis, never a $ figure) as line 1.
- **Asset 3 — FAQ + JSON-LD** for Asset 1, copying `_FAQ` / `_faq_jsonld()`, seeded with the 3 deal-killers (0 clients, black-box, "my ERP already does this").
- **Asset 4 (reuse, not new):** `/paquetes` (Spanish) stays as-is; the English landing CTAs into the same offers.

**Seam to resolve in build:** the live stocky page links into Spanish one-pagers (`/paquetes/starter-fundamentos`) from an English page. **Recommend keeping the cross-link** (the money path resolves via `_cta_buttons` to Stripe/mailto, not the Spanish prose, so it's language-clean); defer English one-pagers until a client needs them (YAGNI).

### Tasks

#### Task 5 (B1): English agency landing route (TDD)
- [ ] Failing test (`tests/test_one_plan_page.py`): `GET /one-plan` returns 200, body contains the H1 promise-B string, contains no banned word (assert absence of `10x`, `digital twin`, `audit-grade`, `certified`, etc.), FAQ JSON-LD present.
- [ ] Run — expect FAIL.
- [ ] Implement `webapp/one_plan_page.py` + `@app.get("/one-plan")` route in `webapp/app.py`, mirroring stocky page; wire the two real offers.
- [ ] Run — expect PASS. Add a banned-words guard test that scans the rendered HTML.
- [ ] Commit `feat: add English AU/NZ agency landing route`.

#### Task 6 (B2): outreach templates + FAQ block
- [ ] Add hook-C plain-text variants to `documentation/GTM_OUTREACH_TEMPLATES.md` (hypothesis-only, no $).
- [ ] Add the 3-deal-killer FAQ entries to Asset 1's `_FAQ`; verify JSON-LD still valid.
- [ ] Commit `docs: add hook-C outreach + agency-landing FAQ`.

---

## Part 4 — Track C: Cin7/Unleashed channel target list + outreach

### The 42 verified firms (Tier-A = rows 1-15 = start here)

Contact door = company site or directory profile URL only (no personal names/emails — enforced by schema). `Verify?` = publicly listed on the Unleashed directory but own domain not yet independently confirmed; resolve before use.

| # | Firm | Country | Ecosystem | Contact door | ICP |
|---|---|---|---|---|---|
| 1 | Integration Kings | AU | Cin7 | integrationkings.com | Tier-A (Elite) |
| 2 | 9 Yards | AU | Cin7 | 9yards.com.au | Tier-A |
| 3 | Software4Business | AU | Cin7 | software4business.com.au | Tier-A |
| 4 | SMB Consultants | AU(+NZ) | Cin7 | smbconsultants.com.au | Tier-A |
| 5 | Waypoint | AU+UK | Cin7 | wearewaypoint.com | Tier-A |
| 6 | GrowthPath | AU | Both | growthpath.com.au | Tier-A |
| 7 | WorkSmart | NZ | Both | worksmarter.co.nz | Tier-A |
| 8 | CNZ Consultants | NZ | Cin7 | cnzconsultants.co.nz | Tier-A |
| 9 | ZMC Consulting | NZ | Cin7 | zmc.nz | Tier-A |
| 10 | Beyond Expectations | NZ | Both | beyondexpectations.co.nz | Tier-A |
| 11 | Equation Limited | NZ | Both | equation.co.nz | Tier-A |
| 12 | Coconut Consulting | AU | Unleashed | coconutconsulting.com.au | Tier-A |
| 13 | Cloud Ease Consulting | AU | Unleashed | cloudease.com.au | Tier-A |
| 14 | Jiffi Consultancy | AU | Unleashed | jiffi.co | Tier-A |
| 15 | Divide by Zero Development | AU(+NZ) | Unleashed | Unleashed profile — Verify? | Tier-A |
| 16 | Neogen | AU | Unleashed | Unleashed profile — Verify? (NOT neogenaustralasia.com.au) | Tier-B |
| 17 | App Advisor | AU | Unleashed | Unleashed listing — Verify? | Tier-B |
| 18 | Cloudsolve | AU | Unleashed | Unleashed listing — Verify? | Tier-B |
| 19 | Business Continuum | AU | Unleashed | Unleashed listing — Verify? | Tier-B |
| 20 | Errant Venture | AU(+NZ) | Unleashed | Unleashed listing — Verify? | Tier-B |
| 21 | Invisible Business Solutions | AU | Unleashed | Unleashed listing — Verify? | Tier-B |
| 22 | Webs App Solutions | AU | Unleashed | Unleashed listing — Verify? | Tier-B |
| 23 | Jill of all Trades | AU | Unleashed | Unleashed listing — Verify? | Tier-C |
| 24 | Elevate Accounting | AU | Unleashed | Unleashed listing — Verify? | Tier-C |
| 25 | Eye On Books | AU | Unleashed | Unleashed listing — Verify? | Tier-C |
| 26 | ATB Partners | AU | Unleashed | Unleashed listing — Verify? | Tier-C |
| 27 | Adrians | AU | Unleashed | Unleashed listing — Verify? | Tier-C |
| 28 | ALTSHIFT | NZ | Unleashed | Unleashed listing — Verify? | Tier-B |
| 29 | Bennett Currie | NZ | Unleashed | Unleashed listing — Verify? | Tier-B |
| 30 | Consult Ltd | NZ | Unleashed | Unleashed listing — Verify? | Tier-B |
| 31 | Empower Business & Accounting Solutions | NZ | Unleashed | Unleashed listing — Verify? | Tier-C |
| 32 | Katalyst Office Management | NZ | Unleashed | Unleashed listing — Verify? | Tier-B |
| 33 | Living Business | NZ | Unleashed | Unleashed listing — Verify? | Tier-B |
| 34 | No Fuss Business | NZ | Unleashed | Unleashed listing — Verify? | Tier-C |
| 35 | Training & Beyond | NZ | Unleashed | Unleashed listing — Verify? | Tier-B |
| 36 | Coombe Smith | NZ | Unleashed | Unleashed listing — Verify? | Tier-C |
| 37 | Engine Room | NZ | Unleashed | Unleashed listing — Verify? | Tier-C |
| 38 | BDS Chartered Accountants | NZ | Unleashed | Unleashed listing — Verify? | Tier-C |
| 39 | BDO Digital | AU | Cin7 | bdo.com.au | Off-ICP (large) |
| 40 | BDO (Auckland/Waikato) | NZ | Unleashed | bdo.nz | Off-ICP (large) |
| 41 | RSM Australia | AU | Unleashed | rsm.global/australia | Off-ICP (large) |
| 42 | Staples Rodway (BDO) Christchurch | NZ | Unleashed | Unleashed listing | Off-ICP (large) |

### Target-list CSV schema (extend the uncommitted `../kern-au-nz-target-list.csv` seed — see Task C1)

```csv
Segment,Name,Website,Location,Country,Ecosystem,Partner_Tier,ICP_Fit,Sells,Why_fit,Personalization_hook,Contact_path,Contact_path_type,Directory_source,Directory_profile_url,Own_site_verified,Confidence,Outreach_stage,Last_verified_date,Notes
```

- New rows `Segment = "Channel partner - inventory implementer"`. `ICP_Fit ∈ {Tier-A, Tier-B, Tier-C, Off-ICP}`. `Outreach_stage ∈ {not_started, hypothesis_sent, nda_sample, diagnostico, puente, expand}`.
- **Guardrail enforced by schema:** `Contact_path` + `Directory_profile_url` are the ONLY contact fields. There is deliberately no `contact_name`/`email`/`phone` column — the sequence physically cannot target a named individual.

### Outreach sequence (spec — do NOT execute in build)

Positioning: **design-partner rev-share, "you operate Kern for your book."** Kern is the analysis engine they run *underneath their own retainer* — same client, same invoice, more clients per consultant-hour. First cohort (3-5 Tier-A firms): free/discounted access + white-glove onboarding in exchange for running it on 2-3 real client datasets + roadmap input. Partner keeps the client, Kern takes rev-share on the analysis layer. Reversible, no exclusivity.

| Stage | Goal | Action | Guardrail |
|---|---|---|---|
| 1. Hypothesis | Earn a reply | Cold touch via public contact door; storefront-signal hypothesis + operator rev-share angle | No client names, no $ figures |
| 2. NDA / sample-data | Get real data | Mutual NDA; partner supplies one anonymized client export | First point any real numbers appear |
| 3. Diagnóstico | Prove value | Run the real inventory diagnostic on the sample | Numbers grounded + cited |
| 4. Puente | Convert | Rev-share design-partner agreement; onboard partner to run Kern on 2-3 clients | Reversible, no exclusivity |
| 5. Expand | Scale | Roll across their book; standing wholesale/rev-share; anonymized case study | Case studies anonymized unless opt-in |

### Tasks

#### Task 7 (C1): persist the target-list CSV
- [ ] The ~66-row seed exists at `../kern-au-nz-target-list.csv` (PARENT of the repo, `C:\Users\Gamer\Music\scm\`, **UNCOMMITTED scratchpad — not in git**). Read its actual columns first, then **relocate it into the repo** (e.g. `documentation/operator/kern-au-nz-target-list.csv`) and extend with the schema above + the 42 channel rows. Do not assume the seed's column set — confirm it before merging schemas.
- [ ] Resolve every `Verify?` firm's directory `partner-profile/<slug>/` URL + own domain; set `Own_site_verified` and `Last_verified_date`.
- [ ] Commit `docs: enumerate Cin7/Unleashed AU-NZ channel target list`.

#### Task 8 (C2): outreach template for the channel
- [ ] Add the design-partner rev-share + storefront-hypothesis cold template to `documentation/GTM_OUTREACH_TEMPLATES.md` (Tier-A "you operate it" variant; Tier-C lighter refer/resell variant). Hypothesis-only, no $.
- [ ] Commit `docs: add channel-partner outreach sequence templates`.

---

## Part 5 — Decisions (FINALIZED at build time, auto-mode — see rationale)

Resolved with the plan's own proposed defaults before dispatching Task 1. No blockers remain.

1. **GMV band cutoffs — DECIDED:** `Starter = USD 1,000,000-3,000,000/yr`, `Growth = USD 3,000,000-8,000,000/yr`, `Scale = USD 8,000,000-15,000,000/yr` (open-ended top). These are the exact `RevenueBand.min_annual_revenue`/`max_annual_revenue` values Task A2 must hardcode.
2. **English landing route slug — DECIDED: `/one-plan`.** Maps directly to positioning line B (the moat spine: "Demand, stock, purchasing and pricing — one plan that stops fighting itself"). Route: `@app.get("/one-plan")` in `webapp/app.py`; page module `webapp/one_plan_page.py`.
3. **LatAm reduced-scope tool set — DECIDED: the original 8 pre-reclassification Starter tools** — `data_quality`, `abc_xyz`, `forecast`, `whatif`, `inventory_optimization`, `excel_replenishment`, `cycle_count`, `newsvendor`. Excludes the 7 "universal" tools PR #167 moved down from Growth (`excess_obsolete`, `financial_kpis`, `pricing`, `reconciliation`, `landed_cost`, `returns`, `risk`) — consistent with "reduced delivery scope, not the same product cheaper" (`MONETIZATION_BRIEF.md`'s own LatAm rationale).
4. **Primary-axis resolution** — RESOLVED in Part 2 (package_key is source of truth, annual_revenue suggests-only). Below-`$1M` → `needs_clarification` / route to the LatAm reduced-scope tier.
5. **AU/NZ currency on the landing page** — the English landing reuses USD-denominated offers. Survivable *only because the page prints no price* (CTAs resolve to Stripe/mailto). If any price ever appears on an AU/NZ surface, it must be AUD/NZD. Do not print a USD number on that page.

## Contradictions found across the docs (all resolve the same way)

`KERN_AGENCIA_IA_TESIS_COMERCIAL.md` + the ATLAS finding are the **governing layer**; earlier docs are inputs read through the thesis where they conflict.

1. "10x" is banned in customer copy but appears as internal positioning in `CLAUDE.md` / `KERN_IDENTIDAD §2.2` → fine internally, never customer-facing.
2. `KERN_IDENTIDAD §4` "operate/execute your chain" language is pre-ATLAS and stale → thesis §7 + ATLAS win; never lift into copy.
3. `MONETIZATION_BRIEF` "$2,700/mo" scenario is a fabricated average the thesis explicitly rebuts → never quote; use `package_specs.py` prices only; ≥$8k target = 2x growth or 4x starter.
4. Two package taxonomies coexist (`KERN_IDENTIDAD` SaaS LatAm ladder $300-5,000 vs the agency catalog $1,500-12,000) → the AU/NZ English build uses ONLY the thesis/`package_specs.py` catalog.
5. Currency: `MONETIZATION_BRIEF` USD "x1.5" vs thesis "bill in AUD/NZD absorbing FX+GST" → local currency on AU/NZ surfaces; thesis governs.
6. **The governing thesis's OWN §4/§5 price tables are stale** — they show the pre-#167 prices (`Starter 2,000 / Growth 4,000 / Scale 7,500 / Retainer 9,000-12,000`). `scm_agent/package_specs.py` (`900 / 1,500 / 3,200 / 4,500`) is the **price source of record**; the thesis §4/§5 tables predate PR #167 and **must not be quoted**. "Thesis governs" applies to *positioning/guardrails*, NOT to its obsolete price numbers.

---

## Self-review (done)

- **Spec coverage:** who-we-sell-to (Part 1), positioning (Part 1/3), pricing model (Part 2), lineamientos (Global Constraints), architecture (Parts 2-4), channel (Part 4) — all present.
- **Placeholder scan:** the only intentional unknowns are flagged as REQUIRED INPUT / `[PROPOSAL]` / `Verify?` — not silent placeholders. The GMV band cutoffs are honestly called out as the one blocker, not fabricated.
- **Type consistency:** `RevenueBand` / `SkuFairnessRule` / `PriceQuote` / `quote_price` / `render_price_string` names are used identically across Part 2's signatures and Task A2/A3.
- **Known limitation:** exact AU/NZ CPIM and Xero-advisor counts are undisclosed publicly (Phase-2 channels); the Cin7/Unleashed (Part 4) and Shopify counts are directly verified.
