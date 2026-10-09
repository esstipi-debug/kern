---
type: "query"
date: "2026-09-21T20:21:22.344765+00:00"
question: "R3.3 del residuo: el stock terminal sube monotonamente con cada palanca?"
contributor: "graphify"
outcome: "corrected"
source_nodes: ["score_run", "capture", "_componentes", "generate_world"]
---

# Q: R3.3 del residuo: el stock terminal sube monotonamente con cada palanca?

## Answer

NO -- era un artefacto de medir media columna, corregido el 2026-09-21. score_run cobra procurement de toda unidad ORDENADA, asi que el capital pagado y sin vender al cierre son DOS cosas: el estante (on_hand_end de la ultima semana) y la CANIERIA (ordenado menos recibido, que sigue viajando y pesa 2,3x mas en el control). La funcion _componentes de scripts/por_que_bajo_el_piso.py media solo el estante. Con estante+canieria la secuencia publicada no es monotona en 0 de 5 mundos, y servicio_econ -- la palanca mas grande, +18,9 puntos de captura -- deja MENOS inmovilizado que proveedor_medido, que da +4,9. El estante subia porque las palancas MUEVEN capital de la canieria al estante: la razon canieria/estante cae de 2,3 a 0,5, porque un nivel de servicio mas alto compra antes y mas y al cierre eso ya aterrizo. Sobrevive, mas debil de lo publicado: toda palanca deja mas que el control en promedio, pero tres de cuatro quedan por debajo del control en 1 de 5 mundos. Regla de diseno que salio de aca: NO se redefinio stock_terminal_usd -- se agregaron canieria_usd y pagado_no_vendido_usd, porque el campo viejo no estaba mal sino incompleto para la pregunta que se le hacia; y el control de la regrabacion fue exigir que ningun campo preexistente cambiara de valor, resumen y por semilla.

## Outcome

- Signal: corrected

## Source Nodes

- score_run
- capture
- _componentes
- generate_world