# Spec — Lector de catálogo público

**Fecha:** 2026-07-25 · **Rama:** `feat/lector-catalogo-publico` (desde `origin/main` @ `ec7f411`)
**Estado:** diseño aprobado, sin código. El plan de implementación se escribe aparte.

---

## 1. Por qué existe

Es el componente 1 de "la máquina" — la reorientación del 2026-07-25 (ver
`2026-07-25-reorientacion-la-maquina-handoff.md` en la rama
`feat/radiografia-congelados-cl`, que queda archivada). La tesis: lo valioso
no es el dominio chileno de congelados, es la disciplina de tres estados. El
lector es la pieza que hace entrar datos a esa disciplina.

**El argumento que lo puso primero.** Hoy el corpus de prueba de todo el
trabajo anterior son 24 strings pegados a mano dentro de un archivo de test.
Existe un normalizador de peso y existe un motor de restricciones fechadas, y
no existe ninguna forma de que a la máquina le entre un catálogo. El lector
destraba los otros dos caminos de generalización; ninguno de los dos lo
destraba a él.

**Medición que respalda la tesis** (clasificación símbolo por símbolo de las
441 líneas de los dos módulos existentes, en la rama archivada): 56,2% es
forma pura, 35,8% es forma buena con vocabulario de veda, 7,9% es léxico y
dato chileno. La máquina ya existía adentro; solo el 7,9% era Chile.

---

## 2. Dónde encaja: el peldaño L0 vacío

El repo ya tiene 3.217 líneas de pipeline de adquisición para price intel, con
una escalera de tiers de robusto a frágil:

| Tier | Qué es | Quién lo implementa hoy |
|---|---|---|
| **L0** | API oficial | solo `MeliApiFetcher`, y MercadoLibre bloquea a `ClaudeBot` por robots.txt |
| L1 | JSON-LD / microdata en la ficha | `acquire/l1.py`, `acquire/structured.py` |
| L2 | watcher | `acquire/watcher.py` |
| L3 | spider | — |

`DEGRADE_TIER` (`src/pricing_intel/acquire/base.py:208`) ya apunta `L1 → L0` y
ahí toca piso. **Leer un feed público es L0**: el peldaño estaba nombrado y
vacío.

Tres consecuencias verificadas:

1. **No hace falta techo legal nuevo.** `max_tier_allowed` siembra el tier
   inicial y el degradado baja hasta L0 como piso. El auto-onboarding
   robots-only (`acquire/auto_approve.py`), que siempre marca `limited` con
   techo `L1`, ya incluye L0. Leer un feed abierto es *más* permisible que
   raspar una ficha, no menos.
2. **El protocolo existente no sirve.** `acquire.base.Fetcher` es por SKU
   (`fetch(sku_ref) -> RawObservation`). Enumerar un catálogo completo no
   tiene `sku_ref` de entrada. Hay contrato nuevo que escribir.
3. **Cero implementación de feeds.** `wp-json`, `products.json`,
   `X-WP-TotalPages` y `per_page` dan 0 hits en todo el repo.

El hallazgo de campo que justifica el componente: de 6 sitios medidos, 4
rechazan la lectura de ficha (Shopify dispara captcha, TodoFrío no emite
JSON-LD) pero todos publican catálogo por feed abierto. **Leer fichas es el
camino frágil; leer feeds es el robusto.**

---

## 3. Decisiones tomadas

