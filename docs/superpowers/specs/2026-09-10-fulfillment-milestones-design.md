# fulfillment_milestones — hitos de cumplimiento con `means`

Fecha: 2026-09-10
Estado: implementado (spec A, 2026-09-10)
Rama sugerida: `feat/fulfillment-milestones`

Origen: caso FDE de Samir Saci (cadena de distribución, timestamps WMS/TMS,
>35% de atrasos sin explicación) contrastado con `OrderLine` y `SERVICE_COVERAGE`.
Este spec es **solo A**: el contrato de hitos. El pack de definiciones (quién
firma `LOAD_ONTIME`) y la atribución de culpa son specs aparte; sin este
contrato esos dos no tienen sobre qué correr.

---

## 1. Qué problema resuelve

Kern ya puntúa el extremo del pedido. `src/order_fulfillment.py` mide fill rate,
OTIF (SCOR RL.1.1) y venta perdida desde tres campos opcionales de
`OrderLine`. `FieldCoverage.means` obliga a decir si `delivered_date` es
despacho o llegada. Eso es el *qué* (¿llegó completo y a tiempo?).

No hay *dónde se detuvo*. Un pedido tarde con un solo timestamp de salida es
un número. El mismo pedido con `pack_end` y `load_end` es un tramo. El
director de logística del caso FDE no pedía otro OTIF: pedía por qué el 35%
no se podía explicar. Eso es instrumentación, no un LLM.

**Tesis en una línea:** un hito es un evento ocurrido, con el mismo rigor
que un campo de servicio: medido / derivado / no disponible, y una oración
que dice qué es el reloj. Ausencia sale del denominador del tramo. Nunca 0.

---

## 2. Estado verificado del repo

| Hecho | Fuente |
|---|---|
| **57** tools registrados | conteo de `key="` en `scm_agent/tools.py` |
| `OrderLine` tiene exactamente tres campos de servicio: `delivered_qty`, `promised_date`, `delivered_date` | `src/connectors/__init__.py` |
| `ServiceCoverage` declara esos tres, sin default y sin omitir | mismo archivo; `tests/test_connector_service_contract.py` **corre** la declaración |
| `ArrivalSource` es un Protocol aparte: el transportista no tiene catálogo | `src/connectors/arrivals.py` |
| `with_arrivals` pisa `delivered_date` con la llegada; una orden sin match **pierde** la fecha de despacho en vez de mezclar dos relojes | `arrivals.py` docstring de `with_arrivals` |
| OTIF de llegada vs despacho: 92,9% vs 99,2% sobre 98.666 pedidos (6,2 puntos) | `arrivals.py`, `examples/run_service_report.py` |
| `flow_map` mide tránsito **por lane** (origen→destino), no descompone un pedido en etapas internas | spec `2026-07-31-flow-map-design.md`; tool `flow_map` |
| `data_quality` es MDM de SKU (GTIN, duplicados), no arqueología de timestamps | `data_quality_tool` |
| Un `Tool` toma **un solo** `request.data_path` | registry / orchestrator |
| Laguna nombrada: “WMS/TMS operacional” | `documentation/KERN_NIVEL_REFERENCIA_SCM.md` |

---

## 3. Frontera con lo que ya existe

La pregunta que este spec tiene que sobrevivir: "¿por qué no es una columna
más en `OrderLine`?".

| | ya existe | este spec |
|---|---|---|
| `order_fulfillment` / `service_scan` | ¿completo y a tiempo? un reloj de entrega | ¿qué hitos hay entre crear y llegar? |
| `SERVICE_COVERAGE` | tres campos, `means` por campo | los mismos tres estados, **por hito** |
| `ArrivalSource` | un evento (llegó el bulto) | una cadena ordenada de eventos; la llegada es el último canónico, no se pisa el OTIF |
| `flow_map` | sigma de tránsito por arista de red | dwell por hop **dentro de un order_id** |
| `ScheduledReceipt` | dos fechas inbound (prometida vs medida) que nunca se colapsan | dos relojes outbound por hop: ocurrió vs no se midió |

No se toca la fórmula de OTIF. `promised_date` vs `delivered_date` sigue
siendo el extremo. Los hitos no “arreglan” un OTIF de despacho haciéndolo
pasar por llegada.

---

## 4. Decisiones cerradas

