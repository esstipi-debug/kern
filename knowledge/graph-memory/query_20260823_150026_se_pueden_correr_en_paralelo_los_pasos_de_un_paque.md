---
type: "query"
date: "2026-08-23T15:00:26.020458+00:00"
question: "Se pueden correr en paralelo los pasos de un paquete?"
contributor: "graphify"
outcome: "useful"
source_nodes: ["Brecha 5: fan-out paralelo de los pasos de un paquete", "scm_agent/packages.py::run_package", "Assembly-Line Balancing"]
---

# Q: Se pueden correr en paralelo los pasos de un paquete?

## Answer

Hoy no hay ninguna primitiva de fan-out en scm_agent/jobs/src. Y lo que gobierna no es que la fase 1 difiera escrituras (hace I/O de red por paso) sino el grafo de dependencias: cycle_count deriva de abc_xyz con KeyError duro. Aterriza en run_package (packages.py:271).

## Outcome

- Signal: useful

## Source Nodes

- Brecha 5: fan-out paralelo de los pasos de un paquete
- scm_agent/packages.py::run_package
- Assembly-Line Balancing