| # | Decisión | Alternativa descartada y por qué |
|---|---|---|
| D1 | **Consumidor: el motor de restricciones fechadas.** Registro nuevo, independiente del dominio. No toca el ledger de precios ni la cascada de homologación. | El ledger existente reusaría 3.217 líneas, pero esa cascada re-chequea un SKU conocido; no enumera una tienda. Y ataría el lector al dominio del que lo estamos sacando. |
| D2 | **El lector para en "campo + procedencia".** Normaliza a un registro canónico donde cada valor carga de dónde salió. No interpreta semántica de dominio. | Solo transporte pierde la procedencia y obliga a cada consumidor a reimplementar el aplanado. Interpretación completa mete dominio dentro del componente que promete no tenerlo. |
| D3 | **Perímetro v1: Woo (3 rutas) + Shopify, con la no-cobertura declarada.** Ante Jumpseller o Bsale reconoce la plataforma y devuelve un desenlace tipado. | Solo JSON pierde la distinción entre "no reconocí" y "reconocí y no publica JSON". Sumar lector HTML mete el camino frágil en v1. |
| D4 | **Validación mixta:** 2 tiendas de congelados CL (feeds ya verificados en campo, una Woo y una Shopify) + 2 de otro rubro o país. La cobertura se declara por plataforma, no por rubro. | Validar solo en congelados chilenos es la trampa de la que venimos saliendo. |
| D5 | **Estructura en dos capas:** red afuera, función pura adentro, un adaptador por plataforma. | Un módulo único con Protocol junta red, forma y procedencia; con tres rutas Woo, paginado, WAF y dos plataformas pasa las 400 líneas antes de la primera plataforma nueva. |
| D6 | **Tipo de campo: `Attribution` + `source`.** Mismo nombre que en la rama archivada, más el campo de procedencia. | Un nombre nuevo (`Claim`) deja dos nombres para la misma idea en el mismo repo. Dejar la procedencia en un mapa aparte separa el valor de su origen justo cuando el punto es que viajen juntos. |
| D7 | **El gate de robots.txt y tier es del llamador**, no del lector. `src/catalog/` no importa nada de `pricing_intel`. | Autogatearse haría imposible olvidarse, al precio de atar el paquete independiente del dominio al paquete de precios. Mover el gate a `src/compliance/` es correcto a largo plazo pero es refactor de código que hoy funciona. |

**D6 tiene un efecto secundario que vale nombrar:** naciendo de `origin/main`,
el `Attribution` de vedas no está en el árbol. Se escribe una sola vez y
genérico, con `source`. Eso entrega parte del enfoque A (extraer el núcleo)
*ganada con dos casos de uso reales* en vez de adivinada con uno — que es
exactamente la objeción que tenía el enfoque A si se hacía primero.

---

## 4. Arquitectura

```
src/catalog/
  source.py            deteccion de plataforma, sondeo de rutas, paginado. LO UNICO que toca la red.
  record.py            fila canonica: cada valor con su procedencia. Modulo puro, sin I/O.
  adapters/
    woo.py             mapeo explicito de campos de WooCommerce Store API
    shopify.py         mapeo explicito de campos de /products.json
examples/
  run_catalog_read.py  la corrida real contra una URL. Fuera de CI.
tests/
  fixtures/catalog/    payloads reales grabados
  test_catalog_record.py
  test_catalog_source.py
```

La costura cae donde ya cae en el repo: `record.py` no importa `httpx` y se
testea con feeds grabados; `source.py` recibe el fetcher inyectado. Cada
plataforma nueva es un archivo, no una rama dentro de un `if`.

---

## 5. Contratos

### 5.1 `Attribution` — un valor y de dónde salió

```python
@dataclass(frozen=True)
class Attribution:
    value: str | None = None
    source: str | None = None       # "feed:prices.price" | "feed:name" | "titulo"
    evidence: str | None = None     # el valor crudo tal cual vino, sin reformatear
    conflicts: tuple[tuple[str, str], ...] = ()
```

**Tres estados, nunca dos.** `declarado` (hay valor, hay evidencia, nada lo
contradice) · `no_declarado` (el feed no lo dice; no se rellena con el valor
más probable) · `contradicho` (dos claves del feed que mapean al mismo
atributo canónico no coinciden).

`source` es la generalización sobre el tipo de la rama archivada: distingue un
valor que el comerciante declaró como **campo** de uno derivado de **texto**.
Esa distinción es la que hace detectable la contradicción entre fuentes.

### 5.2 `CatalogRow` — una fila del catálogo, sin dominio

