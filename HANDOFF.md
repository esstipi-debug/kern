# Linchpin — Session Handoff

**Date:** 2026-07-11 · **Repo:** `esstipi-debug/linchpin` (private) · **Branch:** `feat/e8-internal-tooling`, **PR #138 open as draft**. E1 **#125**, E2 **#128**, E3 **#129**, E4 **#131**, E5 **#134**, E6 **#136** all merged to `main` and deployed live on `https://linchpin.fly.dev`. E7 **#137** open as a draft, reviewed, awaiting merge go-ahead (legal templates — see that branch/PR; this worktree branched from E6's merge commit and does not contain E7's files, which is expected — E7 and E8 don't touch any of the same files). **#122** audit-evidence, **#123** benchmarks, and a `docs/refresh-stale-counts`-style worktree still open concurrently. Note: PR numbering has jumped before (concurrent sessions on this repo also open PRs — see `linchpin-concurrent-sessions` memory) — a gap in the sequence is not a mistake in this session.

**Purpose:** pick up Linchpin work in a fresh session without re-deriving context.

> **Permanent priority rule:** if a `PIPELINE.md` file exists at the repo
> root describing an active deal, that work takes priority over **everything
> else in this file** — any checklist item, any Linchpin 2.0 épica, any
> "next step" noted below. Check for it before picking up anything else.
> No `PIPELINE.md` exists as of this entry (no active deal) — don't create
> one speculatively; it should only exist when there's a real deal to track.
> Suggested format when one is actually needed (keep it this loose — this
> is a scratch file for one person's own tracking, not a deliverable):
> ```markdown
> # Pipeline — <cliente>
> **Estado:** <ej. "esperando datos de intake" | "propuesta enviada" | "negociando alcance">
> **Paquete:** <ej. Diagnostico de Arranque>
> **Próximo paso:** <la acción concreta siguiente, con fecha si la hay>
> **Última actualización:** <fecha>
> ```
> `PIPELINE.md` is real per-deal working data (like `clients/`), not a
> template or sample to commit — gitignore it if you create one.

## 2026-07-13 — ICP LATAM + kit de publicidad (docs only, PR draft, worktree `.wt-kern-icp`)

**Read this section first if you're picking up the "quien es el cliente
final" thread.** Two new docs, both PR-ready as pure documentation (no code
touched, so no ruff/pytest gate applies): `documentation/ICP_Y_DIMENSIONAMIENTO.md`
(the full research — product truth VERIFICADO against code, LATAM market
research VERIFICADO/ESTIMADO, the 6 questions from the brief answered
explicitly) and `documentation/KIT_PUBLICIDAD.md` (ready-to-use ad assets:
ICP one-pager, value prop, 5 message angles, buyer×package table, prohibited
claims list).

**Headline findings, so a fresh session doesn't have to re-derive them:**
- **ICP primario:** retailer/distribuidor/manufactura liviana LATAM, USD
  1-15M facturación anual, sin equipo propio de data science, comprador es
  dueño/CEO (PyME chica) o director de Ops/SC/Compras + COO/CFO (mid-market).
  Ver `ICP_Y_DIMENSIONAMIENTO.md` §3.1 para el ICP secundario y los
  disqualifiers.
- **Correccion importante al propio brief que origino esta tarea:** el
  supuesto "conectores Mercado Libre + Odoo" es **falso** — grep completo del
  repo confirma que Mercado Libre no existe en ningun lado del codigo. Los
  unicos conectores reales son Odoo (`src/connectors/odoo.py`) y Excel
  (`src/connectors/excel.py`); Shopify/Amazon son placeholders de diseno
  futuro (`emulator.py`/`simulator.py`), no productos. **No repetir el claim
  de Mercado Libre en material de venta.**
- **Case studies reales: no existen todavia.** `case-studies/CASE_STUDIES.md`
  son ejercicios de libro de texto (dice literalmente que los case studies de
  marketing anteriores eran placeholders) — cualquier cifra de "$ ahorrados"
  en pauta hoy tiene que venir de un rango de mercado citado, nunca
  presentarse como resultado propio de un cliente real.
- **Autonomia end-to-end (82%/75-80%/40-50%): sigue STALE.** No se encontro
  un re-calculo posterior al cierre de los 5 gaps (#130/#132/#133/#135/#139)
  en `main` — un numero real requiere re-correr el workflow de auditoria, no
  solo leer el codigo. No usar esos porcentajes en material publico hasta
  re-auditar; el claim de venta seguro es el contrato estructural
  (`src/guided.py`: 4 desenlaces, 3 de 4 necesitan humano — eso si es
  100% verificable y estable).
- **Tool count confirmado sin cambios:** 37 tools registradas
  (`scm_agent/tools.py`), 33 expuestas via MCP (gap sigue siendo
  `excel_replenishment`/`leadership_chain`/`odoo_replenishment`/
  `warehouse_layout`, sin cambio desde la auditoria previa).
- **Dimensionamiento bottom-up (Argentina + Mexico, con fuentes oficiales
  reales — SICYPYME/Boletin Oficial AR, INEGI Censos Economicos 2024 MX):**
  ~199.000 empresas Pequena+Mediana en sectores con inventario fisico entre
  los dos paises. Colombia/Chile NO investigados esta sesion — no
  extrapolar. El eslabon mas debil del embudo es "alcanzable por pauta
  digital -> lead -> cierre", que no tiene benchmark LATAM propio (se uso un
  supuesto conservador marcado ESTIMADO) — recomendacion: correr una
  campana piloto chica para calibrar antes de comprometer presupuesto.
- **Tension no resuelta con `MONETIZATION_BRIEF.md`:** ese doc ya concluyo
  que la via principal de monetizacion de corto plazo es marcas Shopify/DTC
  US/UK (fractional supply chain operator, en ingles), con LATAM/Odoo como
  canal SECUNDARIO. Esta tarea profundizo el angulo LATAM porque eso es lo
  que se pidio, sin sobreescribir esa conclusion previa — ver
  `ICP_Y_DIMENSIONAMIENTO.md` §2.1 antes de tratar LATAM como el plan
  principal en una decision de presupuesto real.
- **Nota metodologica:** 5 agentes en paralelo (extraccion de one-pagers,
  verificacion de codigo, 3 angulos de market research) fallaron al arrancar
  por limite de sesion de la API ("session limit, resets 9:30pm
  America/Santiago") — el trabajo se rehizo en el hilo principal con
  Grep/Read directos + WebSearch/WebFetch secuencial en vez de reintentar
  con mas agentes paralelos. Si esto se repite, preferir llamadas directas
  secuenciales sobre relanzar varios agentes a la vez.

**Que sigue (no arrancado esta sesion):** publicar el modulo Odoo (checklist
ya lista en `GTM_SUBMISSIONS.md`) para tener el primer canal LATAM medible;
correr una campana piloto chica para calibrar el embudo real; extender el
dimensionamiento a Colombia/Chile si LATAM se confirma como prioridad y no
solo como canal secundario.

## 2026-07-12 — Rename: Linchpin -> Kern (interno COMPLETO; checklist EXTERNA abajo)

**Que paso:** el proyecto se llama **Kern** (aleman: nucleo). No es cosmetico —
marca la evolucion de "herramienta que analiza" a "el nucleo de decisiones
sobre el que corre el servicio de la agencia" (angulo Tower-first del plan 3.0
+ los paquetes comerciales). La narrativa completa vive en
`documentation/KERN_IDENTIDAD_Y_FILOSOFIA.md` (escrita por la sesion del
branch `feat/state-snapshot-module`, commit `2bd712f`, cherry-picked aqui y
extendida). La narrativa se afirma SOLO en lo verificable en main: QA-gate,
citas a 25 fuentes, writeback staged con rollback, guided outcomes.

**Rename interno (este PR):** todo lo user-facing dice Kern — README (con
parrafo de evolucion), CLAUDE.md, sales docs + 9 one-pagers (pie narrativo),
operator portfolio, webapp UI, deliverable branding, voice agent, prosa MCP,
`pyproject.toml` (`name = "kern"`), LICENSE, CHANGELOG (entrada nueva).

**Queda "linchpin" A PROPOSITO (identificadores API/infra, no marca):**
repo GitHub + `linchpin.fly.dev` + `cd linchpin` en docs (hasta ejecutar la
checklist externa) · env vars `LINCHPIN_*` · tool names MCP `linchpin_*` +
server name `linchpin_mcp` (contrato con clientes MCP conectados) · prefijo de
keys `lpk_` (keys emitidas siguen funcionando) · logger `linchpin.access` ·
`odoo_addon/linchpin_dry_run/` (identidad del modulo en el Store) · slugs
`linchpin-*` en CAPABILITY_EXPANSION_PLAN · historicos (graph-memory,
docs/superpowers, CHANGELOG viejo, books graph generado) · `server.json`
`name`/URLs (atados al repo name para validacion del registry MCP — se
actualizan JUNTO con el repo rename, ver checklist).

### CHECKLIST EXTERNA (operador — comandos exactos, NO ejecutados)

1. **GitHub repo rename** (redirige URLs viejas automaticamente):
   ```bash
   gh repo rename kern --repo esstipi-debug/linchpin --yes
   # despues, en el checkout principal y CADA worktree (.wt-*):
   git remote set-url origin https://github.com/esstipi-debug/kern.git
   # y actualizar: server.json (name -> io.github.esstipi-debug/kern,
   # repository.url), README/CONTRIBUTING (`cd linchpin` -> `cd kern`),
   # badges/links con /linchpin en README y docs.
   ```
2. **Fly.io** — recomendacion: **mantener `linchpin.fly.dev` hasta tener
   dominio propio** (kern.fly.dev como app nueva = migrar secrets/volumen/
   keys de clientes MCP que apuntan a la URL vieja, y `fly.dev` no redirige —
   rompe integraciones por un subdominio que igual no es la marca final).
   Cuando haya dominio (p.ej. `kern.agency` / `getkern.dev`), apuntarlo a la
   app existente con `flyctl certs add <dominio>` y recien ahi decidir si
   renombrar la app. Si igual queres kern.fly.dev ya:
   ```bash
   flyctl apps create kern && flyctl deploy -a kern && flyctl secrets set ... -a kern
   # migrar keys MCP de clientes, avisar, y recien despues: flyctl apps destroy linchpin
   ```
3. **Modulo Odoo `linchpin_dry_run`** — recomendacion: **renombrar en la
   PROXIMA version funcional, no ahora.** Un rename de modulo tecnico
   (directorio + manifest + XML ids) es re-submission completa al Store y
   rompe upgrades de instalaciones existentes; hacerlo sin cambios
   funcionales es puro costo. Cuando toque: nuevo modulo `kern_dry_run` con
   hook de migracion desde `linchpin_dry_run`.
4. **Listings MCP** — donde figure Linchpin (directorios MCP, cuando se
   ejecute el plan de listings de [linchpin-monetization-plan]): usar nombre
   Kern + descripcion nueva de `server.json`; requiere logins del operador.

### Integraciones code-intel (mismo PR) — 2 comandos pendientes del operador

`.mcp.json` ahora wirea **codegraph** (indice de codigo continuo, zero-LLM) y
**serena** (LSP sobre MCP via uvx). El CLI de codegraph quedo instalado (npm,
v1.4.1) pero su PRIMERA ejecucion quedo gateada por permisos de la sesion
(politica supply-chain sobre binarios nuevos — razonable). Para activar:
```bash
cd <repo> && codegraph init        # construye .codegraph/ (gitignored), luego auto-sync
# serena no necesita init: el primer arranque via .mcp.json descarga y corre (trust prompt de Claude Code)
```
**LightRAG NO se integro a proposito** — queda como carril sombra futuro solo
si la recuperacion del books graph se queda corta (ver memoria
`graphify-alternatives-verdict`). graphify sigue canonico (books graph =
infraestructura del producto; estabilidad de citas = moat).

## 2026-07-11 — E8 "tooling interno" — reviewed, fixed, PR #138 open (draft) — needs merge go-ahead

**Read this section first if you're picking up cold.** E8 is code-complete,
adversarially reviewed, and **PR #138 is open as a draft** — the last
épica in the Linchpin 2.0 build protocol. Nothing is blocking except the
operator's explicit "mergea el PR #138" — do not merge it proactively. If
the operator gives that instruction: merge, deploy to Fly (this DOES touch
`webapp/app.py`, unlike E7), verify `GET /api/metrics` responds live, clean
up this worktree/branch — and that's the end of the Linchpin 2.0 protocol
as originally scoped (E1 through E8). What comes after is either a real
deal working through `PIPELINE.md` (see the rule above) or a fresh
protocol/priority the operator defines from here.

### What E8 actually was — reconstructed from breadcrumbs, not the original spec

