# `order_promise` — qué fecha puedo prometer, y desde dónde sale

Fecha: 2026-08-27
Estado: spec para aprobación. Su dependencia (`scheduled_receipts`) ya está construida; el motor de promesa sigue sin escribirse.
Rama sugerida: `feat/order-promise`

---

## 1. Qué problema resuelve

Entra un pedido. Alguien tiene que decirle al cliente una fecha. Hoy esa fecha
sale de una tabla estática ("despachamos en 2 días") o del criterio de quien
atiende, y nadie mide después si se cumplió — porque hasta los PR #292/#293 no
había con qué medirlo.

Kern ya sabe **puntuar** promesas: lee la que se hizo (`commitment_date` en Odoo,
`maxDeliveryDateTime` en Shopify) contra la que se cumplió, y saca OTIF real.
Lo que no sabe es **hacerlas**.

Hacer la promesa es decisión pura: no es registro (el pedido vive en el ERP del
cliente) ni ejecución (el despacho lo hace su bodega). Es exactamente la franja
donde Kern vive, y es la única función que los OMS open source tienen y Kern no.

**La tesis del spec, en una línea:** un OMS promete una fecha; Kern promete una
fecha **con un nivel de servicio declarado**, porque tiene la dispersión del
plazo medida y no solo su media. Una fecha calculada sobre la media se cumple el
~50% de las veces por construcción — es una moneda al aire con cara de
compromiso. Esa diferencia es el producto entero.

---

## 2. Estado verificado del repo (todo leído de un archivo, no de memoria)

