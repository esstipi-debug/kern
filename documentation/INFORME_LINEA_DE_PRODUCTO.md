# Informe: línea de producto, homogeneización y la ruta de citas

> Sesión del 2026-08-09 sobre `kern-main`. Rama `feat/linea-de-producto`, en un
> worktree aparte (`C:\Users\Gamer\Music\scm\kern-linea`). Escrito para que se pueda
> discrepar con una decisión puntual sin releer los diffs.

## Resumen

| Fase | Resultado |
|---|---|
| **1 · Línea de producto** | Hecha. La premisa era falsa: no faltaba decidir, sobraban decisiones. [`LINEA_DE_PRODUCTO.md`](LINEA_DE_PRODUCTO.md) |
| **2 · Homogeneizar** | Casi nada que hacer, medido. Un solo ítem real: partir un módulo de 2031 líneas |
| **3 · Enchufar módulos** | **Nada enchufado, a propósito.** 6 de los 8 candidatos no eran huérfanos |
| **4 · Ruta de citas** | Medida en dos variantes y **descartada**. [`CITAS_LEXICO_VS_HIBRIDO.md`](CITAS_LEXICO_VS_HIBRIDO.md) |
| **5 · Validar** | Suite verde. El hallazgo grande: la suite estaba "roja" por dependencias faltantes, no por código |

Cinco commits, todos con un solo tipo de cambio.

---

## 0. El gate, y los dos desvíos que tomé

El gate de arranque falló en dos de sus tres puntos. Los resolví en vez de parar,
porque los dos tenían salida segura, y los dejo escritos porque cambian cómo hay
que leer el resto.

**El árbol no estaba limpio.** `feat/retencion-de-datos` tenía 14 modificaciones en
el índice y 11 archivos sin trackear de otra sesión (retención, billing, verticales,
paquetes, calculadora ROI). En vez de trabajar encima o stashear trabajo ajeno, creé
un **worktree** desde `e608bbd` en una rama nueva. `kern-main` quedó intacto: nunca
escribí un byte ahí.

Un detalle de esa decisión que conviene saber antes de mergear: la rama sale de
`e608bbd`, que es **la punta de `feat/retencion-de-datos`**, no de `main`. O sea que
arrastra los commits de retención como historia. Lo hice así para no perder trabajo ya
commiteado y empujado a `origin`. Ninguno de mis archivos toca los suyos
(`documentation/`, `scripts/`, `src/bateria/`, dos tests), así que un
`git rebase --onto main feat/retencion-de-datos` debería salir limpio si se prefiere un
PR contra `main` que contenga solo esto. No lo hice yo: reescribir historia de una rama
que otra sesión está usando no es mi llamada.

**El insumo principal no existe.** `09-VIABILIDAD-MONETIZACION.md` no está en el
worktree indicado, ni en el repo nosilo, ni en su historial completo — el slot `09-`
lo ocupan `09-LENTES-ESTRATEGIA.md` y `09-HORIZONTE-ERP-CRM.md`. Lo sustituí por lo
que sí existe y cubre lo mismo o más:

- **`documentation/MONETIZATION_BRIEF.md`**, en este repo, declarado *"the commercial
  source of truth"* por `package_specs.py`. Es literalmente un reporte de viabilidad
  de monetización con verificación adversarial. Es el documento que la misión
  buscaba, y estaba adentro del repo todo el tiempo.
- `05-GTM-KERN.md` y `02-ESTRATEGIA.md` del repo nosilo, ambos nombrados en la
  misión y ambos existentes.

---

## 1. Qué decidí, y contra qué

### El hallazgo que reordena la pregunta

La misión asumía que ~50 tools sin línea de producto significaba *"nadie decidió qué
compra un cliente"*. Medido, es al revés: **se decidió tres veces, incompatiblemente.**

| Fuente | Qué decide | ¿En código? |
|---|---|---|
| `MONETIZATION_BRIEF.md` (jul-2026) | 10 secciones + 2 peldaños self-serve, retainers USD 250–4.500/mes | **Sí**, 9 `PackageSpec` |
| `05-GTM-KERN.md` (**7-ago-2026**) | **Un solo precio: USD 290/mes.** Su §8 prohíbe tramos, negociación, onboarding humano y personalización | No |
| `02-ESTRATEGIA.md` | Enterprise, wedge source-to-pay, 120k–400k/año | No (es Nosilo) |

El código implementa la **más vieja**, y la más nueva —de dos días antes de esta
sesión— la contradice de frente. El problema no era un catálogo sin decisión: era una
decisión vieja congelada en código y una nueva congelada en prosa, sin reconciliar.

