# Diseño: las cuatro brechas después de N1d

Fecha: 2026-09-17. Estado: **diseño verificado contra el código, cero código escrito.**
Rama de origen: `claude/keen-goodall-jfnqo9` (PR #369), después del commit `ead1243` que cerró
N1d (H4: el tool CSV ve el estante y lo que viene en camino).

Qué se diseña acá, en el orden en que conviene ejecutarlo:

| | Brecha | Qué habilita | Tamaño |
|---|---|---|---|
| **C** | Tres brechas hermanas de H4: `shopify_replenishment`, `channel_replenishment`, `drp` | Ningún emisor de cantidad a pedir compra dos veces lo que ya viene | S (3 × medio día) |
| **A** | Barrido de neteo con la batería: sonda "el valor se mueve" sobre TODO tool que emite cantidad a pedir | La lista completa de brechas de esta clase, congelada como vector; una brecha nueva se pone roja sola | S-M (1 día) |
| **D** | N1c (los jobs escriben el snapshot `stock`) + N1a (rutear `forecast_error_out_of_band` y `lead_time_drift` con la serie REAL) | El Tower deja de sensar sobre vacío y sus dos eventos huérfanos tienen consumidor | M (2-3 días) |
| **B** | N2b: shocks en el marcador (`generate_world` + shock determinista, `score_shock`, `--shock` en el CLI) | El número "captura bajo disrupción", que la investigación pide antes de cualquier tool nuevo | M-L (3-5 días) |

Tres cosas que **ninguna** de las cuatro toca: el registro sigue congelado en 60 tools
(`tests/test_registry_frozen.py`), el puntaje de la batería sigue congelado en 65,00 % · 13/20
(`tests/test_bateria_puntaje_publicado.py::PUNTAJE_CONGELADO`) y "misma semilla ⇒ mismo mundo"
(`tests/test_simworld_world.py::test_same_seed_gives_identical_world`). Cada sección dice cómo.

La disciplina común es la de `tests/test_neteo_en_transito.py` (kern#251) y de la batería:
**que un campo exista no acredita nada** — cada sonda corre DOS veces con insumos distintos y
afirma que el VALOR SE MUEVE; y **la ausencia se declara, nunca se asume cero** — sin insumo,
`None`, y el entregable lo dice.

---

## C · Las tres brechas hermanas

Las tres se encontraron con un barrido de dos minutos sobre los jobs que producen cantidades a
pedir, después de cerrar H4. Comparten la causa: la capacidad existe en un camino (el neteo vive
en `src/connectors/replenish.py::plan_replenishment` desde kern#251, con `on_order_by_sku`) y el
camino que más se usa no la enchufa.

### C1 · `shopify_replenishment` nunca netea

**Hoy.** `jobs/shopify_job.py:183` llama `plan_replenishment(connector, cover_periods=...)` sin
`on_order_by_sku`. Es honesto — cada línea sale con `on_order=None` y `position == on_hand` —
pero nunca va a netear, porque nadie le da el mapa. El conector lee inventario con una sola
cantidad: `src/connectors/shopify_inventory.py:151-176` consulta
`inventoryLevels { quantities(names: ["available"]) }`. Shopify expone en esa misma consulta la
cantidad **`incoming`** (unidades en transferencias entrantes y órdenes de compra del propio
admin de Shopify, por ubicación): es el en-tránsito de una tienda, y hoy no se pide.

**Diseño.**

```python
# src/connectors/shopify_inventory.py
def get_inventory_quantities(self, inventory_item_id: str, location_id: str) -> dict[str, int]:
    """{'available': n, 'incoming': m} en UNA llamada; 'incoming' falta en el dict cuando
    la API no lo devolvió (versión vieja, scope insuficiente) -- nunca 0 en su lugar."""
    # misma query, names: ["available", "incoming"]

# get_inventory_level() queda como está (devuelve 'available'): compatibilidad.

# src/connectors/shopify.py
def incoming_by_sku(self) -> dict[str, float] | None:
    """SKU -> unidades entrantes en self._location_id. None cuando ningún item trajo
    'incoming' (la API no lo expone): ausencia declarada. Un SKU con incoming=0 SÍ va en
    el mapa: es una medición ('nada en camino'), no una ausencia."""

# jobs/shopify_job.py
def read_incoming(connector) -> tuple[dict[str, float] | None, str]:   # espejo de odoo_job.read_on_order
plan = plan_replenishment(connector, cover_periods=cover_periods, on_order_by_sku=on_order)
```

`ShopifyReplenishmentReport` gana `on_order_by_sku: dict[str, float] | None` y `netting_note: str`
(las mismas dos piezas que `OdooReplenishmentReport`, ver el final de `jobs/odoo_job.py::run`); el deck gana
el finding "NO descuentan el en-tránsito" cuando el mapa es `None`; el CSV operativo deja la
celda `on_order` en blanco, igual que `test_el_csv_del_job_deja_en_blanco_lo_que_no_midio`.

**Tests** (`tests/test_shopify_en_transito.py`, sobre el fake RPC de
`tests/test_shopify_replenishment.py`):
- `test_el_job_descuenta_lo_que_viene_en_camino`: dos corridas, `incoming` 0 vs 35 → la cantidad
  baja exactamente 35 y `netting_note` dice "descontado".
- `test_sin_incoming_en_la_api_declara_que_no_neteo`: la query devuelve solo `available` →
  `on_order_by_sku is None`, cada línea `on_order=None`, finding en el deck.
- `test_incoming_cero_es_medicion_no_ausencia`: `{"SKU": 0}` entra al mapa y la línea lleva `0.0`.
- `test_una_sola_llamada_por_item`: el fake cuenta llamadas; pedir dos cantidades no duplica RPC.

**No resuelve:** compras hechas fuera de Shopify (un mayorista que compra por mail) no aparecen
en `incoming`; eso lo cubre C2 cuando el ERP es la fuente.

### C2 · `channel_replenishment` no referencia stock en camino

**Hoy.** `jobs/channel_replenishment_job.py:434-437`: `qty = max(0, target - on_hand_erp)`; el
job lee `erp.inventory_levels()` y `erp.lead_times()` y nada más. El ERP que tiene el dato ya lo
expone: `OdooConnector.open_purchase_qty_by_sku()` (`src/connectors/odoo.py:991`), y
`jobs/odoo_job.py::read_on_order` ya envuelve el error del módulo de Compras en una nota.
`UnleashedConnector` no lee órdenes de compra (`src/connectors/unleashed.py:485` deja escrito que
`/PurchaseOrders` no se usa hasta que un cliente lo pida).

**Diseño.**

```python
# jobs/channel_replenishment_job.py
def read_erp_on_order(erp) -> tuple[dict[str, float] | None, str]:
    if isinstance(erp, OdooConnector):
        return odoo_job.read_on_order(erp)            # reuso literal, no copia
    return None, f"{type(erp).__name__}: el conector no expone órdenes de compra abiertas"

on_order, netting_note = read_erp_on_order(erp)
for sku in matched:
    oo = None if on_order is None else on_order.get(sku)
    position = net_flow_position(on_hand_erp, oo or 0.0, 0.0)      # la identidad, no otra fórmula
    qty = max(0.0, round(target - position, 1))
```

`ChannelLine` gana `on_order: float | None = None` y `position: float | None = None` (mismo
docstring que `excel_replenishment_job.ReplenishmentLine`); `ChannelReplenishmentReport` gana
`n_netted`, `netting_note`; `_build_outcome` suma un residual cuando `on_order is None`
("no se descontó lo que viene en camino: ..."); el deck, un finding. `StockDivergence` no cambia:
compara estantes, y el en-tránsito no es estante.

**Tests** (`tests/test_channel_en_transito.py`, con los fakes que ya usa
`tests/test_channel_replenishment*.py`): dos corridas con Odoo con OC abierta 0 vs 35 → baja 35;
con Unleashed → `None` + nota + residual; SKU sin OC abierta → `None` en la línea, no `0.0`.

**No resuelve:** Unleashed sigue sin en-tránsito hasta cablear `/Purchases` (peldaño 2 del plan
del conector); queda declarado en la nota, no escondido.

### C3 · `drp` planifica sin entradas programadas

**Hoy.** `src/drp.py::drp_plan` (líneas 41-66): `available = prev_oh - forecast[t]`; no existe el
término de *scheduled receipts*, que en DRP clásico es insumo estándar (lo que ya se pidió y
llega en el período t). `jobs/drp_job.py` lee `on_hand` por sucursal (`_ONHAND_COLS`, línea 24) y
nada de en-tránsito. Consecuencia: una OC ya emitida a una sucursal se vuelve a planificar como
liberación.

**Diseño** (motor + job, aritmética verificable a mano):

```python
# src/drp.py
@dataclass(frozen=True)
class Branch:
    ...
    scheduled_receipts: tuple[float, ...] = ()   # por período; () = ninguna declarada

def drp_plan(branch, n_periods=None):
    receipts = list(branch.scheduled_receipts) + [0.0] * (n - len(branch.scheduled_receipts))
    for t in range(n):
        available = prev_oh + receipts[t] - forecast[t]       # la única línea que cambia
        ...
# DrpRow gana `scheduled_receipt: float` (auditable en el entregable)
```

Instancia a mano, extendiendo `tests/test_drp.py::test_drp_grid_nets_and_offsets_releases`
(forecast 10/período, on-hand 15, SS 5, lead 1): sin recepciones, `planned_receipt =
[0, 10, 10, 10]`; con `scheduled_receipts = (0, 10, 0, 0)`, `planned_receipt = [0, 0, 10, 10]` y
`planned_order_release = [0, 10, 10, 0]`. **El valor se mueve exactamente lo programado**, y el
proyectado sigue clavado en el piso de SS (5, 5, 5, 5).

`jobs/drp_job.py`: `_RECEIPT_COLS = ("scheduled_receipt", "scheduled_receipts", "in_transit",
"on_order", "incoming", "en_transito", "por_recibir")`, sumadas por (sucursal, período) como la
demanda; `dc_scheduled_receipts` en params para el CD; `DrpReport` gana `n_branches_with_receipts`
y el deck un finding cuando es 0 con sucursales > 0 ("sin entradas programadas en el archivo: las
liberaciones pueden repetir stock ya en camino"). Productor ERP futuro, ya existente del lado de
Odoo: `OdooConnector.scheduled_receipts()` (`src/connectors/odoo.py:809`) trae unidades con
`promised_date` / `expected_date` — bucketizar por período es una función de 15 líneas cuando un
cliente lo pida.

**Tests**: la instancia de arriba; recepción en el período 0 se suma al estante inicial;
`scheduled_receipts` más corta que el horizonte se rellena con 0; el job lee la columna y el
finding aparece/desaparece; QA: `projected >= safety_stock` sigue valiendo.

**No resuelve:** una recepción con fecha incierta (lead time no medido) entra al período
prometido; el diseño de ATP ya separa `promised_date` de `expected_date`, y DRP debería
heredar esa distinción cuando se cablee el productor.

---

## A · Barrido de neteo con la batería

**Hoy.** T05 (`src/bateria/comprobaciones.py:798-812`) sonda TRES entregables —
`_candidato_excel`, `_candidato_odoo`, `_candidato_ddmrp` — y `_veredicto_oc` puntúa **el mejor**.
Eso es correcto para la vara (la pregunta de T05 es "¿Kern puede?") y ciego para el producto
(la pregunta de esta brecha es "¿cuál de los emisores NO puede?"). Por eso H4 vivió meses con la
batería en verde: `inventory_optimization` nunca fue candidato, y las tres hermanas tampoco.

La vara no se toca: `tareas.json` es externa y `TOTAL_TAREAS = 20` (`src/bateria/scorer.py:39`);
agregar una tarea la destruiría (`tests/test_bateria_tareas.py`). El barrido es un **diagnóstico
al lado de la batería**, con su misma disciplina de sonda, que la T05 pasa a consumir.

**Diseño.**

```python
# src/bateria/barrido_neteo.py
@dataclass(frozen=True)
class SondaNeteo:
    tool: str
    entregable: str                       # "jobs.x.Linea"
    campo_en_transito: str | None         # cómo entra el en-tránsito (columna / param / mapa); None = no hay canal
    descuenta: bool | None                # True/False = medido; None = hay canal y nadie lo sondeó
    prueba: str                           # "con 80 en camino la cantidad baja: [120.0] -> [40.0]"

def sonda_dos_corridas(correr: Callable[[float], Sequence[float]], *, en_camino: float) -> tuple[bool | None, str]:
    """La sonda genérica: corre con 0 y con `en_camino`; True si TODA cantidad baja; None si no
    hubo líneas dimensionadas (no se midió nada -- ni se acredita ni se niega)."""

EMISORES: dict[str, Callable[[], SondaNeteo]]      # tool -> sonda
NO_EMISORES: frozenset[str]                        # tools que no emiten cantidad a pedir, con razón

def barrer() -> tuple[SondaNeteo, ...]
```

- Las tres sondas de T05 se **mueven** a `EMISORES` (excel_replenishment, odoo_replenishment,
  ddmrp) y `t05_propuesta_de_oc_por_proveedor` llama `_veredicto_oc(sondas_t05())` sobre las
  mismas tres: el veredicto y `PUNTAJE_CONGELADO` no se mueven. Se agregan sondas para
  `inventory_optimization` (`run(on_hand_by_sku=..., on_order_by_sku=...)`, ya existe desde
  `ead1243`), `shopify_replenishment`, `channel_replenishment`, `drp`, `multi_echelon`, `sop`
  (`opening_inventory` sin en-tránsito: la sonda reporta `campo_en_transito=None`),
  `launch_readiness` y `channel`/`odoo` por sus fakes.
- **Clasificación obligatoria**: `tests/test_barrido_neteo.py` recorre
  `build_default_registry()` y falla si un tool no está ni en `EMISORES` ni en `NO_EMISORES`
  — el patrón de `UNDECLARED_TOOLS` en `tests/test_tool_data_contract.py`: un tool nuevo tiene
  que decir si emite cantidades a pedir, y si lo hace, con qué sonda.
- **Vector congelado**: `ESTADOS_NETEO = {"netea": [...], "no_netea": [...], "sin_canal": [...],
  "sin_sonda": [...]}`; cerrar una brecha es un diff explícito sobre ese vector, igual que
  `ESTADOS_CONGELADOS` en el puntaje publicado. Con C hecho antes, `no_netea` arranca vacío y
  `sin_canal` nombra a `sop`/`multi_echelon` con razón.
- `scripts/barrido_neteo.py` imprime la tabla (tool · canal · veredicto · prueba) y escribe
  `documentation/KERN_BARRIDO_NETEO.md`; `test_el_documento_dice_lo_que_mide` lo mantiene
  sincronizado, como hace `tests/test_bateria_puntaje_publicado.py` con su README.

**Riesgo.** Tiempo: cada sonda corre un job dos veces; las sondas de conector usan fakes en
memoria, así que el barrido entero debería quedar bajo 30 s. La batería completa ya toma 60-90 s;
el barrido corre aparte y no la engorda.

**No resuelve:** el neteo es UNA clase de brecha. La misma maquinaria sirve para otras
("¿usa el lead time medido o el declarado?", "¿lleva la fecha de llegada?"), pero cada sonda
nueva es una decisión de qué se considera insumo, y eso se diseña por clase, no en abstracto.

---

## D · N1c + N1a: el Tower con datos reales

### D1 · N1c — los jobs que leen stock escriben el snapshot `stock`

**Hoy.** `run_all_monitors` (`scm_agent/monitors.py:600-660`) lee `history("stock")`,
`latest("forecast")` y `history("outcomes")`. El único escritor de `stock` es el puente del
marcador (`examples/run_world_sim.py::simulate(state_store=...)`). En producción nadie escribe:
H1 de la investigación. `record_stock(store, cycle_id, rows)` (`src/state/record.py:24`) exige
por fila `product_id`, `on_hand`, `reorder_point`, `avg_daily_demand` (no negativos,
`DOMAIN_COLUMNS["stock"]`, `src/state/system_state.py:64-69`). Cuatro jobs hoy tienen esas
cuatro cosas o casi: `odoo_job`, `excel_replenishment_job`, `channel_replenishment_job` y, desde
`ead1243`, `inventory_optimization` (`on_hand`, `reorder_point`, `forecast`).

**Diseño** — un solo puente, cuatro llamadores:

```python
# jobs/state_bridge.py
def record_stock_from_lines(store: StateStore, *, tool: str, lines: Iterable[StockLine],
                            period_days: float, now: datetime | None = None) -> str:
    """Escribe UN snapshot 'stock' y devuelve la nota para el reporte. cycle_id =
    f"{tool}-{now:%Y%m%dT%H%M%S}" (el store rechaza duplicados por dominio+ciclo)."""

@dataclass(frozen=True)
class StockLine:
    product_id: str
    on_hand: float
    reorder_point: float        # el disparador que el job USÓ (ver mapeo)
    avg_daily_demand: float     # forecast por período / días del período
```

Mapeo por job, declarado en el docstring y en los tests, nunca inventado:

| job | on_hand | reorder_point | avg_daily_demand |
|---|---|---|---|
| `inventory_optimization` | `rec.on_hand` (solo SKUs con posición) | `rec.reorder_point` | `rec.forecast / period_days` |
| `odoo_job` / `shopify_job` | `line.on_hand` | `line.target` (el plan pide cuando la posición cae bajo el target: ES el disparador) | `line.forecast_per_period / 7` |
| `excel_replenishment_job` | `line.on_hand` | `line.trigger` si no es `None`; si es modo cobertura, `line.target` | `demand_per_period / 7`; sin demanda, la fila NO se escribe |
| `channel_replenishment_job` | `line.erp_on_hand` | `line.target` | `channel_demand_per_period / 7` |

Identidad: la misma regla que `jobs/forecast_cycle.py::store_from_params` (líneas 176-206):
**sin `params["state_client_id"]` no se escribe nada, nunca la partición compartida**; un id
inválido levanta `ClientIdError`, no degrada. Se cablea en los `_run` de `scm_agent/tools.py`
(que reciben `params`), igual que `_forecast_run` pasa `store=forecast_cycle.store_from_params(params)`.
Cada reporte gana `state_note: str` ("stock snapshot recorded as cycle X" / "no state_client_id:
not recorded"), y el resumen del agente la repite.

**Tests** (`tests/test_state_bridge_stock.py`): con un `StateStore` en `tmp_path`, correr cada
uno de los cuatro tools con `state_client_id` → `latest("stock")` trae una fila por SKU con el
mapeo de la tabla (valores que se mueven al cambiar el fixture); sin la clave → `count_all() == 0`
y la nota lo dice; una fila con `avg_daily_demand` 0 pasa la validación (no negativa), una sin
`on_hand` no se escribe; después de dos corridas, `run_all_monitors(store=store)` emite
`rop_breach` sobre un SKU bajo su disparador — la prueba de que el Tower dejó de sensar vacío.

**Límite declarado.** El dominio `stock` no tiene `on_order`, y `ColumnRule` solo modela
columnas obligatorias: agregarla exigiría un ciclo de esquema en `system_state.py` y romper al
puente del marcador. Consecuencia honesta: `rop_breach` sigue disparando sobre el ESTANTE; si hay
una OC en camino, el evento se rutea a `inventory_optimization`, que desde `ead1243` netea cuando
el archivo trae `on_order` — pero el CSV sintético del constructor no lo trae. Queda como
seguimiento: "columnas opcionales en `DOMAIN_COLUMNS`".

### D2 · N1a — rutear los dos monitores huérfanos con la serie REAL

**Hoy.** `config/event_routing.yaml` rutea 7 tipos; `forecast_error_out_of_band` y
`lead_time_drift` se emiten (`scm_agent/monitors.py:341-470`, con dedup) y nadie los consume: el
encabezado del yaml lo dice y da la razón correcta — fabricar una serie de 1-2 números para
correr un tool viola las reglas 7 (procedencia total) y 14 (ningún cap silencioso). El
constructor existente para eventos de `stock` sí sintetiza historia
(`inventory_from_state_stock_event`, `_SYNTHETIC_HISTORY_DAYS = 28`, documentado como sintético).

El insumo real existe en el estado, pero solo lo escribe el marcador: `outcomes` con
`metric == "actual_qty"` por período (el puente de `simulate`) y `metric == "lead_time_days"`.
En producción `forecast_cycle.freeze` escribe `forecast_qty` y MAPE/bias, **no** `actual_qty`
(`jobs/forecast_cycle.py:305-331`). Por eso N1a depende de N1c-bis: un productor de `actual_qty`
y `lead_time_days` reales.

**Diseño.**

1. **Productor** (misma sesión que D1, mismo puente): `record_outcomes_from_demand(store, tool,
   series_by_sku, period_days)` escribe `actual_qty` por (SKU, período) a partir de
   `demand_by_sku(orders)` en los jobs de conector, y `lead_time_days` por SKU a partir de
   `connector.receipts()` (el plazo MEDIDO, no `supplierinfo.delay`). Mismo opt-in por
   `state_client_id`.
2. **Constructores** (`scm_agent/event_intent.py`), ambos sobre una sola función:

```python
def _real_series_csv(store: StateStore, skus: Sequence[str], *, min_points: int) -> Path:
    """CSV (product_id, date, quantity) desde history('outcomes') metric=='actual_qty'.
    Levanta EventRoutingError si algún SKU tiene menos de `min_points` (MIN_PERIODS_FOR_POLICY):
    regla 7 -- una serie corta no se completa con nada."""

def forecast_from_outcomes_event(event) -> dict:          # -> tool `forecast`, T2
def inventory_from_lead_time_drift_event(event) -> dict:  # -> tool `inventory_optimization`, T2
    # overrides = {"lead_times": {sku: recent_mean_days}} -- el parámetro que el tool YA acepta
```

   Para `lead_time_drift` el destino correcto es `inventory_optimization` con el override
   `lead_times` (el drift cambia el punto de reorden y el buffer), **no** `sop` con un
   `promo_lift` derivado como sugería la investigación: un lift de promoción es demanda, no
   plazo, y usarlo como proxy sería el cap silencioso de la regla 14.
3. **Partición**: el evento tiene que saber de qué cliente es su serie. `run_all_monitors`
   gana `client_id: str | None` y lo pone en `event.payload["state_client_id"]`; el constructor
   abre `store_for(state_client_id)`; sin él, `EventRoutingError` con la frase "no hay serie
   real que leer" — nunca la partición compartida (la misma regla de `store_from_params`).
4. **Rutas** en `config/event_routing.yaml`: dos entradas nuevas, T2, y se borra el párrafo del
   encabezado que explica por qué no estaban.

**Tests** (`tests/test_event_intent_real_series.py`): store con 12 semanas de `actual_qty` →
el CSV tiene 12 filas por SKU y `handle_event` corre el tool `forecast` de punta a punta por el
orquestador real (como hace el test de aceptación F0 con `stock_below_rop`); con 3 puntos →
`EventRoutingError` que nombra la regla 7; `lead_time_drift` → `overrides["lead_times"]` trae
la media reciente y la política cambia respecto de correr sin override (**el valor se mueve**);
sin `state_client_id` → error, no partición compartida; la tabla de ruteo carga con las 9 rutas.

**No resuelve:** N1b (el reloj: `MONITORS_JOB` sigue sin cron; necesita un secreto de repo que
carga un admin, no código). Y el evento sigue siendo T2: propone, un humano aprueba.

---

## B · N2b — shocks en el marcador

**Hoy.** `generate_world(seed, n_skus, n_suppliers, weeks, regime)` (`src/simworld/world.py:53-93`)
sortea con un solo `rng` la demanda (`(n_skus, weeks)`) y el lead real por orden
(`real_lead_weeks`, mismo shape; `_real_lead_weeks` aplica el OTIF del proveedor). `advance()`
(líneas 144-200) lee `world.demand[:, t]` y `world.real_lead_weeks[i, t]` **en el momento de
emitir** la orden. `score_run` devuelve `Kpis` sin ventana temporal; `degradation()`
(`scorer.py:220`) ya atribuye exceso de costo **semana a semana** entre dos corridas de la misma
semilla. El CLI (`examples/run_world_sim.py:205-262`) genera UN mundo y corre piso, oráculo y
carriles de Kern sobre él; `src/verify/barrido.py::agregar` agrega N mundos en distribuciones con
peor caso y compara contra una línea base grabada. El gemelo (`src/digital_twin.py:71-81`) ya
tiene `Disruption(kind, target, start, duration, magnitude)` con tres tipos; el marcador no.

**Diseño** — el shock es una transformación pura del mundo ya sorteado, así que misma semilla ⇒
mismo mundo base + mismo shock, sin tocar `rng` ni `advance`:

```python
# src/simworld/shocks.py
@dataclass(frozen=True)
class Shock:
    kind: str            # "lead_time_spike" | "supplier_outage" | "demand_surge" (los del gemelo)
    target: str          # sku ("SKU-1003") o proveedor ("Proveedor-2"); demand_surge exige sku o "*"
    start_week: int
    weeks: int
    magnitude: float     # spike: +semanas de lead; surge: multiplicador de demanda; outage: ignorado

def apply_shocks(world: World, shocks: tuple[Shock, ...]) -> World:
    """dataclasses.replace(world, demand=..., real_lead_weeks=...) DESPUÉS del sorteo base.
    lead_time_spike: real_lead_weeks[i, t] += magnitude para órdenes emitidas en la ventana.
    supplier_outage: órdenes emitidas en la ventana llegan no antes de start+weeks+prometido:
                     real_lead_weeks[i, t] = max(real, start + weeks - t + promised_lead).
    demand_surge:    demand[i, t] = round(demand * magnitude) en la ventana.
    Fuera de la ventana nada cambia; aplicar dos veces la misma tupla da el mismo mundo."""
```

`World` no gana campos: el shock viaja explícito a quien lo mide.

```python
# src/simworld/scorer.py
@dataclass(frozen=True)
class ShockImpact:
    first_out_of_band_week: int | None   # None = el shock se absorbió (Anexo A.1: pasa con holgura)
    ttr_weeks: float | None              # desde la primera salida de banda hasta `hold` semanas adentro; None = no recuperó
    recovered: bool
    min_fill: float
    lost_units_post_shock: float         # demanda del MUNDO CON SHOCK no servida en [start, fin)
    lost_vs_baseline: float              # servido base - servido con shock, CON SIGNO (H5: bajo surge puede ser negativo)
    cost_excess_post_shock: float        # degradation() semana a semana, reusada tal cual

def score_shock(world, baseline: list[WeekOutcome], shocked: list[WeekOutcome], shock: Shock,
                *, band: float = 0.02, hold: int = 3) -> ShockImpact
```

Las dos definiciones que la investigación dejó fijadas: TTR se mide **desde la primera semana
fuera de la banda** (v2 del experimento A.1, no desde `start`), y bajo `demand_surge` las unidades
perdidas se miden contra la demanda perturbada, no contra lo que el baseline sirvió — ambas se
reportan, con nombre distinto, nunca una en lugar de la otra (H5).

**Captura bajo disrupción**: los tres carriles corren sobre `apply_shocks(world, shocks)`; el
oráculo ve la verdad perturbada (información perfecta, sigue siendo techo), el piso quedó
congelado en las semanas de calentamiento (no ve el shock: es el piso), Kern replanifica cada
`replan_every` semanas y lo ve por la lente (con su nivel de suciedad). Entonces
`capture_under_shock = capture(floor_shocked, kern_shocked, oracle_shocked)` y el **costo del shock
para Kern** = `degradation(kern_clean_weekly, kern_shocked_weekly, announced)`, semana a semana.

**CLI**: `run_world_sim.py --shock lead_time_spike:SKU-1003:10:3:2` (`kind:target:start:weeks:
magnitude`, repetible); el JSON de salida gana una sección `shock` por carril con `ShockImpact` y
`capture_under_shock`; `--seeds 25` agrega con `src/verify/barrido.agregar` y graba la línea base
con `guardar_linea_base(contexto={..., "shocks": [...]})`, para que "captura bajo un salto de lead
time de 3 semanas: X % ± Y, peor mundo Z" sea una frase con número.

**Tests** (`tests/test_simworld_shocks.py`):
- determinismo: `apply_shocks` dos veces ⇒ mundos idénticos; fuera de la ventana, `demand` y
  `real_lead_weeks` son iguales al mundo base (elemento a elemento);
- `lead_time_spike`: una orden emitida en la ventana llega exactamente `magnitude` semanas más
  tarde que en el mundo base (`advance` sin tocar; `test_order_arrives_after_the_real_lead_time_not_the_promised_one`
  como plantilla);
- `supplier_outage`: ninguna orden emitida en la ventana llega antes de `start + weeks`;
- `demand_surge`: solo la ventana se multiplica; la conservación de masa de `advance` se mantiene;
- `score_shock` sobre trazas a mano (fill base 1,0; con shock (0, 0, 0,5) y luego 1,0 → TTR 3,
  `recovered=True`, `lost_units` = 25 como en el plan de `resilience_stress_test`);
- H5: surge ×2 con servicio pleno → `lost_units_post_shock == 0` y `lost_vs_baseline < 0`,
  reportado con signo, sin `max(0, ...)`;
- CLI: `--shock` produce la sección y `capture_under_shock` puede ser negativa (la investigación
  dice que ese es el resultado que hay que **querer** ver).

**Riesgo.** Runtime: cada shock duplica los carriles (mundo limpio y con shock). Con 8 SKU × 24
semanas × 4 niveles hoy son segundos; con `--seeds 25` son minutos, como el barrido actual.

**No resuelve:** atribuir culpa (Nivel 3). Y si la captura bajo shock sale negativa en la
mayoría de los mundos, el trabajo siguiente **no es N3**: es entender por qué
`inventory_optimization` pierde contra un punto de reorden fijo — y con N1d cerrado, H4 ya no es
el primer sospechoso, así que la respuesta va a ser nueva.

---

## E · Orden, dependencias y qué se mide al final

```
C1 C2 C3  ──►  A (el barrido nace con `no_netea` vacío y lo congela)
                    │
D1 (N1c) ──► D2 (N1a)        B (independiente; conviene después de A para que el marcador
                                no mida una brecha que el barrido ya sabe)
```

- **C antes que A**: si A corre primero, congela tres `no_netea` y C es un diff sobre el
  vector; funciona igual, pero cerrar primero deja el vector limpio desde el día uno.
- **D1 antes que D2**: sin `actual_qty` real en el estado, N1a no tiene serie que leer y el
  constructor solo puede levantar.
- **B al final**: es la medición; conviene que mida el sistema con los cables cerrados.

Criterio de hecho, por brecha: C → los tres tests de "el valor se mueve" en verde y la nota de
ausencia en cada deck; A → `ESTADOS_NETEO` congelado con `no_netea == []` y el documento
publicado; D → `run_all_monitors` emite un `rop_breach` sobre un snapshot escrito por un job de
producción, y los dos eventos huérfanos corren un tool por el orquestador real; B → una línea
base grabada con captura bajo shock sobre 25 mundos.

---

## Anexo — cómo se verificó este diseño

Todo lo citado se leyó en la rama `claude/keen-goodall-jfnqo9` @ `ead1243` el 2026-09-17:

```
grep -n "plan_replenishment(" jobs/shopify_job.py                 # :183 sin on_order_by_sku
grep -n 'quantities(names' src/connectors/shopify_inventory.py      # solo "available"
grep -n "on_order\|in_transit\|net_flow_position" jobs/channel_replenishment_job.py   # vacío
sed -n 41,66p src/drp.py                                             # sin scheduled receipts
sed -n 798,812p src/bateria/comprobaciones.py                        # T05: tres candidatos, el mejor
grep -n "def record_stock" -A 3 src/state/record.py                  # las cuatro columnas
sed -n 600,660p scm_agent/monitors.py                                # qué lee el Tower
grep -n "forecast_error_out_of_band\|lead_time_drift" config/event_routing.yaml  # no ruteados
sed -n 53,125p src/simworld/world.py; sed -n 144,200p src/simworld/world.py       # sorteo y avance
grep -n "^def " src/verify/barrido.py                                # agregar / línea base
```