| # | Decisión | Elegida | Descartadas y por qué |
|---|---|---|---|
| D1 | Dónde viven los timestamps | Protocol `MilestoneSource` + tipos nuevos, **fuera** de `OrderLine` | Meter 6 campos en `OrderLine` (rompe el contrato de exactamente tres campos que el test de cobertura ejecuta; cada ERP tendría que mentir `unavailable` o inventar). Inferir hitos desde `orders()` (silencioso: `created_at` no es el `HEUR_DTS`/`MANU_DTS` del caso FDE). |
| D2 | Vocabulario | Set **cerrado** de 6 hitos (abajo) | Vocabulario abierto por cliente (el engine no puede nombrar hops ni testear cobertura). Set fashion-air (cutoff de aeropuerto, AWB): eso es definición local, spec B. |
| D3 | Qué es un hito | Un instante ISO en el que **ocurrió** un evento | Booleanos (`airport_on_time`, `LOAD_ONTIME`): son reglas sobre instantes, spec B. Duraciones planificadas por hop: también B. |
| D4 | Forma del CSV | Canónico **largo**: una fila = un evento | Solo ancho (una columna por hito): celda vacía se lee como cero. El largo trata la ausencia como fila que no existe. El prepare **sí** acepta ancho y lo normaliza a largo. |
| D5 | Relación con `delivered_date` | Los hitos **no** pisan `OrderLine`. `arrived` puede coincidir con una llegada de `ArrivalSource` por `order_id`, declaración compuesta aparte | Reusar `with_arrivals` para rellenar la cadena (mezclaría el OTIF con el mapa de hops). Auto-mapear `delivered_date` a `arrived` según el texto de `means` (NLP sobre una oración de contrato). |
| D6 | Motor de este PR | Dwell por hop + cobertura de cadena + “tarde sin instrumentar” | Atribución warehouse vs transporte vs forwarder (necesita el pack). Chat MCP sobre la tabla. Tool de RCA con LLM. |
| D7 | Reloj | ISO-8601; fecha sola o fecha+hora. Dwell en **horas** solo si **ambos** extremos tienen hora; si no, **días**. Mixto → días y una caveat en el informe | Asumir medianoche (inventa 8–19 h de dwell en un muelle). Unix epoch. |
| D8 | Inversión de secuencia | QA **falla** (no entregable) si `occurred_at` de un hito posterior es anterior al precedente **presente** | Clamp a 0 (el 35% inexplicado se esconde en ceros). Advertir y seguir (el número viaja igual). |
| D9 | Superficie agente | Tool nuevo `fulfillment_chain` en el mismo PR que el motor | Solo librería (no llega al operador). Meterlo en `service_scan` (cambia el significado de un tool que ya declara OTIF). |

---

## 5. Vocabulario cerrado

Seis hitos, en este orden. Un backend declara los seis. No se agregan IDs
en un PR de conector “porque el WMS tenía otro campo”: o mapea a uno de
estos o queda `unavailable`.

| `milestone_id` | Qué es (la oración por defecto, el conector puede ser más preciso) | Dueño típico, no atribuido |
|---|---|---|
| `order_created` | El pedido existe para el proceso (no “el click del e-commerce” si el planning tool crea otro id) | planning / ERP |
| `released_to_warehouse` | El almacén ya lo puede preparar | planning → WMS |
| `pack_end` | La carga está lista para el muelle | WMS |
| `load_end` | Terminó la carga al vehículo | WMS |
| `carrier_pickup` | El transportista se llevó la carga | TMS |
| `arrived` | Llegó a tienda o cliente, según el `means` de esa fuente | carrier / TMS / tienda |

No hay `airport_on_time`. Ese flag es `carrier_pickup` vs un cutoff, y el
cutoff es una definición.

---

## 6. Contrato de tipos

Archivo nuevo `src/connectors/milestones.py`. No se importa pandas al
import del paquete de conectores (mismo hábito que `service_frame`).

```python
# Reutilizar FieldCoverage / COVERAGE_* tal cual.
# MILESTONE_IDS = el tuple de §5, en orden.

@dataclass(frozen=True)
class MilestoneEvent:
    order_id: str
    milestone_id: str          # uno de MILESTONE_IDS
    occurred_at: str           # ISO date o datetime; no vacío
    source_system: str         # "wms" / "tms" / "erp" / nombre del archivo

@dataclass(frozen=True)
class MilestoneCoverage:
    backend: str
    # un FieldCoverage por cada id de MILESTONE_IDS, todos obligatorios
    def fields(self) -> dict[str, FieldCoverage]: ...

@runtime_checkable
class MilestoneSource(Protocol):
    def milestones(self, *, since: str | None = None) -> list[MilestoneEvent]: ...
```

`MilestoneEvent.__post_init__`: `milestone_id` ∈ set cerrado; `occurred_at`
parseable como fecha o datetime ISO; string vacío de `source_system` es
error. Un evento no lleva SKU: el hop es de la orden. Si un cliente solo
tiene hitos a nivel línea, el prepare agrega por `order_id` tomando el
**último** `occurred_at` de esa línea-hito (la orden no salió hasta que
salió la última línea) y lo declara `derived` en cobertura.

