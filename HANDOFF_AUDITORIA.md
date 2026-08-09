# Handoff — auditoría Kern y arreglos de corrección

Estado al cierre de la sesión anterior. Autocontenido: no hace falta leer el hilo previo.

- **Repo:** `C:\Users\Gamer\Music\scm\supply-chain-optimization`
- **Rama:** `feat/shopify-replenishment-tool` — **todo sin commitear**
- **Python:** usar `./.venv/Scripts/python.exe` (no el del sistema)
- **Suite completa:** `./.venv/Scripts/python.exe -m pytest tests/ -q --no-cov -p no:randomly` — ~12 min

> ⚠️ **No canalices pytest a `tail`**: `cmd | tail` devuelve el exit code de `tail`, no de pytest. Me enmascaró 8 fallos una vez. Usá `> archivo 2>&1; echo "EXIT=$?"`.

---

## 1. Qué se hizo

Auditoría adversarial completa del repo (informe en el hilo previo), y después **4 de los 5 ítems críticos** que salieron de ella.

### Línea base medida antes de tocar nada

| Métrica | Valor |
|---|---|
| Suite completa | **4.742 tests, 0 fallos, 94,63% cobertura, 11:41** |
| Gate de cobertura | `fail_under = 80` en `pyproject.toml:219`, se cumple |
| Herramientas registradas | 52 (`scm_agent/tools.py`) |
| Llamadas a LLM en todo el repo | **4** — ver §5 |
| Costo LLM por job | ~USD 0,004 (≈USD 0,16/mes/cliente) |

### Ítems cerrados

| # | Qué | Archivos | Tests nuevos |
|---|---|---|---|
| **0** | Rejilla completa de períodos | `src/sources.py`, `jobs/inventory_optimization.py`, `scm_agent/tools.py` | `tests/test_demand_period_grid.py` (7) |
| **1** | Umbral mínimo de historia | `src/forecasting.py`, `jobs/inventory_optimization.py`, `jobs/qa.py`, `scm_agent/tool_options.py`, `src/connectors/replenish.py` | `tests/test_insufficient_history.py` (15) |
| **2** | σ_e fuera de muestra | `src/forecasting.py`, `jobs/inventory_optimization.py`, `jobs/forecast_job.py` | `tests/test_oos_sigma.py` (9) |
| **3** | POs irreversibles + rollback honesto | `src/connectors/odoo.py`, `examples/run_odoo.py` | `tests/test_odoo_po_irreversible.py` (9) |

`git diff --stat`: **15 archivos modificados, +732 / −84**, más 4 archivos de test nuevos (40 tests).
Uno de los modificados es `tests/test_deliverable_methodology_honesty.py`, que ganó un test
para las recomendaciones rechazadas (§2).

---

## 2. Estado de verificación — **leer esto antes de seguir**

| Después de | Suite | Resultado |
|---|---|---|
| Ítem 1 | completa | ✅ EXIT=0, 4.761 passed |
| Ítem 0 (rejilla) | completa | ✅ EXIT=0, 4.769 passed |
| Ítems 2 + 3 | completa | ⚠️ **1 fallo**, arreglado — ver abajo |
| Ítems 0-3 (final) | completa | ⏳ **relanzada; confirmar antes de seguir** |

**El fallo y lo que reveló.** `test_deliverable_methodology_honesty.py` valida contra un
**conjunto cerrado** de valores de `sigma_source`, y el nuevo `forecast_error_oos` no
estaba en él. Al arreglarlo apareció una brecha **anterior a estos cambios**: el fixture
de ese módulo solo tiene SKUs sanos, así que nunca ejercitó las recomendaciones que el
run **rechaza** — y esas llevan `method="none"`, que tampoco estaba en el conjunto
permitido. O sea que el test habría fallado con un portafolio con demanda cero desde
antes. Se agregó `test_the_recommendations_the_run_DECLINED_to_size_are_described_just_as_honestly`,
que ejercita los tres tipos de rechazo (`insufficient_history`, `skipped_zero_demand`,
`skipped_zero_cost`) y falla si el fixture deja de producir alguno.

**Primera tarea en la ventana nueva: leer el resultado de la suite relanzada.** Comando
arriba. Última corrida conocida: **1 failed, 4.786 passed, 27:12** — el fallo ya está
arreglado y `test_deliverable_methodology_honesty.py` pasa 15/15 dirigido.

Ojo con el tiempo: el ítem 2 agrega ~0,5 s/SKU (12 reajustes de modelo), así que la
suite pasó de 11:41 a **~27 min**. Es el costo del σ_e fuera de muestra y está dentro de
lo aceptable para un gate de CI, pero conviene saberlo antes de esperar 12 minutos.

---

## 3. Ítems críticos que faltan