The original Linchpin 2.0 protocol text (pasted directly into an early
chat, never committed to the repo) was lost across a context-window
compaction partway through this multi-session build. By the time this
session reached E8, all that remained was three breadcrumbs, found by
grepping this file and the checklist:
`documentation/operator/09_checklist_lanzamiento.md`'s own placeholder
line ("E8 — ninguna, es solo tooling interno" + the "Regla permanente ...
cuando E8 aterrice" section describing the `PIPELINE.md` rule above), and
an old E2-era note in this file's own history mentioning
`leads.jsonl`'s `status` field exists "so E8's `/api/metrics` can count
demos-run vs demos-converted later." From those three fragments, this
session reconstructed E8 as: **(1)** a `GET /api/metrics` endpoint
aggregating the demo/lead funnel telemetry, and **(2)** formally writing
the `PIPELINE.md`-priority rule into this file (not just the checklist),
since this file — not the checklist — is what a fresh session actually
reads first. If the original protocol specified something more/different
for E8, it's lost; this is a defensible reconstruction, not a rediscovery
of the real thing. Flagging this loudly in case a future session finds
the original notes somewhere and E8 needs revisiting.

### What was built

- **`GET /api/metrics`** (`webapp/app.py`): reads `leads.jsonl` (the only
  operational telemetry stream in the codebase — confirmed nothing else
  logs `run_package`/commercial-package activity anywhere) and returns
  aggregate counts only — total captures, unique emails (never the emails
  themselves), a by-source breakdown, and for `demo-scan` specifically,
  counts by status and by dataset. `source`/`status`/`dataset` are all
  **caller-controlled** (a scripted `POST /api/leads`, or the filename a
  lead's own upload happens to be named on `/api/demo-scan`) — an early
  version of this endpoint echoed them as response keys unsanitized and
  uncapped, which the adversarial review below caught as both a real PII
  leak (an email-named upload landed verbatim in the response) and an
  unbounded-growth vector. Fixed via `_metrics_label` (strips to a safe
  character set, caps length) and `_metrics_bump` (caps distinct buckets,
  folds overflow into `"other"`) — every bucket key in the response is now
  provably sanitized and bounded, not just assumed safe because the two
  current writers happen to behave. Gated behind
  `Depends(security.require_api_key)` + `Depends(security.rate_limit)`,
  the same pattern as `POST /api/jobs` — a no-op when `LINCHPIN_API_KEY`
  is unset (the shipped default), so this doesn't force auth in local/dev
  use, only once an operator deploying publicly opts in. A malformed
  line, or a syntactically-valid-JSON line that isn't an object (e.g. a
  hand-edited `leads.jsonl` with a bare string/number/list on one line),
  is skipped, not a crash — an earlier version only guarded
  `json.JSONDecodeError` and crashed (permanently, since `leads.jsonl` is
  append-only with no rotation) on the second case. `SECURITY.md` updated
  (the `LINCHPIN_API_KEY` table row, the "Controls enforced in code" row,
  and the regression-tested-in paragraph) to match.
- **The `PIPELINE.md` priority rule**, moved into this file's own
  standing header (see above) rather than living only in the checklist,
  which a fresh session might not read at all. The checklist's own
  section was shortened to point back here as the source of truth, so the
  rule text doesn't drift out of sync between two copies.
- Test suite for `tests/test_webapp_metrics.py`, matching the house style
  from `tests/test_webapp_security.py`/`tests/test_webapp_demo_scan.py`
  (isolated `LEADS_FILE` fixture, API-key/rate-limit dependency tests, an
  explicit "never a raw email in the response body" assertion, malformed-
  AND wrong-shaped-JSON-line tolerance, label-sanitization/bucket-cap
  coverage, and the `"unknown"` fallback branches).

### Known limitation, deferred rather than fixed

`GET /api/metrics` reads and parses the ENTIRE `leads.jsonl` file on every
single call (`LEADS_FILE.read_text().splitlines()`, no caching, no
pagination) — benchmarked at ~0.9s for 500K lines (~60 MB). Organic growth
alone would take decades to reach that scale at a realistic demo-funnel
capture rate, so this is low-urgency — but since `POST /api/leads` and
`POST /api/demo-scan` are both public and unrate-limited by default
(`LINCHPIN_RATE_LIMIT=0` ships off), a scripted flood of either endpoint
could inflate `leads.jsonl` to that size in hours, not years, making every
subsequent `/api/metrics` call slow (one thread-pool worker tied up per
call, though it doesn't block the event loop directly since the handler
is sync). Not fixed in this PR — a real fix (rotation, a cached/periodic
aggregate instead of read-on-every-call, or enforcing
`LINCHPIN_RATE_LIMIT` at the infra layer) is a reasonable follow-up if
this endpoint ever sees real adversarial traffic, but is disproportionate
scope for "internal tooling" nobody has abused yet. Setting
`LINCHPIN_RATE_LIMIT` in production (already in the launch checklist for
unrelated reasons) mitigates this the same way it mitigates the other
public, unauthenticated endpoints.

## 2026-07-11 — E6 "modo partner / white-label" — MERGED as PR #136, deployed live, verified — this section is now historical

**Read this section first if you're picking up cold.** E6 is code-complete,
adversarially reviewed, all 9 confirmed findings fixed or consciously
documented, full suite green (1804 passed, 3 skipped, ruff clean), and
**PR #136 is open as a draft**. Nothing is blocking except the operator's
explicit "mergea el PR #136" — do not merge it proactively. If the operator
gives that instruction: merge, then deploy to Fly
(`~/.fly/bin/flyctl.exe deploy --app linchpin` from a detached worktree at
`origin/main`) and verify live (check the Odoo addon page's new "For
partners" link resolves at `https://linchpin.fly.dev/paquetes-docs/partner-odoo.md`),
then clean up this worktree/branch, then start E7 (legal templates -
`service-agreement-template.md`/`dpa-lite.md`, per `09_checklist_lanzamiento.md`).

### What's built

A `Branding` block (name/logo/color) that a client's deck can be presented
under instead of Linchpin's own identity — `src/deliverable.py`'s new
`Branding` dataclass + `DEFAULT_BRANDING`, a `ClientProfile.branding` field
(`src/client_profile.py`) that round-trips through save/load like
`warehouse_capacity`, and `run_package(branding=...)` resolution (explicit
arg > `profile.branding` > `DEFAULT_BRANDING`) threaded into
`jobs/package_deliverable.py`'s **consolidated package deck only** —
deliberately scoped the same way E4 scoped `lang`, not into every deck in a
package run. Plus `documentation/paquetes/partner-odoo.md` (the partner
pitch: rev-share 20% vs. white-label flat fee), a new "For partners"
section on the Odoo addon's App Store listing page, and an E6 section in
`09_checklist_lanzamiento.md`.

### The most important thing to understand before extending this: the scope gap

**Only the consolidated package deck gets a partner's branding today.**
Each individual tool's own deck within the same package run (e.g.
`diagnostico/data_quality/deliverable.md`, `diagnostico/abc_xyz/deliverable.md`,
...) still renders Linchpin's `DEFAULT_BRANDING`, unchanged. This was a
deliberate scope decision (mirrors E4's `lang` precedent exactly), but the
adversarial review caught that the FIRST draft of
`documentation/paquetes/partner-odoo.md` overpromised this to a real
paying partner ("el cliente nunca ve 'Linchpin' en ningun documento" — false,
4 of 5 files in a real branded package run still say "Prepared by
Linchpin"). **This is now corrected in the docs** (both `partner-odoo.md`
and the operator checklist explicitly say only the consolidated deck is
branded, and instruct the operator to check every file before handing a
folder to a partner's client) — but the underlying PRODUCT gap is still
open. If a real partner ever pushes back on this ("I'm paying for
white-label and my client is seeing your name"), the fix is threading
`branding` through `run_package()`'s per-step `tool.deliver`/`tool.deck`
calls (`scm_agent/packages.py` lines ~304-308) — touching every one of the
~34 `deck=` lambdas in `scm_agent/tools.py` plus their `jobs/<x>_deliverable.py`
builders. That's real scope, not a quick fix; don't attempt it reactively
mid-partner-onboarding, plan it as its own pass.

Also still open, `primary_color` is stored on `Branding` but **not visually
applied anywhere** — Markdown/XLSX don't render arbitrary text/cell colors
easily, and this was explicitly deferred to "a future richer (HTML/PDF)
renderer" (see `Branding`'s own docstring). The docs now say this
explicitly rather than promising it works.

### Review findings, adjudicated (10 raw, 9 confirmed, 1 refuted)

Full detail is in the fix commit's own message
(`git log --oneline feat/e6-partner-whitelabel` → the "fix: close review
findings..." commit) — read that before touching `Branding.__post_init__`
or the partner docs again, it explains *why*, not just *what changed*.
Short version: fixed a raw-`AttributeError`-instead-of-`ValueError` crash
on a `None` branding name, a regex bug that let a trailing newline slip
past `#RRGGBB` validation, invisible-Unicode-only names bypassing the
required-name check, and an Excel label collision — plus the two doc
overclaims described above. Refuted: a claimed Markdown "image-tag hijack"
via `branding.name` didn't reproduce against 3 real CommonMark parsers (the
narrower "unescaped interpolation can garble formatting" observation is
real but pre-existing across `title`/`client`/finding text too, not unique
to this diff — left as a known limitation, not silently ignored).

## 2026-07-10 — E5 "citation-grounding gate" — reviewed, fixed, PR #134 open (draft) — needs merge go-ahead

**MERGED as PR #134, deployed live, verified — this section is now historical.**

**Read this section first if you're picking up cold.** E5 is code-complete,
adversarially reviewed, all confirmed findings fixed, full suite green
(1780 passed, 3 skipped, ruff clean), and **PR #134 is open as a draft**.
Nothing is blocking except the operator's explicit "mergea el PR #134" —
do not merge it proactively. If the operator gives that instruction: merge,
then deploy to Fly (`~/.fly/bin/flyctl.exe deploy --app linchpin` from a
detached worktree at `origin/main`) and verify live via curl, then clean up
this worktree/branch (`git worktree remove`, PowerShell force-delete
fallback if Windows-locked, `git worktree prune`), then start E6.

An earlier checkpoint of this session (mid-context-handoff) noted the
adversarial review workflow (`wf_cb14d33f-f49` / task `w347mgmu0`) hadn't
finished and couldn't be resumed cross-session. **It turned out to still be
running in the background and completed on its own** — a task-completion
notification arrived carrying the full result in a *later* session/turn, so
it never needed re-running from scratch. Lesson for next time: a workflow
launched via the `Workflow` tool keeps running server-side even if the
session that launched it ends before it finishes; check for a pending
notification before assuming a fresh review is required.

### What the review found and how it was adjudicated

3 dimensions (graph-algorithm correctness in `knowledge.py`; `TOOL_CONCEPTS`
curation quality across a sample of tools; integration/degrade-semantics in
`packages.py`), each finding independently re-verified by a second agent
against the real code and real committed graph (not the diff description).
12 raw findings, 7 confirmed, 5 refuted as having no live behavioral impact
(don't "fix" these if you see them flagged again — they were already
investigated and are working as intended):
- `vehicle_routing`'s anchor imprecision (`route_sheet` is a manufacturing
  routing doc, not vehicle routing) — real, but `vehicle_routing` isn't
  wired into any `PackageSpec` yet, so `citation_gate` never runs for it in
  production today. Worth fixing *before* it's ever added to a package.
- `fefo`'s third anchor (`lot_size`, an EOQ/batch-sizing concept) — its
  entire 2-hop reach is a strict subset of the other two (correct) anchors',
  so it changes zero outcomes; redundant but harmless.
- `sourcing`'s three anchors are about sourcing *location* (make-vs-buy),
  not the supplier-scorecard/TOPSIS ranking the tool implements — but the
  procurement sub-graph is tightly clustered enough that a corrected anchor
  set produces identical keep/omit outcomes on every candidate tested.
- `leadership_chain`'s anchors and the module's "every id verified to
  exist" docstring claim — both independently reconfirmed correct, not
  disputed.
- `filter_citations` hardcodes `graph="books"` instead of reading
  `GroundedCitation.graph` — mechanically true, but `graph="code"` citations
  are structurally unproducible by the current `ground_citations_detailed`
  (it never queries the code graph), so there's no reachable input that
  diverges. A one-line forward-compat nit if `ground_citations_detailed` is
  ever extended to surface code-graph hits — not a defect today.

The 7 confirmed findings (bare-id collision risk in `knowledge.py`;
mismatched `scheduling`/`forecast`/`cycle_count`/`risk` anchors;
`excess_obsolete`'s citation-gate self-validation loophole; `data_quality`'s
accepted coverage gap) are described in the fix commit's own message
(`git log -1 feat/e5-citation-gate` or the PR description) — read that
before touching `citation_gate.py`'s `TOOL_CONCEPTS`/`EXCLUDED_CONCEPTS`
again, it explains *why* each anchor is what it is, not just what changed.

### What's actually built

The gap this closes, straight from the 2.0 protocol: "el grounding actual
es decorativo (el deck demo cita 'Clean Technology'/MPS en data quality)."
Confirmed this was real and is now fixed — see below.

- `scm_agent/knowledge.py`: new `GroundedCitation` dataclass (`text`,
  `node_id`, `graph`); new `ground_citations_detailed()` (same IDF-weighted
  ranking `ground_citations()` always had, but also returns each hit's
  resolved node id — `ground_citations()` is now a thin
  `[c.text for c in ground_citations_detailed(...)]` wrapper, 100%
  backward-compatible, confirmed via `plain == [c.text for c in detailed]`
  in tests). New `node_exists(concept_id, graph="books") -> bool` and
  `concept_distance(from_id, to_id, *, graph="books", max_hops=2) -> int |
  None` (undirected BFS; 0 = same node; both wrap the existing
  `_resolve_node`'s bare-id/namespace tolerance). A precomputed undirected
  adjacency dict is built once in `__init__` from `graph["links"]`
  (skipping low-confidence `INFERRED` edges below `_MIN_INFERRED_CONFIDENCE`,
  mirroring `_detail()`'s existing filter) so repeated per-citation BFS
  calls during a package run don't re-scan all ~3810 edges each time.
- `scm_agent/citation_gate.py` (new): `TOOL_CONCEPTS` — all 37 registered
  tool keys mapped to 1-4 hand-curated, individually-`node_exists()`-verified
  anchor concept ids from `knowledge/scm-books/graph.json` (1953 nodes,
  1847 of them the curated `knowledge::`-namespace taxonomy this map draws
  from). `MIN_CITATIONS=2`, `MAX_HOPS=2`. `filter_citations(kb, tool_key,
  candidates)` keeps a candidate only if its node exists AND is within
  `MAX_HOPS` of at least one of the tool's anchors; if fewer than
  `MIN_CITATIONS` survive, the WHOLE batch degrades to empty (never ships a
  single, weakly-grounded citation). Every omission is both logged
  (`linchpin.citation_gate`, INFO) and returned structurally in
  `GateResult.omitted` — inspectable both ways, per the acceptance
  criterion. A tool absent from `TOOL_CONCEPTS` (shouldn't happen — all 37
  are covered, pinned by `test_every_registered_tool_has_a_concept_map`)
  omits every candidate rather than skipping the check.
- `scm_agent/packages.py::_run_step()`: the ONLY integration point,
  deliberately — the Orchestrator's single-tool path (`webapp/app.py`'s
  `POST /api/jobs`, the MCP server, `examples/run_agent.py`) is untouched
  and still calls the ungated `ground_citations()`. This mirrors E4's own
  hard-learned lesson (don't gate a live production surface nobody asked to
  gate) and matches the protocol's explicit scope: "Intégralo en la fase QA
  del package runner."
- `examples/run_package.py`: new `--verbose` flag (`logging.basicConfig`)
  so an operator running the CLI directly can actually see the omission
  log — tested via **subprocess**, not in-process, specifically because
  `basicConfig()` mutates the root logger process-wide and would leak into
  every other test in the same pytest session otherwise.
- **Verified live** (both via a direct Python run and via `--verbose`
  through the actual CLI): on the Diagnostico demo intake, `data_quality`'s
  ranked candidates (Master Production Schedule, **Clean Technology**,
  ATO-MPS — the exact citations named in the protocol as the bug) are all
  correctly omitted as >2 hops from `step_product_data_standard`, degrading
  that step to zero citations; `abc_xyz`/`excess_obsolete`/`financial_kpis`
  keep their genuinely on-topic citations (post-review-fix content).
- 70+ new/changed tests across `tests/test_citation_gate.py` (unit tests
  against a fake KnowledgeBase, per-anchor connectivity regression tests
  from the review's confirmed findings, plus the protocol's own named
  regression test that "Clean Technology"/MPS never cite again on the
  Diagnostico demo), `tests/test_knowledge.py` (the new public methods +
  the bare-id-collision disambiguation regression), and two `_NoKnowledge`
  test stubs (`test_packages.py`, `test_run_package_cli.py`) that needed a
  `ground_citations_detailed` stub method added or every package test broke.

### A mistake worth knowing about if you touch tests/test_knowledge.py again

An `Edit` call's `old_string` matched only the FIRST of two assertions at
the tail of `test_ground_citations_does_not_surface_leadership_for_an_eoq_brief`
(a `Read` with a truncated `limit` hid the second one), so the insertion
landed the second assertion orphaned inside an unrelated new test function
below it, referencing an undefined `cites` variable. Caught immediately by
running the test file (not by review) — fixed by moving the orphaned
assertion back to its real test. Lesson: when appending near the end of a
file, verify the actual tail with `wc -l` + an untruncated `Read`, not a
`Read` with a `limit` that might cut off content you need to preserve.

E6 (modo partner / white-label, canal Odoo) landed next after this — see
its own section near the top of this file. Next after E6: **E7** (legal
templates — `service-agreement-template.md`/`dpa-lite.md`, both need a real
lawyer's review before use with a paying client, per
`09_checklist_lanzamiento.md`).

## 2026-07-10 — E3 merged into main alongside E4 (real conflicts, resolved by hand)

E3 (`feat/e3-liquidacion`, branched off `origin/main` before E4 existed) and
E4 (`feat/e4-bilingual`, branched off `origin/main` independently, merged
first as **#131**) both touched `examples/run_package.py`,
`src/client_profile.py`, and `tests/test_client_profile.py` — GitHub flagged
E3's PR as `CONFLICTING` once E4 landed. Resolved by hand (both features'
code kept, nothing dropped): `ClientProfile` carries both
`contingent_fee_pct` (E3) and `lang` (E4) fields; `examples/run_package.py`
has both `--fee-pct`/`--fee-floor`/`--measure` (E3) and `--lang` (E4) CLI
flags plus both `_resolve_fee_params()` and `_resolve_lang()` helpers.
**While resolving, applied E4's own adversarial-review fix to
`_resolve_fee_params()` too** — it had the exact same
silently-swallows-a-corrupt-profile.json bug that review caught in
`_resolve_lang()` (see the E4 section below), just not yet fixed since it
predates that finding. Same split-try/except fix applied here for
consistency; add a regression test for it if picking this thread back up
(the E4-side one is `test_corrupt_profile_fails_loudly_instead_of_silently_defaulting`
in `tests/test_run_package_lang_cli.py` — mirror it for `_resolve_fee_params`
in `tests/test_run_package_cli.py`, not yet written as of this merge). Full
suite + ruff re-verified green AFTER the merge, not just before it — see
below.

## 2026-07-10 — E3 "Sprint de Liquidacion" (Oferta #8, precio contingente) shipped

**E1 and E2 closed out first this session:** PR #125 (E1, `/paquetes`) and PR
#128 (E2, `/demo` funnel) both squash-merged and deployed live to
`https://linchpin.fly.dev` — verified end-to-end in the browser/curl, not
just "deploy succeeded" (see the prior entries below for the exact checks).
A concurrent session's PR #126 (MCP tools 8->33) and PR #127 (doc count
refresh) landed on `main` in between — both merged in cleanly with no
conflicts.

**E3 shipped on `feat/e3-liquidacion`:** the 8th commercial package,
**Sprint de Liquidacion** — the only section with **contingent pricing**
(10-20% of cash recovered, floor USD 1,500, never more than what was
actually recovered) instead of a fixed price. New `src/contingent_fee.py`:
`calculate_contingent_fee()` (zero recovery -> zero fee, no floor charged on
nothing recovered; the floor raises a small recovery to a minimum worth
invoicing, but is itself capped at `recovered_cash` so the fee can never
exceed what came back) + `measure_recovery()` for the post-sprint closing
annex (estimated vs. actual recovery per SKU, real fee computed on the real
number). New `LIQUIDACION` `PackageSpec` in `scm_agent/package_specs.py`
(`data_quality`, `excess_obsolete`, `markdown_liquidation` required + `pricing`
optional — reuses the Diagnostico's exact intake, so a client who ran that
first sends nothing new). `ClientProfile.contingent_fee_pct` (new field,
0.10-0.20, deliberately excluded from `as_params()` since no engine `Tool`
reads it — the package CLI reads it directly off the loaded profile).
`examples/run_package.py` gained `--fee-pct`/`--fee-floor`/`--measure`: a
successful `liquidacion` run always writes `estimacion_honorarios.md`
("ESTA ES UNA ESTIMACION, NO UNA FACTURA"); `--measure <post_liquidacion.csv>`
additionally writes `anexo_cierre.md` with the real recovered cash vs. the
estimate and the real fee owed. New one-pager
`documentation/paquetes/sprint-liquidacion.md` + price-table updates across
`MONETIZATION_BRIEF.md`, `documentation/paquetes/README.md`,
`webapp/offers.py` (new 8th `Offer`, appears on `/paquetes` automatically)
and 4 operator docs whose "7 paquetes"/"7 secciones" counts were now stale
(bumped to 8) — `webapp/paquetes_page.py`'s landing copy also updated since
Sprint de Liquidacion breaks the "7 paquetes de alcance fijo" generalization
(it's the one contingent-price section).

**Adversarial review before merge — this one ran clean (15/15 agents, no
session-limit outage this time) and caught one real, consequential bug plus
8 smaller ones, all fixed:**
1. **(HIGH, the important one) price-history intake never reached
   `markdown_liquidation`.** The package spec ran `markdown_liquidation` on
   just the stock CSV; the client's `ventas.csv` (price history) only fed the
   *separate* `pricing` step, never `params['price_history_path']`. On the
   demo intake this meant `n_elasticity=0` (silent fallback to
   salvage/default-markdown heuristics) and `total_recovered=~9,566` instead
   of `n_elasticity=3` and `~50,577` with real elasticity pricing — a >5x
   difference in the exact number the contingent fee is computed from, while
   the one-pager unconditionally promised elasticity pricing "cuando tenés
   historial de precios." Fixed with a new generic
   `PackageStep.extra_input_params: dict[str, str]` hook in
   `scm_agent/packages.py` (`{param_key: slot_name}`, resolved against the
   same intake dir, silently omitted if that slot's file is absent — same
   optional-degrade shape as every other package mechanism) and wired
   `{"price_history_path": "ventas"}` onto the `markdown_liquidation` step.
   Two new regression tests pin both directions (present -> elasticity used;
   absent -> heuristic fallback, unchanged).
2. `measure_recovery()` had no input validation (unlike
   `calculate_contingent_fee`) — a NaN/negative value from a garbled
   `--measure` CSV crashed deep inside an unrelated function with a
   misleading `recovered_cash`-named error. Fixed: validates every value in
   both dicts up front, naming the actual dict + SKU.
3. `_actual_recovery_by_sku` silently coerced unparseable quantity/price
   cells (e.g. Excel's `"1,200"` thousands separator) to `$0` via
   `pd.to_numeric(errors="coerce")` + `sum(skipna=True)` — indistinguishable
   from "the client genuinely sold nothing." Fixed: raises listing the
   affected SKUs instead.
4. `--fee-pct`/`--fee-floor`/`--measure` had no error handling at the CLI
   boundary — an out-of-range `--fee-pct` or malformed `--measure` CSV
   crashed with a raw traceback *after* the full package run (14 files) had
   already succeeded and printed "status: ok." Fixed: `main()` wraps the
   annex-writing call in `try/except (ValueError, OSError)` and prints an
   actionable message — the core deliverables were already safe either way.
5. `ContingentFee.effective_pct`'s docstring claimed it "never exceeds
   fee_pct" — false; the floor is capped at `recovered_cash`, not at
   `fee_pct`, so a small recovery can push the effective rate to 75%+ on a
   10% contract. Fixed the docstring and added a callout in
   `render_fee_estimate`'s floor-applied branch so the client-facing text
   says so too.
6. `examples/run_package.py`'s own module docstring still listed 7 packages,
   the one place in the diff's own touched file that missed the 8th. Fixed.

3 other raised findings were investigated and REFUTED with evidence (not
just dismissed) — see the workflow journal if picking this up: a
`_resolve_fee_params` exception-swallowing claim whose cited scenario never
actually reaches that code path (`load_profile` aborts earlier), an
"empty-lines" `measure_recovery` claim that's the function's documented,
tested contract rather than a bug, and a pandas-version-specific
`groupby(NaN)` claim that didn't reproduce on the installed pandas 3.0.3.

68 new/changed tests total (calculator edge cases: recupero cero, el piso,
límites 10-20%, cap por lo recuperado, NaN/negative rejection; the
price-history wiring regression pair; CLI helpers; package end-to-end +
optional-pricing-skip), full suite green (1559 passed), ruff clean. Verified
live: `--demo --measure <csv>` runs end-to-end with real elasticity pricing
and both annexes read correctly; `/paquetes/sprint-liquidacion` renders.

**E4 (entregables bilingües) already shipped independently** — see the
section directly below; it merged first (**#131**) while this PR was still
open, hence the merge-conflict entry at the top of this file.

**Date:** 2026-07-10 · **Repo:** `esstipi-debug/linchpin` (private) · **Branch:** `feat/e4-bilingual` (E1 **#125** merged + deployed live; E2 **#128** merged + deployed live; E3 open as PR **#129** on `feat/e3-liquidacion`, not yet merged; **#122** audit-evidence, **#123** benchmarks, and a `docs/refresh-stale-counts`-style worktree still open concurrently)
**Purpose:** pick up Linchpin work in a fresh session without re-deriving context.

## 2026-07-10 — E4 "entregables bilingues" (lang es/en) shipped

**Branched off `origin/main` (not off E3),** since E4's core plumbing
(`lang` on `PackageSpec`/`ClientProfile`, `src/i18n.py`) is orthogonal to
E3's `LIQUIDACION` package — no conflict expected when both merge; E3's PR
#129 is still open and unmerged, review it/merge it independently.

**What shipped:** `PackageSpec.lang: str = "es"` (frozen singletons per
package — select a client's language via `dataclasses.replace(spec,
lang="en")`, never by mutating the shared constant) and
`ClientProfile.lang: str = "es"` (validated `"es"`/`"en"`, excluded from
`as_params()` — no engine `Tool` reads it). New `src/i18n.py`: `LABELS`
(the consolidated package deck's own headers/KPI-names/coverage-handoff
text, PLUS `src.deliverable.Deliverable`'s structural scaffolding — section
headers, table columns, Excel sheet names) and `TOOL_TITLES` (all 37
registered `Tool.title` values, translated). `examples/run_package.py`
gained `--lang {es,en}` + `_resolve_lang()` (CLI override > client profile
> "es" default).

**Deliverable.lang defaults to `"en"`**, deliberately — it's the SAME class
every individual tool's own deck uses (`jobs/<x>_job.py::build_deck()`,
~37 files, always English), so defaulting to `"en"` there means NONE of
those ~37 decks changed at all; only `jobs/package_deliverable.py::build()`
(the consolidated package-level deck) passes `lang=spec.lang` explicitly.

**Honest, documented scope boundary** (see `src/i18n.py`'s module
docstring — read it before touching this again): a package deck's
Recommendations/Coverage&handoff sections and each tool's own Finding/
summary prose stay in engine-native English regardless of `lang` — full
per-tool translation is a much larger effort than "two flat dictionaries."
When an `LLMProvider` IS configured, `scm_agent.llm.narrative_rewrite`
rewrites a step's main summary (not its `GuidedOutcome` options text) into
the target language on the fly.

**Adversarial review (2 rounds, 13-15 agents each, both ran clean) caught
real issues both times — the second round specifically caught a production
regression the first round's fixes hadn't introduced yet:**

1. **(HIGH, live-product regression, would NOT have been caught by tests
   alone)** `Orchestrator`'s LLM narrative rewrite — refactored into the new
   shared `scm_agent.llm.narrative_rewrite()` — initially defaulted `lang`
   to `"es"`, which would have silently added an explicit "answer in
   Spanish" instruction to `webapp/app.py`'s `POST /api/jobs`, the live MCP
   server (`webapp/mcp_server.py`), and `examples/run_agent.py` — none of
   which pass `lang` and none of which asked for translation — for any of
   them with a real `ANTHROPIC_API_KEY` configured (the fly.dev deploy has
   one). Fixed: `narrative_rewrite(..., lang: str | None = None)` — `None`
   omits the language clause entirely, reproducing the EXACT pre-E4 prompt
   wording byte-for-byte (pinned in tests). Only the commercial-package
   runner passes a real language (`spec.lang`, defaulting to `"es"`) — that
   path is brand-new behavior with no prior callers to protect.
2. **(HIGH)** `i18n.py`'s own docstring claimed "headers" were covered, but
   `src/deliverable.py`'s `to_markdown()`/`to_excel()` hardcoded every
   section header in English with no `lang` anywhere — verified empirically,
   an "es" deck had Spanish content under literal `## Executive summary`.
   Fixed properly (not just re-documented): `Deliverable` gained `lang`
   (default `"en"`, see above) and ~35 new `i18n.LABELS` entries for every
   header/column/sheet name; `package_deliverable.py` passes it through.
3. **(HIGH, documented not fixed)** Recommendations and Coverage&handoff
   lines unconditionally glue a translated tool title to raw-English
   `GuidedOutcome` text (`scm_agent/tool_options.py`) — same scale problem
   as the per-tool Finding prose, now explicitly named in `i18n.py`'s
   docstring instead of being an undisclosed third leak.
4. **(LOW)** `TOOL_TITLES["whatif"]["es"]` was an awkward calque
   ("Que-Pasa-Si") — fixed to keep "What-If" as a loanword, matching
   `sourcing`/`ddmrp`/`slotting` etc. already doing the same in this dict.
5. **(MEDIUM)** `_resolve_lang()` silently swallowed a corrupt
   `profile.json` (defaulting to `"es"` with zero diagnostic) instead of
   failing loudly like every other profile reader in this codebase
   (`orchestrator.py`, `packages.py::_load_profile`). Fixed: only an
   unslugifiable client label degrades to the default now; a genuinely
   corrupt file raises.

44 new/changed tests (bilingual snapshot test for the consolidated
`.md` AND `.xlsx`, exact-prompt-wording pins for the Orchestrator
regression fix, i18n dict-completeness checks, corrupt-profile handling),
full suite green (1660 passed), ruff clean. Verified live: ran
`DIAGNOSTICO` end-to-end in both languages and read the full consolidated
deck — headers/KPIs/data-sources/coverage all correctly bilingual, the
documented English carve-outs (findings prose, recommendations, guided-
outcome text) present exactly where expected and nowhere else.

**Still pending from E4's own spec, deliberately NOT done in this PR ("PR
aparte" per the 2.0 protocol):** migrate the 7 existing one-pagers
(`documentation/paquetes/*.md`) off informal "tu"-conjugated verbs (not
literal Rioplatense "vos" — verified none of the 7 files actually use
`tenés`/`querés`/`podés` forms, they use `tú`-conjugated `tienes`/`quieres`/
`puedes`, ~36 instances across 7 files) toward the impersonal/imperative
phrasing this session's own new copy (E1-E3) already established, without
touching any price or scope. This is a subjective brand-voice call on
already-shipped, client-facing sales copy — flag it for the next session
rather than guessing unilaterally.

**Next: E5 (compuerta de citation-grounding).** `scm_agent/citation_gate.py`
resolving each candidate L3 citation against `knowledge/scm-books/graph.json`
(≤2 hops from the tool's static concept map), degrading a section to
"sin citas" below 2 resolved citations rather than inventing a replacement.
Full acceptance criteria in the Linchpin 2.0 protocol.

---

## 2026-07-10 — E2 "funnel demo -> mini-reporte" shipped (same session as E1's merge + deploy)

**E1 closed out first:** PR #125 squash-merged to `main` (`00e6ba6`) and
deployed to Fly the same day — `https://linchpin.fly.dev/paquetes` verified
live (all 7 one-pager slugs 200, landing CTAs present, 14 CTAs degrading to
`mailto:` until the operator sets `CALENDLY_URL`/`STRIPE_LINK_*`, see
`documentation/operator/07_setup_venta.md`).

**E2 shipped on `feat/e2-demo-funnel`:** `/demo` no longer returns reorder
points — it now sells what the Diagnostico opens with. New
`webapp/demo_scan.py`: ONE stock CSV (`product_id, on_hand, daily_demand`
[+ `unit_cost`, `days_since_last_sale`] — same shape as the acquisition
playbook's free-scan template on the unmerged
`feat/client-acquisition-playbook` branch, deliberately compatible) →
reuses the three existing jobs' `prepare/run/verify` AS-IS (no delivery
phase): `excess_obsolete` directly, `abc_xyz` via one annualized demand
point per SKU (XYZ is degenerate on a snapshot — documented in the module;
only the ABC axis is quoted), `financial_kpis` via run-rate COGS + on-hand
inventory value (DIO/turns follow). New `POST /api/demo-scan` (public like
`/api/leads`, rate-limited, upload controls copied from `/api/jobs` —
25 MB/413, basename pinning, isolated tempdir, TTL purge — SECURITY.md
threat model now lists it as the 4th untrusted input). Headline: "$X
atrapados en stock muerto/excedente · A-items concentran Y% · DIO Z dias" +
3 hallazgos ejecutivos + CTA a `/paquetes/diagnostico-arranque`. The QA
gate holds at scan level: any of the three `verify()`s failing (or a
non-finite headline number, e.g. all-zero demand → DIO=inf, or missing
`unit_cost` → zero inventory value) ⇒ `qa_failed`, NO artifact written,
honest message in the UI. On QA pass it persists per lead:
`deliverables/leads/<safe-email>/mini_report.md` + `followup_email_draft.md`
(a DRAFT — mail is NEVER sent automatically) — email → dirname via
`safe_lead_dirname()` (traversal-proof, `_at_` for `@`, plus a short hash
of the full normalized email so two distinct addresses can never collide
into the same directory — an earlier version without the hash suffix DID
collide, e.g. `user+test@gmail.com` vs `user_test@gmail.com`; caught by an
adversarial review before merge), root overridable via
`LINCHPIN_LEAD_REPORTS_DIR` (on Fly set it to a path on the `/data`
volume or artifacts die with each deploy). A telemetry line is ALWAYS
appended to `leads.jsonl` (`source: "demo-scan"`, dataset, status,
headline-or-null) — deliberately including `qa_failed` runs, so E8's
`/api/metrics` can count demos-run vs demos-converted later. New tracked
sample `data/sample_stock_snapshot.csv` (8 SKUs, same rows as the free-scan
demo) + downloadable `webapp/static/demo/plantilla_stock.csv`; the demo UI
was rewritten in neutral Spanish (no voseo, per the 2.0 protocol's copy
rule) around the money headline + CTA. The raw upload is never copied into
the lead folder (privacy: derived teaser persists, raw data purges).

**Adversarial review before merge, worth reading if the pattern repeats:**
the review workflow's verify phase hit the session's usage-limit reset
mid-run — 11 of 13 agents errored (`session limit · resets 1:50pm`), so the
tool's own `confirmed: []` output was NOT a clean pass, it was an infra
outage (same failure shape as [[workflow-verify-phase-failure-not-clean]]
from a prior session). Manually adjudicated all 9 raw findings instead of
trusting the empty list. 2 were real code bugs, fixed: (1) `safe_lead_dirname`
collisions (above); (2) attacker-controlled `product_id` landing unescaped
in the persisted `.md` artifacts (`webapp/demo_scan.py::_md_safe` now
collapses it to a conservative charset before embedding — the repo's
existing `defuse_formula()` only covers CSV/Excel formula injection, not
markdown/HTML). 1 was a real HIGH-severity gap addressed with a bounded
mitigation, not a full fix: `/api/demo-scan` is unauthenticated with
`LINCHPIN_RATE_LIMIT` off by default, so an unbounded lead store is a
scriptable disk-exhaustion vector on the small Fly volume — added
`_prune_excess_lead_dirs()` (oldest-evicted count cap, `MAX_LEAD_DIRS=5000`)
as defense-in-depth and flagged setting the real rate limit in the launch
checklist; a full fix (auth, CAPTCHA, per-IP quota) was judged
disproportionate for a deliberately-public lead magnet. The remaining 6
were test-coverage gaps, not code bugs — closed by adding tests, not by
changing behavior (rate-limit regression test, re-scan-same-email overwrite
semantics, non-CSV upload stays a 400 not a 500, duplicate-`product_id`
row-summing pinned to match every other job's existing behavior, the
`LINCHPIN_LEAD_REPORTS_DIR` env var exercised via a real subprocess import
instead of only via monkeypatch, and an explicit assertion that the raw
upload is never copied into the lead folder).

**Next: E3 (Oferta #8 "Sprint de Liquidación").** `markdown_liquidation`
exists as a registered tool (PR #124) but belongs to no package. E3 = new
`LIQUIDACION` PackageSpec (data_quality, excess_obsolete,
markdown_liquidation, pricing opcional) + `src/contingent_fee.py` (10-20%
of recovered cash, floor default $1,500) + `--measure` mode + one-pager
`documentation/paquetes/sprint-liquidacion.md`. Full acceptance criteria in
the Linchpin 2.0 protocol.

## 2026-07-09 — Linchpin 2.0 kicks off: E1 "superficie de venta" shipped (`/paquetes`)

Started the "Linchpin 2.0" build protocol (documented in the operator's own
prompt, not committed to the repo) — its governing principle: 1.x already
solved the *offer* (35 tools, 7 QA-gated commercial packages, a live MCP
server); 2.0 is explicitly **not** more engine capability, it's "the version
that can charge". Its own Paso 0 says detect state from the code, not notes,
and do exactly one épica per session, in order E1->E8. Verified in code (not
assumed): none of E1-E8 existed yet (`webapp/app.py` had no `/paquetes` route,
`scm_agent/package_specs.py` had no `LIQUIDACION`, no `src/i18n.py`, no
`scm_agent/citation_gate.py`, `client_profile` had no `branding`, no
`documentation/legal/`, no `PIPELINE.md`/`GET /api/metrics`) — so E1 was next.

**E1 shipped, branch `feat/e1-sales-surface` (this session, not yet merged/PR'd
at the point of this handoff edit — see the PR link once opened).** New:
`webapp/offers.py` (the 7 official packages' price/cadence/scope, extracted
once from `documentation/MONETIZATION_BRIEF.md`'s pricing table — never
duplicated as prose; per-offer `STRIPE_LINK_<SLUG>` env var naming +
`CALENDLY_URL` CTA resolution, both degrading cleanly to a `mailto:` with a
prefilled subject when unset), `webapp/operator_profile.py` (the "Quien firma"
block, `OPERATOR_*` env vars, `TODO-OPERADOR` placeholders), `webapp/
paquetes_page.py` (server-rendered `/paquetes` grid + `/paquetes/{slug}`
one-pager shell that fetches the real `documentation/paquetes/*.md` client-side
via the already-vendored `marked.min.js` — same proven pattern as the existing
`/operator` page, no new Python markdown dependency). Landing `/` got a compact
marketing hero (1-line value prop, 3 guarantee chips: QA-gate / L3 citations /
safe writeback, CTAs to `/demo` and `/paquetes`) prepended above the existing
interactive dashboard — the dashboard itself was NOT removed or relocated.
New doc `documentation/operator/07_setup_venta.md` (exact Stripe Payment Link
+ Calendly + `fly secrets set` steps, wired into the `/operator` portfolio
nav) and `documentation/operator/09_checklist_lanzamiento.md` (the running
human-action checklist the closing protocol asks for).

**A real bug caught by actually running the page in a browser, not just
tests:** the first version of `paquetes_page.py`'s inline `<script>` had a
stray extra `}` from an f-string brace-escaping slip (`}}).then(...)` where
only one line of that block was genuinely an f-string needing `{{`/`}}`
escaping — every other line was a plain string where doubled braces stay
literal). This produced syntactically invalid JS that silently broke the
`fetch().then()` chain; the one-pager page loaded fine but stayed stuck on
"Cargando..." forever, with no console error surfaced by the substring-only
tests that existed at that point. Caught by loading `/paquetes/starter-
fundamentos` in the browser preview and inspecting `#content.innerHTML`
directly. Fixed, and a regression test now asserts brace-balance in the
generated script (`test_offer_page_inline_script_has_balanced_braces`).
Independent `code-reviewer` agent pass on the full diff (not just this bug)
also caught two real, if low-severity, gaps: a test-isolation leak
(`OPERATOR_NAME`/`OPERATOR_BIO` weren't cleared in one route-level "degrades
without env vars" test, making it order-dependent on ambient shell state) and
a missing URL-scheme allowlist (`CALENDLY_URL`/`STRIPE_LINK_*`/
`OPERATOR_LINKEDIN`/`OPERATOR_PHOTO_URL` were escaped against HTML injection
but not against a `javascript:` URI landing in a rendered `href`/`src`) — both
fixed (`webapp/offers.py::is_safe_external_url` /
`is_safe_same_origin_or_external_url`), both re-reviewed clean. Full suite
1457 passed (16 skipped), `ruff check src tests examples webapp` clean.
Verified live in the browser preview at both desktop and mobile (375px)
widths, `/`, `/paquetes`, `/paquetes/starter-fundamentos`, and the new `/
operator#venta` doc — no console errors.

**Concurrent-session note (verified before starting, not assumed):** this
session found the working tree already on an unrelated, unpushed branch
(`feat/client-acquisition-playbook`, 3 commits ahead of main, a lead-magnet +
acquisition-playbook effort — not part of Linchpin 2.0) plus two other
in-progress worktrees for open PRs **#122** (audit-evidence engine core) and
**#123** (old-method-vs-Linchpin benchmarks). E1 was deliberately branched
fresh off `origin/main` into a new sibling worktree (`.wt-e1-sales`) rather
than building on top of that dirty branch, per the standing rule in §5 below
about concurrent sessions — none of that other work was touched.

**Next: E2 (funnel demo -> mini-reporte).** The current `/demo` already
captures an email lead (`POST /api/leads` -> `leads.jsonl`) but returns
reorder points, not a sales-oriented result. E2 asks for it to run
`excess_obsolete` + `abc_xyz` + `financial_kpis` instead and show a dollar
figure of trapped/excess stock + 3 executive findings + a CTA straight to
`/paquetes/diagnostico-arranque`, persisting a mini-report + a draft
follow-up email per lead under `deliverables/leads/<email>/` (never send mail
automatically). See the full acceptance criteria in the Linchpin 2.0 protocol
if picking this up fresh.

## 2026-07-08 — L3 graph gets a 25th source (AI-in-SC, 10/20 chapters, $0.11) + a researched next-level roadmap

**What actually shipped, committed on `feat/l3-cohen-dai-ai-in-supply-chains` (2f5574f), not yet merged:**
`knowledge/scm-books/` grew from 24 to **25 sources** — Cohen & Dai (eds.), *AI in Supply
Chains: Perspectives from Global Thought Leaders* (Springer, 2026). This is the first source
that's about *AI applied to SCM* rather than classical OR/inventory/forecasting/pricing theory
— all 24 prior sources predate the LLM-agent era. Springer's edition is paywalled per-chapter
(~$30 each, not Open Access), so only the 10/20 chapters with a legitimate free preprint
(author self-archive on SSRN/arXiv or an institutional repo — verified against SSRN,
ResearchGate, Google Scholar, and every author's faculty page; no shadow-library sources) got
extracted: Cachon's "AI's impact so far has been modest" framing essay, Tang's AI-risk-mgmt
survey, Fransoo/Peels/Udenio + Netessine/Shunko on the semiconductor/data-center supply chain
*behind* AI, Simchi-Levi et al. on LLMs for SC decisions, Hu & Liu on coupling AI with OM
theory, Gijsbrechts/Boute/Van Mieghem/Zhang on DRL for inventory (real Alibaba Tmall
deployment), Raman & Kwon on AI in retail labor scheduling, Lee/Shen/Qi/Chen's JD.com case
study, and Tayur's skeptical closer. Graph: 1847→1953 nodes, 3670→3810 edges (clean additive
merge, verified no loss), 123 communities incl. 7 new (AI Supply Chain Research, LLM
Operations & Planning, Reinforcement Learning Tools, AI Hardware & Infrastructure, ...).
Extraction cost: **$0.11** (Kimi backend, 35k in / 18k out tokens). Full provenance + the 8
skipped chapters (no free version exists anywhere — verified) + 2 deliberately-not-ingested
adjacent sources (an HBR reprint, a Duke Fuqua plain-language adaptation — different
register/venue, not the verbatim chapter) are documented in `knowledge/scm-books/README.md`.

**Gotcha worth knowing before the next `graphify extract` on this repo:** `.gitignore` has
`knowledge/scm-books-rebuild/` (the staging dir for new-book raw PDFs + their intermediate
`graphify-out/`), and `graphify`'s own file scanner **respects `.gitignore`** (falls back to it
when no `.graphifyignore` exists, per `graphify/detect.py::_load_graphifyignore`) — pointing
`graphify extract` at anything under that path silently finds "0 papers", no error. The
established convention (matching `scripts/rebuild_l3_graph.ps1`) is source PDFs live **outside
the repo** at `C:\Users\Gamer\Documents\scm-books-corpus\` (now includes a
`cohen-dai-ai-in-supply-chains/` subfolder with the 9 chapter PDFs) — extract from there, write
`--out` into the gitignored rebuild staging dir, then `merge-graphs` + `cluster-only` into the
committed flat `knowledge/scm-books/{graph.json,graph.html,GRAPH_REPORT.md}`. Also:
`graphify cluster-only <path> --graph <custom-path>/graph.json` writes its output to
`<path>/graphify-out/` (its own default), **not** back to wherever `--graph` pointed — copy the
3 files over manually and delete the stray `graphify-out/` afterward if your committed layout
is flat (no `graphify-out/` subfolder) like `knowledge/scm-books/` is. Also needed
`export MOONSHOT_API_KEY=$(...)` manually in every Bash call — the CLI does not auto-load the
repo's `.env`, only the PowerShell rebuild scripts do that.

**Then: a 6-thread parallel research pass (~502k tokens, all findings below are cited)** to
answer three questions — how to level up the L3 graph, what's stale in the planned
integrations, and what cross-functional (non-SCM-department) tooling is actually worth
building next. Findings, ranked by actionability:

1. **Best next graph source, and it's free:** SSRN #5792542, *"Supply Chain Management in the
   AI Era: A Vision Statement from the Operations Management Community"* (Cohen, Dai, Perakis,
   Agrawal, Allon, Boute, Cachon, Cristian, de Véricourt, Harsha, Keskinocak, Miller, Olsen,
   A. Robinson + ~28 more) — https://papers.ssrn.com/sol3/papers.cfm?abstract_id=5792542. It's
   co-authored by literally all 8 authors of the chapters skipped this session (Allon, Miller,
   Perakis/Harsha/Cristian, de Véricourt, Olsen, Robinson), condensing their arguments into one
   freely-downloadable synthesis paper — ingesting this substantially closes the gap left by
   the 8 paywalled chapters without paying Springer. Also flagged as strong candidates:
   Gartner "AI is Not Driving Supply Chain Operating Model Transformation" (May 2026, 140
   leaders surveyed, only 17% pursuing transformational AI redesign) + Gartner "Top Supply
   Chain Technology Trends for 2026" (June 2026); GEP "Supply Chain AI Readiness Report" (2026,
   180 execs, only 4% at scale in procurement despite 90% piloting — directly substitutes for
   the paywalled Robinson "AI readiness" chapter's territory); McKinsey's 2026 procurement/
   agentic-AI and gen-AI-supply-chain pieces (concrete case data, e.g. $30-35M savings on $2M
   spend); 4 new 2026 arXiv papers on agentic AI for inventory (AIM-Bench, "Reliability and
   Effectiveness of Autonomous AI Agents in SCM", "AI Agents for Inventory Control:
   Human-LLM-OR Complementarity"). Full citations in the research log; not yet ingested.

2. **GraphRAG technique that's directly applicable — citation grounding as a pre-publish
   gate.** 2025-2026 work (arXiv 2606.00898, legal domain) treats a knowledge graph as a
   *verification oracle*, not just a retrieval source: every generated citation gets checked
   against the graph on existence/relevance/temporality before publishing (13-21% raw
   hallucination rate found in that domain without it). For Linchpin specifically — a
   deliverable-producing agent, not a chatbot — the actionable version is a QA-gate step that
   resolves every L3 citation in a generated deliverable to an actual `path()`/`affected()`
   result in `graph.json` before it ships, same shape as the existing traversal API, run as a
   post-hoc verifier. Two more findings, lower priority: (a) a thin embedding layer *only* at
   the query-entry step (map a fuzzy user question to the nearest concept node before BFS/DFS)
   — not a parallel vector-retrieval path, since the graph's INFERRED semantic-similarity edges
   already approximate what embeddings buy; (b) structural staleness (`valid_from`/`valid_to` +
   `superseded_by` on EXTRACTED edges, keyed on subject+relation) beats recency-heuristic
   freshness scoring for incremental updates without full re-extraction.

3. **A genuinely new, well-sourced capability idea: `labor_demand_bridge`.** Prompted directly
   by the Raman & Kwon chapter just ingested. Retail/warehouse labor scheduling is structurally
   a **newsvendor problem** (same over/under-staffing cost tradeoff as safety stock), not a
   queueing problem the way call centers are — the real gap is converting a demand forecast
   into an hourly headcount requirement via a labor-standard buffer (extending
   `safety_stock.py`'s z-score machinery, not `scheduling.py`'s fixed-job combinatorics —
   Johnson's rule / Hungarian assignment / dispatching rules have no demand-uncertainty
   dimension). Two open-source libraries are direct wrap targets, not build-from-scratch:
   **`pyworkforce`** (ErlangC/A/B + `MinRequiredResources`/`MinHoursRoster`, built on Google
   OR-Tools CP-SAT) and OR-Tools' own reference `shift_scheduling_sat.py`. Neither is in
   `requirements*.txt` today. Canonical methodology: Buffa, Cosgrove & Luce (1976, *Decision
   Sciences* 7(4)) for the 4-stage forecast→requirement→coverage→assignment pipeline (still
   what Kronos/UKG/Reflexis run); Borst, Mandelbaum & Reiman (2004, *Operations Research* 52(1))
   for the continuous-arrival staffing analogue. This is the first concrete "SCM ↔ HR" bridge
   idea for the project — nothing HR-adjacent exists in the repo today.

4. **Competitive check — the SMB cross-functional bridge is still a real gap, not
   commoditized.** At enterprise tier, "SCM↔finance↔HR bridge" is now standard vendor
   messaging, not a differentiator (Oracle shipped agentic apps spanning finance/SC/HR/CRM in
   Apr/Jun 2026; SAP IBP, Kinaxis Maestro, o9 all claim it). But at SMB/DTC tier the market is
   still fragmented point-solutions nobody has unified: Cogsy/Prediko do inventory-forecast-
   tied-to-cash-flow; Float/Fathom/Pulse do accounting-synced cash forecasting but aren't
   inventory-native; Settle/Wayflyer handle the financing gap; no inventory-optimization vendor
   markets native labor scheduling and vice versa. **This directly validates going ahead with
   the already-fully-researched, still-unbuilt finance/marketing bridge** — see
   `documentation/FINANCE_MARKETING_BRIDGE.md` (dated, still accurate): §1 `rolling_cash_forecast()`
   (13-week direct-method TWCF, needs a date field added to `PurchaseOrder`/`POLine` first —
   real gap, not yet a blocker), §2 markdown+E&O crossover (`markdown_price()` in
   `src/pricing.py` already exists, tested, but **nothing calls it** — not even `pricing.py`'s
   own job; `classify_excess_obsolete()` also already exists; the gap is purely a
   `jobs/markdown_liquidation_job.py` that crosses them), §3 `sop.py` gets an optional
   promo-calendar demand-adjustment param. **Priority order per that doc: markdown+E&O first
   (~1 day, both engines already tested), cash forecast second, S&OP promo-input third.** None
   of the 3 have been built — confirmed this session (`ls src/cash_forecast.py
   jobs/markdown_liquidation_job.py` etc. all 404).

5. **API/library currency corrections for `CAPABILITY_EXPANSION_PLAN.md` §2.7/§2.8** (Shopify +
   Amazon SP-API + ERP/accounting connectors, still 0% built — no Shopify/Amazon/QuickBooks/
   Xero/NetSuite client code exists, only `src/connectors/odoo.py` + `excel.py`) before anyone
   starts building against the June-22 pinned versions:
   - Shopify: plan pins `2026-04`, current stable is `2026-07` — one cycle behind, not yet
     deprecated (Shopify supports each version ~12mo), but re-pin before building.
   - Amazon SP-API: `python-amazon-sp-api==2.1.8` still current. **If any FBA-inventory code
     references `GET_FBA_FULFILLMENT_CURRENT_INVENTORY_DATA`-style report types, those were
     deprecated 2022/removed 2023** — use `GET_LEDGER_SUMMARY_VIEW_DATA`/
     `GET_LEDGER_DETAIL_VIEW_DATA` instead. Amazon's threatened $1,400/yr SP-API subscription
     fee was announced Nov 2025, delayed, paused, then **cancelled outright May 12 2026** — no
     fee currently, but don't treat "free" as permanent in pricing models.
   - QuickBooks (`python-quickbooks==0.9.12`, `intuit-oauth==1.2.6`): both stale (no 2026
     release) but functional; ensure `minorversion=69+` on requests (Intuit deprecated
     minor versions 1-74 as of Aug 2025).
   - Xero (`xero-python==14.0.0`, current): **breaking auth change** — apps created after
     Mar 2 2026 must request 10 granular OAuth2 scopes instead of the old 2 broad ones; existing
     apps got granular scopes auto-assigned by end of Apr 2026 but need updated authorization
     URLs + user re-consent (old broad scopes still work until Sep 2027). Missing scope → 401
     `insufficient_scope`.
   - NetSuite: SOAP is being sunset (2025.2 was the last SOAP release); bigger deadline —
     **Token-Based Auth can no longer create new SuiteTalk integrations starting NetSuite
     2027.1** — plan OAuth2 from the start, don't build new TBA integrations.

6. **Writeback safety design validated against 2025-2026 best practice, not behind it.** The
   existing dry-run/staged-changeset + risk-tier + idempotency-key + TTL-approval +
   audit/rollback design (`src/writeback.py`) maps closely onto the emerging consensus (OWASP
   Agentic AI Top 10 Dec 2025, the OpenPort Protocol's idempotency-key pattern, LangGraph's
   `interrupt_before` gold-standard pattern, a widely-cited four-tier read/reversible/
   external-facing/irreversible risk model). No architectural change indicated. Tangential but
   worth knowing: **Claude Agent SDK flipped its default permission mode from Auto to Manual as
   of v2.1.200 (2026-07-03)** after telemetry showed ~93% of Auto-mode approvals were reflexive
   — if anything in `scm_agent/` assumes SDK auto-approval behavior, re-check against current
   SDK docs.

**The honest tension to flag for whoever picks this up:** all of the above is capability R&D.
`documentation/MONETIZATION_BRIEF.md`'s own "Qué hacer primero" section is explicit that the
30/90-day priority is landing the first 1-2 paying clients via the 7 already-executable
commercial packages, **not** more engine work — "No invertir en SaaS self-serve ni esperar
ingresos del MCP todavía" applies just as much to more capability-building. Treat this handoff
as the answer to "what's the highest-leverage engine work if/when there's a session dedicated
to capability instead of GTM," not as a redirection away from selling. If picking this up,
sanity-check `GTM_SUBMISSIONS.md` and whether a client has landed since 2026-07-06 first — if
one has, prioritize concrete client-requested capability over this roadmap.

**Also noticed, not investigated:** a `linchpin-llamafactory-sft` skill surfaced in
`.claude/skills/` this session (LlamaFactory SFT fine-tuning workflow). [[linchpin-finetuning-verdict]]
(prior-session memory) already concluded LlamaFactory SFT is the wrong tool for "better at
SCM" — it breaks the auditability moat that's the actual product differentiator, and the
`LlamaFactory/` directory at the repo's parent level has no CUDA-capable GPU to run on (AMD
780M). If a fresh session is asked to use that skill, re-read the prior verdict first rather
than assuming the skill's existence means it's been re-validated.

---

**Same-day update — all 7 commercial packages are now executable (PR #116).**
PR #116 (`1cef336`) built the 4 sections deferred by #114: **Scale** ($7.5k/mo,
the full 35-tool catalog) → **Retainer Ejecutivo Fraccional** ($9-12k/mo, the
SAME 35 tools as Scale — the brief is explicit the difference is governance/
cadence, not capability, so `RETAINER_EJECUTIVO` reuses `SCALE`'s step list
verbatim) → 2 one-off projects, **Proyecto de Red/Almacen/Operacion** ($8-18k,
6 tools) and **Proyecto de Sourcing** ($5-10k, 3 tools, reuses Growth's intake
slots). 9 previously-unused tools got mapped into the package runner: sourcing/
landed_cost/acceptance_sampling were already known from Growth; facility_location,
transportation, warehouse_layout, slotting, queuing, scheduling, sop,
earned_value, leadership_chain were new research. Two needed a new mechanism:
`leadership_chain` doesn't take a CSV at all — it reads `params["scores"]` — so
`PackageStep` gained a `params_from_input` hook that converts a one-row
`liderazgo.csv` (C/H/A/I/N, each 0-4) into that override, with its own
validation (missing column / out-of-range value) so a malformed file surfaces
an operator-actionable message instead of a raw `KeyError`. `warehouse_layout`
is purely generative/parametric (no CSV at all, `generate_layout(dict)`) — it
gets `input_slot=None` like `odoo_replenishment`, with site/building/rack
dimensions living directly in `PackageStep.params`.

**Adversarial review caught 2 real demo-data bugs, both fixed before merging**:
a workflow-based review (4 dimensions, 8 raw findings) hit a session rate limit
during its verify phase — every skeptic call failed, so the workflow returned
`confirmed: []`, which is NOT a "clean bill of health," it's an infra outage.
Manually re-verified all 8 findings directly. Two were real: the slotting demo
generator (`_demo_lineas_pedido`) re-rolled its target basket size on *every*
iteration of a while-loop instead of once, and iterated a Python `set` to build
CSV rows — a set's iteration order for strings depends on `PYTHONHASHSEED`,
randomized per process by default, so `--demo` produced the same *content* but
non-reproducible *row order* across separate runs. Fixed (target size rolled
once; `list` instead of `set`) and verified by running `--demo` in two separate
Python processes and diffing the resulting CSV byte-for-byte. Lesson: when a
workflow's verify phase fails outright (not "refuted", actually errors), don't
trust an empty `confirmed` list — check the raw finding count and manually
adjudicate. 10 new tests (37 total for packages), full suite green (1369
passed), ruff clean. Demo verified end-to-end: Scale 35/35, Retainer 35/35,
Proyecto Red/Almacen 6/6, Proyecto Sourcing 3/3, all QA-approved.

All 7 sections of the "Estructura de empaquetado comercial" are now sellable
one-pagers (`documentation/paquetes/`) backed by a real, QA-gated runner — this
was the user's explicit ask this session ("seguir construyendo: las 4 secciones
restantes de la escalera con el mismo runner"), not a speculative extension.

**Earlier the same day — the monetization brief landed and its first 3 commercial packages went executable (PR #113 + PR #114).**
PR #113 (`4eaa018`) merged `documentation/MONETIZATION_BRIEF.md`: a deep-research
report (~50 search agents + 3-vote adversarial verification) concluding the
fastest defensible path to >= USD 8,000/month for a solo operator is the
**productized/fractional inventory service**, not SaaS self-serve or the MCP
server. It also settled, via a 3-judge panel (fixed tiers 40.3/50 vs. a
consulting-ladder 37.7 vs. a la carte 32.3), the **"Estructura de empaquetado
comercial"**: 7 fixed-scope sections sold separately, no section ever sells a
single tool — Diagnostico de Arranque ($1.5-2.5k unico) -> Starter ($2k/mo) ->
Growth ($4k/mo+QBR) -> Scale ($7.5k/mo) -> Retainer Ejecutivo ($9-12k/mo), plus
2 one-off projects (network/warehouse, sourcing/landed-cost). Shortest path to
$8k/mo: **2 Growth clients**. See [[linchpin-monetization-plan]] for the full
plan and [[linchpin-project]] for the underlying product.

PR #114 (`df0f835`, same day) turned the **first 3 sections** (Diagnostico,
Starter, Growth — the other 4 deferred on purpose) from a price table into
runnable deliverables: `scm_agent/packages.py` (a two-phase package runner that
reuses each registered `Tool`'s existing `prepare/run/qa/deliver` callbacks —
no job logic duplicated) + `scm_agent/package_specs.py` (the 3 specs: 4/8/26
tools respectively) + `jobs/package_deliverable.py` (consolidated deck) +
`examples/run_package.py` (`--demo` runs Growth's 26/26 tools end-to-end in
~12s; `--checklist` prints the exact client intake checklist per package) +
Spanish sales one-pagers in `documentation/paquetes/` + runbook RB-9. The
per-tool "QA fails => no deliverable" guarantee now holds at PACKAGE level
structurally: phase 1 computes every step's prepare/run/qa with zero writes;
phase 2 writes the per-tool deliverables + the consolidated deck only if every
EXECUTED step (required or optional) passed QA — one failing step means zero
files written, full stop. A derive-step gotcha worth knowing: `cycle_count`
cannot take the raw sales CSV directly (its value-derivation dict comprehension
overwrites duplicate SKU rows instead of summing them), so the package derives
its input from the already-computed `abc_xyz` classification instead — the
client only ever sends one sales file. 17 new tests, full suite green (1359
passed), ruff clean. Also fixed in the same effort: the brief said "34 tools
del catalogo completo" but the registry (and this doc's own tool count) has
**35** — the brief's own arithmetic (26 Growth + 9 Scale-only) already implied
35; corrected on the PR #113 branch before merging.

**Still not done:** the 5 GTM directory/store listings from `GTM_SUBMISSIONS.md`
are still pure operator-login actions (account creation/OAuth an agent
shouldn't do on the user's behalf), unchanged — that and actually landing the
first paying client are the real next steps, not more package engineering.

**Same-day update (2026-07-03), after the MCP fix below:** the Odoo Store module
(`odoo_addon/linchpin_dry_run/`) shipped (PR #103) - built, adversarially
reviewed (6 real findings fixed: SSRF, error-leak, TransientModel data loss,
overbroad access group, inaccurate data-sent disclosure, documented plaintext
key storage), Docker-install-verified against real Odoo 17, and **live UI
click-through tested in a real browser** (user authorized full browser
control) - settings, group-gated menu, and a real request to production
Linchpin all confirmed working, including the auth-rejection error path
staying clean (no leaked response body). See [[linchpin-odoo-store-module]]
for the full account. Also: tried to actually list the MCP server on
Glama/Smithery/PulseMCP directly in a browser - all three gate behind account
creation/login, which is out of scope for an agent to do on the operator's
behalf. Prepared `server.json` (PR #104) for the **official MCP registry**
instead, which all three read from - the operator still needs to run
`mcp-publisher login github` + `mcp-publisher publish` themselves (~2 min,
real GitHub OAuth, can't be done by an agent). See [[linchpin-monetization-plan]]
§5 for the full finding. Also created `case-studies/UPWORK_FIVERR_PROFILE.md`
on an unmerged branch (`content/upwork-fiverr-positioning`, pushed, no PR yet -
draft for the user to review) - Upwork/Fiverr gig-landing positioning, service
packages mapped to the real tool registry, pricing anchored to the researched
comparables.

**Same-day, also: the code graph (`graphify-out/`) was refreshed** via
`/graphify --update` after 336 of 457 files had changed since the last build
(2026-07-02). Caught and fixed a real bug in the process, worth knowing before
running `--update` again on a large batch of changed files: `build_merge()`'s
`prune_sources` param already auto-replaces re-extracted files' stale nodes
internally (matching by `source_file`) in the installed graphify version
(0.9.5) - passing `changed` files into `prune_sources` too (as the skill's own
`references/update.md` instructs, written for an older version) double-prunes
and strips the freshly-inserted nodes as well, silently collapsing the graph.
Caught by graphify's own shrink-safety check before anything was written to
disk; re-ran with `prune_sources` limited to genuinely deleted files only.
Final graph: 5143 nodes, 10991 edges, 300 communities (all labeled). Also
reinstalled the `graphifyy` uv tool from scratch (`uv tool install --reinstall
--force graphifyy`) to fix a broken `tree_sitter._binding` import that blocked
AST extraction entirely - a Windows reparse-point deletion error required
PowerShell's `Remove-Item -Recurse -Force` on the tool venv dir first, plain
`rm`/`uv pip install --reinstall` couldn't clear it.

**Pending exploration, left for a future session/window to pick up**: the
single most interesting question this refreshed graph can answer -
`src/guided.py` has the highest betweenness centrality in the whole graph
(0.112) and bridges 47 distinct communities (from Odoo connector tests to
voice doc-reader to risk-assessment deliverables) - i.e. it's the de facto
connective tissue of the entire system, not just one module among many. Trace
it with `graphify query "Why does src/guided.py connect so many different
communities - what is the never-unprotected guided-execution contract and
which subsystems actually depend on it?"` (or open `/graphify` and ask
interactively) - not run yet this session, the user asked to defer it.

**Resume here — THE MCP SERVER ACTUALLY WORKS NOW, GENUINELY VERIFIED (not just "deploy succeeded").**
PR #100 (merged, `334e954`) fixed 3 compounding bugs that meant **no real MCP
client had ever successfully completed a tool call against the deployed
server**, found by actually driving a real MCP client round trip through the
mounted app — something no prior session had done (earlier "verified" checks
only covered `/api/health`, `/`, and that `/mcp` 307-redirected, never an
authenticated `initialize`+`tools/call`):
1. `app.mount()` doesn't propagate ASGI lifespan into the sub-app -> the
   FastMCP session manager's task group never started -> every real call past
   auth 500'd ("Task group is not initialized").
2. FastMCP's own default internal path ("/mcp") doubled onto the parent mount
   path ("/mcp") -> the only path that actually worked was `/mcp/mcp`, one
   segment longer than the documented client URL (`docs/MCP_SERVER.md`).
3. FastMCP's DNS-rebinding Host-header check auto-allowlists only
   localhost/127.0.0.1/::1 -> a public deploy 421'd every real client
   regardless of a valid key. Fixed with `LINCHPIN_MCP_ALLOWED_HOSTS` (new env
   var, see `docs/DEPLOYMENT.md`).

**Production HAS been redeployed with this fix (same day, 2026-07-03)** —
user supplied a fresh Fly API token in chat (handled per
[[secret-pasted-in-chat-handling]]: saved locally, never echoed, deleted after
use), staged `LINCHPIN_MCP_ALLOWED_HOSTS=linchpin.fly.dev`, ran `fly deploy
--app linchpin`, then **issued a real production MCP key
(`fly ssh console -C "python examples/issue_mcp_key.py issue ..."`) and ran an
actual `initialize`+`tools/call` round trip against
`https://linchpin.fly.dev/mcp/`** with the official `mcp` SDK client — got a
real `abc_xyz` classification result back, then revoked the throwaway test
key. **Nothing blocks §3.1's MCP-directory listings anymore** — genuinely
confirmed, not assumed.

Earlier in the same overall effort (PR #99, `9101c7a`, already on `main`
before this): `WEB_CONCURRENCY=1` (2 uvicorn workers OOM-killed the 512mb VM)
and the persistent Volume moved from `/app/data` to `/data` (it was shadowing
baked-in sample CSVs) — both already live and stable, unaffected by the above.
A separate bug was caught earlier still in `railway.json`/`docs/DEPLOYMENT.md`
(PR #98, merged): `pip install -e ".[web]"` alone omits the `mcp` extra, which
`webapp/app.py` hard-requires — fixed to `.[web,mcp]`. `railway.json` +
`docs/DEPLOYMENT.md` §2b are kept in case Railway becomes viable again later.

The user's stated objective for this whole project, verbatim: *"el objetivo
de este agente es generar dinero"* — default to whatever advances revenue
over further engine/backlog polish unless told otherwise. Also in flight,
same session that found the bug above: a real installable **Odoo Apps Store
module** (Odoo.sh/on-premise only, not Odoo Online — see
[[linchpin-odoo-store-module]] for why), calling this now-fixed MCP surface
via per-client keys.

> A new Claude Code session in this repo also auto-loads memory: `MEMORY.md` →
> [[linchpin-project]], [[linchpin-priority-monetization]], [[linchpin-monetization-plan]],
> [[linchpin-audit-fixes-2026-07]], [[linchpin-formula-injection-fix]],
> [[linchpin-concurrent-sessions]], [[linchpin-odoo-store-module]]. This file is the human-readable, in-repo
> consolidation — memory has the play-by-play and the full research trail
> (real 2026 market data on MCP directories, x402 adoption, Odoo Store pricing).

---

## 1. What Linchpin is

Agentic supply-chain AI: a deterministic Python engine (EOQ, safety stock,
(s,Q)/(R,S), multi-echelon GSM, DDMRP, ABC-XYZ, newsvendor, queuing, scheduling,
facility location, DRP, transportation, FEFO, financial KPIs, supplier
scorecards, MCDM sourcing, landed cost, cost-to-serve, S&OP, reverse logistics,
warehouse layout, voice doc-reader) + an orchestrator agent, grounded in an
**L3 knowledge graph** (24 curated SCM sources) and packaged through a
**client-ready deliverable generator** (md + xlsx, cited). Where it can't act
itself, it hands off a ready-to-execute packet (the "never unprotected" Guided
Execution Layer, `src/guided.py`). Live **Odoo ERP connector** (`src/connectors/odoo.py`)
reads/writes through the safe-staging plane (`src/writeback.py`).

**New as of this session:** Linchpin now also **exposes** an MCP server of its
own (`webapp/mcp_server.py`, mounted at `/mcp`), not just consumes one
(graphify). This is Phase A of an explicit, memory-tracked go-to-market plan —
see [[linchpin-monetization-plan]] for the full ladder (MCP read-only → Stripe
metered → x402 pay-per-call) and where-to-publish research (Odoo Apps Store 70%
split, AI-agent directories, GEO, a portfolio site vs. Upwork/Fiverr commission).

---

## 2. Current state (verified 2026-07-02, after PR #96)

- **Tests:** 1202 passing, 13 skipped (`PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/ -q`). `ruff check src tests examples` clean.
- **Agent surface: still 34 tools** in the registry (`build_default_registry()` — don't trust any doc's number without re-running that check). Of those, **8 are also reachable over MCP** (see below) — the rest remain internal/dashboard-only for now.
- **Repo visibility: private** (the user made it private mid-session; it was public before, and the committed `knowledge/scm-books/graph.json` — 2.2MB of curated 24-source distillation — was exposed the whole time it was public). Not retroactive: any pre-existing clone/fork keeps its copy.
- **MCP server (PR #91, shipped):** `webapp/mcp_server.py`, mounted at `/mcp` on the existing FastAPI app, Streamable HTTP transport. Exposes exactly 8 read-only tools (`linchpin_inventory_optimize`, `linchpin_classify_abc_xyz`, `linchpin_newsvendor_order_quantity`, `linchpin_forecast_demand`, `linchpin_financial_kpis`, `linchpin_price_optimize`, `linchpin_audit_data_quality`, `linchpin_whatif_sensitivity`). `odoo_replenishment` and everything else that can write to a client's system of record is deliberately NOT exposed — traced and confirmed by an independent security review, not just asserted in a comment. Auth: new `src/mcp_keys.py::McpKeyStore` (SQLite, per-client high-entropy keys, hash-only at rest, issue/validate/revoke) + `webapp/mcp_auth.py` middleware — separate from the dashboard's single shared `LINCHPIN_API_KEY`. Rate limiting is **identity-aware** (keyed by resolved client name post-auth, not shared source IP — a real bug the security review caught and this session fixed before merge). Manual key issuance only: `python examples/issue_mcp_key.py issue "<client name>"`. Full reference: `docs/MCP_SERVER.md`.
- **Formula-injection fix (PR #92, shipped) — now fully closed (PR #94, this session):** the MCP security review incidentally surfaced a pre-existing CSV/Excel formula-injection gap (OWASP CSV injection) in the dashboard's regular deliverable exports — a `product_id` starting with `=`/`+`/`-`/`@` survived unescaped into generated `.xlsx`/`.csv` files, and openpyxl auto-promotes a leading `=` string to a live formula. Fixed: new `src/sanitize.py::defuse_formula()`, wired into every confirmed sink (`src/excel_export.py`, `jobs/deliverables.py`, `src/export.py::write_summary_csv()`). **PR #94 closed the one sink PR #92 missed**: `src/powerbi_export.py` has its own `_write()` CSV helper that never routed through `write_summary_csv()` — now sanitized the same way.
- **Unauthenticated deliverable downloads — closed (PR #95, this session):** `GET /jobs-output/*` (serving generated job deliverables) had zero auth of its own, only an unguessable `tempfile.mkdtemp()` dir name — while `POST /api/jobs` was already gated by `LINCHPIN_API_KEY`. New `webapp/security.py::jobs_output_auth_middleware` requires the same key once configured (no-op when unset). Watch the registration order in `webapp/app.py` if you touch this again: it must be registered **before** `security_headers_middleware` so the latter stays the outermost layer — a code-review pass caught (and this session fixed + verified live) that the reverse order silently strips every hardening header off this middleware's own 401 responses. Both §3.2 follow-ups from PR #92 are now closed; no known formula-injection or jobs-output auth gaps remain.
- **Deployment — LIVE on Fly.io as of 2026-07-03.** Railway's `railway.json` (PR #96) is kept as a demoted "2b. Alternative" in `docs/DEPLOYMENT.md`, but the user's Railway trial ran out before anyone actually deployed anything, so Fly.io became the primary path — and this time it went all the way to a real deploy. **App: `linchpin` on Fly, org `personal`, region `iad`, live at `https://linchpin.fly.dev`.** The user provided a Fly API token (pasted directly in chat — flagged to them as now-exposed and worth rotating after the fact) which was used non-interactively: installed `flyctl` (wasn't on this machine before), created the app + a 1GB Volume, staged secrets (`LINCHPIN_API_KEY`/`LINCHPIN_APPROVAL_SECRET`, freshly generated, never printed to any log/output), and ran `fly deploy`. Verified live, not just "deploy succeeded": `/api/health` returns real SKU data, `/` is 200, `/mcp` mounts (307), `POST /api/jobs` correctly 401s without a key, security headers present on every response. **Two real bugs only surfaced at this stage** (local `docker build`/`docker run` testing earlier the same session didn't catch them, since that test ran the container standalone with no Fly Volume attached) — see the top of this file for both (OOM at 2 workers; Volume-mount path shadowing the baked-in sample CSVs). Both are fixed in `fly.toml` **but that fix is not yet merged to `main`** — see §3.1.
- **Bug fixed earlier the same session (PR #98, merged):** `railway.json`'s `buildCommand` and `docs/DEPLOYMENT.md`'s generic "Run it" section both said `pip install -e ".[web]"`, missing the `mcp` extra — but `webapp/app.py` unconditionally imports `webapp.mcp_server`, which hard-imports the `mcp` package, so a fresh install with only `.[web]` crashes at import with `ModuleNotFoundError: No module named 'mcp'`. Verified this failure and the `.[web,mcp]` fix in an isolated venv before committing. This is a DIFFERENT bug from the two found during the live Fly deploy above — three distinct deploy-config bugs surfaced this session in total, none of which the local Docker test or the full pytest suite caught (none of them are Python-level bugs; all three are infra/config-level, a category this repo's tests structurally can't cover).
- **README.md / docs:** already refreshed this cycle (PR #87, #90) — 34-tool capability tables, book/author attribution removed, MIT license section removed from the README text (the actual `LICENSE` file at the repo root was intentionally left untouched — a separate decision the user hasn't made yet).
- **Odoo connector:** unchanged — still needs validation against a REAL Odoo instance (user has none yet; don't treat as urgent unless they say otherwise).

---

## 3. Immediate next steps, in priority order

### 3.1 [explicitly requested by the user] Publish the MCP server on free directories

**Nothing blocks this anymore — production is genuinely verified working
end-to-end as of 2026-07-03 (see the top of this file).** `flyctl` itself is
installed at `C:\Users\<user>\.fly\bin\flyctl.exe` on this machine (not on
PATH by default — invoke it by that full path, or the equivalent on whatever
machine a future session runs on) — a fresh session doesn't need to reinstall
it, only a fresh `FLY_API_TOKEN` when the operator provides one (tokens are
never persisted between sessions, by design).

**Remaining steps, in order:**

1. **Issue an MCP key for a real client** once one exists —
   `fly ssh console -C "python examples/issue_mcp_key.py issue '<client
   name>'" --app linchpin` (runs inside the deployed environment, against the
   mounted Volume at `/data`). Not done yet — no real paying/trial client to
   issue one for as of this write-up.
2. **Register with the official MCP registry**, then list/claim on the
   directories researched previously (see [[linchpin-monetization-plan]] §5
   for the real 2026 numbers): **Glama** (~37-51k servers indexed), **Smithery**
   (~7k+, app-store UI, hosted remote servers), **PulseMCP** (~12-18k+,
   hand-reviewed) — these are discovery storefronts that read off the official
   registry and let an owner claim their listing; check each one's actual
   current submission process before assuming it's identical across all three,
   it still wasn't independently verified step-by-step, only that they exist
   and are free. **This is now the actual next action** — nothing else blocks it.

`railway.json` + `docs/DEPLOYMENT.md` §2b are kept in case Railway becomes
viable again later (a new account, a new trial) — don't delete them, but
don't default back to Railway without the user saying so. **If a future
session's Fly deploy behaves differently than described here** (Fly's
platform/CLI does evolve), trust `fly logs --app linchpin` and `fly status
--app linchpin` over this document's specifics.

### 3.2 [CLOSED, this session] Two follow-ups from the formula-injection fix (PR #92)

Both fixed and merged this session:

1. `webapp/app.py`'s `/jobs-output` deliverable-download route had zero
   authentication of its own. **Fixed in PR #95** — new
   `webapp/security.py::jobs_output_auth_middleware` gates it behind the
   existing `LINCHPIN_API_KEY`.
2. `src/powerbi_export.py::build_powerbi_dataset()` had the identical
   unfixed formula-injection vulnerability. **Fixed in PR #94** — `_write()`
   now sanitizes every cell via `defuse_formula()` before `to_csv()`.

No known follow-ups remain from PR #92's security review.

### 3.3 The rest of the monetization ladder (see [[linchpin-monetization-plan]] for full detail)

- **Phase B (trigger: >5 paying MCP clients, not before):** Stripe metered subscription billing, replacing manual key issuance.
- **Phase C (trigger: real agent-to-agent payment demand shows up, not before):** x402/pay-per-call. Real but volatile market as of this research (volume down ~77% from a Nov 2025 peak) — don't build this speculatively.
- **Odoo Apps Store packaging:** 5 proposed "agent" bundles regrouping the 34 tools by Odoo-fit (Demand & Inventory Planner flagship, Inventory Control, Procurement & Sourcing, Warehouse & Logistics, Finance & Pricing) + a non-Odoo "Strategy Suite". Corrected pricing after checking real comparables: **$99-299 one-time per Store listing** (NOT the $7-22k a naive hours-of-consulting calculation first produced — that mistake and the correction are recorded in memory, worth reading before quoting a client). Recurring revenue has to live on Linchpin's own webapp, not the Odoo Store (structurally one-time-sale only).
- **GEO** (Generative Engine Optimization — get surfaced in ChatGPT/Claude/Perplexity's own answers) and an **own portfolio site** (0% commission vs. Upwork's 10%/Fiverr's 20%) — both zero-build-cost, positioning/content work, not code.
- **Zero paying clients, zero real-Odoo validation today** — the audit's own read was that the Inventory/Demand-Planner slice (~82%) is genuinely sellable now; don't let more engine work substitute for actually finding the first client. A discounted/free founding-client pilot (in exchange for a case study) was the recommended way to get the first proof point before quoting full price.

### 3.4 Lower-urgency engine/code backlog (unchanged, still real, still not urgent)

Jobs-layer `_pick_column` duplication across ~19 files, two coexisting
deliverable-builder generations for inventory/pricing, unstyled generic deck
XLSX, only `POST /api/jobs` authenticated (besides the new `/mcp` gate — most
other endpoints like `/api/portfolio`/`/api/warehouse` still aren't), a few
engine nits (DEA NaN-on-failure, dead Incoterm branch, AutoETS season-length
heuristic). None of this blocks revenue work; see prior HANDOFF revisions in
git history for the full original list if it's ever needed.

---

## 4. How to run (conventions, updated)

- **Python 3.11+**, `.venv` is uv-managed (no pip): `uv pip install --python .venv/Scripts/python.exe <pkg>`.
- **Tests:** `PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/ -q`. **Lint** (matches CI): `ruff check src tests examples`. ASCII-only in console prints (Windows cp1252).
- **⚠️ CI's real dependency source is `requirements-dev.txt`, NOT `pyproject.toml` extras directly.** `requirements-dev.txt` is a hand-maintained mirror (`pip install -r requirements-dev.txt`, comment says "canonical source is pyproject.toml" but CI never reads pyproject's extras itself). **If you add a new optional dependency to `pyproject.toml`, you MUST also add it to `requirements-dev.txt` or CI silently can't import your new code.** Bit this session (`mcp`, `pytest-asyncio` were added to `pyproject.toml`'s extras first, forgotten in `requirements-dev.txt`, caught only by manually building a throwaway venv from `requirements-dev.txt` and running the suite before opening the PR — do that verification step for any future new dependency).
- **New: async tests.** `pytest-asyncio` is now a dependency (`asyncio_mode = "auto"` in `pyproject.toml`), needed for `tests/test_mcp_server.py`'s `async def test_*` functions (FastMCP's `list_tools()`/`call_tool()` are coroutines).
- **Workflow:** feature branch → draft PR → CI green (3.11/3.12/3.13) → `gh pr ready` if opened as draft (a PR still marked draft fails to merge with "Pull Request is still a draft") → squash-merge. Never push straight to `main`.
- **graphify:** `graphify update .` refreshes the **code** graph (AST-only, gitignored `graphify-out/`). The **books** graph lives in `knowledge/scm-books/` (committed, needs an LLM backend to rebuild).
- **New agent-tool recipe** (unchanged): `jobs/<x>_job.py` with a pandas-only `prepare()` → `run`/`verify`/`build_deck` → a `Tool` in `scm_agent/tools.py` → an `options` builder in `scm_agent/tool_options.py` → add its key to `tests/test_scm_agent.py::test_build_default_registry_tools`.
- **New MCP-tool recipe:** add a job_type string literal + a thin `@mcp.tool`-decorated wrapper in `webapp/mcp_server.py`'s `build_mcp_server()`, following the existing 8 as templates — reuses the shared `_run_analysis_tool_sync` bridge, no new plumbing needed. Only ever add tools that are genuinely read-only/no-writeback; that boundary is the whole trust model of this surface.

---

## 5. Gotchas / warnings (read before committing)

- **Worktree recipe (Windows):** `git worktree add -b <branch> C:/Users/<you>/Music/scm/.wt-<x> origin/main` → edit/test with the **main repo's** `.venv/Scripts/python.exe`, cwd = worktree, absolute `PYTHONPATH` → commit → push → `gh pr create --draft` → CI green → `gh pr ready` → `gh pr merge --squash --delete-branch`.
- **`gh pr merge --delete-branch` reliably fails to delete the LOCAL branch** if a worktree still references it — the remote merge still succeeds regardless; verify with `gh pr view N --json state,mergedAt`. Clean up after: `git worktree remove --force <path>`, `git branch -D <branch>`, `git push origin --delete <branch>` manually.
- **Parallel worktrees editing the same shared file (esp. `CHANGELOG.md`'s `[Unreleased]` section) WILL produce a real merge conflict** even when the underlying code changes don't overlap at all — happened twice this session across unrelated branches. `git merge origin/main` + manually dedupe the resulting duplicate section headers.
- **This also happens with plain SEQUENTIAL PRs in one session, not just parallel worktrees** — PRs #94/#95 (this session) both appended to the same `### Fixed` insertion point; #95 hit a real conflict on `gh pr merge` even though it was branched, developed, and pushed one at a time (no worktrees involved), simply because #94 had merged in the interim. Same fix: `git fetch origin && git merge origin/main`, keep both bullets (order doesn't matter), re-run the full suite, re-push, wait for the fresh CI run before merging.
- **This repo runs genuinely concurrent Claude Code sessions.** Re-fetch and re-read `HANDOFF.md` right before finalizing any PR, not just at session start — main moves fast, and another session's work (a different worktree, a different branch) can land while you're mid-task. This session alone saw two other sessions' PRs land unprompted (#89 idempotency race, #92 formula injection via a spawned task) — neither caused a conflict, but both were only discovered by checking, not assumed.
- **`spawn_task` chips the user starts run as fully separate sessions** — they show up as new worktrees (this session saw one under `.claude/worktrees/<name>`, a different convention than this repo's own `.wt-<x>` sibling-directory habit) and can merge to `main` without this session's direct involvement. Check `gh pr list`/`git log` for surprises before assuming a flagged follow-up is still unstarted.
- **Never read or surface PII** — some datasets (e.g. DataCo) carry customer PII; analysis is aggregate-only.
- **Don't paste secrets** into chat or commits. `LINCHPIN_APPROVAL_SECRET`, `LINCHPIN_API_KEY`, and now `data/mcp_keys.sqlite3` (hashed keys, but still real client identities — gitignored, never commit) are all real secrets/sensitive state.
- `.env`, `data/*.sqlite3`, `graphify-out/`, `deliverables/` are gitignored (the `data/*.sqlite3` entry is new this session — it was a real gap before: `CLAUDE.md` claimed all of `data/` was gitignored, only `data/kaggle/` actually was).
- **A cloud platform's persistent Volume mount hides whatever the image already had at that path** — mounting at `/app/data` (where the image ships small static sample CSVs) silently made those files disappear and crashed the app with `FileNotFoundError`. General lesson beyond Fly specifically: never mount a persistent volume at a path the image itself writes static files into; give persisted state its own dedicated path (`/data`, not `/app/data`).
- **Multi-worker memory cost is NOT amortized across `uvicorn --workers N`** for this app — the orchestrator, forecast cache, and L3 knowledge graph are all loaded independently per worker process (no shared-memory IPC), so RAM scales roughly linearly with worker count. A 512mb VM OOM-killed in a crash loop at `--workers 2`; stable indefinitely at `--workers 1`. Don't raise worker count on a memory-constrained host without proportionally raising memory too.
- **Local `docker build && docker run` testing a container standalone does NOT catch bugs that only manifest with a real persistent Volume attached** (the shadowed-sample-CSVs bug above only appeared once actually deployed on Fly with the Volume mounted — the local Docker smoke-test earlier the same session, with no volume, passed cleanly). Local Docker testing catches import/boot errors; it does NOT substitute for a real deploy when volumes/mounts are part of the config being tested.

---

## 6. Key files (updated)

- **Fly.io deployment — LIVE:** `Dockerfile`, `fly.toml` (app `linchpin`, region `iad`, `WEB_CONCURRENCY=1`, Volume mounted at `/data`), `.dockerignore`, `docs/DEPLOYMENT.md` §2a ("Quick path: Fly.io") — verified against the real running app at `https://linchpin.fly.dev`, not just locally.
- **Railway deployment (PR #96, now §2b alternative):** `railway.json` (build/start/healthcheck — `buildCommand` fixed this session to include the `mcp` extra), `docs/DEPLOYMENT.md` §2b.
- **MCP server:** `webapp/mcp_server.py` (the 8 tools + shared bridge), `webapp/mcp_auth.py` (identity-aware auth+rate-limit middleware), `src/mcp_keys.py` (`McpKeyStore`), `examples/issue_mcp_key.py` (operator CLI), `docs/MCP_SERVER.md` (full reference).
- **Security fixes:** `src/sanitize.py` (`defuse_formula()`), wired into `src/excel_export.py`, `jobs/deliverables.py`, `src/export.py`, and (PR #94) `src/powerbi_export.py`. `webapp/security.py::jobs_output_auth_middleware` (PR #95, gates `/jobs-output`) — mind the registration order in `webapp/app.py` (must come before `security_headers_middleware`).
- Writeback safety: `src/writeback.py`, `src/writeback_store.py` (`SqliteAuditLedger`, now with atomic `claim()`/`release()` for the idempotency race fix, PR #89).
- Odoo connector: `src/connectors/odoo.py` (`_ReorderRuleStore`, `_DraftPoStore` — partial-failure compensation + idempotency claiming, both now fixed).
- Agent routing/grounding: `scm_agent/registry.py`, `scm_agent/intent.py`, `scm_agent/knowledge.py`.
- Agent: `scm_agent/{orchestrator,registry,intent,knowledge,modes,tools,tool_options,guided_bridge,llm,types}.py`
- Deliverable: `src/deliverable.py`, `jobs/deliverables.py`, `jobs/*_deliverable.py`
- Engines: `src/*.py` (34 tools' worth — run `build_default_registry()`, don't trust a static list)
- Knowledge: `knowledge/scm-books/` (L3 books graph, committed, 24 sources), `graphify-out/` (code graph, gitignored)
- Tests: `tests/test_*.py` (1202 passing, includes new `test_mcp_*.py` files)
- Top-level docs: `README.md`, `scm_agent/README.md`, `docs/MCP_SERVER.md`, `docs/DEPLOYMENT.md`
