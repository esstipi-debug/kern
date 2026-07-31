# Puente simworld -> estado general del Tower — diseno

- Fecha: 2026-07-31
- Estado: aprobado en brainstorming. Sin plan, sin codigo.
- Rama: `feat/simworld-marcador` (continua el spec y plan del 2026-07-27, no los reemplaza)
- Depende de: Task 8 (corredor Kern) del plan existente, terminado. No bloquea,
  no modifica ni reordena las Tasks 8-12 ya aprobadas.
- Referencias: `docs/superpowers/specs/2026-07-27-simworld-marcador-design.md`,
  `docs/superpowers/plans/2026-07-27-simworld-marcador.md`

## El problema

`src/state/system_state.py` define seis dominios (`stock`, `prices_own`,
`prices_competitor`, `forecast`, `decisions`, `outcomes`). Los seis tienen
consumidor ya escrito y probado: `scm_agent/monitors.py` (5 monitores de
inventario + drift), `src/verify/backtest.py` (MAPE/WAPE/bias, sugerencia de
recalibracion de sigma), `src/pricing_guardrails.py`. Ninguno tiene productor en
produccion — `system_state.snapshot()` solo se llama desde `tests/`. El propio
codigo lo asume como normal: `run_all_monitors` dice explicitamente que "a
domain with no snapshot yet... makes its monitor(s) silently contribute zero
events" (`scm_agent/monitors.py:592`). Es una ausencia honesta, no un bug, pero
significa que esta mitad del Tower nunca corrio contra nada que no sea un
DataFrame de prueba escrito a mano.

## Que NO es esto

No reemplaza el `scorer.py` de las Tasks 9-10 del plan existente. Ese marcador
(captura normalizada contra el oraculo, degradacion anunciada por nivel de
suciedad, contrato de KPI portado de gym-invmgmt/AIM-Bench) sigue siendo el
marcador primario y mas rico — especifico de reposicion, con costo
descompuesto y diagnostico de latigo. Esto es **prueba de infraestructura**:
¿la plomeria general del Tower (seis dominios + `verify/backtest.py` + los 6
monitores) funciona de punta a punta con un productor real? simworld es la
fuente de verdad mas barata que ya existe en el repo para contestar esa
pregunta sin esperar al primer cliente.

## Decisiones tomadas

| # | Decision | Descartado, y por que |
|---|---|---|
| 1 | Modulo nuevo `src/state/record.py`, agnostico de simworld: `record_stock`/`record_forecast`/`record_decision`/`record_outcome`, cada una un wrapper delgado sobre `system_state.snapshot()` | Escribir DataFrames a mano desde `run_world_sim.py` (repite las columnas exactas en cada llamador; F2/F3 no tendrian una API compartida que enchufar despues) |
| 2 | Punto de integracion: `simulate()` (Task 11) recibe un `state_store: StateStore \| None = None` opcional. `None` (default) = cero escrituras, ningun test ni golden existente cambia | Escribirlo dentro de `runners.py` (mezclaria I/O de estado con traduccion pura de politica) o en `scorer.py` (mezclaria el marcador de costo con el puente de estado) |
| 3 | `stock` y `outcomes` se escriben **cada semana**; `forecast` y `decisions` solo en semanas de replanificacion (cuando `plan()` corre de verdad, no en las semanas donde se reaplica la politica sostenida) | Escribir forecast/decisions cada semana replicando el mismo numero (falsea el join: parece que Kern predijo de nuevo cuando en realidad la politica seguia congelada) |
| 4 | `cycle_id` = numero de semana como string (`"0"`, `"1"`, ...) | Un prefijo tipo `"w1"` cae al orden lexicografico de `_cycle_sort_key` y `"w10"` ordena antes que `"w2"` -> `CycleOrderError` en la semana 2 |
| 5 | `outcomes` lleva **dos metricas** por SKU y semana: `actual_qty` (demanda verdadera, `WeekOutcome.demand`) y `sold_qty` (venta observada, censurada por quiebre, `WeekOutcome.sold`) | Una sola metrica (esconde exactamente el sesgo que un cliente real sufre: si Kern subestimo y hubo quiebre, `sold` le da la razon aunque haya pedido mal) |
| 6 | El backtest de forecast usa `actual_qty` (verdad no censurada), nunca `sold_qty` | Medir precision de forecast contra una venta ya recortada por el propio quiebre premia a un forecast malo que por suerte no genero quiebre ese periodo |
| 7 | `decisions.order_qty` = la orden que `apply_policy` efectivamente emite esa semana (0.0 explicito en las SKU sin orden, no una fila ausente) | Omitir las semanas sin orden litera "sin senal" como si Kern no hubiera decidido nada, cuando "no pedir" es la decision |
| 8 | `Policy` gana un campo `forecast: dict[str, float]`, poblado en `kern_policy()` (Task 8, aun sin implementar) desde `SkuRecommendation.forecast` | Derivar el forecast desde `reorder_point`/`order_up_to` (son una politica de reposicion, no una prediccion de demanda; confundirlos rompe la semantica que `verify/backtest.py` espera) |
| 9 | `tool` = `"inventory_optimization"` en `decisions` y `outcomes` (el `job_type` real que `kern_policy(forced=True)` ya pasa al orquestador) | Un nombre inventado por el arnes (`"kern_simworld"`) no es una herramienta registrada; `match_decision_actuals` exige que `decisions.tool` y `outcomes.tool` coincidan, y usar el nombre real deja la fila legible fuera de este arnes tambien |