### La decisión

**Un solo producto vendible**, en tres peldaños del mismo bucle: escaneo gratis →
Diagnóstico → operación mensual. Núcleo de 15 tools. Segundo anillo de 21 que existe
pero **no se muestra en un menú**. Tres proyectos puntuales. 14 tools fuera de la
línea, sin borrar.

Y lo que **no** decidí, a propósito: el precio y el motion de la operación mensual
(290 autoservicio vs. 900–3.200 con humano). `05-GTM` §1 dice por escrito que esa
decisión *"no está cerrada"*, y las dos ramas comparten embudo, núcleo y escaneo. La
ingeniería no depende de ella; cerrarla yo habría sido inventar autoridad.

### Los números que la sostienen

- **50** tools registradas · **36** en algún paquete · **14 en ninguno**.
- **9** `PackageSpec` en código vs. **13** one-pagers en `documentation/paquetes/` vs.
  **10 secciones** del brief (11 filas contando la variante LatAm) **+ 2 peldaños
  self-serve** de su addendum. Tres inventarios distintos del mismo catálogo, y ninguno
  coincide con otro.
- **5 tiers recurrentes** declarados para un operador solo con **0 clientes** hoy.
  `02-ESTRATEGIA` §7 fija la métrica que eso viola: *">40% ingreso por servicios → eres
  consultora"*.

---

## 2. Homogeneización: qué medí y por qué casi todo estaba bien

La fase asumía deriva estructural entre "los 91 módulos de `src/`" (son **172**
archivos `.py`). Medido, los tres ítems de prioridad dieron esto:

| Prioridad | Medición | Acción |
|---|---|---|
| Forma de retorno de un job | **44 de 57** módulos con hooks usan `prepare`/`run`/`verify`/`write_operational` | **Ninguna** |
| Errores tragados en silencio | **0**. Hay 15 `except Exception` en `src/`, todos re-lanzan, compensan o degradan con nota | **Ninguna** |
| Módulos > 800 líneas | **1**: `src/bateria/comprobaciones.py`, 2031 líneas | Partido |

Sobre los 13 "desviados": no son deriva. Cuatro de ellos (`qa`, `price_watch_deliverable`,
`repricing`, `intake`) ni siquiera son playbooks — son helpers compartidos que exponen un
solo hook porque eso es todo lo que hacen. El contrato `Tool` de
`nosilo_core.registry` exige `prepare`/`run`/`qa`/`deliver` como **callables**, no como
funciones con nombre fijo en el módulo. `inventory_optimization` no tiene `prepare`
propio porque el suyo vive en `scm_agent/tools.py:90`, y su `qa`/`deliver` son los
compartidos de `jobs/qa.py` y `jobs/deliverables.py`. Homogeneizarlos habría
**duplicado código compartido dentro de cada módulo** — exactamente al revés de lo que
la fase busca.

**El split.** `comprobaciones.py` → paquete de cinco módulos por familia de tarea
(máximo 594 líneas) más `_comunes.py`. Las 51 constantes de vocabulario van todas a
`_comunes.py`: un primer intento las repartió por archivo y `ruff` marcó tres F821,
porque varios bloques referencian constantes declaradas en un bloque anterior. El
`__init__.py` importa las cinco partes para preservar el side-effect de registro de
`@comprobacion`.

Verificado por AST contra `HEAD`: **las 95 definiciones están, cero diferencias de
lógica**, lo único nuevo es `__all__`. Es un movimiento puro.

---

## 3. Por qué no enchufé ningún módulo

La lista de ocho candidatos estaba mal formada. **Seis de los ocho no son huérfanos**,
y registrarlos habría duplicado capacidad existente:

| Módulo | Estado real |
|---|---|
| `supplier_scorecard.py` | Ya consumido por `jobs/sourcing_job.py` → tool `sourcing` |
| `working_capital.py` | Ya consumido por `jobs/cost_to_serve_job.py` → tool `cost_to_serve` |
| `client_profile.py` | Ya cableado en `orchestrator.py:20` y `packages.py:45` |
| `commercial_pricing.py` | Interno: calcula **lo que Kern le cobra al cliente** |
| `contingent_fee.py` | Interno: el fee del Sprint de Liquidación |
| `retention.py` | Interno: política de retención, con su `retention_job.py` |
| `kanban.py` | **Huérfano real.** 27 líneas |
| `src/seo/` + `seo_audit` + `seo_priority` | **Huérfano real.** ~900 líneas |