```
url · site · read_at                                    metadatos, no atributos
title · price · currency · sku · gtin                   Attribution
weight_declared · stock_status                          Attribution
categories: tuple[Attribution, ...]                     una por categoria declarada
raw: Mapping                                            el payload de esa fila, para auditar
```

Los atributos son `Attribution`; `url`/`site`/`read_at` son metadatos de la
lectura y no llevan procedencia (su procedencia es el lector mismo).
`categories` es una tupla de `Attribution` y no un `Attribution` de lista: cada
categoría declarada carga su propia clave de origen, y una fila sin categorías
es una tupla vacía, no un `no_declarado` con valor `None`.

Ni una palabra de especie, veda ni congelado.

`weight_declared` guarda lo que el feed dice. **El lector no compara el peso
del feed contra el peso del título**: cargar ambas fuentes es su trabajo,
decidir la contradicción es del normalizador de peso, que no está en esta
rama. Cuando ese normalizador se traiga, ya acepta `feed_weight_kg` y devuelve
`CONTRADICCION_FUENTE` con tolerancia del 2% — el enganche existe y no hay que
diseñarlo dos veces.

### 5.3 `ReadReport` — la cobertura, declarada siempre

```
platform · route_used · pages_read · rows_emitted
rows_declared:       int | None                         cuantas filas declaro la plataforma
routes_probed:       ((ruta, status_http), ...)
rows_dropped:        ((motivo_tipado, cuenta), ...)     la fila entera no se emitio
undeclared_reasons:  ((motivo_tipado, cuenta), ...)     la fila se emitio, un atributo quedo sin declarar
truncations:         (motivo_tipado, ...)               hay catalogo que no se leyo
attribute_states:    ((atributo, (decl, no_decl, contra)), ...)
outcome: ok | sin_feed_json | bloqueado_waf | plataforma_desconocida | error_red
```

**`truncations`, cardinalidad cerrada en cuatro:** `TOPE_PAGINAS_ALCANZADO` ·
`LECTURA_PARCIAL_PAGINA_FALLIDA` · `PAGINA_REPETIDA` ·
`TOTAL_DECLARADO_NO_COINCIDE`. Existe como campo propio porque meter el
truncamiento en `rows_dropped` rompería su cardinalidad de dos y confundiría
"fila descartada" con "lectura cortada", que son cosas distintas.

**La regla del desenlace: `outcome = "error_red" if truncations else "ok"`**,
con `route_used` poblado. `route_used is None` si y solo si ninguna ruta
entregó feed. Los cuatro truncamientos significan lo mismo desde el punto de
vista de cobertura — hay catálogo que no se leyó y no se sabe cuánto — así que
un consumidor que ramifica por `outcome`, el único campo que sobrevive a un log
de una línea, no puede leer una lectura truncada como completa.

**`rows_declared`** sale del header que la plataforma publica (`X-WP-Total` en
Woo; Shopify no lo publica y queda `None`). Cuando no hubo ningún otro
truncamiento, se reconcilia contra `rows_emitted + suma(rows_dropped)`; si
difieren, entra el cuarto motivo. Sin esa reconciliación la única garantía de
completitud sería "le creí al header de páginas": con un `X-WP-TotalPages`
cacheado por un CDN, o un catálogo que cambia de tamaño a mitad de la lectura,
el informe diría `ok` sobre una lectura parcial. El invariante interno
(`rows_emitted + rows_dropped == filas crudas recibidas`) mide **consistencia**,
no **completitud**: se satisface perfecto con la mitad del catálogo.

**`attribute_states`** lleva, por atributo canónico, los conteos
`(declarado, no_declarado, contradicho)`. Sin él, una lectura donde el 100% de
las filas quedó sin `sku`, `gtin`, `weight_declared` y `stock_status` produciría
un informe con `undeclared_reasons` vacío y `outcome == "ok"` — porque los tres
motivos de `undeclared_reasons` son todos de precio y moneda. Un informe
titulado "la cobertura, declarada siempre" no puede dejar sin declarar la
cobertura de cuatro de los siete atributos canónicos.

