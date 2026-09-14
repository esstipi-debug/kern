# Handshake: adaptadores de canal (Unleashed/Cin7) + guard de cobertura de servicio

Fecha: 2026-08-29 | Worktree: `C:\Users\Gamer\Music\scm\supply-chain-optimization` | Rama: `feat/espejo` @ `58c39a0`

## 1. Objetivo de la proxima sesion

Escribir `src/connectors/unleashed.py` (adaptador `InventorySource`) y el guard
`tests/test_connector_service_contract.py` que obliga a todo adaptador a declarar,
campo por campo, que parte del servicio al cliente puede medir. Rama nueva cortada
de `main`, no de `feat/espejo`.

## 2. Estado de juego

**Hecho y verificado en esta sesion (2026-08-29):**

- Competencia mapeada en 3 anillos con fuentes primarias (URLs en seccion 7):
  - Anillo 1 (la plataforma): Cin7 ForesightAI (Inventoro absorbido, nativo) y
    Unleashed AIM (incluido en Core y Pro; en 2026 ya crea OC/transferencias desde
    la pestana de reposicion). Las DOS plataformas del canal elegido shippean la capa.
  - Anillo 2 (misma forma que Kern): Netstock (sin trial, cotizacion cerrada, anual,
    fee de onboarding aparte) y **StockTrim** (neozelandes, USD 298/mes a 3-5M de
    facturacion, SKUs/usuarios ilimitados, 14 dias sin tarjeta).
  - Anillo 3 (DTC/Shopify): Prediko 49, Fabrikator 99, Cogsy 199, Inventory Planner
    Essentials ~120. Fijan la expectativa de precio del comprador SMB.
- **StockTrim NO puede medir servicio outbound.** Verificado contra su OpenAPI
  publica: `SalesOrderRequestDto` = productId, orderDate, quantity, unitPrice,
  location*, customer*. Sin fecha prometida, sin fecha de despacho, sin cantidad
  entregada, sin estado ni backorder. En los 23 endpoints no hay ninguna entidad de
  despacho / entrega / fulfilment. Su `PurchaseOrderLineItem` SI trae `etaDate` +
  `receivedDate`: el lado ENTRANTE lo tienen, igual que Netstock.
  Reproducible: fetch de `https://api.stocktrim.com/swagger/v1/swagger.json`.
- **Unleashed expone las fechas pero no la cantidad despachada por linea.**
  `SalesOrder.RequiredDate` (promesa), `CompletedDate` (cumplimiento), `OrderStatus`
  incluye `Backordered`; a nivel linea solo `OrderQuantity`. No hay ShippedQuantity.
- **Cin7 Core si tiene cantidad despachada**: endpoints `sale-fulfilment` /
  `-pick` / `-pack` / `-ship`, con despacho multiple (parciales) y `ShipmentDate`.
- Verificado EN CODIGO que la mitad cara ya existe:
  - `src/connectors/__init__.py` - protocolo `InventorySource` y `OrderLine` con
    `delivered_qty` / `promised_date` / `delivered_date` **opcionales**, con la
    semantica de `None` documentada (None != 0 != cantidad pedida).
  - `src/order_fulfillment.py` - `RealizedService`: mide fill rate, OTIF saliente,
    dias sin servir y venta perdida, con `provenance` y `not_measured`. La linea 101
    dice explicito que ese OTIF es al CLIENTE y que el de `supplier_scorecard` es el
    espejo entrante.
  - `src/connectors/odoo.py:1057` - `create_draft_purchase_orders`: el write-back de
    referencia, sobre el plano de staging de `nosilo_core.writeback`.
  - `webapp/pricing_quote.py` - ya cotiza por banda de facturacion (+ SKUs).
  - `webapp/billing.py:15` - `create_checkout_session(offer_slug, sku_count)`.
  - `webapp/shopify_app.py` - OAuth + webhooks GDPR + Billing API completos.
- BWM (hilo previo de esta sesion): `src/mcdm.py` tiene `bwm_weights` correcto y
  testeado, **sin ningun llamador de produccion**. Los jobs usan pesos fijos
  (`jobs/sourcing_job.py:71`) que contradicen a `src/decision_support.py:360` sobre
  los mismos 4 criterios. Corrida real: pasar de pesos hardcodeados a pesos BWM
  **cambia el ganador** del award. Ademas el consistency ratio divide el xi del
  modelo LINEAL por el CI de la tabla del modelo NO LINEAL (Rezaei 2015): un input
  deliberadamente contradictorio saco CR 0,067, o sea el chequeo no rechaza nada.

