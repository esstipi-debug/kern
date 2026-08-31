# Changelog

## [Unreleased]

### Added
- **Spec-driven development, with a written constitution.** `specify init --here
  --integration claude` (spec-kit v1.0.2) installs `.specify/` and ten
  `speckit-*` project skills. The piece that is not scaffolding is
  `.specify/memory/constitution.md`: it writes down, for the first time, **the
  eight fixed places a new capability touches** (`jobs/<x>_job.py`, the `from
  jobs import (` block, the three `_<x>_prepare`/`_<x>_run`/`<x>_tool()`
  functions, the `reg.register()` line, `TOOL_CONCEPTS`, `TOOL_TITLES`, and the
  two pinned counts in `tests/`), plus the QA-gate, loud-degradation,
  data-contract, no-module-level-optional-import, ASCII-only and derived-counts
  rules. That list previously existed nowhere: it was reconstructed from git
  archaeology per feature, and reconstructed **wrong** at least once -- a handoff
  called them "the 7 fixed places" and a later plan corrected it to 8.
  `tests/test_constitution_anchors.py` keeps it honest in both directions (a path
  that moves, or a principle deleted while its anchor stays, both fail).
  `.gitignore` had `.claude/*`, so the installed skills were invisible to git,
  CI and every other worktree until `!.claude/skills/` was added.
  `CLAUDE.md`, `README.md` and `scm_agent/README.md` all promised "adding a
  capability = one `register()` call"; the "no routing edits" half is true and
  kept, the count was not.

- **Odoo Apps Store listing packet for `kern_dry_run`** (17.0 and 19.0): `support`
  URL, cover + wizard/result mockups, OPL-1 `LICENSE`/`COPYRIGHT`, Store price
  omitted (free bait). Sync into the dedicated store repo (module at branch root)
  with `python scripts/sync_linchpin_odoo_apps.py`. Both series branches of
  `esstipi-debug/linchpin-odoo-apps` were force-pushed from `kern` on 2026-08-25
  (`17.0` = `72468e7`, `19.0` = `4defa85`) and `online-odoo` was invited with
  `read` on that repo -- no `gh repo create` is needed; the repo has existed
  since 2026-07-04. Playbook: `documentation/GTM_SUBMISSIONS.md` §5.
- **CI no-core gate:** when `NOSILO_CORE_SSH_KEY` / `NOSILO_CORE_TOKEN` are
  empty, GitHub Actions skips `authenticate-core` (instead of aborting before
  pytest), installs without the private git URL, and runs `KERN_CI_NO_CORE=1`.
  The collect_ignore scanner follows parent-package `__init__.py`,
  `importlib.import_module`, and subprocess CLI strings (`examples/*.py`,
  `python -c` snippets), so `from scm_agent.events import ...` is skipped
  instead of dying on `nosilo_core`. The no-core job overrides
  `fail_under=80` (`--cov-fail-under=0`): skipping the core half is not a
  coverage regression.
- **The Odoo module now ships for Odoo 19, as `kern_dry_run`** (`odoo_addon/19.0/kern_dry_run/`,
  v19.0.1.0.0) — verified by installing it on a clean Odoo 19.0-20260817 database in Docker, not by
  reading the code. The port itself touches three files (+43/-12); the Python and the wizard/menu/
  settings views are byte-identical to the 17.0 tree, checkable with a single `diff -rq`. What actually
  broke: Odoo 19 **removed `res.groups.category_id`** and replaced it with `privilege_id`, pointing at a
  new `res.groups.privilege` model, so the module's one security record failed to install. Odoo's own
  19.0 security documentation is stale here — it still documents `category_id` and never mentions
  `res.groups.privilege` — so verifying against the prose rather than the source would have missed it.
  The group hangs off a Kern-owned privilege rather than stock's, because a privilege renders as a
  single select on the user form: sharing stock's would have made "may run an external analysis"
  mutually exclusive with Inventory User/Administrator, the opposite of what that group exists to say.
  Two further changes fail *silently* if skipped — a manifest `version` that does not start with the
  running series is forced to `installable=False` with only a log warning (`odoo/modules/module.py`),
  and `category` generates the `ir.module.category` xmlid, which moved from `Inventory/Inventory` to
  `Supply Chain/Inventory` in 19. Odoo 18.0 contributes no change that touches this module. Field
  semantics were checked against a fixture with known values rather than demo data (the official image
  creates databases without demo), including two vendors at different sequences to prove
  `seller_ids[:1]` still resolves the primary one; `product.supplierinfo.delay` changed label
  (`Delivery Lead Time` -> `Lead Time`) but not meaning. `_assert_safe_base_url` is untouched and still
  blocks localhost, private ranges and the cloud-metadata address. The module still writes nothing.
