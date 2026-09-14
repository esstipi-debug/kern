---
type: "query"
date: "2026-08-23T14:56:58.687392+00:00"
question: "Que revisa la prosa que narrative_rewrite le escribe al cliente?"
contributor: "graphify"
outcome: "useful"
source_nodes: ["Brecha 8: critico sobre el narrativo del entregable", "scm_agent/orchestrator.py::Orchestrator._narrative", "Forecast Value Added (FVA)"]
---

# Q: Que revisa la prosa que narrative_rewrite le escribe al cliente?

## Answer

Nada. El gate de QA revisa numeros y ninguna funcion verify toma prosa. Hay dos sitios de llamada (orchestrator.py:244 y packages.py:493). Precedente en Python dentro de Kern: src/seo/pdp_writer.py::enhance_with_llm. Teoria: knowledge::forecast_value_added (FVA de Gilliland).

## Outcome

- Signal: useful

## Source Nodes

- Brecha 8: critico sobre el narrativo del entregable
- scm_agent/orchestrator.py::Orchestrator._narrative
- Forecast Value Added (FVA)