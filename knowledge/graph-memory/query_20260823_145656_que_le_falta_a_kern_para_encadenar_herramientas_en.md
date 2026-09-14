---
type: "query"
date: "2026-08-23T14:56:56.876164+00:00"
question: "Que le falta a Kern para encadenar herramientas en vez de correr una sola?"
contributor: "graphify"
outcome: "useful"
source_nodes: ["Brecha 1: bucle ReAct para encadenar las 50 herramientas", "scm_agent/orchestrator.py::Orchestrator.run", "Iterative Re-Evaluation Loop"]
---

# Q: Que le falta a Kern para encadenar herramientas en vez de correr una sola?

## Answer

Un bucle ReAct. intent.classify devuelve un solo job_type y _run es lineal de una pasada; los paquetes iteran una tupla congelada. Aterriza en Orchestrator.run (orchestrator.py:79). Ojo: LLMProvider no expone tool-calling, asi que el bucle va sobre extract() o se extiende nosilo_core.

## Outcome

- Signal: useful

## Source Nodes

- Brecha 1: bucle ReAct para encadenar las 50 herramientas
- scm_agent/orchestrator.py::Orchestrator.run
- Iterative Re-Evaluation Loop