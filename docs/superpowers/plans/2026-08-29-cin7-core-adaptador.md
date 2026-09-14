# Cin7 Core — adaptador y la primera prueba de que el guard generaliza

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development o
> superpowers:executing-plans. Los pasos usan checkbox (`- [ ]`).

**Goal:** `src/connectors/cin7.py`, el segundo adaptador del canal AU/NZ, sometido al mismo
guard de cobertura que Unleashed — que es la primera prueba real de si esa abstraccion
generaliza a un backend con despacho multiple y un modelo de datos distinto.

**Architecture:** `Cin7Connector` lee `/saleList` para la ventana y despues `/sale?ID=` por
venta, que devuelve la orden Y sus fulfilments inline. La cantidad servida NO sale de un
endpoint: sale de un empalme de dos saltos, `Pack.Lines[] (SKU -> Box -> Quantity)` contra
`Ship.Lines[] (Boxes -> ShipmentDate, IsShipped)`. Ese empalme es una funcion pura,
`shipped_by_sku(sale)`, testeada aparte del transporte.

**Tech Stack:** Python 3.11, stdlib (sin firma: Cin7 autentica por cabeceras planas),
pytest. Transporte inyectado httpx-style, igual que `unleashed.py` y `shopify_connect.py`.

**Spec:** el blueprint oficial de la API, `https://dearinventory.docs.apiary.io/api-description-document`
(1,7 MB, formato API Blueprint) — leido entero el 2026-08-29. Todo lo de abajo salio de ahi.

## Global Constraints

