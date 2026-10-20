# Kern guardrails

One ordered map of the rules this repository enforces in code. Every row says
**where the rule lives** (`path:line`, as of the commit this file was written
against) and **which test watches it**.

Sources: `CLAUDE.md`, `.specify/memory/constitution.md` (which wins where they
disagree), and the code itself.

## How to read the evidence

Two different things were checked, and they are not the same strength:

- **Located** — the symbol, constant or file exists at the cited line. Checked
  with `grep` for every row below.
- **Exercised** — the guard test was actually run (see
  [Verification run](#verification-run)). A passing test proves the rule holds
  *today*; it does not prove the rule is *complete*.

Line numbers drift. If a citation is off, search for the symbol name, which is
stable. (The constitution makes the same trade: it names symbols, not lines,
and `tests/test_constitution_anchors.py` fails if a named path disappears.)

## How the guardrails came to exist

### The process

Nearly every guardrail was written **after a defect**, not in anticipation of
one. The constitution says so about itself: each law "is written here because
breaking it has already cost us a shipped bug, an outage, or a silent wrong
answer" (`.specify/memory/constitution.md:3-5`). The loop repeats:

1. **Find the defect**, usually by running a capability on real data, or by an
   audit (an AST scan, an A/B sweep, a simulated-world scoreboard). Synthetic
   fixtures tend not to see these.
2. **Reproduce it end to end** in a test.
3. **Fix the producer**, not the symptom.
4. **Keep the reproducing test as a permanent guard**, and write the rule in
   `CLAUDE.md` or the constitution so the next change reads it first.

Worked examples (each is described in `CLAUDE.md`):

| Defect found | Guard it left |
|---|---|
| The agent accepted `lead_time_stds` but never forwarded it to the job, so every agent run sized as if the supplier never failed. An AST audit of 310 `jobs.*` calls then found 86 unforwarded parameters, 11 of them client facts. | `tests/test_overrides_no_se_tiran.py` |
| A constant idempotency key silently dropped a *different* second operation, reporting `idempotent_skip=True` (the "this was a retry, all fine" signal). | `derive_idempotency_key` + `tests/test_claves_de_idempotencia.py` |
| The engine refused to size a SKU but emitted zeros with `status="ok"`; a downstream monitor then raised a false `dead_stock` alert. | `jobs/qa.py::_sized_field_issues` + `tests/test_negativa_no_es_cero.py` |
| The central inventory tool never looked at on-hand/in-transit stock, yet the scoring battery stayed green for months (it only scored the *best* candidate). | `src/bateria/barrido_neteo.py` + `tests/test_barrido_neteo.py` |
| Lost sales were valued at margin, subtracting unit cost twice. | corrected in `src/simworld/`; baseline re-recorded |

### What the git history can and cannot tell you

**The clone this file was verified in is shallow** (`git rev-parse
--is-shallow-repository` → `true`, 51 commits). Its oldest commit, `01d2d28`
(2026-09-12, #350), is an import of **1,379 files / 441,765 lines**. So for most
guard tests, "first commit that touches the file" only means "first commit
visible here", not "when it was written":

| File | First commit visible in this clone |
|---|---|
| `.specify/memory/constitution.md` | `8bbbaf6`, 2026-09-14, #316 — genuinely added here |
| `tests/test_overrides_no_se_tiran.py`, `test_claves_de_idempotencia.py`, `test_negativa_no_es_cero.py`, `test_barrido_neteo.py` | `b6de482`, 2026-10-09, #369 — genuinely added here |
| `tests/test_core_boundary.py`, `test_pack_boundary.py`, `test_nan_swallower_guards.py`, `test_tool_data_contract.py`, `test_citation_gate.py`, `nosilo_core/writeback.py` | `01d2d28`, 2026-09-12 — **import commit; real creation date not recoverable here** |

The *defect stories* above come from the repository's own prose (`CLAUDE.md`,
test docstrings, `documentation/KERN_*.md`). They were not independently
re-derived from history, because the history is truncated.

Built in sessions with Claude, under the workflow in `CLAUDE.md`: feature
branch → draft PR → CI green (py3.11/3.12/3.13) → squash-merge to `main`.

## 1. Protecting the human: the four outcomes

| Rule | Located | Watched by |
|---|---|---|
| Every consequential result is a `GuidedOutcome` with status `EXECUTED`, `OPTIONS`, `HANDOFF` or `ESCALATED`; the last three need a human. | `nosilo_core/guided.py:25-31` (`_NON_EXECUTED` at `:30`) | `tests/test_guided.py`, `tests/test_guided_builders.py` |
| There are **five** hats (buyer, planner, CFO, commercial, **transport**). The fifth is conditional: it needs weight-per-unit and route distance, and is left **out** (and the exclusion named) when they are absent. | `src/hats.py:46` (`HAT_KEYS`), `src/hats.py:268` (`evaluable_hats`) | `tests/test_hats_valuation.py` |
| An area with no threshold is `no_evaluable`, and the overall cross-area verdict is `indeterminado` — never "nobody objects". A run without freight data therefore cannot return "execute". | `src/objection.py:38` (`NO_EVALUABLE`), `jobs/cross_impact_job.py:124` (`INDETERMINADO`) | `tests/test_cross_impact.py`, `tests/test_cross_impact_job.py`, `tests/test_objection.py` |

## 2. Safe writes to client systems

| Rule | Located | Watched by |
|---|---|---|
| Never mutate a system of record blindly: stage a dry-run `Changeset`, classify by risk tier (`read` / `reversible` / `irreversible`), require an `Approval`, apply idempotently, support `rollback()`. | `nosilo_core/writeback.py:103` (`Changeset`), `:28-29` (tiers), `:151` (`Approval`), `:331` (`rollback`) | `tests/test_writeback.py`, `tests/test_writeback_store.py`, `tests/test_excel_writeback.py` |
| Irreversible changes always need explicit approval. | `nosilo_core/writeback.py:36-42` (`requires_approval`; `:42` "irreversible always needs a human in the loop") | `tests/test_writeback.py`, `tests/test_writeback_store.py`, `tests/test_excel_writeback.py` |
| Approvals are time-boxed: 900 s TTL. | `nosilo_core/writeback.py:190` (`ttl_seconds: float = 900.0`) | `tests/test_writeback.py`, `tests/test_writeback_store.py`, `tests/test_excel_writeback.py` |
| An idempotency key must be **stable** for the same operation and **different** for a different one; a constant key is a mute no-op. | `nosilo_core/writeback.py:70` (`derive_idempotency_key`) | `tests/test_claves_de_idempotencia.py` |
| Real connectors implement the store surface: Odoo and client Excel workbooks. | `src/connectors/odoo.py`, `src/connectors/excel.py` | `tests/test_connector_contract.py`, `tests/test_odoo_addon_boundary.py` |
| Discovery-assisted price watch is **read-only** (never imports `writeback`), and auto-onboarding is always `limited` / tier `L1`, never `allowed`. | `src/pricing_intel/acquire/auto_approve.py:60` (`AUTO_TOS_DECISION = "limited"`), `:63` (`AUTO_MAX_TIER = "L1"`); `jobs/price_watch.py:370-371` | `tests/test_price_watch_e2e.py` |

## 3. Quality of the deliverable

| Rule | Located | Watched by |
|---|---|---|
| **If QA fails, nothing ships.** Enforced in exactly one place, the orchestrator: `tool.qa(...)` returns issues → `STATUS_QA_FAILED` with empty `deliverables`. | `scm_agent/orchestrator.py:252-258` | `tests/test_scm_agent.py` (and `tests/test_orchestrator_*.py`) |
| Degradation must be loud: a `NaN`, a zero or a silent default in place of a real refusal is a defect as serious as a crash. | `.specify/memory/constitution.md:63` (principle III) | `tests/test_nan_swallower_guards.py` |
| Every data tool declares `required_columns`, or is listed in `UNDECLARED_TOOLS` with a written reason; that set may only shrink. | `tests/test_tool_data_contract.py:79` (`UNDECLARED_TOOLS`), `:284`, `:292` | same file |
| A deliverable's citations must resolve to real graph nodes near the tool's anchor concepts, or they are dropped (see [§8](#8-relationship-to-the-graph)). | `scm_agent/citation_gate.py:43-44`, `scm_agent/packages.py:121` | `tests/test_citation_gate.py` |

## 4. Data honesty: absence is declared

Common principle: **a missing value is `None`, never `0`**, and any surface that
shows the result says what is missing.

| Rule | Located | Watched by |
|---|---|---|
| An engine refusal never leaves as a number. Unsized SKUs carry `None` in forecast / reorder point / safety stock / z; QA gates **both** directions (no number on an unsized line, no `None` on a sized one). `skipped_zero_demand` keeps its zeros because there the zero *was measured*. | `jobs/qa.py:75` (`_sized_field_issues`), `jobs/inventory_optimization.py:208` (`UNSIZED_POLICY_KINDS`) | `tests/test_negativa_no_es_cero.py` |
| **Provisional cover** (2–5 periods of history): a number exists, but its field shape declares it is not a policy (no buffer, no z, no forecast). Two distinct lists — `UNSIZED_` ("engine put no number") and `NOT_FULLY_SIZED_` (adds cover) — consumed by different readers. | `jobs/inventory_optimization.py:186` (`MIN_PERIODS_FOR_COVER`), `:216` (`NOT_FULLY_SIZED_POLICY_KINDS`), `src/risk_period.py:20` (`provisional_cover`), `src/verify/coherencia.py:41`, `scm_agent/package_specs.py:261` | `tests/test_negativa_no_es_cero.py` |
| Nobody buys twice what is already on its way: net-flow position (`on_hand + on_order`) lives in one place and is read by the order emitters. With no in-transit data, `on_order` is `None`, not `0`. | `src/ddmrp.py:75` (`net_flow_position`), `src/bateria/barrido_neteo.py` | `tests/test_barrido_neteo.py`, `tests/test_neteo_en_transito.py` |
| Supplier terms (MOQ, order multiple): all consumers read the *same* lot rule, so they never disagree on what is feasible. | `src/constraints.py:73` (`lot_rule`), `:88` (`is_feasible_lot`), `:199` (`cheapest_feasible_lot`) | `tests/test_hats_valuation.py` |
| Per-SKU end-date cap can **shrink** a policy, never **zero** it; three verdicts (`expired`, `would_zero`, `unusable`) refuse to cap and are named on every surface. It never touches the safety buffer. | `src/horizon.py:47-51`, `:111` (`cap_policy`), `jobs/qa.py:318` (`_horizon_issues`) | `tests/test_titular_de_inversion.py` |
| The state store is per client: without `state_client_id` nothing is written, and never to the shared partition; an invalid id raises `ClientIdError`. | `jobs/state_bridge.py:17-18`, `:275` | `tests/test_state_bridge_stock.py`, `tests/test_simworld_state_bridge.py` |
| Client profiles only fill gaps; an explicit override wins; generic labels (`"Client"`, `"MCP client"`) never resolve a profile; `strict_params=True` blocks instead of taking the generic default. | `src/client_profile.py:77` (`is_generic_client_label`), `scm_agent/orchestrator.py:231` | `tests/test_client_profile.py` |
| Every connector exposing `orders()`/`arrivals()` exports a `SERVICE_COVERAGE`, and the test *runs* it against the adapter. | `src/connectors/__init__.py:150`, `src/connectors/odoo.py:2466` | `tests/test_connector_service_contract.py` |

## 5. What the agent accepts, it does not silently drop

| Rule | Located | Watched by |
|---|---|---|
| Every parameter a `jobs.*` call accepts must be forwarded from `scm_agent/tools.py`, or be listed with a written reason. Decided by reading the AST. | `tests/test_overrides_no_se_tiran.py` (uses `ast` at `:138-142`) | same file |
| A date crossing the JSON boundary is parsed in one place; an unreadable one **raises**, naming the key. | `scm_agent/tools.py:107` (`_fecha_param`) | same file |

## 6. Architecture boundaries

| Rule | Located | Watched by |
|---|---|---|
| `src/` and `packs/` depend on `nosilo_core`, never the reverse. | — | `tests/test_core_boundary.py`, `tests/test_pack_boundary.py` |
| Odoo addon boundary. | — | `tests/test_odoo_addon_boundary.py` |
| A new capability touches **eight fixed places**. | `.specify/memory/constitution.md:17`; registry in `scm_agent/tools.py`; `src/i18n.py:233` (`TOOL_TITLES`); pinned sets at `tests/test_scm_agent.py:121` and `tests/test_price_watch_tool.py:161` | `tests/test_constitution_anchors.py` |
| No module on the boot chain imports an optional-extra dependency at module level; the `prod-boot` CI job installs production extras only and imports `webapp.app`. | `.github/workflows/tests.yml:180` | `tests/test_pricing_intel_boot_safety.py` |
| Never delete `nosilo_core/`. | `CLAUDE.md` → Gotchas | — |

## 7. Meta-guardrails: the rules watch themselves

| Rule | Located | Watched by |
|---|---|---|
| Gates are tests, not scripts: CI config drift is caught by the suite. | `.specify/memory/constitution.md:84` (principle V) | `tests/test_ci_lint_and_coverage_scope.py`, `tests/test_coverage_gate.py` |
| Counts are derived, not assumed. `build_default_registry()` registers **60** tools (verified by running it). | `.specify/memory/constitution.md:93` (principle VI) | `tests/test_how_it_works_page.py` |
| The constitution names symbols, and a test fails if a named path vanishes. | `.specify/memory/constitution.md:9-14` | `tests/test_constitution_anchors.py` |

## 8. Relationship to the graph

**Short answer:** almost none of the guardrails *are* the graph. There is one
direct link and two indirect ones.

**Direct — the citation gate.** `scm_agent/citation_gate.py` uses the *books
graph* (`knowledge/scm-books/graph.json`). A citation must resolve to a real
node within `MAX_HOPS = 2` of the running tool's anchor concepts
(`TOOL_CONCEPTS`, `:54`); fewer than `MIN_CITATIONS = 2` survivors and the whole
batch is dropped (`:43-44`). It only removes candidates, never invents one. It
is wired into the commercial-package runner only
(`scm_agent/packages.py::_run_step`, filter call at `:121`); the single-tool path
still uses the ungated `ground_citations`.

**Indirect 1 — the code graph helps you not break guardrails.** Tests are kept
in the code graph *on purpose* (`.graphifyignore:13`, "Tests stay IN … they are
the answer to `graphify affected X`"), so `graphify affected "<symbol>"` tells you
which guard tests a change touches. It is navigation, not enforcement.

**Indirect 2 — graph memory.** `knowledge/graph-memory/` and
`documentation/GRAPH_LESSONS.md` carry lessons between sessions. Context only;
nothing is blocked by them.

Everything else (writeback, QA gate, data contracts, netting, import boundaries)
is plain code and tests and runs with no graph present. The code graph is
AST-only and needs no key.

## Verification run

**Status: pending.** The 20 guard-test files named in this document were being
run when this version was committed (`PYTHONPATH=. pytest <files> -q -x`).
Until this section is updated with the pass/fail counts, treat every row as
**Located**, not **Exercised**.

## Known inconsistencies found while verifying

- `documentation/KERN_BARRIDO_NETEO.md:7` says **7/14** emitters net in-transit
  stock, while `:58` and a fresh `python scripts/barrido_neteo.py --markdown`
  say **8/14**. The headline on line 7 is stale (the document says it is
  generated, so it should be regenerated, not hand-edited).
- This file's own earlier Spanish draft claimed creation dates for guard tests
  that the shallow clone cannot support; see
  [What the git history can and cannot tell you](#what-the-git-history-can-and-cannot-tell-you).

## Not verified

- Rows watched by the files named in §1, §2 and §4 (guided, writeback,
  cross-impact, state-bridge, client-profile): the symbol and the test file are
  located, but I did not trace each test to its exact assertion.
- The Excel connector's specifics (drift check, atomic write, backup,
  cell-exact rollback) and Odoo unit-step rounding are described in
  `CLAUDE.md`; the files exist, the behaviors were not re-derived here.
- The defect stories in [The process](#the-process) are the repo's own account,
  not reconstructed from history (which is truncated in this clone).