### Ítem 6 — dimensión de tenant en `src/state/` (~5 días) ← **siguiente**

`grep -rn "client\|tenant\|org" src/state/*.py` devuelve **cero**. `default_store()` es un singleton de proceso en `data/state` (`src/state/store.py:192-206`). El índice SQLite se particiona por `domain`/`cycle_id`, sin dimensión de cliente.

Consecuencia concreta: `run_forecast_backtest()` (`src/verify/backtest.py:207`) lee `history("forecast")` sin filtro de cliente. Con dos clientes en el mismo proceso, los errores de pronóstico de A recalibrarían el safety stock de B.

Trabajo: columna `client_id` en el esquema (con migración `ALTER TABLE` para DBs existentes), threading por `snapshot`/`latest`/`history`, y filtro en `backtest`.

### Ítem 12 — fuente demo + σ en el entregable de reposición (~2 días)

Dos cosas distintas:

1. `jobs/odoo_job.py:47-54` (`_make_rpc`): si falta **cualquiera** de `ODOO_URL`/`ODOO_DB`/`ODOO_USERNAME`/`ODOO_API_KEY`, cae a `demo_odoo()` — **datos ficticios**. Se reporta en el summary y en un KPI, pero `verify()` (`jobs/odoo_job.py`) **no lo rechaza**. Un entregable entero sobre datos demo pasa QA.
2. El entregable de reposición no lleva MAPE ni intervalo. `error_std` está en el registro (`jobs/inventory_optimization.py:271`) pero el CSV de "pedí X unidades" no muestra el error. El de `forecast` sí lo hace bien (`jobs/forecast_job.py:295-320`) — copiar ese patrón.

---

## 4. Ítems no críticos, ordenados por dependencia

Del roadmap adversarial (workflow `wf_76a71096-1db`, 14 agentes).

| # | Qué | Días | Nota |
|---|---|---|---|
| 4 | Ledger persistente + clave de idempotencia con período en el camino Odoo | 2 | `jobs/odoo_job.py:61` construye `OdooConnector(rpc)` **sin ledger** → sin dedup entre corridas. El patrón ya existe en `jobs/excel_replenishment_job.py:308` |
| 5 | Shopify: paginación por cursor, flag de truncación de órdenes, filtro canceladas/reembolsos, backoff | 8 | Ver §6 |
| 9 | Test de calibración empírica (nivel de servicio simulado vs. objetivo; cobertura de p10/p90) | 3 | Sin esto, el ítem 2 está implementado pero **no verificado** |
| 7 | Auth por cliente en la superficie de escritura | 3 | Hoy `LINCHPIN_API_KEY` compartida. El store `lpk_` por cliente ya existe pero solo cubre el MCP de lectura (`src/mcp_keys.py`) |
| 8 | `run_id` + cablear `audit_evidence.evidence_record()` en `deliver()` | 4 | `run_id` **no aparece ni una vez** en `jobs/`, `scm_agent/`, `webapp/` |
| 11 | Endpoint de aprobación para el camino de **writeback** | 4 | `webapp/app.py:1483-1494` documenta que solo cubre tier de análisis |
| 13 | Batch de forecast por panel | 3 | Hoy un `StatsForecast` por SKU (`src/forecasting_auto.py:141`, `n_jobs=1`). ~0,2 s/SKU → ~35 min con 10.000 SKUs |
| — | **Afinado de TSB** (nuevo, salió del ítem 0) | 1 | `alpha_d=0.2, alpha_p=0.2` hardcodeados (`src/forecasting_auto.py:69`). Sobre series ultra-esporádicas da 52,4 u/año donde lo real son ~7,5 |
| — | Borrar código muerto | 0,5 | `src/data_quality.py:51` (`map_columns`, cero llamadores, tabla de alias divergente de `jobs/intake.py:23-60`), `src/distributions.py:128-141` (`gamma_inverse_fill_rate`, cero llamadores), `src/audit_evidence.py` (ver §5) |
| — | Bug en `src/simworld/runners.py:351-354` | 0,5 | Calcula `dropped = n_raw - n_canonical_rows`, y por eso acredita "anuncié un problema de calidad" en prácticamente todo archivo transaccional limpio |

---

## 5. Hallazgos de la auditoría que siguen abiertos

### No es una agencia AI

**4 llamadas a LLM en todo el repo**, sobre 52 herramientas:

| Dónde | Qué | Peso |
|---|---|---|
| `scm_agent/orchestrator.py:223` | `narrative_rewrite` | Decoración; si falla vuelve al resumen determinista |
| `scm_agent/packages.py:472` | idem | Decoración |
| `scm_agent/intent.py:35` | elegir herramienta | **Solo desempate**: `intent.py:59-61` retorna sin LLM si el match por keywords gana claro |
| `scm_agent/tools.py:210` | perfil CHAIN | 1 herramienta de 52 |

