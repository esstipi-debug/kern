# El servicio medido llega al entregable — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Darle a `src.connectors.service_frame()` su PRIMER llamador de produccion, para que
el fill rate y el OTIF saliente que un adaptador mide lleguen a un `JobReport` y de ahi al
entregable — junto con la declaracion de que campos el ERP del cliente NO pudo dar.

**Architecture:** Un modulo nuevo, `src/connectors/service_scan.py`, con `scan_service(source)`.
Lee cualquier `InventorySource` (prefiriendo `orders_with_shipments()` cuando el adaptador la
tiene), produce las tres cosas que el motor ya sabe consumir — el frame de `order_lines`, los
registros de `RealizedService`, y una serie de demanda — y arrastra el `SERVICE_COVERAGE` del
adaptador convertido en notas legibles. Despues, `examples/run_service_scan.py` corre la cadena
entera contra el job de inventario que YA existe. Sin tool nueva y sin tocar el registry.

**Tech Stack:** Python 3.11, pandas (ya en el camino via `service_frame`), pytest.

**Spec:** el PR #307 (`feat/unleashed-cobertura-servicio`), seccion "Limitacion conocida", y el
docstring de `src/connectors/__init__.py::service_frame` — que se describe a si mismo como
"the missing constructor" y no tiene llamadores.

## Global Constraints

- Rama: `feat/servicio-al-entregable`, apilada sobre `feat/unleashed-cobertura-servicio`
  (necesita `SERVICE_COVERAGE` y `unleashed.py`, que solo existen ahi).
- **Worktree compartido con otra sesion.** `git status` justo antes de cada `git add`.
  `.mcp.json` y `data/bateria/` NO son de este hilo. Nunca `git add -A`.
- Interprete: `.venv/Scripts/python.exe` del worktree.
- `ruff`: `line-length = 120`, `select = ["E","F","I"]`. Lint de CI: `ruff check .` (repo entero).
- Greps acotados a `supply-chain-optimization/`; un `grep -r` sobre `C:\Users\Gamer\Music\scm`
  supera los 120s y se va a background.
- Archivos largos: herramienta de escritura, no `cat <<EOF` (se trunca).
- **Prohibido inventar numeros.** Un campo que el backend no reporta viaja `None` hasta el
  entregable. La regla completa esta en el docstring de `OrderLine`.
- No tocar precios (`webapp/pricing_quote.py`, `webapp/billing.py`,
  `scm_agent/package_specs.py`) — decision D2 abierta, del usuario. Ni BWM (D4).
- **No registrar una tool nueva.** El registry tiene un refactor pendiente y sin empezar;
  meterle la capacidad #51 encima es deuda que este hilo no tiene por que pagar. La
  capacidad se prueba llamando al job directo, igual que hace `plan_replenishment`.

## Lo que ya existe y no se reconstruye