`MilestoneCoverage` no tiene default. Un módulo en `src/connectors/` que
defina `milestones()` y no exporte `MILESTONE_COVERAGE` falla el guard
(espejo de `test_connector_service_contract.py`). El primer implementor es
un stand-in en memoria más el lector de CSV del job; **ningún** ERP
existente implementa el Protocol en este PR. Declarar los seis
`unavailable` en Odoo/Unleashed “por si acaso” afirmaría una medición que
no hicieron.

Composición con carrier, misma forma que `coverage_of_arrivals`:

```python
def coverage_of_milestones(
    warehouse: MilestoneCoverage,
    carrier: MilestoneCoverage,
) -> MilestoneCoverage:
    ...
```

Por hito: si `warehouse.reports` usa ese id, gana warehouse; si no, carrier.
`arrived` casi siempre es carrier. Conflicto (ambos `reports` el mismo id)
→ no se mergea en silencio: el caller elige o el job falla `needs_data`
nombrando el id. No hay “el más temprano gana”: eso es mezclar relojes.

---

## 7. Contrato de archivo (el `data_path` del tool)

Un archivo, CSV o xlsx. El prepare acepta dos formas y emite siempre
eventos largos.

### 7.1 Largo (canónico)

| Campo | Requerido | Alias (en/es/pt, mismo mecanismo que `jobs/intake.py`) |
|---|---|---|
| `order_id` | sí | `order_number`, `pedido`, `plnord` |
| `milestone_id` | sí | `hito`, `milestone`, `event` |
| `occurred_at` | sí | `timestamp`, `fecha_hora`, `event_time` |
| `source_system` | no | `sistema`, `source`; default = nombre del archivo |

`milestone_id` en el archivo puede ser alias de un id canónico
(`pack_end` ← `pack_end` / `fin_packing` / `packing_end`). Un id que no
mapea → `needs_data` listando los valores ofensores, no drop silencioso.

### 7.2 Ancho (lo que un FDE suele armar)

Una fila por `order_id`. Columnas de hito opcionales. Columna ausente o
celda vacía = ese hito no ocurrió en el dato, **no** un dwell 0.
`source_system` puede ser una columna o `params.source_system`.

Promesa de entrega: **no** viaja en este archivo. Si el caller quiere
“tarde sin instrumentar”, pasa también las líneas de servicio (segundo
archivo no cabe en el registry). Por eso esa métrica sale de `params` o
de un join opcional **en el job** contra el mismo CSV si trae
`promised_date` + un hito `arrived`. Si no hay `promised_date`, el informe
no habla de atraso: solo de dwell y de cobertura. Inventar atraso desde
el dwell total vs una media sería el fallo de cantidad negativa con otro
traje.

---

## 8. Motor (`src/fulfillment_milestones.py`)

Puro, sin pandas. Input: secuencia de `MilestoneEvent` (+ opcionalmente,
por `order_id`, `promised_date` y `arrived_on` ya resueltos como fechas).

Salida (dataclass congelada):

`HopDwell`
- `from_id`, `to_id` (hitos canónicos adyacentes **en el vocabulario**, no
  adyacentes en los eventos)
- `dwell` (`timedelta` o `None`)
- `unit`: `"hours"` \| `"days"`
- `n_orders` con ambos extremos
- `n_missing` órdenes que tienen uno y no el otro (el denominador que
  no se usa)

Si `pack_end` y `carrier_pickup` existen pero `load_end` no, el hop
`pack_end → load_end` es `None` y `load_end → carrier_pickup` es `None`.
**No** se salta a `pack_end → carrier_pickup` y se lo llama dwell de
carga. Saltar hops afirma un tramo que nadie midió. Un salto se puede
mostrar en el informe como “cadena incompleta”, nunca como número de hop.

`ChainScan` (por corrida):
- órdenes distintas
- fracción con cadena **completa** (los seis)
- fracción con al menos el par `pack_end` + `carrier_pickup` (el corte
  almacén/transporte del caso FDE, todavía **sin** culpa)
- `uninstrumented_late`: órdenes con `promised_date` y llegada (hito
  `arrived` o fecha de servicio pasada explícita) **tarde**, y al menos
  un hop canónico ausente. Numerador y denominador aparte. Si no hay
  promesa, el campo es `None`, no 0.
- `sequence_violations`: lista; si no está vacía, `verify` falla.

