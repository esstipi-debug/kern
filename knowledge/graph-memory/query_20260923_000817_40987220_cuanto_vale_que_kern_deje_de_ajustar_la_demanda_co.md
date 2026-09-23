---
type: "query"
date: "2026-09-23T00:08:17.492752+00:00"
question: "Cuanto vale que Kern deje de ajustar la demanda contra ventas censuradas (out.sold en vez de demand)?"
contributor: "graphify"
outcome: "useful"
source_nodes: ["history_frame", "WeekOutcome", "kern_policy", "naive_policy", "score_run"]
---

# Q: Cuanto vale que Kern deje de ajustar la demanda contra ventas censuradas (out.sold en vez de demand)?

## Answer

No paga. El techo -darle la demanda VERDADERA, que nadie tiene- mueve la captura +0,0394 en 14/25 mundos, IC95 [-0,0115,+0,0902]; contra +16,2 en 5/5 de la fecha de fin por SKU. El mecanismo es un empate: compra 14.371 de mercaderia para ahorrar 14.476 de quiebre, o sea otra palanca de 'compra mas' chocando contra la pared de KERN_RESIDUO_DE_LA_BRECHA. La censura es un SINTOMA: cuanta demanda esconde el archivo NO correlaciona con cuanto paga descensurar (r=+0,05); lo que correlaciona es que tan mal le va ya a Kern (r=-0,61). Medido en scripts/ventas_censuradas.py, documentation/KERN_VENTAS_CENSURADAS.md.

## Outcome

- Signal: useful

## Source Nodes

- history_frame
- WeekOutcome
- kern_policy
- naive_policy
- score_run