| Pieza | Estado |
|---|---|
| `src.connectors.service_frame(orders)` | existe, **cero llamadores** |
| `src.connectors.realized_service(orders)` | existe, usado solo en tests |
| `jobs.inventory_optimization.run(..., order_lines=)` | acepta el frame; **ningun llamador lo pasa** |
| `src.order_fulfillment.aggregate(records)` | existe |
| `unleashed.SERVICE_COVERAGE` y sus 4 hermanos | existen (PR #307) |

## File structure

| File | Responsibility |
|---|---|
| `src/connectors/service_scan.py` (crear, ~150 lineas) | `ServiceScan` + `scan_service()`: la lectura de servicio de cualquier conector, con sus notas de cobertura y la serie de demanda. |
| `tests/test_connector_service_scan.py` (crear) | Su suite: preferencia de lectura, notas, el frame, y la cadena entera hasta `JobReport`. |
| `examples/run_service_scan.py` (crear, ~70 lineas) | El CLI corrible: `--demo` offline, o Unleashed con credenciales de entorno. |

---

### Task 1: `scan_service` lee el conector y arrastra su declaracion

**Files:**
- Create: `src/connectors/service_scan.py`
- Test: `tests/test_connector_service_scan.py`

**Interfaces:**
- Consumes: `src.connectors.{service_frame, realized_service, ServiceCoverage}`,
  `src.order_fulfillment.aggregate`.
- Produces: `ServiceScan(source_name, records, portfolio, order_lines, demand, coverage,
  notes)` (frozen dataclass) y `scan_service(source, *, stockout_days=None) -> ServiceScan`.

- [ ] **Step 1: Escribir el test que falla**

Ver el archivo de test completo en la ejecucion; los comportamientos que fija son:

1. `scan_service` usa `orders_with_shipments()` cuando el adaptador la tiene (Unleashed
   mide fill rate), y `orders()` cuando no (el simulador).
2. Un campo que el adaptador declara no-reportable produce una NOTA con nombre y motivo;
   uno reportable no produce ruido.
3. Un adaptador sin `SERVICE_COVERAGE` produce una nota que lo dice, no silencio.
4. El `means` de la declaracion viaja a la nota — para Unleashed, la palabra "despacho".
5. `order_lines` sale con las seis columnas exactas que lee el job.
6. `portfolio` es el roll-up ponderado por evidencia, no por SKU.

- [ ] **Step 2: Correr y verlo fallar** — `ModuleNotFoundError: src.connectors.service_scan`

- [ ] **Step 3: Implementar `service_scan.py`** — minimo para pasar.

- [ ] **Step 4: Verde + `ruff check .`**

- [ ] **Step 5: Commit** con pathspec explicito.

---

### Task 2: la cadena entera llega a un `JobReport`

**Files:**
- Modify: `tests/test_connector_service_scan.py`

**Interfaces:**
- Consumes: Task 1 + `jobs.inventory_optimization.run`.
- Produces: nada nuevo. Es la prueba de que los eslabones encajan.

- [ ] **Step 1: Test que falla** — un adaptador Unleashed con despachos, pasado por
  `scan_service` y de ahi a `run_inventory(demand, order_lines=scan.order_lines)`, produce un
  `JobReport` cuyo `realized_service` de un SKU tiene `provenance == "observed"` y un
  `fill_rate` numerico. Sin `order_lines` el MISMO reporte da `not_measured` — las dos
  aserciones juntas, porque la segunda es la que prueba que la primera no es casualidad.

- [ ] **Step 2-4: Verde, lint, commit.**

---

### Task 3: el CLI corrible

**Files:**
- Create: `examples/run_service_scan.py`

- [ ] **Step 1:** `--demo` corre contra `simulator.demo_store()`; sin `--demo` arma un
  `UnleashedConnector` con `UNLEASHED_API_ID` / `UNLEASHED_API_KEY` del entorno y
  `live_http`. Imprime el servicio por SKU, el roll-up, y las notas de cobertura.
  ASCII-only en los prints (Windows cp1252).
- [ ] **Step 2:** correrlo de verdad con `--demo` y pegar la salida.
- [ ] **Step 3:** lint + commit.

---

## Self-review

**Cobertura del spec.** "service_frame sin llamadores" -> Task 1. "`_inventory_run` nunca pasa
order_lines" -> Task 2 lo prueba llamando al job directo; la ruta del ORQUESTADOR sigue sin
pasarlo, y eso queda declarado abajo como limite, no como olvido.

**Limite declarado.** Esto NO hace que el agente pueda pedir un scan de servicio en lenguaje
natural: `_inventory_run` en `scm_agent/tools.py` sigue sin reenviar `order_lines`, y darle un
productor exige o una tool nueva (registry con refactor pendiente) o leer las columnas de
servicio del CSV del cliente (que es el hilo de intake, no este). Se deja escrito en el PR.

**Consistencia de tipos.** `ServiceScan.order_lines` es el DataFrame que devuelve
`service_frame`, con las columnas de `_SERVICE_FRAME_COLUMNS` — no una copia de la lista.
`scan_service` devuelve `ServiceScan` en las tres tareas.
