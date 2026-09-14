---
type: "query"
date: "2026-08-23T14:56:57.652808+00:00"
question: "Puede Kern responder si una compra cumple el contrato del cliente?"
contributor: "graphify"
outcome: "useful"
source_nodes: ["Brecha 4: corpus por cliente, no solo el corpus del oficio", "scm_agent/knowledge.py::KnowledgeBase.__init__", "Supply Contract"]
---

# Q: Puede Kern responder si una compra cumple el contrato del cliente?

## Answer

No. El conjunto de grafos esta fijado a dos (libros y codigo) en KnowledgeBase.__init__:171 y no hay corpus por cliente; client_profile.py guarda solo campos estructurados. La teoria del contrato si existe: knowledge::supply_contract (Chopra cap.15).

## Outcome

- Signal: useful

## Source Nodes

- Brecha 4: corpus por cliente, no solo el corpus del oficio
- scm_agent/knowledge.py::KnowledgeBase.__init__
- Supply Contract