- **17.0 kept in parallel** (`odoo_addon/17.0/kern_dry_run/`, v17.0.1.0.0, re-verified on `odoo:17`)
  under the *same* technical name, which is what the Apps Store requires for two series to be the same
  app. A reproducible install harness lives in `odoo_addon/test/` (`ODOO_SERIES=17|19`) and the whole
  port is written up in `odoo_addon/PORT_17_A_19.md`.

### Changed
- **Nine of the 45 data tools now declare their data contract, up from four**
  (batch 1, the per-SKU demand-history family: `forecast`,
  `inventory_optimization`, `markdown_liquidation`, `pricing`, `sop`). This is a
  product change, not housekeeping: `src/intake_scan.py` derives its verdict from
  `required_columns`, so an undeclared tool answers `no_declarado` -- "I cannot
  tell you whether your folder can run this". On the repo's own sample folder
  those five went from silence to four greens and one honest yellow.
  Three things had to be fixed before any declaration was safe or provable:
  - **`jobs/intake_scan_job.py` could only ever SEE 15 column names.**
    `_canonical_order()` was built from the four demo `PROFILES`, not from the
    registry, and `_map_headers` only walks that set. Declaring anything outside
    it (`adu`, `price`, `weight_kg`, `mean_demand`, ...) would have dropped the
    client's header and reported the tool RED on a folder that carried the
    column -- a permanent false red produced by declaring the contract
    correctly. It now unions the profiles with every column the registry
    declares, lazily (the registry imports this module).
  - **The guard test only checked that `prepare()` refused, never why.** A job
    that rejected the fixture for an unrelated reason -- a numeric coercion, a
    negative quantity, "no SKUs found in the data" -- returned the same
    `needs_data` and the test went green over a false declaration. It now
    requires the refusal to NAME the dropped column, with a declared translation
    table for jobs that use their own vocabulary (`abc_xyz` and `sop` call
    `quantity` "demand").
  - **The fixture wrote 12 rows with 12 distinct SKUs**, so any job with a
    minimum-observations rule (`jobs/hats_job.py` sets `MIN_OBS = 5`) could never
    have been declared, and any job that resamples to months saw a single
    period. It is now 3 keys x 5 rows with dates spread across months.
- **The intake-scan deck's confidence was a cliff, not a derivation.** It applied
  one flat penalty gated on `n_tools_evaluated * 2 < n_tools_total`, which
  vanishes the instant half the registry declares -- so a scan covering 24 of 45
  tools would have claimed exactly the same certainty as one covering all 45,
  starting midway through this very programme. The penalty is now proportional to
  the unevaluated share and rounded up, so the base is reachable only at full
  coverage. Relatedly, the evaluated/total ratio counted all 50 registered tools,
  five of which never read a file: the summary said "9 of 50" while the finding
  printed beside it said "of 45". Both now say 45.
- **`webapp/mcp_tool_specs.py`** described `forecast` as needing a period column.
  It does not -- verified by running it -- and the declared contract now says so.
- **`linchpin_dry_run` renamed to `kern_dry_run`**, executing item 3 of the HANDOFF external checklist,
  which gated the rename on "the next functional version" — this port is it. No migration hook: the
  module was never actually published, so there are no installations to break. `linchpin.fly.dev`, the
  `linchpin_inventory_optimize` tool name and the `lpk_` key prefix deliberately keep the old name;
  they are wire and infrastructure identifiers, not branding.

