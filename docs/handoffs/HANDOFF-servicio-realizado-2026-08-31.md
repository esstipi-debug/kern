# Handshake: el servicio al cliente, medido de punta a punta

Fecha: 2026-08-31 | Worktree: `C:\Users\Gamer\Music\scm\supply-chain-optimization` |
Rama: `main` @ `287fb72`

Sucesor de `docs/handoffs/HANDOFF-adaptadores-canal-2026-08-29.md`, cuyo objetivo esta
cumplido. Ese documento tiene una seccion 8 con lo que se cerro de el.

## 1. Objetivo de la proxima sesion

Decidir y ejecutar UNA de las tres de la seccion 4. Todo lo de este hilo esta mergeado y
verde; no queda nada a medias.

## 2. Estado de juego

**Hecho y verificado.** Siete PRs mergeados a `main`, en este orden:

| PR | Que dejo |
|---|---|
| #307 | Adaptador Unleashed + el guard de cobertura de servicio |
| #308 | `scan_service`: el servicio medido llega a un `JobReport` |
| #309 | Adaptador Cin7 Core |
| #310 | OTIF: una promesa vencida sin despacho es un incumplimiento |
| #311 | Fill rate: una orden abierta no es un faltante ni una venta perdida |
| #312 | Olist + el informe de servicio contra datos reales |
| #313 | Starshipit: la fecha de llegada que el ERP no tiene |

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

Suite completa sobre `main` @ `287fb72`: **6754 passed, 0 failed**. `ruff check .` limpio.

## 4. Decisiones abiertas

**D1 - Que sigue.** Las tres son continuaciones reales; ninguna es obligatoria.

- (A) **La ruta del orquestador** *(recomendada)*. Es el limite que #308 declaro y sigue
  abierto: nada de esto se puede pedir en lenguaje natural. `_inventory_run` en
  `scm_agent/tools.py` no reenvia `order_lines`, y darle un productor exige elegir entre
  una tool nueva (#51) sobre un registry con refactor pendiente, o leer las columnas de
  servicio del CSV del cliente (hilo de intake). Sin esto la capacidad existe y ni el
  usuario la alcanza en una demo sin escribir Python.
- (B) **Shippit y Sendle**, los otros dos transportistas del canal AU/NZ. Ahora es un
  adaptador cada uno y no un rediseno: `ArrivalSource` ya existe. Amplia la cobertura de
  prospectos que pueden medirse.
- (C) **Onboarding self-serve**: que un cliente conecte su cuenta Unleashed/Cin7 solo. Es
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
  SOLO porque `order_fulfillment._number` filtra por `math.isfinite`.
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
- Corribles: `examples/run_service_report.py`, `examples/run_service_scan.py`
- Planes: `docs/superpowers/plans/2026-08-29-{unleashed-y-cobertura-de-servicio,
  servicio-medido-al-entregable,cin7-core-adaptador}.md`
- PRs: https://github.com/esstipi-debug/kern/pull/307 .. /313
- Fuentes externas verificadas en esta sesion:
  - https://apidocs.unleashedsoftware.com/SalesShipments
  - https://dearinventory.docs.apiary.io/api-description-document
  - https://support.starshipit.com/developers/api-reference/starshipit/reference.json

## 8. Lo que NO se toco, a proposito

- Precios: `webapp/pricing_quote.py`, `webapp/billing.py`, `scm_agent/package_specs.py`.
- BWM: `src/mcdm.py`, `jobs/sourcing_job.py`.
- `CLAUDE.md`: **pendiente reconocido**. El repo exige actualizarlo en el mismo PR que
  cambia el hecho, y en siete PRs no se hizo. Documenta la invariante del lado de ESCRITURA
  (writeback) y no tiene nada de la de LECTURA (cobertura de servicio), que ahora rompe el
  build. Es un arreglo de diez lineas y conviene hacerlo antes de que alguien re-derive la
  regla leyendo el guard.
