# Connected systems: que lee y que escribe Kern en cada sistema

> GENERADO por `python scripts/connected_systems.py` desde `SERVICE_COVERAGE` y
> `WRITE_SURFACE` de cada modulo de `src/connectors/`. No editar a mano: el test
> `tests/test_connector_write_surface.py` falla si este archivo no coincide con lo
> que el codigo declara.

Leer con esta clave. **Medido** es un dato que el sistema del cliente registra;
**derivado** es un numero que Kern calcula a partir de lo registrado y dice como;
**no disponible** es lo que ese sistema no tiene, y la frase dice que tiene en su
lugar. Del lado de escritura no hay medias tintas: o **solo lectura**, o **escribe
con aprobacion** (dry-run, aprobacion firmada, idempotencia y rollback).

Una aclaracion que esta tabla no cubre: durante el onboarding asistido, el agente
de setup puede mirar y operar la PANTALLA del cliente (`src/connectors/browser/`),
acotado a una lista de dominios permitidos y con los datos sensibles tachados. Eso
no pasa por el contrato de escritura de abajo porque no habla con ninguna API: es
una persona-maquina usando la interfaz, con el cliente presente.

## Resumen

| Sistema | Fill rate | OTIF: prometida | OTIF: entrega | Escribe |
|---|---|---|---|---|
| Odoo (ERP) | medido | medido | derivado | con aprobacion |
| Unleashed (ERP de inventario) | derivado | medido | derivado | solo lectura |
| Cin7 Core (ERP de inventario) | derivado | medido | derivado | solo lectura |
| Planilla Excel del cliente | no lee pedidos | no lee pedidos | no lee pedidos | con aprobacion |
| Shopify (inventario) | no lee pedidos | no lee pedidos | no lee pedidos | con aprobacion |
| Starshipit (transportista) | no disponible | no disponible | medido | solo lectura |
| Sendle (transportista) | no disponible | no disponible | medido | solo lectura |
| Shippit (transportista) | no disponible | no disponible | medido | solo lectura |
| Olist (dataset publico) | derivado | medido | medido | solo lectura |
| API REST generica de tienda | medido | medido | medido | solo lectura |
| Otro servidor MCP como fuente | medido | medido | medido | solo lectura |
| Odoo (precios) | no lee pedidos | no lee pedidos | no lee pedidos | con aprobacion |
| Shopify (precios) | no lee pedidos | no lee pedidos | no lee pedidos | con aprobacion |
| MercadoLibre (precios) | no lee pedidos | no lee pedidos | no lee pedidos | con aprobacion |
| MercadoLibre (catalogo oficial) | no lee pedidos | no lee pedidos | no lee pedidos | solo lectura |
| Tienda simulada (banco de pruebas) | medido | medido | medido | con aprobacion |

## Odoo (ERP)

Fuente: `Odoo (XML-RPC, modelos estandar de Ventas e Inventario)`

| Campo | Estado | Campo del vendor | Que es el numero |
|---|---|---|---|
| cantidad entregada (fill rate) | medido | `sale.order.line.qty_delivered` | unidades entregadas de la linea; el campo llega con el puente Ventas-Inventario, y sin ese puente el servicio sale no medido en vez de cero |
| fecha prometida (OTIF) | medido | `sale.order.commitment_date` | la fecha comprometida de la ORDEN, replicada a sus lineas. En blanco es lo habitual, y saca la linea de OTIF en vez de medirla contra date_order |
| fecha de entrega (OTIF) | derivado | `stock.move (state=done, picking_type=outgoing) -> max(date) por linea` | la fecha del ULTIMO despacho que sirvio la linea. Es DESPACHO, no llegada: Odoo no registra la recepcion del cliente, asi que este OTIF mide salir a tiempo del deposito y no llegar a tiempo |

**Escribe, con aprobacion:**
- `stock.warehouse.orderpoint.product_min_qty`
- `stock.warehouse.orderpoint.product_max_qty y trigger=manual (politica min/max)`
- `purchase.order (estado borrador, con sus purchase.order.line)`

los reorder points se escriben con aprobacion firmada, idempotencia y trigger manual; el rollback restaura min/max de una regla que existia, pero una regla NUEVA queda en Odoo (hay que archivarla a mano); las ordenes de compra quedan EN BORRADOR y las confirma un comprador en Odoo (irreversibles: aprobacion con nombre obligatoria). Cada escritura deja fila en el ledger.

