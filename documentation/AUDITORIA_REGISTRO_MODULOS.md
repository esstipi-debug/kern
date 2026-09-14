# Auditoría: ¿qué módulos están realmente sin registrar?

> Medido el 2026-08-09 sobre el commit `e608bbd` (worktree limpio).
> Motivo: un encargo para registrar 8 módulos "construidos, probados y no
> invocables por el agente". Tres de los ocho ya son invocables; dos no son
> capacidades SCM. Este documento deja la medición anotada para que la próxima
> sesión no la re-derive.

## El error de método que esta auditoría corrige

La premisa del encargo era: `grep -c <módulo> scm_agent/tools.py` devuelve 0
para los ocho, luego los ocho están sin registrar.

**Ese grep mide la capa equivocada.** La arquitectura es:

```
scm_agent/tools.py  ->  jobs/<x>_job.py  ->  src/<x>.py
```

`tools.py` importa de `jobs/`, no de `src/`. Solo **3** módulos de `src/` se
nombran en `tools.py`, y los tres vía el bloque `# isort: skip` del final de
los imports (`OdooError`, `ServiceCostRates`, `CostModel`). Para todo lo demás,
**cero referencias en `tools.py` es el estado normal de un módulo ya
registrado**.

La medición correcta es sobre `jobs/` y `scm_agent/`, y ahí el resultado cambia.

## Tabla de alcanzabilidad

| Módulo | Alcanzado desde | Veredicto |
|---|---|---|
| `src/working_capital.py` | `jobs/cost_to_serve_job.py:27`, `jobs/cost_to_serve_deliverable.py:16` | **Ya invocable** — tool `cost_to_serve` |
| `src/supplier_scorecard.py` | `jobs/sourcing_job.py:35` | **Ya invocable** — tool `sourcing` |
| `src/client_profile.py` | `scm_agent/orchestrator.py:20`, `scm_agent/packages.py:45` | **Ya cableado** — infraestructura, no es un tool por diseño |
| `src/retention.py` | `jobs/retention_job.py:40` (el job existe; `tools.py` no lo importa) | Sin registrar |
| `src/seo/` | `jobs/seo_audit.py`, `jobs/seo_priority.py` (`tools.py` no los importa) | Sin registrar |
| `src/kanban.py` | nada (testeado en `tests/test_ops_methods.py`) | Sin registrar, y sin job |
| `src/commercial_pricing.py` | nada | Sin registrar |
| `src/contingent_fee.py` | solo prosa en `scm_agent/package_specs.py:43` | Sin registrar |

### Los tres que ya son alcanzables

- **`working_capital`** — el tool `cost_to_serve` se titula literalmente
  *"Cost-to-Serve & Working Capital"*, acepta `dio`/`dso`/`dpo` y
  `dio_days`/`dso_days`/`dpo_days` como params, y embarca `working_cap` y
  `cash_release` dentro del deck del cliente. Registrarlo aparte duplicaría la
  capacidad y competiría por el mismo espacio de intents.
- **`supplier_scorecard`** — `jobs/sourcing_job.py` importa `score_supplier`,
  `rankable`, `lead_times_for_skus` y compañía, y escribe
  `supplier_scorecards.csv` como entregable operativo.
- **`client_profile`** — el orquestador ya lo consulta para fusionar los
  parámetros durables por cliente antes de `prepare` y `run` (documentado en
  `CLAUDE.md`, sección *Client parameter profiles*). Es infraestructura del
  orquestador; convertirlo en tool sería una regresión de diseño.

## Dos módulos cuya etiqueta no describe el código

### `src/retention.py` no es churn

Es un **motor de política de retención de datos**: expiración por
`retention_days`, borrado, estado `blocked_bulk` cuando una regla mal tipeada
haría desaparecer una fracción excesiva del store, configuración en
`config/retention.yaml`. Su docstring es explícito: *"lo interesante de la
retención no es el borrado, es negarse a hacerlo cuando los números pintan
mal"*.

El grafo de libros **sí** tiene un nodo `customer_retention`. Anclar ahí este
módulo sería exactamente el falso amigo léxico del precedente
`vehicle_routing` ("Logistics as the Vehicle for Change" / "Route Sheet"): la
palabra coincide, el tema no.

Consideración aparte, y más importante que la de citas: es una **ruta de
borrado irreversible**. Hoy vive fuera del registry, y el propio módulo enruta
a un humano vía `blocked_bulk`. Hacerlo enrutable desde un brief en lenguaje
natural es una decisión de seguridad, no de cobertura de capacidades.

### `commercial_pricing` y `contingent_fee` no son capacidades SCM

Calculan **lo que Kern le cobra a sus propios clientes**: bandas GMV
(Starter $1-3M / Growth $3-8M / Scale $8M+ / Retainer) y honorarios variables
del 10-20% del cash recuperado, con piso de USD 1.500.

