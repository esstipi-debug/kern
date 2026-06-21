# HANDOFF — resume here next session

**Last updated:** 2026-06-21 · **Status:** design approved (green light), ready to implement.

## TL;DR for the next session
The user gave the **green light** to build the **`scm_agent` orchestrator** (the agentic
SCM "brain" spine). The design is approved and written in
[`docs/superpowers/specs/2026-06-21-scm-agent-orchestrator-design.md`](superpowers/specs/2026-06-21-scm-agent-orchestrator-design.md).

**Next action:** invoke the `superpowers:writing-plans` skill on that spec to produce the
implementation plan, then implement (build order is in §9 of the spec). Brainstorming is
done — do NOT re-brainstorm; the spec is the source of truth.

## Where the repo is (current state, v2.7.0, pushed to origin/main)
- **Engine** `src/` — EOQ, safety stock, (s,Q)/(R,S) policies, fill rate, newsvendor,
  multi-echelon, simulation, forecasting (SES/Croston + σₑ), **pricing** (elasticity →
  optimal price → markdown), pluggable data sources (CSV/DataFrame/SQL), constraints.
- **Job layer** `jobs/` — intake (adapt any client schema), playbooks
  `inventory_optimization` and `pricing`, automated QA, deliverables (Excel + report).
  CLIs: `examples/run_inventory_job.py`, `examples/run_pricing_job.py`. Samples in `jobs/`.
- **Web UI** `webapp/` — FastAPI 4-tab Inventory Planner over the engine (`uvicorn webapp.app:app`).
- **Tests:** 132 passing, ruff clean, `src` coverage ~91% (gate 80%).

## The program (vision) and where we are
"Agentic SCM brain / AI agency" decomposed into layers:
- **L1 tool registry + L2 orchestrator** ← **designed & approved (next to build)**
- L3 knowledge/memory (over `graphify-out/`) · L4 ingestion · L5 agent roster ·
  L6 agency-ops (quoting/proposals) · L7 interfaces (chat). All deferred.

## Approved design highlights (full detail in the spec)
- **Hybrid:** deterministic core + pluggable `LLMProvider` (Claude default, rules fallback — runs with or without `ANTHROPIC_API_KEY`).
- **Package** `scm_agent/` + CLI `examples/run_agent.py` + thin `POST /api/jobs`.
- **3 capabilities:** `inventory_optimization`, `pricing`, **`leadership_chain`** (new).

## Pending input to action first
A leadership skill was handed off and approved as the **3rd tool + install**:
- Extracted at `C:\Users\Gamer\Downloads\ANTROPIC\sfs-skill-extracted\` (`SKILL.md`,
  `practicas.md`, `score.py`). It's the **CHAIN** model (original synthesis, not the book text).
- Install to `~/.claude/skills/liderazgo-chain/` (layout: `SKILL.md` + `references/practicas.md`
  + `scripts/score.py`) and add a `--chart` option to `score.py`.
- The `leadership_chain` tool must, when used, produce **a score + a radar chart + active
  directives** (user requirement: "que sea activo").

## Repo facts / how to run (Windows)
- Repo: `C:\Users\Gamer\Music\scm\supply-chain-optimization` (open THIS folder, not `scm\`).
  Remote: `github.com/esstipi-debug/supply-chain-optimization`.
- The Python with deps is the **`py` launcher** (Python 3.13 + pandas/scipy/fastapi/etc.).
  The `uv` graphify interpreter does NOT have the deps.
- Tests: `py -m pytest -q --cov=src --cov-fail-under=80` · Lint: `py -m ruff check src jobs tests examples scripts webapp`
- Env note: a stray `bolt-training-system` `.git` at the home dir was disabled
  (`C:\Users\Gamer\.git.DISABLED-bolt-training-system`) — do not restore it.

## Brainstorming task state
Tasks #1–#5 complete (context, decompose, questions, approaches, design+approval).
#6 (write spec) done. **#7 pending: user reviews spec → invoke `writing-plans`** (next session).