### Fixed
- **Two Apps Store rule violations in the Odoo module's description page**, present since 17.0 and
  corrected in both series: an external link (the description may only link inside
  `static/description`) and a promotional block quoting revenue-share and white-label pricing. A
  manifest or listing error unpublishes *every* module in the repository, not just the offending one.
- **Stale cross-references left by the rename**, in `documentation/legal/dpa-lite.md`,
  `documentation/operator/09_checklist_lanzamiento.md` and `documentation/ICP_Y_DIMENSIONAMIENTO.md`.
  The launch checklist also claimed the module's "For partners" section still linked to the partner
  one-pager, which stopped being true when that link was removed for the Store rule above.

### Added
- **Onboarding autoservicio de Odoo Online, en solo lectura** (`src/connectors/odoo_connect.py`,
  `POST /api/odoo-lead-time`) — Odoo Online no admite código Python de terceros, así que
  `odoo_addon/linchpin_dry_run` nunca fue una opción para el SaaS de odoo.com; la única puerta es la
  External API que `odoo.py` ya hablaba. Lo que faltaba no era el conector sino el tramo de **cuatro
  campos** (url, base, usuario, clave de API) hasta el mismo `scan_from_deliveries` que consume un CSV
  subido — el motor de la salida #1 no se entera de dónde vinieron las filas.
  `check_connection()` **clasifica** la falla en vez de devolver un "no se pudo conectar" genérico, que
  en un embudo sin humano al otro lado no le sirve a nadie: host inalcanzable / base inexistente /
  credencial inválida / permisos insuficientes mandan a cuatro pantallas distintas del Odoo del cliente.
  La separación base-vs-credencial es determinista y no por texto (`authenticate` no *puede* devolver
  `False` contra una base que no existe: no logra abrir el cursor, levanta), y la de permisos-vs-módulo
  se resuelve consultando `ir.model` antes de probar lectura — un `"AccessError" in fault` funciona en
  inglés y falla en el Odoo en español del cliente, que es exactamente donde tiene que funcionar. Se
  agregó un quinto motivo, `modulo_ausente`: sin la app de Compras instalada el remedio está en
  Aplicaciones, no en permisos de usuario, y confundirlos es media hora perdida del cliente.
  `preflight()` es **pura sobre lo ya leído** (cero RPC extra: una segunda pasada por el ERP se paga en
  latencia y puede contar sobre un estado distinto del que después se mide) y reporta recepciones,
  proveedores, rango de fechas y qué falta. **Sin fechas de recepción se niega a correr nombrando lo que
  falta, y nunca cae a `product.supplierinfo.delay`** — usar la promesa como observación la compara
  consigo misma y devuelve brecha cero sobre el defecto exacto que el escaneo viene a mostrar; es la
  misma regla que el camino del CSV defiende con un test dedicado, replicada aquí.
  La ruta va **autenticada** (`require_api_key` + `rate_limit`, igual que `POST /api/jobs`): una
  credencial del ERP de un cliente no se pide en un formulario anónimo por más que el alcance sea de
  solo lectura. Los cuatro campos llegan con default `""` para que un campo faltante lo rechace el
  handler con su propio mensaje y no un 422 de pydantic, cuyo cuerpo puede arrastrar el resto del
  formulario —con la clave dentro— a la respuesta. Cero writeback, cero persistencia de la credencial,
  cero línea en `leads.jsonl`; `_scrub()` borra la clave de todo texto que venga del servidor.
  57 tests offline contra `InMemoryOdooServer` (nuevo doble del **transporte**: `InMemoryOdoo` dobla el
  ORM, y los cuatro modos de falla viven una capa más abajo). Bajo la regla del gate endurecido: los que
  afirman que algo se mide corren **dos** Odoo con plazos distintos y exigen que la brecha publicada se
  mueva —y por los dos ejes, observado y declarado—, porque `assert "lead_time_days" in fila` pasa igual
  de verde sobre un número correcto que sobre una constante.
