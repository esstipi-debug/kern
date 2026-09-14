---
type: "query"
date: "2026-09-04T17:53:47.196276+00:00"
question: "Todas las citas L3 de Kern pasan por el citation gate?"
contributor: "graphify"
outcome: "useful"
source_nodes: ["Orchestrator", "_step_citations()", "filter_citations()"]
---

# Q: Todas las citas L3 de Kern pasan por el citation gate?

## Answer

No, y la diferencia importa. Orchestrator._ground (scm_agent/orchestrator.py:236-243) llama knowledge.ground_citations(limit=5) DIRECTO y nunca invoca citation_gate: sin anclas, sin MAX_HOPS, sin piso MIN_CITATIONS, sin EXCLUDED_CONCEPTS. El gate solo corre en scm_agent/packages.py::_step_citations y en los tres jobs con pool propio. Consecuencia contraintuitiva: decir 'procedure_draft cita cero' es cierto SOLO por el camino de paquete; por el camino single-tool (que es el que usa un brief comun por CLI o web) muestra lo que el ranking IDF traiga, falsos amigos incluidos. Y los pools NO son uno solo: packages.py:104=16, jobs/repricing.py:59=8, jobs/integrated_plan.py:256=6, jobs/price_intelligence.py:595=6. Cualquier medicion del impacto de una fuente L3 nueva tiene que barrer los cuatro; barrer solo el de 16 subestima el dano. Medido 2026-09-04 con el grafo de 3032 nodos: de 52 tools ancladas, 40 tienen 3 citas, 6 tienen exactamente 2 (al filo del piso) y 6 tienen cero.

## Outcome

- Signal: useful

## Source Nodes

- Orchestrator
- _step_citations()
- filter_citations()