`rows_dropped` y `undeclared_reasons` son dos cosas distintas a propósito, y
confundirlas es el error que este componente existe para no cometer. Una fila
sin precio **se emite** con `price` en `no_declarado` y el motivo contado en
`undeclared_reasons`; no se descarta. Descartar una fila real por un campo
ausente es esconder cobertura.

Cinco desenlaces, no dos. Se emite **siempre**, incluso cuando no se leyó ni
una fila. `routes_probed` guarda las cuatro rutas con su status, así que el
informe dice cuál respondió y cuál no.

---

## 6. Flujo

### 6.1 Sondeo, en orden, registrando cada intento

| # | Ruta | Nota de campo |
|---|---|---|
| 1 | `/wp-json/wc/store/v1/products?per_page=100` | la más común |
| 2 | `/wp-json/wc/store/products?per_page=100` | Distribuidoris, Pro Verdy, Interagro |
| 3 | `/index.php/wp-json/wc/store/products?per_page=100` | permalinks planos (Pesquera Mancilla) |
| 4 | `/products.json?limit=250` | Shopify |

Tres reglas que definen el desenlace:

- **Un 200 con JSON de forma inesperada no es match.** Sigue sondeando. Nunca
  se adivina el mapeo de campos.
- **Un 403 no corta el sondeo** — un WAF que bloquea una ruta no prueba nada
  sobre las otras. Pero si ninguna ruta entrega feed y al menos una devolvió
  403, el desenlace es `bloqueado_waf`, **jamás** `sin_feed_json`. Ese falso
  negativo ya costó sitios reales.
- **Si las cuatro dan 404**, una sola lectura de la home busca huellas de
  plataforma, para distinguir `sin_feed_json` (reconocí Jumpseller o Bsale) de
  `plataforma_desconocida`.

### 6.1.1 Política de redirects

El lector fija la política, no la hereda del cliente inyectado:
**`follow_redirects=True` por pedido**, y `url`/`site` de cada fila salen de la
respuesta que **efectivamente** entregó el feed, no de la URL pedida. Eso
incluye un salto **cross-host**: si el 301 lleva a otro host, se sigue y se
registra el host que respondió.

Dos razones, y las dos son de honestidad:

- Con el default de `httpx` (no seguir), una tienda que hace 301 de
  `/products.json` caería en el desenlace de "ninguna ruta entregó feed" — un
  falso negativo de tienda entera. Y la suite ejercitaría un camino distinto al
  de las corridas reales, porque los tests con `MockTransport` no siguen
  redirects.
- Si se siguen redirects sin corregir la procedencia, las filas quedan
  atribuidas a un host que no respondió. §5.2 dice que la procedencia de esos
  metadatos "es el lector mismo": entonces tienen que decir la verdad de la
  lectura, no la de la intención.

### 6.2 Paginado

- **Woo:** por el header `X-WP-TotalPages`. Si el header falta, hasta recibir
  un arreglo vacío, con **tope duro de 100 páginas** — 10.000 filas a 100 por
  página. El tope no es un límite de catálogo: es la guarda contra un bucle
  infinito cuando el header no viene, y alcanzarlo se registra como motivo en
  el `ReadReport` para que nadie lea una lectura truncada como completa.
- **Shopify:** `page=1..` hasta arreglo vacío, `limit=250`.
- Espera de `rate_limit_seconds` entre pedidos, inyectada.
- **Sin reintentos**, igual que la disciplina degrade-only-never-retry de
  `acquire/l1.py`: una página que falla corta la lectura y `pages_read` dice
  hasta dónde llegó. La lectura parcial se declara, no se esconde.

### 6.3 Mapeo

Cada fila cruda pasa por el adaptador de su plataforma, que mapea claves
**explícitamente nombradas** a los atributos canónicos. Un campo que no está
en el payload queda `no_declarado`.

