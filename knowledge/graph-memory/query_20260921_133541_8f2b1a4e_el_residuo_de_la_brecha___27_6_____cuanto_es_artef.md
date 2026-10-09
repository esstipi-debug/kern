---
type: "query"
date: "2026-09-21T13:35:41.922487+00:00"
question: "El residuo de la brecha (-27,6%) - cuanto es artefacto de medir sobre un horizonte que se acaba?"
contributor: "graphify"
outcome: "useful"
source_nodes: ["capture", "score_run", "generate_world", "_seasonal_trend_demand", "oracle_orders"]
---

# Q: El residuo de la brecha (-27,6%) - cuanto es artefacto de medir sobre un horizonte que se acaba?

## Answer

Casi nada: -0,0054 con el borde acreditado, el 2,0% del residuo. El mecanismo esta en capture(): se normaliza contra el PISO, que tambien es ciego al horizonte, asi que el castigo de fin de horizonte viaja en los DOS terminos de (piso - kern) y se cancela en la resta; solo sobrevive la diferencia de borde, que es chica. Corolario que cuesta caro no saber: score_run cobra procurement de toda unidad ORDENADA, asi que lo pagado-y-no-vendido al cerrar una ventana es on_hand_end MAS (ordenado - recibido), y la canieria pesa 1,8x el estante -- por_que_bajo_el_piso._componentes mide solo on_hand_end. Y generate_world NO se puede re-sortear con otro largo para comparar: _seasonal_trend_demand normaliza la tendencia por max(weeks,1) y sortea el ruido en un bloque (n_skus, weeks), asi que hay que TRUNCAR con dataclasses.replace.

## Outcome

- Signal: useful

## Source Nodes

- capture
- score_run
- generate_world
- _seasonal_trend_demand
- oracle_orders