| Hecho | Fuente |
|---|---|
| **50** tools registrados | conteo de `key="` en `scm_agent/tools.py` |
| **Cero** implementación de ATP, promesa de fecha o reglas de asignación | grep `available.to.promise\|capable.to.promise\|order_promis\|sourcing_rule\|allocation_rule` sobre `src/ jobs/ scm_agent/` → 0 hits |
| El grafo L3 **sí** cubre ATP: nodo `Available-to-Promise (ATP)` → `references` → `Master Production Schedule (MPS)` | `knowledge/scm-books/graph.json`, fuente `vollmann-manufacturing-planning-control.txt` |
| Y cubre la contraparte de medición: `Delivery Promise Accuracy` | `khan-supply-chain-resilience.txt`, Ch 5, PDF p.102 |
| Grafo L3: 3032 nodos / 6182 links | `knowledge/scm-books/graph.json` |
| Techo del pool de citas: `_CANDIDATE_POOL = 16`, `_MAX_CITATIONS = 3`. Empírico, se mueve con cada fuente | `scm_agent/packages.py:104-105` |
| Stock **por ubicación** ya llega de todo conector | `src/connectors/__init__.py::InventoryLevel(sku, available, location)` |
| Plazo de proveedor **medido con dispersión**: media, desvío y p90, más de dónde salió cada uno | `src/supplier_scorecard.py:51-63` (`avg_lead_time`, `lead_time_std`, `lead_time_p90`, `lead_time_source`) |
| El plazo medido se calcula contra recepciones reales, no contra la ficha | `src/connectors/odoo.py::receipts()` |
| Tiempo de tránsito de salida por modo | `src/logistics/modes.py::ModeQuote(mode, cost, transit_days, feasible, reason)` |
| Secuenciación y ventanas de entrega para reparto propio | `src/logistics/routing.py` (Clarke-Wright + sweep, con ventanas) |
| ~~**NO existe** lector de órdenes de compra pendientes~~ → **construido el 2026-08-27**, antes que este motor y por decisión explícita | `src/connectors/__init__.py::ScheduledReceipt` + `OdooConnector.scheduled_receipts()` |
| Una recepción pendiente lleva **dos** fechas que nunca se colapsan: `promised_date` (el `date_planned` del ERP) y `expected_date` (`date_order` + plazo **medido**) | `ScheduledReceipt`, 23 tests |
| `incoming_by_sku(receipts, measured_only=True)` da `sku -> [(fecha, cantidad)]` descartando toda recepción sin fecha medida | `src/connectors/__init__.py` |
| Y el propio conector documenta por qué `date_planned` no sirve como medición: es `date_order + seller.delay` | `src/connectors/odoo.py:595-596` |
| La promesa que se hizo ya se lee de los dos conectores | `commitment_date` (Odoo, PR #292) y `maxDeliveryDateTime` (Shopify, PR #293) |
| La medición de si se cumplió ya existe | `src/order_fulfillment.py::measure_from_order_lines` |
| Estados de `GuidedOutcome` | `nosilo_core.guided`: `EXECUTED`, `OPTIONS`, `HANDOFF`, `ESCALATED` |
| Un `Tool` toma **un solo** `request.data_path` | `scm_agent/registry.py` |

---

## 3. Frontera con lo que ya existe

La pregunta que este spec tiene que sobrevivir es "¿por qué esto no es un
parámetro de algo que ya está?".

| | ya existe | `order_promise` (este spec) |
|---|---|---|
| `multi_echelon`, `drp` | cuánto stock poner en cada nodo, por adelantado | qué fecha darle a **un pedido concreto que entra hoy** |
| `safety_stock`, `policies` | dimensionar el colchón | consumir el colchón que hay, ahora |
| `logistics/modes`, `routing` | cuánto tarda un envío | si además **hay qué enviar**, y desde dónde |
| `supplier_scorecard` | cuánto tarda el proveedor, medido | usar ese plazo para comprometer una fecha al cliente |
| `order_fulfillment` (#292/#293) | puntúa la promesa **después** | la hace **antes** |
| `cross_impact` | a quién lastima una decisión ya propuesta | produce la decisión |

Ninguno responde "entra este pedido: ¿qué fecha digo y de qué bodega sale?".

---

## 4. Decisiones cerradas

**4.1 ATP, no CTP.** El *capable-to-promise* de Vollmann suma capacidad de
producción al cálculo. El ICP de Kern es retail/distribución con inventario
pesado, no manufactura: no hay MPS que consultar. Se implementa ATP y se declara
que CTP queda afuera.

**4.2 La promesa lleva nivel de servicio, o no es una promesa.** La salida nunca
es una fecha sola. Es `(fecha, probabilidad)`: "el 14 con 95% de confianza, el 11
con 50%". El insumo es `lead_time_std` / `lead_time_p90`, que ya está medido. Un
SKU cuyo plazo solo tiene fuente `LEAD_TIME_DECLARED` **no se promete con
confianza**: sale como no cuantificable, igual que `order_fulfillment` reporta
`not_measured` en vez de un cero.

**4.3 Nunca prometer sobre una promesa.** Prohibido usar `date_planned` de Odoo o
`seller.delay` de la ficha del proveedor para calcular una fecha. Ese es el error
que `receipts()` existe para no cometer: la promesa al cliente heredaría la
promesa del proveedor, que el propio repo ya midió que no se cumple. Sin plazo
medido para el SKU, el resultado es `HANDOFF`, no una fecha optimista.

**4.4 El ATP cuenta stock en mano MÁS recepciones con fecha medida.** El lector se
construyó primero, a propósito, porque sin él la fecha era una cota superior y no
una fecha. La regla que importa: `incoming_by_sku(..., measured_only=True)` — solo
entran las recepciones cuya llegada se calculó con el plazo que ese proveedor
**corrió**, nunca con el que prometió. Una recepción sin plazo medido no engorda
el disponible: se reporta como visible-pero-no-comprometible, y la promesa al
cliente se calcula sin ella.

El sesgo sigue siendo conservador y sigue declarándose: un proveedor sin historial
suficiente hace que su carga no cuente, así que la fecha prometida puede ser más
tardía que la real. Prometer de más es el defecto caro.

**4.5 Kern cotiza; el sistema de registro compromete.** Ver §9.1: esto es la
decisión más importante del spec y la que define la forma de la salida.

---

## 5. Contrato de datos

Un solo CSV, como todo tool (`registry.py` da un `data_path`). Una fila por
**línea de pedido a prometer**:

| columna | obligatoria | significado |
|---|---|---|
| `order_id` | sí | agrupa las líneas de un pedido |
| `product_id` | sí | el SKU |
| `quantity` | sí | unidades pedidas |
| `requested_date` | no | la fecha que pide el cliente. Sin ella se cotiza la más temprana posible |
| `location` | no | bodega preferida. Sin ella se evalúan todas |
| `destination` | no | para el tiempo de tránsito de salida |

Y del `ClientProfile` / params ya fusionados: `service_level` (default 0.95),
`lead_time_days`. El stock por ubicación y los plazos medidos entran por el
mismo camino que el resto de los jobs.

**Ausencias que NO se rellenan:** un SKU sin stock en ninguna ubicación y sin
plazo medido no recibe fecha. Un `requested_date` ilegible no se interpreta como
"lo antes posible" — se reporta como fila no evaluable.

---

## 6. Módulos

`src/order_promise.py` — funciones puras, sin IO:

- `available_to_promise(on_hand, committed, horizon)` → el ATP discreto y el
  acumulado de Vollmann. Sin scheduled receipts en fase 1 (§4.4).
- `promise_date(atp, lead_time, service_level)` → la fecha, con la probabilidad
  que la sostiene. Toma media **y** dispersión; sin dispersión no devuelve
  confianza.
- `source_options(request, locations)` → de qué ubicación sale, con las
  alternativas y su costo/tránsito. Usa `logistics.modes` para el tránsito.
- `promise_quote(...)` → arma la cotización completa con su marca de tiempo.

`jobs/order_promise_job.py` — `prepare()` pandas-only leyendo su propio CSV
(nunca `intake.py`, por la receta de CLAUDE.md), `run`, `verify`.

---

## 7. Salida

Un `GuidedOutcome` por pedido, y el estado **no** es decorativo:

| situación | estado | por qué |
|---|---|---|
| Hay ATP y plazo medido; la fecha pedida se puede cumplir al nivel de servicio | `OPTIONS` | Nunca `EXECUTED`: Kern no puede comprometer stock (§9.1). Las opciones son las ubicaciones/modos, ordenadas |
| Se puede cumplir, pero solo partiendo el pedido o subiendo el modo de envío | `OPTIONS` | Con el costo de cada alternativa explícito |
| No hay forma de cumplir la fecha pedida | `HANDOFF` | Con la fecha más temprana alcanzable y qué la limita: stock, plazo o tránsito |
| No hay plazo medido para el SKU | `HANDOFF` | §4.3. Nunca una fecha sacada de la ficha del proveedor |
| El pedido es de un cliente sobre umbral, o la fecha se incumple por margen grande | `ESCALATED` | Con SLA, según el contrato de `nosilo_core.guided` |

Cada cotización lleva: fecha prometida, probabilidad, ubicación de origen, modo,
**marca de tiempo del stock que vio**, y la nota de la cota superior de §4.4.

---

## 8. Gate de QA (`verify`)

Bloquea el entregable si:

1. Alguna fecha prometida sale de un plazo con `lead_time_source` distinto de
   `LEAD_TIME_MEASURED`.
2. Alguna cotización llega sin probabilidad asociada.
3. La suma de lo prometido sobre un SKU supera su ATP (§9.1 — el doble compromiso
   dentro de una misma corrida sí es detectable y es un bug, no un riesgo).
4. Falta la marca de tiempo del stock, o la nota de §4.4.

---

## 9. Trampas de corrección que el spec fija

**9.1 Cotizar no es reservar, y Kern no puede reservar.** Kern no es el sistema de
registro: no puede poner un candado sobre unidades. Dos cotizaciones con diez
segundos de diferencia verían el mismo stock y prometerían la misma última unidad
dos veces — y las dos se verían perfectamente correctas.

Esto **no se arregla con código de Kern**, se arregla con la forma de la salida.
Por eso el estado nunca es `EXECUTED`, por eso toda cotización lleva la marca de
tiempo del stock que vio, y por eso el `verify` chequea el doble compromiso
dentro de la corrida (lo único que sí está en su alcance). El compromiso real lo
hace el ERP del cliente al confirmar el pedido. Es la misma frontera que
`nosilo_core.writeback` traza para cualquier otra escritura.

**9.2 La media es una moneda al aire.** Prometer con la media del plazo da ~50%
de cumplimiento por construcción. Es la trampa que hace que un OMS "funcione" y
que su OTIF sea malo sin que nadie entienda por qué. §4.2 lo prohíbe.

**9.3 El plazo del proveedor no es el plazo al cliente.** Reponer tarda lo que
tarda el proveedor **más** el tránsito de salida al cliente, más el manejo en
bodega. Sumar solo el primero subestima sistemáticamente. `logistics/modes` da el
segundo; el tercero no está medido en ningún lado y se declara como cero
declarado, no como cero medido.

**9.4 Stock disponible ≠ stock vendible.** El `available` de `InventoryLevel` es
lo que el conector reporta como disponible, que en Odoo ya descuenta reservas
pero en otros backends puede no hacerlo. La salida nombra qué definición usó cada
fuente en vez de asumir que las tres coinciden.

---

## 10. Tests

Los que definen si la capacidad es honesta:

- Un SKU con plazo declarado y no medido → `HANDOFF`, nunca una fecha.
- Una promesa al 95% cae más tarde que la misma al 50% (la dispersión se usa).
- Dispersión cero → las dos fechas coinciden (caso degenerado, sin dividir por cero).
- Dos líneas del mismo pedido sobre el mismo SKU no prometen dos veces el mismo stock.
- Sin `requested_date` se cotiza la más temprana; con una imposible, `HANDOFF` con
  la alcanzable y el limitante nombrado.
- La cota superior de §4.4 aparece SIEMPRE en la salida, no solo cuando hay POs.
- El bucle completo: prometer → alimentar la promesa a `order_fulfillment` →
  OTIF medido. Con un mundo simulado de verdad conocida (`src/simworld/`), una
  promesa al 95% cumple ~95%.

Ese último es el que cierra el círculo con #292/#293 y el único que prueba que el
nivel de servicio declarado significa algo.

---

## 11. Lo que NO entra — cada uno es un spec aparte

- ~~Scheduled receipts~~ — **hecho antes que este spec**, por decisión del
  2026-08-27. Solo falta el lado Shopify, que no tiene concepto de orden de
  compra: ahí el ATP seguirá contando únicamente stock en mano.
- **CTP** (capacidad de producción). Fuera del ICP.
- **Reserva/soft allocation** en el sistema de registro. Sería escritura, va por
  `nosilo_core.writeback` con su plano de aprobación, y es otra decisión.
- **Recalibración automática** del nivel de servicio contra el OTIF medido. El
  bucle existe conceptualmente desde #292; automatizarlo es un spec propio.
- **Promesa a nivel de carrito** (antes de que el pedido exista). Es superficie de
  storefront, no de decisión.

---

## 12. Riesgo declarado

El riesgo real no es técnico. Es que **la promesa es la salida más visible que
Kern puede producir**: un número que va directo a un cliente final del cliente.
Un fill rate mal calculado se discute en una reunión; una fecha mal prometida la
sufre alguien esperando un paquete. Por eso el spec empuja tan fuerte hacia
`OPTIONS`/`HANDOFF` y tan poco hacia `EXECUTED`, y por eso §4.4 elige el sesgo
conservador de forma explícita.

El segundo riesgo: §9.1 no tiene solución dentro de la frontera de Kern. Si un
cliente cotiza mucho y confirma poco, las cotizaciones se pisan entre sí y la
única defensa es la marca de tiempo. Si eso resulta insuficiente en un cliente
real, la conclusión honesta es que ese cliente necesita un OMS, no Kern — y ese
es un límite del producto que conviene conocer antes de venderlo.

---

## 13. Los 8 lugares fijos que toca un tool nuevo

Cada uno guardado por un test (ver `linchpin-digital-twin` en memoria):
`register()` en `scm_agent/tools.py` · función de opciones en `tool_options.py` ·
`TOOL_CONCEPTS` en `citation_gate.py` (anclas L3: `Available-to-Promise (ATP)`,
`Master Production Schedule (MPS)`, `Delivery Promise Accuracy`) ·
`TOOL_TITLES` en `src/i18n.py` (es+en) · el set fijo en `tests/test_scm_agent.py` ·
`EXPECTED_OFFLINE_BUCKETS` en `tests/test_capability_smoke.py` · `TOOLS` en
`webapp/how_it_works_data.py` · y los conteos en README (x4), CLAUDE.md y
`scm_agent/README.md`.
