---
type: "query"
date: "2026-09-23T11:50:46.325251+00:00"
question: "Donde se aplica una regla nueva que topea la politica de inventario, y que partes toca?"
contributor: "graphify"
outcome: "useful"
source_nodes: ["cap_policy", "sku_end_dates", "SkuRecommendation", "JobReport", "inventory_options"]
---

# Q: Donde se aplica una regla nueva que topea la politica de inventario, y que partes toca?

## Answer

Cinco lugares, no uno. El motor puro en src/horizon.py (cap_policy); el productor de la columna en jobs/intake.py::sku_end_dates (que espeja stock_position: ultima fila fechada gana, None y nunca {} sin columna); la aplicacion en jobs/inventory_optimization.py::run ANTES de _order_now (topear despues deja la orden de hoy calculada contra el objetivo viejo); la compuerta en jobs/qa.py::_horizon_issues; y TRES superficies -- jobs/deliverables.py (fila del Excel), jobs/inventory_deliverable.py (hallazgo del deck) y scm_agent/tool_options.py (OPCION + residual). Las ocho partes de la constitucion son para un TOOL nuevo; una capacidad dentro de un tool existente toca estas. Cuidado con (s,Q): su order_up_to es None y sus dos mitades son s y Q, asi que una regla que mire solo order_up_to deja pasar la cantidad.

## Outcome

- Signal: useful

## Source Nodes

- cap_policy
- sku_end_dates
- SkuRecommendation
- JobReport
- inventory_options