- **Shopify Admin API version drift detection** (`src/connectors/shopify_api_version.py`) — Shopify
  does not error when the pinned API version falls out of support; it silently *falls forward* and
  serves the request with the oldest version it still accepts, so a stale pin looks healthy while the
  semantics underneath it move. `check_served()` compares the `X-Shopify-API-Version` response header
  against the version requested: a drifted read is logged, a drifted write raises the new
  `ShopifyVersionDriftError`, since writing stock or prices under the wrong version's semantics puts
  bad data in the customer's system of record. `discover_versions()` / `check_pin()` classify the pin
  against Shopify's `publicApiVersions` (SOPORTADA / POR_VENCER / VENCIDA) so an operator can see drift
  coming before it happens. The pin itself — duplicated as `_DEFAULT_API_VERSION` in both the prices
  and inventory connectors — is now the single `PINNED_VERSION` constant in this module, bumped from
  `"2024-10"` to `"2026-07"`.
- **Idempotency key on the two Shopify inventory writes** (`src/connectors/shopify_inventory.py`) —
  `inventorySetQuantities` and `inventoryAdjustQuantities` require the `@idempotent(key: ...)` directive
  as of API version 2026-04, without which the pinned-version bump above would have broken both
  mutations outright. `ShopifyInventoryRPC.set_inventory()` / `.adjust_inventory()` gained a required
  keyword-only `idempotency_key` parameter — **a breaking change for any direct caller of
  `ShopifyInventoryRPC`**, callers now derive the key per entity rather than per changeset so retries of
  the same logical write collide as intended.

### Fixed
- **Two Shopify inventory writes stopped returning a plausible value for an unconfirmed change.**
  `set_inventory()` used to echo back the requested quantity when Shopify's response omitted the
  `inventoryAdjustmentGroup` field (a wrong field name, not a genuine confirmation); `adjust_inventory()`
  used to return `0` in the same situation. Both now raise `ShopifyInventoryError` instead, since a
  returned number the caller cannot distinguish from a real confirmation is worse than an explicit
  failure.

### Added
- **Identity on the live request path** (`webapp/principal.py`, `webapp/security.py`). The access
  layer added previously governed entity-store reads, and nothing in the running service called them
  — it was, honestly, a library the app did not use. This wires it in: every request now carries a
  `Principal` and a starting `Capability` on `request.state`, derived in exactly one place.
  - **Three identity modes, in decreasing order of what they can prove.** Trusted proxy headers
    (opt-in), the shared API key, and anonymous. Identity derivation scattered across route handlers
    is how an authorisation bypass ends up in a codebase without anyone choosing it, so it lives in
    one file.
  - **`X-Auth-*` headers are ignored unless `LINCHPIN_TRUSTED_IDENTITY_HEADERS=1`.** Reading them is
    the shape every real OIDC deployment ends up in *and* a complete authentication bypass if the
    proxy that strips the client's own copy is not there — any caller could otherwise assert any
    identity. Off by default, flagged loudly in production when on, flagged twice when on with no API
    key. Tested from the negative side: forged headers are ignored end to end through the real
    middleware.
  - **The API-key holder is a `service` principal, never a human.** A shared secret identifies a
    deployment; putting a person's name on an audit entry nobody is accountable for is worse than
    leaving it anonymous.
  - **The middleware attaches, it never enforces.** Authorisation happens at the point of access,
    where the action and resource are known; a middleware returning 403 would have to guess them from
    the URL. It also cannot fail a request — a malformed header degrades to anonymous rather than
    becoming a 500.
  - **`production_warnings()` gained the access checks**, and `test_secured_production_has_no_warnings`
    was updated with them. That test encoded a definition of "secured" that was complete when written
    and no longer is: a deployment with a shared API key and no `PolicySet` has no per-principal
    authorisation at all.
  - Nothing changes by default. With no configuration every request is anonymous, the policy engine
    is ungoverned, and behaviour is identical to before.
  25 tests. Note: `webapp/` has never been in CI's `--cov` scope, so `webapp/principal.py` is
  tested but not coverage-gated; widening that scope would move the baseline and belongs in its own
  change.