**En vuelo:** nada. Cero lineas de codigo escritas para este hilo.

**Bloqueado:** nada tecnico. Lo abierto es de precio (decision D2) y no frena el
codigo: frena el valor del codigo.

## 3. Estado del arbol

- Sin commitear: `.mcp.json` (1 linea modificada) y `data/bateria/` sin trackear.
  Ninguno pertenece a este hilo. **No arrastrarlos a la rama nueva.**
- **OTRA SESION COMPARTE ESTE WORKTREE** ("HANDOFF investigation", corriendo hoy,
  sobre un hilo de precios: `banda_prohibida()`, piso, umbral). Re-correr
  `git status` JUSTO ANTES de tocar o commitear, no solo al empezar.
- Stashes: 4, ninguno de este hilo (el mas nuevo, `feat/retencion-de-datos`).
- PR: ninguno para `feat/espejo`. Divergida 24/24 contra main
  (`git rev-list --left-right --count origin/main...HEAD`).
- Comando de verificacion del estado actual:

```
cd C:/Users/Gamer/Music/scm/supply-chain-optimization && .venv/Scripts/python.exe -m pytest tests/test_order_fulfillment.py tests/test_mcdm.py -q
```

## 4. Decisiones abiertas

**D1 - Cual adaptador primero.**
- (A) **Unleashed primero** *(recomendada)*: las dos fechas mapean limpio, o sea que
  el OTIF saliente -lo unico que verificamos que nadie mide- funciona el dia 1, con
  el adaptador mas barato. Costo: fill rate incompleto (`delivered_qty=None`).
- (B) Cin7 primero: `sale-fulfilment` da la cantidad despachada, o sea el numero
  completo y la demo mas contundente. Costo: mapeo multi-fulfilment, mas trabajo.
- (C) Los dos en paralelo, en worktrees separados. Costo: dos superficies nuevas sin
  el guard todavia escrito.

**D2 - Precio de entrada AU/NZ.** Hoy Starter arranca en USD 900/mes (piso, 500 SKUs,
techo 1.500) sobre un ERP que cuesta 349-399, contra StockTrim 298 con trial gratis.
El tier de 250-300 esta marcado en `scm_agent/package_specs.py:403` como **solo LatAm
y menos entrega**, asi que en AU/NZ no existe.
- (A) Banda self-serve publica ~250-400 para AU/NZ reusando `pricing_quote`.
- (B) Sostener 900 y competir por profundidad: exige que el servicio medido sea la
  primera pantalla, no una feature.
- (C) **Trial de 14 dias sin tarjeta y despues 900** *(recomendada como primer paso:
  es un parametro de Stripe mas entitlements, no toca el precio)*.

**D3 - Rama base.** (A) **cortar de `main`** *(recomendada)* · (B) de `feat/espejo`,
que arrastra 24 commits sin PR.

**D4 - Que hacer con BWM.** (A) cablear a `sourcing_job` y arreglar el CR ·
(B) **dejarlo donde esta** *(recomendada: no es el cuello de este hilo)* ·
(C) solo arreglar el CR y su test.

## 5. Trampas ya pisadas

- **No delegar en subagentes ni workflows** era regla de la sesion previa. Si la
  sesion nueva no la tiene, vuelve a aplicar `kern-usar-cavecrew-siempre`.
- **Crear la cuenta del trial de StockTrim no lo hace el agente.** Si hace falta
  entrar, lo hace el usuario. La evidencia dura salio del swagger publico, que es
  mejor prueba y no necesita cuenta.
- Un `grep -r` sobre todo `C:\Users\Gamer\Music\scm` supera los 120s y se va a
  background. Acotar a `supply-chain-optimization`.
- No hay `jq` en Git Bash.
- venv por worktree: usar el `.venv/Scripts/python.exe` del worktree, no el del padre.
- Heredocs largos con backticks fallaron en Git Bash en esta sesion; para archivos
  largos usar la herramienta de escritura, no `cat <<EOF`.
- No asumir que algo con tests esta en produccion: `bwm_weights` tiene 6 tests y
  cero llamadores.
- `graphify` (MCP) no conecta en esta maquina: `CONNECTION_CLOSED`.

## 6. Skills a usar

- `superpowers:writing-plans` - el plan del adaptador, antes de tocar codigo.
- `superpowers:test-driven-development` - el guard de cobertura se escribe ANTES que
  el adaptador; es lo unico que impide que copiar disuelva el diferenciador.
- `superpowers:brainstorming` - solo si se abre diseno nuevo (D2 es producto, no codigo).
- `anthropic-skills:cavecrew` - delegacion en este repo, si la sesion nueva la permite.

