---
type: "query"
date: "2026-09-23T10:41:12.978639+00:00"
question: "Cuantos mundos hacen falta para decidir si una palanca del marcador paga?"
contributor: "graphify"
outcome: "corrected"
source_nodes: ["score_run", "generate_world", "simulate", "naive_policy"]
---

# Q: Cuantos mundos hacen falta para decidir si una palanca del marcador paga?

## Answer

Depende de DONDE vive la ventaja de la palanca, no de una costumbre. Medido sobre 25 mundos (2026-09-23): las palancas de 'compra menos' escalan -- horizonte_estimado +0,1618 -> +0,1309 en 23/25 con p<0,001, horizonte_real +0,1348 -> +0,1413 en 25/25 -- y las de 'compra mas' NO: servicio_econ +0,1887 -> +0,0512 (p=0,111), y plazo_obs, sigma_obs, proveedor_medido y todo CAMBIAN DE SIGNO. La correlacion contra la captura del control separa las dos familias sin solapamiento (compra menos -0,23..-0,17; compra mas -0,77..-0,42). El mecanismo: una palanca de compra-mas paga donde Kern ya sub-compra, o sea en la cola mala que las cinco semillas canonicas sobre-representan (tres de los ocho peores mundos); una de compra-menos arregla algo que pasa en todos. Documento: KERN_PALANCAS_SOBRE_25_MUNDOS.md; scripts/palancas_sobre_25_mundos.py compara.

## Outcome

- Signal: corrected

## Source Nodes

- score_run
- generate_world
- simulate
- naive_policy