- **`packs/source_to_pay` — the second domain pack, and the test of whether the core extraction
  actually bought anything.** Accounts payable: three-way match of supplier invoices against purchase
  orders and goods receipts, with price/quantity variances, duplicate detection, currency mismatch and
  self-inconsistent-document checks. Not one line of supply chain, and **it imports nothing from
  `src`** — `tests/test_pack_boundary.py` enforces that, which turns "the core generalises" from a
  claim into a check that fails when it stops being true. Verified to bite: a deliberate
  `from src.eoq import ...` in `matching.py` fails the suite.
  - **One `register()` call** puts it through the identical `prepare → run → qa → deliver` pipeline the
    49 supply-chain capabilities use, with the same QA veto and the same never-unprotected outcome. No
    orchestrator change, no routing special case. If a second domain had needed any of those, the tool
    contract would have been an SCM contract wearing a generic name.
  - **`supplier` is reused, not redeclared.** `build_s2p_registry()` accepts the SCM pack's registry so
    both packs observe into one shared entity type — two packs with their own idea of the same company
    is exactly the silo the platform exists to remove.
  - **`Decimal` end to end.** A matcher on binary floats disagrees with the supplier's arithmetic by a
    cent somewhere and raises an exception a human then dismisses, which trains people to click through
    exceptions — worse than not checking at all.
  - **Tolerances take whichever bound is greater**, absolute or percentage: 2% is meaningless on a €3
    line and €5 is meaningless on a €50,000 one. Stated explicitly because the stricter reading also
    exists in the wild and the choice silently changes which invoices need a human. Defaults are tight
    (1% price, zero quantity slack) so a tolerance nobody chose is never why an overcharge is paid.
  - **Severity is about consequence, not size.** A €1 duplicate blocks (the failure mode is paying
    twice); a €90,000 price variance only asks for review (recoverable). A duplicate is surfaced as its
    own `HANDOFF` rather than one row among ninety exceptions somebody skims.
  - **QA refuses a run where almost nothing matched.** An exception rate above 80% is nearly always a
    mapping problem, and delivering it as "94% of your invoices have exceptions" sends an AP team
    chasing ghosts for a day.
  - Guarded fields carried over from the core: invoice totals and the remit-to account cannot be moved
    by an OCR pipeline, at any trust level — extraction confidence is not authority.
  - Pure and clock-free, including "which invoices have we seen before", so a disputed match is
    replayable months later.
  65 tests (30 matching engine + 23 pack integration + 12 boundary). Also fixes `nosilo_core.access` missing from the setuptools
  package list, which would have excluded it from a built wheel.
