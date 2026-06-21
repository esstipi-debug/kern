# Design Spec — `scm_agent` (Orchestrator + Tool Registry)

**Status:** APPROVED (green-lit by user 2026-06-21). Ready for implementation plan.
**Sub-project:** L1+L2 of the "agentic SCM brain / AI agency" program (the spine).
**Builds on:** the existing engine (`src/`), playbooks (`jobs/`), webapp, intake/QA/deliverables.

---

## 1. Purpose

Turn the existing supply-chain toolkit into an **agent**: one entry point that takes a
request (a free-form brief + optional data) and drives it to a finished deliverable,
routing to the right capability. This is the spine the rest of the program plugs into
(memory/L3, ingestion/L4, more agents/L5, agency-ops/L6).

## 2. Decisions (from brainstorming)

| Decision | Choice |
|---|---|
| Decision/execution model | **Hybrid** — deterministic core, pluggable LLM layer |
| LLM layer | `LLMProvider` interface; **Claude (Anthropic) default** when `ANTHROPIC_API_KEY` set; **rules fallback** otherwise (always runnable) |
| Entry point | Python package `scm_agent/` + CLI `examples/run_agent.py` + thin `POST /api/jobs` (reuse webapp FastAPI) |
| MVP capabilities | `inventory_optimization`, `pricing`, **`leadership_chain`** (3rd, qualitative) |
| Deferred | memory/RAG (L3), multi-step plans, more tools, chat console, agency-ops |

## 3. Architecture

```
brief + (optional) data
  → intent.classify (rules)  ──(low confidence & LLM available)──>  LLM parse intent
  → registry.get(tool)        # inventory_optimization | pricing | leadership_chain
  → tool.prepare(input)       # CSV intake for quant tools; brief/answers for leadership
  → tool.run                  # playbook over the engine (or CHAIN scoring)
  → tool.qa                   # if it fails, NO deliverable is written
  → tool.deliver              # Excel / report / chart PNG, per tool
  → [LLM narrative upgrade]   # optional, only if a provider is available
  → JobResult
```

**Approach chosen:** registry-based (capabilities self-register with metadata) over
hardcoded `if job_type == ...` routing, so adding a capability = registering a tool.
The deterministic core is fully testable; the LLM only *improves* parsing and narrative.

## 4. Components

| Module | Responsibility | Key shapes / notes |
|---|---|---|
| `scm_agent/types.py` | request/result DTOs | `JobRequest{brief, data_path, job_type?, params, client}`; `JobResult{status, tool, confidence, deliverables, summary, qa_issues, clarifications}` |
| `scm_agent/llm.py` | pluggable LLM | `LLMProvider` (Protocol: `complete(prompt)->str`, `extract(prompt, schema)->dict`); `ClaudeProvider` (Anthropic SDK if key); `RulesFallback`; `get_provider()` env factory |
| `scm_agent/registry.py` | capability registry | `Tool{key, title, description, intent_keywords, requires_data: bool, prepare, run, qa, deliver}`; `ToolRegistry.register/get/list/match` |
| `scm_agent/intent.py` | classify a brief | `classify(brief, registry, provider) -> IntentResult{job_type, confidence, params}` — keyword scoring vs each tool's `intent_keywords`; LLM parse only when confidence is low and a provider exists |
| `scm_agent/orchestrator.py` | the spine | `Orchestrator.run(brief, data_path=None, overrides=None) -> JobResult` |
| `examples/run_agent.py` | CLI | `--brief "..." [--data x.csv] [--job ...] [--out] [--client]` |
| `webapp/app.py` | HTTP | `POST /api/jobs` (multipart: brief + optional file + params) → `JobResult` JSON + deliverable links |
| `tests/test_scm_agent.py` | tests | core tested with `FakeProvider` / `RulesFallback` (no real LLM) |

**Registry refinement (driven by the leadership tool):** `Tool.requires_data` lets the
orchestrator skip CSV intake; `deliver` returns whatever artifacts fit (Excel / PNG / MD).
Quant tools wrap the existing `jobs.inventory_optimization` / `jobs.pricing`.

## 5. The 3 capabilities

| Tool | Type | Input | Deliverable |
|---|---|---|---|
| `inventory_optimization` | quantitative | demand CSV/Excel | Excel + report (existing `jobs/`) |
| `pricing` | quantitative | price/qty CSV | Excel + report (existing `jobs/`) |
| `leadership_chain` | **qualitative** | brief / answers (no CSV) | **score + radar chart PNG + active directives** |