`commercial_pricing.py` lo advierte en su primer párrafo: *"Do not confuse it
with `src/pricing.py`, `jobs/repricing.py`, or `src/pricing_intel/` — those
price the CLIENT's OWN product catalog and are unrelated."*

El corpus tiene `value_based_pricing` y `price_bundling`, pero son teoría sobre
el precio del producto del cliente. Usarlos para anclar el modelo comercial de
Kern es la misma clase de falso amigo.

## Medición de teoría en el grafo

Búsqueda directa de ids de nodo sobre `knowledge/scm-books/graph.json`:

| Concepto candidato | ¿Existe? |
|---|---|
| `kanban` | sí |
| `takt_time` | sí |
| `vollmann_jit` | sí |
| `customer_retention` | sí, pero es falso amigo (ver arriba) |
| `value_based_pricing` | sí, pero es falso amigo (ver arriba) |
| `price_bundling` | sí, pero es falso amigo (ver arriba) |
| `search_engine_optimization` | **no** |
| `churn` | **no** |
| `customer_lifetime_value` | **no** |
| `contingency_fee` | **no** |

La existencia del nodo es condición necesaria y **no** suficiente: falta medir
cierre a 2 saltos, adyacencia a hubs de libro, y un replay real de
`_step_citations`. Esa segunda mitad requiere ejecutar Python (ver *Bloqueo*).

## Lo que queda genuinamente por registrar

### `seo` — la única brecha grande y real

3.886 líneas entre `src/seo/` (crawler con gate de robots.txt, `geo_probe`,
generación de schema, feeds, `llms.txt`, PDP writer) y dos jobs. Todo con
tests: `test_seo_audit_job.py`, `test_seo_crawl_audit.py`, `test_seo_feeds.py`,
`test_seo_geo_probe.py`, `test_seo_llms_txt.py`, `test_seo_pdp_writer.py`,
`test_seo_priority.py`, `test_seo_schema_gen.py`.

`jobs/seo_audit.py` ya expone exactamente la forma que pide `Tool`:

```
prepare(seed_url, params) -> dict          # jobs/seo_audit.py:450
run(payload) -> SeoAuditReport             # :498
verify(report) -> list[str]                # :534
write_operational(report, out_dir)         # :573
```

Sin teoría SEO en el corpus (27 fuentes de SCM/OR/inventario/pronóstico/
pricing). La salida correcta es la vía honesta ya establecida en el repo, y hay
**dos formas distintas** de honestidad, no una:

- `vehicle_routing`: anclado en `TOOL_CONCEPTS`, pero con cero citas, listado en
  `_ISLANDED_TOOLS`.
- `intake_scan`: **sin** entrada en `TOOL_CONCEPTS`, listado en
  `_NO_CONCEPT_MAP_BY_DESIGN` y en `_CORRECTLY_ZERO_TOOLS`.

Para SEO corresponde la segunda (no hay ningún ancla defendible que exista).

**Registrado** como tool #51 (`scm_agent/tools.py::seo_audit_tool`). El registro
arrastró más blast radius del esperado, todo detectado por la suite completa:
dos conteos fijados en 50 (`tests/test_price_watch_tool.py::test_registry_tool_
count_matches_the_pinned_list` y las tres aserciones de
`tests/test_how_it_works_data.py`), la lista curada de
`webapp/how_it_works_data.py`, y el `_BLOCKED` de
`tests/test_pricing_intel_boot_safety.py`, al que hubo que sumarle
`advertools`/`scrapy` porque el job ahora cuelga del boot chain de `webapp.app`.

`seo_audit` estrena su propia área de dominio (`Search & digital presence`) en vez
de sentarse en `Demand & classification`: un crawl no es un método de planificación
de demanda, y meterlo ahí sobredeclararía lo que la taxonomía SCM realmente cubre.
En SCOR queda en el bucket ya existente `Outside SCOR scope`, junto a
`leadership_chain` — las dos capacidades que Kern vende y que deliberadamente no
son operaciones de cadena de suministro.

### `seo_priority` — exclusión de diseño ya documentada, no un olvido

`jobs/seo_priority.py` **no es SEO**: lee historial de demanda + snapshot de stock,
corre ABC-XYZ, E&O y forecast, y asigna push/cut/hold por SKU. Y su propio docstring
(líneas 41-48) declara: *"Not registered as an `scm_agent` Tool"*, porque necesita
dos formas de archivo y no encaja en el contrato de un solo `data_path` del
registry — el precedente `jobs/integrated_plan.py`. No se registró.

### `kanban` — teoría sólida, pero no es "registrar"