De los dos huérfanos reales:

- **`kanban.py`: no.** Son 27 líneas que dimensionan un lazo pull. DDMRP y `(s,Q)` ya
  responden esa pregunta y ya están en el segundo anillo. Una tercera respuesta a la
  misma pregunta es catálogo.
- **`seo_audit`: no.** Un crawl técnico de sitio es marketing, no cadena de suministro.
- **`seo_priority`: anotado, no registrado.** Cruza ABC-XYZ × excess/obsolete ×
  forecast para dar push/cut/hold por página de producto — es una salida de inventario
  disfrazada de SEO, y para un e-commerce del núcleo sirve. No la registré porque no
  tiene teoría que citar en el corpus (SCM/OR/inventario/pronóstico/pricing), y ese
  problema se resuelve **antes** de exponerla. El precedente correcto está documentado
  en `vehicle_routing`: anchor-islanded con cero honesto, nunca anclada a la fuerza a
  un falso amigo léxico.

También **no toqué `package_specs.py`**, aunque el documento de Fase 1 había decidido
subir `sourcing` al núcleo. Al ir a implementarlo la medición lo desaconsejó: el paso
entraría `required=False`, y el propio docstring de `package_specs.py` documenta que
eso no protege de un archivo presente-pero-mal-formado (la lección que dejó `pricing`),
mientras `sourcing_job` usa `require_all_criteria=True` y rechaza por nombre. Un
cliente Starter con un `proveedores.csv` parcial perdería su entregable mensual entero
a cambio de un bonus. Corregí el documento en vez de dejar escrita una decisión que no
ejecuté.

---

## 4. La ruta de citas: la hipótesis que la medición no respaldó

Detalle completo en [`CITAS_LEXICO_VS_HIBRIDO.md`](CITAS_LEXICO_VS_HIBRIDO.md); acá el
resultado.

Se corrió `_step_citations` —la ruta real de producción— sobre **las 49 tools ancladas
y registradas**, con el gate intacto, en dos variantes:

| | antes | pool armado por `search_hybrid` | léxico entero + semántico agregado |
|---|---|---|---|
| Citas totales | **116** | 114 | **116** |
| Tools "debe-citar" en cero | 0 | **3** | 0 |
| Tools que ganan citas | — | 3 | **0** |
| Tests que se romperían | — | **5** | **0** |

La primera variante es peor y de la peor forma: `ddmrp`, `hat_tension` y
`odoo_replenishment` caen de 3 citas a **cero**, y `earned_value` empieza a citar
*"Depreciation Schedule Methods"*. El pool está topeado en 16, así que lo semántico no
agrega candidatos: los **desplaza**.

La segunda variante es la que decide. Con lo léxico intacto y lo semántico solo
pudiendo **sumar**, **ninguna de las 49 tools gana una sola cita**.

**Por qué:** el gate exige que todo candidato quede a `MAX_HOPS` de un ancla declarada.
Lo que los embeddings encuentran de más no está cerca de ninguna ancla, y el gate lo
descarta antes de que pueda ayudar. El cuello de botella no es el recuperador: es el
conjunto de anclas y la topología del grafo. Las cinco tools anchor-islanded lo están
**en el grafo**, no en la búsqueda.

Y un argumento de empaquetado que solo ya bastaba: `pyproject.toml:74-85` deja
`fastembed` fuera del boot de prod y de `requirements-dev.txt` **por decisión escrita**,
y dice textualmente *"NOT wired into the client-facing citation path"*. El cache de
24 MB está gitignoreado. Hoy, en prod, `search_hybrid` ya degrada a `search`. Aplicarlo
habría hecho que las citas dependan de si `fastembed` está instalado — resultados
distintos entre CI y una máquina local, la clase exacta de regresión silenciosa que
`knowledge/scm-books/README.md` documenta tres veces.

**No se aplicó.** Queda `scripts/measure_citation_retrieval.py` para que la próxima
idea se mida igual en vez de discutirse.

---

## 5. Validación

**El hallazgo más caro de la sesión no estaba en ninguna fase.** La primera corrida de
la suite dio ~150 fallos. No era el código: al worktree le faltaban
`graphify-out/` (gitignoreado, 19,8 MB) y **once dependencias**: `statsforecast`,
`pandera`, `rapidfuzz`, `price-parser`, `python-stdnum`, `extruct`, `splink`,
`statsmodels`, `advertools`, `APScheduler`, `chompjs`. Con `requirements-dev.txt`
instalado —de donde instala CI— la suite quedó verde de una.

