# `service_scan` — tool #51 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development o
> superpowers:executing-plans. Los pasos usan checkbox (`- [ ]`).

**Goal:** Que "medime el servicio de mi cuenta de Unleashed" sea pedible en lenguaje
natural. Es lo unico que resta del limite que declaro el PR #308.

**Architecture:** `jobs/service_scan_job.py` sigue la receta del repo (`prepare` -> `run` ->
`verify` -> deck) y no reimplementa nada: resuelve un `InventorySource` -- Unleashed, Cin7
Core o el simulador offline -- y se lo pasa a `src.connectors.service_scan.scan_service`,
que ya existe y ya esta testeado. Lo nuevo es el envoltorio de agente: resolucion de
credenciales, QA, y un `Deliverable` cuyo mapa de fuentes lleva la declaracion de cobertura
del backend.

**Tech Stack:** Python 3.11, pytest. Sin dependencias nuevas: los adaptadores ya existen.

**Spec:** `docs/handoffs/HANDOFF-servicio-realizado-2026-08-31.md` seccion 4, D1-(B).

## Global Constraints

- Rama: `feat/tool-service-scan`, cortada de `main` @ `33a3f26`.
- **Worktree compartido.** `git status` justo antes de cada `git add`. `.mcp.json`,
  `.dockerignore`, `.github/workflows/docker.yml` y `data/bateria/` son de otra sesion.
- Hay ADEMAS una sesion corriendo sobre `src/discovery_brief.py` (anclaje de citas). Este
  plan no toca ese archivo; si aparece modificado, no es de aca.
- Interprete: `.venv/Scripts/python.exe` del worktree. `ruff check .` (repo entero).
- Suite completa ~30 min: lanzarla en background.
- **Credenciales**: nunca a un log, nunca al resumen, nunca al entregable. El patron del
  repo es params -> env -> stand-in offline (`jobs/odoo_job.py::_make_rpc`).
- Texto de cara al cliente en **ingles** (el deck es ingles por defecto; hay i18n aparte).
- Al tocar cualquier archivo citado por `src/discovery_brief.py`, correr
  `tests/test_discovery_brief.py`: sus 19 citas estan ancladas a numero de linea.

## Los 9 lugares que toda tool nueva toca

Descubiertos rastreando `intake_scan`, la tool #50. Los guards del repo obligan a casi
todos, asi que la estrategia es dejarlos romper y seguirlos.

| # | Archivo | Que exige |
|---|---|---|
| 1 | `jobs/service_scan_job.py` | el job (crear) |
| 2 | `scm_agent/tools.py` | `Tool` + `register()` |
| 3 | `scm_agent/citation_gate.py` | `TOOL_CONCEPTS` — anclas reales del grafo L3 |
| 4 | `webapp/how_it_works_data.py` | `ToolInfo`; su guard afirma `len(TOOLS) == 50` |
| 5 | `tests/test_how_it_works_data.py` | el conteo 50 -> 51 |
| 6 | `webapp/mcp_tool_specs.py` | la superficie MCP |
| 7 | `tests/test_tool_data_contract.py` | `UNDECLARED_TOOLS` o `required_columns` |
| 8 | `CLAUDE.md` | dice "50 agent-routable tools" |
| 9 | `scm_agent/README.md` | la referencia del agente |

**Anclas verificadas contra el grafo real** (`KnowledgeBase.node_exists`), no inventadas:
`perfect_order` SI · `fill_rate` SI · `customer_service` SI. (`otif`, `on_time_in_full`,
`order_fulfillment` NO existen — no usarlas.)

---

### Task 1: el job — resolucion de fuente, medicion y QA

**Files:** Create `jobs/service_scan_job.py`, `tests/test_service_scan_job.py`

**Interfaces:**
- Consumes: `src.connectors.service_scan.scan_service`, `src.connectors.{unleashed,cin7,simulator}`.
- Produces: `ServiceScanReport(source_name, records, portfolio, notes, coverage, n_orders,
  n_lines, since)`; `prepare(data_path, params) -> dict`; `run(payload, **kw) ->
  ServiceScanReport`; `verify(report) -> QaResult`.