**51 de 52 herramientas no tocan un modelo.** Kern es un motor determinista que escribe su propia carta de presentación con un LLM. Eso es la arquitectura correcta para el dominio, pero "agencia AI" es una promesa que el código no sostiene.

Dónde un LLM sí sumaría y hoy no está: (a) entender el CSV del cliente — hoy es regex en `jobs/intake.py`; (b) explicar por qué cambió la recomendación vs. la semana pasada — `src/replenishment_delta.py` calcula el delta, nadie lo narra; (c) redactar el correo al proveedor.

### No es autónomo — falta el lazo cerrado, no las piezas

Del mapeo adversarial (**8 de 8** reclamos "WIRED" fueron degradados bajo escrutinio):

- **Las seis rutas de `config/event_routing.yaml` son T2.** Cero T1, cero T3 → las ramas de auto-ejecución y escalación son inalcanzables *por configuración*.
- `enforce_analysis_tier`, `enforce_writeback_tier`, `handle_event_tiered` → **TEST_ONLY**. Ninguna fila de `AutonomyRecord` se escribe en producción → `/tower` T1/T2 permanentemente vacías, `POST /api/approvals/{id}` solo puede devolver 404.
- **No hay egreso.** `jobs/notify.py` es el único transporte del repo; sus 3 call sites son TEST_ONLY, y `LINCHPIN_SLACK_WEBHOOK_URL` **no está en el bloque `[env]` de `fly.toml`** → `notify.py:65-67` retorna `False` de inmediato. **El sistema no puede decirle nada a un humano sin que se lo pregunten.**
- `src/verify/backtest.py` mide predicho-vs-real y **no lo llama nadie** en producción.
- `run()` acepta `observed_fill_rates` para cerrar el lazo de servicio; **nada se lo pasa**. La propia auto-auditoría del repo lo dice textual: `src/bateria/comprobaciones.py:1271-1276`.
- **No hay detección de deriva de entrada** entre corridas (colapso de filas, cambio del set de SKUs, hueco de fechas): DEAD, confirmado con búsqueda exhaustiva.

**Sí existen** (correcciones a mi propio informe inicial, que decía lo contrario):
- `forecast_error_out_of_band_monitor` (`scm_agent/monitors.py:337-398`) **está en cadencia** vía `POST /api/jobs/run-scheduled` ← cron de GitHub Actions. Pero está **deliberadamente excluido del ruteo** (`config/event_routing.yaml:24-30`): un pronóstico con 110% de error escribe una fila y nada más.
- Circuit breakers: `src/intake_scan.run_breakers` y el `CircuitBreaker` de pricing_intel, ambos WIRED. Pero `run_breakers` nunca llega al event ledger y **no puede frenar un job posterior**.
- `scm_agent/scheduler.py` **no existe** — es `jobs/scheduler.py`.

### Cobertura invertida respecto del riesgo

El núcleo analítico está 95-100%. Los peores módulos son los transportes vivos:

| Módulo | Cob. | Sin cubrir |
|---|---|---|
| `src/connectors/meli.py` | 72% | |
| `src/state/system_state.py` | 73% | 175-195 = `_validate_fallback` (validación sin pandera) |
| `src/connectors/shopify_inventory.py` | 77% | 97-158 = transporte de **escritura** vivo |
| `src/connectors/shopify.py` | 78% | 87-93 `_graphql`, 96-117 `list_variants`, **120-144 `list_orders`** |

`ShopifyCommerceClient` es el camino de producción (`jobs/shopify_job.py:77`) y **todos** los tests corren contra `InMemoryShopifyCommerce`.

---

## 6. Shopify — el defecto que sigue abierto (ítem 5)

`src/connectors/shopify.py:120-143`:

- Pide `first: n` con `n = min(limit, 250)`, `sortKey: CREATED_AT` **sin `reverse: true`** → lee las 250 órdenes **más viejas** de la ventana.
- Sin bucle de cursor. `lineItems(first: 50)` tampoco pagina.
- `self.truncated` se calcula **solo desde variantes** (`shopify.py:206`) → la truncación del historial de órdenes es **silenciosa**.
- Sin filtro de canceladas/reembolsos: `grep -rniE "cancell?ed|refund" src/connectors/` no devuelve nada.
- `inventory_levels()` hace **una llamada por variante** (`shopify.py:220-228`), sin backoff ni manejo de 429 en todo el conector.

Del otro lado, `jobs/intake.py:332` **descarta** cantidades negativas (contadas en `n_dropped_negative_quantity`) en vez de netearlas contra la venta. Demanda bruta, no neta.

---

## 7. Decisiones de diseño tomadas — respetarlas o revertirlas a conciencia

