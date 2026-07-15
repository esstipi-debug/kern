---
type: "query"
date: "2026-07-09T00:26:47.035422+00:00"
question: "Which existing tools are the structural analogues for an audit_evidence capability?"
contributor: "graphify"
outcome: "useful"
source_nodes: ["Tool", "build_default_registry", "Orchestrator"]
---

# Q: Which existing tools are the structural analogues for an audit_evidence capability?

## Answer

jobs/reconciliation_job.py (book-vs-physical column sniffing == GL tie-out skeleton) and jobs/acceptance_sampling_job.py (binomial two-risk plan design == attribute sampling). Newer 5-function job pattern (prepare/run/verify/write_operational/build_deck) + one register() call; QA gate enforced once at orchestrator.py ~:148; SCM mode auto-includes new tools (tool_keys=None)

## Outcome

- Signal: useful

## Source Nodes

- Tool
- build_default_registry
- Orchestrator