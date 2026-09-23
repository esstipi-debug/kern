---
type: "query"
date: "2026-09-23T00:08:25.663855+00:00"
question: "Alcanzan cinco mundos (semillas 1000-1004) para decidir si una palanca del marcador paga?"
contributor: "graphify"
outcome: "corrected"
source_nodes: ["generate_world", "score_run", "simulate"]
---

# Q: Alcanzan cinco mundos (semillas 1000-1004) para decidir si una palanca del marcador paga?

## Answer

NO siempre, y esta rama lo aprendio midiendo. Las cinco semillas canonicas contienen TRES de los ocho mundos donde Kern va peor (1000, 1003, 1004), asi que sobre-representan la condicion en la que cualquier palanca de tipo 'compra mas' luce grande. La palanca de ventas censuradas sale +0,0930 en 4/5 sobre las cinco y +0,0267 en 13/25 sobre las veinticinco: 3,5 veces mas chica. Las palancas anteriores (fecha de fin por SKU, nivel de servicio) sobrevivieron con cinco porque mejoraban en 5/5. Regla: el tamano de la muestra se elige por la DISPERSION del efecto, no por costumbre; una palanca que mejora en poco mas de la mitad de los mundos necesita los 25. Correr los 25 con scripts/ventas_censuradas.py cuesta 17 minutos.

## Outcome

- Signal: corrected

## Source Nodes

- generate_world
- score_run
- simulate