- **`nosilo_core.access` — identity and policy in front of the core**, plus tenant scoping and a
  governed read path in the entity store. Kern authenticates with one shared API key today: there is
  no principal, so there is nothing to authorise against, and "can this agent see supplier bank
  details" has no answer the product can give. This supplies the missing nouns.
  - **Capability attenuation can only narrow.** There is no widening operation in the module, so
    privilege escalation is absent from the algebra rather than prevented by a check somebody has to
    remember. `tenant` is deliberately not a parameter of `attenuate()` — crossing tenants is not a
    narrowing operation and must be unreachable. An exhaustive test runs every ordering of every
    subset of attenuation requests and asserts the result stays inside the original grant on every
    axis.
  - **Delegation to an agent** intersects the human's rights, the agent role's rights and the single
    declared purpose, bounded by an expiry, and records the chain for audit. An agent with no
    `on_behalf_of` cannot be constructed: no accountable owner, nothing to authorise.
  - **Per-field sensitivity, not per-record.** A buyer needs a supplier's lead time and has no
    business seeing its bank account; denying the whole record makes the product unusable and
    returning it whole makes it unsellable. `bank_account` and `credit_limit` are `DROP`ped rather
    than masked — a masked account number still confirms which account is on file, which is enough
    to make a convincing change request. `unit_cost` is bucketed to an order of magnitude.
    Unclassified fields default to `INTERNAL`, so a field nobody has thought about is not the one
    that leaks.
  - **Every decision carries a `reason_code` and a `policy_version`**, including denials. Deny beats
    allow; no matching rule denies; `REQUIRE_APPROVAL` is explicitly *not* `allowed`, because
    treating "a human must sign this off" as a soft yes is how approval gates get bypassed.
  - **Tenant is in every primary key and every `WHERE`** in the entity store, from the first version.
    Two stores over one file see disjoint data even for identical source ids and natural keys. Added
    now because adding it later means a migration, a backfill, and re-auditing every query written in
    between.
  - **The governed read is a separate method** (`store.read`), not an optional `principal=` argument
    on `current()`. An optional security parameter is one a caller forgets and a reviewer cannot see
    the absence of. `current`/`as_of`/`history` stay ungoverned on purpose: `observe()` merges
    against real values, and a masked value must never be written back as if it were data.
  - **A denial returns exactly what a missing entity returns.** Distinguishing them would turn every
    denial into an existence oracle.
  - Unconfigured, the policy engine permits everything and reports `no_policy_configured` — the same
    "empty disables the gate" convention `webapp/security.py` already uses. `ungoverned_warning()`
    is how a production deploy finds out it is still in that state.
  - Not OPA/Rego: `nosilo_core` is stdlib-only by contract and a sidecar is a large operational ask
    for a rule set this size. The trigger to switch is documented in `policy.py`.
  51 tests (39 access + 12 store integration), plus 12 new boundary cases for the new subpackage.
- **`nosilo_core.entities` — canonical entities with stable identity, per-field provenance and
  bitemporal history**, plus `src/entities.py` declaring the three supply-chain master-data types
  (**SKU**, **supplier**, **location**) on top of it. Kern has been job-centric until now — a file
  goes in, a deliverable comes out, nothing is remembered between runs — which is fine for producing
  a plan and useless for answering "what did we know about this supplier on the day we approved that
  PO", or for recognising that Odoo's `product.product/42` and Shopify's `variant/99` are the same
  SKU. Entity types are declared by the *domain*, not the core: a source-to-pay pack registers
  `Invoice` exactly the way `src/entities.py` registers `Sku`, with no change to `nosilo_core` —
  the first real proof that the extraction bought what it was meant to buy.
  - **Genuinely append-only.** No `UPDATE` on `entity_version`, not even to stamp a closing
    timestamp. Because every version is a full snapshot, "the version in effect at
    (`valid_at`, `known_at`)" is just the highest version at or before both instants;
    `valid_to`/`superseded_at` are derived from the neighbouring version at read time. Enforced by a
    test that traces every SQL statement the store issues.
  - **Two time axes.** `valid_from` is business time ("when was this true"), `recorded_at` is system
    time ("when did we learn it"). The second is the one that reconstructs what a decider actually
    knew, and neither can be added retroactively — the information was never captured.
  - **Provenance per field, not per record.** A supplier's legal name can come from the ERP while its
    bank account comes from a portal and its risk score from a third party.
  - **Deterministic resolution**: declared owner → guarded-field hold → same-source revision →
    trust → recency → source ordering. Total, so replay is reproducible.
  - **Guarded fields** (`bank_account`, `credit_limit`, `payment_terms`, `list_price`) are never
    overwritten by an automated feed at any trust level; a competing claim surfaces as a
    `FieldConflict` for a human. Supplier bank-account takeover arrives looking exactly like a
    routine master-data sync.
  - **Money stays `Decimal` end to end**, including through JSON/SQLite via a tagged round-trip.
  - Two bugs found by the tests and fixed before merge: (1) same-source revisions were dropped when
    two observations shared a timestamp string, which they routinely do on Windows' ~15 ms timer
    resolution — a source is now always authoritative over its own earlier claim, checked *before*
    trust/recency precisely because those can tie; (2) `src.connectors.Product.cost` defaults to
    `0.0`, so a storefront that simply does not track cost was asserting a zero book cost — a
    non-positive amount from a connector DTO is now treated as "not reported".
  - Exact natural-key matching only. Probabilistic entity resolution, merge/unmerge, backdated
    corrections and policy-filtered reads are deliberately absent: each is a design decision rather
    than a mechanical addition, and a wrong probabilistic match welds two real entities together.
  92 tests.

