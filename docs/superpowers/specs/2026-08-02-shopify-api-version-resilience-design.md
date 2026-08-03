# Resiliencia de versión de API en los conectores de Shopify

Fecha: 2026-08-02 · Estado: diseño aprobado, sin implementar

Primer entregable (A) de una serie de cuatro sobre la integración Shopify↔Odoo.
Los otros tres —sync continuo (B), self-healing de deriva (C), auto-update del conector (D)—
dependen de este y tienen su propio spec.

## 1. El problema

Los dos conectores de Shopify fijan la versión de API a mano:

```python
# src/connectors/shopify_inventory.py:28  y  src/connectors/shopify_prices.py:52
_DEFAULT_API_VERSION = "2024-10"
```

La constante está **duplicada**, que es la forma habitual de que un pin se actualice en un
archivo y no en el otro.

`2024-10` está fuera de soporte desde ~octubre de 2025. Shopify soporta cada versión un mínimo
de 12 meses y publica una nueva cada trimestre.

**Lo importante: Shopify no falla ante una versión retirada.** Hace *fall-forward* — sirve la
petición con la stable accesible más antigua y responde 200. Hoy (agosto 2026) esa es `2025-10`;
`2025-07` dejó de ser accesible el 16 de julio de 2026.

Consecuencia: el conector cree hablar `2024-10` y está hablando `2025-10`. Siete trimestres de
deriva semántica, sin un solo error. El comentario en `shopify_prices.py:19-21` ya advertía que
había que verificar las formas contra la doc viva; nadie lo hizo, y nada obligó a hacerlo.

### Fallos silenciosos del mismo patrón, ya presentes

1. `set_inventory` lee `res.get("inventorySetGroup")`; la doc de `2026-07` dice que
   `inventorySetQuantities` devuelve `inventoryAdjustmentGroup`. Cuando no encuentra el campo,
   el método **devuelve `available`** — el valor que se quiso escribir, no el confirmado. Un
   fallo aquí es indistinguible del éxito.
2. Los clientes hacen `resp.raise_for_status()` y descartan los headers, incluido
   `X-Shopify-API-Version`, que es justo el dato que delata el fall-forward.

## 2. Los dos mecanismos de detección

Ambos son del propio Admin API. Ninguno requiere scrapear docs ni mantener un calendario.

| Mecanismo | Qué da | Coste |
|---|---|---|
| Header `X-Shopify-API-Version` | La versión **realmente** usada, en cada respuesta | Cero — ya viene |
| Query `publicApiVersions { handle supported displayName }` | Versiones accesibles y si están soportadas | Una llamada, cacheable |

El header es la señal de "algo ya se rompió". La query es la señal de "algo se va a romper".

## 3. Alcance del bump a `2026-07`

Vigente hasta el 16 de julio de 2027. **No es un cambio de constante**: trae un cambio
incompatible.

> «As of version `2026-04`, the idempotency key is required and must be provided using the
> `@idempotent` directive.»

Aplica a `inventorySetQuantities` y `inventoryAdjustQuantities` — las dos escrituras de stock.
Con el pin en `2026-07` y sin la directiva, ambas fallan.

La clave ya existe: `ShopifyInventoryStore` habla la superficie de writeback, que trabaja con
`idempotency_key`. Nunca viajó en la petición. Hay que enhebrarla, no inventarla.

Verificado contra la doc de `2026-07`:

| Operación | Estado |
|---|---|
| `productVariants(first:, query:)` | Existe |
| `productVariantsBulkUpdate` | Existe; `ProductVariantsBulkInput.price` (Money) **no** deprecado |
| `inventorySetQuantities` | Existe; exige `@idempotent`; payload `inventoryAdjustmentGroup` |
| `inventoryAdjustQuantities` | Existe; exige `@idempotent` |

## 4. Diseño

### Módulo nuevo: `src/connectors/shopify_api_version.py`

Sin I/O propio salvo la query de descubrimiento; recibe el transporte por parámetro, como el
resto de conectores.

```python
PINNED_VERSION = "2026-07"          # única fuente; los dos conectores la importan

class ShopifyVersionDriftError(RuntimeError): ...

def served_version(response) -> str | None
    # lee X-Shopify-API-Version; None si el transporte no expone headers (tests offline)

def check_served(response, *, requested: str, is_write: bool) -> None
    # servida == pedida        -> no hace nada
    # servida != pedida, write -> ShopifyVersionDriftError
    # servida != pedida, read  -> logging.warning, sigue
    # sin header               -> no hace nada (no rompe los stand-ins offline)

def discover_versions(graphql) -> list[ApiVersion]      # publicApiVersions
def check_pin(versions, *, pinned) -> PinStatus         # pura: SOPORTADA | POR_VENCER | VENCIDA
```

