# Changelog

## [Unreleased]

### Added
- **`repricing` — proposed multichannel repricing (decision bridge, tool #41)**
  (`jobs/reprice_recommend.py`): closes the gap between read-only competitor
  observation and a written price. Joins the competitor price position
  (handed over from `price_watch`/`price_intelligence`, or a standalone CSV)
  with per-SKU elasticity into one proposed price — bounded at the elasticity
  optimum vs. the observed market price, never below the margin floor, with
  floor-vs-market conflicts surfaced rather than silently resolved.
  Recommendations only: this tool never writes a price anywhere itself; the
  proposal is meant to be staged into the existing approval-gated writeback
  changeset (`src/writeback.py`) like any other reversible change. Deliberately
  NOT on the read-only MCP surface (`webapp/mcp_server.py`) — it needs two
  tabular inputs (demand/price history plus a competitor position table),
  which the single-rows MCP bridge can't carry, same reasoning as the existing
  `leadership_chain`/`warehouse_layout` exclusions. 22 tests.
- **`GET /stocky-alternative` — English-language SEO/conversion landing page**
  (`webapp/stocky_alternative_page.py`) for the "stocky alternative" search wave: Shopify
  delisted its native Stocky forecasting/PO app from the App Store in Feb 2026 and shuts it
  down entirely on 2026-08-31, a dated wave of merchants actively searching for a replacement
  right now (GTM research, 2026-07-16). Names the three jobs Stocky did (forecasting, reorder/PO
  suggestions, low-stock alerts), a comparison table vs. typical recurring alternatives, an
  FAQPage JSON-LD block for search-result rich snippets, and CTAs into the two packages that
  actually replace Stocky (`diagnostico-arranque`, `starter-fundamentos` — no new pricing
  invented, structured data from `webapp/offers.py`). English + its own minimal shell
  (deliberately not the rest of the site's Spanish nav/copy), same dark/teal visual system.
  6 tests.
- **`guided` outcome now exposed on the MCP surface** (`webapp/mcp_server.py`), closing the
  parity gap flagged as a deliberate follow-up in PR #135. Every read-only MCP analysis tool
  response now carries the machine-readable "never-unprotected" contract (`src/guided.py`:
  status EXECUTED/OPTIONS/HANDOFF/ESCALATED plus the ranked options / prepared handoff /
  escalation), serialized identically to `POST /api/jobs`
  (`asdict(result.guided) if result.guided is not None else None`). An MCP client is itself an
  agent — it needs to know what a human still has to decide, not just the human-readable report.
  No new information disclosure: the same content was already rendered into the returned
  `report_markdown`; this only makes it structured. 2 tests.
- **`examples/run_repricing.py` — operator entry point for the multichannel repricing playbook**
  (`jobs/repricing.py`, 3.0-audit finding #6). The playbook (stage -> guardrail-gate ->
  approve -> apply -> verify against Shopify / MercadoLibre / Odoo) was fully engineered and
  tested but reachable only from tests — no CLI, route, or tool registration. This CLI runs it
  end to end against the offline in-memory stand-in for the chosen channel (no credentials
  needed): `--channel {shopify,meli,odoo}`, optional `--prices "SKU=price,..."` / `--reason`,
  and a safe posture — staging is a **dry run** by default (nothing written), and even
  `--apply` requires a named `--approved-by` (the playbook never auto-applies). Live channels
  still need the client's own credentials + a real per-channel RPC (the operator's integration
  step). 13 tests.
- **Optional Sentry error tracking** (`webapp/observability.py::init_observability`), gated
  entirely by `SENTRY_DSN`. Shipped OFF: unset DSN (dev/CI/tests, the default) is a no-op with
  zero side effects; an operator turns it on by setting the `SENTRY_DSN` secret — the same
  "no-op until you opt in" pattern as `LINCHPIN_API_KEY`. With `fastapi` installed, unhandled
  500-causing exceptions are captured automatically (auto-enabled FastAPI integration).
  `sentry-sdk` is imported lazily (boot-safe — passes the `prod-boot` gate) and lives in the
  `web` extra so production has it without a Dockerfile change. Safe defaults: `send_default_pii`
  is **off** (the repo's "never surface PII" rule) unless `SENTRY_SEND_DEFAULT_PII=1`, and
  performance tracing is off (`traces_sample_rate=0.0`) unless `SENTRY_TRACES_SAMPLE_RATE` is set.
  Closes the gap where a production outage had no automated alerting (2026-07-16 incident was
  caught by hand). To activate live: `flyctl secrets set SENTRY_DSN=... -a linchpin`.

### Fixed
- **Package deliverables shipped with ZERO L3 citations for 6 tools in every package that runs
  them** (3.0-audit finding #7, the widest blast radius of the citation fixes below).
  `scm_agent/packages.py::_run_step` grounded each step's citations with the same `limit=3`
  shallow candidate pool, so on-topic citations ranked below #3 never reached the strict 2-hop
  gate -- `inventory_optimization` (citation-less in **every** inventory package),
  `pricing`, `excel_replenishment`, `odoo_replenishment`, `risk`, and `reconciliation` all
  degraded to zero. Fixed by grounding on the fixed `tool.title` (the method identifier -- not
  the `f"{spec.title}: {tool.title}"` request brief, whose package-name prefix injected
  cross-domain noise and made a tool's citations differ between packages) over a wider pool
  (`_CANDIDATE_POOL=8`), shown as a tight capped set (`_MAX_CITATIONS=3`), via a new testable
  `_step_citations` helper. **`8` is the empirical ceiling, not arbitrary**: widening further
  re-admits hub-noise for tools whose zero is correct -- `data_quality` (its zero is intentional)
  re-admits manufacturing TQM/QFD citations at pool 11, `cycle_count` re-admits cash-cycle
  citations at pool 12 -- so 8 recovers the six fixable tools with margin while leaving
  `data_quality`/`cycle_count`/`whatif`/`earned_value` correctly at zero. Adversarial review also
  caught one false-friend the wider pool surfaced -- `returns` shipped an off-topic "Reverse
  Auction" (procurement) citation, 2 hops from its `reverse_logistics` anchor via a shared book
  hub -- now blocked via `EXCLUDED_CONCEPTS["returns"]` (restoring the on-topic "Value Recovery"
  citation), the same mechanism already used for `excess_obsolete`. The gate's anchor/hop
  semantics (`MAX_HOPS`, `MIN_CITATIONS`) are otherwise unchanged. 18 tests (incl. a full
  off-topic sweep over every package tool) + an updated re-derivation regression test.
  `dea`/`learning_curve`/`slotting` stay zero at any pool (anchor islanding), and
  `markdown_liquidation`'s pre-existing borderline "Excess Capacity" citation is untouched --
  both separate anchor-tightening items, out of scope.
- **Price-intelligence deliverables shipped with ZERO or off-topic L3 citations** on realistic
  briefs (3.0-audit finding #7, blast radius of the integrated-plan fix below). Confirmed by
  adversarial review: `jobs/price_intelligence.py::gated_citations` had the identical `limit=3`
  shallow-pool + brief-in-grounding defect -- a realistic "benchmark our prices against Amazon
  and MercadoLibre" brief grounded islanded case/forecast nodes (`Amazon` -> a Cachon/Tang case
  node, `benchmark` -> forecast nodes) that displaced the real price-position anchors past the
  top-3 pool, degrading to **zero** citations; other briefs dragged off-topic nodes in. Fixed
  the same way as `integrated_plan`: ground on the fixed pricing keyword set only (not the
  brief -- these citations ground the *method*, so they are deterministic and on-topic
  regardless of wording), over a wider pool (`_CANDIDATE_POOL=6`), shown as a tight capped set
  (`_MAX_CITATIONS=3`). The citation gate is unchanged. 8 tests. This is the offer #9
  ("Diagnostico de Posicion de Precios") engine, so the fix is monetization-relevant. The same
  pattern still latently affects `scm_agent/packages.py::_run_step` (22/121 package steps) --
  a larger follow-up (per-tool pool sizing), not fixed here.
- **S&OP integrated-plan deliverables shipped with ZERO or off-topic L3 citations**
  (3.0-audit finding #7). `jobs/integrated_plan.py::gated_citations` had two defects, both
  reproduced against the committed graph: (1) **recall** -- it grounded only the top-3
  candidate citations before the strict 2-hop gate, and the S&OP concept cluster is fragmented
  across disconnected graph components (the code-bridged `vollmann_sop` anchor is islanded from
  the `aggregate_planning` / `sales_and_operations_planning` cluster), so the top-3 could all
  land in different islands and every one be dropped -- ZERO citations on 6 of 8 plainly-S&OP
  briefs; (2) **precision** -- it fed the free-text client brief into grounding (where it
  outweighs the keyword set), so incidental tokens passed the permissive 2-hop gate
  (`aggregate_planning` reaches ~19% of the graph within 2 hops via the shared book-hub node):
  a brief saying "budget cap" surfaced an emissions "cap-and-trade" citation, and EOQ-flavored
  wording surfaced inventory citations over the real S&OP nodes. Fixed by grounding on the
  fixed S&OP keyword set only (not the brief -- these citations ground the S&OP *method*, so
  they are now deterministic and on-topic regardless of client wording) over a modestly wider
  candidate pool (`_CANDIDATE_POOL=6`), shown as a tight capped set (`_MAX_CITATIONS=3`). The
  gate itself (anchors, `MAX_HOPS`, `MIN_CITATIONS`) is **unchanged**. 12 tests (realistic-brief
  recall regressions + lexical-noise precision guards + brief-independence + cap). The identical
  `limit=3` shallow-pool pattern latently affects `jobs/price_intelligence.py` and
  `scm_agent/packages.py::_run_step` -- flagged as a follow-up, not fixed here.
- **Production boot crash: `ModuleNotFoundError` for the optional pricing extras.** The
  price-watch tools put `src.pricing_intel` on the app's import chain (`webapp.app` ->
  `scm_agent` -> `tools` -> `pricing_intel`), but several of its modules hard-imported
  packages that only the optional `pricing-intel`/`dataquality` extras install
  (`bs4`, `price-parser`, `rapidfuzz`, `python-stdnum`). Those deps are present in dev/CI
  *transitively* (e.g. `extruct` -> `mf2py` -> `beautifulsoup4`), so the full suite and CI
  passed while the real Fly image (`pip install -e ".[web,mcp]"`) crashed on boot with the
  app never binding `0.0.0.0:8000`. All five hard imports are now **lazy** (moved inside the
  functions that use them, matching the repo's existing "degrade gracefully when the extra is
  absent" convention — `extract.py`/`normalize.py` raise a clear "install the pricing-intel
  extra" error, `match/fuzzy.py`/`match/probabilistic.py`/`match/gtin.py` import on demand). A
  new subprocess regression test (`tests/test_pricing_intel_boot_safety.py`) blocks those four
  packages and asserts `webapp.app` still imports, so this class of "green in CI, dead in prod"
  bug can never reach the deployment silently again. Note: the price-intelligence *tools* still
  need the `pricing-intel`/`dataquality` extras to run — they now degrade with an actionable
  error instead of taking down the whole app; installing those extras in the prod image (to make
  the tools functional live, vs. CLI-only) is a separate deployment decision.
- **`pricing-intel`/`dataquality` now installed in the production image** (Dockerfile:
  `pip install -e ".[web,mcp,pricing-intel,dataquality]"`). Without them, the public,
  unauthenticated `POST /api/demo-price-scan` lead magnet (the cheapest, most GTM-ready
  offer's free demo) raised an uncaught `PriceParserUnavailable`/`ExtractionDependencyMissing`
  on any real submission — a 500 for a real visitor, unmonitored since `SENTRY_DSN` isn't set
  in prod. Reproduced directly (a fresh venv built from the pre-fix Dockerfile line crashes the
  same way a real visitor would hit; the identical call succeeds once the extras are installed)
  before and after this change. Safe to add now that the previous entry's lazy-loading fix and
  the `prod-boot` CI job exist: neither depends on these extras being absent, and the `prod-boot`
  job already covers the case where they regress to a hard import. No new job-queue/worker
  infrastructure needed — the added packages are lazy-loaded and small (tens of MB), so nothing
  is paid until a price-intelligence job actually runs.

### Changed
- **Rename: Linchpin -> Kern** (brand only). All user-facing surfaces — README, sales
  docs (`documentation/`, one-pagers), operator portfolio, webapp UI, deliverable
  branding (`src/deliverable.py`), voice agent, MCP server prose, `pyproject.toml`
  (`name = "kern"`) — now say **Kern** (`documentation/KERN_IDENTIDAD_Y_FILOSOFIA.md`
  has the narrative). Deliberately unchanged (API/infra identifiers, not brand):
  GitHub repo name + `linchpin.fly.dev`, `LINCHPIN_*` env vars, `linchpin_*` MCP tool
  names + `linchpin_mcp` server name, `lpk_` key prefix, `linchpin.access` logger,
  `odoo_addon/linchpin_dry_run/`, historical docs/graph-memory. External renames
  (repo, Fly, Odoo Store, MCP listings) tracked as a checklist in `HANDOFF.md`.

### Added
- **Approval TTL on T2 pending autonomy records** (`scm_agent/autonomy.py`) — the Linchpin 3.0
  plan repeatedly promised "T2 con Approval TTL" but `AutonomyRecord` had no expiry, so a stale
  one-click approval could be rubber-stamped hours later and `list_pending()` never aged anything
  out (independent audit finding #2). A T2 pending record is now stamped with an `expires_at`
  deadline (`created_at` + TTL; default `900s`, matching `src/writeback.py`'s `Approval` window,
  overridable via `LINCHPIN_AUTONOMY_TTL_SECONDS`); T1/T3 and legacy rows keep `NULL` and never
  expire. `acknowledge()` refuses a lapsed record (surfaced as `409` by `POST /api/approvals/{id}`)
  — the safe direction on timeout is lapse-and-re-trigger, never auto-approve. New `expire_stale()`
  sweep transitions lapsed `PENDING` → `STATUS_EXPIRED` (idempotent); `list_pending(now=...)` hides
  lapsed rows even before a sweep, which the `/tower` page uses so it never renders an "Aprobar"
  button that could only `409`, and it shows the deadline in a new "Vence" column. Legacy ledger
  files are migrated in place (`ALTER TABLE ADD COLUMN`, guarded against a concurrent-request
  duplicate-column race).
- **`src/escalation.py` wired into `jobs/intake.py`'s plausibility checks** — `intake.py::normalize()`
  used to silently drop unusable rows (bad dates, missing/negative quantities) with zero
  visibility into how many or why; a report could be built on a shrunken, unrepresentative
  slice of a client's real demand history and still ship with the same confident label as a
  well-backed one. `IntakeQuality` (new frozen dataclass: per-reason drop counts +
  `dropped_fraction`) is now tracked via `normalize_tracked()`/`prepare_tracked()`
  (`normalize()`/`prepare()` stay byte-for-byte unchanged for existing callers — a shared
  `_normalize_core()` backs both, each row attributed to the FIRST filter that would drop it
  so nothing double-counts). `inventory_optimization` (the one real consumer of
  `jobs/intake.py` among the 37 tools) now states a residual whenever ANY rows were dropped,
  and escalates via the new `src/escalation.py::maybe_escalate_data_quality()` (OPERATIONAL
  trigger, configurable `intake_quality_threshold`, default 20%) when the drop is severe
  enough to doubt the result — the same failure class `jobs/forecast_job.py`'s MASE=inf
  handling addresses for unvalidated SKUs, applied to the intake step. `maybe_escalate_financial`
  was refactored (behavior-preserving) to share a `_maybe_escalate()` engine with the new
  function. +19 tests (intake tracking, escalation gate, tool_options wiring, 2 full
  orchestrator end-to-end runs proving a messy CSV genuinely escalates and a clean one
  doesn't). Verified empirically post-review: multi-reason-bad rows (bad date AND negative
  quantity in the same row) attribute correctly with no double-count, and a custom
  `intake_quality_threshold` override shifts the escalation boundary end-to-end through a
  real `Orchestrator.run()` call. 1831 tests green, ruff clean.
- **`POST /api/jobs` exposes the `guided` outcome** — the orchestrator's never-unprotected
  contract (`GuidedOutcome` from `src/guided.py`: ranked OPTIONS, a HANDOFF packet, or an
  ESCALATED route/SLA/reason) was computed on every run but never left `webapp/app.py` — an
  API/UI caller had no structured way to know "this needs a choice," "this needs a data
  file," or "this is gated behind a finance approver," short of parsing free-text
  `summary`/`clarifications` strings. Now serialized under `"guided"` via
  `dataclasses.asdict()` (the whole `GuidedOutcome`/`ExecutionOption`/`HandoffPacket`/
  `EscalationPacket`/`Residual` chain is frozen dataclasses with only JSON-primitive
  fields, so this is a lossless, zero-custom-code mapping). +3 tests covering the OPTIONS,
  HANDOFF, and ESCALATED shapes end-to-end through the real endpoint (the ESCALATED case
  reuses the financial-threshold-escalation feature, proving the two features compose
  correctly). An adversarial review caught a real, if narrow, gap this exposure newly makes
  reachable: `src/guided.py::verify_guided()` — the ONE shared QA gate every tool's
  `verify()` calls — never checked `ExecutionOption.score` for finiteness, so a non-finite
  score (`NaN`/`Infinity`, possible from an unguarded CSV `float()` conversion) could reach
  `webapp/app.py`'s `SafeJSONResponse` (`allow_nan=False`) and turn a would-be `200` into an
  unhandled `500`. Verified empirically that the one cited scenario (`jobs/risk_job.py`) was
  already caught incidentally by its own `total_emv` finiteness check — but the general gap
  was real, since nothing guaranteed every tool's aggregate check would catch it. Fixed at
  the shared gate (checks both `outcome.options` and the nested `outcome.escalation.options`)
  so all 37 tools are protected uniformly, with +4 dedicated tests. 1733 tests green (full
  suite, confirming the shared-gate change breaks no existing tool), ruff clean. Known,
  deliberately out-of-scope follow-ups from the review: `webapp/mcp_server.py`'s parallel
  `/mcp` response does not yet expose the same `guided` signal for MCP-only callers; and an
  anonymous caller on a deploy with `LINCHPIN_API_KEY` unset (the shipped default) now sees
  `guided.handoffs[].artifact`/`.data` and `guided.escalation.recommendation` verbatim —
  confirmed harmless today (no currently-wired tool populates real content there) but worth
  remembering if a future tool starts putting sensitive drafts in those fields.

### Fixed
- **`POST /api/jobs` no longer blocks the event loop** — the orchestrator run (CPU-bound
  pandas/Excel/report generation, up to several seconds) used to execute synchronously
  inside the `async def` route handler. With `WEB_CONCURRENCY=1` in production (a single
  event loop), this stalled every other request — including `GET /api/health` — for the
  run's duration. The blocking work (temp-dir staging + `Orchestrator.run()` + building the
  download-URL map) is now extracted into `_run_job_sync()` and dispatched via
  `asyncio.to_thread`, mirroring the pattern `webapp/mcp_server.py` already used. Genuine
  async I/O (`await file.read(...)`, the upload-size check) stays on the event loop before
  the thread dispatch. Proven with a real regression test
  (`test_jobs_does_not_block_a_concurrent_health_check`) that fires a slow job and a health
  check concurrently via `httpx.AsyncClient`/`ASGITransport`/`asyncio.gather` (not the
  serializing `TestClient`) and asserts the health check completes first — manually verified
  this test fails against the pre-fix code. An adversarial review of the refactor caught a
  real precedence regression (an invalid filename + an oversized body now returned 413
  instead of the original 400) — fixed by validating the filename in the async handler,
  before the size check, via a new `_safe_upload_basename()` helper, with a dedicated
  regression test. +3 tests total.

### Added
- **`/console` API key field (fixes a live production bug)** — `LINCHPIN_API_KEY` is set on
  the production deploy, but the agent console (`webapp/static/prototype/index.html`) had no
  UI field to supply `X-API-Key`, so every visitor hit a bare 401 with no explanation. Added
  a masked API-key input (remembered in `localStorage` so an operator doesn't retype it every
  visit), wired into the `POST /api/jobs` request header, and a clear error message
  ("API key invalida o faltante...") instead of the empty, confusing result panel a 401
  previously produced (FastAPI's `{"detail": "..."}` body has no `status`/`summary` field, so
  it silently rendered a "bad" chip with a blank summary). Non-401 error responses now also
  surface `data.detail` instead of the same blank panel. Verified live end-to-end (gate off
  and gate on, missing/wrong/correct key, reload persistence) — caught and fixed a real bug
  along the way where the new `oninput` handler wasn't wired into `renderVals()`, so it
  silently never fired despite being correctly attached as a React prop.
- **Financial-threshold escalation + unvalidated-forecast disclosure** — the two writeback
  tools (`odoo_replenishment`, `excel_replenishment`) no longer present a restock plan as
  freely-actionable "options" regardless of size: `src/escalation.py` gains
  `maybe_escalate_financial()`, a pure helper that re-routes an options outcome to
  `ESCALATED` (financial-threshold trigger, routed to a finance approver with an SLA) when
  the restock's estimated $ value exceeds a threshold (`params.financial_threshold`,
  default $50k) - the same ranked options survive, both inside the escalation packet AND at
  the outcome's top level, so nothing is silently dropped from the rendered deck - they're
  just gated behind a required sign-off. Odoo prices the restock from its own product cost
  (already read in `prepare()`); the Excel planilla gains an optional cost column
  (`_COST_CANDIDATES`, mirroring the existing stock/ROP/demand detection pattern) - absent,
  the $ value stays unknown and the check never guesses. A new `escalation_banner()` helper
  makes the escalation unmissable in every document a human actually reads, not just correct
  in the data model: it leads the deck's findings and Coverage & handoff section (both
  `odoo_job.py` and `excel_replenishment_job.py` build_deck), and prepends a "# STOP" warning
  to `apply_howto.md` before the one-shot apply recipe - closing a real adversarial-review
  finding where the escalation was structurally correct but invisible to `Orchestrator.run()`
  callers, the rendered deck, and the exact document an operator opens before applying.
  Separately, `jobs/forecast_job.py` no longer silently ships a SKU whose forecast accuracy
  (MASE) couldn't be backtested (too little history) with the same confident label as a
  well-validated one: the portfolio's unvalidated share is now stated as an explicit residual
  (with a non-empty `risk_if_skipped`, enforced by `verify_guided`) and dampens the outcome's
  confidence. +26 tests across `test_escalation.py`, `test_forecast_tool.py`,
  `test_connector_odoo_tool.py`, `test_excel_replenishment.py`. Known follow-ups (not fixed
  here - separate, pre-existing gaps, not introduced by this change): `webapp/app.py`'s
  `POST /api/jobs` JSON response doesn't surface `guided`/escalation at all for ANY tool
  (not just these two); `examples/apply_replenishment.py` doesn't hard-block on
  `outcome.status == ESCALATED`, only warns in the howto document.
- **MCP surface expanded 8 → 33 tools (`webapp/mcp_tool_specs.py`)** — every read-only
  analysis tool is now exposed to remote MCP clients, not just the original Phase A eight:
  cost-to-serve, landed cost, earned value, learning curve, E&O, markdown liquidation, FEFO,
  IRA reconciliation, cycle count, returns disposition, S&OP, DDMRP, multi-echelon, DRP,
  simulation-optimized policy, sourcing, acceptance sampling, risk, DEA, queuing, job
  sequencing, transport-mode selection, facility location, slotting, and vehicle routing.
  The tool surface moved out of `webapp/mcp_server.py` into a spec table
  (`webapp/mcp_tool_specs.py` — name, job_type, title, column contract per tool; the server
  registers each spec in a loop), so adding a tool = adding one spec. Still excluded, by
  design: the writeback pair (`odoo_replenishment`, `excel_replenishment`) and the two
  non-tabular tools (`leadership_chain`, `warehouse_layout`). +5 tests (spec-registered
  bridge e2e, required-param degradation, description contract).
- **Data-protection trust note (README + `/demo`)** — surfaced the existing safe-staging/isolation controls (per-job sandboxed directory, validated uploads, auto-purge, [SECURITY.md](SECURITY.md)) as a visible third "cross-cutting guarantee" in the README and a short note next to the file upload on the public `/demo` page, where a prospect actually hands over their data. No behavior change — documentation/copy only.
- **`vehicle_routing` agent tool + `src/logistics/routing.py`** — new capability (36th tool),
  grounded in Ballou *Logistica/Administracion de la cadena de suministro* Cap. 7 ("Diseno de
  rutas para los vehiculos"): groups delivery stops into capacity-feasible vehicle routes with
  the Clarke-Wright savings algorithm and the sweep algorithm, sequences each route
  nearest-neighbor, and recommends whichever plan has the lower total distance against a
  one-truck-per-stop baseline. Optional per-stop time windows get a lightweight feasibility
  check (arrival-time accumulation + late-arrival flagging) — a deliberate extension beyond the
  book's 2004 scope, not a full VRPTW solver. Offline, Euclidean distance from a single depot,
  wired via `jobs/vehicle_routing_job.py` with ranked options (adopt savings vs. sweep, resolve
  late stops first). +20 tests.
- **Recurring planilla monitoring (`src/replenishment_delta.py` + `examples/monitor_planilla.py`)** — the subscription-service layer over the Excel loop: each run re-plans the client's planilla, diffs against the previous snapshot, and reports **what changed** (SKUs newly below target first, then growing/shrinking order sizes, recovered SKUs, catalog adds/removes) instead of a full plan the client re-reads every week. Pure delta core (`snapshot`/`compare`/`render_markdown`, versioned snapshots, clock never read — labels are caller-supplied like `week-27`), CLI saves the baseline on first run and advances the snapshot each run; schedule with the OS for a standing cadence. Read-only by design — applying stays a separate human decision (`apply_replenishment.py`). Backups (`*.linchpin-backup*`) and monitor state (`.linchpin-monitor/`) are now gitignored (client inventory data, never versioned). +9 tests.
- **Operator apply/rollback CLI (`examples/apply_replenishment.py`)** — the missing last mile of the planilla loop: one command re-plans from the CURRENT file, prints the plan table and the exact cell-level before/after (guards tagged), asks for confirmation (`--yes` to skip), applies through the safe-staging plane, and prints the ready-to-paste `--rollback <key>` command. Idempotency and rollback survive across invocations via a persistent SQLite ledger (`--ledger`, default `data/writeback_ledger.sqlite3`; `excel_replenishment_job.prepare` now wires it via `params["ledger_path"]`). A planilla already carrying the exact plan is detected as a no-op (nothing re-applied); a healthy planilla exits cleanly with nothing to do. `apply_howto.md` now leads with the one-liner. +10 tests.
- **`excel_replenishment` agent tool (`jobs/excel_replenishment_job.py`)** — the client's planilla as a brief-routable capability (35th tool): "actualiza la reposicion de mi planilla" reads their own inventory spreadsheet (auto-detecting the sheet, header row, and SKU/stock/reorder-point/demand columns — Spanish or English, accents folded), plans the restock (demand-cover mode when a demand column exists, classic min/max on the reorder point otherwise), and STAGES the recommended order quantities back into the planilla as a reversible dry-run changeset through the Excel safe-staging connector — surfaced as >=2 ranked executable options (apply after approval / export only); a human approves before anything touches the file. QA-gated (protected outcome + staging consistent with the plan), CSV + `apply_howto.md` deliverables + premium deck. Hardened by adversarial review: blank ROP/demand cells are excluded-and-counted (never coalesce to 0), duplicate SKUs fail closed, formula cells read their Excel-cached value (actionable error when uncached), column binding is deterministic (priority-ordered candidates), a leading catalog sheet no longer blocks a qualifying later sheet, the idempotency key derives from staged content (weekly re-runs never collide with last week's apply), and the changeset carries no-op **guard cells** over the plan's inputs so the connector's drift check also refuses stale-input applies. +28 tests.
- **Excel writeback connector (`src/connectors/excel.py`)** — a client's planilla becomes a safely writable system of record. `ExcelWorkbookStore` implements the writeback store surface (`read/applied_keys/claim/release/commit/rollback`), so the whole safety plane (risk tiers, signed time-boxed approvals, idempotency, audit, optional SQLite ledger persistence) is reused from `src/writeback.py` unchanged. Entity = sheet, field = cell ref ("D5") — zero schema assumptions; `resolve_row_edits()` adds the semantic layer (SKU + column header -> cell) with header-row auto-detection, so a client's own layout (title rows, headers not on row 1) needs no config. Safety beyond the shared plane: **drift check** (commit refuses, writing nothing, if any staged cell changed on disk after staging), **atomic write** (temp + `os.replace`; a locked/open-in-Excel file surfaces an actionable error with the original untouched), **per-commit file backup** (`<name>.<key>-<hash>.linchpin-backup<ext>` — byte-exact original for anything cell-level rollback can't cover), a **crash-window tripwire** (an orphaned backup from an attempt that wrote the file but died before recording its audit blocks naive retries — which would otherwise bake the mutated file in as "original" and poison rollback — until the operator reconciles), cell-exact `rollback()` incl. clearing cells that didn't exist before, and `.xlsm` macros preserved (`keep_vba`) but never executed. +23 tests.
- **Per-client parameter profiles (`src/client_profile.py`)** — durable client-wide cost/capacity parameters (holding rate, order cost, service level, lead time, warehouse capacity) at `clients/<slug>/profile.json`, so an operator records a client's real numbers once instead of every run silently using the generic engine defaults (0.95 / 0.25 / $75). The orchestrator merges the profile into a job's params before `prepare`/`run` — profile values only fill gaps, an explicit override always wins; profile `lead_time_days` becomes intake's default lead time when the client CSV carries none (per-SKU CSV values still win). New `Orchestrator.run(..., strict_params=True)` (CLI: `--strict-params`) makes a tool's `required_client_params` block with `needs_clarification` instead of assuming defaults, so a missing number is asked exactly once and then persisted via `upsert_profile` (which slugifies ids, preserves un-passed fields/metadata, validates values as finite, and writes atomically). Trust boundary: generic placeholder labels ("Client", "MCP client") never load/save a profile, `save_profile` requires canonical slugs (kills path traversal + save/load key mismatch), and the multi-tenant webapp/MCP surface constructs its orchestrator with profiles **disabled** (opt-in via `LINCHPIN_CLIENTS_ROOT`) since `client` there is a caller-typed label, not an authenticated identity. `clients/` is gitignored but NOT regenerable — back it up. +45 tests.
- **Client-ready deliverable generator (`src/deliverable.py` + `jobs/inventory_deliverable.py`)** — turns engine output into sellable *documents* (the top hireability gap: "consultants get paid for documents"). Produces a sectioned Markdown report + multi-sheet XLSX with an executive summary, quantified findings, a KPI table with *selection rationale*, a **data-source map** (every figure traceable to its origin + refresh cadence), L3 citations, and a **coverage & handoff** block (confidence + residual human action, honoring the never-unprotected contract). Engine-agnostic — `from_result` composes from any orchestrator `JobResult`; the inventory-optimization adapter turns SKU recommendations + portfolio numbers into the deliverable. Pure/deterministic (prepared date injected, not read from the clock). +11 tests.
- **Operating modes — Inventory vs SCM (`scm_agent/modes.py`)** — two role profiles over one engine. `INVENTORY` mode is the stock-centric subset (reorder points, safety stock, ABC-XYZ, reconciliation, inventory reporting); `SCM` mode is the end-to-end superset (+ S&OP, sourcing, landed cost, logistics, cost-to-serve, risk, sustainability, leadership) and exposes every registered tool. Each mode carries its tool surface, persona, and deliverable/KPI catalogue; `build_registry(mode)` / `orchestrator_for(mode)` scope the orchestrator with no edits to tools. SCM is the default — it never narrows capability unless Inventory is explicitly requested.
- **Operations / strategy / sustainability L3 layer** — ingested four authoritative SCM textbooks into the books knowledge graph (`knowledge/scm-books/`): Vollmann *Manufacturing Planning & Control* (S&OP/MPS/MRP/DRP — the planning/manufacturing spine, 149 nodes), Ivanov *Global Supply Chain & Operations* (sourcing/network/risk/digital, 70 nodes), Christopher *Logistics & SCM* (agility/lead-time/network/3PL, 247 nodes), Grant, Trautrims & Wong *Sustainable Logistics & SCM* (green/reverse logistics/circular economy, 177 nodes), a deepened **Chopra & Meindl** (32 → 312 nodes: strategy, network design, sourcing, transportation, coordination, revenue management), and **Heizer, Render & Munson** *Operations Management* (161 nodes: quality/SPC & Six Sigma, process & layout design, facility location, project management/PERT-CPM, QFD, capacity/TOC, decision analysis). The books graph grew 430 → 1824 nodes / 3640 edges / 122 communities across **23 sources**. Vollmann/Ivanov via the Kimi backend; Christopher/Grant/Chopra/Heizer via host subagents (no API key) — single canonical graph, concepts merged by label into cross-book bridges.
- **Guided Execution Layer (`src/guided.py`)** — the "never leave the user unprotected" contract. A `GuidedOutcome` is either `EXECUTED` or carries an executable path: ranked `ExecutionOption`s, a prepared `HandoffPacket`, or an `EscalationPacket`. `verify_guided()` is a QA gate (same shape as `jobs/qa.py`) that flags any non-executed result with no path as `unprotected`, and any residual without a stated risk. Builders (`as_executed`/`as_options`/`as_handoff`/`as_escalation`) make outcomes protected by construction.
- **Safe-staging writeback (`src/writeback.py`)** — the agent never mutates a system of record directly: `stage()` computes a dry-run `Changeset` (field-level before/after) without writing; risk tiers (read / reversible / irreversible) decide whether a time-boxed `Approval` is required; `apply()` is idempotent on the changeset key; every applied change is audited and `rollback()`-able. Ships an `InMemoryStore` reference connector.
- **Orchestrator guarantee** — `Orchestrator.run()` now attaches a protected `GuidedOutcome` to every `JobResult` at a single boundary (`scm_agent/guided_bridge.py`): ok→executed, needs_clarification→options, needs_data→handoff, qa_failed/error→escalation. New field `JobResult.guided`.
- **ABC-XYZ classification (`src/classification.py`)** — Pareto importance + CV predictability into a 9-cell matrix that assigns a default review policy, cycle-service-level target and buffer distribution per SKU (capability M4).
- **Inventory financial KPIs (`src/financial_kpis.py`)** — inventory turns, DIO, GMROI, sell-through, weeks of supply, inventory-to-sales, cash-to-cash, stockout rate (capability M13 / §4.5).
- **DDMRP buffers + net-flow planning (`src/ddmrp.py`)** — red/yellow/green zones, ADU, TOR/TOY/TOG, net-flow position and planning priority (capability M5).
- **Inventory event detection (`src/alerting.py`)** — stockout-risk / reorder-due / excess / dead-stock detection over a SKU snapshot, surfaced through the Guided Execution Layer as an executable handoff (capability M14, pure core).
- **Forecast accuracy metrics (`src/forecast_metrics.py`)** — MAE/RMSE/bias/MAPE/WAPE/MASE/RMSSE, dependency-free (capability M2).
- **Warehouse space + COI slotting (`src/space.py`)** — cube/m3 sizing, utilization, and class-based slotting by Cube-per-Order Index (capability M7).
- **Inventory reconciliation / IRA (`src/reconciliation.py`)** — system-vs-physical variance, record accuracy, dollar impact, ABC-tiered cycle-count plan (capability M6).
- **Landed-cost / TCO engine (`src/landed_cost.py`)** — Incoterm-aware duty base + freight/insurance/handling/broker, per-unit landed cost (capability M8).
- **Supplier scorecards (`src/supplier_scorecard.py`)** — OTIF/DIFOT, on-time/in-full rates, avg lead time, defect PPM (capability M8).
- **Purchase order (`src/purchase_order.py`)** — immutable PO + guarded state machine (draft→approved→issued→received / cancel) (capability M8).
- **Data-quality core (`src/data_quality.py`)** — GTIN/UPC/EAN check-digit validation, SKU normalization, canonical column mapping (capability M11).
- **SKU deduplication (`src/sku_dedup.py`)** — duplicate detection by shared GTIN + fuzzy name match (rapidfuzz when installed, `difflib` fallback) (capability M11).
- Optional dependency extras: `dataquality` (rapidfuzz, python-stdnum), `mcdm` (pymcdm), `forecast` (statsforecast, utilsforecast) — modules degrade gracefully when absent.
- **Supply-chain leadership grounding (L3)** — ingested Palamariu & Alicke, *From Source to Sold: Stories of Leadership in Supply Chain* (2022) into the books knowledge graph (`knowledge/scm-books/`): +312 leadership concept nodes from 26 leader interviews, plus the book's **CHAIN model** (Collaborative / Holistic / Adaptable / Influential / Narrative) as explicit citable nodes bridged to `supply_chain_strategy`. The books graph grew 430→742 nodes / 763→1384 edges; `scm_agent` `KnowledgeBase.search()` / `explain()` now ground the `leadership_chain` capability with chapter-level citations (previously the CHAIN model was synthesized in code with no L3 source).
- **Voice agent brain (`src/voice/`)** — the credential-free core of capability M16: 7 call playbooks (`playbooks.py`), the outbound-call compliance gate (`compliance.py` — DNC/consent/calling-window/attempt-cap + TCPA/EU-AI-Act AI-disclosure), the 12 logistics document field maps (`doc_schemas.py`), the six-block ElevenLabs system-prompt + agent-config builder (`agent_config.py`), and the curated RAG knowledge base (`knowledge_base/logistics_kb.md`). Only live dialling (ElevenLabs/Twilio) needs credentials.
- Capability Expansion Plan progress: **Fase 0** (foundations) + **Fase 1** + pure cores of **Fases 3-4** (M6/M7/M8/M11/M2-metrics) + first dep-gated module with fallback + **Fase 5 voice-agent brain** (M16, credential-free). See `documentation/CAPABILITY_EXPANSION_PLAN.md`. ~159 new tests; full suite 374 passing, ruff clean.
- **`newsvendor` + `cycle_count` agent tools** — wired the existing single-period newsvendor engine and a standalone ABC-weighted cycle-count schedule (`jobs/newsvendor_job.py`, `jobs/cycle_count_job.py`) into the registry as two more brief-routable capabilities.
- **`multi_echelon` agent tool** — wired the existing serial-GSM safety-stock-placement engine into the registry (`jobs/multi_echelon_job.py`): the cost-minimizing buffer split across a supplier→DC→store network, per-stage order-up-to levels.
- **`transportation` agent tool + `src/logistics/`** — new mode-selection engine (`freight.py`, `modes.py`): cheapest feasible transport mode (parcel/LTL/FTL/intermodal) per shipment from a configurable rate card, offline (no carrier API), wired via `jobs/transportation_job.py`.
- **`fefo` agent tool + `src/lots/`** — new lot-expiry engine (`expiry.py`, `fefo.py`): shelf-life aging report, First-Expired-First-Out issue order, at-risk-of-expiry quantity, markdown-vs-scrap recommendation, wired via `jobs/fefo_job.py`.
- **`slotting` + `simulation` agent tools** — wired the existing cube-per-order-index slotting engine and the Monte-Carlo safety-stock/order-up-to search into the registry (`jobs/slotting_job.py`, `jobs/simulation_job.py`).
- **`excess_obsolete` agent tool + `src/excess_obsolete.py`** — new engine: classify on-hand stock into healthy/excess/dead from days-of-cover and time-since-last-sale, size the cash tied up, recommend a disposition per SKU, wired via `jobs/excess_obsolete_job.py`.
- **`facility_location` agent tool + `src/facility_location.py`** — new engine: single-facility siting (center-of-gravity + Weiszfeld 1-median) vs. the current site, offline straight-line distance, wired via `jobs/facility_location_job.py`.
- **`drp` agent tool + `src/drp.py`** — new engine: time-phased distribution requirements planning grid per branch (gross→net→planned order releases), rolled up as the central DC's gross requirements, wired via `jobs/drp_job.py`.
- Registry now carries **34 agent-routable tools** (`build_default_registry()` is the source of truth); +10 tools shipped across PRs #73-#80 without a matching changelog update at the time - backfilled here. See HANDOFF.md for the audit finding that caught the gap.

### Fixed
- **World-class audit remediation (P0 + P1, PRs #82/#83)** — writeback `Approval` now requires an HMAC-SHA256 `signature` (env `LINCHPIN_APPROVAL_SECRET`) so an `Approval` can no longer be forged by constructing the dataclass directly; new `src/writeback_store.py::SqliteAuditLedger` for a persistent, restart-surviving audit/idempotency ledger; the Odoo connector's draft-PO write path now routes through the writeback safety plane instead of calling `execute_kw(..., "create")` directly (closes a duplicate-RFQ-on-retry gap); agent tool routing (`scm_agent/registry.py`) now matches on word boundaries with plural tolerance instead of raw substring matching; citation grounding (`scm_agent/knowledge.py`) re-ranks by IDF weight instead of raw token count and gates method-rule triggers to the active tool's own keyword domain; math fixes in `src/eoq.py` (volume-discount EOQ), `src/multi_echelon.py` (serial-GSM coupling), `src/newsvendor.py` (both optimizers), and `src/pricing.py`/`jobs/pricing.py` (`confident` logic).
- **`safety_stock()` regression coverage** — added a test pinned to Vandeput's Table 4.1 worked example that checks the exact signed value through `safety_stock()` itself, closing a gap where the module's own tests didn't catch a sign error in the core formula (only distant integration tests happened to).
- **`_ReorderRuleStore.commit()` / `_DraftPoStore.commit()` partial-failure rollback** — a write failing partway through a multi-change commit to Odoo no longer leaves earlier writes in that same call live with no audit trail; on any exception, everything written so far in that call is compensated (restored/deleted) before the failure propagates, so a retry with the same idempotency key can proceed cleanly.
- **CSV/Excel formula-injection guard (`src/sanitize.py`)** — a `product_id` (or any other string field) starting with `=`, `+`, `-` or `@` survived unescaped into generated `.xlsx`/`.csv` deliverables; openpyxl auto-promotes a leading `=` to a live formula cell, so a malicious upload could execute code when a client opened the file (OWASP CSV injection). New `defuse_formula()` helper prefixes such values with a leading `'` (Excel's standard force-text marker) before every cell/row write in `src/excel_export.py`'s `_write_table()` and the GSM-summary cells in `write_analysis_workbook()`, `jobs/deliverables.py::write_excel()`/`write_pricing_excel()`, and `src/export.py::write_summary_csv()` (which every `jobs/*_job.py` CSV deliverable goes through) — every sink confirmed reachable from an uploaded CSV's unvalidated columns. Checks `value.lstrip()` for the trigger prefix (not just the literal first character) so a payload disguised behind leading whitespace/control characters can't bypass it independent of any caller's own `.strip()`. Found during a security review of an unrelated branch; pre-existing, not specific to that branch's changes.
- **Same formula-injection gap in `src/powerbi_export.py`'s Power BI CSV export** — this module has its own `_write()` CSV sink, not covered by the fix above (it never routed through `src/export.py::write_summary_csv()`). Now sanitizes every cell (`df.apply(lambda col: col.map(defuse_formula))`) before `to_csv()`, closing the same gap for `demand_history`/`product_summary`/`policies`/`cost_optimization`/`fill_rate`/`gsm_nodes`.
- **Unauthenticated deliverable downloads (`webapp/app.py`'s `/jobs-output` mount)** — access to a job's generated deliverable was pure obscurity (an unguessable `tempfile.mkdtemp()` directory name in the URL), no expiry, no revocation, while `POST /api/jobs` was already gated by `LINCHPIN_API_KEY`. New `webapp/security.py::jobs_output_auth_middleware` requires the same key on every `GET /jobs-output/*` request once an operator configures `LINCHPIN_API_KEY` (no-op when unset, unchanged default). Registered *before* `security_headers_middleware` so the latter stays the outermost layer and its hardening headers still apply to this middleware's own 401 responses (an initial reversed registration order was caught in code review — verified live that it silently stripped `X-Content-Type-Options`/`X-Frame-Options`/CSP off every 401).
- **Missing `mcp` extra in the production install command** (`railway.json`'s `buildCommand`, `docs/DEPLOYMENT.md` §2) — `pip install -e ".[web]"` alone omits the `mcp` package, but `webapp/app.py` unconditionally imports `webapp.mcp_server`, which hard-imports `mcp.server.fastmcp` at module load time; a fresh install with only `.[web]` crashed at import with `ModuleNotFoundError: No module named 'mcp'`. Reproduced in an isolated venv before fixing both to `.[web,mcp]`. Test suites never caught this because CI installs from `requirements-dev.txt`, which always includes `mcp` unconditionally — a bespoke production install command is the one place that drifted.
- **Two production-only bugs found deploying `fly.toml` to a real Fly.io app** (`https://linchpin.fly.dev`, live) — neither was caught by local `docker build`/`docker run` testing, since that ran the container standalone with no persistent Volume attached: (1) 2 uvicorn workers OOM-killed the 512mb VM in a crash-restart loop (each worker independently loads pandas/numpy/scipy + the orchestrator + the L3 knowledge graph, so memory scales linearly with worker count) — fixed via `WEB_CONCURRENCY=1`; (2) mounting the persistent Volume at `/app/data` shadowed the small static sample CSVs baked into the image at that same path (a Volume mount hides whatever's already on disk at its destination), crashing with `FileNotFoundError` — fixed by mounting at `/data` instead, with `LINCHPIN_MCP_KEYS_PATH` updated to match.

### Added
- **Fly.io deployment config** (`Dockerfile`, `fly.toml`, `.dockerignore`) — the user's Railway trial ran out before a first deploy happened, so Fly.io replaces it as the primary "quick path" in `docs/DEPLOYMENT.md` §2a (Railway's config is kept as a demoted §2b alternative). Locally built and ran the image via Docker Desktop this session; confirmed `/api/health`, `/`, and `/mcp` all serve correctly from the built container before committing.
- **Read-only MCP server (Phase A go-to-market, `webapp/mcp_server.py`)** — exposes 8 of the 34 registered tools (inventory optimization, ABC-XYZ, newsvendor, forecast, financial KPIs, pricing, data quality, what-if) to other AI agents over MCP/Streamable HTTP, mounted at `/mcp` alongside the dashboard. Deliberately excludes every writeback-capable tool (`odoo_replenishment` and anything touching `src/writeback.py`) - analysis only. New `src/mcp_keys.py::McpKeyStore`: a SQLite-backed per-client API key store (high-entropy tokens, only the SHA-256 hash ever persisted, issue/validate/revoke), gating the MCP surface independently of the dashboard's single shared `LINCHPIN_API_KEY` (`webapp/mcp_auth.py`). Manual key issuance for now (`examples/issue_mcp_key.py`) - Phase A billing is a Stripe Payment Link followed by a hand-issued key, no self-serve signup or metering yet. Rate limiting (`webapp/security.py::rate_limit`, now accepts an optional `key` override) is identity-aware for authenticated MCP traffic - each client's quota is keyed by their own resolved name, not shared source IP, so distinct clients (or an MCP client and a dashboard user) behind the same NAT/proxy can't throttle each other; caught and fixed during an independent security review before merge. See `docs/MCP_SERVER.md`.

### Changed
- **Renamed the project to Linchpin** — repo, distribution package, agent console, and docs. The GitHub repository moved to `esstipi-debug/linchpin` (the old `supply-chain-optimization` URL redirects automatically). The importable module `scm_agent` and the engine package `src` are unchanged.
- **Reframed the README around multi-source grounding.** The value proposition now leads with the project's knowledge graph of 17 SCM books and the codebase, rather than a single book. Per-module academic citations (Vandeput 2020 and others) in `src/` docstrings and the L3 bridge are unchanged — the engine still maps to Vandeput's chapters where it implements them.

## [2.9.0] - 2026-06-24

### Added
- `warehouse_layout` capability (10th tool) + `warehouse/` core: parametric 3D warehouse
  (building, yard, gates, docks, racks, slots), geometry QA, self-contained Three.js viewer,
  webapp `/api/warehouse` + `/warehouse`. Foundation (capa 1a) of the warehouse spatial twin.

## [2.8.0] - 2026-06-21

### Added
- `scm_agent` orchestrator: routes a free-form brief (+ optional data) to a capability and drives prepare → run → QA → deliver.
- `leadership_chain` capability (CHAIN model): score + radar chart + active directives; `jobs/leadership.py`.
- Pluggable `LLMProvider` (Claude when `ANTHROPIC_API_KEY` is set, rules fallback otherwise).
- CLI `examples/run_agent.py` and `POST /api/jobs` HTTP endpoint with downloadable deliverables.
- Optional `llm` and `web` dependency extras.

### Security
- `POST /api/jobs`: sanitize the uploaded filename to a basename pinned inside the per-job directory (blocks path traversal / arbitrary file write) and cap uploads at 25 MB (413 on exceed).

### Hardening
- `POST /api/jobs`: sweep per-job output directories older than `JOBS_TTL_SECONDS` (1 h) on each request so deliverables/uploads don't accumulate.
- `examples/run_agent.py` self-inserts the repo root on `sys.path`, so the documented commands run from any working directory.
- Orchestrator logs swallowed exceptions at `DEBUG` (`exc_info=True`) without breaking the never-crash contract.

## [2.7.0] - 2026-06-19

### Added
- **Price optimization** (`src/pricing.py`): demand-elasticity estimation
  (log-log fit), profit-maximizing price for constant-elasticity (`p* = c·ε/(ε+1)`)
  and linear demand, markdown/clearance pricing, and `recommend_price` with
  confidence + action. Extends the engine's newsvendor / volume-discount economics.
- **Pricing playbook** (`jobs/pricing.py`): client price/quantity history →
  elasticity → optimal price per SKU with profit-uplift; QA (`qa.verify_pricing`)
  + deliverables (Excel + report) reusing the job-fulfillment pipeline.
- `scripts/generate_pricing_sample.py` + `data/sample_pricing.csv`;
  `examples/run_pricing_job.py`; `jobs/SAMPLE_PRICING_REPORT.md`.
- 12 tests (132 total): pricing engine + pricing playbook.

### Changed
- `src/__init__.py` exports the pricing API; `jobs/README.md` lists the pricing
  job type.

### Added
- **Job-fulfillment layer** (`jobs/`) — package the engine for real supply-chain
  freelance work:
  - `jobs/intake.py`: detect/normalize any client schema (CSV/Excel, ERP or
    Kaggle-style) to the canonical demand schema, aggregated per period
  - `jobs/inventory_optimization.py`: playbook — forecast → (s,Q)/(R,S) policy →
    budget allocation → structured `JobReport`
  - `jobs/qa.py`: automated verification of the report's numbers (nothing ships
    until it passes)
  - `jobs/deliverables.py`: client-ready Excel workbook + Markdown report + CSV
  - `examples/run_inventory_job.py`: end-to-end CLI; `jobs/SAMPLE_REPORT.md`
- 8 job-layer tests (120 total)

### Notes
- Human-in-the-loop by design — automates the analysis/deliverable, not Upwork
  bidding or client comms (ToS).

### Added
- **Inventory Planner web UI** (`webapp/`): a 4-tab dashboard (Portfolio, SKU
  Detail with demand/forecast chart, Budget Planner, Forecast Quality) served by
  FastAPI over the engine — every number comes from `src/`. Vanilla JS, no Node /
  build step; one `uvicorn` command
- `GET /api/portfolio` runs forecasting → policy → constraints with live
  what-if params (service level, lead time, order cost, budget); validated inputs
- `scripts/generate_portfolio.py` seeds an 8-SKU demand portfolio
- `tests/test_webapp.py` (9 tests, 110 total) via FastAPI TestClient
- Screenshots in `webapp/screenshots/`

### Changed
- `requirements-dev.txt`: add fastapi / uvicorn / httpx for the web UI + its tests

### Added
- **`SqlDemandSource`** (`src/sources.py`): live demand from any DB-API 2.0
  connection (SQLite, Postgres, MySQL ...) via a table name or parameterised
  query; validates bare table names as SQL identifiers to avoid injection
- `examples/run_sql_source.py`: live-data demo (SQLite stand-in for an ERP feed)
  running the same source → forecast → policy chain
- 4 SQL-adapter tests (101 total, ~91% coverage)

### Changed
- README: live SQL data path documented; ERP/WMS adapter now a thin layer

## [2.3.0] - 2026-06-19

### Added
- **Pluggable data sources** (`src/sources.py`): `DemandSource` protocol with
  `CsvDemandSource` and `DataFrameDemandSource` adapters — the engine is no
  longer hard-wired to CSV; a SQL/API/ERP adapter just satisfies the protocol
- **Business constraints** (`src/constraints.py`): MOQ, case-pack rounding,
  shelf-life caps, and `allocate_under_budget` — a portfolio budget allocator
  that trims safety stock to fit while preserving cycle-stock economics
- `examples/run_constrained_plan.py`: full chain source → forecast → policy →
  constraints
- 14 tests (97 total, ~91% coverage)

### Changed
- README: "engine → product" section now reflects the assembled AUTO chain

## [2.2.0] - 2026-06-19

### Added
- **Demand forecasting front-end** (`src/forecasting.py`): moving average, simple
  exponential smoothing, and Croston (intermittent demand), with auto method
  selection by ADI classification
- Forecast-error σ_e — the theoretically correct dispersion for safety stock
  (Vandeput 2021, §4.2.5) — exposed via `ForecastResult.to_engine_inputs`
- `examples/run_forecast_to_policy.py`: end-to-end history → forecast → (s,Q) policy
- 13 forecasting tests (83 total, ~90% coverage)

### Changed
- README: forecasting documented; "Future extensions" reframed as the
  engine → product ("AUTO") roadmap

## [2.1.0] - 2026-06-19

### Added
- `pyproject.toml`: PEP 621 packaging + Ruff, pytest and coverage config (pip-installable, no more `PYTHONPATH` hack)
- `requirements-dev.txt` for test/lint tooling (`pytest`, `pytest-cov`, `ruff`)
- Direct unit tests for previously-untested modules: `policies`, `risk_period`, `demand_variability`, `discrete_demand`, `export` (23 new tests, 70 total)
- CI now lints with Ruff and enforces a 80% coverage gate (measured coverage ~90%)

### Fixed
- `tests/test_multi_echelon.py`: `test_gsm_case1_higher_cost_than_optimal` asserted nothing — added the intended cost comparison
- Removed dead code / unused locals and imports flagged by Ruff (`eoq`, `fill_rate`, `policies`, `distributions`, `powerbi_export`, several tests/examples)

### Changed
- `pytest` moved out of runtime `requirements.txt` into dev deps
- Test config moved from `pytest.ini` into `pyproject.toml`

## [2.0.0] - 2026-06-18

### Added
- Product metadata from CSV: `unit_cost`, `lead_time_days` (`src/data_loader.py`)
- EOQ volume discounts §2.5.3 (`compute_eoq_volume_discount`)
- Gamma/auto safety stock in policies (`src/demand_variability.py`)
- Multi-SKU batch analysis (`src/batch.py`, `examples/run_batch.py`)
- Inventory charts (`examples/plot_inventory.py`)
- Sim-opt grid over R and Ss (`optimize_rs_simulation_grid`)
- GitHub Actions CI (Python 3.11–3.13)
- Rewritten FAQ and CONTRIBUTING (removed placeholder marketing)

### Changed
- Power BI export uses per-SKU lead time, unit cost, GSM params
- METHODOLOGY assumptions table updated
- README: CI badge, full project layout

## [1.3.0] - 2026-06-18

### Added
- Power BI CSV dataset export `src/powerbi_export.py`
- `examples/build_powerbi_dataset.py`
- Power Query templates `power-bi/queries/*.pq`
- DAX measures `power-bi/measures.dax`
- Setup guide `power-bi/SETUP.md`
- `--powerbi` flag on `run_complete.py`
- Test for Power BI export (42 total)

## [1.2.0] - 2026-06-18

### Added
- Native Excel export `src/excel_export.py` and `examples/build_excel_workbook.py`
- `--excel` flag on `run_complete.py`
- GSM simulation with customer backorders at demand node
- `lost_sales` on `(s,Q)` simulation
- Agent skills README; skills synced to Claude Code
- Tests: Excel export, GSM backorders (41 total)

### Changed
- `requirements.txt`: openpyxl, pytest
- Excel templates README: live `.xlsx` workflow

## [1.1.0] - 2026-06-18

### Added
- End-to-end script `examples/run_complete.py` with optional CSV export
- CSV export layer `src/export.py` for Excel / Power BI
- GSM discrete simulation `simulate_serial_gsm` (Ch. 10.5)
- Lost sales mode on `(R,S)` simulation (`lost_sales=True`, §5.3.2)
- Tests for GSM simulation and lost sales (39 total)

### Changed
- README: agent skills section, `run_complete` quick start
- Excel templates README: CSV export workflow

## [1.0.0] - 2026-06-18

### Added
- Gamma demand, distribution selection, gamma loss (Ch. 9) — `src/distributions.py`
- Serial multi-echelon GSM allocation (Ch. 10) — `src/multi_echelon.py`
- Newsvendor discrete/continuous (Ch. 11) — `src/newsvendor.py`
- Histogram PMF and KDE discretization (Ch. 12) — `src/discrete_demand.py`
- Simulation-based safety stock optimization (Ch. 13) — `src/simulation_opt.py`
- Example `examples/run_part4.py`
- Tests for chapters 9–13

### Changed
- README and METHODOLOGY: full book coverage (Ch. 1–13) documented
- `src/__init__.py`: exports for all modules

## [0.2.0] - 2026-06-18

### Added
- Stochastic lead time in policies (Ch. 6) — `src/risk_period.py`
- Fill rate + normal loss function (Ch. 7) — `src/fill_rate.py`
- Cost/service optimization for (R,S) and (s,Q) (Ch. 8) — `src/cost_optimization.py`
- Example `examples/run_part3.py`
- Tests for fill rate, cost optimization, lead time

## [0.1.0] - 2026-06-18

### Added
- Python implementation aligned with Vandeput (2020), Part I–II:
  - EOQ model (`src/eoq.py`) — Ch. 2–3
  - Safety stock under normal demand (`src/safety_stock.py`) — Ch. 4
  - Policies `(s,Q)` and `(R,S)` (`src/policies.py`) — Ch. 5
  - Discrete-period simulation with backorders (`src/simulation.py`) — Ch. 5.3
- Sample demand data (`data/sample_demand.csv`)
- Runnable example (`examples/run_part1_part2.py`)
- Unit tests with book numeric example (§2.2.4)
- Documentation rewritten to reference the book

### Changed
- README: removed placeholder marketing; cites Vandeput (2020)
- Excel / Power BI folders marked as planned export layers

### Removed
- Claims of ARIMA/Prophet templates (not in the inventory optimization book)

### Known limitations
- Normal demand assumption only (Ch. 9+ not yet implemented)
- Backorders only (lost sales in Ch. 5.3.2 — planned)
- Single-echelon, single SKU

---

## [2.0.0] - 2026-03-18 (documentation-only release)

Initial markdown structure without executable templates.
