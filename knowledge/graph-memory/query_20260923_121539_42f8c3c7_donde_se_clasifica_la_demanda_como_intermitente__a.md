---
type: "query"
date: "2026-09-23T12:15:39.738325+00:00"
question: "Donde se clasifica la demanda como intermitente (ADI/CV2) y que tools la consumen? (graphify query, budget 800)"
contributor: "graphify"
outcome: "dead_end"
source_nodes: ["Classified"]
---

# Q: Donde se clasifica la demanda como intermitente (ADI/CV2) y que tools la consumen? (graphify query, budget 800)

## Answer

Callejon: graphify query arranco en 7 nodos semilla mal elegidos (Classified de intake_scan, una memoria MCP, tools.py) y devolvio 2776 nodos truncados a 29 sin ninguno relevante. Lo que funciono: python examples/query_knowledge.py --search 'intermittent demand' (da src/forecasting.py::is_intermittent y src/forecastability.py) y luego grep de is_intermittent en jobs/.

## Outcome

- Signal: dead_end

## Source Nodes

- Classified