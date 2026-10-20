# Guardarrailes de Kern

Mapa unico de las reglas que el codigo hace cumplir. Fuentes: `CLAUDE.md`,
`.specify/memory/constitution.md` (que prevalece si hay desacuerdo) y el
historial de git. Cada guardarrail apunta al modulo que lo implementa y al test
que lo vigila.

> Mantenimiento: si un hecho de aqui cambia, se corrige en el mismo PR que lo
> cambio (misma regla que `CLAUDE.md`).

## Como se crearon (el patron)

Casi todos nacieron de un defecto real, no de una idea de diseno. La
constitucion lo dice de si misma: cada ley esta escrita porque romperla ya costo
un bug en produccion, una caida o una respuesta equivocada en silencio.

El ciclo se repite:

1. Se encuentra un defecto, casi siempre corriendo la capacidad sobre datos
   reales o con una auditoria (por AST, por barrido, por marcador simulado).
2. Se reproduce de punta a punta en un test.
3. Se arregla en el productor.
4. El test que lo reproduce queda como guardarrail permanente, y la regla se
   escribe en `CLAUDE.md` o en la constitucion.

Hitos en git: spec-kit, constitucion y contratos de datos el 2026-09-14 (#316);
neteo del en-transito, lotes factibles, flete como quinto sombrero y captura
bajo disrupcion el 2026-10-09 (#369). Trabajo hecho en sesiones con Claude,
siguiendo rama -> PR en borrador -> CI verde -> squash-merge a `main`.

## 1. Proteccion del humano: los cuatro desenlaces

| Regla | Donde vive |
|---|---|
| Todo resultado consecuente es un `GuidedOutcome`: `EXECUTED`, `OPTIONS`, `HANDOFF` o `ESCALATED`. Los tres ultimos requieren un humano. | `nosilo_core.guided`, `documentation/operator/` |
| `cross_impact`: un area sin umbral es `no_evaluable` y el veredicto global es `indeterminado`, nunca "nadie objeta". | `src/hats.py`, `jobs/cross_impact_job.py` |
| Los sombreros son cinco; el quinto (transporte) es condicional. Sin peso por unidad y distancia queda fuera y la exclusion se NOMBRA. Una corrida sin flete no puede devolver "ejecutar". | `src/hats.py` (`HAT_KEYS`, `evaluable_hats`), `tests/test_hats_valuation.py` |

## 2. Escritura segura en sistemas del cliente

| Regla | Donde vive |
|---|---|
| Nunca mutar un sistema de registro a ciegas: `Changeset` dry-run, riesgo `read`/`reversible`/`irreversible`, `Approval` con TTL de 900 s, aplicacion idempotente, `rollback()`. Lo irreversible exige aprobacion explicita. | `nosilo_core.writeback` |
| Conectores reales (Odoo, Excel) respetan esa superficie. Excel: chequeo de deriva, escritura atomica, backup por commit, rollback celda a celda, macros preservadas y nunca ejecutadas. | `src/connectors/odoo.py`, `src/connectors/excel.py` |
| La clave de idempotencia debe ser DISTINTA para una operacion distinta; una constante descarta la segunda operacion en silencio. | `derive_idempotency_key`, `tests/test_claves_de_idempotencia.py` |
| El price watch es 100 % solo lectura (nunca importa `writeback`), el auto-onboarding es solo robots.txt y siempre `limited` (nivel maximo `L1`); subir de nivel es un `GuidedOutcome` pendiente de aprobacion. | `jobs/price_watch.py`, `tests/test_price_watch_e2e.py` |

## 3. Calidad del entregable

| Regla | Donde vive |
|---|---|
| Si el QA falla, no hay entregable. Se hace cumplir en un solo lugar: el orquestador. Estados: `ok`, `needs_clarification`, `needs_data`, `qa_failed`, `error`. | `scm_agent/` (orquestador), `jobs/qa.py`, constitucion II |
| Degradar es ruidoso o es un bug: `NaN`, cero o un default silencioso en lugar de una negativa real es un defecto tan grave como un crash. | constitucion III, `tests/test_nan_swallower_guards.py` |
| Un entregable sin citas verificadas no sale (ver seccion 8, relacion con el grafo). | `scm_agent/citation_gate.py`, `tests/test_citation_gate.py` |
| Cada tool de datos declara `required_columns`/`optional_columns`, o figura en `UNDECLARED_TOOLS` con razon escrita; esa lista solo puede achicarse. Sub-declarar da falsos verdes; sobre-declarar, falsos rojos. | `tests/test_tool_data_contract.py`, `src/intake_scan.py`, constitucion IV |

## 4. Honestidad de los datos: la ausencia se declara

Principio comun: **un dato ausente es `None`, nunca 0**, y la superficie que lo
muestra dice que falta.

| Regla | Donde vive |
|---|---|
| Una negativa del motor no sale vestida de numero: SKU sin politica -> campos en `None`, y el QA gatea ambas direcciones. `skipped_zero_demand` conserva sus ceros porque ahi el 0 es una medicion. | `jobs/inventory_optimization.py`, `jobs/qa.py::_sized_field_issues`, `tests/test_negativa_no_es_cero.py` |
| Cobertura provisional (2-5 periodos): existe, pero la FORMA de sus campos declara que no es una politica (sin buffer, `z` ni forecast). Dos listas distintas, `UNSIZED_POLICY_KINDS` y `NOT_FULLY_SIZED_POLICY_KINDS`. | `src/risk_period.py::provisional_cover` |
| Nadie compra dos veces lo que viene en camino: el neteo contra la posicion vive en un lugar y lo leen ocho emisores. Sin dato de en-transito, `on_order` es `None` y se dice que NO se pudo netear. | `src/ddmrp.py`, `src/bateria/barrido_neteo.py`, `tests/test_barrido_neteo.py` |
| Terminos del proveedor (MOQ, multiplo): cuatro consumidores leen la MISMA regla de lote factible. | `src/constraints.py::cheapest_feasible_lot` |
| Tope por fecha de fin: puede ENCOGER una politica, nunca anularla; tres veredictos se niegan a topear y se nombran. No toca el buffer. | `src/horizon.py::cap_policy`, `jobs/qa.py::_horizon_issues` |
| El Tower sensa con identidad de cliente: sin `state_client_id` no se escribe nada y nunca en la particion compartida. | `jobs/state_bridge.py` |
| Perfiles de cliente: los valores solo llenan huecos, el override explicito gana, y `strict_params=True` bloquea en vez de usar el default generico. Etiquetas genericas nunca resuelven un perfil. | `src/client_profile.py` |
| Cobertura de servicio: todo conector con `orders()`/`arrivals()` exporta `SERVICE_COVERAGE` y un test la EJECUTA contra el adaptador. | `tests/test_connector_service_contract.py` |

## 5. Lo que el agente acepta no se tira en silencio

| Regla | Donde vive |
|---|---|
| Todo parametro que una llamada a `jobs.*` acepta debe reenviarse desde `scm_agent/tools.py` o estar declarado por escrito por que no. Auditoria por AST sobre 310 llamadas. | `tests/test_overrides_no_se_tiran.py` |
| Una fecha que cruza la frontera JSON se parsea en un solo lugar y una ilegible levanta error. | `tools._fecha_param` |

## 6. Fronteras de arquitectura

| Regla | Donde vive |
|---|---|
| `src/` y `packs/` dependen de `nosilo_core`, nunca al reves. | `tests/test_core_boundary.py`, `tests/test_pack_boundary.py` |
| Frontera del addon de Odoo. | `tests/test_odoo_addon_boundary.py` |
| Una capacidad nueva toca ocho lugares fijos (jobs, import, tres funciones del tool, `register`, `TOOL_CONCEPTS`, `TOOL_TITLES`, dos tests pinneados). | constitucion I, `tests/test_constitution_anchors.py` |
| Ningun modulo de la cadena de arranque importa a nivel de modulo una dependencia de un extra opcional; el job `prod-boot` de CI lo vigila. | `tests/test_pricing_intel_boot_safety.py`, `.github/workflows/tests.yml` |
| Nunca borrar `nosilo_core/`. | `CLAUDE.md` (Gotchas) |

## 7. Meta-guardarrailes: las reglas se vigilan a si mismas

| Regla | Donde vive |
|---|---|
| Las compuertas son tests, no scripts: la deriva de CI se detecta con la suite. | constitucion V, `tests/test_ci_lint_and_coverage_scope.py`, `tests/test_coverage_gate.py` |
| Los conteos se derivan de `build_default_registry()` o se fijan a un SHA. Contar un documento interno o un capitulo como obra curada es el sobreclaim que `tests/test_how_it_works_page.py` impide. | constitucion VI |
| La constitucion nombra simbolos, no lineas, y un test falla si un simbolo nombrado desaparece. | `tests/test_constitution_anchors.py` |
| Dependencias sin lock: ante un rojo inocente se sospecha primero de una dependencia; un cron nocturno destapa roturas upstream. | `.github/workflows/tests.yml` |
| Reglas de consola y datos: ASCII-only en prints; nunca leer ni exponer PII (analisis agregado); no pegar secretos. | `CLAUDE.md` (Gotchas) |

## 8. Relacion con el grafo (graphify)

Respuesta corta: **casi ninguno de los guardarrailes es el grafo, pero hay un
vinculo directo y dos indirectos.**

- **Directo: la compuerta de citas.** `scm_agent/citation_gate.py` usa el
  *books graph* (L3, `knowledge/scm-books/graph.json`). Cada cita debe
  resolverse a un nodo real del grafo y estar a no mas de `MAX_HOPS` de los
  conceptos ancla del tool (`TOOL_CONCEPTS`). Si no, se omite; si quedan menos
  de `MIN_CITATIONS`, se descarta todo el lote. Aqui el grafo SI es parte del
  guardarrail. Hoy esa compuerta solo la llama el runner de paquetes
  comerciales (`scm_agent/packages.py::_run_step`); la ruta de un solo tool usa
  `ground_citations` sin compuerta.
- **Indirecto 1: el code graph ayuda a no romper los guardarrailes.** Los tests
  quedan indexados a proposito, asi `graphify affected "<simbolo>"` responde
  que guardarrailes se rompen si cambias algo. Es una herramienta de
  navegacion, no una regla que se haga cumplir.
- **Indirecto 2: la memoria del grafo.** `knowledge/graph-memory/` y
  `documentation/GRAPH_LESSONS.md` guardan lecciones entre sesiones. Sirven de
  contexto para el agente, no bloquean nada.

Todo lo demas (writeback, QA, contratos de datos, neteo, fronteras de import)
es codigo y tests que corren sin ningun grafo. El code graph es AST-only, no
necesita llave, y el books graph solo hace falta para reconstruirlo, no para
que las compuertas funcionen.

## Que no verifique

Este mapa sale de la documentacion del repo y de los nombres de archivos de
test. No ejecute la suite ni lei cada test; si una fila nombra un test, la
existencia del archivo esta confirmada para los citados en el listado de
`tests/`, pero su contenido exacto no.