Esto es el 35%: no “sin causa raíz”, sino **tarde y con agujeros en el
reloj**. Distinto de “tarde y la cadena está completa” (eso ya es
candidato a spec de atribución, y este motor no lo etiqueta).

---

## 9. Tool `fulfillment_chain`

- `requires_data=True`
- `prepare` pandas-only sobre **su** CSV, no `intake.py` (receta del
  repo).
- `run` → `ChainScan`
- `qa` → `verify`: violaciones de secuencia; `dwell` negativo imposible
  si D8 se cumplió; ids fuera de vocabulario no llegan aquí.
- `deliver` Excel + markdown: tabla de hops (n, dwell p50, unidad),
  cobertura por hito (usa `MilestoneCoverage` del lector de archivo:
  columna presente = measured, `means` = “columna {alias} del archivo
  {nombre}”), bloque `uninstrumented_late` o la frase “no hay promesa,
  no se habla de atraso”.
- Outcome: `EXECUTED` si hay al menos un hop medible. `needs_data` si
  cero eventos parseados. Nunca `EXECUTED` con todos los dwell en
  `None` presentados como “cadena sana”.
- Keywords multi-palabra, en/es: `cadena de hitos`, `fulfillment
  milestones`, `por que llega tarde el pedido`, `dwell por etapa`,
  `timestamps wms tms`. Evitar `otif` y `servicio` sueltos (le roban
  ruteo a `service_scan`).

Citas L3 tentativas (verificar en el grafo al implementar, no de
memoria): lead time, order cycle, SCOR RL.1.1, perfect order. No anclar
a `functional_silos` para no imantar el pool.

---

## 10. Tests que cargan el contrato

Nuevo `tests/test_connector_milestone_contract.py`:

1. `FieldCoverage.reports` sigue valiendo por hito.
2. `MilestoneCoverage` exige los seis ids; omitir uno no instancia.
3. Stand-in que **declara** `pack_end` measured y **no** lo emite → fail.
4. Stand-in que emite `pack_end` y lo declara unavailable → fail.
5. Guard de módulos: `milestones()` ⇒ `MILESTONE_COVERAGE`.
6. Merge con conflicto en el mismo id → no elige el min().

Nuevo `tests/test_fulfillment_milestones.py`:

1. Ambos extremos con hora → dwell en horas; 19:00 → 21:00 = 2 h.
2. Solo fechas → dwell en días.
3. Mixto → días + caveat.
4. Falta `load_end` → hops que lo tocan `None`; no aparece hop
   `pack_end → carrier_pickup` numérico.
5. `pack_end` posterior a `load_end` → verify falla, cero entregable.
6. Tarde vs promesa con `arrived` y sin `pack_end` → cuenta en
   `uninstrumented_late`.
7. Tarde con los seis hitos → **no** entra en `uninstrumented_late`.
8. Sin `promised_date` → `uninstrumented_late is None`.
9. Fixture tipo FDE (nombres opacos mapeados por alias, una columna
   vacía) → la vacía no produce dwell 0.

Boot: `milestones.py` y `fulfillment_milestones.py` no importan
`bs4` / extras opcionales a nivel de módulo (cadena de `webapp.app`).

---

## 11. Fuera de alcance (specs siguientes, no “después lo vemos”)

| Spec | Qué espera de A |
|---|---|
| **B — definition pack** | Hitos ya nombrados. El pack escribe reglas (`LOAD_ONTIME = load_end ≤ 19:00`, `CAUSE=...`) y las firma. Sin A, el pack es prosa. |
| **C — atribución** | Corre solo si el pack está firmado para ese cliente. Si warehouse y transporte no acordaron, `OPTIONS`/`HANDOFF`, no un veredicto. |
| Conector WMS/TMS live | Implementa `MilestoneSource`. Este PR no habla XML-RPC de Odoo ni API de Manhattan. |
| UAT “los lunes del planner” | Golden files contra `ChainScan`, no contra un chat. |

---

## 12. Definition of done

- Tipos + Protocol + stand-in + guard de cobertura.
- Motor de dwell con los 9 tests de §10.
- Job + `Tool` `fulfillment_chain` registrado; drift de README/conteo de
  tools actualizado (el guard existente debe pasar).
- Ningún conector ERP existente cambia su `SERVICE_COVERAGE` ni gana
  `milestones()` fingidos.
- `OrderLine` y `with_arrivals` sin diff funcional.

---

## 13. Lo que este spec no afirma

No afirma que Kern ya hace el trabajo FDE. Afirma que el primer objeto
de código que faltaba para **empezar** ese trabajo es el mismo que ya
ganó en servicio: un tipo, una ausencia honesta, una oración `means`,
y un test que ejecuta la declaración en vez de leerla.