1. **`MIN_PERIODS_FOR_POLICY = 6`** (`src/forecasting.py`). Derivación: n períodos → n−1 errores → RSE(σ̂) ≈ 1/√(2(n−1)) ≈ 35% en n=6. Es un **umbral de rechazo**, no una perilla.
2. **Un rechazo es un estado válido del entregable, no un fallo de QA.** El roadmap adversarial proponía que `insufficient_history` cortara la entrega; no lo hice — un SKU delgado entre 3.000 mataría el entregable completo, que es el bug "matar todo el lote" que el repo ya arregló para demanda cero (`tests/test_jobs.py:523-525`). QA solo corta si una línea **con precio** carece de historia.
3. **La rejilla arranca en la primera venta del SKU**, no en la primera fecha del archivo (rellenar hacia atrás inventaría ausencia de un producto que no existía), y **llega hasta la última fecha del archivo** (el silencio posterior es demanda cero real).
4. **σ_e OOS por defecto para todos los métodos**, `max_origins=0` como escape. Costo medido en estado estacionario: SES 0,06 ms, Croston 0,05 ms, TSB 3,5 ms, **AutoETS 37 ms** por ajuste → 12 orígenes ≈ 0,44 s/SKU.
5. **El σ in-sample se conserva** junto al OOS (`error_std` y `error_std_oos`) para que la brecha sea medible.
6. **`create_draft_purchase_orders` exige `approved_by`**, sin default. Que el llamador diga "el humano soy yo" es auditable; que no diga nada, no.
7. **Los puntos de reposición siguen `TIER_REVERSIBLE`.** Etiquetar todo como irreversible haría que la compuerta deje de tomarse en serio.
8. **NaN, nunca 0.0**, cuando no se puede medir dispersión. Un σ de cero es una afirmación; NaN es una admisión.

---

## 8. Correcciones a la auditoría original — no repetir los errores

| Afirmé | Realidad |
|---|---|
| "σ_e in-sample → safety stock **sistemáticamente** subestimado" | **Condicional.** SES/Croston tienen α *fijo*, no estimado: sus valores ajustados ya son predicciones a un paso con datos previos (`forecasting.py:193-195`). Medido: dentro de ~6%, en ambas direcciones. El sesgo real es de **AutoETS sobre series estacionales: 20%** |
| "No hay monitoreo de deriva" | Existe `forecast_error_out_of_band_monitor`, en cadencia — pero excluido del ruteo |
| "No hay circuit breaker" | Hay dos, WIRED — pero ninguno puede frenar un job |
| "`scm_agent/scheduler.py`" | No existe; es `jobs/scheduler.py` |
| "`TIER_IRREVERSIBLE` nunca se usa" | Literal pero engañoso: la guarda **sí existe**, escrita como `changeset.risk_tier != TIER_REVERSIBLE` (`scm_agent/autonomy.py:556`). Lo que falta es el llamador |
| "La suite no termina en una hora" | Falso — tarda 11:41. El archivo quedó en 0 bytes porque `tail` bufferea |

---

## 9. Trampas técnicas encontradas

1. **`pd.date_range(first, last, freq="W")` NO arranca en `first`.** `W` es `W-SUN`: pandas ancla al domingo siguiente. El intake ancla en **lunes**. Resultado: la rejilla no intersectaba ninguna fecha y una historia real volvía como **serie de puros ceros**, en silencio. Solución en `src/sources.py::_fixed_width` — `Week` es de ancho fijo pero pandas lo modela como offset **anclado**, así que `pd.Timedelta(offset)` levanta igual que para `MonthEnd`.
2. **Benchmarks de statsforecast:** el primer ajuste del proceso mide 735 ms, en estado estacionario 37 ms. Calentar antes de medir.
3. **`--cov=src.modulo`** (nombre de módulo) rompe con `ImportError: cannot load module more than once per process` en numpy. Usar `--cov=src --cov=jobs`.
4. **`nosilo_core` no está en el árbol** — es dependencia externa pinneada a `git+https://github.com/esstipi-debug/nosilo-core.git@v0.1.0` (`pyproject.toml:31`). El código vive en `.venv/Lib/site-packages/nosilo_core/`. Tag mutable en repo personal, sin firmar: dependencia de cadena de suministro sin auditar.

---

## 10. Qué NO se verificó

- Comportamiento contra un Odoo o Shopify vivo. Todo sale de leer código y de los stand-ins in-memory.
- Precisión de pronóstico sobre datos de cliente reales.
- Los 91 archivos de test clasificados como "other" (1.125 tests) — la categorización fue por nombre de archivo.
- Que el tag `v0.1.0` de `nosilo-core` sea inmutable.
- **La suite completa después de los ítems 2 y 3** (ver §2).