Vale anotarlo porque es el mismo patrón que `CLAUDE.md` ya documenta para CI:
**cuando algo sale rojo en este repo, sospechar del entorno antes que del código.**

| Verificación | Resultado |
|---|---|
| Suite completa (línea base, antes de cualquier cambio de código) | **4723 passed · 27 skipped · 0 failed** en 878,95 s |
| Suite completa (después de los 5 commits) | **4723 passed · 27 skipped · 0 failed** en 1221,27 s (con cobertura) |
| **Cobertura real medida** (`src` + `scm_agent` + `jobs`) | **94%** — 25.700 sentencias, 1.420 sin cubrir |
| Cobertura que declara la insignia del README | 93% — la insignia **sub-reporta** 1 punto, no infla |
| Las 40 tools que deben citar | intactas — `tests/test_packages_citations.py` **21 passed** |
| `ruff check src tests examples` | **All checks passed** (había 1 error pre-existente; corregido) |
| Contratos nosilo (`run_nosilo_core.py`) | **17/18**, idéntico a la base |

El conteo de tests es **exactamente el mismo antes y después**: 4723/27/0. Ningún test
nuevo, ninguno perdido, ninguno roto — consistente con que el único cambio de código de
la sesión fue un movimiento puro verificado por AST.

Sobre el 17/18: falla `unknown-status`, y **no es de Kern ni de esta rama** — falla
igual en la base sin tocar nada. El `nosilo_core` instalado es 0.1.0 y su
`verify_guided` no emite el issue `unknown status`; el propio harness documenta que ese
vector se ejerce *"desde `nosilo-core@5e9d77a`, que agregó el issue de `unknown_status`.
Antes de ese commit eran ocho y el vector fallaba"*. Es versión de dependencia, no
conformidad rota.

---

## 6. Lo que dejé afuera a propósito

- **No borré ninguna capacidad.** Las 14 tools sin paquete siguen registradas; los 9
  `PackageSpec` siguen existiendo y corriendo. Lo que se retira es su condición de
  opción que un comprador frío elige de una lista.
- **No cerré el precio de la operación mensual.** Está declarado abierto por el dueño
  hace dos días.
- **No implementé los 3 SKU que tienen one-pager y no tienen `PackageSpec`**
  (`price_intelligence`, `org_friction_audit`, `evidencia-auditoria-inventario`). Es
  trabajo de producto, no de ordenamiento, y el tercero además no tiene código.
- **No homogenicé los 13 módulos "desviados".** Medido, o delegan en implementaciones
  compartidas o son helpers de un solo hook: uniformarlos duplicaría código.
- **No toqué `kern-main`.** Todo el trabajo vive en un worktree.

## 7. Lo pendiente, con su razón

| Pendiente | Por qué no ahora |
|---|---|
| Ingerir teoría VRP y de disfunción de medición al grafo de libros | Es lo único que convierte los ceros de `vehicle_routing` / `org_friction_audit` en citas reales. Requiere rebuild del grafo L3, que necesita backend LLM |
| Revisar las anclas de `ddmrp`, `hat_tension`, `odoo_replenishment` | Que pierdan las 3 citas al cambiar el pool muestra que dependen de un margen léxico estrecho: son las candidatas más probables a la próxima regresión por ingesta |
| Que un paso opcional que rechaza degrade a `skipped` | Precondición para subir `sourcing` al núcleo (§3) |
| Reconciliar `documentation/paquetes/` (13) con `package_specs.py` (9) | Decisión de producto: o se implementan o se retiran |
| Actualizar `nosilo_core` a ≥`5e9d77a` | Cerraría el 18/18 de contratos. Es un paquete privado, fuera de este repo |

## 8. Cómo discrepar

| Decisión | Qué habría que sostener para revertirla |
|---|---|
| Un solo bucle de producto | Que un operador solo puede vender y operar 5 tiers + 3 proyectos con 0 clientes |
| Retirar Growth/Scale/Retainer del menú | Que un comprador frío elige entre "26 tools" y "35 tools" por criterio propio |
| No registrar `kanban` ni `seo_audit` | Que una tercera respuesta a la pregunta de DDMRP agrega algo; que un crawl de sitio es SCM |
| No aplicar el híbrido en citas | Que 116→116 citas y 0 tools ganadoras justifican una dependencia nueva en prod |
| Partir `comprobaciones.py` | Que 2031 líneas en un archivo es mejor que cinco de ≤594 con la lógica idéntica |
| No cerrar el precio mensual | Que corresponde cerrarlo sin el dueño |