## Unleashed (ERP de inventario)

Fuente: `Unleashed (API v1: /SalesOrders + /SalesShipments)`

| Campo | Estado | Campo del vendor | Que es el numero |
|---|---|---|---|
| cantidad entregada (fill rate) | derivado | `SalesShipmentLine.ShipmentQty, sumada por (OrderNumber, SalesOrderLineNumber)` | unidades que SALIERON de la linea, sumando despachos parciales. No esta en /SalesOrders: sin leer /SalesShipments queda en None, que es 'no lo medimos' y no un 0% de fill rate |
| fecha prometida (OTIF) | medido | `SalesOrder.RequiredDate` | la fecha que el cliente pidio, a nivel ORDEN y replicada a sus lineas. Vacia es lo habitual, y saca la linea de OTIF en vez de medirla contra OrderDate |
| fecha de entrega (OTIF) | derivado | `SalesShipment.DispatchDate del ultimo despacho de la linea (SalesOrder.CompletedDate cuando no se leyeron despachos)` | la fecha de DESPACHO, no de llegada. Unleashed no registra ninguna llegada: no tiene recurso de entrega y sus dos fechas son salidas. El OTIF que sale de aca mide despachar antes de la fecha pedida, no llegar antes de ella |

**Solo lectura.** solo lectura. Unleashed expone /PurchaseOrders para escribir, pero Kern no lo usa hasta que un cliente pago lo pida (peldano 2 del plan): hoy la PO sale como CSV/mail y se dice.

## Cin7 Core (ERP de inventario)

Fuente: `Cin7 Core (API v2: /saleList + /sale, fulfilments inline)`

| Campo | Estado | Campo del vendor | Que es el numero |
|---|---|---|---|
| cantidad entregada (fill rate) | derivado | `empalme Fulfilments[].Pack.Lines[] (SKU->Box->Quantity) contra Fulfilments[].Ship.Lines[] (Boxes->IsShipped)` | unidades que SALIERON de la linea. NINGUN endpoint la devuelve: Cin7 empaca y despacha por caja, y la etiqueta de la caja es la unica llave entre el SKU y la fecha. Se suman los despachos parciales y no se cuenta lo solo empacado. Sobre una venta con el despacho ya cerrado (CombinedShippingStatus=SHIPPED) una linea sin despachar es un CERO medido, no una ausencia -- el unico backend de este repo con evidencia terminal para afirmarlo |
| fecha prometida (OTIF) | medido | `Sale.ShipBy` | la fecha en que el despacho vence, a nivel ORDEN y replicada a sus lineas. Vacia es lo habitual, y saca la linea de OTIF en vez de medirla contra SaleOrderDate |
| fecha de entrega (OTIF) | derivado | `Fulfilments[].Ship.Lines[].ShipmentDate de la ultima caja que llevo el SKU` | la fecha de DESPACHO, no de llegada. Cin7 Core tiene TrackingNumber y TrackingURL pero ningun campo de entrega confirmada, asi que el OTIF que sale de aca mide despachar antes de la fecha pedida, no llegar antes de ella |

**Solo lectura.** solo lectura. La API de Cin7 Core permite crear ordenes de compra, pero Kern no escribe ahi hasta que un cliente pago lo pida (peldano 2 del plan): hoy la PO sale como CSV/mail y se dice.

## Planilla Excel del cliente

**Escribe, con aprobacion:**
- `celdas de reorder point del libro del cliente (.xlsx/.xlsm)`

escribe celda a celda con chequeo de deriva contra el archivo vivo, escritura atomica, respaldo por commit y rollback celda a celda; las macros de un .xlsm se conservan y nunca se ejecutan.

## Shopify (inventario)

**Escribe, con aprobacion:**
- `inventoryLevel (adjust / set) por ubicacion`

la escritura existe en el conector pero la superficie web/MCP de Kern es de solo lectura sobre Shopify por politica (dos tests lo vigilan); solo un operador con credenciales explicitas la usa.

## Starshipit (transportista)

Fuente: `Starshipit (plataforma de envios AU/NZ; transportista, NO sistema de registro)`

