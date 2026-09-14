---
type: "query"
date: "2026-08-23T14:56:58.178640+00:00"
question: "Kern consume herramientas MCP de terceros?"
contributor: "graphify"
outcome: "useful"
source_nodes: ["Brecha 6: MCP como cliente, no solo como servidor", "src/connectors/__init__.py::InventorySource", "Data Integration"]
---

# Q: Kern consume herramientas MCP de terceros?

## Answer

No: publica 35 y no consume ninguna. Pero la mitad cliente ya esta instalada y sin usar (mcp 1.28.1 trae mcp/client/ completo), asi que no hace falta dependencia nueva. Aterriza en el Protocol InventorySource (src/connectors/__init__.py:60).

## Outcome

- Signal: useful

## Source Nodes

- Brecha 6: MCP como cliente, no solo como servidor
- src/connectors/__init__.py::InventorySource
- Data Integration