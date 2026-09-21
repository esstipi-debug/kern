---
type: "query"
date: "2026-09-21T22:35:39.568613+00:00"
question: "Una fecha de fin real por SKU paga? Y hace falta conocer la demanda futura?"
contributor: "graphify"
outcome: "useful"
source_nodes: ["apply_policy", "Policy", "generate_world", "score_run"]
---

# Q: Una fecha de fin real por SKU paga? Y hace falta conocer la demanda futura?

## Answer

PAGA: +16,2 puntos de captura (-27,6 a -11,5), mejora en los CINCO mundos, segunda palanca mas grande de la rama despues del nivel de servicio y la primera accionable. La regla es topear las DOS mitades de la politica por la demanda que queda hasta la fecha de fin del SKU; topear una sola no cambia la orden porque apply_policy pide hasta order_up_to cuando la posicion cae al reorder_point. El ahorro es casi puro: 12 por ciento menos unidades, 12.311 usd menos de compra, 368 mas de quiebre, el fill no se mueve y el capital inmovilizado al cierre cae 40 por ciento. Es la primera palanca que no es comprar mas sino comprar menos de lo que no vas a vender. Y NO hace falta el futuro: el carril que topea con la demanda futura verdadera (+0,1348) y el que topea con la media observada por semanas restantes (+0,1618) son equivalentes, difieren mas de 0,02 en solo 2 de 5 mundos. La hipotesis de que el estimado ganaba por apretar mas se testeo y se descarto: aprieta en 59 por ciento de los casos, promedia 99 por ciento del real y no correlaciona con la ventaja. Limite que no se puede omitir: en el marcador todos los SKU mueren la misma semana, asi que el numero es el techo de la idea sobre una cartera que se discontinua entera a la vez.

## Outcome

- Signal: useful

## Source Nodes

- apply_policy
- Policy
- generate_world
- score_run