- [ ] **Step 1: test que falla.** Comportamientos:
  1. `prepare` con un `source` inyectado lo usa (el camino de los tests).
  2. Con `UNLEASHED_API_ID`/`UNLEASHED_API_KEY` en el entorno arma un `UnleashedConnector`.
  3. Con `CIN7_ACCOUNT_ID`/`CIN7_APPLICATION_KEY` arma un `Cin7Connector`.
  4. Sin credenciales cae al simulador **y lo dice en `source_name`** — un stand-in que no
     se anuncia es una demo que se lee como una cuenta real.
  5. **Ninguna credencial aparece en `source_name` ni en las notas.** El test las busca
     por su valor literal en todo el reporte serializado.
  6. `run` produce records con `provenance="observed"` y el `portfolio` agregado.
  7. Las notas de cobertura del adaptador viajan al reporte.
  8. `verify` **falla** si el reporte dice haber medido y no tiene ni una linea puntuada:
     un fill rate sobre cero evidencia es el modo de falla que todo este hilo persigue.
  9. `verify` **pasa** con cero ordenes: una cuenta recien conectada no es un error.
- [ ] **Step 2-5:** verlo fallar, implementar, verde + `ruff check .`, commit con pathspec.

---

### Task 2: el deck — el servicio y lo que el backend NO puede medir

**Files:** Create `jobs/service_scan_deliverable.py`, extend `tests/test_service_scan_job.py`

- [ ] **Step 1: test que falla.** Comportamientos:
  1. El `summary` dice el fill rate y el OTIF de cartera, o "not measured" — nunca 0%.
  2. Los `findings` son los SKU con peor servicio, valorizados.
  3. **La declaracion de cobertura va en `data_sources`, no en `findings`** — mismo
     criterio que #318: un finding es un problema del negocio del cliente y esto es un
     limite de la medicion.
  4. Con un backend que declara `delivered_date` como DESPACHO, el deck lo dice con esa
     palabra. Es el hallazgo de 6,2 puntos, y sin el el cliente lee "OTIF" como llegada.
  5. `write_all` deja el caveat en el markdown **y** en la hoja del Excel.
- [ ] **Step 2-5:** fallar, implementar, verde, commit.

---

### Task 3: registrar la tool y seguir a los guards

**Files:** Modify `scm_agent/tools.py`, `scm_agent/citation_gate.py`,
`webapp/how_it_works_data.py`, `tests/test_how_it_works_data.py`,
`webapp/mcp_tool_specs.py`, `tests/test_tool_data_contract.py`

- [ ] **Step 1:** registrar la tool y correr la suite de guards. Se espera que rompan
  varios: `test_how_it_works_data` (50 != 51), `test_citation_gate`
  (`service_scan` sin concept map), `test_tool_data_contract`. **Anotar cual rompio cada
  uno antes de arreglarlo** — es el inventario real de los 9 lugares, y la unica forma de
  saber que no quedo ninguno.
- [ ] **Step 2:** arreglarlos uno por uno. `intent_keywords` distintivos y multi-palabra:
  "service scan", "fill rate", "otif", "servicio al cliente", "cuanto entregue".
- [ ] **Step 3:** verde, `ruff check .`, commit.

---

### Task 4: el camino del agente, corrido de verdad

**Files:** Create `tests/test_agent_service_scan.py`; modify `CLAUDE.md`, `scm_agent/README.md`

- [ ] **Step 1: test que falla.** Un `JobRequest` con un brief en lenguaje natural rutea a
  `service_scan` via `intent.classify`, y el `prepare -> run -> qa` completo produce un
  reporte con servicio observado. **Ese es el objetivo entero del PR**: sin este test,
  "pedible en lenguaje natural" es una afirmacion sin verificar.
- [ ] **Step 2:** implementar lo que falte (probablemente solo keywords).
- [ ] **Step 3:** actualizar `CLAUDE.md` (50 -> 51 tools, y una linea de que hace) y
  `scm_agent/README.md`, en el MISMO PR que cambia el hecho.
- [ ] **Step 4:** suite completa en background; `ruff check .`; correr la tool de punta a
  punta contra el simulador y pegar la salida.
- [ ] **Step 5:** commit.

---

## Self-review

**Cobertura del spec.** D1-(B) del handoff -> Tasks 1-4. "Pedible en lenguaje natural" ->
Task 4 Step 1, que es la unica asercion que lo prueba.

**Sin placeholders.** Cada task nombra sus comportamientos; el codigo se escribe en la
ejecucion, despues de ver fallar el test.

**Riesgo declarado.** Es la pieza mas grande de este hilo: 9 archivos y dos modulos nuevos.
Los guards del repo cubren 6 de los 9 lugares, asi que el riesgo real no es olvidarse uno
sino que el citation gate rechace las anclas — ya verificadas contra el grafo, que es por
lo que ese chequeo va antes y no despues.

**Lo que este plan NO hace:** no toca el refactor pendiente del registry. Agrega una tool
con la receta actual, que es lo que el repo documenta hoy. Si el refactor cambia la receta,
esta tool se migra con las otras 50.