---

## 7. Trampas conocidas y la regla que bloquea cada una

| Trampa | Daño | Regla |
|---|---|---|
| **`currency_minor_unit`.** En Woo Store API los precios vienen como enteros en unidad menor, con el factor en otro campo: `"1990"` con minor_unit 2 es 19,90. | 100× | `evidence` guarda el string crudo; `value` se escala por `currency_minor_unit`. **Si `currency_minor_unit` no viene, el precio queda `no_declarado`** — no se asume 2. Es el mismo primo del `1.000cc` que ya costó en peso. |
| **403 leído como ausencia de catálogo.** | falso negativo de sitio entero | Desenlace `bloqueado_waf` separado de `sin_feed_json` (6.1). |
| **Inferir el mapeo por forma del JSON.** Un enfoque evaluado y descartado: buscar una lista de dicts con una clave parecida a nombre y otra parecida a precio. | filas plausibles y erróneas, sin fallar | Adaptadores explícitos por plataforma (D5). Inferir que un campo *es* el precio es rellenar lo que no se sabe con lo más probable — el bug que la disciplina existe para prohibir. |
| **Cero filas silencioso.** Un Jumpseller devolvería "leí 0 productos". | el operador concluye que la tienda no tiene catálogo | `sin_feed_json` con la plataforma nombrada (D3). |

### 7.1 Un límite que hay que verificar, no asumir

El esquema de WooCommerce Store API **aparentemente** no lleva peso en el
producto (vive en el admin REST API), mientras Shopify sí expone
`variants[].grams`. Esto está sin confirmar contra payload real.

**Primera tarea del plan, antes de escribir cualquier adaptador:** grabar un
payload real de cada plataforma y verificarlo. La regla de decisión, fijada de
antemano para las dos salidas posibles:

- Si Woo **no** trae peso: `weight_declared` queda `no_declarado` en Woo, se
  documenta en el spec y la contradicción peso-feed contra peso-título solo se
  activa en Shopify. No se busca el peso en `attributes[]` por heurística.
- Si Woo **sí** trae peso en alguna clave: se mapea esa clave por nombre
  explícito, como cualquier otra.

### 7.2 Registro de constataciones empíricas

La Tarea 1 del plan escribe acá sus tres decisiones (`woo.WEIGHT_KEYS`,
`shopify.WEIGHT_KEYS`, y el factor de precio de Shopify), cada una con el
fixture que la sostiene. Hasta que esa tarea corra, esta sección está vacía a
propósito: son hechos, y todavía no se midieron.

**Regla obligatoria, ya fijada.** La unidad del peso **no viaja** en el payload
de la Store API de Woo — es un ajuste del sitio. Así que la rama
`woo.WEIGHT_KEYS = ("weight",)` declara una **magnitud sin unidad**. Si esa es
la rama que la Tarea 1 constata, esta sección debe contener textualmente:

> `weight_declared` en Woo no lleva unidad declarada; el consumidor no puede
> tratarlo como kg.

y la misma frase va en el docstring del adaptador y en el campo
`woo_weight_unit_declared: false` del registro de decisiones. Cuando el
normalizador de peso se traiga a esta base, el enganche `feed_weight_kg` tiene
que leer ese campo **antes** de usar el número. Un peso sin unidad tratado como
kilos es el mismo error de 1000× que ya costó una vez, con otra cara.

---

## 8. Manejo de errores

`read_catalog` **no levanta excepciones** por problemas de red ni de forma:
devuelve `(filas, ReadReport)` con el desenlace tipado. Solo levanta por error
de programación (por ejemplo, una URL que no es string).

**Descarte de fila** (`rows_dropped`), solo dos motivos, porque solo dos hacen
que la fila no sea una fila: `FILA_NO_DICT` (el feed devolvió algo que no es un
objeto) y `SIN_TITULO` (sin título no hay nada que identifique al producto, y
el título es la entrada del normalizador de peso y del motor).