## 7. Artefactos

- Protocolo y DTOs: `src/connectors/__init__.py`
- Motor de servicio realizado: `src/order_fulfillment.py` (ver linea 101)
- Write-back de referencia: `src/connectors/odoo.py:1057`
- App de marketplace de referencia: `webapp/shopify_app.py`
- Cotizacion por banda: `webapp/pricing_quote.py` · checkout: `webapp/billing.py:15`
- Precios de paquetes: `scm_agent/package_specs.py` (370, 403, 416)
- MCDM: `src/mcdm.py` · pesos en conflicto: `jobs/sourcing_job.py:71` vs
  `src/decision_support.py:360`
- Fuentes externas verificadas hoy:
  - https://api.stocktrim.com/swagger/v1/swagger.json
  - https://apidocs.unleashedsoftware.com/SalesOrders
  - https://help.core.cin7.com/hc/en-us/articles/9034603570831-SaleShip
  - https://help.core.cin7.com/hc/en-us/articles/9034450166287-Advanced-Sales-Multiple-Fulfillment
  - https://www.unleashedsoftware.com/advanced-inventory-manager/
  - https://www.cin7.com/foresightai/
  - https://www.netstock.com/solutions/performance-analysis/
  - https://www.stocktrim.com/pricing

## 8. Cerrado el 2026-08-29 (rama `feat/unleashed-cobertura-servicio`, cortada de `main`)

Entregado: `src/connectors/unleashed.py` (adaptador `InventorySource`),
`tests/test_connector_unleashed.py` (27 tests), `tests/test_connector_service_contract.py`
(el guard, 25 tests) y la declaracion `SERVICE_COVERAGE` en los CINCO modulos de
`src/connectors/` que leen ordenes. Plan:
`docs/superpowers/plans/2026-08-29-unleashed-y-cobertura-de-servicio.md`.

**Dos correcciones a la seccion 2 de este mismo documento**, verificadas contra la doc del
vendor y ahora fijadas en tests que fallan si alguien las revierte:

1. Unleashed **SI** expone cantidad despachada por linea, en `/SalesShipments` ->
   `SalesShipmentLines[].ShipmentQty`, empalmable por `(OrderNumber,
   SalesOrderLineNumber)`. La seccion 2 decia lo contrario; es cierto de `/SalesOrders` y
   falso de la API entera. **D1 (Unleashed primero) no cambia** -- cambia su costo
   declarado: el fill rate NO queda incompleto si se leen los despachos.
   `orders()` sigue dejando `delivered_qty=None` a proposito (es la respuesta honesta
   cuando no se leyeron despachos); `orders_with_shipments()` es la lectura completa.
2. Unleashed **no tiene ninguna fecha de llegada**. `CompletedDate` y `DispatchDate` son
   las dos eventos de SALIDA, y el indice de endpoints no tiene recurso de entrega. Su
   OTIF es "despachado antes de la fecha pedida", nunca "llegado antes de". Fijado en
   `test_unleashed_declares_its_delivery_date_as_a_dispatch_and_not_an_arrival`.

**Que es exactamente el guard.** Un `SERVICE_COVERAGE` por adaptador, con
`status`/`source`/`means` para cada una de las tres columnas de servicio; el guard lo
EJECUTA contra el adaptador sobre una fixture y falla en las dos direcciones -- un campo
declarado ausente que vuelve con valor, y uno declarado medible que la fixture nunca
produce. Dos tests mas lo hacen fallar a proposito contra declaraciones mentirosas, para
que el guard se haya visto fallar antes de confiarse en que pasa. Y un drift guard lista
los modulos que definen `orders()` y exige que cada uno este declarado o excluido por
nombre -- ya funciono solo: al aparecer `unleashed.py` sin declaracion, rompio.

**Sin tocar, a proposito:** D2 (precio de entrada AU/NZ -- sigue abierta y es del
usuario; ningun archivo de precios se toco), D4 (BWM se queda donde esta: `src/mcdm.py` y
`jobs/sourcing_job.py` intactos), `.mcp.json` y `data/bateria/` (de otra sesion que
comparte el worktree).

**Lo que sigue, si se continua este hilo:** (a) Cin7 Core como segundo adaptador -- tiene
`sale-fulfilment`/`-ship` con `ShipmentDate`, y ahora tendria que declarar su cobertura
igual que este; (b) cablear `orders_with_shipments()` a un job/tool para que el numero
llegue a un entregable, que hoy no pasa: el adaptador existe y nadie lo llama todavia.
