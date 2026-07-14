# Discovery-Assisted Competitor Price Intelligence — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan PR-by-PR. Steps use checkbox (`- [ ]`) syntax for tracking. This repo requires TDD (RED/GREEN/REFACTOR) and >=80% coverage per the user's global rules.

**Goal:** A client hands Kern ONE competitor category/site URL. Kern onboards the domain itself (robots.txt only), crawls it, keeps the Product/Offer pages, homologates each discovered product against the client's own catalog through the existing `gtin -> fuzzy -> probabilistic -> adjudicate` cascade, watches it on a recurring schedule, prioritizes which of the client's SKUs are worth a price move, and produces a per-SKU action plan — with zero hand-prepared CSV and zero human intervention on the happy path. Any move to a MORE aggressive acquisition tier than the site's approved ceiling is ALWAYS surfaced as a pending-approval `GuidedOutcome`, never taken alone.

**Architecture:** This is the "discovery-assisted mode" that `jobs/price_intelligence.py`'s own docstring forward-references ("PR-14's `match/` package"). It reuses, never re-invents:
- `src/pricing_intel/acquire/base.py::require_approved_site` + `SiteConfig` for the compliance gate (a NEW `auto_approve.py` writes the YAML the gate reads).
- `jobs/seo_audit.py`'s `advertools.crawl()` + `structured.extract_product_metadata()` crawl pattern for discovery (third-party site => gated by `require_approved_site`, NOT `seo_audit`'s client-owned `confirmed_domain` gate).
- The full `src/pricing_intel/match/` cascade (`gtin` -> `fuzzy.block_candidates` -> `probabilistic.score_pair`/`classify_score` -> `adjudicate.adjudicate_pair`) + `sku_map.SkuMap` for homologation.
- `jobs/price_monitor.py`'s PR-15 continuous-monitoring precedent: register a `ScheduledJob` with `jobs.scheduler.JobRegistry` (`run_once()` in tests, `BackgroundScheduler` in prod), and converge on the SINGLE `accept_observation` function so sanity-gate -> ledger-append -> market-signal-event logic is never duplicated.
- `jobs/seo_priority.py`'s ABC-XYZ x second-signal -> per-SKU action pattern for `jobs/price_priority.py`.
- `src/guided.py::GuidedOutcome` (`EXECUTED`/`OPTIONS`/`HANDOFF`/`ESCALATED`) for the R5 "human must approve a ceiling raise" contract.

**Tech Stack:** Python 3.11+. Existing extras only (`pricing-intel`, `seo`/advertools, `matching`, `tower`/APScheduler) — NO new third-party dependency. `urllib.robotparser` (stdlib) for R1. Tests: `PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/ -q`. Lint (CI scope): `ruff check src tests examples`.

**Reference — read before touching code (verified against current repo state):**
`jobs/price_intelligence.py` (one-shot L1 precedent + "no silent caps" reporting) ·
`jobs/price_monitor.py` (continuous `accept_observation` + `PRICE_MONITOR_JOB` precedent) ·
`src/pricing_intel/acquire/base.py` (`require_approved_site`, `SiteConfig`, `CircuitBreaker`, `classify_blocking_signal`, `normalize_domain`) ·
`src/pricing_intel/models.py` (`SiteConfig` schema, `ACQUISITION_TIERS`, `TOS_DECISIONS`, `MatchCandidate`) ·
`src/pricing_intel/acquire/structured.py` (`extract_product_metadata`) ·
`jobs/seo_audit.py` (advertools crawl adapter) · `jobs/seo_priority.py` (ABC-XYZ crossing) ·
`src/pricing_intel/match/{gtin,fuzzy,probabilistic,adjudicate,sku_map}.py` ·
`scm_agent/monitors.py` (pure-detection sense layer) · `jobs/scheduler.py` (`JobRegistry`) ·
`src/guided.py` (`GuidedOutcome`) · `scm_agent/tools.py` (`price_intelligence_tool()` lazy-import recipe) · `scm_agent/tool_options.py` (`_ranked`/`price_intelligence_options`).

**Verified against live code (2026-07-13, spot-checked before saving this plan):** `SiteConfig` (src/pricing_intel/models.py:266) has fields `domain, robots_txt_respected, robots_checked_at, tos_summary, tos_decision, rate_limit_seconds, max_tier_allowed, pii_policy="none", selectors_version=None` — note `robots_checked_at` (ISO date str) and `tos_summary` (non-empty str) are BOTH required by `__post_init__` and must be populated by PR-1's `auto_approve_site` (e.g. `tos_summary="Auto-onboarded via robots.txt only; Terms of Service not reviewed by a human."`, `robots_checked_at=now.date().isoformat()`) or the write will raise. `ACQUISITION_TIERS = ("L0","L1","L2","L3")`, `TOS_DECISIONS = ("allowed","limited","prohibited")` confirmed. `src/guided.py` confirmed to export `GuidedOutcome`, `ExecutionOption`, `Residual`, `HandoffPacket`, `EscalationPacket`, `as_options`, `as_handoff`, `as_escalation`, `as_executed`, `verify_guided`, `passed_guided`. `scm_agent/tool_options.py` exists. `jobs/seo_priority.py` confirmed to have `ExcludedSku` (line 116) and `_assign_action` (line 174).

---

## Global Constraints

- **Branching:** This work must NOT build on `feat/state-snapshot-module`. Cut each PR's branch from `main` (`price-intel/<slug>`). Each PR: feature branch -> draft PR -> CI green (py3.11/3.12/3.13) -> squash-merge to `main` BEFORE starting the next dependent PR. Never push straight to `main`.
- **File-size limit:** 800 lines/file hard cap (`CLAUDE.md` "many small files"). `jobs/price_intelligence.py` is already ~680 lines — do NOT extend it; the discovery-assisted continuous mode gets its OWN `jobs/price_watch.py` (see PR-3).
- **ASCII-only in console prints** (Windows cp1252). Markdown/YAML written utf-8 is fine.
- **No silent caps (golden rule 14):** every discovered product / ref / SKU that does not become an accepted, homologated, or actioned row is reported with a machine-readable `reason` — exactly `jobs/price_intelligence.py`'s `RowOutcome`/`PriceIntelReport.rows` convention and `jobs/seo_priority.py`'s `ExcludedSku` convention.
- **Immutability & pure `src/`:** `src/pricing_intel/*` stays pure (no network I/O beyond the existing `acquire/*` fetchers); all crawl/network I/O lives in `jobs/*`. Frozen dataclasses; new objects, never in-place mutation.
- **Test naming:** one file per behavior area (`tests/test_pricing_intel_auto_approve.py`, etc.), never a monolith.

