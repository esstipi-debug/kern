---
type: "query"
date: "2026-08-23T14:56:58.433541+00:00"
question: "Por que la key MCP la emite el operador a mano?"
contributor: "graphify"
outcome: "useful"
source_nodes: ["Brecha 7: onboarding self-serve de la credencial MCP", "src/mcp_keys.py::McpKeyStore.issue", "Digitalization of Supplier Data Collection"]
---

# Q: Por que la key MCP la emite el operador a mano?

## Answer

El bloqueo esta aguas arriba: billing es un stub sin webhook de Stripe, asi que no existe el evento que dispararia issue(). Ojo: el demo publico ya es self-serve; lo que falta es solo la credencial paga. Aterriza en McpKeyStore.issue (src/mcp_keys.py:67).

## Outcome

- Signal: useful

## Source Nodes

- Brecha 7: onboarding self-serve de la credencial MCP
- src/mcp_keys.py::McpKeyStore.issue
- Digitalization of Supplier Data Collection