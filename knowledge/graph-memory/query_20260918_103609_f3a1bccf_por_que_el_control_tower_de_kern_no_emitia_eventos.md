---
type: "query"
date: "2026-09-18T10:36:09.094212+00:00"
question: "Por que el Control Tower de Kern no emitia eventos en produccion, si los monitores estaban escritos?"
contributor: "graphify"
outcome: "useful"
source_nodes: ["run_all_monitors()", "store_from_params()", "plan_replenishment()", "snapshot()"]
---

# Q: Por que el Control Tower de Kern no emitia eventos en produccion, si los monitores estaban escritos?

## Answer

run_all_monitors() lee los dominios stock/forecast/outcomes de src.state, y el unico escritor de 'stock' era el puente del marcador en examples/run_world_sim.py: en produccion ningun job escribia, asi que el bucle sensaba sobre vacio (H1). Lo cierra jobs/state_bridge.py, que los cinco jobs con posicion llaman desde scm_agent/tools.py con la particion de params['state_client_id'] (misma regla que forecast_cycle.store_from_params: sin la clave no se escribe, y nunca la particion compartida). Dos unidades que se cruzan y hay que mirar: avg_daily_demand es por dia CALENDARIO (daily_demand_by_sku), no el promedio de demand_by_sku cuyo periodo es un dia-con-pedidos; y el override lead_times de inventory_optimization esta en PERIODOS (lead_time_periods_from_days), no en dias.

## Outcome

- Signal: useful

## Source Nodes

- run_all_monitors()
- store_from_params()
- plan_replenishment()
- snapshot()