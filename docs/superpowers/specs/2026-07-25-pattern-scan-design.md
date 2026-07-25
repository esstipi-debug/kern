# pattern_scan — deteccion de patrones cronicos sobre la historia de eventos

> Spec de diseno. Fecha: 2026-07-25. Tool #47.
> Estado: aprobado por el usuario, listo para `writing-plans`.

## 1. Problema

`scm_agent/events.py` (`EventLedger`) guarda los eventos que emiten los monitores del
Control Tower, con una ventana de dedup de 3600s. Los monitores corren cada 60 min
(`jobs/monitors_job.py:65`), asi que mientras una condicion se sostiene queda ~1 fila
por hora. Esa historia **no la lee nadie**: el unico lector es `jobs/digest_job.py:48`,
que filtra a 24h y cuenta por tipo.

El resultado es que Kern trata cada alerta como un incidente nuevo. `config/event_routing.yaml`
rutea `rop_breach` a `inventory_optimization` en T2, o sea "hubo una rotura, generemos un
pedido" — la sexta vez igual que la primera. El sistema esta construido para **colapsar**
la senal repetida (esa es la razon de ser del dedup), que es exactamente lo contrario de
buscarla.

`pattern_scan` lee esa historia para separar el sintoma repetido (patron cronico -> arreglo
de **politica**) del incidente aislado (-> pedido puntual).

### Dos hallazgos de exploracion que condicionan el diseno

1. **En produccion la historia no existe todavia.** `LINCHPIN_EVENTS_PATH` no esta seteado
   en `fly.toml`, ni en el `Dockerfile`, ni en los secrets de Fly (verificado: hay 9 secrets
   y ninguno es ese). El ledger cae en su default `data/events.sqlite3` y, con `WORKDIR /app`,
   eso es `/app/data/events.sqlite3` — el filesystem efimero de la maquina. El volumen de Fly
   esta montado en `/data` y hoy solo guarda `mcp_keys.sqlite3`. **Cada deploy borra todos los
   eventos.** De ahi sale PR-0 (seccion 8).

2. **Los monitores nunca avisan cuando se apaga la alarma.** Las seis constantes de evento
   (`scm_agent/monitors.py:96`) son todas de encendido; no existe `rop_breach_resolved`.
   Contar filas por `dedup_key` mide **cuantas horas duro** la condicion, no cuantas veces
   volvio. Distinguir una rotura larga de N roturas separadas hay que inferirlo de los huecos
   entre timestamps (seccion 5.E).

## 2. Decisiones tomadas

| # | Decision | Elegido |
|---|---|---|
| 1 | Fuente de la historia | **Las dos**: `EventLedger` + CSV del cliente, via adaptadores a un tipo comun |
| 2 | Profundidad de la deteccion | **Recurrencia + denominador comun** (agrupar los cronicos por una dimension) |
| 3 | Accion sobre el hallazgo | **Reporte + `GuidedOutcome` con OPTIONS rankeadas.** No muta nada |
| 4 | Inferencia de episodios | **Por hueco temporal**, sin agregar eventos de resolucion |
| 5 | Ubicacion de la logica | **Motor puro en `src/patterns.py`** + adaptadores en `jobs/pattern_scan_job.py` |
| 6 | Roster de `src/state` | **Dentro de PR-1** (habilita `base_mode="evaluated"` y deteccion de censura) |

## 3. Arquitectura

```
EventLedger  ─┐
              ├─> Occurrence(entity_id, signal, ts, severity, source)
CSV historia ─┘                    │
                                   v
                  ┌────────────────────────────────────┐
                  │  src/patterns.py — funciones puras │
                  │  episodios -> perfiles -> denominador│
                  └────────────────────────────────────┘
                                   │
                    PatternReport ─> qa() ─> deliverable + OPTIONS
```

El motor nunca sabe si la ocurrencia vino de sqlite o de un CSV. Agregar una tercera fuente
(un ERP) es un adaptador nuevo y nada mas.