### Changed
- **`nosilo_core` — the domain-agnostic control plane extracted into its own package.**
  Seven modules move out of `src/` and `scm_agent/` with their history intact:
  `writeback` + `writeback_store` (the safe-staging write plane), `guided` +
  `escalation` (the never-unprotected contract), `registry` (the
  `prepare → run → qa → deliver` tool contract and the "QA fails ⇒ no deliverable"
  veto), `types`, and `llm` (the `LLMProvider` protocol). None of them knew anything
  about supply chain; keeping them inside the SCM package meant a second domain pack
  could not reuse them without inheriting SCM. Pure move — no behavior change, no
  compatibility shims: all 136 consumers were rewritten to import from `nosilo_core`
  so the boundary is visible at every call site rather than hidden behind a
  re-export. Imports *inside* the package are relative, so it can be lifted into its
  own distribution without editing a line. The core turns out to be stdlib-only apart
  from a lazy, optional `anthropic` import in `llm`.
  `tests/test_core_boundary.py` (22 tests) is what makes this stick: it walks each
  core module's AST — so a lazy import inside a function body is caught like a
  top-level one — and fails if the core ever imports `src` / `scm_agent` / `jobs` /
  `webapp`, pulls in an unlisted third-party package, or references a domain module
  by string for a dynamic import. CI now lints and measures coverage on
  `nosilo_core` alongside `src`; dropping it from `--cov` would have quietly stopped
  gating the code that gates every write. Suite: 4173 → 4195 passing (+22, exactly
  the new boundary tests), 3 skipped, 95.62% coverage.