**Atributo sin declarar** (`undeclared_reasons`), la fila sí se emite:
`PRECIO_NO_PARSEABLE` · `MINOR_UNIT_AUSENTE` · `MONEDA_AUSENTE`.

Ninguno de los cinco es silencioso: todos quedan contados en el `ReadReport`.

---

## 9. Testing

**Fixtures:** payloads reales grabados en `tests/fixtures/catalog/` — las tres
variantes de ruta Woo, Shopify, un caso 403 y un caso vacío. Ningún test toca
la red.

**`test_catalog_record.py`** (módulo puro): mapeo canónico por plataforma; los
tres estados; el escalado por `currency_minor_unit` y su ausencia.

**La propiedad central, gemela de la de vedas:** para todo `Attribution` en
estado `declarado`, su `evidence` tiene que aparecer literal en el `raw` de esa
fila. Es una propiedad sobre todo el corpus de fixtures, no una lista de
casos, así que cubre también los atributos que todavía no existen.

> **Hallazgo heredado que conviene no perder:** el test de propiedad **no**
> atrapa el arbitraje silencioso. Elegir la primera señal produce evidencia
> literal válida, así que la propiedad pasa. Evidencia inventada y arbitraje
> silencioso son dos fallas distintas y cada una necesita su propia guarda.
> Para este lector, la guarda del arbitraje es que dos claves que mapean al
> mismo atributo canónico y no coinciden dan `contradicho`, nunca "la
> primera gana".

**`test_catalog_source.py`** con fetcher falso inyectado: caída 404→404→200;
el 403 con desenlace `bloqueado_waf`; paginado por `X-WP-TotalPages`; el corte
de Shopify por arreglo vacío; lectura parcial cuando falla una página a mitad;
conteo de esperas de rate limit.

**Verificación por mutación**, como ya es costumbre en este trabajo. Cada una
tiene que poner rojo al menos un test:

1. Ignorar `currency_minor_unit`.
2. Asumir `currency_minor_unit = 2` cuando falta.
3. Tratar el 403 como `sin_feed_json`.
4. Cortar el sondeo al primer 404.
5. Ante dos claves en conflicto, quedarse con la primera.
6. Descartar la fila cuando falta el precio, en vez de emitirla con el precio
   sin declarar.
7. Paginar sin tope cuando falta `X-WP-TotalPages`.

---

## 10. Validación en campo

`examples/run_catalog_read.py --url <tienda>` imprime el `ReadReport` y un
resumen de cobertura. Fuera de CI.

Cuatro tiendas: 2 de congelados CL (una Woo, una Shopify — feeds ya
verificados en campo) y 2 de otro rubro o país. Las chilenas entran **solo
como dataset de validación del lector**: no se extiende el léxico, no se
agregan decretos, no se termina el informe de las 5 distribuidoras y no se
escribe nada de outreach. Eso sigue archivado.

---

## 11. Lo que este componente NO hace

- No interpreta semántica de dominio. No sabe qué es una especie ni una veda.
- No compara peso de feed contra peso de título (§5.2).
- No lee HTML de fichas ni de listados (D3).
- No gatea robots.txt ni el techo de tier; eso es del llamador (D7).
- No escribe nada en ningún sistema de registro. Es 100% lectura.
- No toca el ledger de precios ni la cascada de homologación (D1).

---

## 12. Convenciones que aplican

- Python 3.11+. Tests con `PYTHONPATH=.`.
- Lint: `ruff check src tests examples`.
- **ASCII-only en prints de consola** — `examples/run_catalog_read.py` no
  puede imprimir acentos (Windows cp1252). Los archivos markdown en utf-8 sí.
- Rama nueva → draft PR → CI verde → squash-merge. Nunca directo a `main`.
- El venv vive en el repo principal:
  `C:\Users\Gamer\Music\scm\supply-chain-optimization\.venv\Scripts\python.exe`.
  El repo principal está en otra rama con trabajo concurrente ajeno: **no
  tocarlo**.