| Campo | Estado | Campo del vendor | Que es el numero |
|---|---|---|---|
| cantidad entregada (fill rate) | no disponible | - | un transportista no sabe cuantas unidades se pidieron ni cuantas se sirvieron: mueve bultos, no lineas de pedido. Este dato lo tiene que poner el ERP |
| fecha prometida (OTIF) | no disponible | - | la fecha prometida al cliente se pacta en la venta, no en el envio. Starshipit conoce la fecha de la etiqueta (shipment_date), que es cuando el vendedor decidio despachar -- no lo que le prometio al comprador |
| fecha de entrega (OTIF) | medido | `GET /api/track -> results.tracking_events[] con status='Delivered' -> event_datetime` | la fecha en que el transportista dio por ENTREGADO el envio: el pedido llego al cliente. Es el dato que ningun ERP del canal registra, y sale del evento y no de last_updated_date, que es la ultima actualizacion de cualquier tipo |

**Solo lectura.** solo lectura: un transportista sabe cuando llego un envio y nada mas; Kern lee llegadas para medir OTIF de llegada y no escribe envios.

## Sendle (transportista)

Fuente: `Sendle (transportista AU/NZ; NO es sistema de registro)`

| Campo | Estado | Campo del vendor | Que es el numero |
|---|---|---|---|
| cantidad entregada (fill rate) | no disponible | - | un transportista mueve bultos, no lineas de pedido: la respuesta de tracking no trae ni SKU ni cantidad. Cuanto se pidio y cuanto se sirvio lo pone el ERP |
| fecha prometida (OTIF) | no disponible | - | la fecha prometida al cliente se pacta en la venta, no en el envio. Sendle publica una estimacion de transito al cotizar, que no es lo que el vendedor le prometio al comprador, y por eso no se usa para nada |
| fecha de entrega (OTIF) | medido | `GET /api/tracking/{sendle_reference} -> tracking_events[] con event_type='Delivered' -> scan_time` | la fecha en que el transportista dio por ENTREGADO el envio: el pedido llego al cliente. Sale del evento y NO del campo state, que dice correctamente que llego pero no trae marca de tiempo -- un estado no es una fecha |

**Solo lectura.** solo lectura: transportista; Kern lee llegadas y no crea ni modifica envios.

## Shippit (transportista)

Fuente: `Shippit (plataforma de envios AU/NZ; transportista, NO sistema de registro)`

| Campo | Estado | Campo del vendor | Que es el numero |
|---|---|---|---|
| cantidad entregada (fill rate) | no disponible | - | un transportista mueve bultos, no lineas de pedido: la respuesta de tracking no trae ni SKU ni cantidad. Cuanto se pidio y cuanto se sirvio lo pone el ERP |
| fecha prometida (OTIF) | no disponible | - | la fecha prometida al cliente se pacta en la venta, no en el envio. Shippit publica expected_delivery_date, que es SU estimacion de transito -- no lo que el vendedor le prometio al comprador -- y por eso no se usa para nada |
| fecha de entrega (OTIF) | medido | `GET /orders/{tracking_number}/tracking -> response.track[] con status='Completed' -> date` | la fecha en que el transportista dio por ENTREGADO el envio: el pedido llego al cliente. Sale del evento y NO de expected_delivery_date, que es la estimacion de Shippit y daria un OTIF perfecto por construccion |

**Solo lectura.** solo lectura: transportista; Kern lee llegadas y no crea ni modifica envios.

## Olist (dataset publico)

Fuente: `Olist (dataset publico de un marketplace real; NO es un sistema de registro)`

| Campo | Estado | Campo del vendor | Que es el numero |
|---|---|---|---|
| cantidad entregada (fill rate) | derivado | `orders.order_status (delivered -> 1; canceled/unavailable -> 0; el resto -> None)` | unidades entregadas de la linea, deducidas del estado. El dataset no trae cantidad: cada item es una unidad. Solo los estados TERMINALES producen un cero medido; una orden en transito viaja None porque todavia puede llegar |
| fecha prometida (OTIF) | medido | `orders.order_estimated_delivery_date` | la fecha de entrega que se le mostro al comprador al comprar |
| fecha de entrega (OTIF) | medido | `orders.order_delivered_customer_date (o order_delivered_carrier_date con delivery='carrier', solo para medir la brecha)` | la fecha en que el pedido LLEGO al cliente. Es la unica llegada real de este repo fuera de Shopify: Unleashed, Cin7 Core y Odoo solo registran la salida del deposito, y por eso su OTIF mide despacho y no entrega |