**Nota anti-recurrencia de revision:** se planteo que `jobs/pattern_scan_job.py` importando
`scm_agent.events` a nivel de modulo daria import circular via `scm_agent/__init__.py -> .tools
-> jobs`. **Es falso.** `jobs/price_watch.py:125`, `jobs/price_intelligence.py:50`,
`jobs/price_monitor.py:56`, `jobs/digest_job.py:22`, `jobs/monitors_job.py:53` y
`jobs/price_watch_position.py:49` ya lo hacen, y los dos primeros son tools registrados que
`tools.py` importa eager. `scm_agent/events.py` solo importa stdlib, asi que la importacion del
submodulo durante la inicializacion parcial del paquete resuelve sin ciclo. `disruption_scan`
(#182) **si** es la plantilla correcta.

## 4. Que cambio despues de la revision adversarial

Nueve agentes en cuatro lentes (estadistica, convencion del repo, modos de falla, dominio SCM)
produjeron 63 hallazgos; 44 sobrevivieron una fase de refutacion. Los que cambiaron el diseno
de fondo:

| Lente | Defecto del borrador | Consecuencia si shipeaba |
|---|---|---|
| estadistica | Sin correccion por comparaciones multiples | Con 4 dims x 10 valores y **cero senal real**, P(>=1 hallazgo espurio) = **1.000**. La opcion "atacar el denominador" se emitiria siempre, en todos los clientes |
| dominio | Cruzar el ROP contado como falla | En revision continua un A-class sano cruza su ROP cada ciclo. Los SKUs **mejor** gestionados salian cronicos y recibian la receta de subir buffer |
| adversarial | Huecos no certificados | Un outage de 3 dias convierte `persistent` en `chronic` — diagnosticos **opuestos** con arreglos opuestos. Invierte la distincion que el tool existe para hacer |
| dominio | Falta la clase `oscillating` | Unico caso donde la recomendacion hace dano activo: subir buffer a algo que oscila quiebre/exceso amplifica bullwhip y congela capital |
| adversarial | `entity_id` sin fallback | Los eventos con `sku=None` legitimo colapsaban en UNA entidad vacia que acumulaba mas episodios que cualquier SKU real y ganaba el ranking |
| estadistica | Ranking por `n * lift` | Equivale a `N n^2 / (C m_v)`: premia el denominador chico al cuadrado. 3-de-3 rankeaba sobre 12-de-60 |
| convencion | `list_all()` como unico lector | Carga la tabla entera + `json.loads` por fila, sin indice por ts. `digest_job.py:48` **ya hace ese antipatron hoy** |
| convencion | 7 pinned places, son 9 | `/how-it-works` publica "41 capabilities" con 46 registradas. El pin global vive en `tests/test_price_watch_tool.py:165` |

## 5. Contratos

### A. Tipo seam

```python
Occurrence(
    entity_id: str,          # NUNCA vacio
    signal: str,             # tipo de evento crudo, sin normalizar
    ts: datetime,            # SIEMPRE tz-aware UTC
    severity: str | None = None,
    source: str | None = None,
)
```

**Derivacion de `entity_id`** en el adaptador del ledger (replica `scm_agent/monitors.py`'s
`_price_signal_identifier`, no inventa fallback nuevo):

```
entity_id = event.sku or payload["competitor_sku_ref"] or payload["domain"]
```

Si ninguno resuelve a cadena no vacia, la fila **se descarta, se cuenta y se reporta**
(`n_rows_dropped_no_entity`). Jamas se coerciona a `""` ni a `"None"`.

**Allowlist explicita de tipos** (param `signals`, default = el mapa de familias de E). Los
eventos de salud (`site_degraded`, `stale_feed`, `extraction_failed`) quedan **fuera** del
analisis pero **si** cuentan como actividad del ledger para certificar huecos (E). Los eventos
de autonomia viven en otro sqlite (`LINCHPIN_AUTONOMY_PATH`), no pueden contaminar.

**Invariante:** toda `Occurrence.ts` es tz-aware UTC. Ninguna ruta hace `.replace(tzinfo=None)`.

### B. CSV de historia (fuente dia 1)

| Campo | Alias aceptados |
|---|---|
| `entity_id` | sku, product_id, item, codigo, material |
| `signal` | event_type, tipo, evento, alerta, motivo |
| `timestamp` | date, fecha, ts, datetime, fecha_hora |

Opcional: `severity`.

**Parseo de fechas — decision a nivel ARCHIVO, no fila por fila:**

1. Se escanea la columna completa antes de parsear nada.
2. ISO-8601 se detecta y se aplica directo.
3. Con separador `/` o `-`: si algun valor prueba dd/mm (primera posicion > 12) **y** otro
   prueba mm/dd (segunda posicion > 12), el archivo es contradictorio -> `needs_clarification`
   pidiendo `--date-format`. Es el **unico** caso de bloqueo por ambiguedad.
4. Si nada contradice, se aplica `date_format` (default `dmy`) y **se declara**:
   `"interpretado como dd/mm; si tu archivo es mm/dd pasa --date-format=mdy"`.
5. Naive = UTC (convencion de `events.py::_ensure_utc`).

Bloquear un archivo usable es peor que declarar el supuesto; solo se bloquea el internamente
contradictorio.

**Disclosure obligatorio** (regla 14, ningun cap silencioso): `n_rows_read`,
`n_rows_dropped_bad_ts`, `n_rows_dropped_no_entity`, `n_rows_future_ts` (ts > now + 48h: se
cuentan, se excluyen del span, se reportan — cubre `=TODAY()` y seriales 1900 que inflarian el
span y harian pasar `min_history_days` con datos de tres dias), `ts_min_parsed` / `ts_max_parsed`
**siempre impresos** (un rango 1998-2026 salta a la vista), `date_format_applied`,
`signal_vocabulary`.

**Vocabulario de senales: cero fuzzy matching.** Las senales que no matchean exactamente el mapa
de familias corren en modo `signals-as-given` (cada senal es su propia familia) y el reporte lo
dice. Normalizar en silencio es peor que no normalizar — es el error que mordio en la trilogia
de citation-fix (#160/#162/#164).

### C. CSV maestro opcional (denominador comun)

`entity_id` + cualquier otra columna = dimension candidata. Cero nombres hardcodeados.

**Elegibilidad por CARDINALIDAD, no por tipo:**

- Rechaza si < 2 valores distintos: `"demasiado uniforme"`.
- Rechaza si > `dimension_max_values` (50): `"demasiado granular (k=N valores)"`.
- **Los valores numericos se tratan como etiquetas discretas.** `lead_time_days` con {7, 45}
  tiene cardinalidad 2 y entra sola — es la dimension mas predictiva de la teoria de inventarios
  y el borrador la borraba con su regla "solo categoricas". Cero cuantiles, cero breakpoints
  derivados de los datos.
- **No existe el tope `N/3`**: descartaba la columna "proveedor" entera justo en el cliente chico.
  La proteccion contra columnas tipo-ID es el piso de exposicion por VALOR (seccion F).

**Nada se descarta en silencio:** el report carga `dimensions_evaluated` y
`dimensions_rejected: tuple[tuple[str, str], ...]` (columna, motivo), impresos siempre.

**Semantica del join** (por dimension D): `valid(D)` = entidades de la poblacion base que estan
en el maestro y tienen valor no nulo en D. Las que no, van a un bucket explicito `<sin dato>`
para que las shares sumen 1.0; ese bucket se reporta como calidad de dato y es **inelegible**
como hallazgo. Se publican `entities_in_history`, `entities_in_roster`, `entities_in_maestro`,
`entities_matched`, `n_chronics_unmapped`.

Sin maestro, degrada al modo simple (solo perfiles, sin denominador) y lo dice. No falla.

### D. Umbrales

| Param | Default | Rol |
|---|---|---|
| `window_days` | 90 | ventana pedida |
| `episode_gap_hours` | 48 | hueco que parte episodio |
| `episode_gap_hours_alt` | 24 | segunda corrida de sensibilidad |
| `chronic_min_episodes` | 3 | clamp `max(2, valor)` |
| `min_history_days` | 30 | por debajo -> `needs_data` |
| `persistent_duty_pct` | 50 | umbral de duty cycle |
| `denominator_min_lift` | 1.5 | piso de MAGNITUD |
| `denominator_min_chronics` | 3 | piso de TAMANO DE EFECTO |
| `denominator_min_base` | 10 | piso de EXPOSICION del valor |
| `denominator_fdr_q` | 0.10 | piso de EVIDENCIA (Benjamini-Hochberg) |
| `dimension_max_values` | 50 | cardinalidad maxima de columna |
| `finding_overlap_pct` | 80 | colapso de dimensiones correlacionadas |
| `max_rows` | 500000 | tope duro de lectura |
| `date_format` | `dmy` | convencion CSV |
| `base_mode` | `auto` | `auto` / `evaluated` / `observed` |

`acute` se **deriva**: `2 <= episodios_falla < chronic_min_episodes`. Con
`chronic_min_episodes=2` la banda queda vacia por construccion, no ambigua.

Todos los defaults efectivamente usados se imprimen en el reporte, marcando cual vino del caller.

### E. Clasificacion

**Clave del perfil: (entidad, FAMILIA de senal).** Mapa explicito en config, sin heuristica:

```
availability = {rop_breach, stockout_projected}   escalation = stockout_projected
excess       = {excess_growing}                   escalation = None
price        = {price_move, competitor_price_move, competitor_oos, promo_detected}
                                                  escalation = None
```

`rop_breach` y `stockout_projected` son excluyentes por SKU en un mismo instante: sin familias,
el peor articulo del catalogo se parte en dos perfiles `one_off`. La senal cruda se conserva por
trazabilidad.

**Construccion de episodios:**

- Ocurrencias ordenadas por `ts` ASC, **nunca** por `seq`.
- Un hueco parte episodio solo si `gap >= episode_gap_hours` **Y** el hueco esta **certificado**.
- **Certificacion (ledger):** el hueco esta certificado si el ledger escribio filas de cualquier
  otro tipo o entidad durante ese hueco (incluidos los eventos de salud). Si el ledger estuvo
  globalmente mudo, el sistema pudo estar caido -> hueco `unknown` -> **los dos lados quedan como
  UN episodio**. Nunca se inventa recurrencia.
- **Certificacion (CSV):** no hay instrumentacion que consultar -> se asumen certificados y el
  reporte lo declara en una linea.
- Cada episodio publica `start`, `end`, `span_hours`, `n_occurrences`, `peak_signal`,
  `boundary_certified: bool` y el tamano del hueco previo.
- **Convencion de span:** `span = ultima - primera + una unidad de cadencia` (el dedup window,
  3600s, para el ledger; el intervalo mediano observado para el CSV). Un episodio de una sola
  ocurrencia nunca vale 0 horas.

**Metricas por perfil:** `n_occurrences`, `n_episodes`, `n_failure_episodes`,
`escalation_rate = n_failure_episodes / n_episodes`, `duty_cycle` (horas cubiertas / horas de
ventana efectiva), `days_since_last_episode`, `n_boundaries_unknown`.

**Etiquetas — dos ejes ortogonales, NO excluyentes:**

| Eje | Etiqueta | Regla |
|---|---|---|
| Recurrencia | `one_off` | 1 episodio-falla |
| Recurrencia | `acute` | `2 <= episodios_falla < chronic_min_episodes` |
| Recurrencia | `chronic` | `episodios_falla >= chronic_min_episodes` |
| Persistencia | `persistent` | `duty_cycle >= persistent_duty_pct`, con cualquier numero de episodios |
| Interaccion | `oscillating` | perfil `availability` cronico o persistente **Y** `excess_growing` sobre la misma entidad en la misma ventana |

**Precedencia de ruteo del mensaje** (las etiquetas se publican todas):
`oscillating > persistent > chronic > acute > one_off`.

**Reglas de honestidad, obligatorias:**

- Un perfil `availability` cronico con `escalation_rate == 0` (puro `rop_breach`) se reporta como
  `"cadencia de reposicion normal / la politica esta funcionando"` y queda **PROHIBIDO** como base
  de cualquier opcion que suba stock. Los cruces de ROP son ciclos de reposicion observados, no
  fallas.
- `escalation_rate` se nombra **"tasa de escalacion a cover critico"**, nunca *cycle service
  level*: el ledger no registra quiebre consumado ni recepciones, es un proxy adelantado.
  `knowledge::cycle_service_level` se cita como marco conceptual, jamas como igualdad.
- Un perfil `persistent` con `escalation_rate == 0` se reporta como `"permanentemente en zona de
  reposicion sin llegar a riesgo de quiebre: revisar si el ROP esta sobredimensionado o si la
  reposicion no cierra"`.
- Se imprime **siempre** `duty_cycle` y el span junto a la etiqueta, con la resolucion de muestreo
  como incertidumbre declarada (`"44.9 d +/-4h, en el limite"`). Jamas se invierte una
  recomendacion sin mostrar el numero.
- El listado de no-cronicos se ordena por `duty_cycle` y `span_max` descendente, no alfabetico.
- Los cronicos se ordenan por (`n_failure_episodes`, `max_severity`), desempate por recencia. Los
  que no tuvieron actividad en el ultimo tercio de la ventana se marcan
  `"sin actividad en los ultimos K dias"`.

**Sensibilidad al gap (obligatoria, costo cero):** la clasificacion se corre una segunda vez con
`episode_gap_hours_alt=24` — es re-llamar una funcion pura — y el reporte declara toda entidad
cuya etiqueta cambia: `"con gap=24h este SKU seria chronic(3) en vez de one_off"`. Convierte un
riesgo de calibracion invisible en uno declarado.

**Fechas siempre visibles:** cada perfil imprime la lista de episodios con fechas e intervalos
entre ellos (`"45, 42, 44 dias"`). Sin eso el humano no puede detectar ni una fusion espuria ni
un patron de calendario.

### F. Denominador comun

**Poblacion base — una definicion, dos modos nombrados:**

- `base_mode="evaluated"` (preferido, habilitado por la decision 6):
  `base = (roster evaluado) UNION (entidades observadas en la historia)`. El roster sale del
  snapshot `stock` mas reciente de `src/state` (UNA lectura, no los snapshots de la ventana). La
  union evita perder entidades discontinuadas a mitad de ventana.
- `base_mode="observed"` (degradado): sin roster, `base = entidades observadas`. El reporte lo
  etiqueta: `"lift condicional a haber alertado al menos una vez en la ventana; es la version
  atenuada"`.
- `base_mode="auto"` (**default**): resuelve a `evaluated` si hay un snapshot `stock` legible en
  `src/state`, y a `observed` si no. Nunca falla por ausencia de roster. Pasar `evaluated` explicito
  **si** convierte la ausencia de roster en `needs_data` — es la forma de decir "no me des el
  numero atenuado sin avisarme".

El modo resuelto se declara por nombre. La base **nunca** es el maestro crudo: incluiria entidades
jamas monitoreadas e inflaria el lift en la direccion contraria.

**Entidades censuradas:** toda entidad con historia que no aparece en el roster mas reciente
recibe marca `censored/unknown` en su hueco final, su ultimo episodio se reporta como
`"abierto o censurado, no resuelto"` (jamas "resuelto") y queda excluida de la base.

**Formula**, por dimension D y valor v, sobre `valid(D)`:

```
N   = |valid(D)|                     # base valida de esa dimension
C   = cronicos dentro de valid(D)
m_v = entidades de valid(D) con D = v
n_v = cronicos con D = v

share_chronic(v) = n_v / C
share_base(v)    = m_v / N
lift(v)          = share_chronic(v) / share_base(v)
excess(v)        = n_v - C * m_v / N   # cronicos atribuibles al valor
```

**Gate — pasa solo si cumple los CUATRO:**

1. **Exposicion:** `m_v >= denominator_min_base` (10). Los de abajo se agrupan en el bucket
   `otros (k valores, cada uno < 10 entidades)`, que se reporta como cobertura y es inelegible.
2. **Tamano de efecto:** `n_v >= denominator_min_chronics` (3).
3. **Magnitud:** `lift(v) >= denominator_min_lift` (1.5).
4. **Evidencia:** Fisher exacto una cola sobre la 2x2
   `[[n_v, m_v - n_v], [C - n_v, N - m_v - (C - n_v)]]`, con correccion Benjamini-Hochberg sobre
   las K celdas evaluadas -> `q <= denominator_fdr_q` (0.10). `scipy>=1.10` ya es dependencia core.

**Publicacion obligatoria:**

- Por hallazgo: `n_v`, `m_v`, `C`, `N`, `lift`, IC95 del lift, `p` crudo, `q`, `excess`. Un lift
  sin su fraccion cruda visible (`"12 de 60"`) no se muestra **nunca**.
- A nivel reporte: `K` comparaciones evaluadas y cual de los pisos mordio.
- **Tabla de conteos absolutos:** para cada dimension evaluada, el top de valores por `n_v` crudo,
  **siempre**, pasen o no el gate. Es base-independiente y garantiza que el valor que concentra
  mas dolor no pueda quedar fuera del reporte por un artefacto de eleccion de base.
- Si ninguna celda alcanza `q <= 0.10`: `"no hay denominador comun con evidencia en esta ventana"`
  y cae a las OPTIONS 2/3. Nunca se omite la seccion en silencio.
- No hay piso `C >= 15`: con C chico ningun test alcanza significancia solo, que es el
  comportamiento correcto y auto-regulado.

**Ranking** (solo entre los que pasaron): por `excess(v)` descendente, desempate por `q`
ascendente. `n_v`, `m_v` y `lift` son columnas de contexto, nunca clave de orden. El reporte
explicita que el orden es **"cronicos evitables"**, no "fuerza de la senal".

**Colapso de dimensiones correlacionadas:** los hallazgos que comparten mas de
`finding_overlap_pct` (80%) de su conjunto de cronicos se colapsan en uno:
`"proveedor=Alfa / marca=Alfa-brand / categoria=Congelados: los mismos 12 cronicos"`. El
representante es el de mayor exposicion base (el mas accionable); los otros van como alias. Se
declara la limitacion: `"estas dimensiones estan correlacionadas; el reporte no separa cual es la
causa"`.

### G. GuidedOutcome

**`needs_data`** (antes de correr el motor): historia < `min_history_days`, archivo de ledger
inexistente, o cero ocurrencias. El mensaje lleva el span real y la epoca del ledger.

**`needs_clarification`:** CSV con convencion de fecha contradictoria, o filas en ventana >
`max_rows` (con el `window_days` sugerido).

**`EXECUTED`** — solo si `chronic == 0 AND persistent == 0 AND acute == 0 AND oscillating == 0`:

> "sin patron recurrente en esta ventana: son incidentes aislados -> tratarlos por incidente
> (top N por severidad). Limitacion declarada: una ventana de 90 dias no puede observar
> recurrencia estacional ni ciclos de reposicion largos."

**Prohibido usar la palabra "ruido".**

**`OPTIONS`** — cualquier otro caso:

1. *(solo si hay >=1 GroupFinding con `q <= 0.10`)* `"Atacar el denominador: N cronicos
   concentrados en <dim>=<valor> (n_v de m_v, lift X, q=Y, exceso atribuible Z SKUs)"`.
2. `"Re-perfilar los buffers de los N cronicos — verifica primero las fechas de abajo: si los
   episodios coinciden con promociones o temporada, el arreglo es un pre-build estacional, NO
   subir el buffer base."`
3. `action = "correr inventory_optimization sobre estos K SKUs con service_level=0.98"`,
   `tradeoffs = "el costo de este salto lo calcula ese tool; pattern_scan solo identifica los K"`.
   **Sin cifras de costo en este deliverable** — el tool no tiene holding rate, costo unitario,
   sigma ni lead time.
4. *(solo si `persistent > 0`)* `"N entidades no se estan reponiendo (duty cycle >= 50%): revisar
   cadencia y cantidad de pedido, no el buffer."`
5. *(solo si `acute > 0` y no hay cronicos ni persistentes)* watchlist: `"N entidades con 2
   episodios; re-escanear en X dias."` Nunca `EXECUTED`.
6. *(solo si `oscillating > 0`)* `"N entidades oscilan entre quiebre y exceso: el problema es
   cantidad y cadencia de pedido, no el nivel."` Esta rama **prohibe** que cualquier otra opcion
   cite esas entidades para subir buffer o nivel de servicio.

Las coocurrencias con `lead_time_drift` y `forecast_error_out_of_band` se emiten como **anotacion**
de causa probable en la ficha de la entidad, sin crear clases nuevas ni rutear recomendaciones.

`Residual` obligatorio con `risk_if_skipped` poblado (lo exige `verify_guided`).

### H. QA — invariantes que bloquean el deliverable

Primera linea de `verify()`, sin excepcion:

```python
issues = list(verify_guided(report.outcome))
```

Es el gate compartido del repo y el unico lugar que atrapa `score` no finito antes de
`SafeJSONResponse(allow_nan=False)`. Los de abajo se **agregan**, no lo reemplazan.

**Estructurales**

1. Toda `Occurrence.ts` es tz-aware UTC y las ocurrencias de cada perfil estan ordenadas por `ts`
   ASC. Ningun `gap` negativo. *(El bug real: `list_all()` ordena por `seq`, y `price_move` usa
   `ts=offer.observed_at` mientras `competitor_price_move`, emitido despues en el mismo ciclo,
   usa `datetime.now()`.)*
2. Ningun `entity_id` vacio, `None` o `"None"`.
3. Cada perfil tiene exactamente una etiqueta de recurrencia, y esas etiquetas particionan las
   entidades observadas.
4. `sum_v n_chronic(v) == C` exactamente para toda dimension evaluada (incluido `<sin dato>`), y
   `abs(sum_v share_chronic(v) - 1.0) < 1e-9`.
5. `finding.base_n > 0`, `finding.base_n <= report.n_entities_valid[dim]`,
   `math.isfinite(finding.lift)`.
6. Ningun `GroupFinding` cita entidades fuera del set cronico.

**De disclosure** (auto-consistencia sobre campos del report, porque `qa()` recibe solo el report
y corre **antes** de `deliver()`)

7. `n_rows_dropped_bad_ts > 0` sin `data_quality_note` -> issue. Idem `n_rows_dropped_no_entity`
   y `n_rows_future_ts`.
8. `window_days_effective < window_days_requested` sin `window_note` -> issue.
9. `master_provided` sin `dimensions_evaluated` ni `dimensions_rejected` -> issue.
10. `group_findings` sin `absolute_counts_by_dimension` de la misma dimension -> issue.
11. `n_boundaries_unknown` y `n_profiles_censored` presentes siempre, incluso en 0.

**De dominio**

12. Ninguna opcion que sume inventario cita una entidad `oscillating` o con `excess_growing` en la
    misma ventana.
13. `EXECUTED` bloqueado si existe cualquier perfil `persistent`, `chronic`, `acute` u `oscillating`.
14. Un perfil `chronic` de familia `availability` con `escalation_rate == 0` no puede citarse como
    falla de servicio ni fundar una opcion de subir buffer.

Se **elimina** el invariante `n_episodios > n_ocurrencias` del borrador: es imposible de violar
por construccion y solo da falsa seguridad.

*Cobertura complementaria fuera de `qa()`:* un test asegura que el markdown de
`write_operational` y `build_deck` imprime esos mismos campos. `qa()` nunca ve el markdown.

## 6. Fuera de alcance (YAGNI)

| Que | Por que no |
|---|---|
| `Occurrence.metrics` + bandera "ROP vencido" | Analisis nuevo entero con sus umbrales, texto y tests. El orden de "los K peores" ya queda por `severity` + recurrencia. v2 |
| Costeo del salto de nivel de servicio | El tool no tiene holding rate, costo unitario, sigma ni lead time. Se delega por `action`. Meter numeros seria el sobre-claim que #182 tuvo que corregir |
| `episode_gap_hours` por entidad desde `lead_time_days` | Parametros por fila, join nuevo, superficie de test. La corrida de sensibilidad a 24h da el 90% del valor |
| Clases `escalating`/`decaying`/`dormant`, detector estacional | Con `chronic_min_episodes=3` hay 2 huecos inter-episodio; cualquier estadistico sobre 2 observaciones es ruido |
| Calendario de promociones como input | Input, join y tests nuevos. Se cubre con el copy de la Opcion 2 + las fechas visibles. v2 |
| Banda `extended` (duty 20-50%) | Imprimir `duty_cycle` al lado de la etiqueta elimina el 90% del dano del acantilado |
| Bucketizacion por cuantiles de numericas continuas | La regla de cardinalidad ya cubre el caso del hallazgo. Cortes fijos son v2 |
| Chequeo formal de Simpson entre dimensiones | El colapso por solapamiento >80% + la limitacion declarada es lo que el planificador necesita |
| Modo read-only del sqlite | El chequeo `Path.exists()` antes de construir ya cierra el modo de falla real |
| Retencion / poda del ledger | Mutacion destructiva de historia durable en un PR cuyo tool declara "no muta nada". Follow-up con disparador documentado |
| Piso `C >= 15` | Arbitrario, mata la funcion en el cliente chico, y redundante con el gate de evidencia |
| Bloquear si la cobertura del maestro difiere >10 puntos | La regla 14 pide **declarar**, no abortar. Bloquear castiga al cliente con datos imperfectos, que es el cliente tipico |
| Fuzzy matching del vocabulario de senales | Generador de falsos amigos — exactamente lo que mordio en #160/#162/#164 |
| `monitor_runs` + latido en `run_all_monitors` | La certificacion retrospectiva cubre el caso desde el dia 1 sin schema nuevo ni ruta de escritura nueva. Follow-up, issue abierto el mismo dia |

## 7. Riesgos declarados

- **Ledger esparso.** Con 1-2 entidades alertando, casi ningun hueco se certifica y todo se
  fusiona conservadoramente. El reporte lo declara via `n_boundaries_unknown`. Es el precio de no
  inventar recurrencia, y es el modo de falla correcto (falso negativo, no falso positivo).
- **Ventana de 90 dias.** No puede observar recurrencia estacional ni ciclos de reposicion largos.
  Se declara en el texto de `EXECUTED`.
- **Dimensiones correlacionadas.** El colapso las junta pero no separa cual es la causa. Declarado.
- **`escalation_rate` es un proxy adelantado**, no cycle service level. Declarado en el texto y en
  la cita.

## 8. Alcance

### PR-0 — hoy, antes de escribir una linea del motor

`fly.toml`: `LINCHPIN_EVENTS_PATH = "/data/events.sqlite3"` y
`LINCHPIN_STATE_PATH = "/data/state.sqlite3"` + **`flyctl deploy` MANUAL**.

No hay workflow de auto-deploy en `main` (`.github/workflows/` solo tiene `tests.yml` y
`price-watch-cron.yml`), asi que mergear no cambia prod. **El reloj de `min_history_days=30`
arranca con ese deploy, no con el merge.** Verificacion post-deploy: `/data` existe y el ledger
nuevo escribe ahi.

### PR-0b — 10 lineas, independiente

Backfill de `webapp/how_it_works_data.py` 41 -> 46 (faltan `disruption_scan`, `hat_settlement`,
`hat_tension`, `network_design`, `supplier_management`) + los tres asserts de
`tests/test_how_it_works_data.py` (`len`, tally de `domain_area`, tally de `scor_bucket`). Va
separado porque reasignar `domain_area`/`scor_bucket` de 5 tools es criterio, no mecanica, y hoy
la pagina publica dice "41 capabilities" con 46 registradas.

### PR-1 — el tool

**Codigo nuevo**

- `src/patterns.py` — motor puro: `Occurrence`, familias, episodios, perfiles, denominador. Sin I/O.
- `jobs/pattern_scan_job.py` — adaptadores ledger + CSV,
  `prepare/run/verify/write_operational/build_deck/options`.
- `examples/run_pattern_scan.py` — CLI (`--data` / `--ledger` / `--demo`).

**Roster de `src/state`** (decision 6, ~40 lineas dentro de `jobs/pattern_scan_job.py`)

Una lectura del snapshot `stock` mas reciente via `src.state.system_state.latest("stock")` — UNA
lectura, no los snapshots de la ventana. Es lo que habilita `base_mode="evaluated"` y la deteccion
de entidades censuradas (seccion F). Introduce una dependencia nueva del job hacia `src/state`;
por eso `LINCHPIN_STATE_PATH` entra en PR-0 junto con `LINCHPIN_EVENTS_PATH`. **Si el diff de PR-1
supera ~900 lineas, esta es la primera pieza a cortar**: el tool sigue funcionando en
`base_mode="observed"` con el lift atenuado y declarado.

**Infraestructura del ledger**

- `scm_agent/events.py`: `list_between(start, end, *, types=None, limit=None)` con
  `WHERE ts >= ? AND ts < ? ORDER BY ts ASC`, `CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts)`,
  `count_between(...)` y `epoch()` (`SELECT MIN(ts)`). El parametro se formatea con
  `_ensure_utc(...).isoformat()`; la comparacion lexicografica es segura porque todo write pasa por
  ahi con offset fijo `+00:00`. `list_all()` se **conserva** por back-compat.
- `jobs/digest_job.py:48`: portar `[e for e in ledger.list_all() if e.ts >= cutoff]` a
  `list_between`. Es un bug **activo hoy**, no latente.
- **Chequeo de existencia:** `Path(events_path).exists()` **antes** de construir el `EventLedger`
  -> `needs_data`. `EventLedger.__init__` hace `mkdir` + `connect` + `CREATE TABLE IF NOT EXISTS`,
  o sea fabrica una DB vacia si el path no existe, y el tool declararia "scan limpio" sobre nada.
- **Epoca:** `epoch()` se imprime en todo reporte cuya ventana la cruce: `"historia disponible
  desde 2026-08-01; la ventana pedida de 90 dias cubre solo 12 dias reales"`.

**Los 9 pinned places**

| # | Archivo | Que |
|---|---|---|
| 1 | `scm_agent/tools.py` | `reg.register(...)` |
| 2 | `scm_agent/citation_gate.py` | `TOOL_CONCEPTS["pattern_scan"]` — pool con `limit>=16` sobre keyword set fijo (ceiling empirico, re-medir) |
| 3 | `src/i18n.py` | `TOOL_TITLES` — el titulo `es` no puede ser identico al `en` (`tests/test_i18n.py:72`) |
| 4 | `tests/test_scm_agent.py:123` | key set + assert anti-drift `{t.key for t in TOOLS} == {t.key for t in build_default_registry().list()}` |
| 5 | `tests/test_price_watch_tool.py:165` | pin global `len(reg.list()) == 46` -> `47` |
| 6 | `webapp/how_it_works_data.py` | nueva `ToolInfo(...)` (46 -> 47 tras PR-0b) |
| 7 | `tests/test_how_it_works_data.py` | los 3 asserts |
| 8 | `examples/run_capability_smoke.py` | `_BRIEF` + fixture CSV, si no el tool sale `GATED` en vez de `PASS` |
| 9 | `scm_agent/events.py` + `jobs/digest_job.py` | lector acotado + port |

El assert anti-drift va en `tests/test_scm_agent.py`, **no** en `tests/test_how_it_works_data.py`:
ese modulo es deliberadamente pure-data y arrastrar `scm_agent` le mete orchestrator + grafo +
openpyxl.

**Tests propios** (espejo de #182, que shipeo 3 archivos + 4 fixtures)

- `tests/test_patterns.py` — motor puro: familias, episodios, certificacion de huecos, duty cycle,
  Fisher+BH, ranking por exceso, colapso por solapamiento.
- `tests/test_pattern_scan_job.py` — adaptadores, fallback de `entity_id`, parseo de fechas a nivel
  archivo, disclosure en el markdown.
- `tests/test_pattern_scan_tool.py` — registro, `qa` bloqueante, GuidedOutcome.
- Fixtures: `history_ledger.sqlite3`, `history.csv`, `history_ambiguous_dates.csv`, `master.csv`.
- Test especifico: una fila con `payload_json` corrupto debe leerse igual — prueba de que
  `list_between` no hace `json.loads`.

**Follow-ups con issue abierto:** tabla `monitor_runs` + latido en
`scm_agent/monitors.py::run_all_monitors` (si se hace, va **si o si** ahi, nunca en
`jobs/monitors_job.py::run_monitors_cycle` — ese wrapper no es el camino de prod:
`webapp/app.py:1058` llama `run_all_monitors` directo); y la politica de retencion del ledger
(disparador: `ledger_growth` al 70% del volumen de 1 GB).

## 9. Procedencia de este spec

Borrador de contratos sometido a revision adversarial (`pattern-scan-contract-review`, 9 agentes,
4 lentes independientes, fase de refutacion): 63 hallazgos crudos, 44 confirmados, 0 lentes
fallados. Un hallazgo marcado `critical` por el lente de convencion (import circular via
`scm_agent/__init__`) fue **refutado manualmente** contra el codigo y no se incorporo — ver la
nota en la seccion 3.