**Por qué falla duro solo en escrituras:** escribir stock o precio en una tienda real con la
semántica de una versión que no es la que se cree es el peor caso — datos malos en el sistema
de registro del cliente. Una lectura desalineada es recuperable y no debe tumbar un job.

**Dónde corre cada comprobación.** `check_served` es automática: va en el camino de cada
petición, sin coste ni red extra. `discover_versions` + `check_pin` **no** corren al importar ni
en cada llamada —exigirían red en cada import y romperían los tests offline—; se exponen como
función y se invocan desde un script explícito (`scripts/`), a mano o en CI programado. Es la
alerta anticipada, y su cadencia natural es trimestral, no por petición.

**Por qué "sin header" no es error:** `InMemoryShopifyInventory` y el emulador no devuelven
objetos con headers. Tratar su ausencia como deriva rompería toda la suite offline y empujaría
a la gente a desactivar la comprobación.

### Cambios en los conectores

`shopify_inventory.py`:
- Importa `PINNED_VERSION`; elimina su `_DEFAULT_API_VERSION`.
- `_graphql()` acepta `is_write` y llama a `check_served`.
- `@idempotent` en las dos mutaciones de inventario.
- `adjust_inventory` / `set_inventory` reciben `idempotency_key`; el Protocol
  `ShopifyInventoryRPC` y `InMemoryShopifyInventory` cambian con ellos.
- `set_inventory` lee `inventoryAdjustmentGroup`, y **falla** si no está en vez de devolver
  `available`.

`shopify_prices.py`:
- Importa `PINNED_VERSION`; elimina su `_DEFAULT_API_VERSION`.
- `_graphql()` con `is_write`.
- Actualiza el comentario de las líneas 19-21: la verificación ya no es un TODO en prosa.

El parámetro `api_version=` de ambos constructores se mantiene, con `PINNED_VERSION` de
default. Permite fijar otra versión sin editar código.

## 5. Tests

Offline, con el patrón del repo (`InMemoryShopifyInventory`, emulador). Sin red.

| Caso | Espera |
|---|---|
| servida == pedida | pasa |
| servida != pedida, lectura | warning, devuelve dato |
| servida != pedida, escritura | `ShopifyVersionDriftError` |
| respuesta sin headers | pasa (camino offline) |
| `check_pin` con pin soportado / por vencer / vencido | los tres estados |
| `publicApiVersions` devuelve forma inesperada | error claro, no `KeyError` |
| mutaciones de inventario | llevan `@idempotent` y la clave |
| `set_inventory` sin `inventoryAdjustmentGroup` | falla, **no** devuelve `available` |
| el pin es idéntico en los dos conectores | pasa (evita la regresión de la duplicación) |

## 6. Lo que este trabajo NO verifica

**Todo se prueba offline.** Las cuatro operaciones se verificaron **contra la documentación de
`2026-07`, no contra una tienda**. Sin credenciales de un store real, que el bump funcione de
verdad queda sin medir.

Es una limitación conocida y declarada, no un descuido: no debe describirse como comprobado en
ningún README hasta que corra contra una tienda. La comprobación pendiente es correr
`discover_versions` y una escritura de stock contra un store de desarrollo.

## 7. Fuera de alcance (va a C)

`ignoreCompareQuantity: True` está hardcodeado en `set_inventory`, lo que desactiva a propósito
el compare-and-swap de Shopify. Es la instancia concreta del cuarto hueco de la auditoría
(«sin `expected_version` ni reconciliación: el writeback puede pisar un cambio hecho entre la
lectura y la escritura»). Corresponde al trabajo de deriva, no a este.

## Fuentes

- https://shopify.dev/docs/api/usage/versioning
- https://shopify.dev/docs/api/admin-graphql/latest/objects/ApiVersion
- https://shopify.dev/docs/api/admin-graphql/2026-07/mutations/inventorySetQuantities
- https://shopify.dev/docs/api/admin-graphql/2026-07/mutations/inventoryAdjustQuantities
- https://shopify.dev/docs/api/admin-graphql/2026-07/mutations/productVariantsBulkUpdate
- https://shopify.dev/docs/api/admin-graphql/2026-07/input-objects/ProductVariantsBulkInput
