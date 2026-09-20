# Entregas de la rama `claude/keen-goodall-jfnqo9` (PR #369)

Fecha: 2026-09-20 · Rama: `claude/keen-goodall-jfnqo9` @ `63bc230` ·
PR: [esstipi-debug/kern#369](https://github.com/esstipi-debug/kern/pull/369)

El cuerpo de un PR de GitHub topea en **65.536 caracteres**, y esta rama lo alcanzó en la
decimonovena entrega: quedó en 65.227, o sea 309 de margen, y la vigésima no entraba. Este
documento es la mitad vieja de ese historial, movida acá para que las siguientes entren.

**Qué hay acá:** las entregas **segunda a decimosexta**, verbatim y en orden cronológico
—de los términos del proveedor hasta el arranque muerto—. No se resumió nada al mudarlas:
un historial que se resume cuando se archiva deja de ser un historial.

**Qué NO hay acá, y dónde está:**

| | dónde |
|---|---|
| Primera entrega (el quinto sombrero, `transporte`) | el cuerpo del PR, como marco: *Qué problema cierra*, *El modelo*, *El quinto sombrero es condicional*, *Cambio de comportamiento*, *Alcance*, *Verificación* |
| Decimoséptima a decimonovena | el cuerpo del PR |
| Verificación de la rama completa, estado del diseño de las cuatro brechas y los dos anexos | el cuerpo del PR |

**La regla, para que el cuerpo del PR no se vuelva a llenar:** lleva las **tres últimas**
entregas. Cuando entra una cuarta, la más vieja de las tres se muda al final de este
documento con su texto y su commit intactos, y el cuerpo del PR se queda con tres.

---

## Segunda entrega: términos del proveedor — dos terminales, nunca un punto muerto (`fc8c0e8`)

La pregunta que siguió: *"¿y si Kern recomienda 40 y el proveedor vende de a 30, 60, 90?"* Hasta este commit el único camino era `apply_order_rules` — redondeo hacia arriba, sin decir que 30 existía ni qué costaba cada opción — y solo lo usaba `src/sop_engine/purchase_plan.py`. El job de inventario, los sombreros y `cross_impact` ignoraban los términos: el consejo podía acordar 40, una cantidad que ningún proveedor iba a aceptar.

**Motor** (`src/constraints.py`). `cheapest_feasible_lot` valúa los dos lotes legales vecinos con la misma curva de costo del EOQ (`src.eoq.total_cost`), elige el más barato y devuelve el otro como `alternative` con su penalidad. Verificado a mano: D=1.000, K=50, h=1 → Q*=316; con múltiplo 100 elige **300** (+0,44 usd/año) y deja **400** (+8,77 usd/año) como segunda terminal. Un objetivo bajo el MOQ tiene una sola terminal y lo dice (`alternative=None`); un objetivo ya legal no se mueve; un empate va hacia arriba (más cobertura, menos pedidos). `lot_rule` / `is_feasible_lot` / `feasible_neighbours` / `feasible_lots` son la única definición de "factible" que leen los tres consumidores, así que nunca discrepan.

**Inventario.** `jobs/intake.py` detecta `moq` / `order_multiple` (alias es/en: `pedido_minimo`, `cantidad_minima`, `unidades_por_caja`, `case_pack`, …) y los emite **solo cuando el archivo los trae** — un archivo sin ellos produce el mismo frame de seis columnas de siempre, y el ratchet del corpus de cabeceras no se movió (cobertura 0,513, falsos positivos 0,0595). `jobs/inventory_optimization.py` mueve Q, ciclo, inversión y beta implícita al lote elegido en `(s,Q)`; el registro lleva `order_quantity_unconstrained`, `lot_rule`, `lot_penalty_usd`, `lot_alternative_qty` y su penalidad. Un `(R,S)` con términos los lleva sin aplicarlos y se cuenta (`n_lot_rule_unapplied`). `jobs/qa.py` rechaza una Q que el proveedor no acepta, una alternativa infactible o más barata que la elegida, y un registro sin la Q de origen. CSV, Excel, md y deck muestran las dos terminales; `inventory_options` agrega la OPCIÓN **"Renegotiate supplier terms where rounding costs most"** (segunda, después de Adopt, con la lista por penalidad y el lote alternativo nombrado) y un residual para los `(R,S)`.

**Sombreros.** Con términos, `candidate_grid` son lotes legales (piso `MIN_FEASIBLE_Q_POINTS`=5 en vez de 125 candidatos, techo 100, los vecinos de cada ancla siempre adentro) y `baseline_plan` es el mismo lote que despacha el job. `TensionMap` y `Settlement` llevan los términos; `verify_tension` / `verify_settlement` rechazan un ideal o un acuerdo infactible. `hats_job` lee las columnas o los params de corrida (`--moq` / `--order-multiple` en los dos CLI; la columna del archivo gana). Medido en `run_hats.py --sku SKU-H --order-multiple 30`: comprador 2.460, planner 480, cfo 270, comercial 2.460, settlement 990 — todos múltiplos de 30. `cross_impact` mide la propuesta tal cual pero, si su Q es infactible, agrega un residual que nombra los vecinos legales ("1.750 no cumple multiple 100; los lotes factibles más cercanos son 1.700 o 1.800").

**Fuera, dicho:** leer `product.packaging` de Odoo (`src/connectors/odoo.py` solo lee `min_qty`). Es trabajo de conector y no hay instancia viva para probarlo.

**Verificación:** `tests/test_lot_constraints.py`, nuevo: 30 tests. Regresión de 475 tests en verde; además batería/coherencia (168) y contrato/intake_scan/registro/fronteras (287). `ruff check .` limpio; `webapp.app` importa. El registro sigue en 60.

## Tercera entrega: el tool CSV ve el estante y lo que viene en camino (`ead1243`, N1d / H4)

El hallazgo H4 de la investigación, cerrado en la misma rama. `inventory_optimization` dimensionaba `s` y `Q` y **nunca miraba el stock**: decía cuánto pedir y jamás cuándo, y una OC que transporte atrasó dos semanas era, para él, indistinguible de una OC que no existe — se volvía a pedir. El neteo contra la posición (`NFP = on_hand + on_order`, `src/ddmrp.py`) existía desde kern#251 pero solo en la planilla y en el conector de Odoo; el tool que rutea un brief genérico, el que corre `simworld` y el que dispara `rop_breach` dimensionaba a ciegas.

**Productor** (`jobs/intake.py::stock_position`). Lee `on_hand` y, opcional, `on_order` del archivo del cliente (alias es/en: `stock`, `existencia`, `saldo`; `en_transito`, `por_recibir`, `oc_pendiente`, `open_po`, `incoming`, …), toma la **última fila por SKU en orden de fecha** y devuelve una fila por SKU con la nota de origen. Las dos columnas **no** entran en `CANONICAL_OPTIONAL`: un stock es una foto y `normalize_tracked` agrega flujos por período. `position_maps` produce los dos dicts que consume el job; `on_order_by_sku` es `None` — no `{}` — cuando el archivo no traía la columna, y un SKU con la celda vacía no está en el mapa. `pedido` pelado sigue sin ser alias: es la columna de salida de la planilla.

**Job.** Cada registro lleva `on_hand`, `on_order`, `inventory_position` (la identidad de DDMRP, no una segunda fórmula), `order_now` y `order_qty_now` (`(s,Q)` pide Q en o bajo `s`; `(R,S)` pide hasta `S`). **El en-tránsito netea el disparador, no solo la cantidad**: `n_reorders_avoided_by_transit` cuenta los SKU que el estante solo habría vuelto a pedir. La ausencia se declara: sin en-tránsito `on_order` es `None`, nunca 0, y el reporte, el CSV (celda en blanco), el Excel, el md, el deck (finding "In-transit stock NOT counted"), las OPTIONS (residual) y el resumen del agente dicen que **no** se pudo netear. `jobs/qa.py` gatea la identidad de la posición y que el disparo sea exactamente lo que la política pide en esa posición. `inventory_options` suma la OPCIÓN **"Release the N replenishment order(s) due now"**.

Medido (`tests/test_inventory_en_transito.py`): un estante 1 unidad bajo `s` dispara; con 50 unidades ya compradas la posición queda sobre `s` y **no** dispara (`n_reorders_avoided_by_transit = 1`). En `(R,S)`, 4 unidades en camino bajan la cantidad exactamente 4.

**Verificación:** 31 tests nuevos con la disciplina de `tests/test_neteo_en_transito.py`. Regresión de 673 tests en verde; `ruff check .` limpio.

## Cuarta entrega: diseño de las cuatro brechas siguientes (`29e8970`)

`docs/superpowers/specs/2026-09-17-cuatro-brechas-post-n1d-design.md`: diseño verificado contra el código, con productor real, firmas, instancias a mano, tests nombrados y lo que cada brecha NO resuelve. **C** (las tres hermanas), **A** (barrido de neteo con la batería, sin tocar la vara), **D** (N1c el puente de estado + N1a el ruteo de los dos monitores huérfanos con la serie REAL) y **B** (N2b, shocks deterministas en el marcador). Orden propuesto C → A → D → B. Ninguna toca el registro (60), el puntaje congelado (65,00 %) ni la semilla del marcador.

## Quinta entrega: C construida — nadie compra dos veces lo que ya viene (`c960de4`)

Las tres brechas hermanas de H4, cerradas. El neteo contra la posición existía en un solo lugar y tres emisores de cantidad a pedir no lo enchufaban.

- **C1 · `shopify_replenishment`.** El conector pedía solo `available`; Shopify devuelve además **`incoming`** (transferencias entrantes y las OC del propio admin) en la MISMA consulta. `ShopifyInventoryClient.get_inventory_quantities` pide las dos y `get_inventory_level` pasa a derivarse de ella; `ShopifyConnector.incoming_by_sku()` memoiza **una sola pasada por ítem** (el contador `quantity_calls` del stand-in lo prueba). El job netea vía `plan_replenishment(on_order_by_sku=...)`. Un RPC viejo sin el método sigue leyendo el estante y declara la ausencia. Medido: 12 en estante y 10 en camino bajan el pedido de 28 a 18; con 40 en camino el SKU sale del plan.
- **C2 · `channel_replenishment`.** `read_erp_on_order` reutiliza **literal** `jobs.odoo_job.read_on_order` — el filtro de estado, la regla de pendiente y la guarda de unidad de medida viven en un solo lugar —, la cantidad se dimensiona contra `net_flow_position(erp_on_hand, on_order, 0)` y un ERP que no expone OC abiertas (Unleashed) se nombra por su clase en la nota. Medido: con 8 unidades ya compradas, SKU-3 baja de 20 a 12 y el valor del plan baja 8 × el costo de libro del ERP.
- **C3 · `drp`.** `Branch.scheduled_receipts` entra al grid **antes** del requerimiento neto, como el DRP de manual, y `DrpRow.scheduled_receipt` lo reporta aparte de lo que el plan propone pedir. Instancia verificada a mano: con 10 programadas en t=1, las recepciones planificadas pasan de `[0,10,10,10]` a `[0,0,10,10]` y las liberaciones de `[10,10,10,0]` a `[0,10,10,0]`, con el proyectado clavado en el piso de seguridad. El job lee la columna por (sucursal, período) y el CD la recibe por params.

**Una corrección sobre el diseño, y por qué.** El aviso de "no se pudo netear" se decide por si el ERP se pudo **leer** (`on_order_by_sku is None`), no por si alguna línea trajo un número. Un ERP leído sin ninguna OC abierta es una medición, y decir "no se descontó" sobre él mandaría al comprador a restar a mano lo que ya está restado.

**Verificación:** 36 tests nuevos (`tests/test_shopify_en_transito.py` 9, `tests/test_channel_en_transito.py` 13, `tests/test_drp_en_transito.py` 14) con la disciplina de kern#251: dos corridas por caso y el valor que se mueve. Regresión de 623 tests en verde sobre Shopify, canal, DRP, neteo, conectores, batería, coherencia, registro y contrato de datos. `ruff check .` limpio. El registro sigue en 60 y el puntaje de la batería congelado.

## Sexta entrega: A construida — el barrido que prueba que los siete netean, y nombra a los siete que no pueden (`074b40f`)

Las cinco entregas anteriores afirman "ahora netea". **Esta es la que lo mide, tool por tool.** El diagnóstico: la batería puntúa el **mejor** de tres entregables de reposición, y la pregunta del producto es la contraria —*¿cuál de los emisores **no** puede?*—. El costo está medido en esta misma rama: H4 convivió con la batería en verde porque el tool central nunca fue candidato de T05. **Un tool que nadie sondea no se puede poner rojo.**

`src/bateria/barrido_neteo.py` sonda los **catorce** tools del registro que ponen un número de compra en manos de un operador: dos corridas por emisor (con y sin en-tránsito) y el **valor se mueve**; ninguna sonda mira nombres de campo. Cuatro estados que no se pueden mezclar —`netea`, `no_netea`, `sin_sonda`, `sin_canal`— y el resultado congelado en `ESTADOS_NETEO`: **8 netean, 6 `sin_canal`**, ninguno `no_netea` ni `sin_sonda`. Cerrar una brecha es un diff explícito sobre ese vector, y la decimotercera entrega lo movió (`launch_readiness`, de `sin_canal` a `netea`) exactamente por ese mecanismo.

**Clasificar es obligatorio:** `tests/test_barrido_neteo.py` recorre `build_default_registry()` y falla si un tool no está ni en `EMISORES` (con sonda) ni en `NO_EMISORES` (con razón escrita). Y la razón escrita no vale para siempre: si a un `sin_canal` le aparece un campo con nombre de en-tránsito, la sonda pasa sola a `sin_sonda` y el vector se pone rojo.

Tres decisiones que el diseño no traía: `NO_EMISORES` es un `dict` porque un conjunto pelado no puede llevar la razón; `SondaNeteo` lleva `emite` porque una cantidad, un nivel objetivo y un veredicto de cobertura no cargan el mismo riesgo; y el detector de canal compara **tokens, no substrings** —`"on_order"` vive adentro de `"echelon_order_up_to"` y `multi_echelon` salía acusado de un canal que no tiene—.

**La vara no se tocó:** `TOTAL_TAREAS` sigue en 20 y el puntaje en **65,00 % · 13/20**. T05 pasa a **consumir** estas sondas, así que "netea" se define una sola vez en el repo. Documento: `documentation/KERN_BARRIDO_NETEO.md`.

**Verificación:** `tests/test_barrido_neteo.py`, nuevo: 17 tests, cinco de los cuales sondean a la sonda misma. Los dos frenos verificados por mutación. `ruff check .` limpio.

## Séptima entrega: D construida — el bucle de sensado deja de correr sobre vacío (`7581b7c`, N1c + N1a)

Los dos hallazgos que quedaban del anexo de disrupciones, y son uno: **el Control Tower sensaba sobre nada**. `run_all_monitors` lee tres dominios (`stock`, `forecast`, `outcomes`) y hasta este commit el ÚNICO escritor de `stock` era el puente del marcador (`examples/run_world_sim.py`). En producción el bucle corría, no encontraba nada y no emitía nada — un monitor que nunca dispara y un monitor que no existe se ven exactamente igual desde afuera (H1). Y dos de los monitores que sí existen (`forecast_error_out_of_band`, `lead_time_drift`) no tenían ruta en `config/event_routing.yaml`: emitían un evento que no iba a ninguna parte (H2).

**D1 · El puente (`jobs/state_bridge.py`).** Los **cinco** jobs que ya leen una posición — `inventory_optimization`, `odoo_replenishment`, `shopify_replenishment`, `channel_replenishment`, `excel_replenishment` — escriben ahora el snapshot `stock`, y los tres de conector escriben además la demanda REALIZADA como `outcomes`/`actual_qty` con la fecha de cada pedido. El mapeo de cada job vive en UN lugar, nunca copiado en cinco.

- **La identidad es la misma regla que `forecast_cycle.store_from_params`**: sin `params["state_client_id"]` no se escribe nada y **nunca** la partición compartida; un id inválido levanta `ClientIdError` en vez de degradar a algo que parece funcionar. Cada reporte gana un `state_note` que dice si escribió, dónde y por qué no.
- **`avg_daily_demand` es por día CALENDARIO** (`replenish.daily_demand_by_sku`), no el promedio de `demand_by_sku` — cuyo período es un día-CON-PEDIDOS. Sobre un SKU intermitente el segundo sobre-estima la tasa varias veces, y es justo el número con el que el Tower calcula días de cobertura: dispararía quiebres falsos. El diseño traía la versión equivocada.
- **La planilla en modo punto-de-reorden no escribe.** Sin columna de demanda no hay tasa diaria, y derivarla del ROP sería inventarla. La nota lo dice en vez de omitirlo.
- **Límite declarado, no tapado:** el dominio `stock` no tiene `on_order`, así que `rop_breach` dispara sobre el ESTANTE aunque el job sí netee. Agregar la columna exige un ciclo de esquema en `system_state.py`; queda como seguimiento escrito. → **Cerrado en la duodécima entrega (`3c33ee9`).**

**D2 · Las dos rutas huérfanas.** `config/event_routing.yaml` pasa a **9 rutas**. Los dos monitores leen la serie REAL del estado (`event_intent._real_series_csv`) y se **niegan** con `EventRoutingError` si un SKU no llega al mínimo de `forecast_job` — la regla 7: una serie corta no se completa con nada. El drift entra como override `lead_times` de `inventory_optimization`, convertido de días a **PERÍODOS** con `src.data_loader.lead_time_periods_from_days` (pasar días crudos inflaría el período de riesgo por siete), y **no** como un `promo_lift` de `sop`: un lift es demanda, no plazo. El evento sabe de qué cliente es su serie por `run_all_monitors(client_id=...)` → `payload["state_client_id"]`; sin eso no se rutea.

**Tres errores de unidad del diseño, corregidos al construirlo.** Además de la demanda diaria y de los días-vs-períodos de arriba: el `cycle_id` tiene que ser ISO y no `f"{tool}-{fecha}"`, porque el orden es lexicográfico y el segundo escritor del día sería rechazado con `CycleOrderError` por llamarse distinto. Y la demanda y el lead time de un mismo ciclo se escriben **juntos**: en ciclos separados, `lead_time_drift_monitor` —que compara los dos snapshots más recientes— no dispararía nunca.

**Probado punta a punta, que es lo que H1 pedía:** correr `shopify_replenishment` con `state_client_id` y después `run_all_monitors` emite un `stockout_projected` real. Las dos rutas nuevas corren su tool por el orquestador de verdad hasta `STATUS_OK`.

**Verificación:** 40 tests nuevos (`tests/test_state_bridge_stock.py` 18, `tests/test_event_intent_real_series.py` 22) más 1.075 del área de monitores/estado/conectores. `ruff check .` limpio; `import webapp.app` reproducido a mano.

## Octava entrega: B construida — captura bajo disrupción (`4c8edaf`, N2b)

La última de las cuatro. El marcador medía si las decisiones de Kern son buenas bajo **suciedad de datos** y nunca bajo **disrupción**; el gemelo (`src/digital_twin.py`) sabía inyectar disrupciones y nadie medía sus trazas (H5). Las dos mitades existían y no se tocaban.

**El shock es una transformación pura del mundo YA sorteado.** `Shock(kind, target, start_week, weeks, magnitude)` usa el mismo vocabulario que el gemelo; `apply_shocks` hace `dataclasses.replace` sobre copias y no toca `rng`, `generate_world` ni `advance`. Así *misma semilla + mismo shock ⇒ mismo mundo*, elemento a elemento, y fuera de la ventana nada se movió — sin eso, comparar la corrida limpia contra la perturbada no mide el shock, mide dos sorteos distintos. Dónde pega cada tipo lo decide `advance`, que lee el lead real **en el momento de emitir** la orden.

**`score_shock` → `ShockImpact`, con dos definiciones que la investigación dejó fijadas** y que no se pueden cambiar sin cambiar lo que el número significa: el **TTR se cuenta desde la primera semana FUERA de la banda**, no desde el arranque del shock (la holgura que retrasa el golpe no es lentitud de detección, y cobrársela mediría otra cosa); y bajo un `demand_surge` **las dos medidas de unidades perdidas viajan juntas y con signo** — lo no servido de la demanda perturbada, y lo servido contra el baseline, que puede ser negativo. Ningún `max(0, ...)`.

**Una revisión adversarial de la propia entrega encontró siete defectos reales**, y seis son la misma falla con distinta cara — *un número que se reporta y no es lo que su nombre dice*: `nan`/`inf` en la magnitud pasaban la validación (`nan < 1` es `False`) y dejaban la matriz de demanda entera en NaN; un `lead_time_spike` de 2,5 se aplicaba como **2** (`round(2.5)` es 2 en Python) mientras el JSON publicaba 2,5; un shock de suministro dentro del calentamiento era un no-op garantizado que salía "absorbido"; un tramo **sin demanda** satisfacía el `hold` y declaraba `recovered=True` con TTR sobre un carril que no sirvió una sola unidad; con dos shocks cada impacto se atribuía el daño del otro; una línea base grabada con shock comparada contra una limpia daba una regresión falsa del producto; y `min_fill` / `lost_units_post_shock` son absolutos, no la parte causada por el shock — lo que faltaba era decirlo donde se leen. Los siete cerrados con test.

Y dos que aparecieron al escribirlo: un `demand_surge` en las primeras semanas **se auto-absorbe** (el mundo dimensiona el stock inicial con esa demanda; medido, un ×3 en las semanas 0-3 triplica el stock de arranque), así que se rechaza; y la degradación **dependía del orden de `--levels`** (`--levels 3 0` no producía ninguna, porque la referencia limpia se poblaba dentro del bucle) — lo arregla de paso el refactor que el shock obligó, extraer `_run_lanes` para correr la grilla sobre dos mundos.

**Desvío respecto del diseño, y por qué.** El diseño pedía `--seeds 25` en `run_world_sim.py`; ese flag no existe y nunca existió — el barrido de N mundos del repo es `scripts/verify_fidelidad.py --mundos N`, que ya llama a `agregar`. El `--shock` fue ahí, `agregar` ganó `metrica=` para poder agregar `capture_under_shock` sin mezclarla nunca con la limpia, y el `contexto` de la línea base graba los shocks y la métrica.

Verificado además que el reporte **sin** shock es byte-idéntico al de antes del refactor salvo la clave `shocks` nueva.

## Novena entrega: segunda vuelta de la revisión, y uno de los arreglos anteriores queda anulado (`955d5ae`)

Los dos revisores que faltaban llegaron después y encontraron una falla que los otros cuatro no vieron. **Anula el arreglo #3 de la primera vuelta:** rechazar el shock de suministro que cae dentro del calentamiento cubre **una** causa de no-op, no la general.

`first_out_of_band_week=None` ya significaba ABSORBIDO, así que *"el carril aguantó la disrupción"* y *"la disrupción nunca le llegó"* se renderizaban igual. Medido con el ejemplo literal del diseño (`--seed 4 --skus 8 --shock lead_time_spike:SKU-1003:10:3:2`): el carril de Kern publicaba `absorbido`, `recuperó sí` y una **captura bajo disrupción de −0,221** con un `total_cost` idéntico al último dígito al de su corrida limpia — el SKU apuntado no se pide dentro de la ventana, así que `advance` nunca lee las celdas perturbadas. Y **no se puede rechazar por corrida**: en esa misma corrida el piso SÍ recibe el shock (`vs base +19 u`). Tiene que declararse por **carril**.

- **`ShockImpact.reached`** compara las dos trayectorias elemento a elemento. Si son idénticas el veredicto queda **vacío** — `recovered=None`, nunca `True` — y la fila sale `NO ALCANZADO` en vez de `absorbido / sí`.
- **Ese carril no publica `capture_under_shock`**: sería su captura **limpia** con otra etiqueta, y el barrido la promediaba entre las demás, subiendo la media con mundos donde no pasó nada. Queda `capture_under_shock_unreached`, y una clave ausente ya contaba como mundo NO medible, así que la cobertura lo dice sola.
- **La guarda temprana se queda**, porque es lo único que se puede rechazar ANTES de correr. Pero no cubre `replan_every`: la primera orden sale en `ceil(WARMUP_WEEKS/replan_every)*replan_every`, y con `replan_every=8` un `supplier_outage:*:4:3` la pasa y no golpea nada. Medido, y ahora declarado.
- **`Distribucion.metrica`** viaja dentro de la distribución. La frase vendible del barrido decía "captura" a secas para las dos métricas — byte a byte idéntica a la de un barrido limpio, y en este repo "captura" significa la limpia. Ahora dice **"captura bajo disrupción"**, y el "ningún mundo medible" nombra la segunda causa que solo existe bajo shock.

Y una afirmación falsa en el reporte, del mismo lote: el corte **Anunciado/Silencio** del costo del shock se titulaba *"si Kern **vio venir** el golpe"*. `Policy.announced` se enciende por la **calidad de lo que Kern leyó** (status, qa_issues, residuales, intake_quality) y la lente le muestra el plazo **prometido**, nunca el real. Medido sobre la semilla 4: con `supplier_outage:Proveedor-1:6:4` y con `demand_surge:*:6:4:3` las semanas anunciadas son las mismas que sin shock — `[4, 8, 12]` en los tres casos — mientras el costo semanal **sí** se movió. El número se queda; el título ahora dice lo que el número es.

**Verificación:** `tests/test_simworld_shocks.py` 50 → **56**. Regresión ancha de simworld + verify: 156 en verde. `ruff check .` limpio.

## Décima entrega: la vara del marcador premiaba no comprar (`7aa08c5`)

**La entrega más importante de la rama, y no estaba planificada.** El barrido de 25 mundos que iba a grabar la línea base de captura bajo shock —el criterio de hecho de B— disparó en cambio la alarma del propio arnés: `degrada_con_la_suciedad` marcó *"ensuciar mejoró el resultado"* (L1 34,3 % contra L0 7,6 %), y `verify_fidelidad.py:143` **retorna 1 antes de grabar**, a propósito: se niega a congelar una referencia de una corrida cuyos números él mismo dice que no se pueden citar. Investigarlo, que es lo que esa alarma pide, destapó un defecto en la función de costo que llevaba meses ahí.

**El defecto: una doble resta de `unit_cost`.** `shortage` valuaba la venta perdida a `margin = price - unit_cost`, y `procurement` ya cobra `unit_cost` al comprar la unidad. Servir una unidad neteaba `unit_cost - margin = 2*unit_cost - price`: positivo —o sea **peor que no servir**— en todo SKU con `price < 2*unit_cost`.

No es un caso de borde. `generate_world` sortea `price = unit_cost * U(1.4, 2.8)`, así que **el 42,86 % del catálogo nace de ese lado por construcción**, sin depender de la semilla (medido: 82 de 200 SKU sobre 25 semillas). Lo que eso producía, todo medido antes del cambio:

- **El oráculo pedía exactamente CERO en los 82 SKU** de bajo margen, durante todo el horizonte. Fill 0,1505 ahí contra 0,9826 en el resto: el sub-servicio era quirúrgico, no global.
- **El techo servía MENOS que el piso** (0,641 contra 0,824) en 21 de 25 mundos. Un techo que sirve menos que el piso no es techo de nada.
- **Una política de no comprar nunca nada** sacaba captura **+0,375** y le ganaba al piso en 21/25 mundos.
- Y por eso la suciedad "mejoraba" el resultado: **no borra datos, los MUEVE**. Medido en la primera replanificación, donde las dos corridas todavía comparten historia: intake devuelve 32→32 filas canónicas y cantidad total idéntica (528,00) en L0 y L1, pero los SKU pasan de **8 a 15** —whitespace y mayúsculas parten la serie— y el span se estira de enero a mayo (dd/mm resuelto mes-primero). La tasa por período se desploma, Kern compra un quinto, y comprar menos era contablemente mejor.

**Verificado por cuatro revisores adversariales independientes: 0 de 4 la refutaron.** Dos correcciones que salieron de ahí y están en el código:

- El nombre correcto es **doble resta de `unit_cost`**, no "falta sumar revenue". Ese segundo framing lleva a un arreglo que **duplicaría** el margen de la demanda servida.
- `margin < unit_cost` es condición **suficiente**, no el umbral. El real es estrictamente mayor, porque el almacenaje y los $75 fijos solo empeoran el lado de comprar.

**El arreglo** (el usuario eligió esta opción entre cuatro presentadas, incluida "no tocar la métrica y documentar"): `shortage = lost * price` en `weekly_cost` y `score_run`, y el coeficiente del MILP a `-price` para que el techo sea el techo de lo que se mide. Con eso `total_cost` pasa a ser `constante_del_mundo - profit_economico`, así que minimizar costo y maximizar rentabilidad son el mismo problema. `Kpis.profit` se corrige de paso: era `revenue - total_cost`, que cobra la venta perdida **dos veces** (una por no cobrarla, otra como shortage), y pasa a ser la rentabilidad económica.

**Los tests, reescritos con la semántica nueva y no parcheados.** El del "mutante" de `runners.py` —que fijaba `margin` y trataba `price` como *"very plausible real bug"*— se parte en dos, uno por cada lado del equilibrio nuevo: el escenario original con el **veredicto invertido** (y la explicación de por qué el mutante era el original), y uno que conserva la propiedad de que el oráculo **no** cubre a cualquier precio (`uc=5, price=6`: cubrir ahorra 420 y cuesta 430,04, así que acepta el quiebre). Los dos hand-computed del scorer, con su bloque de derivación del encabezado. El golden de e2e re-medido, que además **pasa a fijar `ceiling.fill >= floor.fill`**: la razón escrita por la que se negaba a fijarlo era el síntoma, no una propiedad — el techo pasó de 0,565 a **1,000**. Y uno nuevo que fija la invariante en términos de producto —servir una unidad nunca puede costar más que perderla, sobre SKU reales del generador—, **confirmado por replay**: con `margin` falla sobre SKU-1003, revertido pasa.

**Y el diagnóstico del propio detector estaba mal.** El docstring de `degrada_con_la_suciedad` decía que esta clase de anomalía es *"el síntoma clásico de una fuga de información entre el generador de suciedad y el marcador"*. La primera vez que disparó de verdad **no había ninguna fuga**: la lente no toca el scorer en ningún punto. Corregido: la alarma dice QUE algo entró por una vía no diseñada, no CUÁL.

## Undécima entrega: las dos líneas base, publicadas (`b1e3293`, `71ddf3d`)

Con la vara arreglada, los dos barridos salieron **sin anomalía** y el arnés los aceptó. Cierra el criterio de hecho de B: *"una línea base grabada con captura bajo shock sobre 25 mundos"*.

25 mundos (semillas 1000-1024), 8 SKU, 24 semanas, replan 4, carril `forced`. Los dos difieren **únicamente** en el shock, que es lo que permite atribuirle la diferencia:

| niv | limpio | bajo `lead_time_spike:*:8:3:3` |
|---|---:|---:|
| **0** | **−40,6 % ± 14,5 %** | **−56,0 % ± 17,5 %** |
| 1 | −117,0 % ± 50,7 % | −92,0 % ± 41,3 % |
| 2 | −132,8 % ± 45,5 % | −107,3 % ± 39,0 % |
| 3 | −122,6 % ± 50,3 % | −99,6 % ± 39,5 % |

**El shock cuesta 15,4 puntos de captura** en el nivel 0. Y una lectura que el documento explica porque no es obvia: en los niveles sucios la captura bajo disrupción sale **menos mala** que la limpia (−92,0 % contra −117,0 % en L1). No es que la disrupción ayude — **el piso también sufre el shock**, y la captura es una razón contra el piso: cuando el denominador se degrada, una política mala se ve menos mala. Por eso se publican las dos columnas y no una.

**El número honesto: Kern decide peor que un punto de reorden fijo en las dos condiciones.** El +7,6 % que daba L0 antes del arreglo era en buena parte el premio por sub-servir (fill 0,639 contra 0,824 del piso, y la vara no lo penalizaba). El arreglo no empeoró el producto; dejó de halagarlo.

`documentation/KERN_CAPTURA_BAJO_DISRUPCION.md` publica las dos, con cómo reproducirlas, y una sección de **qué no dice**. Una de esas salvedades —que no explica POR QUÉ Kern queda por debajo del piso— quedó contestada en la decimoquinta entrega y el documento ahora apunta ahí.

`tests/test_captura_bajo_disrupcion_publicada.py`, nuevo: **7 tests**. Además del anti-deriva (la frase se reconstruye desde el JSON y tiene que aparecer LITERAL en el documento, el mismo mecanismo que `reflect_lessons --check` sobre GRAPH_LESSONS), uno exige que la línea base sea de verdad una corrida bajo disrupción —un archivo que se llame así con números limpios adentro daría tranquilidad siempre— y otro que las dos líneas base difieran **solo** en el shock: si alguien regraba una con otra escala, la resta de 15,4 puntos deja de medir el shock. Confirmado por mutación: cambiar un decimal del documento lo pone rojo.

**Cifras vencidas marcadas donde estaban, no borradas**, porque la trazabilidad de por qué cambiaron vale más que un documento limpio: `NO_SILO_KERN_LOOP.md` (3 lugares), `loops/NO_SILO_KERN_REPORT.md`, `KERN_DISRUPCIONES_Y_DESAJUSTES.md`, `HANDOFF.md` y `TESTING_ENVIRONMENT.md` — donde la *"curva graduada de −6,8 % a +5,0 %"* queda **retirada**: esa curva era el artefacto de la doble resta, no un hallazgo. Lo que sí sigue en pie de ese párrafo es el hallazgo de escalamientos, que no pasa por `capture`. A la escala del loop, las capturas pasan de −0,446 / +0,437 a **−0,6019 / −0,0180**.

## Duodécima entrega: `rop_breach` deja de disparar sobre el estante (`3c33ee9`)

El límite que la séptima entrega dejó **declarado por escrito**, cerrado. El dominio `stock` del estado no tenía `on_order`: los cinco productores ya traían el dato con la semántica `None != 0`, y el que lo tiraba era `StockLine`. El Tower comparaba el **ESTANTE** contra el ROP y disparaba `stockout_projected` / `rop_breach` sobre stock que ya estaba comprado y en camino — exactamente el defecto que kern#251 cerró en los jobs, reabierto un piso más arriba.

**Esquema.** `ColumnRule` gana `required` y `nullable`; `DOMAIN_COLUMNS["stock"]` gana `on_order` como columna OPCIONAL y anulable. Las dos rutas de validación aprendieron las dos banderas — la de pandera **y la de fallback**: sin esa segunda, una instalación sin pandera habría rechazado todo snapshot de stock que no trajera la columna, incluido el que escribe el marcador.

**El neteo va al DISPARADOR, no a la cobertura.** `src/alerting.py::detect_events` —el único lugar del repo con la comparación contra el ROP, del que los dos monitores del Tower ya dependían— compara `net_flow_position(on_hand, on_order, 0)`, la identidad de `src/ddmrp.py`. Los días de cobertura siguen sobre el estante **a propósito**: netearlos cambiaría un falso positivo por un falso **NEGATIVO**, porque una OC que llega en tres semanas no alimenta el mostrador mañana.

**La ausencia se declara.** Sin el dato el evento sale igual, con `netted=False` y la frase "(sin netear: no hay dato de en-tránsito)" en el mensaje. Un `NaN` —que es como vuelve un `None` por parquet— cuenta como ausente, porque `on_hand + nan <= rop` es `False` y dejaría al monitor **mudo** sobre un quiebre real. Un `on_order` negativo también: bajaría la posición y dispararía de menos. Y `event_intent` reenvía la posición al tool que el evento dispara, para que el neteo del job no quede inerte aguas abajo.

Un hueco que encontró mi propio test: el filtro de negativos estaba en `state_bridge._en_camino` y **no** en `alerting._en_transito`, que es el que leen los otros escritores del dominio. Un `on_order` de −40 bajaba la posición y disparaba. Cerrado en los dos lados.

**Verificación:** `tests/test_rop_breach_posicion.py`, nuevo: 15 tests. Suite completa **8.704 passed, 0 failed**. `ruff check .` limpio.

## Decimotercera entrega: `launch_readiness` netea contra una FECHA (`97b80a7`)

El octavo emisor, y netea **distinto** a los otros siete. Su veredicto pregunta si el estante alcanza para llegar al lanzamiento, así que salía **ROJO** —"una orden no puede llegar a tiempo"— sobre stock que ya estaba comprado y en camino.

La diferencia con los demás: una cantidad en camino no sirve si llega **después** del lanzamiento. El archivo puede traer ahora `on_order` y su **ETA** (alias es/en), y el en-tránsito cuenta **solo si aterriza en o antes de la fecha de lanzamiento**:

- con ETA anterior o igual, entra en `position_at_launch` y el veredicto se mueve;
- con ETA posterior **no** cuenta, y el veredicto lo dice;
- con cantidad pero **sin ETA** tampoco cuenta —contarla sería inventar una fecha— pero el veredicto la **NOMBRA** y dice qué columna agregar.

La cobertura en días sigue sobre el estante (`on_hand / demanda moldeada`), por la misma razón que en `rop_breach`.

**El vector congelado del barrido se mueve por primera vez**, que es exactamente lo que `ESTADOS_NETEO` existe para hacer visible: **8 netean, 6 `sin_canal`**. La sonda de `launch_readiness` corre **tres** veces y no dos —acredita solo si lo que llega a tiempo mueve el veredicto **Y** lo que llega tarde no lo mueve—, porque una sonda de dos corridas acreditaría igual a un job que descontara todo sin mirar la ETA. `documentation/KERN_BARRIDO_NETEO.md` regenerado.

**Verificación:** `tests/test_launch_readiness_en_transito.py`, nuevo: 8 tests. Suite completa **8.712 passed, 32 skipped, 19 deselected, 0 failed**. `ruff check .` limpio.

## Decimocuarta entrega: una negativa del motor no puede salir vestida de número (`5408b2f` … `47c1a4a`)

`inventory_optimization` se **niega** a dimensionar un SKU con menos de `MIN_PERIODS_FOR_POLICY` períodos o sin costo usable —correcto— pero emitía esa negativa con `forecast`, `reorder_point`, `safety_stock` y `z_factor` en **0.0**, `status="ok"` y un resumen que decía *"recommended inventory investment $0"*. El aviso viajaba por la prosa; **el número decía lo contrario, y todo consumidor lee el número**: `src/simworld/runners.py` lo ejecutaba como "no pedir nunca", y `state_bridge::_usable` —que existe justamente para omitir esa línea— quedaba derrotado por el `0.0`, así que el Tower emitía **`dead_stock`** sobre un SKU que vendía ~42 u/semana con 120 en el estante.

Los cuatro campos son `float | None` y la ausencia va en `None`. **`skipped_zero_demand` CONSERVA sus ceros**: ahí la demanda SÍ se midió y dio cero, así que el `dead_stock` legítimo sigue saliendo. `jobs/qa.py` gatea las **dos** direcciones, porque anular todo sería el mismo defecto al revés.

**La cantidad que el marcador emite no cambia** —captura idéntica al último dígito— porque `apply_policy` ya trataba un SKU ausente igual que uno con disparador 0. Lo que cambia es que ahora se puede distinguir "me negué" de "decidí no reponer".

Cuatro consumidores más que la misma lista incompleta dejaba pasar, cada uno con su costo propio: `webapp/demo_gap_value.py` (una ruta de producción que reventaba con `TypeError`, y que antes publicaba una brecha de $0 sobre un SKU que nadie dimensionó); `scm_agent/package_specs.py`, que metía el SKU en la **planilla de pedidos del cliente** como `kern_optimized` con disparador cero, pisando su número propio; `src/verify/coherencia.py`, donde pasaba toda invariante trivialmente; y la prosa, que llamaba "negativa" a una cartera de demanda medida en cero —contradiciendo el registro y negando un hallazgo real—.

**Un hallazgo de la revisión adversarial que corrigió al autor:** se había escrito que un pronóstico negativo "no se puede producir con ninguna serie decreciente real". Es falso: sobre catorce semanas `auto_ets` extrapola bajo cero, y ese SKU —1.440 unidades vendidas— habría salido `dead_stock`. El test usa ahora la serie **real** y afirma primero que el pronóstico es negativo.

**Verificación:** `tests/test_negativa_no_es_cero.py`, nuevo. Suite completa **8.746 passed, 0 failed**.

## Decimoquinta entrega: por qué Kern queda por debajo del piso, contestado y medido (`a28bf5c`, `cadb608`)

La pregunta que la undécima dejó escrita en su propia sección de *qué no dice*, cerrada en `documentation/KERN_POR_QUE_BAJO_EL_PISO.md` —línea base congelada, script que la regraba, test anti-deriva— sobre los MISMOS mundos y parámetros con los que se grabó `captura_limpia.json`.

**Kern no pierde por dimensionar mal el buffer: pierde por comprar TARDE.** Todo el déficit es UNA línea: gasta $43.607 menos en comprar y pierde **$78.541 más** en venta perdida, con fill 0,621 contra 0,817 del piso. **El 56 % de la brecha es el arranque muerto** (`MIN_PERIODS_FOR_POLICY` es 6, el arnés replanifica cada 4 con 4 de calentamiento, así que la primera política usable llega en la semana 8 y el piso arranca en la 4): reemplazar SOLO las semanas 4-7 por la política del piso mejora en los **cinco** mundos, −47,0 % → −20,6 %. Y **bajar `MIN_PERIODS_FOR_POLICY` no es el arreglo** — medido, la corrida sale `qa_failed` y la política vuelve vacía igual.

**La hipótesis del buffer quedó REFUTADA.** Con la cobertura exacta del piso compra 3.100 unidades contra 3.064 y captura **peor** (−0,483 contra −0,470): el dinero extra no se convierte en venta sino en stock terminal, $26.191 contra $7.525. El mecanismo está en el perfil de compra por tercio — el oráculo carga adelante y **corta**, cerrando con terminal $0; Kern hacía lo contrario, **cero** al principio y pidiendo hasta la última semana, porque un punto de reorden no tiene noción de horizonte.

**El test anti-deriva cubre las tres formas en que un documento así miente sin que nadie lo note**, las tres verificadas por mutación: que la medición no sea del mismo experimento que produjo el −40,6 %; que un contrafáctico **no sea** un contrafáctico (un carril idéntico al base no midió nada, el mismo defecto que `ShockImpact.reached` declara en el marcador); y que presente como confirmada una hipótesis que la medición refutó.

**Lo que el documento declara que NO dice:** que mide **5 de los 25** mundos y que su promedio base (−47,0 %) es peor que el publicado (−40,6 %) porque entre esos cinco está el peor del barrido; que el efecto de fin de horizonte castiga a cualquier política contra un oráculo que sabe cuándo termina; y que no propone el arreglo sino que nombra la decisión.

**Verificación:** `tests/test_por_que_bajo_el_piso_publicado.py`, nuevo: 12 tests. Suite completa **8.759 passed, 0 failed**. `ruff check .` limpio.

## Decimosexta entrega: el arranque muerto, cerrado y medido (`e2b4435`, `8c6ff33`)

Lo que la decimoquinta nombró y dejó abierto. **Negarse a dimensionar no obliga a no decir nada**: son dos preguntas y se contestaban las dos con silencio. *Cuánto buffer* necesita σ y sin `MIN_PERIODS_FOR_POLICY` (6) observaciones no hay σ honesta — **sigue sin contestarse**. *Cuánto hace falta para llegar a la próxima reposición* necesita la media medida y el plazo del proveedor, que el archivo ya trae.

Entre `MIN_PERIODS_FOR_COVER` (2) y 6 períodos sale una **cobertura provisional**: `src/risk_period.py::provisional_cover` = `media × (plazo + revisión)`, que es el `mu_x` de la ecuación 6.4 —la identidad del propio motor, la misma que `src/policies.py` usa como base del punto de reorden— con el término de seguridad **omitido, no puesto en cero**. Debajo de dos períodos no hay ni media que citar y la negativa se queda entera.

**No es una política, y el registro lo declara en la FORMA de sus campos**: disparador y objetivo sí (`reorder_point == order_up_to == cobertura`), `safety_stock` / `z_factor` / `forecast` en `None`. Cubre la media, o sea que se espera que se quede corta cerca de la mitad de las veces, y su capital va **aparte** (`provisional_investment`) porque no pasa por el allocator. Por eso las listas son **dos**: `UNSIZED_POLICY_KINDS` es "el motor no puso ningún número" y la lee `jobs/qa.py`, que exige los cuatro campos en `None`; `NOT_FULLY_SIZED_POLICY_KINDS` le agrega la cobertura y la leen `coherencia.py` —sus invariantes por SKU presuponen buffer y z— y `package_specs.py`, porque una cobertura sin buffer en la planilla de pedidos del cliente pisa su número propio y además **parece** una política. `jobs/qa.py` chequea la forma entera en las dos direcciones, que la aritmética cierre (`reorder_point == media × tau`) y le aplica su **propia** invariante de evidencia en vez de eximirla.

**La cobertura también dice CUÁNDO.** `_order_now` la trata con la aritmética de un `(R,S)` —es eso: un objetivo sin lote—, así que entra en `order_now` y en la OPCIÓN *"Release the N orders due now"*, que la **nombra aparte** (*"no safety stock behind it"*). Sin esa mitad el arreglo quedaba partido: el marcador ejecutaba la cobertura y la lista que lee el cliente no la nombraba, o sea que el número publicado describía una corrida que un cliente no podía reproducir. El Tower sigue **callado a propósito** (`forecast` en `None`): `order_now` es una lista que el operador va a buscar, un `rop_breach` es un evento que se empuja, y sin buffer el estante cae por debajo la mitad de las semanas.

**Arreglado de paso, el mismo defecto un piso más arriba:** una cartera ENTERA de coberturas no tiene ningún SKU declinado, así que `unsized_note` caía al encabezado normal y publicaba *"recommended inventory investment $0 at 95% service level"* sobre una corrida sin ninguna política dimensionada y con plata que gastar. Ahora lo que decide el titular es cuántos SKU quedaron **dimensionados**.

| | antes | después | |
|---|---:|---:|---|
| **captura** | **−47,0 %** | **−27,6 %** | **+19,4 puntos, mejora en los CINCO mundos** |
| fill | 0,621 | 0,750 | el piso está en 0,817 |
| unidades | 2.228 | 2.987 | el piso compra 3.064 |
| **semanas 0-7** | **0** | **543** | el piso 808, el techo 1.359 |

**Dos controles que hacen que la comparación signifique algo.** Los carriles `piso`, `techo` y `arranque_piso` salen **idénticos** en las dos corridas —o sea que es el mismo experimento y lo único que se movió es el código—, y el contrafáctico que midió el defecto **se encogió**: explicaba el 56 % de la brecha y ahora el 25 % de una brecha más chica. No la cierra: el residual es el mecanismo de E3.

**⚠️ Cambio de comportamiento.** Un cliente con un SKU de entre dos y cinco períodos empieza a recibir un número donde antes recibía silencio. Sale como OPCIÓN con su residual, nunca como algo que Kern ya decidió.

**Verificación:** `tests/test_cobertura_provisional.py`, nuevo: 27 tests. Cuatro archivos adaptados **por intención**, no por sustitución ciega — `test_insufficient_history.py` cambia el contrato que afirmaba (`..._is_refused` → `no_length_below_the_threshold_produces_a_POLICY`, con las dos formas y sin lote ni buffer en ninguna), y `test_negativa_no_es_cero.py` pasa a dos series con nombre porque una negativa entera necesita hoy UN período. Suite completa **8.802 passed, 32 skipped, 19 deselected, 0 failed**. `ruff check .` limpio.
