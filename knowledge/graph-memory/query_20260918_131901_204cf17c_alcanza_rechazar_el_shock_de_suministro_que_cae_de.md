---
type: "query"
date: "2026-09-18T13:19:01.971590+00:00"
question: "Alcanza rechazar el shock de suministro que cae dentro del calentamiento para que el marcador no publique 'absorbido' sobre una disrupcion que no ocurrio?"
contributor: "graphify"
outcome: "corrected"
source_nodes: ["score_shock", "apply_shocks", "run_grid", "WeekOutcome"]
---

# Q: Alcanza rechazar el shock de suministro que cae dentro del calentamiento para que el marcador no publique 'absorbido' sobre una disrupcion que no ocurrio?

## Answer

No, y creerlo costo una entrega entera. La guarda de run_grid (_refuse_shocks_the_grid_cannot_measure) solo cubre la causa que se puede saber ANTES de correr: una ventana entera dentro de WARMUP_WEEKS. La causa general no se sabe hasta correr -- basta que el SKU apuntado no se pida dentro de la ventana, o que replan_every retrase la primera orden (la primera sale en ceil(WARMUP_WEEKS/replan_every)*replan_every, no en WARMUP_WEEKS). Medido con seed 4, 8 SKUs, lead_time_spike:SKU-1003:10:3:2: el carril de Kern publicaba 'absorbido / recupero si' y capture_under_shock -0,221 con total_cost identico al ultimo digito al de su corrida limpia, mientras el PISO si recibia el mismo shock -- asi que tampoco se puede rechazar por corrida, hay que declararlo por CARRIL. La senal barata y en banda es comparar las dos trazas de WeekOutcome elemento a elemento en score_shock (ShockImpact.reached): first_out_of_band_week=None ya significaba ABSORBIDO, asi que 'el carril aguanto' y 'la disrupcion nunca llego' se renderizaban igual. Con reached=False el veredicto queda vacio (recovered=None, nunca True) y el carril no publica capture_under_shock, que seria su captura LIMPIA con otra etiqueta.

## Outcome

- Signal: corrected

## Source Nodes

- score_shock
- apply_shocks
- run_grid
- WeekOutcome