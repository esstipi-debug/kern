---
type: "query"
date: "2026-08-23T14:56:57.396030+00:00"
question: "Por que el MCP de Kern no escribe, si autonomy.py ya sabe degradar a HANDOFF?"
contributor: "graphify"
outcome: "useful"
source_nodes: ["Brecha 3: MCP de escritura con tiers de autonomia", "webapp/mcp_server.py::build_mcp_server", "Price Decision Authority"]
---

# Q: Por que el MCP de Kern no escribe, si autonomy.py ya sabe degradar a HANDOFF?

## Answer

Porque las dos mitades nunca se conectaron. mcp_tool_specs expone 35 de 50 y excluye writeback a proposito; autonomy.py ya auto-aplica lo reversible y degrada lo irreversible. Aterriza en build_mcp_server (webapp/mcp_server.py:172). Doctrina en docs/MCP_SERVER.md.

## Outcome

- Signal: useful

## Source Nodes

- Brecha 3: MCP de escritura con tiers de autonomia
- webapp/mcp_server.py::build_mcp_server
- Price Decision Authority