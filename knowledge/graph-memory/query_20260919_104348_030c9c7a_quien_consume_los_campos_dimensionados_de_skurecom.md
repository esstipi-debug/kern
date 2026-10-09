---
type: "query"
date: "2026-09-19T10:43:48.054633+00:00"
question: "quien consume los campos dimensionados de SkuRecommendation, y donde una negativa emitida como 0.0 se vuelve una decision"
contributor: "graphify"
outcome: "useful"
source_nodes: ["SkuRecommendation", "JobReport", "verify", "stock_lines_from_inventory", "kern_policy", "detect_events"]
---

# Q: quien consume los campos dimensionados de SkuRecommendation, y donde una negativa emitida como 0.0 se vuelve una decision

## Answer

SkuRecommendation se construye en UN solo modulo (jobs/inventory_optimization.py, 4 sitios: tres negativas + la politica con precio), asi que cambiar el tipo de un campo esta contenido en el productor -- webapp/app.py arma un dict con los mismos nombres y NO es un SkuRecommendation. Los consumidores que leen forecast/reorder_point/safety_stock/z_factor SIN guarda de policy_kind son solo cuatro: jobs/qa.py (las invariantes numericas por SKU), jobs/state_bridge.py::stock_lines_from_inventory (divide forecast por los dias del periodo), jobs/deliverables.py (_row y la tabla markdown) y src/simworld/runners.py::kern_policy. Todo el resto ya esta detras de un filtro: jobs/qa.py::UNPRICED_POLICY_KINDS, src/verify/coherencia.py::POLITICAS_SALTADAS, o la lista 'due' que filtra por order_now. Las dos listas de saltadas NO coincidian: qa tenia las tres clases y coherencia dos. Camino del defecto, reproducido: inventory_optimization emite la negativa con 0.0 -> state_bridge._usable no la puede omitir porque 0.0 no es None -> src/alerting.py ve avg_daily_demand 0 con stock y emite dead_stock sobre un SKU que vende.

## Outcome

- Signal: useful

## Source Nodes

- SkuRecommendation
- JobReport
- verify
- stock_lines_from_inventory
- kern_policy
- detect_events