## Advertencia interpretativa (documentada, no escondida)

`match_decision_actuals` compara `order_qty` contra `actual_qty` periodo a
periodo. Para una politica de punto de reorden esto **no es** una metrica de
precision: la orden es deliberadamente discontinua (se pide en bloque para
cubrir varios periodos, no para igualar la demanda de esa semana sola), asi que
un MAPE alto ahi es esperable y no indica que Kern decida mal. Sirve para
probar que el join y el calculo corren de punta a punta con datos reales, no
como una segunda version del marcador de costo (Tasks 9-10 siguen siendo la
medida honesta de si la decision fue buena). Se documenta aca para que nadie,
en una lectura futura del reporte, confunda "MAPE de decisions alto" con
"Kern decide mal".

## Arquitectura

```
run_world_sim.simulate(..., state_store=StateStore(aislado))
  cada semana:
    -> record_stock(store, week, on_hand, reorder_point vigente, avg_daily_demand)
    -> record_outcome(store, week, actual_qty=demand, sold_qty=sold)
  solo semanas de replan:
    -> record_forecast(store, week, forecast_qty=Policy.forecast)
    -> record_decision(store, week, order_qty=apply_policy(...), tool="inventory_optimization")

Al final de la corrida (o en un test de integracion aparte):
  scm_agent.monitors.run_all_monitors(store=store)        -> deben salir eventos, no vacio
  src.verify.backtest.run_forecast_backtest(store=store)   -> deben salir resultados, no vacio
  src.verify.backtest.match_decision_actuals(..., store=store) -> deben salir observaciones
```

`StateStore` siempre aislado (`StateStore(tmp_path)` o equivalente), nunca
`default_store()` — datos sinteticos jamas tocan el estado de produccion ni el
Tower real. Cero cambios a `DOMAIN_COLUMNS`: `period`, `order_qty`, `actual_qty`,
`sold_qty` entran como columnas extra bajo `strict=False`, la misma convencion
que `verify/backtest.py` ya declara para si mismo.

## Que prueba esto, en una frase

Que si mañana llega el primer cliente y algo empieza a llamar
`system_state.snapshot()` con datos reales, el Tower (monitores + backtest) no
se va a encontrar ejercitando ese camino por primera vez contra produccion.

## Que NO entra (YAGNI)

- No se toca `prices_own` / `prices_competitor` (sin bucle de precio en
  simworld; ya excluido en el spec original).
- No se construye backfill de dataset real (Superstore/Olist) ni
  instrumentacion en vivo — quedan como F2/F3, misma API `record_*`, otra
  historia.
- No se registra este puente como capacidad de cliente ni se expone en
  `scm_agent/tools.py` — es arnes, igual que el resto de simworld.
- No se modifica `scorer.py` ni el contrato de captura/degradacion anunciada.

## Pruebas

- Unitaria: `record_stock`/`record_forecast`/`record_decision`/`record_outcome`
  producen un DataFrame que pasa la validacion de `system_state.snapshot()` sin
  columnas faltantes.
- Unitaria: `cycle_id` como numero de semana ordena correctamente aun mas alla
  de la semana 9 (`"10"` despues de `"9"`, no antes).
- Integracion: una corrida corta (misma semilla del golden de Task 11) con
  `state_store` seteado, seguida de `run_all_monitors`, `run_forecast_backtest`
  y `match_decision_actuals` sobre ese store — los tres deben devolver listas
  no vacias. Este es el criterio de aceptacion completo de la Task 13: si
  cualquiera de los tres vuelve vacio, el puente no cerro el lazo.
- Propiedad: correr dos veces con `state_store=None` produce exactamente el
  mismo resultado que antes de esta Task (no-op garantizado por default).