### `leadership_chain` (new — "active")
Wraps the CHAIN leadership model (Collaborative, Holistic, Adaptable, Influential,
Narrative). Mode A (default):
1. From the brief → derive the 5 scores (0–4) **with evidence**. If a provider exists,
   the LLM extracts scores+evidence; otherwise return `needs_clarification` with the
   diagnostic questions (active — tells the user exactly what to answer).
2. Deterministic CHAIN core (port of the skill's `score.py`) → archetype + **priority lever**.
3. **Radar chart** of the 5 dimensions (matplotlib — already a dependency) → `chain_profile.png`.
4. Report `leadership_report.md`: score table (each with one evidence line), archetype,
   priority lever, and **2–3 concrete practices** (active directives, from `practicas.md`).

Modes B (decision review — the 5 lenses) and C (coaching) are LLM-narrative variants;
MVP ships Mode A end-to-end, B/C as LLM-narrative if a provider exists.

### Skill install
Install the provided skill to `~/.claude/skills/liderazgo-chain/` with the correct layout
(`SKILL.md` + `references/practicas.md` + `scripts/score.py`) so `/liderazgo-chain` works in
Claude Code, and add a `--chart PATH` option to `score.py` to export the radar PNG.
Source zip extracted at `C:\Users\Gamer\Downloads\ANTROPIC\sfs-skill-extracted\`.
License note: the skill is **original synthesis** inspired by *From Source to Sold*
(Palamariu & Alicke, 2022) — it does not reproduce the book text. Keep that attribution.

## 6. Error handling — explicit `JobResult.status`

- `ok` — deliverables written.
- `needs_clarification` — ambiguous intent (and no LLM), or leadership Mode A without
  enough evidence → returns candidate job types / the diagnostic questions.
- `needs_data` — required columns missing (quant tools) → names which.
- `qa_failed` — the tool's QA failed → lists issues, no deliverable.
- `error` — tool exception → message.
- LLM unavailable → silently falls back to rules/templates (never fails for this).

## 7. Testing

- Core is 100% testable **without a real LLM** (mock via `FakeProvider` / `RulesFallback`).
- Cases: intent classification ("set up reorder points"→inventory, "what price maximizes
  profit"→pricing, "evaluate my SC leadership / CHAIN"→leadership); registry register/get/match;
  orchestrator end-to-end on the sample CSVs (deliverables + QA pass); `needs_clarification`,
  `needs_data`, `qa_failed` paths; leadership Mode A producing a score + chart file; the LLM
  branch with a deterministic `FakeProvider`.
- Keep `--cov-fail-under=80` on `src` green; add `scm_agent`/`jobs` tests.

## 8. Scope (YAGNI)

- **In:** registry + 3 tools, single-tool routing, rules classifier + LLM parse fallback,
  deterministic deliverables + optional LLM narrative, lib + CLI + thin API, skill install.
- **Out (later sub-projects):** memory/RAG (L3), multi-step plans, forecasting/dashboard/ABC
  tools, chat console, agency-ops (quoting/proposals/tracking).

## 9. Implementation build order (for next session)

1. Install `liderazgo-chain` skill to `~/.claude/skills/` (correct layout) + add `--chart` to `score.py`.
2. `scm_agent/types.py` → DTOs.
3. `scm_agent/llm.py` → `LLMProvider` + `ClaudeProvider` + `RulesFallback` + `get_provider()`.
4. `scm_agent/registry.py` → `Tool` + `ToolRegistry`; register the 3 tools (quant tools wrap `jobs/`).
5. `jobs/leadership.py` → CHAIN playbook (scoring core + radar chart + directives) + `qa.verify_leadership`.
6. `scm_agent/intent.py` → rules classifier + LLM parse fallback.
7. `scm_agent/orchestrator.py` → `Orchestrator.run`.
8. `examples/run_agent.py` → CLI.
9. `webapp/app.py` → `POST /api/jobs`.
10. `tests/test_scm_agent.py` (+ leadership tests).
11. Docs (`scm_agent/README.md`, README/CHANGELOG → v2.8.0, version bump).
12. Full suite + ruff + coverage green → commit + push.

## 10. Acceptance criteria

- `run_agent.py --brief "set up reorder points" --data <demand.csv>` → inventory deliverables, QA pass.
- `run_agent.py --brief "what price maximizes profit" --data <prices.csv>` → pricing deliverables.
- `run_agent.py --brief "evaluate our SC leadership: <evidence>"` → CHAIN score + `chain_profile.png` + directives (or `needs_clarification` with questions when evidence is thin).
- Runs with **and without** `ANTHROPIC_API_KEY`. `POST /api/jobs` returns a `JobResult`.
- Full test suite + ruff clean; `src` coverage ≥ 80%.