Los tres nodos existen y `kanban.py` ya está bridgeado a Jacobs & Chase según
`knowledge/scm-books/README.md`. Pero el módulo son 36 líneas con dos funciones
puras (`num_kanban_cards`, `takt_time`) y **no tiene job**. Registrarlo implica
construir `jobs/kanban_job.py` entero — `prepare` que lea su propio CSV, `run`,
`verify`, deliverable y deck. Eso es *crear* una capacidad, no registrar una
existente.

## Restricción que impone el gate

`tests/test_citation_gate.py::test_every_registered_tool_has_a_concept_map`
exige **igualdad exacta**:

```python
assert registered - _NO_CONCEPT_MAP_BY_DESIGN == set(TOOL_CONCEPTS)
```

Todo tool nuevo necesita, sin excepción, o una entrada en `TOOL_CONCEPTS` o una
en `_NO_CONCEPT_MAP_BY_DESIGN`. Y
`tests/test_packages_citations.py::test_no_anchored_tool_regressed_to_zero`
fija las 45 tools ancladas contra regresión a cero.

## Resumen de lo ejecutado

| Módulo | Acción | Razón |
|---|---|---|
| `src/seo/` + `jobs/seo_audit.py` | **Registrado** (tool #51) | Única brecha grande y real; ya tenía la forma `prepare`/`run`/`verify`/`write_operational` |
| `jobs/seo_priority.py` | No registrado | Su docstring declara la no-registración; necesita dos CSVs |
| `src/working_capital.py` | No registrado | Ya alcanzable vía `cost_to_serve` |
| `src/supplier_scorecard.py` | No registrado | Ya alcanzable vía `sourcing` |
| `src/client_profile.py` | No registrado | Ya cableado en el orquestador; es infraestructura |
| `src/retention.py` | No registrado | Es retención **de datos**, no churn; y es una ruta de borrado irreversible — enrutarla por lenguaje natural es una decisión de seguridad que no se toma de oficio |
| `src/kanban.py` | No registrado | Sin job; registrarlo sería construir una capacidad nueva sobre dos funciones puras |
| `src/commercial_pricing.py` | No registrado | Es el modelo comercial de Kern, no una capacidad SCM |
| `src/contingent_fee.py` | No registrado | Ídem |

Neto: **1 de 8** era una brecha genuina y registrable. De los 7 restantes, 3 ya
eran alcanzables, 2 no son capacidades SCM, 1 no tiene job y 1 es una exclusión
de diseño documentada.

## Nota de método sobre la verificación

Este documento se escribió en dos pasadas. En la primera **no se pudo ejecutar
`pytest`** (toda invocación de `python` por Bash estaba denegada), y por eso se
entregó sin cambios de código: registrar contra el citation gate sin correr la
suite es exactamente el modo de falla que `knowledge/scm-books/README.md`
documenta tras **tres** incidentes, en el último de los cuales ni siquiera la
suite completa bastó (hizo falta una revisión adversarial que encontró
`digital_twin`, `launch_readiness` y `vehicle_routing` sin cobertura).

En la segunda pasada la ejecución quedó disponible y el registro se hizo con TDD
real (test primero, verlo fallar con `KeyError` del registry, luego implementar)
y la suite completa corrida al final, no solo el subconjunto de citas — que es
justamente lo que habría escondido los dos conteos fijados en 50.

Resultado final verificado: **4738 passed, 27 skipped, 0 failed** sobre la suite
completa, y `ruff check src tests examples` limpio salvo un error preexistente en
`tests/test_connector_contract.py` (I001, archivo no tocado por este trabajo).

Tres trampas de medición que costaron tiempo en esta sesión, anotadas para que la
próxima no las repita:

1. **No edites archivos mientras la suite corre.** El primer baseline mostró ~22
   fallos enteramente auto-infligidos: pytest importa los módulos en la colección,
   y los tests que leen `scm_agent/tools.py` desde disco vieron el archivo ya
   editado a mitad de run.
2. **Los procesos huérfanos falsean la suite.** Un run posterior dio 44 fallos —
   incluidos 22 "async def functions are not natively supported" en
   `test_mcp_server.py` que desaparecen al correr ese archivo aislado. La causa
   era contención de CPU de runs previos que habían expirado del lado del cliente
   pero seguían vivos. Corriendo limpio, la suite entera tarda ~6 minutos, no 45.
   Si ves fallos que no reproducen aislados, revisá `Get-Process python` antes de
   creerles.
3. **Un `git worktree` recién creado NO es un baseline válido.** Medir el commit
   padre en un worktree limpio dio 16 fallos (`test_demo`, `test_webapp` de
   upload, `test_price_monitor_job`, `test_simworld_runners`, `test_pricing_intel_*`)
   que son artefactos de la ausencia de `data/` — gitignored, no regenerable por
   checkout. Para comparar ramas hay que copiar los directorios gitignored que los
   tests consumen, o comparar en el mismo árbol.
