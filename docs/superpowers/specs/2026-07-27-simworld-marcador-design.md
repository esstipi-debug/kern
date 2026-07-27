# Entorno de simulacion con marcador — diseno

- Fecha: 2026-07-27
- Estado: aprobado en brainstorming. Sin plan, sin codigo.
- Rama: `feat/simworld-marcador`

## El problema

Kern tiene seis capas de prueba y ninguna mide una **decision**:

| Capa | Que hace | Que no hace |
|---|---|---|
| `scripts/setup-test-env.sh` | Instalacion reproducible | — |
| `examples/run_capability_smoke.py` | 41 herramientas, fixture minimo, 39 PASS | Una herramienta a la vez, un solo tiro |
| `run_capability_smoke.py --stress` | Entrada vacia / esquema errado / basura | Suciedad sintetica, no suciedad de cliente |
| `examples/run_scm_*.py` | Superstore, Olist, DataCo, Procurement | Un solo tiro, sin consecuencia |
| `src/connectors/simulator.py` | Storefront en memoria + writeback | Sin tiempo |
| `src/simulation.py`, `src/verify/backtest.py` | Monte Carlo de una SKU, predicho vs real | Sin agente, sin bucle cerrado |

Todas son de un solo tiro, por herramienta aislada, sobre datos limpios. Existe
evidencia de que Kern **produce** recomendaciones sin romperse. No existe
evidencia de que esas recomendaciones sean **buenas**.

Falso amigo a tener presente: `examples/generate_chaotic_demo_data.py` (sin
trackear) parece este entorno pero no lo es — llama a `compute_eoq` y
`safety_stock` directo, sin pasar por el orquestador, y su "38/100 -> 96/100"
esta escrito a mano. Es material de venta, no un marcador. Este diseno no lo
extiende.

## Que se construye

Un mundo de reposicion **generado con semilla**, cuya verdad se conoce, sobre el
que corren tres corredores durante 52 semanas, viendo el mundo a traves de una
**lente con cuatro niveles de suciedad**.

Salen **dos numeros por nivel de suciedad**: cuanto cayo el marcador, y cuanto de
esa caida Kern anuncio. La diferencia es la degradacion silenciosa.

## Decisiones tomadas

| # | Decision | Descartado, y por que |
|---|---|---|
| 1 | Proposito: marcador honesto contra verdad conocida | Cliente ficticio demostrable (se ve real, no prueba nada); gate de CI (optimiza estabilidad, no evidencia); solo banco de suciedad (sin bucle no hay consecuencia) |
| 2 | Bucle puntuado: reposicion | Abastecimiento y precio quedan para despues; los tres a la vez confunde la atribucion de la falla |
| 3 | Superficie: brief en lenguaje natural por el orquestador completo, mas un carril de diagnostico con `job_type` forzado | Solo brief (no se distingue "decidio mal" de "ruteo mal"); solo forzado (no se puede decir que Kern opero) |
| 4 | Suciedad escalonada 0-3, curva de degradacion | Limpio vs sucio (no muestra el punto de quiebre); solo sucio (no hay contra que comparar) |
| 5 | Mundo propio, contrato de KPI portado | Adoptar gym-invmgmt como entorno (mide una red multi-echelon limpia que no es el ICP); construir desde cero (reinventa un contrato ya publicado bajo MIT) |
| 6 | Grilla: 1 regimen x 4 niveles x 3 corredores = 12 corridas | 4 regimenes (x4 el trabajo antes del primer numero); grilla completa estilo gym-invmgmt (incluye ejes que no son el cliente) |

## Investigacion previa (2026-07-27)

El patron piso heuristico + techo con oraculo privilegiado + marcador normalizado
**ya es el estandar** en la literatura. No hay que disenarlo.

