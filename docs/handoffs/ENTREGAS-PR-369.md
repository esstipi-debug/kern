# Entregas de la rama `claude/keen-goodall-jfnqo9` (PR #369)

Fecha: 2026-09-20 · Rama: `claude/keen-goodall-jfnqo9` @ `63bc230` ·
PR: [esstipi-debug/kern#369](https://github.com/esstipi-debug/kern/pull/369)

El cuerpo de un PR de GitHub topea en **65.536 caracteres**, y esta rama lo alcanzó en la
decimonovena entrega: quedó en 65.227, o sea 309 de margen, y la vigésima no entraba. Este
documento es la mitad vieja de ese historial, movida acá para que las siguientes entren.

**Qué hay acá:** las entregas **segunda a vigesimoséptima**, verbatim y en orden cronológico
—de los términos del proveedor hasta la re-medición de las palancas sobre 25 mundos—. No se resumió nada al mudarlas:
un historial que se resume cuando se archiva deja de ser un historial.

**Qué NO hay acá, y dónde está:**

| | dónde |
|---|---|
| Primera entrega (el quinto sombrero, `transporte`) | el cuerpo del PR, como marco: *Qué problema cierra*, *El modelo*, *El quinto sombrero es condicional*, *Cambio de comportamiento*, *Alcance*, *Verificación* |
| Vigesimoctava a trigésima | el cuerpo del PR |
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

## Decimoséptima entrega: el residuo de la brecha, diagnosticado (`58f5eed`, `4fef196`)

`documentation/KERN_RESIDUO_DE_LA_BRECHA.md`, sobre los MISMOS cinco mundos: **el carril de control sale idéntico al último dígito** (`-0.27623947911345204`) al de la línea base anterior, así que es el mismo experimento y no una medición nueva que se le parece.

**El residuo no es una cosa que Kern haga mal.** Es que un punto de reorden no tiene noción de horizonte, y **toda palanca que baja el quiebre empuja el excedente al stock terminal** — que sube monótonamente con cada una (kern 9.209 → sigma_obs 13.773 → proveedor_medido 19.026 → servicio_econ 22.794 → **todo 33.435**, contra 0 del techo) hasta que el carril que las apila compra **3.763 unidades cuando el oráculo, con información perfecta, compra 3.172**.

**Tres cosas que no eran obvias.** (1) La palanca grande es el **nivel de servicio** (−27,6 % → **−8,8 %**, +18,9 puntos), no el plazo (+2,4 a +5,0): la razón crítica de esta economía es **0,9976**, no 0,95 — y es la única que Kern hoy **no puede accionar**, porque la lente exporta `unit_cost` y no precio; `jobs/intake.py` sí sabe leer `revenue`, así que es una brecha de **canal, no de motor**. (2) **Medir mejor al proveedor con las propias recepciones ayuda poco y no es confiable**: empeora en **3 de 5** mundos. (3) **Apilar palancas es peor que la mejor sola** (−18,6 % contra −8,8 %), con la varianza explotando de +0,1106 a −0,3029. Lo que el residuo pide no es más buffer sino **noción de horizonte** — y el documento declara que **no lo midió**.

**El defecto que apareció al medir.** El primer carril, el que le pasa a Kern la dispersión del plazo observada en sus propias recepciones, salía **idéntico dígito por dígito** al de control. No era el modelo: `jobs/inventory_optimization.run` acepta `lead_time_stds` y `scm_agent/tools.py` **nunca lo reenviaba**. Sin error y sin aviso: la política salía dimensionada como si el proveedor no fallara nunca —`σ_L² · μ_d²` es el término **dominante** del período de riesgo— con el entregable diciendo igual *"95 % service level"*. Con `lead_time_stds={"A": 3.0}` el buffer pasa de **11,9 a 238,4** llamando al job, y quedaba en 11,9 por el orquestador. Es el defecto de la rama **al revés**: en la negativa-como-cero la prosa avisaba y el número mentía; acá la prosa ya decía *"defaulted to 0"* y lo que fallaba era la cañería.

**Verificación:** `tests/test_residuo_publicado.py` (14) y `tests/test_overrides_no_se_tiran.py`, nuevos. Suite completa **8.819 passed, 0 failed**.

## Decimoctava entrega: once hechos del cliente que el agente tiraba en silencio (`6b2de29`)

El guard que nació con `lead_time_stds` cubría **un** tool. Generalizado a los 60 —recorriendo por AST toda llamada a `jobs.*` dentro de `scm_agent/tools.py`— la auditoría dio **86 parámetros sin reenviar**, y **once eran hechos del cliente**. Cada uno reproducido en las **dos** direcciones antes de tocar nada: que no llega por el orquestador, y que si llegara mueve un campo del reporte. Un parámetro que no llega pero tampoco cambia nada no vale una línea, y esa mitad es la que separa un defecto de una perilla que nadie quiso exponer.

**El más caro: el mapeo explícito de columnas.** `intake.detect_columns` acepta `overrides` con la precedencia **más alta** —"mi columna `bultos_despachados` ES la cantidad"—, distinto de `column_aliases`, que es un vocabulario y sí se exponía. Sin él, un export cuya columna de cantidad la tabla no conoce salía `needs_data` y **no había forma de contestarle desde afuera**: el operador leía "falta la columna cantidad" sobre un archivo que la tiene, con otro nombre. Va a los **tres** lectores del mismo archivo, porque si no se arregla la demanda y se sigue sin poder nombrar la columna de stock.

Los otros diez, con lo que costaba cada uno: `service_level_by_class` (el override de los packs verticales, en tres YAML — y el docstring del propio job cuenta que este mismo drop ya se arregló una capa más abajo); `units_sold` (sin denominador, `return_rate_by_reason` **no se calcula**, o sea que el número por el que se corre ese tool salía `None` sobre todo archivo); `stockout_periods`/`total_periods` (sin los dos, `stockout_rate` es `None`); `counts_per_year`; `as_of` y `today` (sin ellos la única respuesta posible era "hoy", y un operador que reconcilia el cierre del trimestre pasado no tenía cómo pedirlo); `now`; y `default_unit_cost` —el gemelo de `default_lead_days`, que sí se pasaba—, donde el 1,0 por omisión no es un costo sino un marcador que multiplica toda cifra de inversión.

**Una fecha que cruza la frontera JSON** se parsea en un solo lugar y una ilegible **levanta** nombrando la clave: el reloj como fallback silencioso convertiría un typo en un reporte fechado hoy que dice estar fechado en otro lado, y fechar mal un análisis de envejecimiento es la clase de error que nadie encuentra leyendo el entregable.

**⚠️ Un hallazgo que NO se arregla acá, y el test lo deja visible.** `nosilo_core.writeback` garantiza que *"the same idempotency_key never lands twice"*, y el default de `channel_replenishment_job.run` es la **constante** `"channel-replenish-1"`, que `_channel_run` nunca pisaba: la **segunda** reposición legítima de un cliente choca contra la primera y no aterriza. Poder pasar la clave es la mitad del arreglo; que el default no deba ser una constante compartida por todos los clientes y todas las corridas es una decisión de diseño sobre las claves de writeback. El test lo afirma explícito —sin override la clave **es** esa constante—, así que el día que se arregle se pone rojo y hay que venir a mirarlo.

**El guard.** Entiende dos cosas o acusa de más: que un destino se llama desde **varios** sitios y alcanza con que uno reenvíe el parámetro (`risk_job.run` recibe `severity_thresholds` en la rama que las tiene y no en la otra: eran cuatro falsos positivos), y que un kwarg puede viajar dentro de un `**{...}` condicional, que es la forma correcta cuando el job no acepta `None`. Las exenciones van por **nombre** cuando son genéricas —`prepared`, `lang`, `data_source`, `residual` suman 65 de las 75 que quedan— y por `módulo.función.parámetro` cuando la razón es propia: `min_periods` está ahí porque exponerla dejaría saltear la negativa del motor desde afuera, y `sop_job.confidence` porque **una confianza que el llamador tipea no es una medición**. Cada entrada lleva su razón escrita y un test la exige: un conjunto pelado es el cajón donde va a parar lo que nadie quiso decidir. Verificado por mutación sobre tres reenvíos distintos.

**Verificación:** `tests/test_overrides_no_se_tiran.py`, 15 tests. Suite completa **8.831 passed, 32 skipped, 19 deselected, 0 failed**. `ruff check .` limpio.

## Decimonovena entrega: una clave de idempotencia constante es un no-op mudo (`63bc230`)

El hallazgo que la decimoctava dejó abierto, cerrado. `nosilo_core.writeback` garantiza —y lo dice en su encabezado— que *"the same idempotency_key never lands twice"*. Eso es lo que hace seguro reintentar, pero pide la contraparte, que es la que se olvida: **la clave tiene que ser distinta para una operación distinta**.

**Reproducido con el conector Odoo de demo**, sobre la constante `"channel-replenish-1"` que traía `channel_replenishment_job` y que el agente nunca pisaba: `stage_restock({SKU-1: +50})` sale `applied=True`, y `stage_restock({SKU-2: +999})` sale **`applied=False, idempotent_skip=True`**. Contenido distinto (`content_hash` distinto), misma clave, y la segunda se descarta **en silencio** — con la señal que significa *"esto era un reintento, todo bien"*. El punto de reorden del segundo SKU nunca aterrizaba. **No era el único:** `repricing_job` tenía `f"repricing-{channel}-1"`, constante **por canal**, y ahí lo que no aterriza es un precio en un marketplace vivo; más tres CLI de `examples/`, que valen igual porque son la plantilla de la que se copia.

**Y no era ignorancia del repo.** `excel_replenishment_job` ya derivaba del contenido estacionado más las celdas guarda que leyó del archivo vivo, `shopify_stock_sync` tiene una función dedicada, y `scm_agent/autonomy_promotion.py` lleva el comentario que lo explica entero: *"Unique per occurrence (NOT a fixed `degrade-<event_type>` key) … a reused idempotency_key would make writeback.apply() treat that second, genuinely different degradation as an idempotent skip of the first"*. Tres lugares con la regla escrita y dos que no la seguían.

La definición va **donde vive la semántica**: `nosilo_core.writeback.derive_idempotency_key(prefijo, edits)`, que cumple las dos mitades —estable para la misma operación (si no, se rompe la idempotencia y un reintento aplica dos veces) y distinta para otra (si no, se rompe la garantía de que aterrice)— con el prefijo separando emisores. **No se tocaron** los dos que ya estaban bien: cambiarles la derivación invalidaría `applied_keys` ya registradas sin ningún beneficio.

**El guard, en tres mitades.** (1) **Ninguna clave puede ser un literal** — se decide leyendo el AST y no admite excepciones, y reconoce el andamio de `src/connectors/odoo.py`, que pasa un placeholder y re-keyea con el `content_hash` en la línea siguiente. (2) **Los emisores sondeados se corren de verdad**: misma operación → misma clave, operación distinta → otra, y las dos reposiciones del caso reproducido **aterrizan las dos**, con el reintento de la primera todavía deduplicando. (3) **Todo el que estacione un changeset está sondeado o declarado** con su razón —el patrón de `NO_EMISORES` del barrido de neteo—, incluidos los seis conectores que solo reenvían: acusarlos sería acusar al cartero, pero tienen que estar nombrados, porque el día que uno empiece a fabricarla la razón deja de ser cierta.

Verificado por mutación. Y el test que en la entrega anterior afirmaba que sin override la clave **era** la constante —puesto ahí para ponerse rojo el día que esto se arreglara— se puso rojo, y se vino a mirarlo.

**Verificación:** `tests/test_claves_de_idempotencia.py`, nuevo: 11 tests. Suite completa **8.842 passed, 32 skipped, 19 deselected, 0 failed**. `ruff check .` limpio.

## Vigésima entrega: el efecto de fin de horizonte NO explica el residuo (`5c7a043`)

`documentation/KERN_HORIZONTE_DE_LA_VARA.md`. La decimoséptima dejó una deuda escrita en su propia sección de límites —*"no diagnostica el efecto de fin de horizonte por separado… parte del residuo es de la vara y no de Kern. Cuánta parte queda abierto"*— y el orden importaba: **antes** de darle noción de horizonte a Kern hay que saber cuánto del −27,6 % se lo debe la vara, porque si fuera casi todo, "darle horizonte" sería enseñarle a ganarle al marcador en vez de a comprar mejor.

**No se lo debe.** Con el borde de la ventana acreditado, mover la fecha de fin del mundo —de "se acaba en la semana 24" a "quedan 24 semanas más"— mueve la captura de Kern **−0,0054**: el **2,0 %** del residuo, y para el lado peor. Chico en los **cinco** mundos (−0,0171 a +0,0019), no un promedio que esconde dispersión. Lo enorme es la **convención de puntuación**: sin acreditar el borde, ese mismo eje mueve la captura **−0,4051** (de −0,08 a −0,99 por mundo). Ese efecto aparente no es el horizonte, es decidir que lo comprado y no vendido al cerrar la ventana vale cero.

**El mecanismo es más simple que el experimento.** `capture = (piso − kern) / (piso − techo)`: el castigo de fin de horizonte lo sufren **los dos** términos del numerador, porque el piso es tan ciego al horizonte como Kern. Lo que sobrevive a la resta no es el castigo sino su **diferencia**, que es chica. La columna `terminal` se recolectó justamente para exponer ese artefacto —su docstring dice que está para *"distinguir una decisión mala de un artefacto de medir sobre un horizonte que se acaba"*— y la normalización ya lo había neutralizado antes de que nadie mirara la columna.

**El diseño, y por qué hay que truncar.** Se sortea un mundo de 48 semanas y se lo **trunca** con `dataclasses.replace` —transformación pura, mismo contrato que `shocks.py`, el `rng` no se toca—. Re-sortear con `weeks=24` **no serviría**: `_seasonal_trend_demand` normaliza la tendencia por `max(weeks,1)` y sortea el ruido en un bloque `(n_skus, weeks)`, así que dos mundos de la misma semilla y distinto largo difieren **también en sus primeras 24 semanas**. **El control:** piso y Kern son ciegos al horizonte, así que sus costos sobre la ventana tienen que salir **idénticos** entre las tres fechas de fin. Salen idénticos en las cinco semillas, y el script se planta antes de publicar si alguna vez no lo fueran.

**⚠️ El sesgo que apareció midiendo, y que alcanza a lo ya publicado.** La primera versión acreditaba el borde contando **solo el estante** y daba −0,20. El número no cerraba: el techo con futuro gastaba 39.856 más dentro de la ventana y solo aparecían 14.679 de stock. El resto estaba en la **cañería** — `score_run` cobra `procurement` de toda unidad **ordenada**, arribe o no, y las que siguen viajando al cerrar la ventana están pagadas, no vendidas, y no aparecen en `on_hand_end`. Y el sesgo **no es parejo**: un techo que sabe la fecha de fin deja de ordenar y llega al borde con la cañería vacía (**cero exacto en las cinco semillas**), y uno con futuro llega con ella llena. Medida sobre piso y Kern, la cañería pesa **1,8× el estante**, o sea que `stock_terminal_usd` de `por_que_bajo_el_piso._componentes` se pierde dos tercios del número. La conclusión no cambia —la cañería también se cancela en la normalización—, pero **la sección R3.3 del residuo descansa sobre la mitad chica: queda marcada, no corregida**.

**No existe una convención de borde neutra.** Lo que queda vale su margen futuro, que la ventana no ve: `unit_cost` es cota inferior, `price` cota superior, y la superior **se degenera** justo donde más falta haría —con la fecha de fin conocida el techo llega sin nada que acreditar mientras el piso cobra el suyo a precio de venta, y en la semilla 1003 la vara se da vuelta (span −7.647). Ahí `captura` devuelve `None` y **nunca** un `nan`: un `nan` promediado con `fmean` sale publicado como si fuera una medición. El resumen lleva el conteo de mundos medibles (**4 de 5** en esa fila).

**Consecuencia de producto:** darle horizonte a Kern **ya no se justifica como cerrar un artefacto**. Y la cadena de un cliente real no se acaba, así que apuntarle a la fecha del marcador sería optimizar contra el arnés; la versión desplegable es una fecha de fin **real** por SKU —discontinuación, fin de temporada, cierre de ventana promocional— que el archivo del cliente tiene que traer, con la ausencia declarada. Es la forma de todas las demás capacidades de esta rama, y `launch_readiness` ya netea contra una fecha.

**Verificación:** `tests/test_horizonte_publicado.py`, nuevo: 14 tests. Fija el control en las cinco semillas, que el truncado sea puro, que re-sortear **no** sirva (rojo el día que coincidan), que el borde sea estante más cañería, que la cañería pese más que el estante, que un span degenerado salga `None`, y que el efecto acreditado sea chico **en cada mundo** y menor que el no acreditado. Suite completa **8.856 passed, 32 skipped, 19 deselected, 0 failed**. `ruff check .` limpio.

## Vigesimoprimera entrega: la monotonía del cierre era un artefacto de medir media columna (`dc7d764`)

La vigésima dejó una afirmación **marcada, no corregida**: R3.3 del residuo publicaba el mecanismo como una sola columna —*"el stock terminal sube monótonamente con cada palanca"*— y esa columna mide el **estante** y nada más. Esto la mide con el número completo.

**La monotonía es falsa.** `score_run` cobra `procurement` de toda unidad **ordenada**, arribe o no dentro del horizonte, así que el capital pagado y sin vender al cierre son **dos** cosas: el estante (`on_hand_end`) y la **cañería** (`ordenado − recibido`, que sigue viajando). Con el número completo la secuencia publicada queda **kern 30.440 → sigma_obs 31.805 → proveedor_medido 39.116 → servicio_econ 33.949 → todo 51.751**: `servicio_econ`, la palanca **más grande** (+18,9 puntos de captura), deja **menos** capital inmovilizado que `proveedor_medido`, que da +4,9. Y no es el promedio escondiendo dispersión — con el número completo la secuencia no es monótona en **0 de 5** mundos, mientras que el estante solo lo es en 4 de 5 y en la media.

**Por qué el estante engañaba, que es el hallazgo real.** La razón cañería/estante cae de **2,3** (piso y kern) a **0,5** (`servicio_econ` y `todo`). Un nivel de servicio más alto compra **antes y más**, así que al cerrar el horizonte eso ya aterrizó en vez de seguir viajando. El estante subía monótonamente porque las palancas **mueven capital de la cañería al estante**, no porque el total suba de forma ordenada.

**Lo que sobrevive, más débil de lo publicado:** toda palanca deja más capital pagado y sin vender que el control —de +1.365 (`sigma_obs`) a +21.311 (`todo`)—, pero ni eso es universal: tres de las cuatro quedan **por debajo** del control en 1 de 5 mundos; `proveedor_medido` es la única que sube en los cinco. **Lo que no se toca:** `todo` compra **3.763 unidades cuando el oráculo, con información perfecta, compra 3.172**. Esa afirmación es sobre unidades pedidas, no sobre el cierre, y es la mitad fuerte del argumento.

**La decisión de diseño: `stock_terminal_usd` NO se redefinió.** Ese campo mide el estante y eso es lo que su nombre dice — no estaba mal, estaba **incompleto para la pregunta que se le hacía**. `_componentes` gana `canieria_usd` y `pagado_no_vendido_usd` (más los conteos de unidades), así que cada afirmación puede declarar sobre cuál de los tres descansa. Redefinir el viejo habría dejado toda línea base anterior incomparable sin que nada lo dijera.

**El control de la regrabación.** Se re-corrió la medición completa y se comparó campo por campo contra la línea base publicada: **cero diferencias** en todo campo preexistente, resumen y por semilla. Es el mismo experimento con una lente más completa, no una medición nueva que se le parece — y el test que ya exigía que el control reprodujera `por_que_bajo_el_piso_con_cobertura.json` sirvió de guard sin tocarlo.

**Verificación:** el test de monotonía se partió en cinco, y el nuevo se pone **rojo si el número completo volviera a ser monótono** — para que nadie reescriba la versión vieja sin volver a medir. Suite completa **8.860 passed, 32 skipped, 19 deselected, 0 failed**. `ruff check .` limpio.

## Vigesimosegunda entrega: las cuatro afirmaciones de cierre del "por qué bajó el piso", y una estaba al revés (`d3346bd`)

La vigesimoprimera corrigió R3.3 del residuo y dejó **declarado** que `KERN_POR_QUE_BAJO_EL_PISO.md` apoya cuatro afirmaciones en la misma columna incompleta, sin regrabar. Esto las corrige.

**Las dos líneas base de ese documento no son simétricas, y eso decide el alcance.** El *después* se regraba con el código actual. El *diagnóstico* está congelado pre-arreglo **a propósito** —el propio documento dice que *"es lo que motivó la decisión y borrarlo dejaría la conclusión sin su evidencia"*— y ese código ya no existe, así que sus cifras de cierre **no se pueden completar**. Se regrabó lo regrabable, se marcó el resto como estante solo, y un test nuevo se pone rojo el día que el diagnóstico gane columnas. Control de la regrabación: **cero diferencias** en todo campo preexistente, promedios y por mundo.

**⚠️ Una de las cuatro estaba al revés.** La viñeta decía *"castiga **más** a Kern que al piso —$4.572 contra $7.525 de terminal **hoy**"*, con **dos errores encadenados**: el $4.572 es el Kern de **antes** del arreglo, no el de hoy (hoy es $9.209); y con **ese** número la conclusión sale **invertida**, porque 4.572 es *menos* que los 7.525 del piso — o sea que diría que el fin de horizonte castiga más al **piso**. Con el número correcto y completo la conclusión **sí** se sostiene, y más fuerte: Kern inmoviliza **$30.440 contra $25.017** del piso, en **4 de 5** mundos (con el estante solo eran 3 de 5). Y encaja con la vigésima entrega: acreditar el borde deja a Kern **peor**, y **ésta es la razón**.

**Las otras tres.** (1) El techo cierra en **$0** y ahora se sabe que es cero **entero** —ni estante ni cañería—: un Wagner-Whitin que sabe la fecha de fin no deja nada viajando; la afirmación se **refuerza**. (2) *"Todo lo que hace comprar más termina en stock terminal"*: la **dirección** se sostiene con el número completo —los dos carriles que compran más que Kern dejan más inmovilizado, +1.307 y +26.908— pero el **nombre** no: es estante **más** cañería. (3) El **$26.191** de E2 está congelado y no se puede completar, pero el **mecanismo** se re-midió sobre la corrida de después: `cobertura_piso` compra **+1.068 unidades** y deja **+26.908** más de capital pagado y sin vender.

**Donde el número incompleto NO engañaba, también se dice.** De esos +26.908 de `cobertura_piso`, **+26.971 son estante**: la cañería casi no se mueve (21.230 → 21.167), porque forzar la cobertura hace comprar **antes**, y lo que se compra antes **llega** antes. Ahí la columna vieja daba la respuesta correcta. Un documento que corrija solo donde le conviene no es una corrección, y hay un test que lo exige.

**Verificación:** `tests/test_por_que_bajo_el_piso_publicado.py`, 5 tests nuevos —la identidad del cierre, que el diagnóstico siga congelado, el techo en cero entero, la viñeta corregida con su conteo de mundos, y el caso donde el estante no engañaba—. Suite completa **8.865 passed, 32 skipped, 19 deselected, 0 failed**. `ruff check .` limpio.

## Vigesimotercera entrega: una fecha de fin por SKU paga +16,2 puntos, y con datos que el cliente ya tiene (`f04dda6`)

`documentation/KERN_HORIZONTE_POR_SKU.md`. La vigésima cerró que el fin de horizonte **no** explica el residuo y dejó abierta la única versión **desplegable** de "horizonte": no la fecha en que se acaba el marcador, sino una **fecha de fin real por SKU** —discontinuación, fin de temporada, cierre de ventana promocional—. La cadena de un cliente no se acaba; **un SKU sí**. Esto **mide** esa idea: no agrega un campo al intake ni una regla al motor, porque se mide antes de construir.

**La regla.** *No acumules por encima de lo que todavía podés vender.* Con `restante` = demanda que queda hasta la fecha de fin del SKU, se topean las **dos** mitades: `order_up_to = min(order_up_to, restante)` **y** `reorder_point = min(reorder_point, restante)`. Las dos, o no sirve — `apply_policy` pide hasta el objetivo cuando la posición cae al disparador, así que topear una sola deja la orden igual de grande. Un SKU sin `restante` queda **intacto**: topear con un cero que nadie midió sería decirle que no compre nunca más.

**+16,2 puntos, y mejora en los CINCO mundos** (−27,6 % → −11,5 %). Es la segunda palanca más grande de la rama después del nivel de servicio (+18,9), y a diferencia de aquélla **ésta sí se puede accionar**. Y el ahorro es casi **puro**, no un intercambio:

| | kern | con tope | Δ |
|---|---:|---:|---:|
| unidades | 2.987 | 2.634 | **−12 %** |
| compra (usd) | 135.359 | 123.048 | **−12.311** |
| quiebre (usd) | 109.567 | 109.935 | +368 |
| fill | 0,750 | 0,748 | −0,002 |
| capital pagado y sin vender | 30.440 | **18.276** | **−40 %** |

Gasta 12.311 menos y paga 368 más de quiebre; el servicio no se mueve. Es la **primera** palanca de la rama que no es *"comprá más"* sino *"comprá menos de lo que no vas a vender"* — justo la forma del residuo que ninguna de las otras podía tocar.

**Saber el futuro verdadero no compra nada.** Dos carriles: uno topea con la demanda futura **real** del mundo (nadie la tiene) y el otro con la **media observada × semanas restantes** (lo que un cliente calcula con su propio historial). Salen **+0,1348** y **+0,1618**: equivalentes. Difieren más de 0,02 en solo **2 de 5** mundos; en los otros tres es ±0,004, ruido. La hipótesis de que el estimado ganaba por ser un tope más apretado se **testeó y se descartó** —sobre 160 casos aprieta en el 59 % y promedia el 99 % del real, y no correlaciona: la semilla 1002 no aprieta y gana +0,060, la 1004 es la que más aprieta y gana +0,0006—. Por eso la palanca es desplegable: **no necesita información que nadie tiene, necesita una fecha**.

**⚠️ El límite que vuelve honesto al titular.** En el marcador **todos los SKU mueren la misma semana**, así que +16,2 es el **techo de la idea** sobre una cartera donde toda la línea se discontinúa a la vez. Una cartera real tiene fechas escalonadas y la mayoría de sus SKU **no tiene ninguna**: lo realizable es proporcional a la fracción que traiga la fecha, y este número **no se le promete a nadie**. Falta además medir qué pasa con una fecha **equivocada**, que es la pregunta de seguridad de la capacidad.

**Verificación:** `tests/test_horizonte_por_sku_publicado.py`, 5 tests —que pague en **cada** mundo y no en promedio, que los dos carriles sigan siendo equivalentes (nombrando las dos semillas donde difieren), que el ahorro siga siendo puro, que el documento declare que todos los SKU mueren juntos, y que el tope cubra las dos mitades dejando intacto al SKU sin dato—. Suite completa **8.870 passed, 32 skipped, 19 deselected, 0 failed**. `ruff` limpio.

## Vigesimocuarta entrega: la línea base ya no está vencida — y la mitad que no se pudo regrabar es el hallazgo (`6db47a4`, `bd77cec`)

El titular vendible del repo —*"Kern decide peor que un punto de reorden fijo en las dos condiciones"*— era del **18-sep** (`b1e3293`), **anterior** a la cobertura provisional. O sea que describía un código que ya no existe. Esto lo vuelve a medir.

**La mitad limpia: −40,6 % → −13,8 %. +26,8 puntos.** Y no es una media que esconda dispersión, que es el criterio que esta rama se exige:

| | 18-sep | hoy | Δ |
|---|---:|---:|---:|
| media | −40,6 % | **−13,8 %** | **+26,8** |
| mediana | −36,4 % | −6,3 % | +30,1 |
| p90 | −26,3 % | **+1,0 %** | +27,2 |
| mejor mundo | −15,9 % | **+12,7 %** | +28,7 |
| peor mundo | −71,9 % | −59,6 % | +12,3 |

**Mejora en 25 de 25 mundos** (mínimo +9,4, máximo +42,6, t = 13,5) y **cuatro mundos** (1005, 1010, 1016, 1022) salen con captura **positiva**: ahí la política de Kern cuesta *menos* que el piso. Con datos limpios el titular ya no se sostiene sin matizar; en los niveles sucios sigue costando mucho más.

**El control es lo que hace creíble el delta.** El mismo barrido corrido hoy en un worktree de `b1e3293` reproduce su archivo grabado **bit a bit**: 48 campos, **cero diferencias**. Las dependencias de este repo se resuelven frescas en cada corrida —ya se puso rojo por un release de upstream sin un commit propio—, así que la deriva del entorno era una explicación viva. No lo es: los +26,8 son producto. Segundo amarre: las dos capturas por semilla que `5408b2f` dejó escritas (1003 y 1004) coinciden al dígito.

**Y solo incluye UNA de las tres palancas medidas en la rama.** La cobertura provisional está en el motor; el nivel de servicio (+18,9) es un *carril* de `scripts/residuo_de_la_brecha.py` y la fecha de fin por SKU (+16,2) un *post-proceso* en `scripts/horizonte_por_sku.py` — ninguna existe fuera de `scripts/`. La predicción escrita **antes** de medir era −21,2 % extrapolando desde cinco mundos: se quedó corta por 7,4 puntos.

**⚠️ La mitad bajo disrupción NO se pudo regrabar, y eso vale más que el número.** `verify_fidelidad.py --shock` salió con **código 1 y se negó a grabar** porque `degrada_con_la_suciedad` disparó: `L3 captura -70.0% > L2 -92.9%`. Esa compuerta no se puentea. El diagnóstico, y **no es una fuga** —la lente no toca el marcador—: al pasar de L2 a L3 Kern compra **466.062 → 888.718 (+91 %)** y el quiebre baja 857.000. Mejora porque el error que corrige es enorme: **en los niveles sucios Kern colapsa a casi-no-pedir** (semilla 1013: compra 19.780 donde el piso compra 202.167, fill 0,22 contra 0,79). Cualquier cosa que infle la demanda ayuda. No es nuevo —el orden invertido ya estaba en las dos líneas base del 18-sep (+10,2 y +7,7)—, la cobertura lo llevó a cruzar el umbral de ruido.

**Y la columna limpia pasó esa misma compuerta por 0,6 puntos** (+24,9 contra un margen de 25,5). El documento lo declara: el aviso vale para sus filas L2 y L3. La fila **L0 —el titular— no está implicada**, porque de L0 a L1 la captura se desploma 103 puntos.

**Segundo hallazgo, de paso: una fecha ambigua apaga la negativa del motor.** L1 salió **idéntico bit a bit** en los 25 mundos (delta +0,0), y eso es un mecanismo, no ruido. `06/01/2025`, escrito `dd/mm/yyyy` por `inject_mixed_date_formats`, es ambigua y se parsea **month-first** → 1 de junio, cuyo lunes es 2025-05-26. La grilla semanal pasa de **4 semanas a 21** rellenas de ceros fabricados, y 21 ≥ `MIN_PERIODS_FOR_POLICY` hace que el motor **deje de negarse**: donde en L0 emite ocho `provisional_cover` honestas, en L1 emite **quince `(R, S)`** con pronóstico 0,22 para un SKU que vende ~16 por semana. Por eso la cobertura provisional compra exactamente cero a L1: **solo se dispara cuando el motor se niega**. La ambigüedad ya estaba documentada y aceptada en `jobs/intake.py::_parse_dates` (se revirtió un `dayfirst=True` peor); lo que ese comentario acota mal no es la causa sino el **radio**: declara *"una fila mal etiquetada"* y lo medido es *"la negativa deja de existir"*.

**Artefactos.** `captura_limpia_pareada.json` (la captura **por mundo** en las dos épocas — sostiene el "25 de 25" y no se puede rehacer desde un solo checkout, así que lleva los dos sha adentro) y `captura_bajo_shock_RECHAZADA.json` (la corrida rechazada con su motivo en el propio contexto: **evidencia, no referencia**). Las dos líneas base del 18-sep quedan congeladas con su nombre de commit.

**Verificación:** `tests/test_captura_bajo_disrupcion_publicada.py` pasa de **7 a 16 tests** — que los congelados sean comparables con los vigentes, que el documento cite sus frases verbatim, que el +26,8 y los cuatro extremos sean la **resta de los dos archivos** y no números tipeados, que el pareado diga 25/25 y reproduzca los agregados, que la rechazada se declare rechazada, que la columna con shock vigente siga siendo la congelada (**se pone rojo el día que se logre regrabarla**, que es cuando el documento tiene que volver a publicar el costo del shock) y que el documento declare que la columna limpia rozó la compuerta, recalculando brecha y margen. Suite completa **8.879 passed, 32 skipped, 19 deselected, 0 failed**. `ruff check .` limpio.

## Vigesimoquinta entrega: el colapso en los niveles sucios era LECTURA, y arreglarlo desbloqueó la línea base con shock (`7841f96`, `3bc13a7`, `bbfd346`, `9892d25`)

La vigesimocuarta dejó abierto el ítem más grande que había encontrado: la regrabación bajo disrupción **bloqueada** por una anomalía real, con el diagnóstico de que *"en los niveles sucios Kern colapsa a casi-no-pedir"*. Esto lo persigue hasta el fondo, y el fondo no estaba donde parecía.

### El gatillo: la suciedad "cosmética" cambiaba el dato

`src/simworld/lens.py` declara sus tres inyectores de nivel 1 como **"cosmético puro"** —la misma SKU mal escrita, el mismo día en otro formato, el mismo número en formato europeo—. `jobs/intake.py` no los leía igual, y el precio estaba a la vista sin que nadie lo leyera: **el nivel 1 había mejorado +0,0 puntos en los 25 mundos** cuando llegó la cobertura provisional. Exactamente cero, en los veinticinco. Eso no es ruido, es un mecanismo — y el mecanismo es que esa cobertura **solo se dispara cuando el motor se niega**, y a nivel 1 el motor no se negaba: emitía una política confiada sobre una serie que se había inventado.

Sobre el archivo real de la semilla 1000 a la semana 20, el nivel "cosmético" devolvía **16 SKU** (son 8), **912 unidades de 2.596** y un calendario que terminaba el 1-dic en vez del 19-may. Aislando cada inyector por separado, **dos causas**:

- `" sku-1003"` y `"SKU-1003"` eran **dos productos**. La línea hacía `.str.strip()`, que limpia el espacio y no la caja. Cada serie perdía la mitad de su historia y las dos mitades caían por debajo de `MIN_PERIODS_FOR_POLICY`.
- `"06/01/2025"` (6 de enero, `dd/mm/yyyy`) se parseaba **month-first** → 1 de junio. Una sola fecha estiraba la grilla semanal de **4 períodos a 21** rellenos de ceros fabricados, y con eso cruzaba el umbral: **el motor dejaba de negarse**.

`_fold_ids` pliega por identidad conservando la grafía **más frecuente** (con desempate alfabético y no por orden de fila: dos corridas del mismo archivo tienen que coincidir en cómo se llama un producto) y **declara** lo plegado en `IntakeQuality.folded_ids` — un catálogo que de verdad distinga `ab-100` de `AB-100` es raro pero posible, y lo único que no puede pasar es fusionarlos en silencio. `_slash_date_order` lee el orden de la **propia columna** (una fecha con barra cuyo primer componente es > 12 solo puede ser un día) y aplica el `dayfirst` **solo a los tokens con barra**: ésa es la diferencia con el `dayfirst=True` que se revirtió, que cambiaba mes por día en el ~39 % de las fechas ISO. Sin evidencia, o con evidencia **contradictoria**, cae al comportamiento de antes.

**La especificación es el invariante, no las piezas: si el nivel 1 es cosmético de verdad, tiene que leerse IGUAL que el nivel 0.** Ahora se cumple — coincidencia fila a fila en las 160.

| nivel | antes | con la lectura arreglada | mundos que mejoran |
|---|---:|---:|---|
| L1 | −117,0 % | **−28,3 %** | 24/25 · t = 10,1 |
| L2 | −116,8 % | **−48,1 %** | **21/21** · mínimo +20,5 |
| L3 | −91,9 % | **−26,3 %** | 20/21 · t = 8,7 |

Y el control de que toca solo lo que debe: **L0 sale bit a bit idéntico** en las dos condiciones. Son **dos palancas disjuntas** — la cobertura provisional arregló el arranque muerto (solo L0), la lectura arregló lo sucio. La época intermedia queda congelada en `captura_limpia_a7892b7.json`.

### La compuerta acusaba por el ordinal, y el ordinal dejó de medir lo que importa

Con la lectura arreglada **la inversión L2/L3 persiste**, y ahí se vio que la premisa era lo que estaba mal. `degrada_con_la_suciedad` decía: si un nivel más sucio captura mejor, algo entra por una vía que nadie diseñó. La **observación** sigue valiendo; la **inferencia** no. Los niveles son monótonos en **cantidad de defectos**, no en **distorsión**: el archivo de nivel 3 le entrega al motor **0,857** de la demanda verdadera y el de nivel 2 **0,634**, porque los inyectores del 3 **inflan** (`inject_duplicate_rows` 101,2 %, `inject_unmarked_promo` 114,9 %) y los del 2 **desinflan** (`inject_mixed_units` 79,9 %, `inject_calendar_gaps` 95,0 %). Kern comprando más al leer L3 es la respuesta **correcta** a un archivo más fiel.

Es la opción que el dueño eligió entre cuatro; las otras tres se descartaron por escrito (rebalancear los inyectores invalida toda línea base grabada y se parece a ajustar el experimento para que pase; la excepción declarada es una escotilla; no tocar nada deja la línea base con shock congelada para siempre).

Cada `Distribucion` lleva ahora su **fidelidad**, y hay dos decisiones adentro: se mide **por intake y no sobre el CSV crudo** —sobre el crudo el nivel 1 daría perfecto, y entregaba el 35 % de las unidades: una fidelidad que no pase por el mismo lector que el producto daría por bueno exactamente el defecto que existe para detectar— y el error es **`|1 − fidelidad|`**, porque inflar un 26 % y desinflar un 26 % son igual de infieles.

**La alarma conserva sus dientes**, con un test por cada filo: acusa si la captura mejora con un archivo **igual de fiel** (la forma exacta del defecto que atrapó el 18-sep, donde las fidelidades eran idénticas porque el problema no estaba en la lente), si es **peor**, y si **no hay fidelidad medida** — exculpar con una medición ausente la volvería un adorno. Y la exención **no se traga en silencio**: `inversiones_explicadas` la imprime aparte con las dos fidelidades nombradas.

### El resultado: las dos líneas base grabadas, y el costo del shock vuelve

**La mitad bajo disrupción se regrabó por primera vez desde el 18-sep.** Y no porque la compuerta se aflojara: la inversión sigue superando el margen de ruido, o sea que la versión vieja habría disparado igual.

| nivel | limpia | bajo disrupción | costo del shock |
|---|---:|---:|---:|
| **L0** | −13,8 % | −20,9 % | **−7,1 puntos** |
| L1 | −28,3 % | −33,0 % | −4,6 |
| L2 | −48,1 % | −46,9 % | +1,2 |
| L3 | −26,3 % | −24,6 % | +1,8 |

**Dos advertencias, las dos en el documento.** El −7,1 **no** es "el shock duele menos que el 15,4 del 18-sep": el denominador es otro, porque la captura se normaliza contra la brecha piso-techo y hoy Kern captura mucho mejor. Y los positivos en L2/L3 **no** son que la disrupción ayude: el piso también sufre el shock, y cuando el denominador se degrada una política mala se ve menos mala.

**Control de que medir la fidelidad no perturba nada:** la línea base grabada con la compuerta nueva es **idéntica campo por campo** a la reconstruida de la corrida anterior sin ella. Es observación pura, no un carril nuevo.

### Lo que NO se arregló, y es la capa que queda

`lens.history_frame` exporta **`out.sold`** —*"lo efectivamente vendido"*—, no la demanda. Una política mala causa quiebre, el quiebre **censura** el histórico, y la política siguiente sale más chica todavía: antes del arreglo, a la semana 4 el nivel 1 traía el 100 % de las unidades del nivel 0 y a la semana 20 el **35 %**. El piso es inmune porque `naive_policy` es **congelada** (*"sin revisar nunca"*): lee el archivo censurado una sola vez, en el calentamiento. **El bucle solo muerde al carril que replanifica.** Ajustar demanda contra ventas censuradas es una capacidad, no un arreglo de lectura, y no está medido cuánto vale.

→ **Medido en la vigesimosexta, acá abajo: no paga.**

**Verificación:** `tests/test_suciedad_cosmetica_no_cambia_el_dato.py` (10) con el invariante de cierre LEVEL 1 == LEVEL 0, `tests/test_compuerta_por_fidelidad.py` (12) con un test por cada filo de la alarma, y `test_captura_bajo_disrupcion_publicada.py` de 16 a 18. Tres de sus tests se pusieron rojos al regrabar y los tres eran **señales correctas** —el documento citaba números vencidos, el pareado se comparaba contra una época que ya no era la suya, y uno afirmaba que la columna limpia "rozó la compuerta por 0,6 puntos"—; un cuarto (`test_la_columna_con_shock_vigente_sigue_siendo_la_congelada`) se puso rojo **el día exacto para el que se escribió**. `ruff check .` limpio.

## Vigesimosexta entrega: ajustar contra ventas censuradas NO paga — medido sobre 25 mundos (`2ee1ee6`)

La vigesimoquinta cerró con el único ítem que quedaba del colapso y **no** era un defecto de lectura: Kern replanifica sobre `out.sold`, no sobre la demanda, así que quiebre → histórico censurado → política más chica → más quiebre. Terminaba diciendo *"no está medido cuánto vale, y medirlo es el primer paso antes de construir nada"*. Esto lo mide. **La respuesta es que no vale**, y las tres cosas que aparecieron midiendo valen más que el número.

El agujero es real y grande: el archivo con el que Kern replanifica **no declara el 23,2 % de la demanda**, y el **34,8 %** de sus filas salen de una semana que cerró el estante en cero.

### Cuatro carriles, y la marca es un síntoma observable y no la respuesta

Una fila del histórico es una (SKU, semana), y quedó **censurada** cuando el estante cerró esa semana en cero. Los carriles cambian qué hace Kern con esas filas y nada más — no se tocó el motor, ni el piso, ni el techo, ni el marcador, que sigue puntuando contra la demanda verdadera aunque Kern haya leído otra cosa.

| carril | clase | captura | Δ vs kern | t | mejoran |
|---|---|---:|---:|---:|---:|
| **kern** | **control** | **−13,8 %** | — | — | — |
| `censura_omitida` (borra la fila) | desplegable | −14,9 % | **−0,0107** | −0,47 | 12/25 |
| `censura_corregida` (`max(vendido, media de las limpias)`) | desplegable | −11,1 % | **+0,0267** | +1,09 | 13/25 |
| `sin_censura` (la demanda verdadera) | **diagnóstico** | −9,9 % | **+0,0394** | +1,60 | 14/25 |

**El techo de la idea es +3,9 puntos y no se distingue de cero** (IC 95 % [−0,0115, +0,0902]; 14 de 25 mundos es una moneda).

**La comparación contra las otras palancas se hace con cuidado, porque H3 es sobre esto mismo.** La fecha de fin por SKU (+16,2) y el nivel de servicio (+18,9) se midieron sobre **cinco** mundos y ésta sobre veinticinco; restar un número de cinco contra uno de veinticinco es exactamente el error que este documento documenta. Lo afirmable sin esa resta: sobre los **mismos cinco** ésta lee +11,5 contra +16,2 —más chica, del mismo orden, y por H3 ese +11,5 es la lectura *inflada*—; y sobre veinticinco ésta mejora en 14/25 mientras aquélla mejoraba en **5/5** de los suyos. Lo que las palancas anteriores leerían sobre 25 mundos **no está medido**, y ni el documento ni esta entrega lo suponen.

**Y el `p = 0,12` no significa "no se pudo ver".** Un resultado nulo no decide nada sin la potencia: con `sd = 0,1232` sobre 25 mundos pareados el efecto mínimo detectable al 80 % es **+7,2 puntos**, y la probabilidad de haber detectado una palanca del tamaño de la fecha de fin por SKU (+16,2) es del **100 %** —del 97 % para +10—. El intervalo deja abierto si esto vale cero o cuatro puntos; lo que cierra es que valga dieciséis. El test recalcula la potencia desde la línea base en vez de confiar en la tabla, porque si la dispersión entre mundos creciera el mismo `p` pasaría a significar lo contrario.

Los dos carriles desplegables marcan la fila con `on_hand_end == 0` —lo que un ERP sí tiene; `jobs.intake` ya conoce el vocabulario de esa columna— y **ninguno mira `out.lost`**, que es exactamente lo que un cliente no puede ver. Eso no se protege con un grep: `test_los_carriles_desplegables_NO_miran_la_venta_perdida` **muta** la columna de la verdad y exige que su salida no se mueva ni una celda, con el carril de diagnóstico como contraste para que la mutación no pueda ser vacía.

**Dos controles de que es el mismo experimento.** El archivo que lee el carril de control sale **byte a byte** igual al de `lens.export_history`, y sus números reproducen **las dos líneas base publicadas a seis decimales**: −0,137879 contra el L0 de `captura_limpia.json` (25 mundos) y −0,276239 contra `horizonte_por_sku.json` (las 5 canónicas). Dos arneses distintos, el mismo número.

### H1 · La censura es un síntoma, no la causa — y los dos no correlacionan

Lo esperable sería que la palanca pague donde más demanda se esconde. No es lo que pasa:

| correlación con el Δ del techo | r |
|---|---:|
| cuánta demanda esconde el archivo | **+0,05** |
| qué tan mal le va ya a Kern | **−0,61** |

Descensurar paga **+0,1035** en los ocho mundos donde Kern va peor y **−0,0029** —nada— en los ocho donde va bien. La semilla 1020 esconde el 38 % de su demanda, la que más, y descensurarla le **cuesta** 0,0818; la 1023 esconde el 18 % y gana +0,2580. Donde la palanca ayuda, el problema es la política.

### H2 · El mecanismo: un dólar de mercadería por cada dólar de quiebre

Descensurar **funciona mecánicamente** —Kern ve más demanda, compra 301 unidades más (+8,7 %) y el fill sube 3,3 puntos— y aun así no paga: compra **14.371** de mercadería para ahorrar **14.476** de quiebre. Es un empate. Todo el neto (−2.329, el **2,5 %** de la brecha contra el techo) sale del costo fijo de ordenar, porque con más demanda declarada pide en lotes más grandes y menos veces. Y deja **7.167 más** de capital pagado y sin vender al cierre.

Encaja exacto en la taxonomía de `KERN_RESIDUO_DE_LA_BRECHA.md`: *toda palanca que baja el quiebre deja más capital inmovilizado*. Es **otra palanca de "comprá más"**, y esa familia ya tenía medida su pared. La única que pagó fue la primera que era *"comprá menos de lo que no vas a vender"*.

### H3 · Cinco mundos habrían dicho que sí

|  | 5 mundos (1000-1004) | 25 mundos |
|---|---:|---:|
| `sin_censura` (techo) | **+0,1151** (3/5) | +0,0394 (14/25) |
| `censura_corregida` (desplegable) | **+0,0930** (4/5) | +0,0267 (13/25) |

**+9,3 puntos en 4 de 5 se habría publicado como una palanca de tamaño medio.** Sobre 25 mundos es 3,5 veces más chica. No es mala suerte: por H1 la palanca paga donde Kern ya va mal, y **tres de las cinco semillas canónicas (1000, 1003, 1004) están entre los ocho peores mundos** — el subconjunto está sesgado hacia exactamente la condición que la infla.

Las cinco alcanzaron para las palancas anteriores porque aquéllas mejoraban en 5/5. La regla que queda: **el tamaño de la muestra se elige por la dispersión del efecto, no por costumbre.** Los 25 mundos costaron 17 minutos.

### H4 · Borrar la fila censurada es **peor** que no hacer nada

`censura_omitida` es la respuesta que esta rama aplica en todas partes —*la ausencia se declara*— y acá **pierde** (−0,0107, con el fill cayendo 5,3 puntos). El motivo no es ruido, es **selección**: una fila que no se agotó trae la demanda **exacta** (`advance` hace `sold = min(disponible, demanda)`), así que cada superviviente es una medición perfecta; lo sesgado es **cuáles** sobreviven. Medido sobre los 200 SKU-mundos del piso, la media de las supervivientes queda en el 95,9 % de la verdadera contra el 82,6 % de la censurada cruda: borrar recupera tres cuartos del sesgo de nivel y **pierde la cola**, que es justo de lo que se hace el buffer. Y el bucle corre para atrás — ese carril termina con **42,7 %** de filas agotadas contra 34,8 % del control.

**La regla nueva:** declarar la ausencia sigue siendo correcto cuando el consumidor es **una persona** que va a decidir con ese número. Cuando el consumidor es **un estimador**, borrar no es neutral: cambia la distribución de la que estima. Esta rama las había tratado como una sola cosa.

### Lo que queda servido

`censura_corregida` recupera el **67,8 %** del techo con datos que el cliente ya tiene. Dado el tamaño del techo es un resultado menor, pero evita repetir el trabajo: si alguna vez hay otra razón para descensurar, la forma barata ya está medida.

**Verificación:** `tests/test_ventas_censuradas_publicado.py` (12) — cinco de mecanismo (el diagnóstico devuelve la demanda exacta, los desplegables no miran la respuesta, una fila sin quiebre es exacta, corregir nunca baja un valor, el control es byte a byte) y siete que atan el documento a su fuente, incluidos el que reproduce `horizonte_por_sku.json` al último dígito y el que recalcula la potencia. `scripts/por_que_bajo_el_piso.py` se dividió en dos mitades para no copiar el lector del orquestador; sus 63 tests siguen verdes. Suite completa **8.915 passed, 32 skipped, 19 deselected, 0 failed**. `ruff check .` limpio.

## Vigesimoséptima entrega: re-medidas sobre 25 mundos, cuatro de siete palancas cambian de signo — y la desplegable escala (`2cdc349`)

La vigesimosexta dejó un ítem abierto que era una deuda contra la propia rama: `+16,2` (fecha de fin por SKU) y `+18,9` (nivel de servicio) seguían siendo números de **cinco** mundos, y acababa de medirse que su propia palanca se encogía 3,5 veces al ampliar la muestra. Esto re-mide **los dos barridos enteros** —no solo los dos titulares— con `--semillas 1000..1024`, sin tocar una línea de ningún carril. `documentation/KERN_PALANCAS_SOBRE_25_MUNDOS.md`.

### La predicción, escrita antes de correr

El mecanismo de H1 de la vigesimosexta es sobre **comprar más donde ya se compra poco**. De ahí salían tres predicciones falsables, escritas antes de lanzar los barridos:

| | predicción | resultado |
|---|---|---|
| **P1** | `servicio_econ` (comprá más) se encoge al menos un 30 % | ✅ **3,69×** (−73 %) |
| **P2** | `horizonte_estimado` (comprá menos) se encoge **menos** que `servicio_econ` | ✅ **1,24×** (−19 %) |
| **P3** | la correlación con la captura del control separa a las dos familias | ✅ **sin solapamiento** |

Lo que la predicción **no** anticipó, y es lo más caro: que **cuatro de los cinco carriles del residuo cambiaran de signo**.

### El resultado

| palanca | familia | en 5 mundos | **en 25** | t | p | mejoran | r con la captura del control |
|---|---|---:|---:|---:|---:|---:|---:|
| **horizonte_estimado** | comprá menos | +0,1618 (5/5) | **+0,1309** | **+5,81** | **0,00001** | **23/25** | −0,17 |
| **horizonte_real** | comprá menos | +0,1348 (5/5) | **+0,1413** | **+7,42** | **0,00000** | **25/25** | −0,23 |
| servicio_econ | comprá más | +0,1887 (4/5) | +0,0512 | +1,66 | 0,111 | 18/25 | −0,54 |
| censura_corregida | comprá más | +0,0930 (4/5) | +0,0267 | +1,09 | 0,287 | 13/25 | −0,77 |
| plazo_obs | comprá más | +0,0240 (3/5) | **−0,0015** | −0,15 | 0,882 | 12/25 | −0,52 |
| sigma_obs | comprá más | +0,0501 (4/5) | **−0,0136** | −1,09 | 0,285 | 8/25 | −0,42 |
| proveedor_medido | comprá más | +0,0485 (2/5) | **−0,0296** | −1,56 | 0,133 | 5/25 | −0,62 |
| todo (apiladas) | apiladas | +0,0901 (3/5) | **−0,0652** | −1,66 | 0,111 | 10/25 | −0,47 |

**El control reproduce las cinco líneas base congeladas al último dígito** en los tres barridos (captura, costo total y unidades pedidas, semilla por semilla). Es la misma medición; lo único que cambió es cuántos mundos entran. Y no es *"no lo vimos"*: la potencia para haber detectado el **+18,9** publicado del nivel de servicio era del **100 %**, y la del **+9,3** de la censura del **95 %**.

### El mecanismo: la correlación parte las palancas en dos familias, sin solapamiento

| familia | rango de `corr(captura del control, Δ de la palanca)` |
|---|---|
| **comprá menos** (2 carriles) | **−0,23 … −0,17** |
| comprá más (5 carriles) | −0,77 … −0,42 |

Ni un carril de una familia cae en el rango de la otra. Una palanca de **comprá más** paga cuando Kern sub-compra, y eso pasa en los mundos donde ya le va mal: su ventaja está **concentrada** en la cola que las cinco canónicas sobre-representan. Una de **comprá menos** arregla algo que pasa en **todos** los mundos: su ventaja está **repartida**.

La regla que queda es más útil que *"usá 25 mundos"*: **una palanca cuya ventaja depende de que la política ya esté rota necesita una muestra que represente la distribución entera; una que arregla algo que pasa siempre, no.**

### `todo` es la lectura más limpia de la pared de "comprá más"

Los cinco carriles del residuo **mejoran el servicio** —todos— y cuatro de cinco terminan **peor** que no hacer nada. El carril que los apila tiene **el mejor fill de todos los de Kern (0,832)** y **el peor resultado (−20,3 %)**: compra **4.189 unidades, más que el oráculo con información perfecta** (3.678), y deja 54.452 de capital pagado y sin vender. Servir mejor no es el objetivo; el objetivo es el costo total, y esta economía castiga el inventario más de lo que premia el servicio.

La de comprá menos hace lo contrario: **−9,9 % de unidades, −12.003 de compra, +1.399 de quiebre, el fill sin moverse (−0,004) y el capital al cierre −33 %.** Sobre 25 mundos lleva a Kern de **−13,8 % a −0,7 %**, prácticamente a la par del piso; y el carril de diagnóstico lo pasa con **+0,3 %**, la primera vez en esta rama que un carril de Kern queda por encima de un punto de reorden fijo.

### Qué cambia en lo ya publicado, y qué no

**Ninguna línea base congelada se toca**: siguen siendo la medición correcta *de cinco mundos*, y hay un test que exige que sigan teniendo cinco — la evidencia del contraste vive en que las dos muestras coexistan.

- **Se invierte el orden de las palancas.** `KERN_HORIZONTE_POR_SKU.md` decía que la fecha de fin por SKU era *"la segunda más grande después del nivel de servicio"*. Sobre 25 mundos es **la más grande por un factor de 2,6**, y el nivel de servicio no se distingue de cero. Como además es la única que Kern **sí puede accionar**, el orden se invirtió a favor de lo desplegable.
- **Se refuerza "saber el futuro verdadero no compra nada".** Sobre cinco mundos el carril desplegable parecía **ganarle** al de diagnóstico (−0,0270), una inversión que aquel documento tuvo que explicar. Sobre 25 la ventaja del diagnóstico es **+0,0104 con p = 0,378**: equivalentes, y sin el artefacto.
- **Se refuerzan las dos conclusiones cualitativas del residuo y se derrumban sus tres carriles desplegables.** *"Medir al proveedor vale poco y empeora en 3 de 5 mundos"* → mejora en **5 de 25** y netea **−0,0296**. *"Apilar palancas es peor que la mejor palanca sola"* → `todo` netea **−0,0652**, peor que no hacer nada. Pero **sobre 25 mundos ese documento no tiene ninguna palanca desplegable que pague.**
- **Y se cierra una decisión de producto:** la única palanca grande que Kern **no** podía accionar —el nivel de servicio, que necesita precio y es brecha de canal, no de motor— **ya no hay que perseguirla**.

Los dos documentos superados llevan el aviso arriba, con un test que lo exige: quien llegue por un link se entera ahí mismo.

**Verificación:** `tests/test_palancas_sobre_25_mundos_publicado.py` (8) — que el control reproduzca las congeladas y que el carril dé lo publicado sobre esas cinco, que la palanca de comprá menos escale, que la de comprá más no y que el conjunto de carriles que se dan vuelta sea exactamente el nombrado, que los dos rangos de correlación **no se solapen**, que la potencia vuelva decisión al resultado nulo, que las líneas base de cinco mundos sigan teniendo cinco, que los dos documentos superados apunten acá, y que el documento publique las tres predicciones **y lo que no anticipó** — publicar una predicción solo cuando acierta es lo que la vuelve inservible. `scripts/palancas_sobre_25_mundos.py` solo **compara**: los barridos son los scripts publicados con veinte semillas más. Suite completa **8.923 passed, 32 skipped, 19 deselected, 0 failed**. `ruff check .` limpio.