## NON-GOALS (guardrails — the plan schedules NO work for these; every PR that could drift toward one carries an explicit risk callout)

1. **Never evade a block.** No retry-past-403/429, no user-agent rotation, no proxy pool, no header spoofing. The existing `CircuitBreaker` degrade-only path (`classify_blocking_signal` -> `record_failure` -> tier step-down) is the ONLY block response. advertools runs with `ROBOTSTXT_OBEY=True` + identifiable non-rotating UA + bounded delay/concurrency (reuse `seo_audit`'s settings).
2. **Never auto-approve ToS.** Only robots.txt self-approves. The auto-written YAML is ALWAYS `tos_decision: limited`, NEVER `allowed` (traceable that no human reviewed the ToS).
3. **Never touch PII.** `pii_policy` stays `"none"` in every written config (`SiteConfig.__post_init__` enforces this; a test asserts it too).
4. **Never write/mutate the competitor's price or our own catalog.** 100% read/observation. No writeback connector is invoked anywhere in this feature. The R5 ceiling raise is surfaced for a human, never applied to a `SiteConfig` file by the engine.

## WHERE R5's `GuidedOutcome` CHECK PHYSICALLY LIVES (called out per task request)

- The **decision** is a pure function `plan_watch_escalation(...) -> WatchEscalationDecision` introduced in **PR-8** (`src/pricing_intel/watch_policy.py`). It compares a desired watch tier against the site's already-approved `SiteConfig.max_tier_allowed`. It NEVER mutates a `SiteConfig`.
- The **check is wired into the call chain in PR-9**, inside `jobs/price_watch.py`'s scaling step, and is evaluated **BEFORE any tier/cadence change is applied**, by construction: the function that would apply a change is only reached on the `approved_within_ceiling` branch. On the `needs_ceiling_raise` branch the function returns a `GuidedOutcome` (`OPTIONS`/`HANDOFF`) and the caller applies NOTHING. There is no code path that raises the tier and then asks.
- The tool's `options` hook (**PR-11**) surfaces that pending-approval `GuidedOutcome` to the operator instead of an `EXECUTED` outcome, so the human sees the ceiling-raise request as a ranked choice.
- **CONFIRMED with the user (2026-07-13):** there is no "auto-escalate without asking" branch anywhere in this plan. A tier raise beyond the approved ceiling ALWAYS produces a `GuidedOutcome`, no exceptions.

---

## File Structure

```
src/pricing_intel/
  acquire/
    auto_approve.py        # NEW (PR-1)  robots.txt self-onboarding -> config/sites/<domain>.yaml ("limited")
  discover.py              # NEW (PR-2)  pure: keep only Product/Offer pages from crawled HTML
  homologate.py            # NEW (PR-4)  pure: gtin->fuzzy->probabilistic->adjudicate cascade -> homologation table
  watch_policy.py          # NEW (PR-8)  pure: R5 bounded-escalation guard -> WatchEscalationDecision / GuidedOutcome
jobs/
  price_watch.py           # NEW (PR-3/5/6/9)  discovery-assisted playbook: prepare(URL)->crawl->homologate->watch cycle
  price_priority.py        # NEW (PR-10) ABC-XYZ x price_position_matrix -> per-SKU action
scm_agent/
  monitors.py              # MODIFY (PR-7) add competitor_price_move_monitor + wire into run_all_monitors
  tools.py                 # MODIFY (PR-11) register price_watch tool (39th) via lazy-import recipe
  tool_options.py          # MODIFY (PR-11) price_watch_options -> GuidedOutcome (OPTIONS, surfaces R5 escalation)
config/sites/
  discovered-retailer.test.yaml   # NEW (PR-1 test fixture, .test TLD, "limited")
tests/
  test_pricing_intel_auto_approve.py     # NEW (PR-1)
  test_pricing_intel_discover.py         # NEW (PR-2)
  test_price_watch_discovery.py          # NEW (PR-3)
  test_pricing_intel_homologate.py       # NEW (PR-4)
  test_price_watch_homologation.py       # NEW (PR-5)
  test_price_watch_cycle.py              # NEW (PR-6)
  test_monitors_price_move.py            # NEW (PR-7)
  test_pricing_intel_watch_policy.py     # NEW (PR-8)
  test_price_watch_scaling.py            # NEW (PR-9)
  test_price_priority.py                 # NEW (PR-10)
  test_price_watch_tool.py               # NEW (PR-11)
  test_price_watch_e2e.py                # NEW (PR-12)
examples/
  run_price_watch.py       # NEW (PR-12) runnable CLI: one URL -> full deliverable set
CLAUDE.md, HANDOFF.md      # MODIFY (PR-12) tool count 38->39, pointer to this mode
```

---

### Task 1 (PR-1): Auto-onboarding — `auto_approve.py` (R1)

**Files:** Create `src/pricing_intel/acquire/auto_approve.py`, `tests/test_pricing_intel_auto_approve.py`, `config/sites/discovered-retailer.test.yaml` (fixture).

**Adds:** Given a URL, resolve the bare domain (reuse `base.normalize_domain`), read the domain's real robots.txt via `urllib.robotparser.RobotFileParser`, and — only if it permits the fetch for our identifiable UA — write `config/sites/<domain>.yaml` with `tos_decision: limited`, `robots_txt_respected: true`, `pii_policy: none`, `max_tier_allowed: L1` (the minimal tier discovery needs — NEVER higher), `rate_limit_seconds` = robots.txt `Crawl-delay` if present else a safe default (e.g. 5.0), `robots_checked_at` = today's ISO date, `tos_summary` = a fixed auto-onboarding disclosure string (see note above — both required by `SiteConfig.__post_init__`). If robots disallows: write NOTHING, return a rejection with a reason.

**Interfaces (signatures only — Plan First, no bodies):**
- Constant `AUTO_TOS_DECISION = "limited"` (module-level; used AND asserted in tests so a future edit to `"allowed"` fails).
- `AUTO_MAX_TIER = "L1"`.
- `AUTO_TOS_SUMMARY = "Auto-onboarded via robots.txt only; Terms of Service not reviewed by a human."` (constant, asserted in a test).
- `@dataclass(frozen=True) OnboardingResult(domain: str | None, approved: bool, config_path: Path | None, reason: str)`.
- `auto_approve_site(url: str, *, config_dir: Path | str = base.DEFAULT_SITES_CONFIG_DIR, robots_reader: Callable[[str, str], bool] | None = None, user_agent: str = pdp_fetcher.USER_AGENT, now: date | None = None) -> OnboardingResult`.
  - `robots_reader(robots_url, user_agent) -> bool` is dependency-injected so tests run fully offline (default impl builds a `RobotFileParser`, `.set_url(...)`, `.read()`, returns `.can_fetch(user_agent, url)`); network read is the ONLY I/O and it is behind this seam.
  - Delegates the allow/deny decision to `RobotFileParser.can_fetch` semantics (401/403 robots => disallow-all; 404/absent => allow-all per RFC 9309). Documented in the docstring; a fetch failure that isn't a clean allow is treated conservatively as a rejection.
  - **Idempotent + non-destructive:** if `config/sites/<domain>.yaml` already exists, return `approved` reflecting its existing `SiteConfig.is_approved` and DO NOT overwrite it (never silently downgrade a human's `allowed`/`prohibited` or re-date a reviewed record). Reason string says `config_already_exists`.
  - Writes via `yaml.safe_dump`; re-loads through `base.load_site_config` before returning so a malformed write fails loudly (fail-fast at the boundary).

**Test plan (RED first):**
- `test_writes_limited_config_when_robots_allows` — inject a reader returning True; assert file created AND `load_site_config(...).tos_decision == "limited"` AND `.max_tier_allowed == "L1"` AND `.pii_policy == "none"` AND `.robots_txt_respected is True`. **Explicitly assert the file text does NOT contain `allowed`** for `tos_decision`.
- `test_never_writes_allowed` — assert module constant `AUTO_TOS_DECISION == "limited"` and that the written `SiteConfig.is_approved` is via the `limited` path.
- `test_rejects_and_writes_nothing_when_robots_disallows` — reader returns False; assert `approved is False`, `config_path is None`, no file on disk, reason mentions `robots_disallow`.
- `test_id_only_or_malformed_url_rejected` — a bare id / non-http URL -> `domain is None`, rejected, no file (reuses `normalize_domain` returning None).
- `test_existing_config_not_overwritten` — pre-place `example-blocked.test.yaml`-style `prohibited` config; call auto-approve; assert file byte-unchanged and result reflects `is_approved False`, reason `config_already_exists`.
- `test_robots_crawl_delay_becomes_rate_limit` — reader reports a crawl-delay; assert `rate_limit_seconds` reflects it (else default).
- `test_required_siteconfig_fields_populated` — assert `robots_checked_at` is a valid ISO date string and `tos_summary` is non-empty (guards against a `__post_init__ValueError` on write).
- GREEN: implement. REFACTOR: extract the `RobotFileParser` default reader into a small named helper; keep function <50 lines.

**Dependencies:** none (foundation PR).

**Risk callouts:**
- CRITICAL / NON-GOAL 2: the ONLY tempting drift is writing `tos_decision: allowed`. Mitigated by the module constant + two dedicated assertions (constant value AND file text).
- NON-GOAL 1: auto-approve must NOT self-grant a high tier — `max_tier_allowed` is fixed at `L1`; a test asserts it (a future L2/L3 needs a human, which is also what R5 enforces downstream).
- The robots read is the single network touch — keep it injectable so CI never hits a real robots.txt.

---

### Task 2 (PR-2): Discovery page filter — `discover.py` (R2, pure half)

**Files:** Create `src/pricing_intel/discover.py`, `tests/test_pricing_intel_discover.py`.

**Adds:** A pure function that, given already-crawled pages (URL + HTML string), keeps only those carrying `Product`/`Offer` structured data, reusing `structured.extract_product_metadata`. No network I/O (mirrors `structured.py`'s invariant).

**Interfaces:**
- `@dataclass(frozen=True) DiscoveredProduct(url: str, site: str, title: str | None, brand: str | None, gtin: str | None, price_hint: str | None, offers: tuple[dict, ...])` — the L1 discovery record downstream homologation consumes.
- `filter_product_pages(pages: Iterable[CrawledPageLike], *, site: str) -> list[DiscoveredProduct]` where `CrawledPageLike` is any object/dict with `.url`/`.html` (accepts `seo_audit.crawl_audit.CrawledPage` and a plain `{"url","html"}` dict, so tests need no live crawl). A page whose `extract_product_metadata` yields no JSON-LD/microdata Offer node is dropped (not an error — an honest "not a product page").
- Small pure extractors `_title_from(meta)`, `_brand_from(meta)`, `_gtin_from(meta)` pulling schema.org fields out of the extracted offers/product nodes; missing fields stay `None` (never fabricated — golden rule 14 applied to structure, same discipline as `structured.py`).

**Test plan (RED first):**
- `test_keeps_page_with_jsonld_offer` — HTML fixture with a valid `<script type="application/ld+json">` Product/Offer -> one `DiscoveredProduct` with parsed title/brand/gtin.
- `test_drops_page_without_structured_data` — plain HTML, no ld+json/microdata -> dropped.
- `test_extracts_gtin_when_present_else_none` — Offer with `gtin13` vs one without -> `gtin` populated vs `None`.
- `test_handles_malformed_ldjson_via_fallback` — malformed script block; assert no crash and honest degrade (reuses `structured.py`'s chompjs/regex fallback path).
- `test_empty_and_missing_html_are_dropped_not_errors`.
- GREEN + REFACTOR.

**Dependencies:** none new (imports existing `structured.py`). Independently mergeable — pure and offline.

**Risk callouts:** none touch a NON-GOAL (pure, no network, no PII fields read — `pii_policy` stays none; only product/price schema fields are read).

---

### Task 3 (PR-3): Discovery crawl wiring — `jobs/price_watch.py` skeleton + `prepare()` (R2, network half)

**Files:** Create `jobs/price_watch.py`, `tests/test_price_watch_discovery.py`.

**Adds:** The discovery-assisted playbook's `prepare(seed_url, params)` — the network entry point. Flow: `auto_approve_site(seed_url)` (PR-1) -> if not approved, return a `needs_data`-style honest skip with the rejection reason (NO crawl) -> else `require_approved_site(domain)` (hard gate; raises if somehow unconfigured) -> crawl via an advertools adapter copied from `seo_audit._crawl_domain` (same `ROBOTSTXT_OBEY=True`, identifiable UA, bounded `DOWNLOAD_DELAY`/`CONCURRENT_REQUESTS_PER_DOMAIN`, `xpath_selectors={"page_html": "/html"}`) -> `discover.filter_product_pages(...)`.

**Interfaces:**
- `prepare(seed_url: str, params: dict | None = None) -> dict` returning `{"domain","discovered": list[DiscoveredProduct], "onboarding": OnboardingResult, "skipped_reason": str | None, ...}`. First positional arg is a URL, NOT a `data_path` (same shape as `seo_audit.prepare` / `price_intelligence` one-shot mode).
- Reuse (do not re-copy) `AdvertoolsUnavailableError` handling pattern from `seo_audit`.
- The crawl adapter uses `require_approved_site`'s `SiteConfig` gate — deliberately NOT `seo_audit`'s `confirmed_domain` gate, because this is a THIRD-PARTY competitor site under the pricing-intel ToS/robots-approval workflow, not the client's own SEO site.

**Test plan (RED first):**
- `test_prepare_skips_without_crawl_when_robots_disallows` — monkeypatch `auto_approve_site` to return a rejection; assert crawl adapter is NEVER called (patch the crawl fn to raise if invoked) and `skipped_reason` is surfaced.
- `test_prepare_gates_on_require_approved_site` — a `prohibited` config in a tmp `config_dir` -> `SiteNotApprovedError` handled into an honest skip, no crawl.
- `test_prepare_filters_to_product_pages` — inject a fake crawl DataFrame (reuse `seo_audit.pages_from_crawl_dataframe` shape) -> only product pages survive.
- `test_crawl_adapter_uses_robotstxt_obey_and_identifiable_ua` — assert the custom_settings passed to the crawl carry `ROBOTSTXT_OBEY=True` and a non-rotating identifiable UA (guards NON-GOAL 1).
- GREEN + REFACTOR (keep `prepare` <50 lines; push the crawl adapter into a private helper).

**Dependencies:** PR-1 (auto_approve), PR-2 (discover). Requires both merged.

**Risk callouts:**
- CRITICAL / NON-GOAL 1: the crawl must reuse advertools' robots-obeying, non-evasive settings verbatim — a test asserts `ROBOTSTXT_OBEY=True` + identifiable UA. No retry/proxy/UA-rotation anywhere.
- Acceptance criterion "robots disallows => NO config, NO fetch": enforced here (skip before crawl) + PR-1 (no config). Dedicated test above.
- Do NOT import `jobs.price_watch` at `scm_agent/tools.py` top-of-module (circular-import hazard) — PR-11 uses the lazy-import recipe.

---

### Task 4 (PR-4): Homologation cascade — `homologate.py` (R3, pure)

**Files:** Create `src/pricing_intel/homologate.py`, `tests/test_pricing_intel_homologate.py`.

**Adds:** A pure orchestration of the existing match cascade: for each `DiscoveredProduct` vs the client's own catalog (`list[fuzzy.ProductAttributes]` + optional GTIN map), run `gtin.match_by_gtin` first; on `None`, `fuzzy.block_candidates` to shortlist, then `probabilistic.score_pair` + `classify_score` on the best block candidate; feed the [0.5,0.85) band to `adjudicate.adjudicate_pair` (which returns `deferred` with no LLM wired — never auto-confirms). Emits one `HomologationRow` per discovered product: `my SKU <-> competitor product <-> method <-> confidence <-> status`. NO I/O — `sku_map` persistence is the caller's job (PR-5), exactly as `gtin.py`/`probabilistic.py` document.

**Interfaces:**
- `@dataclass(frozen=True) HomologationRow(our_product_id: str | None, competitor_sku_ref: str, site: str, method: str, score: float, status: str, reason: str, confirmed_by: str | None)` — projects a `MatchCandidate` plus the "unmatched" case (`our_product_id=None`, `status="rejected"`, reason).
- `homologate(discovered: Sequence[DiscoveredProduct], our_catalog: Sequence[ProductAttributes], *, our_gtins: dict[str,str] | None = None, llm: adjudicate.LlmAdjudicator | None = None, now: datetime | None = None) -> HomologationReport` where `HomologationReport` carries `rows: tuple[HomologationRow,...]`, `n_confirmed`, `n_suspect`, `n_unmatched`, and `unmatched: tuple[...]` (golden rule 14 — a discovered product that matches nothing is reported, never dropped).
- Auto-confirm ONLY via `gtin` (0.99) or `probabilistic >= CONFIRM_THRESHOLD` (0.96); everything in the suspect band stays `suspect`/`deferred` — surfaced for human review, never persisted as confirmed here.

**Test plan (RED first):**
- `test_gtin_exact_match_confirms` — matching valid GTIN both sides -> row `method="gtin"`, `status="confirmed"`, `confirmed_by="auto"`.
- `test_high_probabilistic_confirms` — reworded-title/same-brand pair (probabilistic worked example 1) -> `confirmed`.
- `test_ambiguous_pair_stays_suspect_not_confirmed` — probabilistic worked example 3 (0.9484...) -> `suspect`, `confirmed_by=None` (guards silent auto-confirm).
- `test_attribute_conflict_rejected` — worked example 2 (WH-1000XM5 vs XM4) -> `rejected`.
- `test_discovered_product_matching_nothing_is_reported` — a competitor product with no plausible block candidate -> appears in `unmatched` with a reason, counted in `n_unmatched`.
- `test_no_llm_defers_never_fabricates` — band pair with `llm=None` -> `adjudicate` returns `deferred`; row stays `suspect`.
- GREEN + REFACTOR.

**Dependencies:** PR-2 (`DiscoveredProduct`). Independently mergeable (pure).

**Risk callouts:**
- HIGH: the temptation is to auto-confirm the suspect band to raise "coverage". Forbidden — dedicated `stays_suspect` / `defers` tests. Only gtin/probabilistic>=0.96 auto-confirm; the whole point of the human-review states (`suspect`/`deferred`) is preserved.
- NON-GOAL 3: homologation reads title/brand/gtin/attributes only — no PII.

---

### Task 5 (PR-5): Persist homologation + publish the table — `price_watch.py` (R3, wiring)

**Files:** Modify `jobs/price_watch.py`; create `tests/test_price_watch_homologation.py`.

**Adds:** Wire `homologate(...)` into the playbook after discovery, persist each `confirmed`/`suspect` row to `sku_map.SkuMap.record(...)` (append-only, versioned — golden rule 8; `confirmed_by` set only for genuine auto-confirm rows), and publish the homologation table deliverable (`homologation_table.csv`) with columns `my_sku, competitor_product, method, confidence, status` plus a separate `unmatched` sheet/CSV (golden rule 14). Uses `defuse_formula` on every string cell (existing CSV/Excel formula-injection guard, same as `price_intelligence.write_operational`).

**Interfaces:**
- `run_homologation(payload: dict, *, sku_map: SkuMap | None = None, now: datetime | None = None) -> HomologationReport` (defaults to `default_sku_map()`, never closes the shared singleton — mirror `price_monitor.run_price_monitor_cycle`'s singleton-lifecycle discipline).
- `write_homologation(report, out_dir, client="Client") -> dict[str, Path]`.

**Test plan (RED first):**
- `test_confirmed_rows_persisted_to_sku_map` — pass an isolated `SkuMap(tmp_path)`; assert `list_all_confirmed()` returns the gtin/probabilistic-confirmed pairs only.
- `test_suspect_rows_recorded_but_not_confirmed` — assert a suspect row is recorded with `status="suspect"`, `confirmed_by=None`.
- `test_table_written_with_all_columns_and_unmatched_sheet`.
- `test_formula_injection_defused_in_table` — a competitor title starting with `=` is neutralized.
- `test_shared_sku_map_singleton_not_closed`.
- GREEN + REFACTOR.

**Dependencies:** PR-3 (playbook skeleton), PR-4 (homologate).

**Risk callouts:**
- NON-GOAL 4: `sku_map.record` is append-only match metadata, not a writeback to the competitor or our catalog — verify no `src/writeback.py` connector is invoked.
- Golden rule 8: never overwrite a prior `sku_map` version; a re-run adds a new version (test via `history`).

---

### Task 6 (PR-6): Recurring watch cycle + scheduler registration — `price_watch.py` (R4, scheduler half)

**Files:** Modify `jobs/price_watch.py`; create `tests/test_price_watch_cycle.py`.

**Adds:** `run_price_watch_cycle(...)` — one continuous-monitoring cycle over the CONFIRMED discovery matches (`sku_map.list_all_confirmed()` scoped to discovery sites), re-acquiring each via the L1 PDP path (`pdp_fetcher.fetch_pdp_html` gated by `require_approved_site` + `CircuitBreaker`, exactly `price_intelligence._acquire_one`'s discipline) and converging on `jobs.price_monitor.accept_observation` (REUSED — no second sanity/ledger/market-signal implementation). Register `PRICE_WATCH_JOB = ScheduledJob(id="price_watch_cycle", func=run_price_watch_cycle, trigger="interval", trigger_args={"hours": DEFAULT_CADENCE_HOURS})` — same shape as `PRICE_MONITOR_JOB`; `run_once()` in tests, no daemon/sleep (golden rule 9).

**Interfaces:**
- `run_price_watch_cycle(*, sku_map=None, ledger=None, event_ledger=None, http_client=None, sites_config_dir=None, now=None) -> PriceWatchCycleReport` — plain, all-default-kwargs, synchronous (registrable as `ScheduledJob.func`). Report carries `outcomes: tuple[PairOutcome,...]` and an `events` roll-up (reuse the `PriceMonitorCycleReport` shape/vocab — `accepted/quarantined/discarded/skipped`).
- Only re-acquires L1-approved sites within `SiteConfig.max_tier_allowed`; a domain approved only to a lower tier is honestly `skipped` (reason `tier_not_approved`), never silently escalated.

**Test plan (RED first):**
- `test_cycle_reacquires_confirmed_discovery_pairs` — seed an isolated `SkuMap` with a confirmed pair + a mock `httpx.MockTransport` PDP; assert one `accepted` outcome and a ledger append.
- `test_cycle_converges_on_accept_observation` — assert market-signal events come from `accept_observation` (spy), not a re-implementation.
- `test_403_degrades_via_circuit_breaker_not_retry` — mock a 403; assert `record_failure`/`site_degraded` path, no retry, outcome `skipped: blocked_403` (guards NON-GOAL 1).
- `test_run_once_is_synchronous_no_daemon` — register `PRICE_WATCH_JOB` in an isolated `JobRegistry`, call `run_once`; assert it returns without a background thread.
- `test_tier_beyond_ceiling_skipped_not_escalated_here` — a site approved only to L0 -> `skipped: tier_not_approved` (the ESCALATION to raise it is PR-9's concern, not this cycle's).
- GREEN + REFACTOR.

**Dependencies:** PR-5 (confirmed pairs exist in sku_map). Requires `jobs/price_monitor.py`'s `accept_observation` (already on `main`).

**Risk callouts:**
- CRITICAL: converge on `accept_observation` — a duplicated sanity/ledger path is a review-blocking finding (spec's PR-15 precedent). Dedicated spy test.
- NON-GOAL 1: 403/429 -> `CircuitBreaker` degrade only. Dedicated test.
- No daemon/sleep in tests (golden rule 9). Dedicated `run_once` test.

---

### Task 7 (PR-7): Control Tower monitor — `scm_agent/monitors.py` (R4, sense half)

**Files:** Modify `scm_agent/monitors.py`; create `tests/test_monitors_price_move.py`.

**Adds:** `competitor_price_move_monitor(...)` — a PURE sense-layer adapter (matching `monitors.py`'s "a monitor never touches src.state itself; its caller passes the data in" convention) that promotes already-emitted pricing market-signal events (`price_move`/`competitor_oos`/`promo_detected` from `src.pricing_intel.events`, produced by `accept_observation` in PR-6) into Control-Tower-routable `Event`s, dedup'd via the shared `EventLedger` (`_emit` helper) — NEVER a second price-move detector. Wire it into `run_all_monitors` behind a `_monitor_enabled(config, "competitor_price_move")` flag + a `config/monitors.yaml` entry.

**Interfaces:**
- `EVENT_COMPETITOR_PRICE_MOVE = "competitor_price_move"` (distinct from inventory event types).
- `competitor_price_move_monitor(price_signal_events: list[Event], *, source=SOURCE, ledger: EventLedger | None = None) -> list[Event]` — dedup via `_emit`; `dedup_key` = `"{sku}:{event_type}"` (existing convention).
- `run_all_monitors` gains an optional read of recent pricing-signal events (passed in / read from the pricing ledger by the caller, keeping the monitor pure) so the A1 cycle surfaces competitor price moves alongside inventory conditions.

**Test plan (RED first):**
- `test_promotes_price_move_event_to_control_tower` — feed a `price_move` event; assert one `competitor_price_move` Control Tower event.
- `test_dedup_collapses_repeat_move` — same event twice through a real `EventLedger`; second run yields `[]`.
- `test_no_signal_no_events`.
- `test_does_not_reimplement_detection` — feed a NON-signal event (e.g. `site_degraded`); assert it is not promoted (the monitor adapts existing signals, it does not re-derive "is this a move").
- `test_run_all_monitors_includes_price_move_when_enabled`.
- GREEN + REFACTOR.

**Dependencies:** PR-6 (the cycle that emits the price-move signals). Mergeable independently of PR-6 at the unit level (monitor is pure, tested with hand-built events), but the end-to-end wiring assumes PR-6.

**Risk callouts:**
- MEDIUM / precedent: do NOT introduce a "second scheduling mechanism" or a second detection path — the monitor consumes existing `detect_market_signal_events` output. Dedicated `does_not_reimplement_detection` test.

---

### Task 8 (PR-8): Bounded auto-scaling guard — `watch_policy.py` (R5, pure decision)

**Files:** Create `src/pricing_intel/watch_policy.py`, `tests/test_pricing_intel_watch_policy.py`.

**Adds:** The pure R5 guard. Given a high-value SKU's desired watch aggressiveness (a target cadence and/or a target acquisition tier) and the site's already-approved `SiteConfig.max_tier_allowed`, decide: (a) `approved_within_ceiling` — a cadence increase that stays within the approved tier (safe to apply), or (b) `needs_ceiling_raise` — the desired tier exceeds the ceiling, which returns a `GuidedOutcome` (`OPTIONS`/`HANDOFF`) asking a human to raise the ceiling and applies NOTHING. NEVER mutates a `SiteConfig`; NEVER returns an `EXECUTED` outcome for a tier raise.

**Interfaces:**
- `@dataclass(frozen=True) WatchEscalationDecision(kind: str, applied_cadence_hours: float | None, guided: GuidedOutcome | None, reason: str)` where `kind in ("approved_within_ceiling", "needs_ceiling_raise", "no_change")`.
- `plan_watch_escalation(*, site_config: SiteConfig, current_cadence_hours: float, desired_cadence_hours: float, desired_tier: str, sku_value_rank: str, now=None) -> WatchEscalationDecision`.
  - Cadence-only tightening within the approved `max_tier_allowed` -> `approved_within_ceiling` (bounded by a floor cadence constant, e.g. no faster than the site's `rate_limit_seconds` implies).
  - `ACQUISITION_TIERS.index(desired_tier) > ACQUISITION_TIERS.index(site_config.max_tier_allowed)` -> `needs_ceiling_raise`, with `guided = as_options(...)` / `as_handoff(...)` carrying: the SKU, the current ceiling, the requested tier, the reason (high-value SKU), and a `HandoffPacket` whose steps are "review this domain's ToS + robots for the higher tier and, if cleared, raise `max_tier_allowed` in `config/sites/<domain>.yaml`". A `Residual` states the risk of NOT raising (staler reads on a high-value SKU). Passes `verify_guided` (has an executable path).

**Test plan (RED first):**
- `test_cadence_tightening_within_ceiling_is_applied` — desired tier == ceiling, faster cadence -> `approved_within_ceiling`, `applied_cadence_hours` set, `guided is None`.
- `test_tier_raise_beyond_ceiling_returns_guided_never_applies` — desired L2 vs ceiling L1 -> `needs_ceiling_raise`, `applied_cadence_hours is None`, `guided.status in (OPTIONS, HANDOFF)`, `passed_guided(guided) is True`.
- `test_guided_outcome_is_never_executed_for_a_raise` — assert `guided.status != EXECUTED`.
- `test_never_mutates_site_config` — pass a frozen `SiteConfig`; assert it is unchanged and no config file is written (guard is pure).
- `test_floor_cadence_respected` — a desired cadence faster than the rate-limit floor is clamped, not exceeded.
- GREEN + REFACTOR.

**Dependencies:** none new (imports `models.SiteConfig`, `guided`). Independently mergeable (pure).

**Risk callouts:**
- CRITICAL / R5 CONFIRMED DECISION: there is NO "auto-escalate without asking" branch — a tier beyond the ceiling ALWAYS returns a `GuidedOutcome`. Dedicated `returns_guided_never_applies` + `never_executed` tests. The user explicitly confirmed this branch; do not add the auto-escalate alternative.
- NON-GOAL 4: never mutates a `SiteConfig` / never writes a config. Dedicated `never_mutates` test.

---

### Task 9 (PR-9): Wire the R5 guard into the watch cycle — `price_watch.py` (R5, wiring — the call-chain location)

**Files:** Modify `jobs/price_watch.py`; create `tests/test_price_watch_scaling.py`.

**Adds:** In the cycle's per-SKU scaling step, call `plan_watch_escalation(...)` **before** any cadence/tier change is applied. On `approved_within_ceiling`, apply the tighter cadence for that SKU within the cycle's own scheduling (bounded, in-process). On `needs_ceiling_raise`, collect the returned `GuidedOutcome` into the cycle report's `pending_escalations` and apply NOTHING to that SKU's tier. The report exposes `pending_escalations: tuple[GuidedOutcome,...]` so the tool (PR-11) and any operator surface can render them.

**Interfaces:**
- `PriceWatchCycleReport` gains `pending_escalations: tuple[GuidedOutcome, ...]`.
- The scaling call site is a single private helper `_scale_one(entry, site_config, ...) -> tuple[float | None, GuidedOutcome | None]` — the ONLY place a cadence/tier change is decided, and it delegates the decision entirely to `watch_policy.plan_watch_escalation`. There is no other code path that changes a tier.

**Test plan (RED first):**
- `test_high_value_within_ceiling_tightens_cadence` — assert the SKU's applied cadence tightened and NO escalation surfaced.
- `test_high_value_beyond_ceiling_surfaces_escalation_applies_nothing` — assert `pending_escalations` non-empty AND the site's effective tier for that SKU is UNCHANGED (no L2 acquisition attempted).
- `test_escalation_checked_before_apply` — spy on `plan_watch_escalation`; assert it is called and, on `needs_ceiling_raise`, the tier-apply helper is NOT reached (order/short-circuit proof).
- `test_pending_escalations_pass_verify_guided`.
- GREEN + REFACTOR.

**Dependencies:** PR-6 (cycle), PR-8 (guard).

**Risk callouts:**
- CRITICAL / R5: the acceptance criterion "if the engine wants to raise a tier beyond what's approved, it surfaces as a pending-approval option, never executes alone" is proven HERE by `beyond_ceiling_surfaces_escalation_applies_nothing` + `escalation_checked_before_apply`. This is the physical call-chain location of the guard.

---

### Task 10 (PR-10): Value-based prioritization — `jobs/price_priority.py` (R6)

**Files:** Create `jobs/price_priority.py`, `tests/test_price_priority.py`.

**Adds:** A pure cross-referencing playbook mirroring `jobs/seo_priority.py`: cross `classify_portfolio` (ABC-XYZ via `jobs.abc_xyz_job`) with the `price_position_matrix` signal (each SKU's `position_index = our_price / avg competitor price`, reusing `price_intelligence._position_index` semantics / a `PriceIntelReport`) -> ONE action per SKU: `igualar_precio` / `oportunidad_subir` / `vigilar` / `ignorar_bajo_valor`. Invents NO new demand/price computation (golden rule 1). Reports every SKU present in only one input (golden rule 14). Has `verify`/`price_priority_passed` QA (matching `jobs/qa.py` naming).

**Action rule (documented, deterministic — mirrors `seo_priority._assign_action`):**
- `ignorar_bajo_valor` — C-class (low importance) SKU: not worth a price action this cycle.
- `igualar_precio` — A/B-class AND `position_index > 1 + band` (we're materially pricier than the competitor average): match down to defend volume.
- `oportunidad_subir` — A/B-class AND `position_index < 1 - band` (we're materially cheaper): margin headroom, raise toward market.
- `vigilar` — A/B-class AND `position_index` within `[1-band, 1+band]` (at market) OR no confirmed competitor read yet (`insufficient_signal`): keep watching.
- `band` is a param (default 0.05), same "honest neutral band" idea as `seo_priority`'s `trend_threshold_pct`.

**Interfaces:**
- `@dataclass(frozen=True) SkuPriceAction(product_id, action, abc, xyz, position_index: float | None, competitor_read: str, reason: str)`.
- `@dataclass(frozen=True) ExcludedSku(product_id, reason)` (same shape as `seo_priority`).
- `prepare(data_path, params)` (demand-history CSV feeds ABC-XYZ; `params["price_report"]` or a price-position CSV supplies the competitor side — mirror `seo_priority`'s multi-input `params` convention), `run(payload) -> PricePriorityReport`, `verify(report)`, `write_operational(report, out_dir, client)` (dual CSV: actions + excluded).

**Test plan (RED first):**
- `test_a_class_pricier_gets_igualar_precio`.
- `test_a_class_cheaper_gets_oportunidad_subir`.
- `test_at_market_gets_vigilar`.
- `test_c_class_gets_ignorar_bajo_valor`.
- `test_sku_without_confirmed_read_gets_vigilar_insufficient_signal_not_a_guess`.
- `test_sku_in_only_one_input_is_excluded_and_reported`.
- `test_verify_flags_invalid_action_or_missing_reason`.
- GREEN + REFACTOR.

**Dependencies:** none hard-blocking (can consume a `PriceIntelReport`/matrix CSV independently), but semantically completes the pipeline after PR-5/PR-6. Best merged after PR-5 so the E2E (PR-12) can chain it.

**Risk callouts:**
- MEDIUM: pure cross-reference — do NOT re-derive demand or price math (golden rule 1). `insufficient_signal` test guards against fabricating an action where there's no confirmed competitor read.
- NON-GOAL 4: outputs are RECOMMENDATIONS only; no price is written anywhere (mirror `seo_priority`'s `requires_human_signoff` framing where the action implies a change).

---

### Task 11 (PR-11): Expose the discovery-assisted mode as an agent tool — `tools.py` + `tool_options.py` (39th tool)

**Files:** Modify `scm_agent/tools.py`, `scm_agent/tool_options.py`; create `tests/test_price_watch_tool.py`. Modify `CLAUDE.md` tool count 38->39 (also touched in PR-12 docs, coordinate).

**Adds:** Register a `price_watch_tool()` using the SAME lazy-import recipe as `price_intelligence_tool()` (local `from jobs import price_watch` inside each hook to avoid the documented circular-import hazard). Distinctive multi-word `intent_keywords` (e.g. `"watch competitor prices"`, `"vigila los precios de la competencia"`, `"descubre productos de la competencia"`, `"discovery price monitoring"`, `"competitor price discovery"`, `"homologa productos competencia"`). The `options` hook -> `tool_options.price_watch_options` returns a `GuidedOutcome`: normally `OPTIONS` (ranked next steps over the priority plan), but when the cycle produced `pending_escalations` (R5), the hook surfaces the ceiling-raise `GuidedOutcome` (`OPTIONS`/`HANDOFF`) so the operator sees the pending approval — the tool never reports an `EXECUTED` outcome for a tier raise.

**Interfaces:**
- `price_watch_tool() -> Tool` with `requires_data=False` semantics adapted (input is a URL param, like `seo_audit`), `prepare`/`run`/`qa`/`deliver`/`deck` wired to `jobs.price_watch` + `jobs.price_priority`, `options=tool_options.price_watch_options`.
- `tool_options.price_watch_options(report) -> GuidedOutcome` using the existing `_ranked` helper for the happy path, and folding in `report.pending_escalations` when present.
- `reg.register(price_watch_tool())` appended in `build_default_registry()`.

**Test plan (RED first):**
- `test_tool_registered_and_routable` — `build_default_registry().get("price_watch")` exists; an intent brief routes to it (reuse the `test_*_tool.py` routing precedent).
- `test_options_hook_returns_protected_outcome` — `passed_guided(price_watch_options(report))` True.
- `test_options_surfaces_pending_ceiling_raise` — a report with a `needs_ceiling_raise` escalation -> the hook's outcome carries that handoff/option (R5 visible to the operator).
- `test_registry_now_has_39_tools`.
- GREEN + REFACTOR.

**Dependencies:** PR-3/5/6/9 (the playbook), PR-10 (priority), PR-8 (guided escalation shape).

**Risk callouts:**
- HIGH / circular import: MUST use the lazy local-import recipe (`price_intelligence_tool()` documents exactly why). A top-of-module import re-creates the hazard.
- R5: the options hook must not flatten a ceiling-raise into an `EXECUTED`/happy-path option — dedicated `surfaces_pending_ceiling_raise` test.

---

### Task 12 (PR-12): End-to-end acceptance + CLI + docs (R1-R6 acceptance)

**Files:** Create `examples/run_price_watch.py`, `tests/test_price_watch_e2e.py`; modify `CLAUDE.md` (tool list/count, project map row), `HANDOFF.md` (status pointer).

**Adds:** A runnable CLI (`examples/run_price_watch.py --url <competitor_category_url> --catalog <our_catalog.csv>`) that runs the full pipeline offline-deterministically via injected fixtures (mock crawl + `httpx.MockTransport`, a `.test`-TLD approved fixture domain), and an E2E test proving the acceptance criteria end to end.

**Test plan (RED first):**
- `test_single_url_produces_full_deliverable_set` — one never-seen `.test` URL -> site approved (`limited`), a `homologation_table.csv`, a `price_position_matrix.xlsx`, and a per-SKU `price_priority.csv`, with zero human intervention.
- `test_robots_disallow_yields_reason_no_config_no_fetch` — robots-disallow fixture -> no `config/sites/<domain>.yaml` written, crawl never invoked, honest reason reported (acceptance criterion 2).
- `test_ceiling_raise_surfaces_as_pending_option_not_executed` — force a high-value SKU wanting L2 on an L1 ceiling -> a pending-approval `GuidedOutcome`, no L2 acquisition (acceptance criterion 3).
- `test_full_suite_green_and_ruff_clean` (run in CI, not a pytest assertion): `pytest tests/ -q` green, `ruff check src tests examples` clean.
- GREEN + REFACTOR.

**Dependencies:** all prior PRs.

**Risk callouts:**
- Docs must state the tool is read-only observation (NON-GOAL 4) and that auto-onboarding is robots-only + `limited` (NON-GOAL 2). Keep `CLAUDE.md`'s tool count and this mode's one-line description accurate in the same PR that ships it (repo convention: fix stale facts in the PR that changes them).

---

## Consolidated Risk Register

| Risk | NON-GOAL / Rule | Where it could sneak in | Mitigation (test) |
|---|---|---|---|
| Auto-config marked `allowed` | NG-2 | PR-1 | Module constant `AUTO_TOS_DECISION="limited"` + assert file text has no `allowed` |
| Config written despite robots disallow | Acceptance 2 | PR-1/PR-3 | `rejects_and_writes_nothing` + `prepare_skips_without_crawl` |
| Auto self-grant high tier | NG-1 | PR-1 | `max_tier_allowed` fixed at L1 + assertion |
| Crawl retries/rotates past 403/429 | NG-1 | PR-3/PR-6 | `robotstxt_obey_and_identifiable_ua` + `403_degrades_via_circuit_breaker_not_retry` |
| Silent auto-confirm of suspect band | Golden rule 14 / R3 | PR-4 | `ambiguous_pair_stays_suspect` + `no_llm_defers` |
| Duplicated sanity/ledger path | PR-15 precedent | PR-6 | `converges_on_accept_observation` spy |
| Second detection/scheduling mechanism | PR-15 precedent | PR-6/PR-7 | `does_not_reimplement_detection`; single `PRICE_WATCH_JOB` |
| Tier raised without human | **R5 (confirmed)** | PR-8/PR-9/PR-11 | `returns_guided_never_applies`, `escalation_checked_before_apply`, `surfaces_pending_ceiling_raise` |
| Any writeback to competitor/our catalog/config | NG-4 | PR-5/PR-8/PR-9 | `never_mutates_site_config`; no `src/writeback.py` import |
| PII read | NG-3 | PR-1/PR-2/PR-4 | `pii_policy: none` asserted; only product/price schema fields read |
| Circular import at tool registration | repo hazard | PR-11 | lazy local-import recipe |
| File >800 lines | repo limit | PR-3/5/6/9 (all in `price_watch.py`) | new `jobs/price_watch.py`, extract pure logic to `src/pricing_intel/{discover,homologate,watch_policy}.py` |
| `SiteConfig.__post_init__` raises on write | new (spot-checked) | PR-1 | `required_siteconfig_fields_populated` test asserts `robots_checked_at`/`tos_summary` populated |

## Acceptance Criteria (final checklist)

- [ ] One never-seen competitor category URL -> site approved (`limited`) or rejected-with-reason, a homologation table, a `price_position_matrix`, and a per-SKU priority plan — zero human intervention (PR-12 `single_url_produces_full_deliverable_set`).
- [ ] robots.txt disallow -> NO config saved, NO fetch, reason reported (PR-1 + PR-3 + PR-12).
- [ ] Tier raise beyond the approved ceiling -> pending-approval `GuidedOutcome`, never executed alone (PR-8 + PR-9 + PR-11 + PR-12).
- [ ] `pytest tests/ -q` (PYTHONPATH=.) green; `ruff check src tests examples` clean (every PR + PR-12).
- [ ] 38 -> 39 registered tools; `CLAUDE.md` updated (PR-11/PR-12).

## Judgment call made by planning (flagging for confirmation, not blocking)

The spec left "extend `price_intelligence.py` or new `price_watch.py`" as an open file-layout choice. This plan resolves it toward a **new `jobs/price_watch.py`** — `price_intelligence.py` is already ~680 lines, so extending it would breach the repo's 800-line soft limit. Pure logic is further extracted into `src/pricing_intel/{discover,homologate,watch_policy}.py` to keep every file small.