| Fuente | Que se toma | Estado |
|---|---|---|
| [gym-invmgmt](https://arxiv.org/abs/2605.11355) — MIT, `pip install gym-invmgmt`, may 2026 | El contrato de KPI y la idea de ejes de escenario | Se **porta**, no se depende. Su topologia es multi-echelon de 9 nodos; el ICP de Kern es un dueno-operador con proveedores |
| [RetailBench](https://arxiv.org/abs/2603.16453) — Ant Group + CityU HK | Valida el patron: 180 dias, oraculo privilegiado, 7 LLMs | Sin repo publico. Resultado citable: solo un subconjunto de los LLMs sobrevive el horizonte completo, y el mejor queda muy por detras del oraculo |
| [AIM-Bench](https://arxiv.org/abs/2508.11416) | Metricas de sesgo: anclaje a la media, persecucion de demanda, efecto latigo | Sin repo publico. Se copian las definiciones |
| [OR-Gym](https://arxiv.org/pdf/2008.06319) — `InvManagement-v0/v1` | Referencia historica | Superado por gym-invmgmt, su descendiente directo |

**El hueco:** ninguno de los cuatro ensucia los datos. Confirmado explicitamente
en gym-invmgmt: ningun escenario incluye datos corruptos ni faltantes; todos usan
demanda estocastica limpia. Lo mas cercano vive en robustez de forecasting de
series de tiempo (corrupciones tipo pico y salto de nivel con severidad
controlable), no en la decision de reposicion. La capa de suciedad es la
contribucion propia de este entorno.

## Arquitectura

Cuatro unidades, un proposito cada una. Ninguna conoce las internas de las otras.

```
Mundo (verdad, con semilla)  ->  Lente sucia (nivel 0-3)  ->  Kern (brief, QA, guided)
      ^                                                              |
      +---------------- la decision cambia el mundo ------------------+

El bucle de arriba, corrido tres veces sobre el mismo mundo:
   Piso (punto de reorden fijo)   Kern   Techo (informacion perfecta)
                          -> Marcador normalizado
```

### 1. `src/simworld/world.py` — la verdad

Funciones puras. Nadie fuera del marcador la lee.

- `generate_world(seed, n_skus, n_suppliers, weeks, regime) -> World`
- `World` (frozen): catalogo (sku, costo, precio, proveedor), proveedores (lead
  time nominal **prometido** vs distribucion **real**, comportamiento OTIF),
  matriz de demanda verdadera `n_skus x weeks`, y la lista de eventos.
- `advance(world, state, orders) -> (WorldState, WeekOutcome)`: materializa la
  demanda de la semana, recibe las ordenes cuyo lead time real vencio, registra
  quiebres, sobrante y costo.

**Invariante que da validez a toda la comparacion:** la demanda verdadera y los
lead times reales se sortean **una vez**, en la generacion. Los tres corredores
enfrentan la realizacion identica. Un corredor no puede tener suerte.

Eventos del mundo (parte de la verdad, no de la suciedad): un proveedor que se
atrasa tres semanas, un peak de demanda, una SKU discontinuada, un quiebre de
proveedor.

### 2. `src/simworld/lens.py` — la lente sucia

- `export(world, state, level) -> Path` escribe el CSV que Kern realmente lee.
- Nivel 0 = canonico limpio: `date, product_id, quantity` requeridas y
  `unit_cost, lead_time_days, lead_time_std, revenue` opcionales, tal como las
  espera `jobs/intake.py` (`CANONICAL_REQUIRED` / `CANONICAL_OPTIONAL`).
- Los niveles **acumulan**: 3 incluye 2 incluye 1.
- Cada defecto es un inyector con firma uniforme y declara dos cosas: **que
  corrompe** y si es **detectable desde el archivo solo**.

**La lente nunca muta el mundo.** El marcador se calcula siempre contra la verdad
limpia, aunque Kern haya visto basura. Eso es lo que hace que la curva signifique
algo: si Kern pidio mal porque el exporte tenia la SKU duplicada, el quiebre se
cuenta igual contra el.

| Nivel | Defectos |
|---|---|
| **1 — desprolijo** (no cambia la verdad si se limpia bien) | SKU con espacios y mayusculas inconsistentes; fechas en dos formatos en la misma columna; numeros con separador de miles y coma decimal; encabezados que no coinciden con los canonicos |
| **2 — tipico de PyME** (cambia la verdad si no se detecta) | La misma SKU con dos grafias, la demanda se parte en dos; unidades mezcladas (filas en cajas y filas en unidades, sin columna que lo diga); devoluciones cargadas como ventas negativas; lead time faltante en un tercio de los proveedores; semanas sin filas — cero demanda o no se cargo |
| **3 — caotico** (rompe supuestos, no solo columnas) | Stock negativo en el snapshot; proveedor renombrado a mitad del historico, el OTIF se parte en dos; promociones sin marcar (picos que no son demanda base); filas duplicadas exactas por doble exportacion; costo en cero en algunas filas; SKU discontinuada que sigue apareciendo con stock |

Nota de reuso: `jobs/intake.py` ya hace mapeo difuso de encabezados y ya devuelve
un `IntakeQuality`. Parte del antidoto de nivel 1 existe, y ese `IntakeQuality`
es el enganche natural para medir cuanto anuncio Kern.

### 3. `src/simworld/runners.py` — los tres corredores

Contrato unico: `(view) -> dict[sku, qty]`.

| Corredor | Que es | Como decide |
|---|---|---|
| `naive` | **Piso** | Punto de reorden fijo, recalculado nunca: `R = 5 x promedio semanal de las ultimas 4 semanas observadas`, congelado en la semana 4. Cuando el stock en mano cae por debajo de `R`, pide hasta `R`. Es lo que hace el `stale_reorder_point` del demo actual, y lo que hace el cliente real |
| `kern_brief` | **Lo que se mide** | `Orchestrator.run(brief, data_path=view)` con brief en lenguaje natural. Camino completo incluyendo clasificacion de intencion |
| `kern_forced` | **Carril de diagnostico** | Igual pero `job_type="inventory_optimization"`. La diferencia contra `kern_brief` aisla el costo del ruteo |
| `oracle` | **Techo** | Ve la demanda futura verdadera y el lead time real de cada orden que emite. Pide, en cada semana, exactamente lo que se consumira durante ese lead time real: sin stock de seguridad, porque no hay incertidumbre que cubrir. Miope por diseno y aun asi inalcanzable. **Nunca ve la lente** |

**Kern actua por el plano de writeback, no lo puentea.** El `GuidedOutcome` se
traduce asi: `EXECUTED` -> se aplica la orden. `OPTIONS` -> se toma la mejor
rankeada. `HANDOFF` y `ESCALATED` -> **semana sin accion**, y se cuenta. Eso
modela a un humano dormido y pone precio honesto al contrato de nunca
desprotegido, en vez de esconderlo.

### 4. `src/simworld/scorer.py` — el marcador

Contrato de KPI portado de gym-invmgmt: nivel de servicio, fill rate, demanda
insatisfecha, inventario promedio, diagnostico de latigo (`var(ordenes) /
var(demanda)`), costo descompuesto (compra, mantenimiento, tuberia, penalidad de
quiebre, costo fijo de ordenar) y profit por periodo.

Metricas de sesgo portadas de AIM-Bench: anclaje a la media de demanda, y
persecucion de demanda (correlacion entre la orden de `t` y la demanda de `t-1`).
Kern es determinista: estructuralmente no deberia tener ninguno de los dos. Es la
respuesta medible a "por que no uso un LLM suelto para esto".

**Marcador normalizado:**

```
captura = (costo_piso - costo_kern) / (costo_piso - costo_oraculo)
```

`0` = no mejor que el punto de reorden fijo. `1` = igual al oraculo. Un numero
absoluto es discutible; este aguanta a un comprador esceptico, y el oraculo, al
ser inalcanzable por construccion, protege de sobrevender.

**Degradacion anunciada** — el numero que ningun banco publicado mide:

La atribucion es **por periodo**, no global. Para cada semana `t` se calcula el
exceso de costo que la suciedad le agrego a Kern respecto de la misma semana en
el nivel 0 — misma semilla, mismo mundo, asi que las semanas son comparables una
a una:

```
exceso(t, n) = costo_kern(t, nivel n) - costo_kern(t, nivel 0)
caida(n)     = suma de exceso(t, n) sobre todas las semanas
anunciado(n) = suma de exceso(t, n) solo en las semanas donde Kern emitio senal protectora
silencio(n)  = caida(n) - anunciado(n)
```

Senal protectora en la semana `t` = `IntakeQuality` bajo umbral, o el `JobResult`
volvio `needs_data` / `needs_clarification` / `qa_failed`, o el reporte llevaba
una advertencia de `data_quality`. Una sola alcanza.

`silencio(n)` es el caso que importa: Kern entrego un numero con cara de seguro,
construido sobre un exporte roto, sin decir nada. Es el unico de los dos que le
importa a alguien que va a firmar una orden de compra con la recomendacion.

Borde a manejar explicitamente: `exceso(t, n)` puede ser **negativo** — la
suciedad puede abaratar una semana por accidente. No se recorta a cero; se suma
con su signo, y si el total sale negativo el reporte lo dice en vez de esconderlo
detras de un `max(0, ...)`.

### CLI

`examples/run_world_sim.py` — flags `--seed`, `--level`, `--weeks`, `--skus`,
`--runners`, `--out`. Escribe `deliverables/simworld/report.{md,json}` mas la
curva de degradacion (gitignored, como el resto de `deliverables/`).

## Escala de la primera entrega

- 1 regimen de demanda: estacional con tendencia
- 4 niveles de suciedad: 0, 1, 2, 3
- 3 corredores puntuados (piso, `kern_brief`, techo) + 1 carril de diagnostico
- 52 semanas, revision semanal
- 120 SKUs, 4 proveedores
- **12 corridas puntuadas + 4 de diagnostico**

El eje de regimen queda disenado para enchufar los demas despues sin tocar el
motor. Los datos de esos regimenes no entran ahora.

## Errores y bordes

- **Semilla fija y explicita.** Una corrida que no se reproduce no vale. Se
  imprime en el reporte.
- **Si Kern iguala al oraculo, es un bug, no un logro.** El oraculo es
  inalcanzable por construccion; igualarlo significa fuga de informacion del
  mundo hacia la lente. Falla ruidosa, no silenciosa.
- Si un corredor pide una SKU que no existe en el mundo, es error — no se ignora.
- Un corredor que devuelve `{}` toda la corrida debe producir el peor marcador
  posible, no un `NaN` que se lea como neutro. (Antecedente directo: el NaN
  silencioso de safety stock cerrado en #193.)
- ASCII en todo print de consola (cp1252 en Windows).
- Ningun import de extra opcional a nivel de modulo en la cadena de arranque.

## Que NO entra (YAGNI)

- Precio, abastecimiento y multi-echelon.
- Los otros tres regimenes de demanda.
- Adaptador de Kern a gym-invmgmt para validacion externa publicable.
- LLM real en el brief: corre con el clasificador determinista. El eje queda
  abierto.
- Registrar `simworld` como herramienta del agente. Es un arnes, no una
  capacidad de cliente.

## Pruebas

- Unitaria: cada inyector de suciedad corrompe lo que declara y nada mas.
- Unitaria: `advance` conserva la masa — lo pedido mas lo que habia es igual a lo
  vendido mas lo que queda mas lo perdido.
- Propiedad: misma semilla, mismo mundo, byte a byte.
- Propiedad: la lente no muta el mundo.
- Propiedad: los tres corredores ven la realizacion identica de demanda y lead
  time.
- Integracion: corrida corta (8 semanas, 5 SKUs, nivel 0) donde el oraculo domina
  al piso y Kern queda entre ambos.
- Regresion: el marcador de esa corrida corta queda fijado como golden.
