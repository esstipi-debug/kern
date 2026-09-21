---
type: "query"
date: "2026-09-21T21:22:32.411515+00:00"
question: "KERN_POR_QUE_BAJO_EL_PISO: el fin de horizonte castiga mas a Kern que al piso?"
contributor: "graphify"
outcome: "corrected"
source_nodes: ["score_run", "_componentes", "naive_policy", "oracle_orders"]
---

# Q: KERN_POR_QUE_BAJO_EL_PISO: el fin de horizonte castiga mas a Kern que al piso?

## Answer

SI, pero el documento lo afirmaba con el numero equivocado y con DOS errores encadenados, corregidos el 2026-09-21. Citaba "4.572 contra 7.525 de terminal hoy": 4.572 es el Kern de ANTES del arreglo de la cobertura provisional (hoy es 9.209), y con ese numero la conclusion sale al reves, porque 4.572 es MENOS que los 7.525 del piso. Con el numero completo (estante mas canieria) la conclusion se sostiene y se refuerza: Kern inmoviliza 30.440 contra 25.017 del piso, en 4 de 5 mundos (con el estante solo eran 3 de 5). Encaja con KERN_HORIZONTE_DE_LA_VARA: acreditar el borde deja a Kern PEOR justamente porque inmoviliza mas que el piso. Dos cosas de metodo que salieron de aca. UNA: las dos lineas base de ese documento NO son simetricas -- el DESPUES se regraba con el codigo actual, pero el DIAGNOSTICO esta congelado pre-arreglo y ese codigo ya no existe, asi que sus cifras de cierre no se pueden completar y quedan marcadas como estante solo. DOS: la correccion tiene que cubrir tambien donde el numero incompleto NO enganaba -- forzar la cobertura del piso manda el dinero al estante y casi no mueve la canieria (21.230 a 21.167), asi que ahi el estante daba la respuesta correcta; un documento que corrija solo donde le conviene no es una correccion.

## Outcome

- Signal: corrected

## Source Nodes

- score_run
- _componentes
- naive_policy
- oracle_orders