# Disrupciones y desajustes entre áreas — qué capacidad tiene Kern hoy, qué puede lograr, y qué marca la diferencia

**Fecha:** 2026-09-17 · **Rama:** `claude/keen-goodall-jfnqo9` · **PR:** [#369](https://github.com/esstipi-debug/kern/pull/369)

> Pregunta de origen: *¿qué pasa con Kern cuando hay disrupciones de ventas o inventario, o hay desacuerdos o desajustes con el área de transporte?* Este documento es la investigación que siguió a esa pregunta. **Todo lo que afirma está verificado contra el código de esta rama**, y los dos experimentos del Anexo A corren con semilla fija y se reproducen en segundos.
>
> Regla de lectura, la misma de `KERN_BATERIA_20_TAREAS.md`: un número que sube en la vara que uno mismo propone merece desconfianza por defecto. Por eso el §4 empieza por los hallazgos que **bajan** la lectura, no por los que la suben.

---

## 1. Resumen ejecutivo

Kern tiene dos fortalezas reales en este terreno y tres huecos estructurales.

**Fortalezas.** (1) **Valorar una decisión bajo tensión**: el consejo de sombreros y `cross_impact` convierten "las áreas no se ponen de acuerdo" en un veredicto medido — `ejecutar` / `bilateral` / `reunion` / `indeterminado` — con umbrales que el operador declara y con la honestidad de decir `no_evaluable` cuando falta un insumo. Desde #369, transporte es una de esas áreas. (2) **Medir lo que el sistema del cliente no ve**: sobre 98.666 pedidos reales, el OTIF por llegada es 92,9% y por despacho 99,2% — 6,2 puntos de servicio que ningún ERP del canal registra, y Kern los mide componiendo ERP + transportista (`src/connectors/arrivals.py`).

**Huecos.** (1) **El bucle de sensado no tiene motor en producción.** Los monitores del Control Tower existen, están probados y están cableados a un endpoint — pero el único cron que lo llamaba nunca corrió y está apagado, y ningún job de producción escribe el snapshot de `stock` que los monitores leen. Están construidos y duermen. (2) **Nadie mide la respuesta a una disrupción.** El gemelo digital inyecta tres tipos de shock; el plan `resilience_stress_test` está escrito hasta los tests; no hay una línea de código. (3) **El tool principal de reposición no ve el tránsito.** Una OC atrasada por transporte — la disrupción de transporte más común — es invisible para `inventory_optimization`; el neteo del en-tránsito vive solo en los conectores.

**La tesis.** La capacidad diferencial alcanzable a corto plazo **no es un tool nuevo**. Es cerrar cuatro cables (§5, Nivel 1) y construir un arnés que **mida** la respuesta a un shock (§5, Nivel 2) — porque "Kern captura X% de la brecha entre un punto de reorden fijo y un oráculo *bajo una disrupción*" es un número que ningún competidor del segmento publica, y el repo ya tiene el 80% de la maquinaria para producirlo. Atribuir culpa entre almacén, forwarder y transporte (Nivel 3) es lo que más pide el cliente y lo que menos se puede computar sin una definición firmada — y el spec para esa firma ya existe.

---

## 2. El mapa: qué existe, verificado

Organizado por lo que hace falta para responder a una disrupción — sentir, decidir, ejecutar, verificar, equilibrar — más las dos capas específicas de la pregunta: medir el servicio y coordinar áreas.

| Capa | Qué hay | Dónde | Estado real |
|---|---|---|---|
| **Sentir** (A1) | 6 monitores puros con dedup: `rop_breach`, `stockout_projected`, `excess_growing` (tendencia de 2 snapshots), `forecast_error_out_of_band`, `lead_time_drift`, `competitor_price_move` | `scm_agent/monitors.py`, `config/monitors.yaml` | Construidos, 39 tests. **Sin driver en prod** (§4 H1). Dos de seis **emiten y nadie consume** (H2) |
| **Decidir** (A2) | Ruteo evento → tool como dato, 7 rutas, 4 constructores de params | `config/event_routing.yaml`, `scm_agent/event_intent.py` | Vivo. Un evento corre el orquestador real (prepare → run → QA → deliver): "QA falla ⇒ no hay entregable" aplica también a lo automático |
| **Ejecutar** (A3) | Tiers T1 (auto, auditado) / T2 (un clic, TTL 900 s, expira) / T3 (escalado con SLA); writeback irreversible se degrada a humano aunque sea T1 | `scm_agent/autonomy.py`, `POST /api/approvals/{id}` | Vivo |
| **Verificar** (A4) | Backtest predicho vs real, informe de confiabilidad por tool, promoción T2→T1 solo por N ciclos de evidencia, degradación inmediata | `src/verify/`, `scm_agent/autonomy_promotion.py`, `config/autonomy.yaml` | Construido. **Solo verifica pronósticos**, no decisiones (H3). La promoción no está en ningún job |
| **Equilibrar** (A5) | Plan integrado v1: pronóstico → shaping por precio → plan de compra → 3 checks de coherencia → calendario de caja | `src/sop_engine/`, `jobs/integrated_plan.py` | Vivo. Sin solver conjunto (CP-SAT esperaba evidencia de A4 que no llegó) |
| **Shock de ventas → plan** | `sop` acepta un calendario de lifts por período (`promo_lift`) y re-corre chase/level/hybrid | `src/sop.py::apply_promo_lift` | Vivo. Es el único tool que toma una disrupción de demanda **como entrada** |
| **Simular la disrupción** | Gemelo multi-eslabón con `supplier_outage`, `lead_time_spike`, `demand_surge`; bullwhip a propósito | `src/digital_twin.py`, tool `digital_twin` | Vivo. **Nadie mide TTR ni unidades perdidas sobre sus trazas** (H5) |
| **Riesgo** | Matriz 5×5, EMV, FMEA, brecha TTR > TTS (Simchi-Levi), mitigaciones rankeadas | `src/risk.py`, tool `risk` | Vivo. TTR/TTS son **tipeados a mano**, no medidos. Ninguna mitigación lleva *cuándo* (batería T13) |
| **Noticias de proveedores** | GDELT como proxy de tamizaje, solo lectura, nunca probabilidad | `src/disruption.py`, tool `disruption_scan` | Vivo, honesto sobre sus límites |
| **Recurrencia** | Episodios por hueco temporal, perfiles crónico/episódico, denominador común con corrección BH | `src/patterns.py` (spec `2026-07-25-pattern-scan`) | **Motor construido, job y tool no**. Decisiones 7-9 reconciliadas |
| **Coordinar áreas** | 5 sombreros sobre (Q, SL), mapa de tensión, settlement con pesos = política del operador, `cross_impact` con veredicto de objeción, `org_friction` (fábrica oculta, ratchet, vacío de dueño) | `src/hats.py`, `src/hat_council.py`, `src/cross_impact.py`, `src/objection.py`, `src/org_friction.py` | Vivo. Transporte entra con #369, condicional a peso + distancia |
| **Medir servicio** | Fill rate, OTIF de salida (SCOR RL.1.1), venta perdida, con procedencia `observed` / `client_reported` / `not_measured` / `model_implied`; composición ERP + transportista | `src/order_fulfillment.py`, `src/connectors/arrivals.py`, `shippit.py`, `sendle.py`, `starshipit.py`, tool `service_scan` | Vivo. Es la capa más madura de toda la pregunta |
| **Cadena de hitos** | 6 hitos cerrados (`order_created` → … → `carrier_pickup` → `arrived`), dwell por tramo, inversiones rechazadas por QA, `uninstrumented_late` como agujero de reloj y no como causa | `src/connectors/milestones.py`, `src/fulfillment_milestones.py`, `jobs/fulfillment_chain_job.py` | **Construido y probado, tool sin registrar** (registro congelado en 60, ADR-2026-09 §3) |
| **Definiciones firmadas** | Predicados cerrados sobre hitos (`LOAD_ONTIME = load_end ≤ 19:00`), partes `planning/warehouse/transport/forwarder`, firma por parte, sin firma ⇒ HANDOFF | spec B `2026-09-10-fulfillment-definitions-design.md` | **Spec cerrado (D1–D10), cero código** |
| **Atribución de causa** | Árbol `CTRL == SHP/FWD` ⇒ `CAUSE` | spec C | **No escrito** |
| **Transporte: costo y red** | Modo por envío (parcel/LTL/FTL/intermodal), flete por lane, VRP (savings, sweep), flow map con tránsito por tramo y capital aguas abajo | `src/logistics/`, `src/flow_map.py`, tools `transportation`, `vehicle_routing`, `flow_map` | Vivo |
| **Disputa con transportista** | Trigger `DISPUTE` → claims / account manager, SLA mismo día; 7 playbooks de voz (ETA, demurrage, OS&D…) que **solo toman hechos**, nunca admiten responsabilidad | `nosilo_core/escalation.py`, `src/voice/` | Vivo en dry-run; el marcador real (ElevenLabs) se niega a marcar hasta cablear credenciales |
| **En-tránsito** | Neteo contra posición de inventario (NFP de DDMRP), con `None` cuando no se pudo netear — nunca cero | `src/connectors/replenish.py::plan_replenishment(on_order_by_sku=…)` | Vivo **solo en el camino de conectores** (Odoo, Shopify, planilla). No en `inventory_optimization` (H4) |
| **El marcador** | Mundo con verdad conocida: demanda real, lead times reales con OTIF por proveedor, suciedad graduable; piso vs Kern vs oráculo; KPIs `fill_rate`, `bullwhip`, `capture`, `degradation` | `src/simworld/`, `src/verify/barrido.py`, `carteras.py` | Vivo. **No inyecta shocks** (H8). Capturas publicadas en escala chica: **−0,446 (L0)**, +0,437 (L1) |

---

## 3. Lo que dice la teoría del grafo, y cómo mapea

El grafo de libros (27 obras curadas) ancla la pregunta en tres lugares, y cada uno cae sobre una parte distinta del código:

| Teoría | Dónde está en el grafo | Qué dice | A qué parte de Kern le corresponde |
|---|---|---|---|
| **Coordinación = incentivos + información** | Chopra & Meindl cap. 10: *Misaligned Incentives*, *Supply Chain Coordination*, *Bullwhip Effect*, *CPFR* | "Los incentivos locales llevan a cada eslabón a optimizar su propio objetivo en vez de la ganancia total." La información distorsionada amplifica pedidos (bullwhip) | Los **sombreros** son la capa de incentivos (cada área con su utilidad sobre la misma decisión). Los **monitores + `src.state`** son la capa de información. `src/simworld/scorer.py` ya mide bullwhip |
| **Readiness → Response → Recovery** | Khan et al. (2022) cap. 2: *Readiness, Response and Recovery*, *Mitigation Capability*, *Disruption Information Sharing* | "Monitorear **y compartir** información de disrupciones para que se mueva rápido entre partes" | Sentir (A1) + decidir (A2) es *response*; **recovery no se mide** (H5) |
| **TTR vs TTS** | Simchi-Levi, en `src/risk.py` (*Risk Forecasting and Resilience Planning*, *Flexibility and Agility*, *Supply Chain Resilience*) | La exposición es el tiempo en que la cadena no aguanta menos el tiempo que tarda en recuperarse | `risk.py` los toma tipeados. El gemelo podría **medirlos** |
| **Consolidación de entrada** | Christopher cap. 7 *In-Bound Consolidation*; Ivanov *Consolidation* | Llenar el camión sin derramarlo es una decisión de cantidad, no de ruta | Es exactamente la utilidad del quinto sombrero (#369): serrucho en Q |

Dos ausencias del grafo que importan para el gate de citas (`scm_agent/citation_gate.py`, `MIN_CITATIONS = 2`): `--bridge "S&OP"` y `--bridge "expedite"` **no anclan a ningún nodo**. Cualquier capacidad nueva que modele expedite (§5, Nivel 3d) va a necesitar un ancla honesta o declararse `_NO_CONCEPT_MAP_BY_DESIGN` con motivo medido, como hizo `org_friction_audit`. No fabricar un ancla.

---

## 4. Hallazgos duros

Ordenados por cuánto cambian la respuesta. Cada uno con el archivo que lo prueba.

### H1. El bucle de sensado no tiene motor en producción

`MONETIZATION_BRIEF.md` (24-jul) dice: *"Ya en producción: los 3 monitores + dedup; el endpoint `POST /api/jobs/run-scheduled` ya corre `run_all_monitors` inline en cada tick (los monitores **ya sensan en prod**)."* Cada eslabón de esa frase es cierto y la conclusión es falsa:

- El endpoint existe y corre los monitores cuando lo llaman (`webapp/app.py:2108`).
- El **único** llamador programado era `.github/workflows/price-watch-cron.yml`. Ese workflow **nunca corrió con éxito** — le faltó el secreto `KERN_JOBS_API_KEY` desde el 19-jul — y se **apagó** el 15-sep (#362), con el motivo escrito en el archivo: *"dos meses muerto sin que nadie lo extrañara es un dato sobre cuánto se lo necesita"*.
- Aunque alguien lo llamara, los monitores de inventario leen el dominio `stock` de `src.state`. En producción, **ningún job escribe ese snapshot**: los únicos escritores de `record_*` fuera de tests son `jobs/forecast_cycle.py` (dominios `forecast` y `outcomes`) y `examples/run_world_sim.py` (el arnés). `webapp/app.py:256` lo documenta como *"empty Tower on day one is expected"*. El runner concierge (`jobs/monitors_job.py::run_concierge_alerts`) espera que *"el operador cargue el CSV del merchant en `src.state`"* — a mano.

**Lectura:** hoy Kern **no siente** una disrupción de inventario por sí solo. La siente cuando un humano corre un tool. Toda la maquinaria para que la sienta sola está construida y probada; le faltan un reloj y una fuente.

### H2. Los dos monitores que detectan las disrupciones de la pregunta emiten y nadie consume

`forecast_error_out_of_band` (la venta se fue de banda respecto del pronóstico) y `lead_time_drift` (el proveedor o el transporte tardan más que su línea base) **son exactamente** "disrupción de ventas" y "desajuste con transporte". Ambos emiten eventos reales, con dedup, al ledger. **Ninguno tiene ruta en `config/event_routing.yaml`**, a propósito: el encabezado del YAML explica que rutearlos a un tool exigiría fabricar una historia de demanda desde 1-2 agregados, violando la regla 7 (procedencia total) y la 14 (ningún cap silencioso). El consumidor previsto — "A4's `src/verify/backtest.py` es el hogar natural" — nunca los consumió.

**Lectura:** la decisión de no rutear fue correcta. Lo que falta no es una ruta a cualquier tool, sino una ruta a un tool que pueda actuar **con la serie real** (§5, N1a).

### H3. A4 verifica pronósticos, no decisiones

`src/verify/backtest.py` empareja `forecast_qty` contra `outcomes`. El camino `decisions` (¿la cantidad que Kern recomendó fue la correcta?) **no está construido** — `match_decision_actuals` necesita un `decision_value_column` por tool que no existe, y el spec del Tower (`2026-07-21-tower-a4-reliability-design.md`) lo dejó explícitamente fuera. Consecuencia en cadena: la promoción T2→T1 por evidencia (regla de oro 11) no puede ocurrir nunca para reposición, porque la evidencia que exige no se genera. Y `config/autonomy.yaml` pide 4 ciclos consecutivos ≥ 0,92 de precisión de algo que nadie mide.

### H4. El tool principal de reposición no ve el en-tránsito

`grep -n "transit\|on_order\|receipt" jobs/inventory_optimization.py` devuelve **vacío**. El neteo contra la posición de inventario (NFP = on-hand + on-order − demanda calificada, `src/ddmrp.py`) existe y está bien hecho — con la disciplina de que un SKU que no está en el mapa sale `on_order=None`, nunca cero — pero solo en `src/connectors/replenish.py::plan_replenishment`, que usan `odoo_job`, `shopify_job` y la planilla (`test_neteo_en_transito.py`, kern#251). El tool CSV — el que rutea un brief genérico, el que corre `simworld`, el que dispara `rop_breach` — dimensiona contra `on_hand` solo.

**Lectura:** una OC que transporte atrasó dos semanas es, para `inventory_optimization`, indistinguible de una OC que no existe. Va a **volver a pedir**. Esa es la disrupción de transporte más frecuente y el tool central no la ve.

> **Estado (2026-09-17, misma rama, N1d):** cerrado. `jobs.intake.stock_position` lee `on_hand` / `on_order` del archivo (última fila por SKU), el job calcula `inventory_position` con la identidad de `src/ddmrp.py` y emite `order_now` / `order_qty_now` por SKU; el en-tránsito netea el disparador, no solo la cantidad, y sin columna de en-tránsito todo entregable dice que NO pudo netear. Prueba: `tests/test_inventory_en_transito.py`.

### H5. El gemelo inyecta shocks; nadie los mide

`src/digital_twin.py` tiene `Disruption(kind, target, start, duration, magnitude)` con tres tipos y devuelve trazas completas por período. El plan `docs/superpowers/plans/2026-07-20-resilience_stress_test.md` está escrito hasta el nivel de los **tests con instancias verificadas a mano** (`test_time_to_recover_measures_first_in_band_period`, etc.), en 3 tasks, "~1 sesión, casi pura composición". `ls src/resilience.py` → no existe.

Anexo A.1 lo prueba en 40 líneas sobre el gemelo tal cual está: un corte de proveedor de 10 períodos sin holgura de dimensionamiento hunde el fill a 0,00, pierde 803 unidades (7,4% de la demanda posterior al shock) y tarda 9 períodos en recuperarse. Ese es el TTR medido que `risk.py` hoy pide tipeado.

Una corrección al plan, encontrada al correrlo: `lost_units(baseline_served, disrupted_served, …)` mide pérdidas contra el **servido del baseline**. Bajo un `demand_surge` eso da ~0 aunque el fill caiga a 0,27, porque la demanda subió y el servido también. Las unidades perdidas bajo un surge son `demanda_disrupta − servido_disrupto`, no `baseline − servido`. El plan hay que ajustarlo en la Task 1 antes de implementarlo, no después.

### H6. Transporte: medible sí, representable desde #369, atribuible no

- **Medible.** `service_scan` da OTIF de salida con procedencia, y `coverage_of_arrivals` compone ERP + transportista declarando de dónde sale cada campo. Sobre Olist: 6,2 puntos de brecha entre despacho y llegada.
- **Representable.** El quinto sombrero (#369) pone el flete en la misma decisión (Q, SL). Sobre un SKU de 12 kg/u a 900 km, el ideal de transporte cae en Q = 1.658 — el camión lleno sin derramar — que no es ni el máximo de comprador ni el mínimo del CFO. Y una propuesta de 1.600 → 1.700 unidades pasa de `ejecutar` a `bilateral`: transporte objeta +0,89 usd/u de flete.
- **Atribuible, no.** El spec B lo dice textual: *"sin firma, no hay 'el almacén llegó tarde'. Hay un HANDOFF para sentar a las partes."* Tiene diez decisiones cerradas y cero código. El spec C (causa) no está escrito. Y la cadena de hitos que ambos necesitan (`fulfillment_chain`) está construida, probada, y **sin registrar** — la única línea que la separa de ser ruteable es una excepción al ADR-2026-09 §3.

### H7. La recurrencia está construida y desconectada

`src/patterns.py` responde "¿este quiebre es crónico o episódico, y qué tienen en común los crónicos?" — con corrección de Benjamini-Hochberg para no inventar un denominador común por comparaciones múltiples. Lee del `EventLedger` **o** de un CSV del cliente. No tiene job, no tiene tool, y su cadencia semanal (decisión 9) nunca se cableó. Es el eslabón entre "hubo una disrupción" y "esta disrupción es estructural": sin él, cada evento se trata como el primero.

### H8. El marcador es el arnés correcto y no inyecta shocks

`src/simworld/world.py` ya modela lo que importa: cada proveedor tiene un `otif` sorteado entre 0,55 y 0,95 y `real_lead_weeks` realiza atrasos de 1-3 semanas contra la promesa; el estado lleva `in_transit`. El marcador mide `fill_rate`, `bullwhip`, `capture` (0 = igual al punto de reorden fijo, 1 = igual al oráculo) y `degradation` (cuánto costó la suciedad y cuánto de eso Kern avisó). `src/verify/barrido.py` agrega N mundos en una distribución con peor caso. **No hay forma de inyectar un shock** — ni un salto de lead time en una ventana, ni un surge de demanda — y por lo tanto no hay "captura bajo disrupción".

Y hay que decir el número publicado (`NO_SILO_KERN_LOOP.md`): en escala chica y semilla 4, la captura es **−0,446 en L0** (datos limpios: Kern **peor que el punto de reorden fijo**) y +0,437 en L1. El propio documento lo publica con su caveat de escala. Cualquier medición de resiliencia se va a leer sobre esa base, y conviene saberlo antes de construirla: si Kern pierde contra el piso sin shock, medir con shock va a mostrar más de lo mismo hasta que se entienda por qué.

### H9. Lo que el gemelo y los sombreros ya componen

Anexo A.2: una traza de demanda del gemelo con un surge ×2 de 12 semanas, leída como CSV por el consejo de cinco sombreros. Lo que se mueve: la demanda anual sube 20%, la desviación semanal casi se duplica (79 → 150), Q* pasa de 1.279 a 1.401, SL* baja de 97,5% a 95,0%, y la concesión de **comercial** pasa de 0,04 a 0,10 — comercial es quien paga el shock en la reconciliación. El choque más caro crece de −44.430 a −51.250 usd. Es decir: **una disrupción de ventas ya se puede traducir en "quién cede cuánto" sin escribir código nuevo**; lo que falta es el cable que lo dispare solo (H1) y el arnés que lo puntúe (H8).

---

## 5. Qué capacidad se puede lograr

Tres niveles, del más chico al más grande. Cada uno dice qué habilita, qué toca, cuánto es, y qué **no** resuelve. Los niveles 1 y 2 **no tocan el registro congelado**: no agregan ningún tool.

### Nivel 1 — Cerrar cables (días, cero tools nuevos)

| | Qué | Toca | Habilita |
|---|---|---|---|
| **N1a** | Rutear `forecast_error_out_of_band` y `lead_time_drift` — pero **con la serie real**, no fabricada: el constructor de params lee el historial de `forecast_cycle` / `outcomes` de `src.state` y lo entrega como CSV al tool `forecast` (error) o `sop` con `promo_lift` derivado (drift). Respeta reglas 7 y 14 | 2 entradas en `event_routing.yaml`, 1-2 constructores en `event_intent.py`, tests | Las dos disrupciones de la pregunta dejan de ser eventos huérfanos |
| **N1b** | Un reloj para `MONITORS_JOB`: cron dedicado (`monitors-cron.yml` con el secreto que `price-watch-cron` nunca tuvo) o `fly machine run --schedule hourly`. Regla 9: sigue siendo one-shot | 1 workflow + 1 secreto de repo (lo carga un admin, no el código) | El Tower deja de dormir |
| **N1c** | Que los jobs que ya leen `on_hand` (`odoo_job`, `excel_replenishment_job`, `channel_replenishment_job`) escriban un snapshot `stock` vía `record_stock` al terminar | 3 llamadas + tests | Los monitores tienen qué leer. Sin esto, N1b corre sobre vacío |
| **N1d** | Columna opcional `on_order` / `en_transito` en el intake de `inventory_optimization`, neteada con `net_flow_position`, con la disciplina de `test_neteo_en_transito`: **el valor tiene que moverse**, `None` cuando no viene | `jobs/intake.py` alias, `jobs/inventory_optimization.py`, tests | El tool central deja de recomprar lo que transporte atrasó. Es el fix de H4 |

**Riesgo:** N1a es el único con decisión de diseño real (a qué tool rutear cada uno). **Lo que no resuelve:** nada de esto mide si la respuesta fue buena.

### Nivel 2 — Medir la respuesta (1-2 semanas, cero tools nuevos salvo el que ya está planificado)

| | Qué | Toca | Habilita |
|---|---|---|---|
| **N2a** | `resilience_stress_test` según el plan existente (3 tasks, tests escritos), **con la corrección de `lost_units` bajo surge** (H5). Alimenta TTR medido a `risk.py` | `src/resilience.py`, `jobs/resilience_stress_test_job.py`, registro (excepción ADR §3, es el tool planificado desde julio), `citation_gate` con las 6 anclas verificadas | TTR/TTS medidos en vez de tipeados. Cierra SCOR AG |
| **N2b** | **Shocks en el marcador**: `generate_world(..., shocks=(Shock(kind, sku_or_supplier, start_week, weeks, magnitude),))` que perturba `real_lead_weeks` o `demand` **después** del sorteo base (misma semilla ⇒ mismo mundo + shock), y `score_run` con TTR y unidades perdidas post-shock. `barrido.py` ya agrega N mundos con peor caso | `src/simworld/world.py`, `scorer.py`, `examples/run_world_sim.py --shock`, tests | **"Captura bajo disrupción"**: el número que marca la diferencia. Y el que va a exponer H8 antes que cualquier cliente |
| **N2c** | Camino `decisions` de A4: `match_decision_actuals` con `decision_value_column` por tool (empezar por `inventory_optimization.reorder_point`), y que `handle_event` registre la decisión en `src.state` al correr | `src/verify/backtest.py`, `scm_agent/event_intent.py` | La promoción T2→T1 por evidencia deja de ser imposible. Regla 11 se vuelve operable |

**Riesgo:** N2b puede devolver capturas negativas bajo shock. Ese es el resultado que hay que **querer** ver. **Lo que no resuelve:** nada de esto atribuye culpa.

### Nivel 3 — Atribuir y coordinar (semanas; **decisiones de producto**, cada una una fila en el ADR)

| | Qué | Toca | Habilita |
|---|---|---|---|
| **N3a** | Registrar `fulfillment_chain` (borrar un skip, 7 lugares) y el job + tool de `pattern_scan` (motor listo, decisiones 7-9 cerradas) | Registro 60 → 62, ADR §3, `how_it_works`, README | Dwell por tramo desde un brief; "¿esto es crónico?" |
| **N3b** | Spec B `fulfillment_definitions`: predicados firmados, `HANDOFF` para sentar a las partes cuando no hay firma | Nuevo módulo + store + tool (spec §5-§11 ya lo dice todo) | Convierte "nos echamos la culpa" en "nadie firmó qué es tarde" |
| **N3c** | Spec C (causa): el árbol `CTRL == SHP/FWD ⇒ CAUSE` sobre flags firmados. **Escribir el spec primero**; B se detiene en `true/false/unmeasurable` a propósito | Spec + módulo | La atribución que el cliente pide — y la única que se puede defender: sobre definiciones que las partes firmaron |
| **N3d** | Expedite en el sombrero transporte: utilidad no plana en SL (cortes esperados × tarifa de expedite **declarada**). Hoy es un límite declarado del modelo | `src/hats.py`, `FreightProfile.expedite_rate`, tests; ancla L3 (§3: no existe, hay que resolverlo honestamente) | Transporte y comercial dejan de estar de acuerdo por omisión en SL |

---

## 6. Lo que no es computable, y no conviene fingir que sí

- **La reunión.** `cross_impact` dice si hace falta convocarla y quién objeta. No la reemplaza.
- **La firma.** Spec B registra `{party, name, signed_at}` tipeado por el operador. No es criptografía ni un workflow de aprobación; es un registro de quién dijo que sí.
- **La culpa sin definición.** Sin pack firmado, cualquier RCA de atraso es opinión. Kern devuelve HANDOFF, no un veredicto.
- **La negociación con el transportista.** Los playbooks de voz toman hechos (ETA, free days, OS&D); nunca admiten responsabilidad ni ofrecen arreglo. El dial real está apagado por diseño hasta que exista el cliente.
- **La demanda que no fue.** `outliers.py` limpia la historia y `sop` acepta un lift; ningún módulo *predice* una disrupción. `disruption_scan` es tamizaje, y lo dice.

---

## 7. Recomendación

**Nivel 1 completo + N2b, en ese orden, antes que cualquier tool nuevo.**

Por qué ese orden y no otro: la doctrina del repo — la batería, el marcador, el ADR — es que nada entra sin evidencia. Hoy no hay evidencia de cómo responde Kern a una disrupción porque no hay instrumento que la mida (H5, H8). N2b construye el instrumento sobre el arnés que ya existe y ya publica capturas; N1 hace que lo que se mida sea lo que corre en producción y no una demo. Recién con esa foto — "captura bajo un salto de lead time de 3 semanas: X% ± Y, peor mundo Z" — tiene sentido decidir si vale la pena N3, que es el único nivel que pide descongelar el registro.

**Criterio de corte para N2b, explícito:** si la captura bajo shock sale negativa en la mayoría de los mundos, el trabajo siguiente **no es N3** — es entender por qué `inventory_optimization` pierde contra un punto de reorden fijo (H4 es el primer sospechoso: sin ver el tránsito, re-pide y amplifica). Eso también sería un hallazgo que marca una gran diferencia, solo que en la dirección incómoda.

---

## Anexo A — Experimentos reproducibles

### A.1 Medir TTR sobre el gemelo tal cual está (prototipo de N2a)

Red proveedor → CD (lead 3) → tienda (lead 2), demanda base 100 ± 15, 160 períodos, shock en t=50 por 10 períodos, semilla 7. TTR = períodos desde la primera salida de la banda ±2% del fill del baseline hasta 3 períodos consecutivos adentro. `asf` = `auto_size_factor` del gemelo (1,5 es el default: 50% de holgura).

| Shock | asf | Cae en | TTR | Unidades perdidas (vs baseline) | Fill mínimo |
|---|---|---|---|---|---|
| `supplier_outage` ×10 | 1,5 | t+10 | 5 | 453 (4,2%) | 0,00 |
| `lead_time_spike` +8 | 1,5 | t+10 | 3 | 279 (2,6%) | 0,00 |
| `demand_surge` ×3,0 | 1,5 | t+0 | 10 | 0 (0,0%) — ver H5 | 0,41 |
| `supplier_outage` ×10 | 1,0 | t+6 | 9 | 803 (7,4%) | 0,00 |
| `lead_time_spike` +8 | 1,0 | t+6 | 7 | 630 (5,8%) | 0,00 |
| `demand_surge` ×3,0 | 1,0 | t+0 | 10 | 30 (0,3%) — ver H5 | 0,27 |

Dos cosas que solo se ven corriéndolo: (1) con la holgura por defecto, un shock moderado (+4 de lead time, surge ×2) **se absorbe por completo** y no sale de la banda — el gemelo dimensiona generoso; (2) el "cae en t+10" del outage no es lentitud de detección sino el pipeline: la tienda vive del stock del CD hasta que se vacía. Un monitor de `lead_time_drift` sobre el CD lo habría visto en t+0.

```python
# PYTHONPATH=. python3 - <<'PY'   (extracto; el script completo corre en ~2 s)
from src.digital_twin import DemandProfile, Disruption, NodeSpec, simulate_network
nodes = [NodeSpec("proveedor","supplier"), NodeSpec("cd","dc",supplier="proveedor",lead_time=3),
         NodeSpec("tienda","store",supplier="cd",lead_time=2)]
base = simulate_network(nodes, DemandProfile(base=100, noise_std=15), periods=160, seed=7, auto_size_factor=1.0)
shock = simulate_network(nodes, DemandProfile(base=100, noise_std=15), periods=160, seed=7, auto_size_factor=1.0,
                         disruptions=(Disruption("supplier_outage","proveedor",50,10),))
# fill = served/demand por período; TTR = primera salida de banda -> 3 períodos consecutivos adentro
```

### A.2 Disrupción de ventas → consejo de cinco sombreros (composición existente)

Traza de demanda semanal de la tienda del gemelo (52 semanas, semilla 11, base 400 ± 90), escrita como CSV con `weight_kg = 12` y leída por `hats_job.prepare(path, {"distance_km": 900})`.

| | Sin shock | Surge ×2, 12 semanas |
|---|---|---|
| Demanda anual | 20.442 | 24.536 (+20%) |
| σ semanal | 79 | 150 (×1,9) |
| Q\* / SL\* | 1.279 / 97,5% | 1.401 / 95,0% |
| Concesión comercial | 0,04 | **0,10** |
| Concesión transporte | 0,07 | 0,05 |
| Ideal transporte (Q) | 1.638 | 1.664 (el camión sigue siendo el camión) |
| Top choque | cfo vs comercial −44.430 | cfo vs comercial −51.250 |

La lectura útil no es que Q\* suba: es **quién paga**. El surge duplica la variabilidad, el settlement baja el nivel de servicio para no comprar capital, y la concesión de comercial se multiplica por 2,5. Eso es una frase que un operador puede llevar a la reunión — y hoy se obtiene sin código nuevo.

---

## Anexo B — Verificación de esta rama

- Suite completa local sobre `6f76282`: **8.469 passed, 32 skipped, 1 failed**, 30 min. Los 19 `deselected` son los marcados `integracion` (`pyproject.toml addopts = -m 'not integracion'`), que necesitan servicios vivos.
- El único fallo, `tests/test_graph_lessons_no_deriva.py::test_el_check_completo_cuando_estan_las_herramientas`, **no es de esta rama**: `GRAPH_LESSONS.md` quedó viejo respecto del grafo de código (última regeneración el 14-sep, #316; `main` tiene 15 commits encima). La regeneración borra dos líneas — `GuidedOutcome (1× useful)` y un `HandoffPacket` duplicado — y no menciona ningún símbolo de #369. En CI ese test se saltea porque el runner no tiene `graphify`. Se deja fuera de este PR: regenerarlo acá sin haber grabado una memoria sería ensanchar el diff; la línea correcta es correr `python scripts/reflect_lessons.py` desde el checkout principal en el próximo PR que grabe una.
- CI de GitHub: rojo por cuota de Actions agotada desde el 6-sep (todos los jobs mueren en 3-22 s sin instalar dependencias; misma firma en todas las corridas de `main`). `prod-boot` reproducido localmente con el set exacto `.[core,web,mcp]`: `prod boot import OK`. Detalle en el comentario del PR.
