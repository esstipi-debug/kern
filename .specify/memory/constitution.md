# Kern Constitution

The laws this repository actually runs on. Every one of them is written here
because breaking it has already cost us a shipped bug, an outage, or a silent
wrong answer -- not because it sounded like a good idea.

Two rules about this file itself:

- **It names symbols, not line numbers.** Line numbers drift; a constitution
  that rots is worse than none. `tests/test_constitution_anchors.py` fails if
  any path or symbol named below stops existing.
- **When a law here goes stale, fix it in the same PR that changed the
  underlying fact** -- the same rule `CLAUDE.md` lives under.

## Core Principles

### I. A new capability touches eight fixed places (NON-NEGOTIABLE)

`CLAUDE.md` and `README.md` both say "adding a capability = one `register()`
call + a `jobs/<x>_job.py`". That is an understatement, and believing it is how
tools ship half-wired. The real list, every item verified against the code:

1. **`jobs/<x>_job.py`** -- a pandas-only `prepare()` that reads its own CSV
   (**not** `jobs/intake.py`), plus `run`, `verify`, and where the tool ships a
   deliverable, `write_operational` and `build_deck`.
2. **The import block** -- `from jobs import (` in `scm_agent/tools.py`, kept
   alphabetical (ruff's isort enforces it).
3. **`_<x>_prepare`, `_<x>_run`, `<x>_tool()`** -- the three module-level
   functions in `scm_agent/tools.py`. `_<x>_prepare` MUST map `ValueError` and
   `FileNotFoundError` to `Prepared(status="needs_data", ...)`; mapping them to
   `needs_clarification` puts the tool outside every data-contract guard.
4. **`reg.register(<x>_tool())`** -- inside `build_default_registry()` in
   `scm_agent/tools.py`. `ToolRegistry.register` raises on a duplicate key, so
   collisions fail loudly rather than silently overriding.
5. **`TOOL_CONCEPTS`** -- `scm_agent/citation_gate.py`. A tool with no anchor
   concepts cannot cite, and an uncited deliverable does not ship.
6. **`TOOL_TITLES`** -- `src/i18n.py`. Spanish and English MUST differ; a test
   already fails when someone pastes the same string twice.
7. **`test_build_default_registry_tools`** -- `tests/test_scm_agent.py`. The
   pinned key set.
8. **`test_registry_tool_count_matches_the_pinned_list`** --
   `tests/test_price_watch_tool.py`. The pinned count.

Plus, when the tool takes options: an options function in
`scm_agent/tool_options.py`.

**Deliberately NOT touched:** `webapp/how_it_works_data.py` (frozen by its own
tests) and `webapp/mcp_tool_specs.py` (MCP exposure is a separate decision, not
an automatic consequence of registering a tool).

**Rationale:** this list existed only as folklore reconstructed from git
archaeology, and it was reconstructed wrong at least once -- a handoff called it
"the 7 fixed places" and a later plan corrected it to 8. A procedure nobody
wrote down is a procedure that gets a step skipped.

### II. QA fails, so nothing ships

The pipeline is `brief -> intent.classify -> registry.get(tool) -> prepare ->
run -> QA -> deliver -> JobResult`. A failing `verify()` MUST block the
deliverable. Never soften a QA gate to make a run produce output; a wrong answer
delivered confidently is the worst thing this product can do.

### III. Degradation is loud or it is a bug

A capability that cannot compute its answer MUST say so. Returning `NaN`, zero,
a silent fallback default, or a fabricated metric in place of a real refusal is
a defect of the same severity as a crash -- crashes at least get noticed. This
is the rule behind the `verify_*`/`*_passed` invariants in `jobs/qa.py`.

### IV. Every data tool declares its contract, or says why it cannot

A tool with `requires_data=True` MUST declare `required_columns` (and
`optional_columns` where they exist), or stay listed in `UNDECLARED_TOOLS` in
`tests/test_tool_data_contract.py` **with a written reason naming the code that
makes it undeclarable**. That set may only shrink.

Two failure directions, and they are not symmetric. **Under-declaring** makes
`src/intake_scan.py` report a green verdict for a client folder that cannot
actually run the tool -- a false green, the exact failure the scan exists to
prevent. **Over-declaring** produces a false red: a pointless data request. When
forced to choose, choose the false red. Put "one of these five" style
requirements in `optional_columns`, never in `required_columns`.

### V. The gates are tests, not scripts

CI configuration drift MUST be caught by the test suite, not by someone
noticing. This is why `tests/test_ci_lint_and_coverage_scope.py`,
`tests/test_coverage_gate.py`, `tests/test_pack_boundary.py`,
`tests/test_citation_gate.py` and `tests/test_no_core_gate.py` exist. A new gate
goes in `tests/`, and any shell alias for it (`Makefile`) MUST match what CI
actually runs.

### VI. Counts are derived, never assumed

Any statement of "Kern has N tools" MUST be computed from
`build_default_registry()` or pinned to a commit SHA in the same text. Hardcoded
counts in prose have already gone stale in `documentation/`, on the
`/how-it-works` page, and inside a test's own explanatory comment.

## Runtime and Platform Constraints

- **Python 3.11+.** Tests run `pytest tests/ -q` with `PYTHONPATH=.`.
- **Never add a module-level import of an optional-extra dependency** (`bs4`,
  `rapidfuzz`, `price-parser`, `python-stdnum`, `extruct`, `httpx`, ...) to any
  module on the boot chain `webapp.app -> scm_agent -> tools -> jobs -> src`.
  Import it lazily inside the function that uses it. Production installs only
  `.[core,web,mcp,pricing-intel,dataquality]`; the full suite stays green
  because dev pulls those deps transitively, so this class of break is invisible
  until the app fails to boot in production. The `prod-boot` job in
  `.github/workflows/tests.yml` is the guard.
- **ASCII-only in console prints.** Windows cp1252 raises on em dashes. Markdown
  files written utf-8 are fine.
- **Dependency changes go through `uv`**, never bare `pip install` -- `uv.lock`
  is the artifact CI and the container resolve from. The `PreToolUse` hook
  `.claude/hooks/uv-guard.sh` enforces this.
- **Never read or surface PII.** Some datasets carry customer records; analysis
  is aggregate-only. Never commit secrets or paste them into a transcript.

## Development Workflow

- Feature branch -> **draft PR** -> CI green on py3.11/3.12/3.13 ->
  **squash-merge** to `main`. Never push straight to `main`.
- **Test first.** The failing test lands before the implementation that makes it
  pass. A declaration, a count, or a contract asserted without a test that would
  fail if it were wrong is not evidence.
- **A clean merge is not proof the code imports.** Git resolving two branches
  without conflict says nothing about whether the result runs. Import the
  package and run the suite after every merge.
- **Verify by running, not by reading.** "The docs say" is not verification.
  Cite `path:line` for a code claim, and the command you ran for a behavioral
  one.
- Specifications and plans for new work live in `specs/<NNN>-<slug>/` per this
  toolkit. The 60 artifacts under `docs/superpowers/` are the historical record
  and stay where they are.

## Governance

This constitution supersedes conflicting guidance elsewhere in the repository,
including `CLAUDE.md`, `README.md` and `CONTRIBUTING.md`. Where one of those
contradicts a principle here, the other file is the thing that is wrong, and
correcting it is part of the PR that noticed.

Amendments require: the change written here, the rationale stated in the PR
body, and any test or document the amendment invalidates updated in the same
PR. A principle may be removed only by showing the failure it prevented can no
longer occur.

`/speckit-analyze` treats a violation of any MUST above as CRITICAL.

**Version**: 1.0.0 | **Ratified**: 2026-08-31 | **Last Amended**: 2026-08-31
