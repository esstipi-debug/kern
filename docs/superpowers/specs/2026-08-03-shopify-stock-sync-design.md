# Sync de stock Odoo → Shopify (B1)

Fecha: 2026-08-03 · Estado: diseño aprobado, sin implementar

Primero de los tres entregables del sync continuo (B). Los otros dos —precios (B2) y
pedidos (B3)— dependen de este y tienen su propio spec.

**Depende de A** ([resiliencia de versión de API](2026-08-02-shopify-api-version-resilience-design.md),
PR [kern#222](https://github.com/esstipi-debug/kern/pull/222)): `set_inventory` exige ahora
una clave de idempotencia. Esta rama sale de la de A, no de `main`.

## 1. Qué problema resuelve

Hay dos conectores que leen y escriben los mismos SKUs, y nada que los mantenga de acuerdo.
Si Odoo dice 40 y Shopify dice 37, hoy nadie se entera hasta que un cliente compra la unidad
38. El caso que duele es la sobreventa.

## 2. Decisiones tomadas

| Decisión | Valor | Por qué |
|---|---|---|
| Fuente de verdad | **Odoo manda, Shopify obedece** | El stock real vive en el ERP: compras, recepciones, ajustes, almacenes. Flujo en una sola dirección |
| Autonomía | **T2 — propone, un humano aprueba** | «La autonomía se gana con evidencia medida, nunca declarada». Se empieza midiendo aciertos y se sube a T1 cuando los datos lo respalden |
| Cadencia | La del endpoint programado que ya existe | No se construye scheduler |
| Watermark | **Ninguno** | El stock es comparación de estado completo, no un flujo de eventos. El watermark corresponde a B3 |

## 3. Lo que NO se construye

Casi todo el mecanismo ya existe en el repo. Este spec añade un job, no una plataforma.

| Pieza | Dónde está ya |
|---|---|
| Scheduler | GitHub Actions `cron` → `POST /api/jobs/run-scheduled`. El scheduler en proceso fue rechazado a propósito (`WEB_CONCURRENCY=1`, VM de 512mb), y hay un suelo de intervalo mínimo que hace inofensivo un tick duplicado. El propio endpoint documenta la intención: *«continuo degrada a batch»* ([webapp/app.py:979](../../../webapp/app.py)) |
| Deduplicación | `EventLedger`, idempotente sobre SQLite, con ruta inyectable |
| Ruteo y niveles de autonomía | `config/event_routing.yaml` — ruteo como dato, sin cambios de código para añadir una ruta |
| Staging, aprobación firmada y rollback | `nosilo_core/writeback.py` |
| Aborto ante deriva de versión | A, ya construido |

## 4. Diseño

### El job

`jobs/shopify_stock_sync.py`. No construye conectores ni clientes: los recibe, como el resto
de `src/connectors/`. Cinco pasos y ninguno más:

1. Recibe el estado deseado, `desired: dict[str, float]` (SKU → cantidad).
2. Lee el estado actual de Shopify, SKU a SKU, vía `ShopifyInventoryStore.read`.
3. Compara y arma los `edits` de los que divergen.
4. `writeback.stage(...)` → **un solo `Changeset` por corrida**, con todos los SKUs divergentes
   dentro. No uno por SKU. **No escribe nada**: `stage` es dry-run por contrato.
5. Emite un `Event` con el resumen y la clave de idempotencia del changeset.

**Quién calcula `desired`.** El job no lo hace. Lo calcula el cableado del endpoint programado,
que lee `OdooConnector.inventory_levels()` y se lo pasa. Así el job queda puro y testeable sin
Odoo, y B2 puede reutilizarlo pasándole la salida del repricing.

**De dónde sale la clave de idempotencia.** `writeback.stage` la recibe como parámetro, así que
hay que elegirla. Se deriva de forma determinista del contenido: un hash estable de los `edits`
ordenados, con prefijo `shopify-stock-sync:`. Consecuencia deseada: si la misma divergencia se
vuelve a estagear en el siguiente tick, la clave es la misma y `claim()` la rechaza como
duplicada. La idempotencia queda cubierta en las dos capas —writeback y ledger de eventos— y
no solo en la de eventos.

### El seam para B2

El job recibe `desired` **ya calculado**, no un conector. Para B1 se lo pasa
`OdooConnector.inventory_levels()`; para B2 se lo pasará el motor de repricing, que **calcula**
el precio en vez de leerlo de un sistema. Es la generalización más barata que sirve a ambos:
un parámetro, sin protocolo ni clase intermedia.

Esto es deliberado y no es generalidad especulativa: B2 ya está comprometido, y la decisión de
que kern es el dueño del precio ya está tomada.

### Deduplicación

El `dedup_key` del evento es el **`content_hash` del changeset**, que `writeback` ya calcula
sobre `(target, risk_tier, changes)`.

Consecuencia: si la divergencia persiste, el siguiente tick del cron produce el mismo hash y
el ledger lo deduplica solo. Si la divergencia cambia, el hash cambia y merece un evento nuevo.
No hay que inventar ninguna ventana temporal ni llevar estado propio.

### Tipo de evento y ruteo

Tipo: `shopify_stock_divergence`, siguiendo la convención de los monitores existentes
(`rop_breach`, `stockout_projected`, `excess_growing`).

Una entrada nueva en `config/event_routing.yaml` lo rutea a tier **T2**. El payload lleva la
clave de idempotencia del changeset, el número de SKUs divergentes y la magnitud total, para
que la superficie de aprobación pueda mostrar qué se va a aplicar sin releer nada.

### Registro en el endpoint programado

Se añade el job a la lista que corre `POST /api/jobs/run-scheduled`, junto a `PRICE_MONITOR_JOB`
y `PRICE_WATCH_JOB`. **No se crea un workflow nuevo**: el cron existente ya dispara el endpoint.

## 5. Errores

| Situación | Comportamiento |
|---|---|
| SKU presente en Odoo y ausente en Shopify | Se reporta en el resultado y se salta. No tumba la corrida: un catálogo desalineado es normal |
| Shopify no responde | La corrida falla. El siguiente tick reintenta; el suelo de intervalo del endpoint hace inofensivo el reintento |
| Deriva de versión de API | A ya aborta la escritura. Aquí no se añade nada |
| `desired` vacío | Corrida válida sin cambios. No es un error, y no debe emitir evento |

## 6. Tests

Offline, sin red, con el patrón del repo: `demo_odoo()` contra `InMemoryShopifyInventory` y
`EventLedger(":memory:")`.

| Caso | Espera |
|---|---|
| Sin divergencia | Ningún evento, ningún changeset |
| Con divergencia | Changeset en staging, evento emitido, **Shopify sin tocar** |
| El mismo desajuste dos veces | Un solo evento (dedup por `content_hash`) |
| Una divergencia distinta tras la primera | Segundo evento |
| SKU desconocido en Shopify | Se reporta, el resto de SKUs sigue procesándose |
| `desired` vacío | Sin evento |
| Tras aprobar y aplicar | Shopify queda en el valor de Odoo, y el rollback lo revierte |

## 7. Fuera de alcance

- **Precios (B2) y pedidos (B3)**, cada uno con su spec. B3 requiere construir la creación de
  `sale.order` en Odoo, que hoy no existe.
- **Reparación automática de deriva (C)**: aquí se detecta y se propone; C es quien decide
  reparar sin intervención.
- **`ignoreCompareQuantity: True`**, que sigue hardcodeado en `set_inventory` y desactiva el
  compare-and-swap de Shopify. Pertenece a C.

## 8. Lo que este trabajo no verificará

Como en A: todo el testing es offline. Que el sync funcione contra una tienda real y una
instancia de Odoo reales queda sin medir hasta que haya credenciales. No debe describirse como
comprobado hasta entonces.
