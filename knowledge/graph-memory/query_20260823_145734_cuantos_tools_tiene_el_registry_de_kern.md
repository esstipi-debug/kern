---
type: "query"
date: "2026-08-23T14:57:34.800506+00:00"
question: "Cuantos tools tiene el registry de Kern?"
contributor: "graphify"
outcome: "corrected"
correction: "Son 50, no 35 ni 45 ni 37. La entrada de memoria de 2026-07-06 decia 35 y quedo vieja; tres comentarios en el codigo tambien: autonomy.py:10 dice 37, packages.py:69 y :72 dicen 45, citation_gate.py:92 llama a flow_map 'tool #47'. Contar con: build_default_registry().list()."
source_nodes: ["build_default_registry()", "Tool"]
---

# Q: Cuantos tools tiene el registry de Kern?

## Answer

build_default_registry registra exactamente 50 tools, y las 50 son agent-routable (ninguna tiene intent_keywords vacio). El servidor MCP expone 35 de esas 50 y excluye todo writeback a proposito.

## Outcome

- Signal: corrected
- Correction: Son 50, no 35 ni 45 ni 37. La entrada de memoria de 2026-07-06 decia 35 y quedo vieja; tres comentarios en el codigo tambien: autonomy.py:10 dice 37, packages.py:69 y :72 dicen 45, citation_gate.py:92 llama a flow_map 'tool #47'. Contar con: build_default_registry().list().

## Source Nodes

- build_default_registry()
- Tool