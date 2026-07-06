---
type: "gap"
date: "2026-07-06T12:49:05.081850+00:00"
question: "Que capacidades del puente finanzas/marketing propuesto ya existen en el codigo vs cuales son gaps reales por construir?"
contributor: "graphify"
outcome: "useful"
source_nodes: ["working_capital()", "cash_release_plan()", "markdown_price()", "classify_excess_obsolete()", "run_sop_cycle()"]
---

# Q: Que capacidades del puente finanzas/marketing propuesto ya existen en el codigo vs cuales son gaps reales por construir?

## Answer

Verificado en codigo el 2026-07-06: el "puente" finanzas/marketing que se propuso expandir esta MAS construido de lo que parece a simple vista, pero tres piezas concretas siguen sin existir. No confundir "existe el bloque" con "esta conectado como entregable".

YA CONSTRUIDO (no es gap):
- src/working_capital.py (working_capital(), cash_release_plan()) ya calcula cash-to-cash (DIO+DSO-DPO), capital de trabajo neto y cash liberado por palanca. Esta enganchado como lente OPCIONAL dentro del tool `cost_to_serve` (jobs/cost_to_serve_job.py linea ~135) cuando el cliente pasa parametros DIO/DSO/DPO -- no es un tool propio en el registry.
- src/pricing.py ya tiene markdown_price(remaining_units, periods_left, fit, current_price) -- resuelve el precio que agota el stock remanente en N periodos dada una curva de elasticidad. No lo usa nadie mas que el propio tool `pricing`.
- src/excess_obsolete.py (classify_excess_obsolete) ya clasifica sano/excedente/muerto y dimensiona el cash en riesgo (excess_value). El job jobs/excess_obsolete_job.py solo devuelve la clasificacion y una accion en texto ("liquidate / return / draw down"), NUNCA llama a pricing.markdown_price().
- jobs/fefo_job.py SI tiene una nocion de markdown (markdown_price_pct) pero es un % FIJO configurable, no la curva de elasticidad de src/pricing.py -- y solo aplica a perecederos (fefo), no a excedente/obsoleto general.
- src/sop.py (run_sop_cycle) ya modela chase/level/hybrid con costo/servicio/inventario -- el nucleo analitico de S&OP es solido.

GAPS REALES (nada de esto existe, 0% construido al 2026-07-06):
1. Ningun job cruza classify_excess_obsolete() con markdown_price() para producir un calendario de liquidacion por SKU (precio + semanas para vaciar + $ recuperado vs escrito a cero) para stock NO perecedero. Es la pieza mas barata de construir (~1 dia): ambos motores ya existen, solo falta el job que los una.
2. No existe un tool propio de cash-flow forecast ROTATIVO (13 semanas) atado al calendario real de POs/reabastecimiento -- solo existe la foto fija (ciclo promedio DIO/DSO/DPO) dentro de cost_to_serve.
3. src/sop.py no toma un calendario de promociones/marketing como input de ajuste a la demanda en la etapa de demand review.

Investigacion de metodologias canonicas para estas 3 piezas (cash forecast rotativo, markdown/clearance pricing, promo uplift, IBP vs S&OP) en curso via workflow wf_24f18e81-045; el documento final va en documentation/FINANCE_MARKETING_BRIDGE.md (pendiente de escribir).

## Outcome

- Signal: useful

## Source Nodes

- working_capital
- cash_release_plan
- markdown_price
- classify_excess_obsolete
- run_sop_cycle