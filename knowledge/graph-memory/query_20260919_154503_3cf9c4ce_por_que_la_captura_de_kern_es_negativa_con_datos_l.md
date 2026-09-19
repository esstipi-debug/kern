---
type: "query"
date: "2026-09-19T15:45:03.081448+00:00"
question: "por que la captura de Kern es negativa con datos limpios, y donde esta la causa"
contributor: "graphify"
outcome: "useful"
source_nodes: ["kern_policy", "naive_policy", "apply_policy", "simulate", "score_run", "run"]
---

# Q: por que la captura de Kern es negativa con datos limpios, y donde esta la causa

## Answer

Todo el deficit contra el piso es UNA linea: venta perdida (+78.541 usd promedio sobre 5 mundos), contra -43.607 que Kern gasta de MENOS en procurement+holding+fixed. No es el buffer: darle la cobertura exacta del piso (lead_times=5 periodos, que es lo que fija naive_policy) le hace comprar 3.100 unidades contra 3.064 del piso y capturar PEOR (-0,483 contra -0,470), porque el dinero extra se convierte en stock terminal (26.191 contra 7.525 del piso). La causa principal es el ARRANQUE MUERTO: MIN_PERIODS_FOR_POLICY=6 y el arnes replanifica cada 4 semanas con WARMUP_WEEKS=4, asi que la primera llamada trae 4 periodos, el motor se niega, y la primera politica usable llega en la semana 8 mientras el piso arranca en la 4. Reemplazar SOLO las semanas 4-7 por la politica del piso mueve la captura de -0,470 a -0,206 y mejora en los 5 mundos: 56% de la brecha. Bajar min_periods NO es el arreglo, la corrida sale qa_failed por las invariantes de evidencia de jobs/qa.py y la politica vuelve vacia igual. El mecanismo lo muestra el perfil semanal: el oraculo carga adelante (1.359 unidades en las semanas 0-7) y CORTA al final (447, terminal 0 usd); Kern compra 0 al principio y sigue pidiendo hasta la semana 23, porque una politica de punto de reorden no tiene nocion de horizonte.

## Outcome

- Signal: useful

## Source Nodes

- kern_policy
- naive_policy
- apply_policy
- simulate
- score_run
- run