---
type: "query"
date: "2026-09-18T11:34:55.126102+00:00"
question: "Como se le inyecta una disrupcion al marcador simulado sin romper 'misma semilla, mismo mundo'?"
contributor: "graphify"
outcome: "useful"
source_nodes: ["apply_shocks()", "advance()", "initial_state()", "score_shock()"]
---

# Q: Como se le inyecta una disrupcion al marcador simulado sin romper 'misma semilla, mismo mundo'?

## Answer

Con una transformacion PURA del mundo ya sorteado: src/simworld/shocks.py::apply_shocks hace dataclasses.replace(world, demand=..., real_lead_weeks=...) sobre copias, sin tocar rng, generate_world ni advance. Donde pega cada tipo lo decide advance(): lee demand en la columna t de la semana y real_lead_weeks[i,t] en el momento de EMITIR la orden (arrival = t + lead), asi que un lead_time_spike perturba la columna de emision. Dos trampas medidas: real_lead_weeks es int64 (una magnitud float hay que redondearla), e initial_state dimensiona el stock de arranque con las primeras INITIAL_STOCK_WEEKS semanas de demanda, asi que un demand_surge temprano se auto-absorbe -- shocks.py lo rechaza en vez de producir una corrida que se lee como robustez.

## Outcome

- Signal: useful

## Source Nodes

- apply_shocks()
- advance()
- initial_state()
- score_shock()