### Added
- **`network_design` — multi-facility p-median network design tool** (`src/network_design.py`, `jobs/network_design_job.py`). Solves which `p` of several candidate sites to open and assigns each demand point to one, minimizing total weighted travel (with optional fixed costs/capacities), via an exact MILP (`scipy.optimize.milp`, HiGHS branch-and-bound) — the multi-facility counterpart to the existing single-site `facility_location` tool (continuous center-of-gravity/Weiszfeld). Reuses `facility_location`'s `DemandPoint`/`Location`/`total_weighted_distance` primitives rather than duplicating them. Reports the weighted-travel saving versus the best single-facility baseline. Part of the CSCP/SCPro gap-closing initiative (raising ASCM/CSCMP reference-doc coverage). 15 tests (engine + job + routing/orchestrator).
- **`supplier_management` — Kraljic supplier segmentation tool** (`src/supplier_management.py`, `jobs/supplier_management_job.py`). Segments suppliers on the classic spend-x-supply-risk quadrant matrix (leverage/strategic/non-critical/bottleneck) from a supplier CSV, with a ranked `GuidedOutcome` per quadrant. Part of the CSCP/SCPro gap-closing initiative (Phase 1, alongside `network_design`). Two real bugs found and fixed during review: a NaN spend value corrupting every supplier's spend_share via a shared total, and `normalize_drivers` re-sniffing the supplier column internally instead of honoring `prepare()`'s resolved `supplier_col` override (silently zeroing every supplier's risk score for non-English column names). Full engine + job + routing test coverage.
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
- **`GET /how-it-works` was advertising 41 of 50 tools** (`webapp/how_it_works_data.py`). The
  hand-maintained `TOOLS` table had fallen **nine** tools behind `build_default_registry()`:
  `supplier_management`, `network_design`, `disruption_scan`, `hat_tension`, `hat_settlement`,
  `flow_map`, `cross_impact`, `org_friction_audit` and `intake_scan` each landed a `register()`
  call with no matching `ToolInfo`, and nothing failed. Accumulated drift across several PRs, not
  a regression from any one of them. This page is client-facing and gets quoted in sales
  conversations, so a stale table understates the product to a prospect -- it is not a cosmetic
  count. Adds the nine entries (both lenses + legend) and a **drift guard** asserting set equality
  between `TOOLS` and the registry in both directions -- the pattern that already catches this in
  `tests/test_scm_agent.py` and `tests/test_capability_smoke.py`. The guard immediately earned
  itself twice: `org_friction_audit` (#198) drifted the table again within a day of the original
  fix, and `intake_scan` did the same during this PR's rebase onto `main` -- both caught by the
  assertion rather than by someone reading the page. The old test asserted only
  `len(TOOLS) == 41`, which is self-consistent and can never go red on its own; that is the
  failure mode being closed, not the count itself. The three decision-alignment tools get their
  own domain area rather than being folded into "Inventory & replenishment", which would have
  overstated the inventory engine from 9 tools to 12 on a chart people read in sales calls.
- **A built wheel silently shipped without thirteen subpackages** (`src.connectors`, `src.state`,
  `src.verify`, `src.pricing_intel` and its two children, `src.simworld`, `src.seo`, `src.voice`,
  `src.lots`, `src.logistics`, `src.sop_engine`, `src.bateria`). `[tool.setuptools] packages = [...]`
  is an explicit list and does **not** auto-include subpackages, so each new one had to be
  remembered — and twice it was not. Verified rather than reasoned about: a wheel built from the
  previous config installs into a clean venv where `import src.connectors` raises
  `ModuleNotFoundError`; with the fix it imports. 111 → 189 modules in the wheel, none lost.
  Replaced with `[tool.setuptools.packages.find]` so discovery is automatic and the list cannot rot
  again. Patterns (`nosilo_core*`, `packs*`, `src*`) rather than bare discovery, because `webapp`,
  `scm_agent` and `jobs` are deliberately not packaged — they are the application, imported off
  `PYTHONPATH` by uvicorn.
  Nothing was broken in practice, which is exactly why it survived: the Dockerfile and Railway both
  `pip install -e .` with `PYTHONPATH` on the source tree, so imports never resolve through the
  installed package and the list was never exercised. Free to introduce, invisible until the first
  `pip install .`.
- **`optimize_sku_price` could ship an exploding markup as `status="ok"`** (adversarial pricing
  audit, 3 findings). (1) *Wrong statistical gate*: the optimizer gated on the elasticity CI
  excluding **0**, but the constant-elasticity markup `p* = c*eps/(eps+1)` explodes as
  `eps -> -1` from below — `eps = -1.05` implied a price of **21x cost**, shipped as `ok`. The
  gate now also requires the SKU's own CI to exclude **-1** in the elastic direction
  (`ci_high < -1`), else `needs_data` with an honest reason. (2) *No move-size clamp*:
  `current_price` was carried but never used in any guard. Proposals are now clamped to a
  configurable per-step band vs the live price (`max_move_pct`, default +/-20%, `None`
  disables) and to ~1.3x the observed price range the elasticity was fitted on
  (`SkuElasticityFit.price_low/price_high`, the same extrapolation guard
  `src.pricing.recommend_price` already applied to its `confident` flag) — both disclosed on
  the result (`move_clamped`/`range_clamped`); the landed-cost/margin floor still wins over
  both clamps. (3) *Gate had no economic sanity checks*:
  `src/pricing_guardrails.py::gate_price_changeset` (the central pre-ship gate) now blocks any
  staged change moving a price beyond `max_move_pct` (default 50% backstop, `None` disables)
  and — when the caller supplies `landed_costs` (forwarded from
  `jobs/repricing.py::stage_repricing`) — any price below cost, since `min_margin_pct`/
  `floor_ratio` of 0 upstream provide no floor. All violations reported at once. 26 tests.
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