- Rama: `feat/cin7-core-adaptador`, apilada sobre `feat/servicio-al-entregable` (necesita
  `ServiceCoverage` de #307 y `scan_service` de #308). Es la tercera de la pila.
- **Worktree compartido.** `git status` justo antes de cada `git add`. `.mcp.json` y
  `data/bateria/` NO son de este hilo. Nunca `git add -A`.
- Interprete: `.venv/Scripts/python.exe` del worktree.
- `ruff check .` (repo entero), `line-length = 120`.
- Greps acotados a `supply-chain-optimization/`.
- Archivos largos: herramienta de escritura, no heredoc (se trunca a los ~6 KB).
- **Prohibido inventar numeros.** Un campo que el backend no reporta viaja `None`.
- No tocar precios (D2, abierta) ni BWM (D4).

## Hechos verificados (2026-08-29, blueprint oficial) — y las 3 correcciones

Base: `https://inventory.dearsystems.com/ExternalApi/v2/`.
Auth: cabeceras `api-auth-accountid` + `api-auth-applicationkey`.
Limite: **60 llamadas por minuto**, `429 Too Many Requests`.
Paginado: `?Page=&Limit=` (default 100), envoltorio `{"Total": N, "Page": n, "<X>List": [...]}`.

| Endpoint | Lo que da |
|---|---|
| `GET /saleList?Page&Limit&CreatedSince&UpdatedSince&Status&OrderStatus` | `SaleID`, `OrderNumber`, `OrderDate`, `ShipBy`, `Status`, `OrderStatus`, `CombinedShippingStatus`, `FulFilmentStatus` |
| `GET /sale?ID={SaleID}` | `Order.Lines[]` (`SKU`, `Quantity`, `Price`, `Backorder`, `BackorderQuantity`) **y** `Fulfilments[]` con `Pick`/`Pack`/`Ship` inline |
| `GET /product` | `SKU`, `Name`, `PriceTier1`, `AverageCost` |
| `GET /ref/productavailability` | `SKU`, `Location`, `OnHand`, `Allocated`, `Available` |

**Correccion 1 — `/sale/fulfilment/ship` no tiene producto ni cantidad.** Sus `Lines[]` son
CAJAS: `{ID, ShipmentDate, Carrier, Boxes, TrackingNumber, TrackingURL, IsShipped}`. La nota
previa decia que ese endpoint daba la cantidad despachada. Es falso. La cantidad por SKU
esta en `Pack.Lines[]` (`SKU`, `Box`, `Quantity`) y la fecha en `Ship.Lines[]` indexada por
`Boxes`. **El empalme es SKU -> caja -> fecha de despacho**, dos saltos.

**Correccion 2 — `Box` (pack, singular) contra `Boxes` (ship, plural).** Los dos son
STRING, no listas, y el del ship puede llevar varias etiquetas en un solo texto. Un empalme
por igualdad exacta pierde silenciosamente las lineas de un envio multi-caja.

**Correccion 3 — no hay lectura masiva de fulfilments.** Unleashed tiene
`/SalesShipments?startDate=`; Cin7 no tiene nada equivalente. Hay que llamar `GET /sale?ID=`
una vez por venta. A 60 llamadas por minuto, 500 ventas son ~9 minutos. Eso no es un detalle
de implementacion: es un hecho que el operador tiene que ver, y va en las notas.

**Y lo que se repite:** Cin7 Core tampoco tiene fecha de llegada. `ShipmentDate` es el
DESPACHO. Hay `TrackingNumber` y `TrackingURL`, pero ningun campo de entrega confirmada. O
sea que su OTIF, igual que el de Unleashed y el de Odoo, mide salir a tiempo.

**Una cosa en la que Cin7 SI es mejor que Unleashed**, y es la que el handoff queria decir:
`CombinedShippingStatus == "SHIPPED"` es un estado TERMINAL de la venta entera. Sobre una
venta asi, una linea sin cantidad despachada es un CERO MEDIDO — la venta se cerro sin
servirla — y no una ausencia. Unleashed no tiene con que afirmar eso. Es el unico lugar de
los dos adaptadores donde un `0.0` es honesto, y por eso se implementa explicitamente.

**Diferencia de seguridad, declarada:** la clave de Cin7 VIAJA en una cabecera en cada
request. La de Unleashed firma y no viaja. No es un defecto de este adaptador —- es como
autentica el vendor — pero se dice en el docstring, porque cambia donde hay que auditar.

## File structure

| File | Responsibility |
|---|---|
| `src/connectors/cin7.py` (crear, ~330 lineas) | Auth, paginado, `shipped_by_sku`, `Cin7Connector`, `live_http`, `SERVICE_COVERAGE`. |
| `tests/test_connector_cin7.py` (crear) | Su suite: auth, paginado, 429, el empalme de dos saltos, el cero medido, y los `None`. |
| `tests/test_connector_service_contract.py` (modificar) | Un `AdapterCase` mas. El drift guard lo va a exigir solo. |

---

### Task 1: transporte — auth, paginado y el 429 que no se disfraza

**Files:** Create `src/connectors/cin7.py`, `tests/test_connector_cin7.py`

**Interfaces:**
- Produces: `Cin7Error`, `Cin7RateLimited(Cin7Error)`,
  `Cin7Credentials(account_id, application_key)`, `auth_headers(creds)`,
  `fetch_pages(http, resource, params, creds, *, list_key, max_pages=100) -> list[dict]`.

- [ ] **Step 1: test que falla** — las cabeceras llevan los dos identificadores; el
  paginado camina por `Total`/`Limit` y para; un 429 levanta `Cin7RateLimited` cuyo mensaje
  nombra el limite de 60/min (un rate limit devuelto como lista vacia es una cuenta sin
  ventas); un 401 levanta `Cin7Error`.
- [ ] **Step 2: verlo fallar** (`ModuleNotFoundError`).
- [ ] **Step 3: implementar.**
- [ ] **Step 4: verde + `ruff check .`.**
- [ ] **Step 5: commit con pathspec explicito.**

---

### Task 2: el empalme de dos saltos, como funcion pura

**Files:** Modify `src/connectors/cin7.py`, `tests/test_connector_cin7.py`

**Interfaces:**
- Produces: `_Shipped(qty, dispatched)`, `shipped_by_sku(sale) -> dict[str, _Shipped]`,
  `_box_labels(texto) -> tuple[str, ...]`.

- [ ] **Step 1: test que falla.** Comportamientos:
  1. Una caja empacada y enviada produce cantidad y fecha.
  2. Una caja empacada y NO enviada (`IsShipped: false`) NO cuenta: empacar no es despachar.
  3. Dos fulfilments parciales del mismo SKU se SUMAN.
  4. La fecha es la del ULTIMO despacho de ese SKU.
  5. `"Boxes": "Box 1, Box 2"` empalma con las dos lineas de pack.
  6. Una linea de pack cuya caja no aparece en ningun ship queda fuera.
- [ ] **Step 2-5: fallar, implementar, verde, commit.**

---

### Task 3: `Cin7Connector` — catalogo, stock y ordenes

**Files:** Modify `src/connectors/cin7.py`, `tests/test_connector_cin7.py`

**Interfaces:**
- Produces: `Cin7Connector(http, creds, *, max_pages=100, max_sales=500)` con
  `list_products()`, `inventory_levels()`, `orders(since=None)`, y el atributo `notes`.

- [ ] **Step 1: test que falla.** Comportamientos:
  1. Satisface `InventorySource`.
  2. `list_products` mapea `SKU`/`Name`/`PriceTier1`/`AverageCost`.
  3. `inventory_levels` usa `Available`, no `OnHand` (lo asignado a otras ordenes ya tiene dueno).
  4. `orders(since=)` manda `CreatedSince` al servidor y despues pide `/sale?ID=` por venta.
  5. Una venta `VOIDED` o con `OrderStatus != AUTHORISED` no es demanda.
  6. `promised_date` = `ShipBy`, de la ORDEN, replicada a las lineas; vacia -> `None`.
  7. **`orders()` sola YA trae las tres columnas de servicio.** A diferencia de Unleashed,
     Cin7 no necesita `orders_with_shipments()`: el fulfilment viene inline. Eso hace que
     `scan_service` caiga a `orders()` y aun asi mida todo — la prueba de que la eleccion de
     lectura de `scan_service` no estaba hecha a la medida de Unleashed.
  8. **El cero medido:** sobre una venta con `CombinedShippingStatus == "SHIPPED"`, una linea
     sin cantidad despachada sale `0.0`. Sobre una `PARTIALLY SHIPPED`, sale `None`.
  9. `max_sales` corta y **lo dice en `notes`** — un tope silencioso se lee como cobertura completa.
- [ ] **Step 2-5: fallar, implementar, verde, commit.**

---

### Task 4: la declaracion, y el guard corriendola

**Files:** Modify `src/connectors/cin7.py`, `tests/test_connector_service_contract.py`

- [ ] **Step 1:** correr el guard SIN declaracion y verlo romper solo (drift guard).
- [ ] **Step 2: test que falla** — un `AdapterCase` de Cin7, mas dos aserciones propias:
  que `delivered_date` se declare como despacho y no como llegada (igual que Unleashed), y
  que `delivered_qty` sea `derived` nombrando el empalme Pack+Ship y no un endpoint solo.
- [ ] **Step 3:** escribir `SERVICE_COVERAGE` y `live_http`.
- [ ] **Step 4:** verde, `ruff check .`, suite completa.
- [ ] **Step 5:** commit.

---

## Self-review

**Cobertura.** Las 3 correcciones -> Tasks 2 y 3 (el empalme, el multi-caja, el tope con
nota). El "Cin7 es mejor" del handoff -> Task 3 punto 8, ahora cierto por un motivo
verificable. La generalidad del guard -> Task 3 punto 7 y Task 4.

**Sin placeholders.** Cada task nombra sus comportamientos; el codigo se escribe en la
ejecucion, despues de ver fallar el test.

**Consistencia de tipos.** `_Shipped(qty, dispatched)` con los mismos nombres que en
`unleashed.py`, a proposito: son la misma idea y leerlas distinto invita a divergir.
`fetch_pages` lleva `list_key` (Cin7 nombra la lista por recurso) donde la de Unleashed no
lo necesita — son dos funciones distintas en dos modulos distintos, no una compartida
forzada; la firma se parece porque el problema se parece, y unificarlas hoy ataria dos
vendors que paginan distinto.