**Solo lectura.** solo lectura: es un dataset publico (Olist, Brasil 2016-2018), no un sistema vivo; sirve para medir OTIF de llegada contra despacho, nunca para escribir.

## API REST generica de tienda

Fuente: `commerce HTTP store API (offline: connectors.emulator)`

| Campo | Estado | Campo del vendor | Que es el numero |
|---|---|---|---|
| cantidad entregada (fill rate) | medido | `GET /admin/orders -> line_items[].delivered_qty` | units the store reported shipping; JSON null round-trips to None, never to 0.0 |
| fecha prometida (OTIF) | medido | `GET /admin/orders -> line_items[].promised_date` | the delivery date the store says it promised the customer |
| fecha de entrega (OTIF) | medido | `GET /admin/orders -> line_items[].delivered_date` | the date the store says the line reached the customer |

**Solo lectura.** solo lectura: adaptador generico sobre una API REST de tienda; el lado de escritura de un backend nuevo se declara en su propio modulo, no aca.

## Otro servidor MCP como fuente

Fuente: `servidor MCP remoto (esquema de tool, no un vendor fijo)`

| Campo | Estado | Campo del vendor | Que es el numero |
|---|---|---|---|
| cantidad entregada (fill rate) | medido | `tool de ordenes -> lines[].delivered_qty` | unidades que el servidor dice haber servido; ausente -> None, nunca rellenada con la cantidad pedida |
| fecha prometida (OTIF) | medido | `tool de ordenes -> lines[].promised_date` | la fecha de entrega que el servidor dice haber prometido |
| fecha de entrega (OTIF) | medido | `tool de ordenes -> lines[].delivered_date` | la fecha en que el servidor dice que la linea se sirvio |

**Solo lectura.** solo lectura: otro servidor MCP como fuente de productos, stock y pedidos. Kern nunca invoca herramientas de escritura de un MCP ajeno.

## Odoo (precios)

**Escribe, con aprobacion:**
- `product.template.list_price`

cambio de precio con dry-run, aprobacion firmada y rollback; lo consume el playbook de repricing, que ademas exige >=2 citas del grafo antes de escribir (gate de precios).

## Shopify (precios)

**Escribe, con aprobacion:**
- `productVariant.price`

cambio de precio por variante con dry-run, aprobacion firmada y rollback; mismo gate de precios que Odoo.

## MercadoLibre (precios)

**Escribe, con aprobacion:**
- `items/{id}.price`

cambio de precio de publicacion con dry-run, aprobacion firmada y rollback; mismo gate de precios. Nunca se escribe un precio a un competidor ni a un marketplace ajeno.

## MercadoLibre (catalogo oficial)

**Solo lectura.** solo lectura del catalogo oficial de MercadoLibre (/products/search, /categories). La mitad de ESCRITURA de preventa (publicar o modificar una publicacion) no vive aca: los cambios de precio van por meli_prices.py, con dry-run y aprobacion firmada. Buscar un producto en el catalogo ajeno no cambia nada en ningun sistema.

## Tienda simulada (banco de pruebas)

Fuente: `simulator (offline stand-in, NOT a system of record)`

| Campo | Estado | Campo del vendor | Que es el numero |
|---|---|---|---|
| cantidad entregada (fill rate) | medido | `OrderLine.delivered_qty` | units the simulated store shipped; invented by demo_store(), never observed |
| fecha prometida (OTIF) | medido | `OrderLine.promised_date` | order date + 3 days, the lead time demo_store() pretends to have promised |
| fecha de entrega (OTIF) | medido | `OrderLine.delivered_date` | the promise, plus two days on part of the lines so the demo is not perfect |

**Escribe, con aprobacion:**
- `reorder points del almacen simulado`

banco de pruebas sin credenciales: escribe reorder points en memoria por el mismo camino de dry-run + aprobacion + rollback que un backend real, para que el circuito se pueda demostrar sin un sistema vivo.
