---
type: "query"
date: "2026-09-04T17:53:04.779961+00:00"
question: "What is the recipe to add a new agent tool?"
contributor: "graphify"
outcome: "corrected"
correction: "La memoria del 2026-06-24 decia 'No routing edits' y nombraba solo jobs/<x>_job.py + scm_agent/tools.py. Es falso: son 14 espejos de produccion mas 9 archivos de test, y al menos dos de esos conteos ya estaban derivados antes de tocarlos."
source_nodes: ["build_default_registry()", "Tool", "procedure_audit_tool()"]
---

# Q: What is the recipe to add a new agent tool?

## Answer

Registrar la Tool en scm_agent/tools.py es aproximadamente el 10% del trabajo. El PR #322 (procedure_audit + procedure_draft, 51->53 tools) tuvo que tocar 14 espejos de produccion: scm_agent/tools.py, scm_agent/citation_gate.py (TOOL_CONCEPTS y EXCLUDED_CONCEPTS), scm_agent/tool_options.py, src/i18n.py (TOOL_TITLES es+en), webapp/how_it_works_data.py, webapp/mcp_tool_specs.py, webapp/static/operator/index.html, examples/run_capability_smoke.py, docs/MCP_SERVER.md, documentation/operator/04_runbooks.md, documentation/operator/05_deliverable_catalog.md, README.md, scm_agent/README.md y CHANGELOG.md; mas 9 archivos de test con conteos pineados (test_scm_agent, test_price_watch_tool, test_capability_smoke, test_how_it_works_data, test_how_it_works_page, test_mcp_server, test_tool_data_contract, test_packages_citations, test_intake_scan_job). VARIOS de esos numeros son conteos DERIVADOS, no espejos del registry: el encabezado de tests/test_tool_data_contract.py decia 40 cuando su propia lista ya tenia 41 entradas, o sea que ya venia derivando ANTES de la PR. Y test_intake_scan_job.py assertaba n_tools_evaluated==4 sin que nada lo relacione con el registry. Regla: contar leyendo la fuente, nunca copiando el numero anterior.

## Outcome

- Signal: corrected
- Correction: La memoria del 2026-06-24 decia 'No routing edits' y nombraba solo jobs/<x>_job.py + scm_agent/tools.py. Es falso: son 14 espejos de produccion mas 9 archivos de test, y al menos dos de esos conteos ya estaban derivados antes de tocarlos.

## Source Nodes

- build_default_registry()
- Tool
- procedure_audit_tool()