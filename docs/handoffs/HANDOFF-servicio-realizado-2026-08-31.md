# Handshake: el servicio al cliente, medido de punta a punta

Fecha: 2026-08-31 | Worktree: `C:\Users\Gamer\Music\scm\supply-chain-optimization` |
Rama: `main` @ `201ff1d` | Actualizado: 2026-08-31 (tras #317 y #318)

Sucesor de `docs/handoffs/HANDOFF-adaptadores-canal-2026-08-29.md`, cuyo objetivo esta
cumplido. Ese documento tiene una seccion 8 con lo que se cerro de el.

## 1. Objetivo de la proxima sesion

Decidir y ejecutar UNA de las tres de la seccion 4. Todo lo de este hilo esta mergeado y
verde; no queda nada a medias.

**Lo que cambio desde la primera version de este documento**, y es una correccion al
propio documento: la recomendacion decia que la ruta del orquestador exigia elegir entre
una tool nueva sobre un registry con refactor pendiente, o "leer las columnas de servicio
del CSV del cliente". Al mirarlo se vio que la segunda no era lo que parecia --
`jobs/intake.py` no tenia NINGUNA columna de servicio en su esquema canonico, asi que un
export que si las traia las perdia en silencio antes de llegar al motor. **Ni tool nueva ni
refactor: faltaba un productor, y estaba en el archivo del cliente.** Cerrado en #315.

O sea que de la ruta del orquestador queda **solo la mitad del conector**: "medime el
servicio de mi cuenta de Unleashed" sigue sin ser pedible en lenguaje natural. La mitad del
archivo -- "corre mi CSV y decime mi fill rate" -- ya funciona.

## 2. Estado de juego

**Hecho y verificado.** Once PRs mergeados a `main`, en este orden:

| PR | Que dejo |
|---|---|
| #307 | Adaptador Unleashed + el guard de cobertura de servicio |
| #308 | `scan_service`: el servicio medido llega a un `JobReport` |
| #309 | Adaptador Cin7 Core |
| #310 | OTIF: una promesa vencida sin despacho es un incumplimiento |
| #311 | Fill rate: una orden abierta no es un faltante ni una venta perdida |
| #312 | Olist + el informe de servicio contra datos reales |
| #313 | Starshipit: la fecha de llegada que el ERP no tiene |
| #314 | Este handoff + la invariante de cobertura en `CLAUDE.md` |
| #315 | El servicio sale del archivo del cliente y llega al agente |
| #317 | Actualizacion de este handoff |
| #318 | **El caveat de la fecha de entrega llega al archivo que abre el cliente** |

**La invariante nueva del repo, y es la pieza que hay que entender antes de tocar nada:**
todo modulo de `src/connectors/` que defina `orders()` **o** `arrivals()` debe exportar un
`SERVICE_COVERAGE` declarando, campo por campo, que parte del servicio al cliente puede
medir -- `status` (measured/derived/unavailable), `source` (el campo del vendor) y `means`
(que ES el numero, en una frase). El guard no lo lee: lo CORRE contra el adaptador y falla
en las dos direcciones. Un modulo nuevo sin declaracion rompe el build; paso tres veces en
esta sesion, sin que nadie se acordara.

**El hallazgo que sostiene el argumento de venta**, medido y no supuesto: sobre 98.666
pedidos reales, el OTIF por LLEGADA al cliente es 92,9% y por SALIDA del deposito 99,2%.
6,2 puntos -- 1 de cada 14 pedidos -- que ningun ERP del canal puede ver. El fill rate es
identico en las dos columnas, que es la comprobacion de que la comparacion aisla solo la
dimension temporal.

**En vuelo:** nada.

**Bloqueado:** correr Starshipit contra una cuenta viva. Necesita `StarShipIT-Api-Key` y
`Ocp-Apim-Subscription-Key` de Settings > API, que las genera el usuario. El adaptador esta
escrito y testeado contra la referencia OpenAPI oficial; falta la corrida real.

## 3. Estado del arbol

- **Sin commitear, y NO son de este hilo:** `.mcp.json`, `.dockerignore`,
  `.github/workflows/docker.yml` (modificados), `data/bateria/` (sin trackear). Los tres
  primeros los toco otra sesion mientras esta corria. **No arrastrarlos.**
- **OTRA SESION COMPARTE ESTE WORKTREE.** Correr `git status` JUSTO ANTES de cada `git
  add`, no solo al empezar. Todos los commits de este hilo usaron pathspec explicito.
- Ademas hay una sesion aparte corriendo sobre "el OTIF de la promesa vencida", que **ya
  esta mergeado como #310**. Si sigue viva va a producir un PR redundante o en conflicto:
  conviene matarla.
- Stashes: 4, ninguno de este hilo.
- PRs abiertos: 10, ninguno de este hilo (los mas viejos son #270-#298).
- Comando de verificacion:

```
cd C:/Users/Gamer/Music/scm/supply-chain-optimization && .venv/Scripts/python.exe -m pytest tests/ -q -k "connector or fulfillment or example" && .venv/Scripts/python.exe examples/run_service_report.py --data data/kaggle/olist --top 5
```

Suite completa sobre `main` @ `201ff1d`: **6782 passed, 0 failed**. `ruff check .` limpio.

## 4. Decisiones abiertas

**D1 - Que sigue.** Continuaciones reales; ninguna es obligatoria. (A) quedo hecha.

- ~~(A) Que la nota de servicio llegue al ENTREGABLE~~ **HECHO en #318.** El caveat viaja
  ahora al markdown y a la hoja *Data Sources* del `.xlsx`, en `data_sources` y no entre
  los `findings` -- un finding es un problema del negocio del cliente y esto es un limite
  de la medicion.
- (B) **La tool #51, el camino del conector** *(recomendada ahora)*. Lo unico que queda del
  limite original:
  "medime el servicio de mi cuenta de Unleashed" no es pedible en lenguaje natural. Exige
  decidir sobre el registry, cuyo refactor sigue pendiente e intacto (ver
  `kern-registry-refactor-pending-plan` en memoria).
- ~~(C) **Shippit y Sendle**~~ **HECHO el 2026-09-01** (`b3aadc1`, `d3f2c5c`). Un
  adaptador cada uno contra el `ArrivalSource` que ya existia; el Protocol no se toco.
  Plan en `docs/superpowers/plans/2026-09-01-adaptadores-llegada-shippit-sendle.md`.
  Los dos entraron a `ARRIVAL_SOURCES`, asi que heredan los cuatro tests parametrizados
  del guard (46 -> 48 casos).

  **Lo que destapo, y es lo que importa mas que los dos adaptadores:** los tres
  transportistas NO se consultan igual. Starshipit acepta la referencia del ERP
  (`/api/track?order_number=`); Shippit y Sendle se consultan por su llave propia
  (`tracking_number` y `sendle_reference`), asi que necesitan un mapa
  `order_id -> llave` que alguien tiene que armar desde el ERP. Cin7 Core guarda un
  `TrackingNumber`; Unleashed no expone ninguno.

  De ahi sale un guard transversal nuevo: **una orden sin llave produce el mismo
  silencio que una que el transportista dio por no entregada**, y `with_arrivals` saca a
  las dos del denominador de OTIF. Mismo output, significados opuestos. La nota tiene que
  distinguirlos, no solo contar.
- ~~(E) **Cablear la capa de transportistas**~~ **HECHO el 2026-09-01.** `scan_service(source, *, carrier=None)`
  acepta un `ArrivalSource` o una `CarrierFactory` (recibe los `order_id` ya leidos, para
  no releer la fuente -- 60 llamadas/min contra Cin7). Empalma, compone la cobertura con
  `coverage_of_arrivals`, publica `arrival_coverage` y propaga `source.notes` +
  `join.note` + `carrier.notes`. Solo Starshipit se puede resolver por credenciales hoy
  (`jobs/service_scan_job.py::_resolve_carrier`): Shippit y Sendle exigen un mapa
  `order_id -> llave del transportista` que ninguna linea del repo produce -- Unleashed no
  expone tracking y Cin7 lo menciona en prosa sin parsearlo.

  **Tres correcciones al motor, sin las cuales el empalme degradaba en silencio:**
  1. `RealizedService.lines_otif_scored` (nuevo) -- `aggregate()` ponderaba el OTIF de
     cartera por `lines_scored` (el denominador del FILL RATE), no por las lineas que
     realmente puntuaron OTIF. Coincidian mientras el ERP fechaba todo lo que entregaba;
     un transportista con cobertura parcial los divorcia.
  2. `aggregate()` ahora propaga `lines_in_flight` -- antes la exclusion se veia por SKU y
     se perdia en el portfolio, que es lo unico que consume el entregable.
  3. La ventana de observacion se fija con `max(pre-empalme, post-empalme)`, no solo
     pre-empalme. Un revisor adversarial (workflow de 17 agentes, 6 hallazgos, 1
     sobrevivio a dos escepticos independientes) encontro que fijarla SOLO pre-empalme
     tapaba un lado y destapaba el otro: con cobertura parcial, una llegada tardia
     reportada en una orden prueba que el reloj avanzo para TODA la cartera, y sin esa
     evidencia un stockout total sin reportar (en otra orden) se leia como que no habia
     pasado nada -- fill rate y ventas perdidas de esa orden desaparecian a la vez, no
     solo el OTIF. Reproducido y corregido; test en
     `tests/test_connector_service_scan_carrier.py::test_una_llegada_tardia_en_cobertura_parcial_no_esconde_un_stockout`.

  De paso, un defecto preexistente del PR #313: `jobs/service_scan_deliverable.py` pasaba
  la explicacion de cada KPI como tercer POSICIONAL, que cae en `target` (la columna
  "Target" de la tabla) y no en `rationale` ("Why it matters", que quedaba vacia en las
  cuatro filas de TODO deck de esta tool, con o sin transportista). Corregido de paso.

  6889 tests, 0 fallas, 0 regresiones sobre las 6849 preexistentes. PR pendiente de abrir.
- (D) **Onboarding self-serve**: que un cliente conecte su cuenta Unleashed/Cin7 solo. Es
  lo que pide la decision de self-serve AU/NZ (2026-08-05) y hoy no existe nada.

**D2 - Precio de entrada AU/NZ.** Sigue abierta desde el handoff anterior y no se toco.
Ningun archivo de precios fue modificado en los siete PRs.

**D3 - Que hacer con el drift guard cuando llegue un cuarto verbo.** Hoy busca `orders` o
`arrivals`. Un modulo que llene un campo de servicio por otra via se colaria.
- (A) Dejarlo y agregar el verbo cuando pase *(recomendada: dos verbos cubren todo lo que
  existe, y adivinar el tercero es especulacion)*.
- (B) Invertirlo: exigir declaracion a TODO modulo de `connectors/` salvo lista de
  excepciones. Mas estricto, mas ruidoso.

## 5. Trampas ya pisadas

**De la investigacion previa -- cinco correcciones, todas verificadas contra doc del vendor
y ahora fijadas en tests:**

1. Unleashed **SI** expone cantidad despachada por linea, en `/SalesShipments`, no en
   `/SalesOrders`.
2. Unleashed **no tiene ninguna fecha de llegada**: `CompletedDate` y `DispatchDate` son
   las dos salidas.
3. Cin7: `/sale/fulfilment/ship` **no tiene producto ni cantidad** -- sus lineas son CAJAS.
   La cantidad esta en `Pack` y la fecha en `Ship`, empalmadas por etiqueta de caja.
4. Cin7: `Box` (pack, singular) contra `Boxes` (ship, plural, puede llevar varias en un
   string). Empalmar por igualdad exacta pierde todo envio multi-caja en silencio.
5. Cin7: **no hay lectura masiva de fulfilments**. Una llamada por venta, 60/min.

**De la API de Starshipit:** `last_updated_date` con `tracking_status: "Delivered"` NO es
la fecha de entrega -- es la ultima actualizacion de cualquier tipo. Un escaneo posterior la
corre y convierte un pedido cumplido en incumplido. La fecha buena es el `event_datetime`
del evento `Delivered` en `tracking_events[]`.

**De diseno, y las tres las encontro CORRER el codigo, no leerlo:**

- **Comparar dos lecturas con ventanas derivadas distintas compara dos cohortes.** El
  informe de llegada-vs-despacho derivaba una ventana por lectura; la de despacho termina
  antes, asi que una orden cancelada quedaba "en curso" en una y "vencida" en la otra.
  Parte de la brecha era artefacto. Hay que fijar `as_of` en las dos.
- **Excluir toda linea prometida a futuro descarta hechos observados.** El primer intento
  del arreglo de fill rate buscaba simetria contra el sesgo de supervivencia y rompio 6
  tests legitimos donde las entregas SI habian ocurrido. La evidencia le gana a la
  simetria; la asimetria residual se publica en `lines_in_flight`.
- **La ventana de observacion la fijan los HECHOS, no las promesas.** Incluir
  `promised_date` deja que un pedido a 2027 estire la ventana y haga "vencer" toda promesa
  vigente.

**De #315, y son de la misma familia que las de arriba:**

- **Un esquema canonico que no menciona un dato lo DESCARTA en silencio.** `jobs/intake.py`
  normalizaba demanda y no tenia columnas de servicio, asi que el archivo del cliente podia
  traer el dato y se perdia antes del motor. Antes de concluir "el dato no esta", chequear
  si el normalizador lo esta tirando.
- **El servicio NO se agrega por periodo.** La demanda si. Dos pedidos del mismo SKU en la
  misma semana son dos resultados de servicio distintos, y sumarlos borra el que fallo. Por
  eso `service_lines` lee el archivo una segunda vez en vez de derivar del frame de demanda.
- **Una fecha de entrega en un ARCHIVO es de tipo desconocido.** De un ERP sabemos que
  significa porque leimos la doc del vendor; de un CSV no. `fecha_despacho` y
  `fecha_entrega` dan el mismo campo con sentidos opuestos, y entre los dos hay 6,2 puntos
  medidos. Por eso el frame lleva `.attrs["service_headers"]` con la columna que matcheo.

**De #318:**

- **Las citas de `src/discovery_brief.py` estan ancladas a NUMERO DE LINEA** (19 de ellas).
  Agregar un campo a un dataclass corrio 6 lineas tres de ellas y rompio
  `test_todas_las_citas_de_linea_son_exactas`. El guard funciona -- atrapo una rotura real
  -- pero el mecanismo se rompe con cualquier insercion arriba aunque el valor no cambie.
  **Es la tercera vez que este repo pelea con citas que derivan** (#160/#162/#164, #200).
  Al tocar cualquier archivo citado, correr ese test. Anclarlas a simbolo esta pendiente.
- **El texto de cara al cliente va en INGLES.** El deck es en ingles por defecto y tiene
  i18n (`src/i18n.py`) para una version en espanol ENTERA. #315 escribio la nota en espanol
  y la inyectaba en un deck ingles; corregido en #318. Mezclar idiomas dentro de un mismo
  entregable no es i18n, es un descuido.

**De proceso:**

- El guard del repo `test_no_inert_parameters_outside_the_allowlist` atrapo un
  `live_http(creds)` que declaraba credenciales sin usarlas. Correr la suite COMPLETA antes
  de abrir PR, no solo la slice relevante.
- Heredocs largos en Git Bash se truncan (~6 KB) y fallan con "unexpected EOF". Para
  bloques grandes, escribir a un archivo del scratchpad y `cat >>`.
- `python - <<'EOF'` con `\\n` adentro de un string escribe saltos reales y rompe el
  archivo. Usar `"\n".join([...])`.
- Un `grep -r` sobre todo `C:\Users\Gamer\Music\scm` supera los 120s y se va a background.
- venv por worktree: `.venv/Scripts/python.exe` del worktree.
- pandas convierte `None` en `NaN` en columnas de float. La cadena de servicio sobrevive
  SOLO porque `order_fulfillment._number` filtra por `math.isfinite`. **Se piso dos veces
  en esta sesion**: en un test se afirma el SIGNIFICADO (no es 0.0, y el motor la deja
  afuera), nunca `v is None`.
- **Un test que falla puede tener la fixture equivocada y la conducta buena.** Paso tres
  veces: un OTIF esperado en 1.0 que era 0.9 por SCOR, una regla de exclusion que descartaba
  entregas observadas, y una fixture "ilegible" que se detectaba bien. Antes de tocar el
  codigo, verificar cual de los dos esta mal.
- La suite completa tarda ~30 min. Lanzarla en background y seguir trabajando.

## 6. Skills a usar

- `superpowers:writing-plans` - antes de tocar codigo en cualquiera de las tres opciones de
  D1. (A) y (C) son multi-archivo.
- `superpowers:test-driven-development` - el guard y los adaptadores se escribieron asi, y
  es lo que hace que la declaracion de cobertura valga algo.
- `superpowers:brainstorming` - solo para D1-(C), que abre diseno de producto nuevo.

## 7. Artefactos

- Guard de cobertura: `tests/test_connector_service_contract.py`
- Adaptadores: `src/connectors/{unleashed,cin7,olist,starshipit,arrivals}.py`
- Motor: `src/order_fulfillment.py` · puente: `src/connectors/__init__.py`
  (`realized_service`, `observation_window_end`, `service_frame`)
- Lectura de servicio: `src/connectors/service_scan.py`
- Servicio desde el archivo del cliente: `jobs/intake.py::service_lines` (alias en
  `SERVICE_ALIASES`), cableado en `scm_agent/tools.py::_inventory_prepare/_inventory_run`
- El caveat al entregable: `JobReport.service_note` ->
  `jobs/inventory_deliverable.py::build` -> `Deliverable.data_sources`
- Corribles: `examples/run_service_report.py`, `examples/run_service_scan.py`
- Planes: `docs/superpowers/plans/2026-08-29-{unleashed-y-cobertura-de-servicio,
  servicio-medido-al-entregable,cin7-core-adaptador}.md`
- PRs: https://github.com/esstipi-debug/kern/pull/307 .. /318
- Fuentes externas verificadas en esta sesion:
  - https://apidocs.unleashedsoftware.com/SalesShipments
  - https://dearinventory.docs.apiary.io/api-description-document
  - https://support.starshipit.com/developers/api-reference/starshipit/reference.json

## 8. Lo que NO se toco, a proposito

- Precios: `webapp/pricing_quote.py`, `webapp/billing.py`, `scm_agent/package_specs.py`.
- BWM: `src/mcdm.py`, `jobs/sourcing_job.py`.
- `CLAUDE.md`: **ya no es pendiente**. Se cerro en #314 (la invariante de cobertura del
  lado de lectura) y #315 (el productor desde el archivo del cliente). Habia estado sin
  actualizar durante siete PRs, contra la regla del propio repo.
- El **refactor del registry** sigue pendiente e intacto. #315 no lo toco: no agrego una
  capacidad nueva, hizo que la tool de inventario -- ya registrada y ruteada -- reporte
  servicio cuando el archivo lo permite.
