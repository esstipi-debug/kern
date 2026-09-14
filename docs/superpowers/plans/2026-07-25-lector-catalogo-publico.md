# Lector de Catalogo Publico Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Leer el catalogo publico de una tienda WooCommerce o Shopify a partir de su URL y devolver filas canonicas donde cada valor carga de que clave del feed salio, mas un informe de cobertura que declara siempre lo que no se pudo leer.

**Architecture:** Dos capas con una costura dura. `src/catalog/source.py` es lo unico que toca la red: sondea cuatro rutas en orden, pagina, y recibe el cliente HTTP y la espera inyectados. `src/catalog/record.py` es un modulo puro (tipos `Attribution` / `CatalogRow` / `ReadReport` y el arbitro de procedencia) y `src/catalog/adapters/woo.py` + `shopify.py` mapean claves explicitamente nombradas a los atributos canonicos; cada plataforma nueva es un archivo, no una rama dentro de un `if`. Nada de `src/catalog/` importa `pricing_intel`, ni escribe en ningun sistema de registro, ni interpreta semantica de dominio.

**Tech Stack:** Python 3.11+, `httpx>=0.28` (solo en `source.py`; los tests usan `httpx.MockTransport`), `dataclasses` frozen, `decimal.Decimal` para el escalado de precios, `pytest`, `ruff`.

## Global Constraints

- **Worktree de trabajo:** `C:/Users/Gamer/Music/scm/.wt-radiografia-cl`, rama `feat/lector-catalogo-publico` (cortada de `origin/main` @ `ec7f411`). Todo el diff vive aca.
- **NO TOCAR** `C:/Users/Gamer/Music/scm/supply-chain-optimization` (otra rama, trabajo concurrente ajeno). Solo lectura si hace falta.
- **Interprete (el venv vive en el repo principal):** `C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe`. Python 3.11+.
- **Comando de tests (exacto, desde el worktree):**
  `PYTHONPATH=. C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m pytest tests/test_catalog_record.py tests/test_catalog_source.py tests/test_catalog_boundaries.py tests/test_catalog_cli.py -q`
  En PowerShell el equivalente es `$env:PYTHONPATH='.'; C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m pytest ... -q`.
- **Criterio de aceptacion de cada paso: CERO FALLOS mas los nombres de los tests que tienen que estar en verde o en rojo.** Este plan NO declara conteos absolutos de tests (`N passed`): dependen de cuantas filas traigan los fixtures grabados en la Tarea 1 y de `len(CATALOG_FILES)`, asi que un numero escrito hoy es una trampa de mantenimiento y entrena al implementador a racionalizar diferencias de conteo -- que es justo como pasaria inadvertida una regresion real. Para medir el delta de tests recolectados: `pytest <archivo> --collect-only -q | tail -1` antes y despues del paso.
- **Suite completa (lo que corre CI):** `pytest tests/ -q --cov=src --cov-report=term-missing --cov-fail-under=80` con `PYTHONPATH=.`. El codigo nuevo en `src/catalog/` **si** cuenta contra el gate del 80% (`[tool.coverage.run] source=['src']`); `examples/` y `jobs/` no.
- **Comando de lint (exacto, los tres directorios):** `C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m ruff check src tests examples`. Config: `target-version=py311`, `line-length=120`, `select=[E,F,I]` (isort incluido: `from __future__` primero, luego stdlib, luego third-party `httpx`, luego first-party `src.*`), `ignore=[E501]`.
- **ASCII-only en prints de consola.** `examples/run_catalog_read.py` no puede imprimir acentos (Windows cp1252). Los archivos markdown si van en utf-8 con acentos.
- **`per_page=100`** en las tres rutas Woo (es el maximo que la ruta acepta; `per_page=250` seria rechazado con 400 `rest_invalid_param`). **`limit=250`** en Shopify.
- **Tope duro de 100 paginas** (`MAX_PAGES = 100`) en los dos paginadores. No es limite de catalogo: es la guarda contra bucle infinito, y alcanzarlo se registra como motivo tipado en el `ReadReport`.
- **Sin reintentos** (disciplina degrade-only-never-retry): una pagina que falla corta la lectura y `pages_read` dice hasta donde llego.
- **`src/catalog/` NO importa nada de `src/pricing_intel/`.** Si un paso parece necesitarlo, el paso esta mal disenado. Corolario: el `USER_AGENT` y el patron de fetch se re-escriben en `source.py`; no se importan de `pdp_fetcher.py`.
- **`src/catalog/` no importa `httpx`, `urllib`, `requests` ni `socket` en ningun archivo salvo `source.py`.** `record.py` y los adaptadores son puros.
- **`src/catalog/__init__.py` no re-exporta `source.py`** (regla prod-boot: `httpx` es extra opcional; el patron verificado es `src/pricing_intel/acquire/__init__.py`, que no importa submodulos). No hace falta tocar `pyproject.toml`: `[tool.setuptools] packages=["src"]` ya cubre subpaquetes nuevos.
- **Cero vocabulario de dominio** en `src/catalog/`: ni especie, ni veda, ni congelado, ni decreto, ni nombre de pez. Hay test de guarda.
- **El lector no gatea `robots.txt` ni el techo de tier**; ese gate es del llamador. Cero apariciones de `robots`, `max_tier_allowed`, `tos_decision` en `src/catalog/`.
- **100% lectura.** Cero apariciones de `writeback`, `connectors`, `Changeset`, `Approval`, `ledger`, `homolog` en `src/catalog/`.
- **No lee HTML de fichas ni de listados.** La unica lectura de HTML permitida es **una sola** de la home, exclusivamente para huella de plataforma, y solo despues de cuatro 404. Cero `extruct`, `bs4`, `JSON-LD`, `microdata`.
- **El enganche del peso NO se redisena en esta rama.** El normalizador de peso futuro ya acepta `feed_weight_kg` y devuelve `CONTRADICCION_FUENTE` con tolerancia del 2%; este plan no implementa ni la tolerancia ni la comparacion feed-vs-titulo. `weight_declared` solo guarda lo que el feed dice.
- **Fixtures = payloads reales grabados** en `tests/fixtures/catalog/`, con procedencia y fecha por fixture. Ningun test toca la red (hay guarda de socket autouse).
- **Flujo de rama:** rama nueva -> draft PR -> CI verde -> squash-merge. Nunca directo a `main`.
- **Cada tarea que crea codigo termina en commit** con mensaje conventional commits en ASCII.

## Decisiones que este plan cierra

El spec no se re-litiga. Estas son ambiguedades que el spec deja abiertas y que el plan cierra para que el implementador no tenga que preguntar. Cada una lleva su razon.

| # | Ambiguedad | Decision | Razon |
|---|---|---|---|
| C1 | El spec fija cinco desenlaces, pero el tope de 100 paginas y la lectura parcial "se registran como motivo en el `ReadReport`" y `rows_dropped` / `undeclared_reasons` tienen cardinalidad cerrada (2 y 3). | Campo nuevo `truncations: tuple[str, ...]` con **cuatro** constantes cerradas: `TOPE_PAGINAS_ALCANZADO`, `LECTURA_PARCIAL_PAGINA_FALLIDA`, `PAGINA_REPETIDA`, `TOTAL_DECLARADO_NO_COINCIDE` (ver C16). | Meter el truncamiento en `rows_dropped` rompe su cardinalidad de 2 y confunde "fila descartada" con "lectura cortada". |
| C2 | Que `outcome` corresponde a una lectura parcial (fallo la pagina 3 de 5). | `error_red`, con `route_used` **poblado** y las filas de las paginas 1-2 emitidas. `route_used is None` si y solo si ninguna ruta entrego feed. **Y la regla vale para los cuatro truncamientos, no solo para la pagina fallida:** `outcome = "error_red" if truncations else "ok"`. | Los otros cuatro desenlaces son falsos (si hubo feed). Blanquear `route_used` en ese caso destruye informacion cierta. Los cuatro truncamientos significan lo mismo desde el punto de vista de cobertura -- hay catalogo que no se leyo y no se sabe cuanto -- asi que un consumidor que ramifica por `outcome` (el unico campo que sobrevive a un log de una linea) leeria una lectura truncada como completa. El propio CLI ya devuelve exit 1 ante cualquier truncamiento: el tipo no puede decir lo contrario que su renderizador. |
| C3 | Convencion de `pages_read`. | Numero de paginas de feed cuyas filas **se emitieron**: incluida la pagina 1 (el sondeo que acerto) y una ultima pagina vacia; **excluida** una pagina repetida, cuyas filas se descartan por C4. | Monotono y sin casos especiales: header=3 -> 3; sin header con 2 llenas + 1 vacia -> 3; falla la 3 -> 2; **pagina repetida en la 3 -> 2**; catalogo vacio -> 1; tope -> 100. |
| C4 | Shopify: el spec corta el paginado por arreglo vacio, pero `/products.json` de storefront no esta documentado y puede **repetir la primera pagina** ante `page=2` en silencio. | Se detecta pagina repetida comparando el cuerpo crudo contra el de la pagina anterior; se corta, se registra `PAGINA_REPETIDA` y se descartan sus filas. El tope de 100 paginas aplica a los dos paginadores. | Sin esto, un endpoint que ignora `page` da bucle o catalogo duplicado, y la mutacion 7 quedaria cubierta en Woo y descubierta en Shopify. |
| C5 | Woo >= 10.7.0 emite `weight`, pero **la unidad no viaja en el payload** (es un setting de sitio); `formatted_weight` es una cadena localizada y traducible que devuelve el texto traducido "N/A" cuando el peso no esta seteado. | `weight_declared` en Woo mapea unicamente la clave `weight` por nombre. `formatted_weight` **nunca** se parsea (hay test de guarda). La ausencia de unidad se documenta como limitacion conocida en el docstring del adaptador y en la constatacion del spec. | Parsear una cadena localizada para sacar la unidad es adivinar; "N/A" traducido parseado como numero es exactamente el bug de 100x en otra forma. |
| C6 | Shopify tiene dos claves de peso (`grams` y `weight`+`weight_unit`) en unidades distintas, y la doc oficial de Shopify las muestra inconsistentes entre si. | **No** son candidatas de arbitraje (no son dos lecturas de la misma magnitud). La Tarea 1 fija **una sola** clave desde el fixture grabado y el adaptador nombra esa. | Arbitrar entre gramos y onzas compararia peras con manzanas; declararlas candidatas produciria `contradicho` espurio en cada fila. |
| C7 | Un `prices.price` ausente no encaja en ninguno de los tres motivos de `undeclared_reasons`. | Ausente, vacio o malformado -> `PRECIO_NO_PARSEABLE`. No se agrega un cuarto motivo. | El spec fija tres motivos y solo tres. La fila **se emite** igual (regla dura, mutacion 6). |
| C8 | Que hace `resolve` con una clave presente cuyo valor no mapea al vocabulario canonico cerrado (p.ej. un `stock_availability.class` desconocido). | Se descarta ese candidato del arbitraje y decide el otro; si ninguno mapea, `no_declarado`. Nunca se inventa un valor. **El descarte es admisible SOLO porque la Tarea 15 lo convierte en un test rojo:** una asercion sobre todo el corpus verifica que ningun valor presente en una clave mapeada cae fuera del vocabulario cerrado del adaptador. Si datos reales traen una clase desconocida, el arreglo es **ampliar el vocabulario**, nunca ensanchar el descarte. | Tratar lo desconocido como conflicto daria `contradicho` falso; adivinar el valor es el bug prohibido. Pero "la senal que no entendi la borro" tambien es silencioso, y el spec exige que ninguno de los motivos lo sea: la guarda de corpus es lo que lo hace ruidoso sin agregar un campo al informe (la cardinalidad de `rows_dropped` y `undeclared_reasons` es cerrada por el spec). |
| C9 | Que es `CatalogRow.url` (permalink del producto vs URL de la lectura). | La URL exacta de la **pagina de feed** que trajo esa fila. El permalink del producto queda dentro de `raw`. | El spec dice que `url`/`site`/`read_at` son metadatos cuya procedencia "es el lector mismo". Promover el permalink exigiria un octavo `Attribution`, y los siete atributos canonicos son cerrados. |
| C10 | Donde viven las guardas de frontera (vocabulario, imports, sin red, ASCII) que el spec no ubica. | Archivo nuevo `tests/test_catalog_boundaries.py`. | Precedente exacto del repo: `tests/test_pricing_intel_boot_safety.py`. Meterlas en `test_catalog_record.py` lo infla y mezcla dos propositos. |
| C11 | El sleeper del rate limit. | Keyword-only **obligatorio**, igual que `client`. `source.py` no importa `time`. | Un default `= time.sleep` deja la espera cableada en el modulo; obligarlo hace explicito cada call site (mismo argumento que el `client` obligatorio de `pdp_fetcher.py`) y el grep de `time.sleep` en `source.py` da cero. |
| C12 | Shopify no emite ningun campo equivalente a `currency_minor_unit`: el factor **no viaja**, y el spec 7 prohibe asumirlo. | `shopify.PRICE_MINOR_UNIT: int \| None` admite **exactamente dos valores: `0` o `None`**. `0` solo si el payload grabado declara la unidad mayor **por la forma del propio valor** (string con separador decimal, p.ej. `"199.00"`) Y la comparacion contra la ficha publica lo confirma; en cualquier otro caso, `None` y el precio Shopify queda `no_declarado` con `MINOR_UNIT_AUSENTE` contado en todas las filas. **El valor `2` esta prohibido por codigo y por test.** Ademas, con `PRICE_MINOR_UNIT == 0` el adaptador afirma el precio **solo** sobre raws cuya forma coincide con la grabada (string con separador decimal); un entero desnudo queda `no_declarado` + `MINOR_UNIT_AUSENTE`. | Un `2` global derivado de mirar UNA tienda en el navegador y aplicado a toda tienda Shopify del planeta es exactamente lo que el spec 7 prohibe, con el dano nombrado: `"19990.00"` sale `199.90` en cada fila, declarado, plausible y 100x abajo, sin fallar nunca. Y seria asimetrico con Woo, donde la ausencia del factor SI da `no_declarado`. El separador decimal no es una inferencia por magnitud (eso es el bug prohibido): es una declaracion del propio payload sobre la unidad en la que esta expresado el valor, del mismo tipo que leer una clave. La guarda de forma por fila cierra el 100x en la direccion contraria (una tienda que emita centavos enteros). |
| C13 | `_report_without_feed` solo separa 403 y fallo de transporte; que pasa con 429, 5xx, 3xx o mezclas. | Familias explicitas, en este orden: (1) denegacion `{401, 403, 407, 429, 451}` -> `bloqueado_waf`; (2) `STATUS_SIN_RESPUESTA`, 3xx y 5xx (o sea cualquier status que no sea 200 ni 404) -> `error_red`; (3) si **todos** los status estan en `{404, 200}` -> **una sola** lectura de la home; si hay huella -> `sin_feed_json` con la plataforma, si no -> `plataforma_desconocida`; (4) si la home no se pudo leer o no devolvio 200 -> `error_red` (o `bloqueado_waf` si su status es de la familia de denegacion), **nunca** `plataforma_desconocida`. El intento contra la home se registra en `routes_probed` como `("/", status)`. | Con la cascada original, cuatro 429, cuatro 500, cuatro 301 o cualquier mezcla 404+5xx caen en `plataforma_desconocida`, que el operador lee como "esta tienda no tiene catalogo": es el mismo falso negativo de sitio entero del 403 con otro numero. Un 200 con JSON de forma inesperada es funcionalmente "aca no hay feed", igual que un 404, asi que no impide la lectura de huella; cualquier otro status significa que no sabemos, y no saber es `error_red`. Y "no pude leer la home" no es un veredicto sobre la plataforma. Registrar el intento de home mantiene declarada la unica lectura de HTML del componente. |
| C14 | Que cuenta como candidato **presente** en `resolve`. Solo `None` no alcanza: WooCommerce publica `"sku": ""` cuando no esta seteado, y Shopify hace lo mismo con `sku`/`barcode`. | Un candidato esta presente solo si su raw es **escalar** (`str`, `int`, `float`, `bool`) y, cuando es `str`, solo si `raw.strip()` no es vacio. Todo lo demas (`None`, `""`, `"   "`, `{}`, `[]`) no es candidato y el atributo queda `no_declarado`. La regla vive **una sola vez** en `resolve`, no duplicada en cada adaptador. | Sin esto el lector AFIRMA un sku declarado cuyo valor es la cadena vacia en la mayoria de tiendas Woo reales: es el espejo exacto del bug prohibido (declarar donde el feed no dice nada), y el test de propiedad no lo atrapa porque `""` es una hoja literal del `raw`. Un raw contenedor (`{}`) es peor: daria `declarado` con evidencia `"{}"`, que ademas rompe la propiedad con un falso "evidencia inventada". |
| C15 | Politica de redirects: el plan no la declaraba y quedaba en el cliente inyectado (los tests con `MockTransport` no siguen redirects; el CLI si). | `_Requester.get` pasa `follow_redirects=True` **por pedido**, asi que test y campo ejercitan el mismo camino. `_Response` carga la URL final y `url`/`site` de cada fila salen de la respuesta que **efectivamente** entrego el feed, no del `base` pedido. | Con el default de httpx (no seguir), una tienda que hace 301 de `/products.json` caia en la bolsa de C13 -- falso negativo de tienda entera -- y la suite entera ejercitaba un camino distinto al de las cuatro corridas reales. Y si se siguen redirects sin corregir la procedencia, las filas quedan atribuidas a un host que no respondio, cuando el spec 5.2 dice que la procedencia de esos metadatos "es el lector mismo": tienen que decir la verdad de la lectura. |
| C16 | El feed declara cuantas **filas** tiene (`X-WP-Total`) y el plan observaba ese header y lo tiraba. | `ROW_TOTAL_HEADER: str \| None` entra al contrato de adaptador (`"X-WP-Total"` en Woo, `None` en Shopify). Se lee de la pagina 1, viaja al informe como `rows_declared: int \| None`, y **cuando no hubo ningun otro truncamiento** se compara contra `rows_emitted + suma(rows_dropped)`; si difieren, cuarto motivo `TOTAL_DECLARADO_NO_COINCIDE` (que por C2 degrada el `outcome`). | Sin la reconciliacion, la unica garantia de completitud es "le crei al header de paginas": con un `X-WP-TotalPages` cacheado por un CDN o un catalogo que cambia de tamano a mitad de la lectura, el informe dice `truncations == ()` y `ok` sobre una lectura parcial. El invariante que ya existe (`rows_emitted + rows_dropped == filas crudas recibidas`) mide **consistencia interna**, no completitud: se satisface perfecto con la mitad del catalogo. La condicion "solo si no hubo otro truncamiento" evita reportar dos veces el mismo corte. |
| C17 | `undeclared_reasons` tiene tres motivos y los tres son de precio/moneda, asi que una lectura donde el 100% de las filas quedo sin `sku`, `gtin`, `weight_declared` y `stock_status` produce un informe con `undeclared_reasons` vacio y `outcome == "ok"`. | `ReadReport.attribute_states: tuple[tuple[str, tuple[int, int, int]], ...]` -- por atributo canonico, los conteos `(declarado, no_declarado, contradicho)` -- poblado en `read_catalog` con una funcion **pura** `attribute_states_of(rows)` que vive en `record.py` y descubre los atributos con `iter_attributions`. El CLI **renderiza** ese campo en vez de calcularlo. | El informe que el spec 5.3 titula "la cobertura, declarada siempre" no declaraba la cobertura de cuatro de los siete atributos canonicos. La prueba de que el calculo es trivial y de que es parte del informe es que ya existia: `_coverage()` dentro del CLI, o sea fuera del tipo, invisible para un consumidor programatico o para un log del `ReadReport`. Descubrir los atributos por introspeccion hace que un atributo nuevo entre solo. |

---

## File Structure

| Archivo | Unica responsabilidad |
|---|---|
| `src/catalog/__init__.py` | **Crear.** Docstring + `from __future__ import annotations`. No importa submodulos (regla prod-boot). |
| `src/catalog/record.py` | **Crear.** Modulo puro: `Attribution` y sus tres estados, `CatalogRow`, `ReadReport`, `AdapterResult`, el arbitro `resolve`, `dig`, `evidence_of`, `scale_price`, `tally`, y las constantes cerradas de desenlaces y motivos. Cero I/O. |
| `src/catalog/adapters/__init__.py` | **Crear.** Tabla de despacho `ADAPTERS` (plataforma -> modulo). Sin logica. |
| `src/catalog/adapters/woo.py` | **Crear.** Mapeo explicito del esquema WooCommerce Store API: reconocimiento de forma, precio por `currency_minor_unit`, stock por dos claves (arbitraje real), categorias, peso. |
| `src/catalog/adapters/shopify.py` | **Crear.** Mapeo explicito de `/products.json`: reconocimiento de forma, precio con la unidad menor fijada como constante nombrada, sku/gtin/stock/peso por clave unica. |
| `src/catalog/source.py` | **Crear.** Lo unico que toca la red: `read_catalog`, sondeo de las cuatro rutas, huella de plataforma en la home, los dos paginadores, esperas inyectadas, y el armado del `ReadReport`. |
| `examples/run_catalog_read.py` | **Crear.** CLI `--url <tienda>`: corre la lectura real e imprime el `ReadReport` y el resumen de cobertura, ASCII-only. Fuera de CI. |
| `tests/fixtures/catalog/` | **Crear.** Payloads reales grabados (3 variantes Woo, Shopify, vacio, 403, homes de huella) + `PROVENANCE.md` + `DECISIONS.json` (las tres decisiones empiricas de la Tarea 1, legibles por maquina). |
| `tests/test_catalog_record.py` | **Crear.** Modulo puro: tipos, tres estados, arbitraje, mapeo canonico por plataforma, escalado por `currency_minor_unit` y su ausencia, y la propiedad central sobre todo el corpus. |
| `tests/test_catalog_source.py` | **Crear.** Red simulada con `httpx.MockTransport`: sondeo, 403, huellas, paginado Woo y Shopify, lectura parcial, esperas, invariante de conteos. |
| `tests/test_catalog_boundaries.py` | **Crear.** Guardas de frontera y nada mas (C10): vocabulario de dominio, imports prohibidos, pureza de `record.py`, ASCII del arbol y del archivo del CLI, placeholders sin rellenar, factor de precio de Shopify. |
| `tests/test_catalog_cli.py` | **Crear.** Los tests del CLI que no son guardas de frontera: renderizado del informe y el gate de `robots.txt` del llamador (funcion pura, offline). |
| `docs/superpowers/specs/2026-07-25-lector-catalogo-publico-design.md` | **Modificar.** Se le agrega la seccion 7.2 con la constatacion empirica de la Tarea 1 (lo pide el propio spec en 7.1). |

---

### Task 1: Grabar payloads reales y fijar las tres decisiones empiricas

Tarea de **verificacion empirica**, no de codigo. Es la primera del plan porque el spec 7.1 lo ordena: el esquema de Woo "aparentemente" no lleva peso y Shopify "aparentemente" expone `variants[].grams`, y las dos cosas estan sin confirmar contra payload real. Entregable: fixtures reales, `DECISIONS.json` (las tres decisiones en formato legible por maquina, que es lo que los tests leen) y la constatacion escrita en el spec. Ningun `.py` de `src/` se toca aca.

**Files:**
- Create: `tests/fixtures/catalog/woo_v1_route.json`, `tests/fixtures/catalog/woo_store_route.json`, `tests/fixtures/catalog/woo_indexphp_route.json`, `tests/fixtures/catalog/shopify_products.json`, `tests/fixtures/catalog/woo_empty_page.json`, `tests/fixtures/catalog/waf_403.txt`, `tests/fixtures/catalog/home_jumpseller.html`, `tests/fixtures/catalog/home_generic.html`, `tests/fixtures/catalog/PROVENANCE.md`, `tests/fixtures/catalog/DECISIONS.json`
- Modify: `docs/superpowers/specs/2026-07-25-lector-catalogo-publico-design.md`
- Test: no aplica (no hay codigo). La verificacion es el chequeo de forma del paso 5, que corre sobre los archivos grabados.

**Interfaces:**
- Consumes: nada.
- Produces: los nombres de archivo de fixture de arriba (las tareas 6-15 los cargan por esos nombres exactos), y **tres valores de decision** que las tareas 8 y 9 consumen. Los nombres van escritos **calificados, tal como van a existir en el codigo** (no hay ningun simbolo `WOO_WEIGHT_KEYS` a nivel de modulo: la constante se llama `WEIGHT_KEYS` dentro de `woo.py`):
  - `woo.WEIGHT_KEYS: tuple[str, ...]` = `("weight",)` o `()`.
  - `shopify.WEIGHT_KEYS: tuple[str, ...]` = `("variants.0.grams",)` o `("variants.0.weight",)` o `()`.
  - `shopify.PRICE_MINOR_UNIT: int | None` = `0` o `None` (C12: el `2` esta prohibido).
- Produces tambien, y esto es lo que hace que las decisiones **no** viajen solo en prosa: `tests/fixtures/catalog/DECISIONS.json`, commiteado, legible por maquina, con la evidencia al lado. Las Tareas 8 y 9 no ramifican sobre la constante que estan testeando: cruzan la constante contra este archivo y contra la forma del fixture.

- [ ] **Step 0: Fijar los ocho hosts antes de tocar la red**

Sin esto la tarea no se puede correr: las Tareas 6-15 cargan los fixtures por nombre exacto y las Tareas 8-9 hardcodean constantes derivadas de ellos, asi que un `TIENDA_*` sin resolver bloquea el DAG entero.

**Candidatos verificados que ya existen en el arbol** (rama `chore/config-sites-cl`, `config/sites/*.yaml`, `robots_checked_at: 2026-07-24`). Leerlos desde el worktree, que es lectura y no toca el repo principal:

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
git show chore/config-sites-cl:config/sites/todofrio.cl.yaml
git show feat/radiografia-congelados-cl:docs/superpowers/specs/2026-07-24-radiografia-congelados-chile-design.md
```

| Fixture | Host candidato | Ruta esperada | Estado del gate |
|---|---|---|---|
| `woo_v1_route.json` | `todofrio.cl` | ruta 1 (`wc/store/v1`) | robots leido 2026-07-24 **para rutas de ficha**, NO para la ruta del feed -> re-chequear (Step 1) |
| `woo_store_route.json` | `delmaratuhogar.cl` | ruta 2 (`wc/store`) | idem |
| `woo_indexphp_route.json` | por descubrir (permalinks planos) | ruta 3 (`index.php/wc/store`) | idem |
| `shopify_products.json` | `mariscosdelaconcha.cl` | ruta 4 (`/products.json`) | idem |
| `woo_empty_page.json` | el Woo mas chico de los tres | ruta que respondio + `&page=999` | idem |
| `waf_403.txt` | por descubrir | cualquiera que devuelva 403 | idem |
| `home_jumpseller.html` | por descubrir (Jumpseller) | `/` | idem |
| `home_generic.html` | `mardeli.cl` (ni Jumpseller ni Bsale) | `/` | idem |

**Los hosts candidatos NO son un hecho: son un punto de partida.** Cada uno se confirma sondeando las cuatro rutas antes de grabar. Comando de descubrimiento, que sirve tanto para confirmar un candidato como para resolver los tres "por descubrir":

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
PY=C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe
$PY -c "
import sys, httpx
UA = {'User-Agent': 'KernCatalogReader/1.0 (+https://kern.example/catalog-reader)'}
ROUTES = ('/wp-json/wc/store/v1/products?per_page=100', '/wp-json/wc/store/products?per_page=100',
          '/index.php/wp-json/wc/store/products?per_page=100', '/products.json?limit=250')
base = sys.argv[1].rstrip('/')
for route in ROUTES:
    try:
        r = httpx.get(base + route, timeout=20, headers=UA, follow_redirects=True)
        print(route, r.status_code, (r.headers.get('content-type') or '')[:40], len(r.text))
    except Exception as exc:
        print(route, 'ERR', type(exc).__name__)
home = httpx.get(base + '/', timeout=20, headers=UA, follow_redirects=True)
low = home.text.lower()
print('home', home.status_code, 'jumpseller' if 'jumpseller' in low else '', 'bsale' if 'bsale' in low else '')
" https://HOST
```

Reglas de resolucion, escritas de antemano:
- **Ruta 3 (permalinks planos):** es la mas rara. Si ninguno de los candidatos responde en la ruta 3, sondear los otros hosts del set de referencia (`frozt.cl`, `southwind.cl`) y, si tampoco, **sintetizar** el fixture copiando `woo_store_route.json` y anotando en `PROVENANCE.md`: "ruta 3 sintetizada desde la ruta 2 el `<fecha>`; las rutas 1-2-3 son alias del MISMO set v1 (verificado contra el codigo de WooCommerce 10.9.4), asi que el payload es identico por construccion y lo unico que la ruta 3 cambia es la URL sondeada". Es honesto porque el hecho de esquema esta verificado, y queda declarado.
- **403:** si ninguna tienda devuelve 403 el dia del grabado, aplicar la regla que ya esta en el Step 3 (cuerpo placeholder ASCII + nota).
- **Jumpseller:** IceFish figura como Jumpseller en las notas de la rama archivada, sin host. Si no se resuelve, sondear cualquier tienda chilena en Jumpseller y, si no aparece ninguna, **sintetizar** `home_jumpseller.html` con un fragmento HTML minimo que contenga `cdn.jumpseller.com` y anotarlo en `PROVENANCE.md`. Lo que el test necesita es el marcador, no la tienda.
- **Host caido el dia del grabado:** reemplazarlo por el siguiente candidato de su misma plataforma y anotar el reemplazo en `PROVENANCE.md`. Nunca dejar un fixture sin fila de procedencia.

Escribir la tabla resuelta (host real por fixture) en `PROVENANCE.md` en el Step 4.

- [ ] **Step 1: Escribir el grabador (throwaway, fuera del repo) y chequear el gate del llamador**

El gate de `robots.txt` es del llamador (D7), y en esta tarea el llamador sos vos. **El gate es bloqueante y su criterio es ejecutable, no "a mano":** un print de 2000 caracteres no dice que grupo `User-agent` aplica, ni como resuelve `Allow` contra `Disallow`, ni que hacer con los wildcards. La regla es `urllib.robotparser`, que ya implementa esa semantica, y se corre **por ruta**:

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
PY=C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe
$PY -c "
import sys
from urllib.robotparser import RobotFileParser
UA = 'KernCatalogReader/1.0'
ROUTES = ('/wp-json/wc/store/v1/products?per_page=100', '/wp-json/wc/store/products?per_page=100',
          '/index.php/wp-json/wc/store/products?per_page=100', '/products.json?limit=250', '/')
base = sys.argv[1].rstrip('/')
rp = RobotFileParser()
rp.set_url(base + '/robots.txt')
try:
    rp.read()
    read_ok = True
except Exception as exc:
    print('robots.txt no se pudo leer:', type(exc).__name__)
    read_ok = False
for route in ROUTES:
    print(('OK   ' if (not read_ok or rp.can_fetch(UA, base + route)) else 'BLOCK'), route)
" https://HOST
```

Decisiones fijadas de antemano sobre el propio `robots.txt` (para que el gate no dependa de la lectura del dia):
- `robots.txt` con **404, 5xx, vacio o ilegible** -> se permite, y se anota `robots: n/a (<status>)` en la fila del fixture.
- **Cualquier** ruta con `BLOCK` -> **la tienda se descarta para esa ruta**. Si la ruta bloqueada es la que se iba a grabar, se descarta la tienda entera y se pasa al siguiente candidato del Step 0.
- El booleano por ruta va a una columna nueva de la tabla de `PROVENANCE.md`, para que el gate quede auditable y no haya que confiar en la memoria de quien lo corrio.

Escribir el grabador en el scratchpad (NO en el repo, NO commitearlo):

```python
# C:/Users/Gamer/AppData/Local/Temp/claude/C--Users-Gamer-Music-scm/f7886b41-e072-4a3d-84da-8dd79cac168f/scratchpad/record_catalog_fixture.py
"""Graba un payload real de catalogo en tests/fixtures/catalog/.

Uso:
  python record_catalog_fixture.py <url_completa> <archivo_destino> [--json-head N] [--text-head N]

--json-head N  recorta el arreglo de productos a los primeros N elementos (payload gigante).
--text-head N  guarda solo los primeros N caracteres del cuerpo (HTML de home).
Imprime status y los headers que importan para el paginado.
"""
import json
import sys
from pathlib import Path

import httpx

UA = "KernCatalogReader/1.0 (+https://kern.example/catalog-reader)"


def main() -> int:
    url, destination = sys.argv[1], Path(sys.argv[2])
    json_head = int(sys.argv[sys.argv.index("--json-head") + 1]) if "--json-head" in sys.argv else None
    text_head = int(sys.argv[sys.argv.index("--text-head") + 1]) if "--text-head" in sys.argv else None

    response = httpx.get(url, timeout=20.0, headers={"User-Agent": UA}, follow_redirects=True)
    print("status         :", response.status_code)
    print("content-type   :", response.headers.get("content-type"))
    print("X-WP-Total     :", response.headers.get("X-WP-Total"))
    print("X-WP-TotalPages:", response.headers.get("X-WP-TotalPages"))

    destination.parent.mkdir(parents=True, exist_ok=True)
    if text_head is not None:
        destination.write_text(response.text[:text_head], encoding="utf-8")
        print("wrote text     :", destination, len(response.text[:text_head]), "chars")
        return 0

    payload = json.loads(response.text)
    if json_head is not None:
        if isinstance(payload, list):
            payload = payload[:json_head]
        elif isinstance(payload, dict) and isinstance(payload.get("products"), list):
            payload = {**payload, "products": payload["products"][:json_head]}
    destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote json     :", destination)
    print("top-level type :", type(payload).__name__)
    print("row count      :", len(payload if isinstance(payload, list) else payload.get("products", [])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Anonimizacion y recorte: los payloads de catalogo son **publicos y sin PII** (nombre de producto, precio, sku, categoria), asi que no se redacta contenido; lo unico que se hace es **recortar** con `--json-head 3` para que el fixture no pese megas, y `--text-head 4000` para las homes. No borrar claves: el fixture tiene que seguir siendo el esquema real, y el test de propiedad compara `evidence` contra `raw`.

- [ ] **Step 2: Grabar las tres variantes de ruta Woo y la de Shopify**

Los hosts son los que el Step 0 dejo **resueltos y con el gate en OK**; `TIENDA_*` de aca en adelante significa "el host que el Step 0 fijo para ese fixture", no un placeholder pendiente. Si el sondeo del Step 0 mostro que una tienda cambio de ruta, se graba la que respondio y se anota en `PROVENANCE.md`. Cuatro comandos:

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
PY=C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe
REC=C:/Users/Gamer/AppData/Local/Temp/claude/C--Users-Gamer-Music-scm/f7886b41-e072-4a3d-84da-8dd79cac168f/scratchpad/record_catalog_fixture.py

$PY $REC "https://TIENDA_WOO_V1/wp-json/wc/store/v1/products?per_page=100"          tests/fixtures/catalog/woo_v1_route.json      --json-head 3
$PY $REC "https://TIENDA_WOO_STORE/wp-json/wc/store/products?per_page=100"          tests/fixtures/catalog/woo_store_route.json   --json-head 3
$PY $REC "https://TIENDA_WOO_FLAT/index.php/wp-json/wc/store/products?per_page=100" tests/fixtures/catalog/woo_indexphp_route.json --json-head 3
$PY $REC "https://TIENDA_SHOPIFY/products.json?limit=250"                           tests/fixtures/catalog/shopify_products.json   --json-head 3
```

Anotar de la salida de cada comando: `status`, `X-WP-TotalPages` (existe o no), `top-level type` y `row count`. Van a `PROVENANCE.md`.

Dato de esquema ya verificado contra el codigo fuente de WooCommerce 10.9.4 (no re-investigar): las rutas 1 y 2 son **alias del mismo set v1** (`RoutesController::register_all_routes` registra `wc/store` y `wc/store/v1` con el mismo schema), asi que las dos fixtures deben tener forma identica; si difieren, la tienda tiene otra cosa en el medio y hay que anotarlo.

- [ ] **Step 3: Grabar el caso vacio, el 403 y las dos homes**

```bash
# Pagina vacia: pedir una pagina mas alla del final de la tienda Woo mas chica.
$PY $REC "https://TIENDA_WOO_V1/wp-json/wc/store/v1/products?per_page=100&page=999" tests/fixtures/catalog/woo_empty_page.json

# 403: sondear la tienda que en campo bloqueo (Shopify dispara captcha / WAF).
$PY $REC "https://TIENDA_CON_WAF/wp-json/wc/store/v1/products?per_page=100" tests/fixtures/catalog/waf_403.txt --text-head 2000

# Homes para la huella de plataforma.
$PY $REC "https://TIENDA_JUMPSELLER/" tests/fixtures/catalog/home_jumpseller.html --text-head 4000
$PY $REC "https://TIENDA_SIN_HUELLA/" tests/fixtures/catalog/home_generic.html     --text-head 4000
```

Reglas de aceptacion de los cuatro feeds del Step 2 (van en el chequeo del Step 4, no son opcionales). Seis tests posteriores alinean filas contra payload **por indice** (`zip(result.rows, _WOO_V1)`) asumiendo cero descartes: si una de las 3 filas recortadas viene sin `name` (Woo) o sin `title` (Shopify), el adaptador la descarta con `SIN_TITULO`, los indices se corren y esos tests comparan la fila equivocada -- pueden pasar en verde comparando pares mal alineados, que es exactamente la clase de falla silenciosa que este componente existe para no tener.
- Las 3 filas de cada fixture Woo tienen que traer `name` no vacio, y las 3 de Shopify `title` no vacio. Si no, subir `--json-head` hasta obtener 3 filas con titulo y volver a recortar.
- Las rutas 1 y 2 tienen que dar forma identica (son alias del mismo set v1). Si difieren, anotarlo.

Reglas de aceptacion de estos cuatro:
- `woo_empty_page.json` tiene que ser `[]`. Si la tienda devuelve la ultima pagina en vez de vacio, probar con otra tienda Woo.
- `waf_403.txt`: si ninguna tienda devuelve 403 el dia del grabado, escribir el archivo con la unica linea ASCII `403 Forbidden` y anotar en `PROVENANCE.md`: "sin 403 en vivo el <fecha>; cuerpo placeholder. El test de `bloqueado_waf` assertea sobre el status, nunca sobre el cuerpo." El desenlace `bloqueado_waf` se decide por status, no por contenido.
- `home_jumpseller.html`: verificar que el marcador quedo **dentro** del recorte:
  `grep -io "jumpseller" tests/fixtures/catalog/home_jumpseller.html | head -3` tiene que devolver al menos un hit. Si no, subir `--text-head` a 12000 y repetir.
- `home_generic.html`: verificar lo contrario:
  `grep -ic "jumpseller\|bsale" tests/fixtures/catalog/home_generic.html` tiene que devolver `0`.

- [ ] **Step 4: Resolver las tres decisiones y escribir la constatacion**

Correr el chequeo de forma sobre lo grabado. Este comando **es** la verificacion de la tarea:

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -c "
import json
from pathlib import Path
F = Path('tests/fixtures/catalog')
woo = json.loads((F/'woo_v1_route.json').read_text(encoding='utf-8'))
shp = json.loads((F/'shopify_products.json').read_text(encoding='utf-8'))
assert isinstance(woo, list) and woo, 'woo fixture debe ser un arreglo no vacio'
assert isinstance(shp, dict) and shp.get('products'), 'shopify fixture debe ser {products: [...]}'
assert all(isinstance(p, dict) and str(p.get('name') or '').strip() for p in woo), 'el recorte Woo debe traer 3 filas con titulo'
assert all(isinstance(p, dict) and str(p.get('title') or '').strip() for p in shp['products']), 'el recorte Shopify debe traer 3 filas con titulo'
w0, s0 = woo[0], shp['products'][0]
print('WOO keys        :', sorted(w0))
print('WOO weight?     :', 'weight' in w0, repr(w0.get('weight')))
print('WOO formatted?  :', 'formatted_weight' in w0, repr(w0.get('formatted_weight')))
print('WOO prices      :', json.dumps(w0.get('prices'), ensure_ascii=False)[:400])
print('WOO minor_unit  :', repr((w0.get('prices') or {}).get('currency_minor_unit')))
print('WOO categories  :', [c.get('name') for c in (w0.get('categories') or [])])
print('WOO is_in_stock :', repr(w0.get('is_in_stock')), 'availability:', repr(w0.get('stock_availability')))
v0 = (s0.get('variants') or [{}])[0]
print('SHP prod keys   :', sorted(s0))
print('SHP var keys    :', sorted(v0))
print('SHP handle      :', repr(s0.get('handle')))
print('SHP price       :', repr(v0.get('price')), type(v0.get('price')).__name__)
print('SHP price shape :', 'string decimal' if (isinstance(v0.get('price'), str) and '.' in v0['price']) else 'otra forma')
print('SHP grams       :', repr(v0.get('grams')), 'weight:', repr(v0.get('weight')), 'unit:', repr(v0.get('weight_unit')))
print('SHP currency?   :', repr(s0.get('currency')))
print('SHP empty page  :', json.loads((F/'woo_empty_page.json').read_text(encoding='utf-8')))
"
```

Ahora aplicar las tres tablas de decision. **Las dos ramas de cada una estan fijadas de antemano; no se vuelve a preguntar.**

**Decision A - peso en Woo** (`woo.WEIGHT_KEYS`, la consume la Tarea 8):

| Observacion | Decision | Que se escribe |
|---|---|---|
| `WOO weight? True` | `woo.WEIGHT_KEYS = ("weight",)` | Se mapea `weight` por nombre explicito, como cualquier otra clave (regla del spec 7.1, que no se re-litiga). `weight_declared` queda `declarado` en las filas que la traen. |
| `WOO weight? False` | `woo.WEIGHT_KEYS = ()` | `weight_declared` queda `no_declarado` en Woo, siempre. Se documenta en el spec. **En ningun caso** se busca el peso en `attributes[]` por heuristica. |

En las dos ramas: `formatted_weight` **no se parsea nunca** (C5). Contexto ya verificado (no re-investigar): `weight` existe en el `ProductSchema` de la Store API a partir de WooCommerce **10.7.0** (commit del 2026-02-22); en 10.6.0 y anteriores no existe, y ese es todavia la mayor parte del parque instalado. O sea: las dos ramas estan vivas y la observacion depende de la version de la tienda grabada. Si `weight` aparece, anotar la version de Woo si el sitio la expone.

**La unidad del peso NO viaja en el payload de la Store API** (es un setting de sitio), asi que la rama `("weight",)` declara una **magnitud sin unidad**. Eso es una limitacion real y el spec 7.1 igual manda mapearla (el lector carga las fuentes, decidir es del normalizador de peso, §5.2). Para que no se lea como kilos, la rama `("weight",)` obliga a escribir, en los tres lugares, la misma frase: en el docstring del adaptador, en `DECISIONS.json` (campo `woo_weight_unit_declared: false`) y en la seccion 7.2 del spec -> **"`weight_declared` en Woo no lleva unidad declarada; el consumidor no puede tratarlo como kg"**. Cuando el normalizador de peso se traiga a esta base (no en esta rama), el enganche `feed_weight_kg` tiene que leer ese campo antes de usar el numero.

**Decision B - clave de peso en Shopify** (`shopify.WEIGHT_KEYS`, la consume la Tarea 9):

| Observacion | Decision | Que se escribe |
|---|---|---|
| `grams` presente (con o sin `weight`) | `shopify.WEIGHT_KEYS = ("variants.0.grams",)` | Se mapea solo `grams` (unidad explicita: gramos, declarada **en el nombre de la clave**, que es una declaracion del esquema y no una suposicion). `weight`/`weight_unit` se dejan sin leer **a proposito** y se anota por que: la propia doc de Shopify muestra `grams` y `weight`+`weight_unit` inconsistentes entre si en su ejemplo, asi que derivar uno del otro no tiene base. |
| `grams` ausente y `weight` presente | `shopify.WEIGHT_KEYS = ("variants.0.weight",)` | Se mapea `weight`. Si `weight_unit` tambien esta, se anota su valor en la constatacion pero **no** se mapea (seria un octavo atributo canonico y los siete son cerrados); la unidad queda como limitacion documentada, con la misma frase obligatoria de la Decision A. |
| ninguna presente | `shopify.WEIGHT_KEYS = ()` | `weight_declared` queda `no_declarado` tambien en Shopify. **Este desenlace es admisible y no obliga a rediseno**: significa que la contradiccion peso-feed contra peso-titulo no se activa en ninguna plataforma de v1, y se escribe asi en el spec. |

**Decision C - unidad menor del precio en Shopify** (`shopify.PRICE_MINOR_UNIT`, la consume la Tarea 9). El factor **no viaja en el payload** (no hay equivalente de `currency_minor_unit`), y las dos superficies oficiales de Shopify discrepan: la Ajax API devuelve centavos como entero (`12900`) y Admin REST devuelve string decimal (`"199.00"`). No se infiere por magnitud en tiempo de corrida, y **no se afirma un factor global de plataforma a partir de una sola tienda mirada en el navegador**: eso es lo que el spec 7 prohibe, y por C12 los unicos valores admisibles son `0` y `None`.

Procedimiento, con los dos comandos que lo hacen reproducible (el `handle` sale del print que el Step 4 ya imprime):

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
PY=C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe
REC=C:/Users/Gamer/AppData/Local/Temp/claude/C--Users-Gamer-Music-scm/f7886b41-e072-4a3d-84da-8dd79cac168f/scratchpad/record_catalog_fixture.py
SCRATCH=C:/Users/Gamer/AppData/Local/Temp/claude/C--Users-Gamer-Music-scm/f7886b41-e072-4a3d-84da-8dd79cac168f/scratchpad

# 1) el payload de la ficha del mismo producto (misma superficie no documentada)
$PY $REC "https://TIENDA_SHOPIFY/products/<handle>.json" $SCRATCH/shp_pdp.json

# 2) el precio MOSTRADO, sin interpretar la pagina: el meta OpenGraph de precio
$PY -c "
import re, sys, httpx
UA = {'User-Agent': 'KernCatalogReader/1.0 (+https://kern.example/catalog-reader)'}
html = httpx.get(sys.argv[1], timeout=20, headers=UA, follow_redirects=True).text
print(re.findall(r'product:price:amount\"[^>]*content=\"([^\"]*)\"', html)[:3])
print(re.findall(r'content=\"([^\"]*)\"[^>]*product:price:amount', html)[:3])
" "https://TIENDA_SHOPIFY/products/<handle>"
```

| Observacion | Decision | Que se escribe |
|---|---|---|
| `SHP price shape` es **string decimal** (`"19990.00"`) Y el valor del payload es igual al precio mostrado | `shopify.PRICE_MINOR_UNIT = 0` | Constante con comentario **relleno de verdad**: `# verificado contra <host>/products/<handle> el <fecha>: payload "<valor>" == precio mostrado <valor>`. La forma del propio valor (separador decimal) es la evidencia de que ya esta en unidad mayor; el numero del navegador solo la confirma. Ademas el adaptador afirma el precio **solo** sobre raws de esa misma forma (C12). |
| cualquier otra cosa: entero desnudo, forma mixta, el valor no coincide ni x1 ni x100 con el precio mostrado, o no se pudo leer el precio mostrado | `shopify.PRICE_MINOR_UNIT = None` | El precio Shopify queda `no_declarado` en **todas** las filas con `MINOR_UNIT_AUSENTE` contado, exactamente como Woo sin `currency_minor_unit`. Se escribe en el spec que Shopify no declara el factor y por eso no se afirma el precio. **Menos cobertura es el desenlace correcto; un precio 100x mal no lo es.** |

Nota sobre la rama que el plan **elimino**: el disparador original "dos tiendas Shopify grabadas discrepan entre si" era inevaluable, porque esta tarea graba UNA sola tienda Shopify. El criterio de arriba se decide con una sola tienda y no necesita la segunda.

Escribir `tests/fixtures/catalog/PROVENANCE.md` (utf-8, acentos permitidos):

```markdown
# Procedencia de los fixtures de catálogo

Payloads públicos grabados con el grabador descartable de la Tarea 1. Sin PII:
sólo nombre de producto, precio, sku, categoría y stock. Único tratamiento
aplicado: recorte (`--json-head 3` en los feeds, `--text-head` en los HTML).

| Fixture | Host | URL grabada | Fecha | Status | robots.txt (esa ruta) | X-WP-Total | X-WP-TotalPages | Filas |
|---|---|---|---|---|---|---|---|---|
| woo_v1_route.json | ... | ... | 2026-07-.. | 200 | OK / n/a (404) | ... | ... | 3 |
| woo_store_route.json | ... | ... | | 200 | | | | 3 |
| woo_indexphp_route.json | ... | ... | | 200 | | | | 3 |
| shopify_products.json | ... | ... | | 200 | | (n/a) | (n/a) | 3 |
| woo_empty_page.json | ... | ...&page=999 | | 200 | | | | 0 |
| waf_403.txt | ... | ... | | 403 | | | | (n/a) |
| home_jumpseller.html | ... | / | | 200 | | (n/a) | (n/a) | (n/a) |
| home_generic.html | ... | / | | 200 | | (n/a) | (n/a) | (n/a) |

Reemplazos y sintesis (si hubo): ...

## Decisiones fijadas por este grabado

Los valores canonicos viven en `DECISIONS.json`, que es lo que los tests leen.
Esta seccion es la version legible por humanos y tiene que decir lo mismo:

- `woo.WEIGHT_KEYS` = ...  (unidad declarada en el payload: NO)
- `shopify.WEIGHT_KEYS` = ...
- `shopify.PRICE_MINOR_UNIT` = ...  (evidencia: host, handle, fecha, payload, precio mostrado)
```

Y escribir `tests/fixtures/catalog/DECISIONS.json` (ASCII, commiteado). **Este archivo es el que hace que las decisiones no viajen solo en prosa:** las Tareas 8 y 9 lo leen y cruzan sus constantes contra el, en vez de ramificar sobre la constante que estan testeando.

```json
{
  "recorded_at": "2026-07-..",
  "woo_weight_keys": ["weight"],
  "woo_weight_unit_declared": false,
  "shopify_weight_keys": ["variants.0.grams"],
  "shopify_price_minor_unit": 0,
  "shopify_price_evidence": {
    "host": "...",
    "handle": "...",
    "checked_at": "2026-07-..",
    "payload_value": "19990.00",
    "payload_shape": "string decimal",
    "displayed_price": "19990"
  }
}
```

Reglas duras del archivo, verificadas por la Tarea 16: `shopify_price_minor_unit` solo puede ser `0` o `null`; si es `0`, `payload_shape` tiene que ser `"string decimal"`; ningun campo puede quedar con un `<placeholder>` sin rellenar.

`DECISIONS.json` **no es un payload de feed**, asi que queda fuera del corpus de la propiedad central: la Tarea 15 lo excluye por una constante nombrada (`NON_FEED_FIXTURES`), no por un glob mas estrecho -- el glob sigue siendo lo que hace que un fixture de feed nuevo entre solo.

Y agregar al final del spec (`docs/superpowers/specs/2026-07-25-lector-catalogo-publico-design.md`) la seccion que el propio 7.1 pide:

```markdown
### 7.2 Constatación empírica (Tarea 1 del plan, <fecha>)

Payloads reales grabados en `tests/fixtures/catalog/` (procedencia en
`PROVENANCE.md`). Lo que 7.1 dejaba por verificar:

- **Peso en Woo:** <SÍ / NO> trae peso. Clave mapeada: `<weight | ninguna>`.
  `formatted_weight` no se parsea nunca: es una cadena localizada y traducible,
  y devuelve el texto traducido "N/A" cuando el peso no está seteado. La unidad
  del peso **no viaja en el payload** de la Store API (es un setting de sitio),
  así que `weight_declared` en Woo queda sin unidad declarada: **el consumidor
  no puede tratarlo como kg**, y el enganche futuro `feed_weight_kg` tiene que
  leer `woo_weight_unit_declared` de `DECISIONS.json` antes de usar el número.
  Nota de versión: la Store API sólo declara `weight` desde WooCommerce 10.7.0.
- **Peso en Shopify:** clave mapeada `<variants[].grams | variants[].weight | ninguna>`.
  Las otras claves de peso se dejan sin leer a propósito (unidades distintas, y
  la propia documentación de Shopify las muestra inconsistentes entre sí).
- **Unidad menor del precio en Shopify:** `<0 | no declarada>` (el valor `2` está
  prohibido: afirmar un factor de plataforma desde una sola tienda es lo que §7
  prohíbe). Con `0`, la evidencia es doble: la **forma** del propio valor en el
  payload (string con separador decimal, o sea ya en unidad mayor) y la
  comparación contra el precio mostrado en la ficha pública de `<handle>`; y el
  adaptador sólo afirma el precio sobre valores de esa misma forma. Shopify no
  emite ningún campo equivalente a `currency_minor_unit`, así que cuando la forma
  no lo declara el precio queda `no_declarado` con `MINOR_UNIT_AUSENTE` contado,
  exactamente como Woo sin `currency_minor_unit`. Nunca una inferencia por
  magnitud del número.
- **Consecuencia sobre la contradicción peso-feed vs peso-título:** <se activa
  sólo en <plataforma> | no se activa en ninguna plataforma de v1>. En esta rama
  no se implementa igual (§5.2 y §11): el lector carga las fuentes, no decide.
```

- [ ] **Step 5: Commit**

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
git add tests/fixtures/catalog/woo_v1_route.json tests/fixtures/catalog/woo_store_route.json \
        tests/fixtures/catalog/woo_indexphp_route.json tests/fixtures/catalog/shopify_products.json \
        tests/fixtures/catalog/woo_empty_page.json tests/fixtures/catalog/waf_403.txt \
        tests/fixtures/catalog/home_jumpseller.html tests/fixtures/catalog/home_generic.html \
        tests/fixtures/catalog/PROVENANCE.md tests/fixtures/catalog/DECISIONS.json \
        docs/superpowers/specs/2026-07-25-lector-catalogo-publico-design.md \
        docs/superpowers/plans/2026-07-25-lector-catalogo-publico.md
git commit -m "test(catalog): fixtures reales de Woo y Shopify y constatacion del peso

Graba las tres variantes de ruta Woo, Shopify /products.json, una pagina vacia,
un 403 y dos homes de huella de plataforma. Resuelve las tres decisiones que el
spec 7.1 dejaba por verificar (peso en Woo, clave de peso en Shopify, unidad
menor del precio en Shopify) y las escribe en el spec y en PROVENANCE.md."
```

---

### Task 2: `Attribution` y sus tres estados

**Files:**
- Create: `src/catalog/__init__.py`, `src/catalog/record.py`
- Test: `tests/test_catalog_record.py`

**Interfaces:**
- Consumes: nada.
- Produces:
  - `AttributionState = Literal["declarado", "no_declarado", "contradicho"]` y `ATTRIBUTION_STATES: tuple[str, ...]`.
  - `@dataclass(frozen=True) class Attribution` con campos, en este orden: `value: str | None = None`, `source: str | None = None`, `evidence: str | None = None`, `conflicts: tuple[tuple[str, str], ...] = ()`; y la propiedad `state -> AttributionState`.
  - `SOURCE_PREFIX_FEED = "feed:"`, `SOURCE_TITULO = "titulo"`, `ALLOWED_SOURCE_PREFIXES: tuple[str, ...]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_catalog_record.py
"""Tests del modulo puro src/catalog/record.py.

Ningun test de este archivo toca la red: record.py no importa httpx y todo
corre contra los payloads grabados en tests/fixtures/catalog/ (Tarea 1) o
contra payloads derivados de esos payloads reales.
"""

from __future__ import annotations

import dataclasses
import socket

import pytest

from src.catalog.record import (
    ALLOWED_SOURCE_PREFIXES,
    ATTRIBUTION_STATES,
    SOURCE_PREFIX_FEED,
    Attribution,
)


@pytest.fixture(autouse=True)
def _no_real_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ningun test de catalogo abre un socket. record.py es puro y no deberia
    poder ni intentarlo; esta guarda lo vuelve verificable."""

    def _blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("a catalog test tried to open a real socket")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked)


def test_attribution_is_frozen() -> None:
    attribution = Attribution(value="19.90", source="feed:prices.price", evidence="1990")
    with pytest.raises(dataclasses.FrozenInstanceError):
        attribution.value = "0.00"  # type: ignore[misc]


def test_attribution_field_names_order_and_defaults_are_exact() -> None:
    assert [(f.name, f.default) for f in dataclasses.fields(Attribution)] == [
        ("value", None),
        ("source", None),
        ("evidence", None),
        ("conflicts", ()),
    ]


def test_conflicts_is_a_tuple_of_two_string_tuples_never_a_list() -> None:
    attribution = Attribution(conflicts=(("feed:is_in_stock", "true"), ("feed:stock_availability.class", "out-of-stock")))
    assert isinstance(attribution.conflicts, tuple)
    for entry in attribution.conflicts:
        assert isinstance(entry, tuple) and len(entry) == 2
        assert all(isinstance(part, str) for part in entry)


def test_there_are_exactly_three_states_never_a_two_valued_boolean() -> None:
    assert ATTRIBUTION_STATES == ("declarado", "no_declarado", "contradicho")
    assert len(ATTRIBUTION_STATES) == 3


@pytest.mark.parametrize(
    "attribution, expected",
    [
        (Attribution(value="19.90", source="feed:prices.price", evidence="1990"), "declarado"),
        (Attribution(), "no_declarado"),
        (Attribution(value="19.90", source="feed:prices.price"), "no_declarado"),
        (Attribution(evidence="1990", source="feed:prices.price"), "no_declarado"),
        (Attribution(conflicts=(("feed:a", "1"), ("feed:b", "2"))), "contradicho"),
        (
            Attribution(value="19.90", evidence="1990", conflicts=(("feed:a", "1"), ("feed:b", "2"))),
            "contradicho",
        ),
    ],
)
def test_state_needs_value_and_evidence_and_nothing_contradicting_it(attribution: Attribution, expected: str) -> None:
    assert attribution.state == expected
    assert attribution.state in ATTRIBUTION_STATES


def test_no_declarado_never_carries_a_filled_in_most_likely_value() -> None:
    absent = Attribution()
    assert absent.state == "no_declarado"
    assert absent.value is None
    assert absent.evidence is None
    assert absent.source is None
    assert absent.value != ""
    assert absent.value != 0


def test_allowed_source_prefixes_distinguish_a_declared_field_from_derived_text() -> None:
    assert ALLOWED_SOURCE_PREFIXES == (SOURCE_PREFIX_FEED, "titulo")
    assert SOURCE_PREFIX_FEED == "feed:"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
PYTHONPATH=. C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m pytest tests/test_catalog_record.py -q
```

Salida esperada: error de coleccion, `ModuleNotFoundError: No module named 'src.catalog'` (0 tests corridos).

- [ ] **Step 3: Write minimal implementation**

```python
# src/catalog/__init__.py
"""Lector de catalogo publico: filas canonicas con procedencia por valor.

Deliberadamente vacio de imports de submodulos. ``source.py`` importa httpx a
nivel de modulo (httpx es un extra opcional), asi que re-exportarlo aca lo
pondria en cualquier cadena de arranque que importe ``src.catalog`` y romperia
el arranque de produccion, que instala solo ``.[web,mcp]``. Mismo patron que el
``__init__.py`` del subpaquete de adquisicion de precios del repo, que tampoco
importa submodulos. Importar submodulos por su nombre completo:
``from src.catalog.record import CatalogRow``.
"""

from __future__ import annotations
```

```python
# src/catalog/record.py
"""Fila canonica de catalogo: cada valor carga de donde salio.

Modulo puro. No hace I/O, no importa httpx, y no interpreta semantica de
dominio: aca no entra una sola palabra del rubro del comerciante, y hay una
guarda de test que lo verifica (``tests/test_catalog_boundaries.py``). La red
vive entera en ``source.py``; el mapeo de claves por plataforma, en
``adapters/``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, get_args

# Tres estados, nunca dos. Un booleano "vino o no vino" no puede expresar
# "dos claves del feed dicen cosas distintas", que es justo la falla que este
# tipo existe para hacer visible.
AttributionState = Literal["declarado", "no_declarado", "contradicho"]
ATTRIBUTION_STATES: tuple[str, ...] = get_args(AttributionState)

# ``source`` distingue un valor que el comerciante declaro como CAMPO de uno
# derivado de TEXTO. "titulo" queda reservado para el normalizador de peso
# futuro y ningun adaptador de esta rama lo emite (el lector no compara el peso
# del feed contra el peso del titulo: carga las fuentes, no decide).
SOURCE_PREFIX_FEED = "feed:"
SOURCE_TITULO = "titulo"
ALLOWED_SOURCE_PREFIXES: tuple[str, ...] = (SOURCE_PREFIX_FEED, SOURCE_TITULO)


@dataclass(frozen=True)
class Attribution:
    """Un valor y de donde salio.

    ``evidence`` guarda el valor crudo tal cual vino del feed, sin reformatear
    -- sin trim, sin normalizar decimales, sin escalar. ``value`` es el valor ya
    normalizado. Que los dos viajen juntos es lo que hace auditable la lectura:
    ``evidence`` tiene que poder encontrarse literal dentro del ``raw`` de la
    fila.
    """

    value: str | None = None
    source: str | None = None
    evidence: str | None = None
    conflicts: tuple[tuple[str, str], ...] = ()

    @property
    def state(self) -> AttributionState:
        """``declarado`` / ``no_declarado`` / ``contradicho``, y nada mas.

        Un atributo con conflictos es ``contradicho`` aunque alguien haya
        rellenado ``value``: ante dos claves que no coinciden no gana la
        primera.
        """
        if self.conflicts:
            return "contradicho"
        if self.value is not None and self.evidence is not None:
            return "declarado"
        return "no_declarado"
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
PYTHONPATH=. C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m pytest tests/test_catalog_record.py -q
C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m ruff check src tests examples
```

Salida esperada: **cero fallos** y `All checks passed!`. Referencia del nombre del modulo, no de un docstring: la mencion al subpaquete de precios va SIN nombrarlo, porque `FORBIDDEN_TOKENS` de la Tarea 16 escanea todos los archivos de `src/catalog/` y el token entra igual dentro de un docstring. La referencia concreta al modulo va en la nota del PR (ultima tarea), no en el fuente.

- [ ] **Step 5: Commit**

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
git add src/catalog/__init__.py src/catalog/record.py tests/test_catalog_record.py
git commit -m "feat(catalog): Attribution con procedencia y tres estados

value + source + evidence + conflicts, frozen. El discriminador de estado
expone exactamente declarado / no_declarado / contradicho: no_declarado nunca
se rellena con el valor mas probable y conflicts vuelve contradicho aunque
alguien haya poblado value."
```

---

### Task 3: El arbitro de procedencia (`dig`, `evidence_of`, `resolve`)

El corazon del componente. Dos claves del feed que mapean al mismo atributo canonico y no coinciden dan `contradicho`, nunca "la primera gana".

**Files:**
- Modify: `src/catalog/record.py`
- Test: `tests/test_catalog_record.py`

**Interfaces:**
- Consumes: `Attribution`, `SOURCE_PREFIX_FEED` (Tarea 2).
- Produces:
  - `def dig(node: Any, path: str) -> Any` -- sigue un path punteado (`"prices.price"`, `"variants.0.grams"`, `"categories.2.name"`); los segmentos numericos indexan secuencias; devuelve `None` si falta cualquier segmento.
  - `def evidence_of(raw: Any) -> str` -- el valor crudo verbatim si ya es `str`, si no `json.dumps(raw)`.
  - `def resolve(candidates: Sequence[tuple[str, Any]], normalizer: Callable[[Any], str | None] | None = None) -> Attribution` -- `candidates` son pares `(clave_sin_prefijo, valor_crudo)`. La regla de **presencia** es C14 y vive aca, una sola vez: escalar y, si es `str`, no vacio despues de `strip()`.
  - `def is_present(raw: Any) -> bool` -- la regla de C14, exportada para que ningun adaptador la reimplemente.
  - `def bool_stock_reading(raw: Any) -> str | None` -- `True -> "in_stock"`, `False -> "out_of_stock"`, cualquier otra cosa `None`.

- [ ] **Step 1: Write the failing test**

Agregar a `tests/test_catalog_record.py` (y ampliar el import existente de `src.catalog.record` con `bool_stock_reading`, `dig`, `evidence_of`, `resolve`):

```python
def test_dig_follows_dotted_paths_and_indexes_sequences() -> None:
    payload = {"prices": {"price": "1990"}, "variants": [{"grams": 500}, {"grams": 900}], "categories": [{"name": "A"}]}
    assert dig(payload, "prices.price") == "1990"
    assert dig(payload, "variants.0.grams") == 500
    assert dig(payload, "variants.1.grams") == 900
    assert dig(payload, "categories.0.name") == "A"


def test_dig_returns_none_for_any_missing_segment_and_never_raises() -> None:
    payload = {"prices": {"price": "1990"}, "variants": []}
    assert dig(payload, "prices.currency_minor_unit") is None
    assert dig(payload, "variants.0.grams") is None
    assert dig(payload, "nope.nope.nope") is None
    assert dig(payload, "prices.price.deeper") is None
    assert dig(None, "prices.price") is None


def test_evidence_of_keeps_a_string_verbatim_and_renders_scalars_literally() -> None:
    assert evidence_of("1990") == "1990"
    assert evidence_of(" 2.5 ") == " 2.5 "  # sin trim: el crudo es el crudo
    assert evidence_of(12900) == "12900"
    assert evidence_of(2.5) == "2.5"
    assert evidence_of(True) == "true"
    assert evidence_of(False) == "false"


def test_resolve_with_no_candidate_present_is_no_declarado() -> None:
    assert resolve(()).state == "no_declarado"
    assert resolve((("sku", None),)).state == "no_declarado"


@pytest.mark.parametrize("absent", [None, "", "   ", "\t", {}, [], (), {"price": "1990"}, ["1990"]])
def test_resolve_treats_a_blank_or_container_raw_as_absent_never_as_declared(absent: object) -> None:
    """WooCommerce publica ``"sku": ""`` cuando el sku no esta seteado, y Shopify
    hace lo mismo con ``sku``/``barcode``. Declarar la cadena vacia es el espejo
    exacto del bug prohibido: afirmar donde el feed no dice nada. Y un raw
    contenedor daria ``declarado`` con evidencia ``"{}"``, que ademas rompe el
    test de propiedad con un falso "evidencia inventada"."""
    attribution = resolve((("sku", absent),))
    assert attribution.state == "no_declarado"
    assert attribution.value is None
    assert attribution.source is None
    assert attribution.evidence is None


def test_is_present_is_the_single_definition_of_presence() -> None:
    assert is_present("ABC-1") is True
    assert is_present("0") is True
    assert is_present(0) is True
    assert is_present(False) is True   # un booleano de stock presente es informacion
    assert is_present(2.5) is True
    assert is_present(None) is False
    assert is_present("") is False
    assert is_present("   ") is False
    assert is_present({}) is False
    assert is_present([]) is False


def test_a_blank_candidate_does_not_block_a_real_one() -> None:
    attribution = resolve((("is_in_stock", ""), ("stock_availability.class", "in-stock")), normalizer=_stock_reading)
    assert attribution.state == "declarado"
    assert attribution.source == "feed:stock_availability.class"


def test_resolve_with_one_candidate_declares_it_with_its_prefixed_key() -> None:
    attribution = resolve((("name", "Filete 1kg"),))
    assert attribution.state == "declarado"
    assert attribution.value == "Filete 1kg"
    assert attribution.evidence == "Filete 1kg"
    assert attribution.source == "feed:name"
    assert attribution.conflicts == ()


def test_resolve_with_two_agreeing_candidates_declares_the_first_present_key() -> None:
    attribution = resolve((("is_in_stock", True), ("stock_availability.class", "in-stock")), normalizer=_stock_reading)
    assert attribution.state == "declarado"
    assert attribution.value == "in_stock"
    assert attribution.source == "feed:is_in_stock"
    assert attribution.evidence == "true"


def test_resolve_arbitration_never_lets_the_first_key_win_a_disagreement() -> None:
    """Guarda del arbitraje. Es SEPARADA del test de propiedad a proposito: la
    propiedad no atrapa el arbitraje silencioso, porque quedarse con la primera
    senal produce una evidencia literal perfectamente valida."""
    attribution = resolve((("is_in_stock", True), ("stock_availability.class", "out-of-stock")), normalizer=_stock_reading)
    assert attribution.state == "contradicho"
    assert attribution.value is None
    assert attribution.evidence is None
    assert attribution.source is None
    assert attribution.conflicts == (
        ("feed:is_in_stock", "true"),
        ("feed:stock_availability.class", "out-of-stock"),
    )


def test_resolve_skips_a_candidate_whose_value_does_not_map_instead_of_guessing() -> None:
    attribution = resolve((("is_in_stock", True), ("stock_availability.class", "quien-sabe")), normalizer=_stock_reading)
    assert attribution.state == "declarado"
    assert attribution.value == "in_stock"
    assert attribution.source == "feed:is_in_stock"
    assert resolve((("stock_availability.class", "quien-sabe"),), normalizer=_stock_reading).state == "no_declarado"


def _stock_reading(raw: object) -> str | None:
    """Normalizador de prueba: el real vive en el adaptador Woo."""
    classes = {"in-stock": "in_stock", "out-of-stock": "out_of_stock"}
    if isinstance(raw, bool):
        return bool_stock_reading(raw)
    if isinstance(raw, str):
        return classes.get(raw)
    return None


def test_bool_stock_reading_maps_only_booleans() -> None:
    assert bool_stock_reading(True) == "in_stock"
    assert bool_stock_reading(False) == "out_of_stock"
    assert bool_stock_reading("true") is None
    assert bool_stock_reading(1) is None
    assert bool_stock_reading(None) is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
PYTHONPATH=. C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m pytest tests/test_catalog_record.py -q
```

Salida esperada: error de coleccion, `ImportError: cannot import name 'dig' from 'src.catalog.record'`.

- [ ] **Step 3: Write minimal implementation**

En `tests/test_catalog_record.py`, ampliar el import de `src.catalog.record` con `bool_stock_reading`, `dig`, `evidence_of`, `is_present`, `resolve`.

En `src/catalog/record.py`: agregar `import json` y `from typing import Any, Callable, Mapping, Sequence` al bloque de imports (ruff `I` ordena stdlib primero), y agregar despues de la clase `Attribution`:

```python
def dig(node: Any, path: str) -> Any:
    """Sigue un path punteado dentro de un payload ya parseado.

    Los segmentos numericos indexan secuencias (``variants.0.grams``). Devuelve
    ``None`` en cuanto falta un segmento: un campo ausente es
    ``no_declarado``, nunca una excepcion y nunca un default adivinado.
    """
    current = node
    for segment in path.split("."):
        if isinstance(current, Mapping):
            if segment not in current:
                return None
            current = current[segment]
        elif isinstance(current, (list, tuple)) and segment.isdigit():
            index = int(segment)
            if index >= len(current):
                return None
            current = current[index]
        else:
            return None
    return current


def evidence_of(raw: Any) -> str:
    """El valor crudo tal cual vino, sin reformatear.

    Un string se guarda verbatim (sin trim, sin normalizar decimales). Cualquier
    otro escalar se renderiza con ``json.dumps``, que es exactamente como
    aparece dentro del payload -- eso es lo que hace demostrable la propiedad
    "la evidencia aparece literal en el ``raw`` de la fila".
    """
    return raw if isinstance(raw, str) else json.dumps(raw)


def bool_stock_reading(raw: Any) -> str | None:
    """Lectura canonica de un booleano de stock. Solo booleanos: un ``1`` o un
    ``"true"`` no se interpretan."""
    if not isinstance(raw, bool):
        return None
    return "in_stock" if raw else "out_of_stock"


def is_present(raw: Any) -> bool:
    """Si el feed DIJO algo en esa clave. Una sola definicion para los siete
    atributos y las dos plataformas.

    ``None`` no alcanza como unica ausencia: WooCommerce publica ``"sku": ""``
    cuando el sku no esta seteado, y Shopify hace lo mismo con ``sku`` y
    ``barcode``. Declarar la cadena vacia seria afirmar donde el feed no dice
    nada -- el espejo exacto del bug que este componente prohibe -- y el test de
    propiedad no lo atraparia, porque ``""`` es una hoja literal del ``raw``.
    Un raw contenedor (``{}``, ``[]``) tampoco es un valor: seria evidencia
    ``"{}"``, que ademas no aparece como hoja en ningun payload.
    """
    if isinstance(raw, str):
        return bool(raw.strip())
    return isinstance(raw, (int, float, bool))


def resolve(
    candidates: Sequence[tuple[str, Any]],
    normalizer: Callable[[Any], str | None] | None = None,
) -> Attribution:
    """Arbitra entre las claves del feed que mapean al mismo atributo canonico.

    ``candidates`` son pares ``(clave, valor_crudo)`` con la clave SIN el
    prefijo de procedencia (``resolve`` lo agrega). Reglas, en orden:

    - ninguna clave presente, o ninguna con lectura canonica -> ``no_declarado``;
    - todas las presentes coinciden -> ``declarado``, con la procedencia de la
      primera clave presente y su valor crudo como evidencia;
    - dos presentes no coinciden -> ``contradicho``, con las dos claves y sus
      valores crudos en ``conflicts``. **Nunca gana la primera.**

    Un candidato presente cuyo valor no mapea al vocabulario canonico cerrado se
    descarta del arbitraje (no se adivina que quiso decir); si era el unico, el
    atributo queda ``no_declarado``. Que ese descarte no sea silencioso lo
    garantiza la guarda de corpus de la Tarea 15, no este modulo.

    Presencia = ``is_present``: escalar, y si es ``str``, no vacio. La regla vive
    aca una sola vez para que ningun adaptador la reimplemente distinto.
    """
    normalize = normalizer or evidence_of
    readings = [
        (key, raw, normalize(raw))
        for key, raw in candidates
        if is_present(raw)
    ]
    usable = [(key, raw, reading) for key, raw, reading in readings if reading is not None]
    if not usable:
        return Attribution()
    if len({reading for _, _, reading in usable}) > 1:
        return Attribution(
            conflicts=tuple((SOURCE_PREFIX_FEED + key, evidence_of(raw)) for key, raw, _ in usable)
        )
    key, raw, reading = usable[0]
    return Attribution(value=reading, source=SOURCE_PREFIX_FEED + key, evidence=evidence_of(raw))
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
PYTHONPATH=. C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m pytest tests/test_catalog_record.py -q
C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m ruff check src tests examples
```

Salida esperada: **cero fallos** y `All checks passed!`. En verde en particular: `test_resolve_arbitration_never_lets_the_first_key_win_a_disagreement`, `test_resolve_treats_a_blank_or_container_raw_as_absent_never_as_declared` (los 9 casos) y `test_is_present_is_the_single_definition_of_presence`.

- [ ] **Step 5: Commit**

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
git add src/catalog/record.py tests/test_catalog_record.py
git commit -m "feat(catalog): arbitro de procedencia con dig, evidence_of y resolve

Dos claves del feed que mapean al mismo atributo canonico y no coinciden dan
contradicho con las dos claves y sus valores crudos en conflicts. Nunca gana la
primera. Un candidato sin lectura canonica se descarta en vez de adivinarse, y
una clave presente pero vacia (el \"sku\": \"\" de Woo) cuenta como ausente: no
se declara donde el feed no dice nada."
```

---

### Task 4: `CatalogRow` y la introspeccion de atributos

**Files:**
- Modify: `src/catalog/record.py`
- Test: `tests/test_catalog_record.py`

**Interfaces:**
- Consumes: `Attribution` (Tarea 2).
- Produces:
  - `CANONICAL_ATTRIBUTES: tuple[str, ...] = ("title", "price", "currency", "sku", "gtin", "weight_declared", "stock_status")`.
  - `@dataclass(frozen=True) class CatalogRow` con `url: str`, `site: str`, `read_at: datetime` (metadatos planos), los siete `Attribution` de arriba, `categories: tuple[Attribution, ...] = ()`, `raw: Mapping[str, Any] = field(default_factory=dict)`.
  - `def iter_attributions(row: CatalogRow) -> tuple[tuple[str, Attribution], ...]` -- descubre por introspeccion de `dataclasses.fields` cada `Attribution` de la fila, incluidas las de dentro de tuplas, con nombre `"categories.0"` para esas. Un atributo canonico nuevo aparece aca sin tocar nada.

- [ ] **Step 1: Write the failing test**

Agregar a `tests/test_catalog_record.py` (ampliar imports con `CANONICAL_ATTRIBUTES`, `CatalogRow`, `iter_attributions`, y `from datetime import datetime, timezone`):

```python
READ_AT = datetime(2026, 7, 25, 12, 0, 0, tzinfo=timezone.utc)


def _row(**overrides: object) -> CatalogRow:
    base: dict[str, object] = {
        "url": "https://tienda.test/wp-json/wc/store/v1/products?per_page=100",
        "site": "tienda.test",
        "read_at": READ_AT,
    }
    base.update(overrides)
    return CatalogRow(**base)  # type: ignore[arg-type]


def test_url_site_and_read_at_are_flat_metadata_not_attributions() -> None:
    row = _row()
    assert not isinstance(row.url, Attribution)
    assert not isinstance(row.site, Attribution)
    assert not isinstance(row.read_at, Attribution)
    assert row.url.startswith("https://")
    assert row.site == "tienda.test"
    assert row.read_at == READ_AT


def test_exactly_these_seven_attributes_are_attributions() -> None:
    assert CANONICAL_ATTRIBUTES == ("title", "price", "currency", "sku", "gtin", "weight_declared", "stock_status")
    row = _row()
    for name in CANONICAL_ATTRIBUTES:
        assert isinstance(getattr(row, name), Attribution), name


def test_categories_is_one_attribution_per_declared_category_not_a_list_valued_one() -> None:
    row = _row(
        categories=(
            Attribution(value="Congelados", source="feed:categories.0.name", evidence="Congelados"),
            Attribution(value="Mariscos", source="feed:categories.1.name", evidence="Mariscos"),
            Attribution(value="Ofertas", source="feed:categories.2.name", evidence="Ofertas"),
        )
    )
    assert isinstance(row.categories, tuple)
    assert len(row.categories) == 3
    assert all(isinstance(item, Attribution) for item in row.categories)
    assert len({item.source for item in row.categories}) == 3


def test_a_row_without_categories_is_an_empty_tuple_not_a_no_declarado_attribution() -> None:
    assert _row().categories == ()


def test_raw_is_a_mapping_with_that_rows_payload() -> None:
    payload = {"id": 7, "name": "Filete", "prices": {"price": "1990"}}
    row = _row(raw=payload)
    assert isinstance(row.raw, Mapping)
    assert row.raw == payload


def test_iter_attributions_discovers_every_attribution_by_introspection() -> None:
    row = _row(
        title=Attribution(value="Filete", source="feed:name", evidence="Filete"),
        categories=(Attribution(value="A", source="feed:categories.0.name", evidence="A"),),
    )
    found = dict(iter_attributions(row))
    for name in CANONICAL_ATTRIBUTES:
        assert name in found
    assert "categories.0" in found
    assert found["categories.0"].value == "A"
    assert "url" not in found and "site" not in found and "read_at" not in found and "raw" not in found
    assert len(found) == len(CANONICAL_ATTRIBUTES) + 1
```

El archivo de test no tiene todavia ninguna linea `from typing import ...` (la Tarea 2 lo creo con `dataclasses`, `socket`, `pytest` y el import de `src.catalog.record`), asi que **la linea se agrega nueva**: insertar `from typing import Mapping` en el bloque de stdlib, despues de `import socket` -- ahi la deja ruff `I`.

- [ ] **Step 2: Run test to verify it fails**

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
PYTHONPATH=. C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m pytest tests/test_catalog_record.py -q
```

Salida esperada: error de coleccion, `ImportError: cannot import name 'CANONICAL_ATTRIBUTES' from 'src.catalog.record'`.

- [ ] **Step 3: Write minimal implementation**

En `src/catalog/record.py`: cambiar el import de dataclasses a `from dataclasses import dataclass, field, fields`, agregar `from datetime import datetime`, y agregar al final:

```python
# Los siete atributos canonicos del registro. Cerrados a proposito: promover un
# octavo (por ejemplo el permalink del producto, o la unidad del peso) es una
# decision de diseno, no una consecuencia de que una plataforma traiga la clave.
CANONICAL_ATTRIBUTES: tuple[str, ...] = (
    "title",
    "price",
    "currency",
    "sku",
    "gtin",
    "weight_declared",
    "stock_status",
)


@dataclass(frozen=True)
class CatalogRow:
    """Una fila del catalogo, sin dominio.

    ``url`` es la URL exacta de la pagina de feed que trajo esta fila, ``site``
    el host de la tienda y ``read_at`` el instante de la lectura: son metadatos
    de la lectura y no llevan procedencia porque su procedencia es el lector
    mismo. ``raw`` es el payload de ESTA fila (no del feed completo), para
    auditar. El permalink del producto, si la plataforma lo declara, vive dentro
    de ``raw``.

    ``weight_declared`` guarda unicamente lo que el feed dice del peso. Este
    modulo NO compara el peso del feed contra el peso del titulo: cargar las dos
    fuentes es su trabajo, decidir la contradiccion es del normalizador de peso,
    que no esta en esta rama.
    """

    url: str
    site: str
    read_at: datetime
    title: Attribution = Attribution()
    price: Attribution = Attribution()
    currency: Attribution = Attribution()
    sku: Attribution = Attribution()
    gtin: Attribution = Attribution()
    weight_declared: Attribution = Attribution()
    stock_status: Attribution = Attribution()
    categories: tuple[Attribution, ...] = ()
    raw: Mapping[str, Any] = field(default_factory=dict)


def iter_attributions(row: CatalogRow) -> tuple[tuple[str, Attribution], ...]:
    """Cada ``Attribution`` de la fila, descubierta por introspeccion.

    Recorre los campos del dataclass en vez de una lista escrita a mano, asi que
    un atributo canonico nuevo entra en el test de propiedad y en el resumen de
    cobertura sin que nadie tenga que acordarse de agregarlo. Las
    ``Attribution`` dentro de tuplas se nombran ``"<campo>.<indice>"``.
    """
    found: list[tuple[str, Attribution]] = []
    for spec in fields(row):
        value = getattr(row, spec.name)
        if isinstance(value, Attribution):
            found.append((spec.name, value))
        elif isinstance(value, tuple):
            for index, item in enumerate(value):
                if isinstance(item, Attribution):
                    found.append((f"{spec.name}.{index}", item))
    return tuple(found)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
PYTHONPATH=. C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m pytest tests/test_catalog_record.py -q
C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m ruff check src tests examples
```

Salida esperada: **cero fallos** y `All checks passed!`.

- [ ] **Step 5: Commit**

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
git add src/catalog/record.py tests/test_catalog_record.py
git commit -m "feat(catalog): CatalogRow con siete atributos canonicos e introspeccion

url/site/read_at son metadatos planos (su procedencia es el lector). categories
es una Attribution por categoria declarada, no una Attribution de lista, y una
fila sin categorias es tupla vacia. iter_attributions descubre los atributos por
introspeccion para que uno nuevo entre solo en propiedad y cobertura."
```

---

### Task 5: `ReadReport`, los cinco desenlaces y los motivos tipados

**Files:**
- Modify: `src/catalog/record.py`
- Test: `tests/test_catalog_record.py`

**Interfaces:**
- Consumes: `CatalogRow` (Tarea 4).
- Produces:
  - `ReadOutcome = Literal["ok", "sin_feed_json", "bloqueado_waf", "plataforma_desconocida", "error_red"]` y `READ_OUTCOMES: tuple[str, ...]`.
  - `DROP_FILA_NO_DICT = "FILA_NO_DICT"`, `DROP_SIN_TITULO = "SIN_TITULO"`, `DROP_REASONS: tuple[str, ...]` (cardinalidad 2).
  - `UNDECLARED_PRECIO_NO_PARSEABLE = "PRECIO_NO_PARSEABLE"`, `UNDECLARED_MINOR_UNIT_AUSENTE = "MINOR_UNIT_AUSENTE"`, `UNDECLARED_MONEDA_AUSENTE = "MONEDA_AUSENTE"`, `UNDECLARED_REASONS: tuple[str, ...]` (cardinalidad 3).
  - `TRUNCATION_TOPE_PAGINAS = "TOPE_PAGINAS_ALCANZADO"`, `TRUNCATION_PAGINA_FALLIDA = "LECTURA_PARCIAL_PAGINA_FALLIDA"`, `TRUNCATION_PAGINA_REPETIDA = "PAGINA_REPETIDA"`, `TRUNCATION_TOTAL_NO_COINCIDE = "TOTAL_DECLARADO_NO_COINCIDE"` (C16), `TRUNCATION_REASONS: tuple[str, ...]` (cardinalidad 4).
  - `@dataclass(frozen=True) class ReadReport` con `outcome: ReadOutcome`, `platform: str | None = None`, `route_used: str | None = None`, `pages_read: int = 0`, `rows_emitted: int = 0`, `rows_declared: int | None = None` (C16), `routes_probed: tuple[tuple[str, int], ...] = ()`, `rows_dropped: tuple[tuple[str, int], ...] = ()`, `undeclared_reasons: tuple[tuple[str, int], ...] = ()`, `truncations: tuple[str, ...] = ()`, `attribute_states: tuple[tuple[str, tuple[int, int, int]], ...] = ()` (C17).
  - `def attribute_states_of(rows: Sequence[CatalogRow]) -> tuple[tuple[str, tuple[int, int, int]], ...]` -- funcion **pura**: por atributo canonico, los conteos `(declarado, no_declarado, contradicho)` en el orden de `ATTRIBUTION_STATES`, descubiertos con `iter_attributions` (C17). Es la que el CLI renderiza en vez de calcular.
  - `@dataclass(frozen=True) class AdapterResult` con `rows: tuple[CatalogRow, ...] = ()`, `dropped: tuple[str, ...] = ()`, `undeclared: tuple[str, ...] = ()` -- lo que devuelve un adaptador: motivos crudos, uno por ocurrencia, sin agregar.
  - `def tally(reasons: Iterable[str], order: Sequence[str]) -> tuple[tuple[str, int], ...]` -- agrega a `((motivo, cuenta), ...)` en el orden declarado, omitiendo los de cuenta cero.

- [ ] **Step 1: Write the failing test**

Agregar a `tests/test_catalog_record.py` (ampliar imports con `AdapterResult`, `DROP_FILA_NO_DICT`, `DROP_REASONS`, `DROP_SIN_TITULO`, `READ_OUTCOMES`, `ReadReport`, `TRUNCATION_REASONS`, `TRUNCATION_TOPE_PAGINAS`, `TRUNCATION_TOTAL_NO_COINCIDE`, `UNDECLARED_MINOR_UNIT_AUSENTE`, `UNDECLARED_MONEDA_AUSENTE`, `UNDECLARED_PRECIO_NO_PARSEABLE`, `UNDECLARED_REASONS`, `attribute_states_of`, `tally`):

```python
def test_there_are_exactly_five_outcomes() -> None:
    assert READ_OUTCOMES == ("ok", "sin_feed_json", "bloqueado_waf", "plataforma_desconocida", "error_red")
    assert len(READ_OUTCOMES) == 5


def test_read_report_carries_platform_route_pages_and_rows() -> None:
    names = {spec.name for spec in dataclasses.fields(ReadReport)}
    assert {"platform", "route_used", "pages_read", "rows_emitted"} <= names
    assert {"routes_probed", "rows_dropped", "undeclared_reasons", "truncations", "outcome"} <= names


def test_a_report_is_emitted_even_when_not_a_single_row_was_read() -> None:
    report = ReadReport(outcome="plataforma_desconocida")
    assert report is not None
    assert report.outcome == "plataforma_desconocida"
    assert report.rows_emitted == 0
    assert report.route_used is None
    assert report.routes_probed == ()


def test_there_are_exactly_two_drop_reasons() -> None:
    assert DROP_REASONS == ("FILA_NO_DICT", "SIN_TITULO")
    assert len(DROP_REASONS) == 2
    assert (DROP_FILA_NO_DICT, DROP_SIN_TITULO) == DROP_REASONS


def test_there_are_exactly_three_undeclared_reasons() -> None:
    assert UNDECLARED_REASONS == ("PRECIO_NO_PARSEABLE", "MINOR_UNIT_AUSENTE", "MONEDA_AUSENTE")
    assert len(UNDECLARED_REASONS) == 3
    assert (
        UNDECLARED_PRECIO_NO_PARSEABLE,
        UNDECLARED_MINOR_UNIT_AUSENTE,
        UNDECLARED_MONEDA_AUSENTE,
    ) == UNDECLARED_REASONS


def test_drop_reasons_and_undeclared_reasons_never_share_a_name() -> None:
    assert set(DROP_REASONS).isdisjoint(UNDECLARED_REASONS)
    assert set(TRUNCATION_REASONS).isdisjoint(set(DROP_REASONS) | set(UNDECLARED_REASONS))


def test_truncation_reasons_are_a_third_closed_set() -> None:
    assert TRUNCATION_REASONS == (
        "TOPE_PAGINAS_ALCANZADO",
        "LECTURA_PARCIAL_PAGINA_FALLIDA",
        "PAGINA_REPETIDA",
        "TOTAL_DECLARADO_NO_COINCIDE",
    )
    assert TRUNCATION_TOPE_PAGINAS in TRUNCATION_REASONS
    assert TRUNCATION_TOTAL_NO_COINCIDE in TRUNCATION_REASONS


def test_attribute_states_counts_every_canonical_attribute_even_the_undeclared_ones() -> None:
    """C17: el informe que se titula "la cobertura, declarada siempre" tiene que
    declarar los siete atributos, no solo los que tienen motivo tipado."""
    rows = (_row(title=Attribution(value="P", source="feed:name", evidence="P")),)
    states = dict(attribute_states_of(rows))
    assert set(states) == set(CANONICAL_ATTRIBUTES)
    assert states["title"] == (1, 0, 0)
    assert states["sku"] == (0, 1, 0)
    assert states["stock_status"] == (0, 1, 0)
    assert attribute_states_of(()) == ()


def test_tally_aggregates_counts_in_the_declared_order_and_omits_zeroes() -> None:
    assert tally([DROP_FILA_NO_DICT, DROP_FILA_NO_DICT], DROP_REASONS) == (("FILA_NO_DICT", 2),)
    assert tally([DROP_SIN_TITULO, DROP_FILA_NO_DICT], DROP_REASONS) == (("FILA_NO_DICT", 1), ("SIN_TITULO", 1))
    assert tally([], DROP_REASONS) == ()
    assert tally([UNDECLARED_MINOR_UNIT_AUSENTE] * 3, UNDECLARED_REASONS) == (("MINOR_UNIT_AUSENTE", 3),)


def test_adapter_result_returns_raw_reason_occurrences_not_aggregates() -> None:
    result = AdapterResult(dropped=(DROP_FILA_NO_DICT, DROP_FILA_NO_DICT))
    assert result.rows == ()
    assert result.dropped == ("FILA_NO_DICT", "FILA_NO_DICT")
    assert result.undeclared == ()
    assert tally(result.dropped, DROP_REASONS) == (("FILA_NO_DICT", 2),)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
PYTHONPATH=. C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m pytest tests/test_catalog_record.py -q
```

Salida esperada: error de coleccion, `ImportError: cannot import name 'READ_OUTCOMES' from 'src.catalog.record'`.

- [ ] **Step 3: Write minimal implementation**

En `src/catalog/record.py`: agregar `from collections import Counter` y `Iterable` al import de `typing`, y agregar al final:

```python
# Cinco desenlaces, no dos. Se emite siempre, incluso cuando no se leyo ni una
# fila: "lei 0 productos" sin desenlace tipado hace que el operador concluya que
# la tienda no tiene catalogo.
ReadOutcome = Literal["ok", "sin_feed_json", "bloqueado_waf", "plataforma_desconocida", "error_red"]
READ_OUTCOMES: tuple[str, ...] = get_args(ReadOutcome)

# Descarte de fila: solo dos motivos, porque solo dos hacen que la fila no sea
# una fila. Todo lo demas se emite con el atributo sin declarar.
DROP_FILA_NO_DICT = "FILA_NO_DICT"
DROP_SIN_TITULO = "SIN_TITULO"
DROP_REASONS: tuple[str, ...] = (DROP_FILA_NO_DICT, DROP_SIN_TITULO)

# Atributo sin declarar: la fila SI se emite. Confundir esto con un descarte es
# el error que este componente existe para no cometer. Un precio ausente, vacio
# o malformado cae en PRECIO_NO_PARSEABLE: los motivos son tres y solo tres.
UNDECLARED_PRECIO_NO_PARSEABLE = "PRECIO_NO_PARSEABLE"
UNDECLARED_MINOR_UNIT_AUSENTE = "MINOR_UNIT_AUSENTE"
UNDECLARED_MONEDA_AUSENTE = "MONEDA_AUSENTE"
UNDECLARED_REASONS: tuple[str, ...] = (
    UNDECLARED_PRECIO_NO_PARSEABLE,
    UNDECLARED_MINOR_UNIT_AUSENTE,
    UNDECLARED_MONEDA_AUSENTE,
)

# Lectura cortada: distinta de una fila descartada y de un atributo sin
# declarar. Existe para que nadie lea una lectura truncada como completa.
TRUNCATION_TOPE_PAGINAS = "TOPE_PAGINAS_ALCANZADO"
TRUNCATION_PAGINA_FALLIDA = "LECTURA_PARCIAL_PAGINA_FALLIDA"
TRUNCATION_PAGINA_REPETIDA = "PAGINA_REPETIDA"
# El feed declara cuantas filas tiene (X-WP-Total). Recibir menos y no decirlo es
# cobertura escondida: el invariante rows_emitted + rows_dropped == filas crudas
# mide consistencia interna, y se satisface perfecto con la mitad del catalogo.
TRUNCATION_TOTAL_NO_COINCIDE = "TOTAL_DECLARADO_NO_COINCIDE"
TRUNCATION_REASONS: tuple[str, ...] = (
    TRUNCATION_TOPE_PAGINAS,
    TRUNCATION_PAGINA_FALLIDA,
    TRUNCATION_PAGINA_REPETIDA,
    TRUNCATION_TOTAL_NO_COINCIDE,
)


@dataclass(frozen=True)
class ReadReport:
    """La cobertura de una lectura, declarada siempre.

    ``routes_probed`` guarda las cuatro rutas con su status, exitosas o no (mas
    la lectura de la home como ``("/", status)`` cuando ocurre), asi que el
    informe dice cual respondio y cual no. ``route_used`` esta poblado si y solo
    si alguna ruta entrego feed -- una lectura parcial (``error_red`` con filas)
    conserva la ruta, porque es informacion cierta.

    ``rows_declared`` es el total de filas que declaro la plataforma, si lo
    declara (``X-WP-Total`` en Woo): recibir menos y no decirlo es cobertura
    escondida. ``attribute_states`` declara la cobertura de los SIETE atributos
    canonicos, no solo de los tres que tienen motivo tipado.
    """

    outcome: ReadOutcome
    platform: str | None = None
    route_used: str | None = None
    pages_read: int = 0
    rows_emitted: int = 0
    rows_declared: int | None = None
    routes_probed: tuple[tuple[str, int], ...] = ()
    rows_dropped: tuple[tuple[str, int], ...] = ()
    undeclared_reasons: tuple[tuple[str, int], ...] = ()
    truncations: tuple[str, ...] = ()
    attribute_states: tuple[tuple[str, tuple[int, int, int]], ...] = ()


@dataclass(frozen=True)
class AdapterResult:
    """Lo que devuelve un adaptador por pagina: filas mas motivos crudos.

    Los motivos vienen uno por ocurrencia (sin agregar) para que el llamador los
    sume sobre todas las paginas con ``tally`` una sola vez.
    """

    rows: tuple[CatalogRow, ...] = ()
    dropped: tuple[str, ...] = ()
    undeclared: tuple[str, ...] = ()


def tally(reasons: Iterable[str], order: Sequence[str]) -> tuple[tuple[str, int], ...]:
    """Agrega motivos a ``((motivo, cuenta), ...)`` en el orden declarado.

    Motivos agregados con conteo, nunca filas individuales. Los de cuenta cero se
    omiten para que el informe no se llene de ruido.
    """
    counts = Counter(reasons)
    return tuple((reason, counts[reason]) for reason in order if counts[reason])


def attribute_states_of(
    rows: Sequence[CatalogRow],
) -> tuple[tuple[str, tuple[int, int, int]], ...]:
    """La cobertura por atributo canonico: ``(declarado, no_declarado, contradicho)``.

    Funcion pura y parte del INFORME, no del renderizador: un consumidor que
    ramifica por el tipo -- o un log de una linea del ``ReadReport`` -- tiene que
    poder ver que el 100% de las filas quedo sin ``sku``, que es cobertura que
    ``undeclared_reasons`` no puede expresar (sus tres motivos son de precio y
    moneda, cerrados por el spec).

    Los atributos salen de ``iter_attributions``, o sea por introspeccion: uno
    nuevo aparece aca sin tocar esta funcion. Las categorias se agregan bajo
    ``categories`` (se cuentan las declaradas de cada fila).
    """
    if not rows:
        return ()
    counts: dict[str, dict[str, int]] = {}
    for row in rows:
        for name, attribution in iter_attributions(row):
            bucket = counts.setdefault(name.split(".")[0], {state: 0 for state in ATTRIBUTION_STATES})
            bucket[attribution.state] += 1
    return tuple(
        (name, tuple(counts[name][state] for state in ATTRIBUTION_STATES))  # type: ignore[misc]
        for name in sorted(counts)
    )
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
PYTHONPATH=. C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m pytest tests/test_catalog_record.py -q
C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m ruff check src tests examples
```

Salida esperada: **cero fallos** y `All checks passed!`.

- [ ] **Step 5: Commit**

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
git add src/catalog/record.py tests/test_catalog_record.py
git commit -m "feat(catalog): ReadReport con cinco desenlaces y motivos tipados cerrados

Dos motivos de descarte de fila, tres de atributo sin declarar y cuatro de
lectura cortada, en conjuntos cerrados y disjuntos. tally agrega motivo+cuenta en
el orden declarado. attribute_states declara la cobertura de los siete atributos
canonicos, no solo la de los tres con motivo tipado. El informe se construye
siempre, incluso con cero filas."
```

---

### Task 6: Adaptador Woo -- reconocimiento de forma, identidad y descartes

**Files:**
- Create: `src/catalog/adapters/__init__.py`, `src/catalog/adapters/woo.py`
- Test: `tests/test_catalog_record.py`

**Interfaces:**
- Consumes: `AdapterResult`, `Attribution`, `CatalogRow`, `DROP_FILA_NO_DICT`, `DROP_SIN_TITULO`, `dig`, `resolve` (Tareas 3-5).
- Produces (contrato que `source.py` va a consumir en las Tareas 10-14, identico en los dos adaptadores):
  - `PLATFORM: str` = `"woo"`.
  - `REQUIRED_KEYS: tuple[str, ...]` = `("id", "prices")`.
  - `TOTAL_PAGES_HEADER: str | None` = `"X-WP-TotalPages"`.
  - `def recognizes(payload: Any) -> bool`.
  - `def rows_of(payload: Any) -> tuple[Any, ...]`.
  - `def next_page_url(base: str, route: str, page: int) -> str`.
  - `def to_rows(payload: Any, *, url: str, site: str, read_at: datetime) -> AdapterResult`.
  - `ADAPTERS: dict[str, ModuleType]` en `adapters/__init__.py` (tabla de despacho plataforma -> modulo).

- [ ] **Step 1: Write the failing test**

Agregar a `tests/test_catalog_record.py` (imports nuevos: `import json`, `from pathlib import Path`, `from src.catalog.adapters import ADAPTERS`, `from src.catalog.adapters import woo`):

```python
FIXTURES = Path(__file__).parent / "fixtures" / "catalog"
_WOO_V1 = json.loads((FIXTURES / "woo_v1_route.json").read_text(encoding="utf-8"))
_WOO_STORE = json.loads((FIXTURES / "woo_store_route.json").read_text(encoding="utf-8"))
_WOO_FLAT = json.loads((FIXTURES / "woo_indexphp_route.json").read_text(encoding="utf-8"))
_WOO_EMPTY = json.loads((FIXTURES / "woo_empty_page.json").read_text(encoding="utf-8"))

FEED_URL = "https://tienda.test/wp-json/wc/store/v1/products?per_page=100"


def _woo_rows(payload: object) -> AdapterResult:
    return woo.to_rows(payload, url=FEED_URL, site="tienda.test", read_at=READ_AT)


def test_adapter_dispatch_is_a_table_not_a_chain_of_ifs() -> None:
    assert ADAPTERS["woo"] is woo
    assert woo.PLATFORM == "woo"


def test_woo_recognizes_its_own_recorded_payload_on_the_three_route_variants() -> None:
    for payload in (_WOO_V1, _WOO_STORE, _WOO_FLAT):
        assert woo.recognizes(payload) is True
        assert len(woo.rows_of(payload)) == len(payload)


def test_woo_recognizes_an_empty_page_so_an_empty_catalog_is_not_a_shape_failure() -> None:
    assert _WOO_EMPTY == []
    assert woo.recognizes(_WOO_EMPTY) is True
    assert woo.rows_of(_WOO_EMPTY) == ()


def test_woo_does_not_recognize_a_200_with_an_unexpected_shape() -> None:
    assert woo.recognizes({"foo": [{"name": "x", "price": "1"}]}) is False
    assert woo.recognizes([{"nombre": "x", "precio": "1"}]) is False
    assert woo.recognizes("not json at all") is False
    assert woo.recognizes(None) is False


def test_woo_next_page_url_appends_the_page_parameter_to_the_probed_route() -> None:
    assert woo.next_page_url("https://tienda.test", "/wp-json/wc/store/v1/products?per_page=100", 2) == (
        "https://tienda.test/wp-json/wc/store/v1/products?per_page=100&page=2"
    )


def test_woo_maps_title_from_the_name_key_with_that_exact_provenance() -> None:
    result = _woo_rows(_WOO_V1)
    row, raw = result.rows[0], _WOO_V1[0]
    assert row.title.state == "declarado"
    assert row.title.value == raw["name"]
    assert row.title.evidence == raw["name"]
    assert row.title.source == "feed:name"


def test_woo_carries_the_read_metadata_and_that_rows_own_payload() -> None:
    result = _woo_rows(_WOO_V1)
    assert result.rows[0].url == FEED_URL
    assert result.rows[0].site == "tienda.test"
    assert result.rows[0].read_at == READ_AT
    assert result.rows[0].raw == _WOO_V1[0]
    assert result.rows[1].raw == _WOO_V1[1]


def test_woo_maps_sku_from_the_named_key_with_that_exact_provenance() -> None:
    """Payload sintetico a proposito: sobre el fixture real, un sku ausente es
    ``no_declarado`` legitimo, asi que ahi el assert no puede morder. Sin este
    test, borrar SKU_KEYS y el argumento ``sku=`` de ``to_rows`` deja las tres
    suites en verde y el entregable de esta tarea sin testear."""
    payload = [{"id": 1, "name": "P", "sku": "ABC-1", "prices": {"price": "1990", "currency_minor_unit": 2}}]
    row = _woo_rows(payload).rows[0]
    assert row.sku.state == "declarado"
    assert row.sku.value == "ABC-1"
    assert row.sku.evidence == "ABC-1"
    assert row.sku.source == "feed:sku"
    assert woo.SKU_KEYS == ("sku",)


def test_woo_sku_published_as_an_empty_string_is_undeclared_not_declared() -> None:
    """Woo publica ``"sku": ""`` cuando no esta seteado (C14)."""
    payload = [{"id": 1, "name": "P", "sku": "", "prices": {"price": "1990", "currency_minor_unit": 2}}]
    row = _woo_rows(payload).rows[0]
    assert row.sku.state == "no_declarado"
    assert row.sku.source is None


def test_woo_on_the_real_fixture_leaves_gtin_undeclared_because_the_schema_has_no_gtin() -> None:
    row = _woo_rows(_WOO_V1).rows[0]
    assert row.sku.source in (None, "feed:sku")   # el fixture real puede no traer sku
    assert row.gtin.state == "no_declarado"
    assert woo.GTIN_KEYS == ()


def test_woo_maps_one_attribution_per_declared_category() -> None:
    result = _woo_rows(_WOO_V1)
    for row, raw in zip(result.rows, _WOO_V1):
        # El indice de la procedencia es el del payload CRUDO, no el de la lista
        # filtrada: si una categoria sin nombre precede a una con nombre, filtrar
        # primero desfasaria los indices y el test fallaria sobre codigo correcto.
        declared = [
            (index, item)
            for index, item in enumerate(raw.get("categories") or [])
            if isinstance(item, Mapping) and str(item.get("name") or "").strip()
        ]
        assert len(row.categories) == len(declared)
        for attribution, (index, item) in zip(row.categories, declared):
            assert attribution.source == f"feed:categories.{index}.name"
            assert attribution.value == item["name"]


def test_woo_maps_exactly_three_categories_from_a_three_category_payload() -> None:
    payload = [
        {
            "id": 1,
            "name": "Producto",
            "prices": {"price": "1990", "currency_minor_unit": 2, "currency_code": "CLP"},
            "categories": [{"name": "A"}, {"name": "B"}, {"name": "C"}],
        }
    ]
    row = _woo_rows(payload).rows[0]
    assert len(row.categories) == 3
    assert [item.value for item in row.categories] == ["A", "B", "C"]
    assert [item.source for item in row.categories] == [
        "feed:categories.0.name",
        "feed:categories.1.name",
        "feed:categories.2.name",
    ]


def test_woo_row_without_categories_gets_an_empty_tuple() -> None:
    payload = [{"id": 1, "name": "Producto", "prices": {"price": "1990", "currency_minor_unit": 2}}]
    assert _woo_rows(payload).rows[0].categories == ()


def test_woo_drops_a_row_that_is_not_an_object_and_counts_it_once_per_row() -> None:
    payload = ["no soy un objeto", 42, {"id": 1, "name": "Producto", "prices": {}}]
    result = _woo_rows(payload)
    assert len(result.rows) == 1
    assert tally(result.dropped, DROP_REASONS) == (("FILA_NO_DICT", 2),)


def test_woo_drops_a_row_without_a_title_because_nothing_identifies_the_product() -> None:
    payload = [
        {"id": 1, "prices": {}},
        {"id": 2, "name": "", "prices": {}},
        {"id": 3, "name": "   ", "prices": {}},
        {"id": 4, "name": "Producto", "prices": {}},
    ]
    result = _woo_rows(payload)
    assert [row.title.value for row in result.rows] == ["Producto"]
    assert tally(result.dropped, DROP_REASONS) == (("SIN_TITULO", 3),)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
PYTHONPATH=. C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m pytest tests/test_catalog_record.py -q
```

Salida esperada: error de coleccion, `ModuleNotFoundError: No module named 'src.catalog.adapters'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/catalog/adapters/__init__.py
"""Un adaptador por plataforma, despachados por tabla.

Cada plataforma nueva es un archivo nuevo aca mas una entrada en ``ADAPTERS``:
nunca una rama dentro de un ``if`` con el mapeo de campos adentro. Ningun
adaptador toca la red ni importa httpx -- eso vive solo en ``source.py``.

Contrato que todo modulo de adaptador cumple:

- ``PLATFORM: str``
- ``REQUIRED_KEYS: tuple[str, ...]`` -- claves nombradas que definen la forma
- ``TOTAL_PAGES_HEADER: str | None`` -- header de total de paginas, si la
  plataforma lo emite
- ``recognizes(payload) -> bool``
- ``rows_of(payload) -> tuple[Any, ...]``
- ``next_page_url(base, route, page) -> str``
- ``to_rows(payload, *, url, site, read_at) -> AdapterResult``
"""

from __future__ import annotations

from types import ModuleType

from . import woo

ADAPTERS: dict[str, ModuleType] = {woo.PLATFORM: woo}
```

```python
# src/catalog/adapters/woo.py
"""Mapeo explicito del esquema de producto de la WooCommerce Store API.

Cada atributo canonico sale de una clave NOMBRADA en una constante de este
modulo. No se infiere el mapeo por forma del JSON: buscar "una lista de dicts
con una clave parecida a nombre y otra parecida a precio" produce filas
plausibles y erroneas sin fallar nunca, y es exactamente el bug que este
componente existe para prohibir.

Nota de esquema (verificada contra el codigo fuente de WooCommerce 10.9.4): las
rutas ``/wp-json/wc/store/products`` y ``/wp-json/wc/store/v1/products`` son
alias del MISMO set v1, con schema, parametros y headers identicos. Hay un solo
adaptador Woo; lo unico que cambia entre las tres variantes es la URL sondeada.
"""

from __future__ import annotations

from datetime import datetime
from types import MappingProxyType
from typing import Any, Mapping

from ..record import (
    DROP_FILA_NO_DICT,
    DROP_SIN_TITULO,
    AdapterResult,
    Attribution,
    CatalogRow,
    dig,
    resolve,
)

PLATFORM = "woo"

# Claves que definen la forma. Un 200 con JSON que no las trae NO es match: se
# sigue sondeando y nunca se adivina el mapeo de campos.
REQUIRED_KEYS: tuple[str, ...] = ("id", "prices")
TOTAL_PAGES_HEADER: str | None = "X-WP-TotalPages"

TITLE_KEYS: tuple[str, ...] = ("name",)
SKU_KEYS: tuple[str, ...] = ("sku",)
# El ProductSchema de la Store API no declara ningun campo de GTIN/EAN, asi que
# ``gtin`` queda no_declarado en Woo. No se busca en ``attributes[]``: eso seria
# rellenar lo que no se sabe con lo mas probable.
GTIN_KEYS: tuple[str, ...] = ()
CATEGORIES_KEY = "categories"


def recognizes(payload: Any) -> bool:
    """True solo si el payload tiene la forma nombrada de un feed de Woo.

    Un arreglo vacio se reconoce: una tienda sin productos publicados es un feed
    valido con cero filas, no una forma inesperada.
    """
    if not isinstance(payload, list):
        return False
    if not payload:
        return True
    return any(
        isinstance(item, Mapping) and all(key in item for key in REQUIRED_KEYS)
        for item in payload
    )


def rows_of(payload: Any) -> tuple[Any, ...]:
    """Las filas crudas de una pagina de feed."""
    return tuple(payload) if isinstance(payload, list) else ()


def next_page_url(base: str, route: str, page: int) -> str:
    """La ruta sondeada ya trae ``per_page``; la pagina se agrega encima."""
    return f"{base}{route}&page={page}"


def _categories(raw: Mapping[str, Any]) -> tuple[Attribution, ...]:
    declared = raw.get(CATEGORIES_KEY)
    if not isinstance(declared, (list, tuple)):
        return ()
    found: list[Attribution] = []
    for index in range(len(declared)):
        # El indice es el del payload crudo, y esa es la procedencia que viaja:
        # una categoria sin nombre no corre el indice de las que si lo tienen.
        key = f"{CATEGORIES_KEY}.{index}.name"
        attribution = resolve(((key, dig(raw, key)),))
        if attribution.state == "declarado":
            found.append(attribution)
    return tuple(found)


def to_rows(payload: Any, *, url: str, site: str, read_at: datetime) -> AdapterResult:
    """Mapea una pagina de feed a filas canonicas mas los motivos crudos."""
    rows: list[CatalogRow] = []
    dropped: list[str] = []
    for raw in rows_of(payload):
        if not isinstance(raw, Mapping):
            dropped.append(DROP_FILA_NO_DICT)
            continue
        title = resolve(tuple((key, dig(raw, key)) for key in TITLE_KEYS))
        # No hace falta re-chequear el blanco: C14 lo garantiza dentro de resolve,
        # una sola vez para los siete atributos y las dos plataformas.
        if title.state != "declarado":
            dropped.append(DROP_SIN_TITULO)
            continue
        rows.append(
            CatalogRow(
                url=url,
                site=site,
                read_at=read_at,
                title=title,
                sku=resolve(tuple((key, dig(raw, key)) for key in SKU_KEYS)),
                gtin=resolve(tuple((key, dig(raw, key)) for key in GTIN_KEYS)),
                categories=_categories(raw),
                raw=MappingProxyType(dict(raw)),
            )
        )
    return AdapterResult(rows=tuple(rows), dropped=tuple(dropped))
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
PYTHONPATH=. C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m pytest tests/test_catalog_record.py -q
C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m ruff check src tests examples
```

Salida esperada: **cero fallos** y `All checks passed!`. El mapeo de `sku` esta cubierto por el test de payload sintetico (`test_woo_maps_sku_from_the_named_key_with_that_exact_provenance`); el assert flexible sobre el fixture real existe solo porque ahi un `sku` ausente es `no_declarado` legitimo.

- [ ] **Step 5: Commit**

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
git add src/catalog/adapters/__init__.py src/catalog/adapters/woo.py tests/test_catalog_record.py
git commit -m "feat(catalog): adaptador Woo con claves nombradas y descartes de fila

Reconocimiento por claves requeridas (un 200 de forma inesperada no es match),
titulo/sku/categorias por nombre explicito y los dos unicos motivos de descarte.
gtin queda no_declarado porque el schema de la Store API no lo declara: no se
busca en attributes[] por heuristica."
```

---

### Task 7: Precio escalado por `currency_minor_unit`, y moneda

La trampa del 100x. En la Store API los precios vienen como enteros en unidad menor con el factor en otro campo: `"1990"` con `currency_minor_unit` 2 es 19,90. En una tienda CLP el factor suele ser 0, y ahi `"19990"` es 19.990 CLP: asumir 2 daria 199,90.

**Files:**
- Modify: `src/catalog/record.py`, `src/catalog/adapters/woo.py`
- Test: `tests/test_catalog_record.py`

**Interfaces:**
- Consumes: `Attribution`, `evidence_of`, `resolve`, `UNDECLARED_*` (Tareas 3 y 5).
- Produces:
  - `def parse_amount(raw: Any) -> Decimal | None` en `record.py` -- **la unica** definicion de "monto parseable" (string o `int` no-bool que `Decimal` acepte). Existe porque si no, la regla queda reimplementada en tres lugares con tres criterios distintos (`amount.isdigit()` en un test, `is None` en otro, `Decimal(str(raw))` en la implementacion) y la expectativa de un test de fixture pasa a depender de que payload se grabo: una bomba de tiempo dentro de la tarea que fija los fixtures.
  - `def scale_price(raw: Any, minor_unit: Any, *, key: str) -> tuple[Attribution, tuple[str, ...]]` en `record.py` -- devuelve la `Attribution` del precio y los motivos de `undeclared_reasons` que disparo. La reusa el adaptador Shopify en la Tarea 9.
  - `woo.PRICE_KEY = "prices.price"`, `woo.MINOR_UNIT_KEY = "prices.currency_minor_unit"`, `woo.CURRENCY_KEYS = ("prices.currency_code",)`.
  - `woo.to_rows` ahora puebla `price` y `currency` y devuelve `AdapterResult.undeclared`.

- [ ] **Step 1: Write the failing test**

Agregar a `tests/test_catalog_record.py` (imports nuevos: `from decimal import Decimal`, y `parse_amount` + `scale_price` desde `src.catalog.record`):

```python
def test_scale_price_scales_by_minor_unit_and_keeps_the_raw_string_as_evidence() -> None:
    attribution, reasons = scale_price("1990", 2, key="prices.price")
    assert attribution.state == "declarado"
    assert attribution.value == "19.90"
    assert Decimal(attribution.value) == Decimal("19.90")
    assert attribution.evidence == "1990"
    assert attribution.source == "feed:prices.price"
    assert reasons == ()


def test_scale_price_with_minor_unit_zero_leaves_the_amount_untouched() -> None:
    attribution, reasons = scale_price("19990", 0, key="prices.price")
    assert attribution.value == "19990"
    assert attribution.evidence == "19990"
    assert reasons == ()


def test_scale_price_without_minor_unit_is_undeclared_and_never_assumes_two() -> None:
    attribution, reasons = scale_price("1990", None, key="prices.price")
    assert attribution.state == "no_declarado"
    assert attribution.value is None
    assert attribution.value != "19.90"
    assert reasons == (UNDECLARED_MINOR_UNIT_AUSENTE,)


@pytest.mark.parametrize("bad", [None, "", "  ", "abc", "1.9.9", True, 19.9, {"price": "1990"}])
def test_scale_price_with_an_unusable_amount_is_undeclared_and_counted(bad: object) -> None:
    attribution, reasons = scale_price(bad, 2, key="prices.price")
    assert attribution.state == "no_declarado"
    assert UNDECLARED_PRECIO_NO_PARSEABLE in reasons


@pytest.mark.parametrize("bad_unit", ["2", -1, 2.0, True])
def test_scale_price_rejects_a_minor_unit_that_is_not_a_non_negative_int(bad_unit: object) -> None:
    attribution, reasons = scale_price("1990", bad_unit, key="prices.price")
    assert attribution.state == "no_declarado"
    assert UNDECLARED_MINOR_UNIT_AUSENTE in reasons


@pytest.mark.parametrize(
    "raw, expected_declared",
    [("1990", True), ("19990", True), (1990, True), ("199.00", True), ("", False), ("abc", False),
     ("1.9.9", False), (True, False), (19.9, False), ({"price": "1990"}, False), (None, False)],
)
def test_parse_amount_is_the_single_definition_of_a_parseable_amount(raw: object, expected_declared: bool) -> None:
    """La MISMA regla que usa scale_price, para que ningun test la reimplemente
    con un criterio propio (y despues falle sobre codigo correcto segun que
    payload se grabo en la Tarea 1)."""
    assert (parse_amount(raw) is not None) is expected_declared


def test_scale_price_refuses_to_scale_a_value_that_already_carries_a_decimal_separator() -> None:
    """Un separador decimal es evidencia de que el monto ya esta en unidad mayor.
    Escalarlo por un factor afirmado es el bug de 100x: "19990.00" con factor 2
    saldria 199.90, declarado y plausible, sin fallar nunca. La evidencia del
    payload gana sobre el factor afirmado: si se contradicen, no se afirma nada."""
    attribution, reasons = scale_price("19990.00", 2, key="variants.0.price")
    assert attribution.state == "no_declarado"
    assert attribution.value is None
    assert attribution.value != "199.90"
    assert UNDECLARED_MINOR_UNIT_AUSENTE in reasons
    # Con factor 0 no hay contradiccion: el valor se deja tal cual.
    sin_escalar, sin_motivos = scale_price("19990.00", 0, key="variants.0.price")
    assert sin_escalar.state == "declarado"
    assert Decimal(sin_escalar.value) == Decimal("19990.00")
    assert sin_escalar.evidence == "19990.00"
    assert sin_motivos == ()


def test_woo_price_on_the_real_fixture_honours_that_stores_own_minor_unit() -> None:
    result = _woo_rows(_WOO_V1)
    for row, raw in zip(result.rows, _WOO_V1):
        prices = raw.get("prices") or {}
        minor = prices.get("currency_minor_unit")
        amount = prices.get("price")
        parseable = parse_amount(amount) is not None
        unit_ok = isinstance(minor, int) and not isinstance(minor, bool) and minor >= 0
        no_contradice = not (isinstance(amount, str) and "." in amount and unit_ok and minor != 0)
        if parseable and unit_ok and no_contradice:
            assert row.price.state == "declarado"
            assert row.price.evidence == amount
            assert row.price.source == "feed:prices.price"
            assert Decimal(row.price.value) == Decimal(amount).scaleb(-minor)
            if minor == 0:
                assert "." not in row.price.value
        else:
            assert row.price.state == "no_declarado"


def test_woo_row_without_minor_unit_emits_the_row_with_the_price_undeclared() -> None:
    payload = [
        {"id": n, "name": f"Producto {n}", "prices": {"price": "1990", "currency_code": "CLP"}}
        for n in (1, 2, 3)
    ]
    result = _woo_rows(payload)
    assert len(result.rows) == 3
    assert result.dropped == ()
    assert all(row.price.state == "no_declarado" for row in result.rows)
    assert ("MINOR_UNIT_AUSENTE", 3) in tally(result.undeclared, UNDECLARED_REASONS)


def test_woo_row_without_a_price_is_emitted_never_dropped() -> None:
    payload = [
        {"id": 1, "name": "Con precio", "prices": {"price": "1990", "currency_minor_unit": 2, "currency_code": "CLP"}},
        {"id": 2, "name": "Sin precio", "prices": {"currency_minor_unit": 2, "currency_code": "CLP"}},
    ]
    result = _woo_rows(payload)
    assert [row.title.value for row in result.rows] == ["Con precio", "Sin precio"]
    assert result.dropped == ()
    assert result.rows[1].price.state == "no_declarado"
    assert ("PRECIO_NO_PARSEABLE", 1) in tally(result.undeclared, UNDECLARED_REASONS)


def test_woo_maps_currency_from_the_currency_code_key() -> None:
    payload = [{"id": 1, "name": "P", "prices": {"price": "1990", "currency_minor_unit": 2, "currency_code": "CLP"}}]
    row = _woo_rows(payload).rows[0]
    assert row.currency.state == "declarado"
    assert row.currency.value == "CLP"
    assert row.currency.source == "feed:prices.currency_code"


def test_woo_without_currency_code_counts_moneda_ausente_and_still_emits() -> None:
    payload = [{"id": 1, "name": "P", "prices": {"price": "1990", "currency_minor_unit": 2}}]
    result = _woo_rows(payload)
    assert len(result.rows) == 1
    assert result.rows[0].currency.state == "no_declarado"
    assert ("MONEDA_AUSENTE", 1) in tally(result.undeclared, UNDECLARED_REASONS)


def test_a_missing_price_is_an_undeclared_attribute_and_a_missing_title_is_a_dropped_row() -> None:
    """rows_dropped y undeclared_reasons son dos cosas distintas y no se mezclan."""
    sin_precio = _woo_rows([{"id": 1, "name": "P", "prices": {"currency_minor_unit": 2, "currency_code": "CLP"}}])
    assert sin_precio.dropped == ()
    assert tally(sin_precio.undeclared, UNDECLARED_REASONS) != ()

    sin_titulo = _woo_rows([{"id": 1, "prices": {"price": "1990", "currency_minor_unit": 2, "currency_code": "CLP"}}])
    assert tally(sin_titulo.dropped, DROP_REASONS) == (("SIN_TITULO", 1),)
    assert sin_titulo.undeclared == ()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
PYTHONPATH=. C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m pytest tests/test_catalog_record.py -q
```

Salida esperada: error de coleccion, `ImportError: cannot import name 'scale_price' from 'src.catalog.record'`.

- [ ] **Step 3: Write minimal implementation**

En `src/catalog/record.py`: agregar `from decimal import Decimal, InvalidOperation` y agregar despues de `resolve`:

```python
def parse_amount(raw: Any) -> Decimal | None:
    """El monto crudo como ``Decimal``, o ``None`` si no es un monto.

    Unica definicion de "monto parseable" en todo el paquete: un ``str`` o un
    ``int`` (no bool) que ``Decimal`` acepte. Los tests la usan para derivar su
    expectativa en vez de reimplementar la regla, que es como una expectativa
    termina dependiendo de que payload se grabo.
    """
    if isinstance(raw, bool) or not isinstance(raw, (str, int)):
        return None
    try:
        return Decimal(str(raw))
    except InvalidOperation:
        return None


def scale_price(raw: Any, minor_unit: Any, *, key: str) -> tuple[Attribution, tuple[str, ...]]:
    """Escala un monto entero en unidad menor a unidad mayor.

    ``evidence`` guarda el string crudo tal cual vino; ``value`` es el monto ya
    escalado por ``minor_unit``. Si ``minor_unit`` no viene, o no es un entero no
    negativo, el precio queda ``no_declarado`` con ``MINOR_UNIT_AUSENTE``: **no
    se asume 2**. Una tienda CLP suele declarar 0, y asumir 2 divide cada precio
    por cien sin fallar nunca. Y si el monto ya trae separador decimal, un factor
    no nulo lo contradice: tampoco se afirma (ver abajo).

    Devuelve ``(attribution, motivos)`` donde los motivos son entradas de
    ``UNDECLARED_REASONS`` -- la fila se emite igual: descartarla por un campo
    ausente es esconder cobertura.
    """
    reasons: list[str] = []
    unit_ok = isinstance(minor_unit, int) and not isinstance(minor_unit, bool) and minor_unit >= 0

    # Un monto que ya trae separador decimal esta expresado en unidad MAYOR: el
    # propio payload lo declara con su forma. Escalarlo por un factor no nulo es
    # el bug de 100x, y ademas significa que el factor afirmado contradice la
    # evidencia. Ante la contradiccion no se afirma nada: gana el payload.
    already_major = isinstance(raw, str) and "." in raw
    if unit_ok and already_major and int(minor_unit) != 0:
        unit_ok = False

    if not unit_ok:
        reasons.append(UNDECLARED_MINOR_UNIT_AUSENTE)

    amount = parse_amount(raw)
    if amount is None:
        reasons.append(UNDECLARED_PRECIO_NO_PARSEABLE)

    if amount is None or not unit_ok:
        return Attribution(), tuple(reasons)
    scaled = amount.scaleb(-int(minor_unit))
    return Attribution(value=str(scaled), source=SOURCE_PREFIX_FEED + key, evidence=evidence_of(raw)), ()
```

Nota de orden: `scale_price` usa `UNDECLARED_*`, que en el archivo estan declarados mas abajo. Como el uso es en tiempo de ejecucion (dentro de la funcion), el orden textual no importa; dejar `scale_price` junto a `resolve` mantiene juntas las tres funciones de mapeo.

En `src/catalog/adapters/woo.py`: agregar a los imports de `..record` los nombres `UNDECLARED_MONEDA_AUSENTE`, `scale_price`; agregar las constantes y poblar los dos atributos:

```python
PRICE_KEY = "prices.price"
MINOR_UNIT_KEY = "prices.currency_minor_unit"
CURRENCY_KEYS: tuple[str, ...] = ("prices.currency_code",)
```

Dentro de `to_rows`, antes de construir la fila:

```python
        price, price_reasons = scale_price(dig(raw, PRICE_KEY), dig(raw, MINOR_UNIT_KEY), key=PRICE_KEY)
        currency = resolve(tuple((key, dig(raw, key)) for key in CURRENCY_KEYS))
        undeclared.extend(price_reasons)
        if currency.state != "declarado":
            undeclared.append(UNDECLARED_MONEDA_AUSENTE)
```

pasando `price=price, currency=currency` al `CatalogRow`, declarando `undeclared: list[str] = []` junto a `dropped`, y devolviendo `AdapterResult(rows=tuple(rows), dropped=tuple(dropped), undeclared=tuple(undeclared))`.

- [ ] **Step 4: Run test to verify it passes**

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
PYTHONPATH=. C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m pytest tests/test_catalog_record.py -q
C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m ruff check src tests examples
```

Salida esperada: **cero fallos** y `All checks passed!`. En verde en particular: `test_scale_price_without_minor_unit_is_undeclared_and_never_assumes_two` y `test_scale_price_refuses_to_scale_a_value_that_already_carries_a_decimal_separator`.

- [ ] **Step 5: Commit**

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
git add src/catalog/record.py src/catalog/adapters/woo.py tests/test_catalog_record.py
git commit -m "feat(catalog): precio escalado por currency_minor_unit y moneda declarada

evidence guarda el string crudo, value se escala por el factor del propio feed.
Sin currency_minor_unit el precio queda no_declarado con MINOR_UNIT_AUSENTE: no
se asume 2. Una fila sin precio se emite igual, con el motivo contado."
```

---

### Task 8: Stock Woo con arbitraje real, y peso segun la constatacion de la Tarea 1

Woo declara el stock por dos claves distintas (`is_in_stock` y `stock_availability.class`) que mapean al mismo atributo canonico. Ese es el par que hace real la guarda del arbitraje sobre datos de tienda.

**Files:**
- Modify: `src/catalog/adapters/woo.py`
- Test: `tests/test_catalog_record.py`

**Interfaces:**
- Consumes: `bool_stock_reading`, `resolve`, `dig` (Tarea 3); la decision A de la Tarea 1.
- Produces:
  - `woo.STOCK_BOOL_KEY = "is_in_stock"`, `woo.STOCK_CLASS_KEY = "stock_availability.class"`, `woo.WOO_STOCK_CLASSES: Mapping[str, str]`.
  - `woo.WEIGHT_KEYS: tuple[str, ...]` -- `("weight",)` o `()`, segun la decision A de la Tarea 1.
  - `woo.to_rows` ahora puebla `stock_status` y `weight_declared`.

- [ ] **Step 1: Write the failing test**

Agregar a `tests/test_catalog_record.py`, arriba de los tests nuevos, el cargador del archivo de decisiones de la Tarea 1 (una sola vez en el archivo):

```python
_DECISIONS = json.loads((FIXTURES / "DECISIONS.json").read_text(encoding="utf-8"))
```

Y los tests:

```python
def test_woo_stock_maps_from_the_boolean_key_when_both_agree() -> None:
    payload = [
        {
            "id": 1,
            "name": "P",
            "prices": {"price": "1990", "currency_minor_unit": 2, "currency_code": "CLP"},
            "is_in_stock": True,
            "stock_availability": {"text": "En stock", "class": "in-stock"},
        }
    ]
    row = _woo_rows(payload).rows[0]
    assert row.stock_status.state == "declarado"
    assert row.stock_status.value == "in_stock"
    assert row.stock_status.source == "feed:is_in_stock"
    assert row.stock_status.evidence == "true"


def test_woo_stock_conflict_is_contradicho_not_first_wins() -> None:
    """Guarda del arbitraje sobre un par de claves que Woo publica de verdad.
    Payload derivado del fixture real: se le invierte una sola clave."""
    payload = [dict(_WOO_V1[0])]
    payload[0]["is_in_stock"] = True
    payload[0]["stock_availability"] = {"text": "Agotado", "class": "out-of-stock"}
    row = _woo_rows(payload).rows[0]
    assert row.stock_status.state == "contradicho"
    assert row.stock_status.value is None
    assert row.stock_status.conflicts == (
        ("feed:is_in_stock", "true"),
        ("feed:stock_availability.class", "out-of-stock"),
    )


def test_woo_stock_falls_back_to_the_other_key_when_one_is_absent() -> None:
    solo_clase = [
        {
            "id": 1,
            "name": "P",
            "prices": {"price": "1990", "currency_minor_unit": 2, "currency_code": "CLP"},
            "stock_availability": {"class": "out-of-stock"},
        }
    ]
    row = _woo_rows(solo_clase).rows[0]
    assert row.stock_status.value == "out_of_stock"
    assert row.stock_status.source == "feed:stock_availability.class"


def test_woo_stock_with_neither_key_is_no_declarado() -> None:
    payload = [{"id": 1, "name": "P", "prices": {"price": "1990", "currency_minor_unit": 2, "currency_code": "CLP"}}]
    assert _woo_rows(payload).rows[0].stock_status.state == "no_declarado"


def test_woo_stock_classes_is_a_closed_vocabulary() -> None:
    assert dict(woo.WOO_STOCK_CLASSES) == {
        "in-stock": "in_stock",
        "out-of-stock": "out_of_stock",
        "available-on-backorder": "backorder",
    }


def test_woo_weight_comes_only_from_a_named_feed_key_never_from_the_title() -> None:
    result = _woo_rows(_WOO_V1)
    for row in result.rows:
        if row.weight_declared.state == "declarado":
            assert row.weight_declared.source is not None
            assert row.weight_declared.source.startswith("feed:")
            assert row.weight_declared.source != "titulo"


def test_woo_weight_keys_match_the_recorded_payload_and_the_committed_decision() -> None:
    """La constante NO se testea contra si misma. Un test que ramifica sobre el
    valor que esta verificando queda verde con cualquier valor que el
    implementador tipee, incluido el placeholder del plan. La expectativa se
    deriva de las dos fuentes de verdad: el payload grabado y DECISIONS.json."""
    trae_weight = any("weight" in row for payload in (_WOO_V1, _WOO_STORE, _WOO_FLAT) for row in payload)
    assert (woo.WEIGHT_KEYS == ("weight",)) is trae_weight
    assert woo.WEIGHT_KEYS in (("weight",), ())
    assert list(woo.WEIGHT_KEYS) == _DECISIONS["woo_weight_keys"]
    # La unidad no viaja en el payload de la Store API: queda declarado en el
    # archivo de decisiones para que el consumidor no lo tome como kg.
    assert _DECISIONS["woo_weight_unit_declared"] is False


def test_woo_weight_when_mapped_comes_from_that_exact_key() -> None:
    """El comportamiento del mapeo, sobre un payload sintetico y solo en la rama
    que la Tarea 1 fijo."""
    payload = [
        {
            "id": 1,
            "name": "P",
            "prices": {"price": "1990", "currency_minor_unit": 2, "currency_code": "CLP"},
            "weight": "2.5",
            "formatted_weight": "2,5 kg",
        }
    ]
    row = _woo_rows(payload).rows[0]
    if woo.WEIGHT_KEYS == ("weight",):
        assert row.weight_declared.state == "declarado"
        assert row.weight_declared.value == "2.5"
        assert row.weight_declared.evidence == "2.5"
        assert row.weight_declared.source == "feed:weight"
    else:
        assert row.weight_declared.state == "no_declarado"


def test_woo_never_parses_the_localized_formatted_weight_string() -> None:
    """formatted_weight es una cadena localizada y traducible, y devuelve el
    texto traducido de "N/A" cuando el peso no esta seteado: parsearla como
    numero es el bug de 100x en otra forma."""
    payload = [
        {
            "id": 1,
            "name": "P",
            "prices": {"price": "1990", "currency_minor_unit": 2, "currency_code": "CLP"},
            "formatted_weight": "N/A",
        }
    ]
    row = _woo_rows(payload).rows[0]
    assert row.weight_declared.state == "no_declarado"
    assert "formatted_weight" not in woo.WEIGHT_KEYS


def test_woo_never_hunts_for_the_weight_inside_attributes() -> None:
    payload = [
        {
            "id": 1,
            "name": "P",
            "prices": {"price": "1990", "currency_minor_unit": 2, "currency_code": "CLP"},
            "attributes": [{"name": "Peso", "terms": [{"name": "2.5 kg"}]}],
        }
    ]
    assert _woo_rows(payload).rows[0].weight_declared.state == "no_declarado"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
PYTHONPATH=. C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m pytest tests/test_catalog_record.py -q
```

Salida esperada: **rojo** con `AttributeError: module 'src.catalog.adapters.woo' has no attribute 'WOO_STOCK_CLASSES'` y `assert 'no_declarado' == 'declarado'` en los de stock. Los que tienen que estar en rojo, por nombre: `test_woo_stock_maps_from_the_boolean_key_when_both_agree`, `test_woo_stock_conflict_is_contradicho_not_first_wins`, `test_woo_stock_falls_back_to_the_other_key_when_one_is_absent`, `test_woo_stock_classes_is_a_closed_vocabulary`, `test_woo_weight_keys_match_the_recorded_payload_and_the_committed_decision` y `test_woo_weight_when_mapped_comes_from_that_exact_key`.

**Dos de los tests nuevos arrancan en VERDE y hay que probar que no son vacios** (el peso por default es `no_declarado`, y el cuerpo del `for` de uno de ellos no corre): `test_woo_never_hunts_for_the_weight_inside_attributes` y `test_woo_weight_comes_only_from_a_named_feed_key_never_from_the_title`. Mini-mutacion, una y se revierte: en `woo.py` cambiar `WEIGHT_KEYS` por `("attributes.0.terms.0.name",)` y correr los dos -- tienen que ponerse rojos. Despues `git checkout -- src/catalog/adapters/woo.py`.

- [ ] **Step 3: Write minimal implementation**

En `src/catalog/adapters/woo.py`: agregar `bool_stock_reading` al import de `..record` y agregar las constantes y el normalizador:

```python
STOCK_BOOL_KEY = "is_in_stock"
STOCK_CLASS_KEY = "stock_availability.class"
# Vocabulario cerrado. Una clase desconocida no se interpreta: el candidato se
# descarta del arbitraje y decide el otro.
WOO_STOCK_CLASSES: Mapping[str, str] = MappingProxyType(
    {
        "in-stock": "in_stock",
        "out-of-stock": "out_of_stock",
        "available-on-backorder": "backorder",
    }
)

# Decision A de la Tarea 1 (constatacion empirica, spec 7.2). La Store API solo
# declara ``weight`` desde WooCommerce 10.7.0; en tiendas anteriores la clave no
# existe y ``weight_declared`` queda no_declarado, que es un desenlace legitimo.
# La UNIDAD del peso no viaja en el payload (es un setting de sitio), y
# ``formatted_weight`` es una cadena localizada y traducible que devuelve el
# texto traducido de "N/A" cuando el peso no esta seteado: no se parsea nunca.
# LIMITACION CONOCIDA, y es de contrato, no cosmetica: ``weight_declared`` en Woo
# es una magnitud SIN unidad declarada. El consumidor no puede tratarla como kg;
# el enganche futuro del normalizador de peso tiene que leer
# ``woo_weight_unit_declared`` de tests/fixtures/catalog/DECISIONS.json primero.
WEIGHT_KEYS: tuple[str, ...] = ("weight",)  # o () -- el valor sale de DECISIONS.json (Tarea 1)


def _stock_reading(raw: Any) -> str | None:
    if isinstance(raw, bool):
        return bool_stock_reading(raw)
    if isinstance(raw, str):
        return WOO_STOCK_CLASSES.get(raw)
    return None


def _stock(raw: Mapping[str, Any]) -> Attribution:
    """Dos claves del feed que mapean al mismo atributo canonico. Si no
    coinciden, ``contradicho``: nunca gana la primera."""
    return resolve(
        (
            (STOCK_BOOL_KEY, dig(raw, STOCK_BOOL_KEY)),
            (STOCK_CLASS_KEY, dig(raw, STOCK_CLASS_KEY)),
        ),
        normalizer=_stock_reading,
    )
```

Y en `to_rows`, pasar al `CatalogRow`:

```python
                stock_status=_stock(raw),
                weight_declared=resolve(tuple((key, dig(raw, key)) for key in WEIGHT_KEYS)),
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
PYTHONPATH=. C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m pytest tests/test_catalog_record.py -q
C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m ruff check src tests examples
```

Salida esperada: **cero fallos** y `All checks passed!`, con el arbol limpio (`git status --short` sin la mini-mutacion del paso 2).

- [ ] **Step 5: Commit**

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
git add src/catalog/adapters/woo.py tests/test_catalog_record.py
git commit -m "feat(catalog): stock Woo con arbitraje de dos claves y peso por clave nombrada

is_in_stock y stock_availability.class mapean al mismo atributo canonico: si no
coinciden el resultado es contradicho con las dos claves y sus valores crudos.
El peso sale de la clave nombrada de la constatacion de la Tarea 1;
formatted_weight no se parsea y attributes[] no se recorre."
```

---

### Task 9: Adaptador Shopify

`/products.json` de storefront **no esta documentado por Shopify**: las superficies que si estan documentadas (Ajax API, Admin REST, Liquid) discrepan entre si en el mismo campo -- la Ajax API devuelve el precio como entero en centavos (`12900`) y Admin REST como string decimal (`"199.00"`). Por eso el adaptador mapea solo claves nombradas, cada una independientemente opcional, y la unidad menor del precio se rige por **C12**: `PRICE_MINOR_UNIT` admite `0` o `None` y nada mas, el `0` solo cuando la **forma del propio valor** lo declara (string con separador decimal) y la ficha publica lo confirma, y con `0` el precio se afirma **solo** sobre valores de esa misma forma. No se infiere por magnitud del numero en tiempo de corrida ("esto parece centavos porque es grande" es exactamente el bug de 100x) y **no se afirma un factor de plataforma desde una sola tienda**: eso es el mismo bug cometido en el diseno en vez de por olvido.

**Files:**
- Create: `src/catalog/adapters/shopify.py`
- Modify: `src/catalog/adapters/__init__.py`
- Test: `tests/test_catalog_record.py`

**Interfaces:**
- Consumes: `AdapterResult`, `Attribution`, `CatalogRow`, `DROP_FILA_NO_DICT`, `DROP_SIN_TITULO`, `UNDECLARED_MONEDA_AUSENTE`, `bool_stock_reading`, `dig`, `resolve`, `scale_price` (Tareas 3-7); las decisiones B y C de la Tarea 1.
- Produces:
  - `PLATFORM = "shopify"`, `REQUIRED_KEYS = ("handle", "variants")`, `TOTAL_PAGES_HEADER = None`, `ROWS_KEY = "products"`.
  - `TITLE_KEYS`, `SKU_KEYS`, `GTIN_KEYS`, `CURRENCY_KEYS`, `STOCK_KEYS`, `WEIGHT_KEYS`, `PRICE_KEY`, `PRICE_MINOR_UNIT: int | None`.
  - `recognizes`, `rows_of`, `next_page_url`, `to_rows` con las mismas firmas que `woo`.
  - `ADAPTERS` con las dos plataformas.

- [ ] **Step 1: Write the failing test**

Agregar a `tests/test_catalog_record.py` (import nuevo: `from src.catalog.adapters import shopify`):

```python
_SHOPIFY = json.loads((FIXTURES / "shopify_products.json").read_text(encoding="utf-8"))
SHOPIFY_URL = "https://tienda.myshopify.test/products.json?limit=250"


def _shopify_rows(payload: object) -> AdapterResult:
    return shopify.to_rows(payload, url=SHOPIFY_URL, site="tienda.myshopify.test", read_at=READ_AT)


def test_shopify_is_in_the_dispatch_table_alongside_woo() -> None:
    assert ADAPTERS == {"woo": woo, "shopify": shopify}
    assert shopify.PLATFORM == "shopify"


def test_shopify_recognizes_its_recorded_payload_and_rejects_other_shapes() -> None:
    assert shopify.recognizes(_SHOPIFY) is True
    assert shopify.recognizes({"products": []}) is True
    assert shopify.recognizes({"foo": [{"handle": "x"}]}) is False
    assert shopify.recognizes(_WOO_V1) is False
    assert shopify.recognizes(None) is False


def test_shopify_rows_of_reads_the_products_array() -> None:
    assert len(shopify.rows_of(_SHOPIFY)) == len(_SHOPIFY["products"])
    assert shopify.rows_of({"products": []}) == ()
    assert shopify.rows_of({}) == ()


def test_shopify_next_page_url_appends_the_page_parameter() -> None:
    assert shopify.next_page_url("https://tienda.test", "/products.json?limit=250", 3) == (
        "https://tienda.test/products.json?limit=250&page=3"
    )


def test_shopify_has_no_total_pages_header_so_it_cuts_on_an_empty_array() -> None:
    assert shopify.TOTAL_PAGES_HEADER is None


def test_shopify_maps_title_sku_gtin_and_stock_by_explicit_name() -> None:
    result = _shopify_rows(_SHOPIFY)
    for row, raw in zip(result.rows, _SHOPIFY["products"]):
        assert row.title.value == raw["title"]
        assert row.title.source == "feed:title"
        variant = (raw.get("variants") or [{}])[0]
        if variant.get("sku"):
            assert row.sku.source == "feed:variants.0.sku"
            assert row.sku.value == variant["sku"]
        if variant.get("barcode"):
            assert row.gtin.source == "feed:variants.0.barcode"
        if isinstance(variant.get("available"), bool):
            assert row.stock_status.value == ("in_stock" if variant["available"] else "out_of_stock")
            assert row.stock_status.source == "feed:variants.0.available"


def test_shopify_price_minor_unit_is_never_two_and_matches_the_committed_decision() -> None:
    """C12. El factor NO viaja en el payload de Shopify, asi que un 2 global
    derivado de mirar una tienda y aplicado a todas es el bug de 100x hecho
    diseno: "19990.00" saldria 199.90, declarado y plausible, sin fallar nunca.
    Los unicos valores admisibles son 0 y None, y el valor tiene que coincidir con
    DECISIONS.json y con la forma del propio fixture."""
    assert shopify.PRICE_MINOR_UNIT in (0, None)
    assert shopify.PRICE_MINOR_UNIT != 2
    assert shopify.PRICE_MINOR_UNIT == _DECISIONS["shopify_price_minor_unit"]
    variant = ((_SHOPIFY["products"][0].get("variants") or [{}])[0])
    forma_decimal = isinstance(variant.get("price"), str) and "." in variant["price"]
    if shopify.PRICE_MINOR_UNIT == 0:
        assert forma_decimal, "el factor 0 solo se afirma si el payload declara unidad mayor por su forma"
        assert _DECISIONS["shopify_price_evidence"]["payload_shape"] == "string decimal"


def test_shopify_price_is_declared_only_for_a_raw_of_the_recorded_shape() -> None:
    """Con el factor 0, un entero desnudo NO es la forma que se verifico: se
    declara no_declarado en vez de emitir un precio 100x arriba."""
    decimal_shape = {"products": [{"handle": "p", "title": "P", "variants": [{"price": "19990.00"}]}]}
    integer_shape = {"products": [{"handle": "p", "title": "P", "variants": [{"price": "12900"}]}]}

    if shopify.PRICE_MINOR_UNIT == 0:
        row = _shopify_rows(decimal_shape).rows[0]
        assert row.price.state == "declarado"
        assert row.price.evidence == "19990.00"
        assert row.price.value is not None and Decimal(row.price.value) == Decimal("19990.00")
        assert row.price.source == "feed:variants.0.price"
    else:
        assert _shopify_rows(decimal_shape).rows[0].price.state == "no_declarado"

    otra_forma = _shopify_rows(integer_shape)
    assert otra_forma.rows[0].price.state == "no_declarado"
    assert otra_forma.rows[0].price.value != "129.00"
    assert ("MINOR_UNIT_AUSENTE", 1) in tally(otra_forma.undeclared, UNDECLARED_REASONS)


def test_shopify_price_on_the_real_fixture_keeps_the_raw_value_as_evidence() -> None:
    result = _shopify_rows(_SHOPIFY)
    for row, raw in zip(result.rows, _SHOPIFY["products"]):
        variant = (raw.get("variants") or [{}])[0]
        precio = variant.get("price")
        declarable = (
            shopify.PRICE_MINOR_UNIT == 0
            and parse_amount(precio) is not None
            and isinstance(precio, str)
            and "." in precio
        )
        if not declarable:
            assert row.price.state == "no_declarado"
        else:
            assert row.price.state == "declarado"
            assert row.price.evidence == precio


def test_shopify_weight_keys_match_the_recorded_variant_and_the_committed_decision() -> None:
    """Igual que en Woo: la expectativa se deriva del fixture y de DECISIONS.json,
    no de la constante que se esta verificando."""
    variant = ((_SHOPIFY["products"][0].get("variants") or [{}])[0])
    assert (shopify.WEIGHT_KEYS == ("variants.0.grams",)) is ("grams" in variant)
    if "grams" not in variant:
        assert (shopify.WEIGHT_KEYS == ("variants.0.weight",)) is ("weight" in variant)
    assert list(shopify.WEIGHT_KEYS) == _DECISIONS["shopify_weight_keys"]


def test_shopify_weight_follows_the_task_one_finding_and_maps_exactly_one_key() -> None:
    """Decision B de la Tarea 1. grams y weight NO son candidatas de arbitraje:
    estan en unidades distintas y la propia doc de Shopify las muestra
    inconsistentes entre si, asi que compararlas daria contradicho espurio."""
    assert len(shopify.WEIGHT_KEYS) <= 1
    payload = {
        "products": [
            {"handle": "p", "title": "P", "variants": [{"price": "12900", "grams": 500, "weight": 17.6, "weight_unit": "oz"}]}
        ]
    }
    row = _shopify_rows(payload).rows[0]
    if shopify.WEIGHT_KEYS == ("variants.0.grams",):
        assert row.weight_declared.value == "500"
        assert row.weight_declared.evidence == "500"
        assert row.weight_declared.source == "feed:variants.0.grams"
    elif shopify.WEIGHT_KEYS == ("variants.0.weight",):
        assert row.weight_declared.source == "feed:variants.0.weight"
    else:
        assert shopify.WEIGHT_KEYS == ()
        assert row.weight_declared.state == "no_declarado"


def test_shopify_currency_is_named_but_absent_from_the_storefront_payload() -> None:
    payload = {"products": [{"handle": "p", "title": "P", "variants": [{"price": "12900"}]}]}
    result = _shopify_rows(payload)
    assert result.rows[0].currency.state == "no_declarado"
    assert ("MONEDA_AUSENTE", 1) in tally(result.undeclared, UNDECLARED_REASONS)
    assert shopify.CURRENCY_KEYS == ("currency",)


def test_shopify_maps_product_type_as_a_category_and_never_tags() -> None:
    payload = {
        "products": [
            {"handle": "p", "title": "P", "product_type": "Congelados", "tags": ["oferta", "kilo"], "variants": []}
        ]
    }
    row = _shopify_rows(payload).rows[0]
    assert [item.value for item in row.categories] == ["Congelados"]
    assert [item.source for item in row.categories] == ["feed:product_type"]


def test_shopify_drops_non_objects_and_titleless_rows_with_the_same_two_reasons() -> None:
    payload = {"products": ["nope", 7, {"handle": "a", "variants": []}, {"handle": "b", "title": "P", "variants": []}]}
    result = _shopify_rows(payload)
    assert [row.title.value for row in result.rows] == ["P"]
    assert tally(result.dropped, DROP_REASONS) == (("FILA_NO_DICT", 2), ("SIN_TITULO", 1))
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
PYTHONPATH=. C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m pytest tests/test_catalog_record.py -q
```

Salida esperada: error de coleccion, `ImportError: cannot import name 'shopify' from 'src.catalog.adapters'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/catalog/adapters/shopify.py
"""Mapeo explicito de /products.json de storefront.

Shopify no documenta este endpoint. Las tres superficies que SI documenta
discrepan entre si en el mismo campo: la Ajax API devuelve el precio como entero
en centavos (12900), Admin REST como string decimal ("199.00"), y el objeto
Liquid define ``weight`` como gramos mientras Admin REST tiene ``grams``,
``weight`` y ``weight_unit`` a la vez -- inconsistentes entre si en su propio
ejemplo. O sea que aca no se puede extrapolar de la documentacion: cada clave se
mapea por nombre, cada una es independientemente opcional, y lo que no esta en
el payload queda ``no_declarado``.

A diferencia de Woo, el payload NO trae ningun campo equivalente a
``currency_minor_unit``: el factor no viaja. Por eso ``PRICE_MINOR_UNIT`` es una
constante nombrada, fijada contra el precio mostrado en la ficha publica del
producto del fixture (Tarea 1 del plan, spec 7.2), con la evidencia al lado.
Nunca se infiere por magnitud del numero en tiempo de corrida.
"""

from __future__ import annotations

from datetime import datetime
from types import MappingProxyType
from typing import Any, Mapping

from ..record import (
    DROP_FILA_NO_DICT,
    DROP_SIN_TITULO,
    UNDECLARED_MINOR_UNIT_AUSENTE,
    UNDECLARED_MONEDA_AUSENTE,
    AdapterResult,
    Attribution,
    CatalogRow,
    bool_stock_reading,
    dig,
    resolve,
    scale_price,
)

PLATFORM = "shopify"

REQUIRED_KEYS: tuple[str, ...] = ("handle", "variants")
ROWS_KEY = "products"
# Shopify no emite header de total de paginas en esta superficie: el corte es por
# arreglo vacio, con deteccion de pagina repetida y tope de paginas en source.py.
TOTAL_PAGES_HEADER: str | None = None

TITLE_KEYS: tuple[str, ...] = ("title",)
SKU_KEYS: tuple[str, ...] = ("variants.0.sku",)
GTIN_KEYS: tuple[str, ...] = ("variants.0.barcode",)
# Nombrada aunque el payload de storefront no la traiga: si una tienda la emite,
# se mapea sin cambiar codigo. Si no viene, MONEDA_AUSENTE queda contado.
CURRENCY_KEYS: tuple[str, ...] = ("currency",)
STOCK_KEYS: tuple[str, ...] = ("variants.0.available",)
PRICE_KEY = "variants.0.price"

# Decision B de la Tarea 1: UNA sola clave de peso. ``grams`` y ``weight`` estan
# en unidades distintas, asi que no son dos lecturas de la misma magnitud y
# declararlas candidatas de arbitraje daria ``contradicho`` en cada fila.
WEIGHT_KEYS: tuple[str, ...] = ("variants.0.grams",)  # o ("variants.0.weight",) o ()

# Decision C de la Tarea 1, con la regla dura de C12: los unicos valores
# admisibles son 0 y None. El 2 esta PROHIBIDO -- el factor no viaja en el
# payload de Shopify, asi que afirmarlo global desde una sola tienda es el bug de
# 100x hecho diseno. El default es None (no se afirma) y solo pasa a 0 cuando el
# payload grabado declara unidad mayor POR SU FORMA (string con separador
# decimal) y la ficha publica lo confirma. Al fijarlo en 0, reemplazar esta linea
# por la evidencia real, con los dos numeros:
#   verificado contra <host>/products/<handle> el <fecha>:
#   payload "<valor>" == precio mostrado <valor>
PRICE_MINOR_UNIT: int | None = None

# Con PRICE_MINOR_UNIT == 0, el precio se afirma SOLO sobre un raw de la misma
# forma que la verificada. Un entero desnudo es la otra superficie de Shopify (la
# Ajax API devuelve centavos) y afirmarlo con factor 0 seria un precio 100x
# arriba: forma distinta a la verificada -> no se declara.
PRICE_SHAPE_DECIMAL_STRING = True

# El vocabulario de categorias de Shopify: ``product_type`` es la categoria del
# producto. ``tags`` NO se mapea a proposito -- una etiqueta de marketing no es
# una categoria, y tratarla como tal es interpretar, no leer.
CATEGORY_KEYS: tuple[str, ...] = ("product_type",)

# Shopify no tiene vocabulario de clases de stock: el stock es un booleano
# (``variants[].available``) y se lee con ``bool_stock_reading``. Nota, no simbolo.


def _price(raw: Any) -> tuple[Attribution, tuple[str, ...]]:
    """El precio, solo cuando el factor y la FORMA del valor estan verificados.

    Sin factor declarado (``PRICE_MINOR_UNIT is None``) el precio no se afirma:
    misma regla que Woo sin ``currency_minor_unit``. Con factor 0, ademas, la
    forma del raw tiene que ser la verificada en la Tarea 1 (string con separador
    decimal): un entero desnudo es la otra superficie de Shopify y afirmarlo seria
    un precio 100x arriba. En los dos casos la fila SE EMITE, con el motivo
    contado.
    """
    if PRICE_MINOR_UNIT is None:
        return Attribution(), (UNDECLARED_MINOR_UNIT_AUSENTE,)
    if PRICE_SHAPE_DECIMAL_STRING and not (isinstance(raw, str) and "." in raw):
        return Attribution(), (UNDECLARED_MINOR_UNIT_AUSENTE,)
    return scale_price(raw, PRICE_MINOR_UNIT, key=PRICE_KEY)


def recognizes(payload: Any) -> bool:
    """True solo si el payload es ``{"products": [...]}`` con las claves
    nombradas. Un arreglo de productos vacio se reconoce."""
    if not isinstance(payload, Mapping):
        return False
    products = payload.get(ROWS_KEY)
    if not isinstance(products, list):
        return False
    if not products:
        return True
    return any(
        isinstance(item, Mapping) and all(key in item for key in REQUIRED_KEYS)
        for item in products
    )


def rows_of(payload: Any) -> tuple[Any, ...]:
    if not isinstance(payload, Mapping):
        return ()
    products = payload.get(ROWS_KEY)
    return tuple(products) if isinstance(products, list) else ()


def next_page_url(base: str, route: str, page: int) -> str:
    return f"{base}{route}&page={page}"


def _categories(raw: Mapping[str, Any]) -> tuple[Attribution, ...]:
    found: list[Attribution] = []
    for key in CATEGORY_KEYS:
        attribution = resolve(((key, dig(raw, key)),))
        if attribution.state == "declarado" and str(attribution.value).strip():
            found.append(attribution)
    return tuple(found)


def to_rows(payload: Any, *, url: str, site: str, read_at: datetime) -> AdapterResult:
    rows: list[CatalogRow] = []
    dropped: list[str] = []
    undeclared: list[str] = []
    for raw in rows_of(payload):
        if not isinstance(raw, Mapping):
            dropped.append(DROP_FILA_NO_DICT)
            continue
        title = resolve(tuple((key, dig(raw, key)) for key in TITLE_KEYS))
        # El blanco lo garantiza resolve (C14): no se re-chequea aca.
        if title.state != "declarado":
            dropped.append(DROP_SIN_TITULO)
            continue
        price, price_reasons = _price(dig(raw, PRICE_KEY))
        currency = resolve(tuple((key, dig(raw, key)) for key in CURRENCY_KEYS))
        undeclared.extend(price_reasons)
        if currency.state != "declarado":
            undeclared.append(UNDECLARED_MONEDA_AUSENTE)
        rows.append(
            CatalogRow(
                url=url,
                site=site,
                read_at=read_at,
                title=title,
                price=price,
                currency=currency,
                sku=resolve(tuple((key, dig(raw, key)) for key in SKU_KEYS)),
                gtin=resolve(tuple((key, dig(raw, key)) for key in GTIN_KEYS)),
                weight_declared=resolve(tuple((key, dig(raw, key)) for key in WEIGHT_KEYS)),
                stock_status=resolve(
                    tuple((key, dig(raw, key)) for key in STOCK_KEYS), normalizer=bool_stock_reading
                ),
                categories=_categories(raw),
                raw=MappingProxyType(dict(raw)),
            )
        )
    return AdapterResult(rows=tuple(rows), dropped=tuple(dropped), undeclared=tuple(undeclared))
```

Nota sobre `_categories` en este adaptador: la comprobacion `and str(attribution.value).strip()` que el bloque trae es redundante desde C14 (la garantiza `resolve`); dejarla o quitarla es indiferente, pero **no** se agrega ninguna otra copia de esa regla.

En `src/catalog/adapters/__init__.py` cambiar el import y la tabla:

```python
from . import shopify, woo

ADAPTERS: dict[str, ModuleType] = {woo.PLATFORM: woo, shopify.PLATFORM: shopify}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
PYTHONPATH=. C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m pytest tests/test_catalog_record.py -q
C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m ruff check src tests examples
```

Salida esperada: **cero fallos** y `All checks passed!`. En verde en particular: `test_shopify_price_minor_unit_is_never_two_and_matches_the_committed_decision` y `test_shopify_price_is_declared_only_for_a_raw_of_the_recorded_shape`.

- [ ] **Step 5: Commit**

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
git add src/catalog/adapters/shopify.py src/catalog/adapters/__init__.py tests/test_catalog_record.py
git commit -m "feat(catalog): adaptador Shopify sin afirmar un factor de precio que no viaja

/products.json de storefront no esta documentado y las superficies oficiales de
Shopify discrepan en el mismo campo. El factor de unidad menor no viaja en el
payload, asi que solo admite 0 o None: el 0 unicamente cuando la forma del propio
valor declara unidad mayor (string con separador decimal) y la ficha publica lo
confirma, y solo se afirma el precio sobre valores de esa misma forma. Sin eso, el
precio queda no_declarado con MINOR_UNIT_AUSENTE contado, igual que Woo sin
currency_minor_unit. Una sola clave de peso: grams y weight estan en unidades
distintas y no son candidatas de arbitraje."
```

---

### Task 10: Sondeo de las cuatro rutas, en orden, registrando cada intento

**Files:**
- Create: `src/catalog/source.py`
- Test: `tests/test_catalog_source.py`

**Interfaces:**
- Consumes: `ADAPTERS` (Tarea 6/9); `CatalogRow`, `DROP_REASONS`, `ReadReport`, `UNDECLARED_REASONS`, `tally` (Tareas 4-5).
- Produces:
  - `USER_AGENT`, `DEFAULT_TIMEOUT_SECONDS = 15.0`, `DEFAULT_RATE_LIMIT_SECONDS = 1.0`, `MAX_PAGES = 100`, `STATUS_SIN_RESPUESTA = 0`.
  - `PROBE_ROUTES: tuple[tuple[str, str], ...]` -- las cuatro rutas con su plataforma, en el orden exacto del spec.
  - `def read_catalog(url: str, *, client: httpx.Client, sleeper: Callable[[float], None], rate_limit_seconds: float = DEFAULT_RATE_LIMIT_SECONDS, now: datetime | None = None) -> tuple[tuple[CatalogRow, ...], ReadReport]`.
  - Internos que las Tareas 11-14 extienden: `_Response` (con `url`, la URL **final** de la respuesta), `_Requester`, `_Probe`, `_parse_json`, `_site_of`, `_require_url`, `_probe_routes`.

**Nota de diseno de los Fakes de esta tarea (leerla antes de escribir los tests).** La Tarea 12 agrega el paginado, y el paginador pide la pagina 2 de todo feed encontrado que traiga filas y no declare cuantas paginas hay. Para que los tests de esta tarea no cambien de expectativa cuando eso llegue, se escriben **desde ya** terminando el feed de forma explicita: los fakes Woo de 200 llevan `{"X-WP-TotalPages": "1"}` (header inerte hasta la Tarea 12, y ahi corta el bucle en la pagina 1) y los fakes Shopify de 200 programan ademas su pagina 2 vacia (`SHOPIFY + "&page=2"`, ruta que nadie pide hasta la Tarea 12). Y las aserciones sobre esperas se escriben **relacionales** (`len(waits) == len(seen) - 1`), no con un conteo fijo, porque el conteo depende del paginado. Esto no es acomodar el test al codigo: es no escribir una expectativa que ya sabemos falsa dos tareas mas adelante.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_catalog_source.py
"""Tests de src/catalog/source.py con la red simulada.

Ninguna conexion real: el httpx.Client se construye sobre httpx.MockTransport y
una guarda autouse revienta si algo intenta abrir un socket. La espera de rate
limit tambien es falsa, asi que la suite no consume tiempo de reloj.
"""

from __future__ import annotations

import json
import socket
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx
import pytest

from src.catalog.record import ReadReport
from src.catalog.source import PROBE_ROUTES, STATUS_SIN_RESPUESTA, read_catalog

BASE = "https://tienda.test"
READ_AT = datetime(2026, 7, 25, 12, 0, 0, tzinfo=timezone.utc)

WOO_V1 = "/wp-json/wc/store/v1/products?per_page=100"
WOO_STORE = "/wp-json/wc/store/products?per_page=100"
WOO_FLAT = "/index.php/wp-json/wc/store/products?per_page=100"
SHOPIFY = "/products.json?limit=250"

WOO_PAGE = json.dumps([{"id": 1, "name": "Producto", "prices": {"price": "1990", "currency_minor_unit": 2, "currency_code": "CLP"}}])
WOO_EMPTY = "[]"
SHOPIFY_PAGE = json.dumps({"products": [{"handle": "p", "title": "Producto", "variants": [{"price": "12900"}]}]})
SHOPIFY_EMPTY = json.dumps({"products": []})

# Feed de una sola pagina: el header dice que hay una y el paginado de la Tarea 12
# no pide una segunda. Inerte hasta esa tarea.
ONE_PAGE: dict[str, str] = {"X-WP-TotalPages": "1"}


def _woo_page(number: int) -> str:
    return f"{WOO_V1}&page={number}"


def _shopify_page(number: int) -> str:
    return f"{SHOPIFY}&page={number}"


# Shopify no emite header de total de paginas, asi que su feed se termina
# programando la pagina siguiente vacia. Nadie la pide hasta la Tarea 12.
SHOPIFY_ONE_PAGE = {SHOPIFY: (200, SHOPIFY_PAGE, {}), _shopify_page(2): (200, SHOPIFY_EMPTY, {})}


@pytest.fixture(autouse=True)
def _no_real_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    def _blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("a catalog test tried to open a real socket")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked)


def _route_of(request: httpx.Request) -> str:
    query = request.url.query.decode()
    return request.url.path + (f"?{query}" if query else "")


@dataclass
class Fake:
    """Fetcher falso programable por ruta: (status, cuerpo, headers).

    ``status=0`` simula un fallo de transporte (el round-trip nunca se completa).
    Una ruta no programada devuelve 404. Registra las URLs pedidas y las esperas.
    """

    responses: dict[str, tuple[int, str, dict[str, str]]] = field(default_factory=dict)
    seen: list[str] = field(default_factory=list)
    waits: list[float] = field(default_factory=list)

    def client(self) -> httpx.Client:
        def handler(request: httpx.Request) -> httpx.Response:
            route = _route_of(request)
            self.seen.append(route)
            status, body, headers = self.responses.get(route, (404, "not found", {}))
            if status == STATUS_SIN_RESPUESTA:
                raise httpx.ConnectError("simulated transport failure", request=request)
            return httpx.Response(status, text=body, headers=headers)

        return httpx.Client(transport=httpx.MockTransport(handler))

    def sleeper(self, seconds: float) -> None:
        self.waits.append(seconds)

    def read(self, **kwargs: object) -> tuple[tuple[object, ...], ReadReport]:
        return read_catalog(BASE, client=self.client(), sleeper=self.sleeper, now=READ_AT, **kwargs)  # type: ignore[arg-type]


def test_the_four_probe_routes_are_the_exact_strings_of_the_spec_in_order() -> None:
    assert PROBE_ROUTES == (
        ("/wp-json/wc/store/v1/products?per_page=100", "woo"),
        ("/wp-json/wc/store/products?per_page=100", "woo"),
        ("/index.php/wp-json/wc/store/products?per_page=100", "woo"),
        ("/products.json?limit=250", "shopify"),
    )


def test_probing_walks_the_routes_in_order_one_to_four() -> None:
    """Lo que este test cuida es el ORDEN del sondeo. La cardinalidad total la
    cuida test_four_404s_trigger_exactly_one_home_read (Tarea 11), que agrega una
    quinta lectura -- la de la home -- en este mismo escenario."""
    fake = Fake()
    fake.read()
    assert fake.seen[:4] == [WOO_V1, WOO_STORE, WOO_FLAT, SHOPIFY]


def test_every_attempt_is_recorded_successful_or_not() -> None:
    fake = Fake(responses={WOO_STORE: (403, "blocked", {}), **SHOPIFY_ONE_PAGE})
    _, report = fake.read()
    assert report.routes_probed == (
        (WOO_V1, 404),
        (WOO_STORE, 403),
        (WOO_FLAT, 404),
        (SHOPIFY, 200),
    )
    assert report.route_used == SHOPIFY
    assert report.platform == "shopify"
    assert report.outcome == "ok"


def test_probing_does_not_stop_at_the_first_404() -> None:
    fake = Fake(responses={WOO_FLAT: (200, WOO_PAGE, ONE_PAGE)})
    rows, report = fake.read()
    assert report.route_used == WOO_FLAT
    assert report.platform == "woo"
    assert report.outcome == "ok"
    assert report.routes_probed == ((WOO_V1, 404), (WOO_STORE, 404), (WOO_FLAT, 200))
    assert len(rows) == 1


def test_a_200_with_an_unexpected_shape_is_not_a_match_and_probing_continues() -> None:
    fake = Fake(
        responses={
            WOO_V1: (200, json.dumps({"foo": [{"nombre": "x", "precio": "1"}]}), {}),
            WOO_STORE: (200, WOO_PAGE, ONE_PAGE),
        }
    )
    rows, report = fake.read()
    assert report.route_used == WOO_STORE
    assert (WOO_V1, 200) in report.routes_probed
    assert len(rows) == 1
    assert rows[0].title.value == "Producto"


def test_a_200_with_invalid_json_is_not_a_match_either() -> None:
    fake = Fake(responses={WOO_V1: (200, "<html>no json</html>", {}), **SHOPIFY_ONE_PAGE})
    _, report = fake.read()
    assert report.route_used == SHOPIFY


def test_one_rate_limit_wait_before_every_request_except_the_first() -> None:
    """Relacional a proposito: la cantidad de pedidos cambia cuando entra el
    paginado (Tarea 12), pero la regla -- una espera antes de cada pedido menos el
    primero -- no."""
    fake = Fake(responses=dict(SHOPIFY_ONE_PAGE))
    fake.read(rate_limit_seconds=0.25)
    assert len(fake.seen) >= 4
    assert len(fake.waits) == len(fake.seen) - 1
    assert set(fake.waits) == {0.25}


def test_the_first_matching_route_costs_no_wait_at_all() -> None:
    fake = Fake(responses={WOO_V1: (200, WOO_PAGE, ONE_PAGE)})
    fake.read()
    assert fake.seen == [WOO_V1]
    assert fake.waits == []


def test_read_catalog_always_returns_rows_and_a_report() -> None:
    for responses in ({}, {WOO_V1: (200, WOO_PAGE, ONE_PAGE)}, {WOO_V1: (200, WOO_EMPTY, {})}):
        result = Fake(responses=responses).read()
        assert isinstance(result, tuple) and len(result) == 2
        rows, report = result
        assert isinstance(rows, tuple)
        assert isinstance(report, ReadReport)


def test_ok_with_zero_rows_only_happens_when_a_valid_feed_answered_an_empty_array() -> None:
    rows, report = Fake(responses={WOO_V1: (200, WOO_EMPTY, {})}).read()
    assert rows == ()
    assert report.outcome == "ok"
    assert report.rows_emitted == 0
    assert report.pages_read == 1
    assert report.route_used == WOO_V1


def test_read_catalog_raises_only_on_a_programming_error() -> None:
    fake = Fake()
    with pytest.raises(TypeError):
        read_catalog(123, client=fake.client(), sleeper=fake.sleeper)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        read_catalog("tienda-sin-esquema", client=fake.client(), sleeper=fake.sleeper)


def test_a_redirect_is_followed_and_the_rows_carry_the_url_that_answered() -> None:
    """C15. La politica de redirects es del lector, no del cliente inyectado: si
    no la fija, la suite entera ejercita el camino sin redirects y las corridas
    reales el camino con redirects. Y las filas se atribuyen al host que
    RESPONDIO, no al que se pidio: su procedencia es el lector mismo, asi que
    tiene que decir la verdad de la lectura."""
    destino = "https://www.tienda.test" + WOO_V1

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == BASE + WOO_V1:
            return httpx.Response(301, headers={"Location": destino})
        if str(request.url) == destino:
            return httpx.Response(200, text=WOO_PAGE, headers=ONE_PAGE)
        return httpx.Response(404, text="not found")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    rows, report = read_catalog(BASE, client=client, sleeper=lambda seconds: None, now=READ_AT)
    assert report.outcome == "ok"
    assert report.route_used == WOO_V1
    assert len(rows) == 1
    assert rows[0].site == "www.tienda.test"
    assert rows[0].url == destino
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
PYTHONPATH=. C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m pytest tests/test_catalog_source.py -q
```

Salida esperada: error de coleccion, `ModuleNotFoundError: No module named 'src.catalog.source'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/catalog/source.py
"""Deteccion de plataforma, sondeo de rutas y paginado. LO UNICO que toca la red.

El cliente HTTP y la espera de rate limit entran INYECTADOS y son obligatorios:
cada call site es explicito sobre timeout, headers y transporte, y los tests
corren enteros sin red con ``httpx.MockTransport``. Por eso este modulo no
importa ``time``.

``read_catalog`` no levanta excepciones por problemas de red ni de forma:
devuelve ``(filas, ReadReport)`` con el desenlace tipado. Solo levanta por error
de programacion (una URL que no es string).

Este modulo no aplica ningun gate legal ni de techo de adquisicion: eso es del
llamador, y el paquete no importa nada del paquete de precios del repo. Por eso
el User-Agent y el patron de fetch se escriben aca en vez de reusarse: la
duplicacion es el precio de que este paquete quede independiente. Hay guardas de
test que verifican las dos cosas (``tests/test_catalog_boundaries.py``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

import httpx

from .adapters import ADAPTERS
from .record import DROP_REASONS, UNDECLARED_REASONS, CatalogRow, ReadReport, tally

# User-Agent identificable y honesto: sin impersonar un navegador, sin rotacion.
# Un operador que mire sus logs puede decir exactamente que le pego y por que.
USER_AGENT = "KernCatalogReader/1.0 (+https://kern.example/catalog-reader)"

DEFAULT_TIMEOUT_SECONDS = 15.0
DEFAULT_RATE_LIMIT_SECONDS = 1.0

# Guarda contra bucle infinito, no limite de catalogo: 100 paginas a 100 filas
# son 10.000 filas. Alcanzarlo se registra como truncamiento en el informe.
MAX_PAGES = 100

# Status con el que se registra un intento cuyo round-trip nunca se completo.
STATUS_SIN_RESPUESTA = 0

# El orden importa y es el del spec. Un 403 no corta el sondeo y un 404 tampoco:
# lo que un WAF hace con una ruta no prueba nada sobre las otras.
PROBE_ROUTES: tuple[tuple[str, str], ...] = (
    ("/wp-json/wc/store/v1/products?per_page=100", "woo"),
    ("/wp-json/wc/store/products?per_page=100", "woo"),
    ("/index.php/wp-json/wc/store/products?per_page=100", "woo"),
    ("/products.json?limit=250", "shopify"),
)


@dataclass(frozen=True)
class _Response:
    status: int
    text: str
    headers: Mapping[str, str]
    # La URL FINAL (despues de redirects). Es la que respondio, y por eso es la
    # que viaja a ``CatalogRow.url`` / ``site``: la procedencia de esos metadatos
    # es el lector, asi que tienen que decir la verdad de la lectura (C15).
    url: str


class _Requester:
    """Un GET por vez, con la espera inyectada y sin reintentos.

    La espera se hace antes de cada pedido MENOS el primero, asi que N pedidos
    cuestan N-1 esperas. Un fallo de transporte devuelve ``None``: es dato para
    el llamador, no una excepcion, y no se vuelve a intentar.

    Los redirects se siguen SIEMPRE, fijado por pedido (C15): asi la suite y las
    corridas reales ejercitan el mismo camino, y una tienda que hace 301 de la
    ruta del feed no se reporta como plataforma desconocida.
    """

    def __init__(
        self,
        client: httpx.Client,
        sleeper: Callable[[float], None],
        rate_limit_seconds: float,
    ) -> None:
        self._client = client
        self._sleeper = sleeper
        self._rate_limit_seconds = rate_limit_seconds
        self._made_a_request = False

    def get(self, url: str) -> _Response | None:
        if self._made_a_request:
            self._sleeper(self._rate_limit_seconds)
        self._made_a_request = True
        try:
            response = self._client.get(
                url,
                timeout=DEFAULT_TIMEOUT_SECONDS,
                headers={"User-Agent": USER_AGENT},
                follow_redirects=True,
            )
        except httpx.HTTPError:
            return None
        return _Response(
            status=response.status_code,
            text=response.text,
            headers=dict(response.headers),
            url=str(response.url),
        )


@dataclass(frozen=True)
class _Probe:
    """El resultado del sondeo: las cuatro rutas con su status, y el feed si
    alguna entrego uno."""

    probed: tuple[tuple[str, int], ...]
    platform: str | None = None
    route: str | None = None
    payload: Any = None
    text: str = ""
    headers: Mapping[str, str] = field(default_factory=dict)
    # URL final de la pagina que entrego el feed (C15). Vacia si ninguna lo hizo.
    final_url: str = ""


def _parse_json(text: str) -> Any:
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def _site_of(url: str) -> str:
    return urlsplit(url).netloc.lower()


def _require_url(url: Any) -> str:
    """Valida en la frontera. Es lo unico que este modulo levanta."""
    if not isinstance(url, str):
        raise TypeError(f"url must be a string, got {type(url).__name__}")
    stripped = url.strip()
    if not stripped or not urlsplit(stripped).netloc:
        raise ValueError(f"url must include a scheme and a host, got {url!r}")
    return stripped.rstrip("/")


def _probe_routes(base: str, requester: _Requester) -> _Probe:
    """Prueba las cuatro rutas en orden y registra cada intento.

    Un 200 con JSON de forma inesperada NO es match: se sigue sondeando y nunca
    se adivina el mapeo de campos.
    """
    probed: list[tuple[str, int]] = []
    for route, platform in PROBE_ROUTES:
        response = requester.get(base + route)
        if response is None:
            probed.append((route, STATUS_SIN_RESPUESTA))
            continue
        probed.append((route, response.status))
        if response.status != 200:
            continue
        payload = _parse_json(response.text)
        if payload is None or not ADAPTERS[platform].recognizes(payload):
            continue
        return _Probe(
            probed=tuple(probed),
            platform=platform,
            route=route,
            payload=payload,
            text=response.text,
            headers=response.headers,
            final_url=response.url,
        )
    return _Probe(probed=tuple(probed))


def read_catalog(
    url: str,
    *,
    client: httpx.Client,
    sleeper: Callable[[float], None],
    rate_limit_seconds: float = DEFAULT_RATE_LIMIT_SECONDS,
    now: datetime | None = None,
) -> tuple[tuple[CatalogRow, ...], ReadReport]:
    """Lee el catalogo publico de una tienda y declara su cobertura."""
    base = _require_url(url)
    read_at = now or datetime.now(timezone.utc)
    requester = _Requester(client, sleeper, rate_limit_seconds)

    probe = _probe_routes(base, requester)
    if probe.payload is None or probe.platform is None or probe.route is None:
        return (), ReadReport(outcome="plataforma_desconocida", routes_probed=probe.probed)

    adapter = ADAPTERS[probe.platform]
    # url y site salen de la respuesta que ENTREGO el feed, no del base pedido:
    # con redirects seguidos, atribuir las filas al host pedido seria falso (C15).
    page_url = probe.final_url or base + probe.route
    result = adapter.to_rows(
        probe.payload, url=page_url, site=_site_of(page_url), read_at=read_at
    )
    return result.rows, ReadReport(
        outcome="ok",
        platform=probe.platform,
        route_used=probe.route,
        pages_read=1,
        rows_emitted=len(result.rows),
        routes_probed=probe.probed,
        rows_dropped=tally(result.dropped, DROP_REASONS),
        undeclared_reasons=tally(result.undeclared, UNDECLARED_REASONS),
    )
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
PYTHONPATH=. C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m pytest tests/test_catalog_source.py -q
C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m ruff check src tests examples
```

Salida esperada: **cero fallos** y `All checks passed!`.

- [ ] **Step 5: Commit**

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
git add src/catalog/source.py tests/test_catalog_source.py
git commit -m "feat(catalog): sondeo de las cuatro rutas con cliente y espera inyectados

Las cuatro rutas del spec en orden, cada intento registrado con su status. Un 200
de forma inesperada no es match y el sondeo sigue. Los redirects se siguen por
pedido, asi que la suite y las corridas reales ejercitan el mismo camino, y cada
fila se atribuye al host que respondio. read_catalog devuelve siempre
(filas, ReadReport) y solo levanta por error de programacion."
```

---

### Task 11: Los cuatro desenlaces sin feed, por familia de status

El falso negativo que ya costo sitios reales: un 403 leido como "esta tienda no tiene catalogo". Y el cero-filas silencioso: un Jumpseller que devuelve "lei 0 productos" y hace que el operador concluya que la tienda no vende nada. La leccion se generaliza a la familia entera (C13): el numero 403 no es lo que importa, importa que el sitio nos denego, fallo, o dijo "aca no hay feed".

**Files:**
- Modify: `src/catalog/source.py`
- Test: `tests/test_catalog_source.py`

**Interfaces:**
- Consumes: `_Probe`, `_Requester`, `_probe_routes`, `STATUS_SIN_RESPUESTA` (Tarea 10).
- Produces:
  - `PLATFORM_FINGERPRINTS: tuple[tuple[str, tuple[str, ...]], ...]` -- plataforma -> marcadores nombrados, buscados case-insensitive en el HTML de la home.
  - `DENIAL_STATUSES: frozenset[int] = frozenset({401, 403, 407, 429, 451})` -- la familia de denegacion (C13). El 403 no es un caso especial: es un miembro.
  - `NO_FEED_HERE_STATUSES: frozenset[int] = frozenset({200, 404})` -- los status que significan "aca no hay feed" sin decir nada malo del sitio (C13). Un 200 con JSON de forma inesperada ya fue rechazado por el sondeo.
  - `def _fingerprint_platform(html: str) -> str | None`.
  - `def _report_without_feed(probe: _Probe, base: str, requester: _Requester) -> ReadReport`.

**La cascada es C13 y no se improvisa.** El plan original blindaba el 403 y dejaba pasar toda su familia: cuatro 429, cuatro 500, cuatro 3xx o cualquier mezcla 404+5xx caian en `plataforma_desconocida`, que el operador lee como "esta tienda no tiene catalogo" -- el mismo falso negativo de sitio entero del 403 con otro numero.

- [ ] **Step 1: Write the failing test**

Agregar a `tests/test_catalog_source.py` (imports nuevos: `from pathlib import Path`, `from src.catalog.source import PLATFORM_FINGERPRINTS`):

```python
FIXTURES = Path(__file__).parent / "fixtures" / "catalog"
WAF_BODY = (FIXTURES / "waf_403.txt").read_text(encoding="utf-8")
HOME_JUMPSELLER = (FIXTURES / "home_jumpseller.html").read_text(encoding="utf-8")
HOME_GENERIC = (FIXTURES / "home_generic.html").read_text(encoding="utf-8")


def test_a_403_on_any_route_yields_bloqueado_waf_never_sin_feed_json() -> None:
    fake = Fake(responses={WOO_STORE: (403, WAF_BODY, {})})
    rows, report = fake.read()
    assert report.outcome == "bloqueado_waf"
    assert report.outcome != "sin_feed_json"
    assert rows == ()
    assert report.route_used is None
    assert report.platform is None
    assert (WOO_STORE, 403) in report.routes_probed
    assert len(report.routes_probed) == 4


def test_a_403_does_not_cut_the_probe_when_a_later_route_delivers() -> None:
    fake = Fake(responses={WOO_V1: (403, WAF_BODY, {}), **SHOPIFY_ONE_PAGE})
    _, report = fake.read()
    assert report.outcome == "ok"
    assert report.route_used == SHOPIFY
    assert (WOO_V1, 403) in report.routes_probed


@pytest.mark.parametrize("status", [401, 403, 407, 429, 451])
def test_every_denial_status_is_bloqueado_waf_never_plataforma_desconocida(status: int) -> None:
    """C13: el 403 no es un caso especial, es un miembro de una familia. Un 429 en
    las cuatro rutas leido como "no reconoci la plataforma" es el mismo falso
    negativo de sitio entero que el 403, con otro numero."""
    fake = Fake(responses={route: (status, "denegado", {}) for route, _ in PROBE_ROUTES})
    _, report = fake.read()
    assert report.outcome == "bloqueado_waf"
    assert report.outcome != "plataforma_desconocida"
    assert "/" not in fake.seen


@pytest.mark.parametrize("status", [301, 302, 500, 502, 503])
def test_a_status_that_is_neither_200_nor_404_on_every_route_is_error_red(status: int) -> None:
    """No saber no es un veredicto sobre la plataforma."""
    fake = Fake(responses={route: (status, "boom", {}) for route, _ in PROBE_ROUTES})
    _, report = fake.read()
    assert report.outcome == "error_red"
    assert report.platform is None
    assert "/" not in fake.seen


def test_a_soft_404_does_not_disable_the_platform_fingerprint() -> None:
    """Una ruta que devuelve 200 con HTML de soft-404 significa "aca no hay feed",
    igual que un 404: no puede impedir la unica lectura de huella. Sin esto,
    sin_feed_json es practicamente inalcanzable en campo."""
    fake = Fake(
        responses={
            WOO_V1: (200, "<html>pagina no encontrada</html>", {}),
            "/": (200, HOME_JUMPSELLER, {}),
        }
    )
    _, report = fake.read()
    assert report.outcome == "sin_feed_json"
    assert report.platform == "jumpseller"
    assert (WOO_V1, 200) in report.routes_probed


def test_a_home_that_cannot_be_read_is_error_red_not_a_platform_verdict() -> None:
    """"No pude leer la home" no es "plataforma desconocida"."""
    fake = Fake(responses={"/": (500, "boom", {})})
    _, report = fake.read()
    assert report.outcome == "error_red"
    assert report.platform is None
    assert ("/", 500) in report.routes_probed


def test_a_home_denied_after_four_404s_is_bloqueado_waf() -> None:
    fake = Fake(responses={"/": (403, WAF_BODY, {})})
    _, report = fake.read()
    assert report.outcome == "bloqueado_waf"
    assert ("/", 403) in report.routes_probed


def test_the_home_read_is_declared_in_routes_probed() -> None:
    """La unica lectura de HTML del componente no puede ser invisible."""
    fake = Fake(responses={"/": (200, HOME_GENERIC, {})})
    _, report = fake.read()
    assert ("/", 200) in report.routes_probed
    assert len(report.routes_probed) == 5


def test_a_403_verdict_never_depends_on_the_response_body() -> None:
    for body in (WAF_BODY, "", "<html>whatever</html>"):
        _, report = Fake(responses={WOO_V1: (403, body, {})}).read()
        assert report.outcome == "bloqueado_waf"


def test_four_404s_trigger_exactly_one_home_read() -> None:
    fake = Fake(responses={"/": (200, HOME_GENERIC, {})})
    fake.read()
    assert len(fake.seen) == 5
    assert fake.seen[:4] == [WOO_V1, WOO_STORE, WOO_FLAT, SHOPIFY]
    assert fake.seen[4] == "/"
    assert fake.seen.count("/") == 1


def test_a_recognized_fingerprint_yields_sin_feed_json_with_the_platform_named() -> None:
    fake = Fake(responses={"/": (200, HOME_JUMPSELLER, {})})
    rows, report = fake.read()
    assert report.outcome == "sin_feed_json"
    assert report.platform == "jumpseller"
    assert rows == ()
    assert report.route_used is None
    assert report.rows_emitted == 0


def test_no_recognizable_fingerprint_yields_plataforma_desconocida() -> None:
    fake = Fake(responses={"/": (200, HOME_GENERIC, {})})
    _, report = fake.read()
    assert report.outcome == "plataforma_desconocida"
    assert report.platform is None


def test_the_home_is_never_read_when_a_feed_answered() -> None:
    fake = Fake(responses={WOO_V1: (200, WOO_PAGE, ONE_PAGE), "/": (200, HOME_JUMPSELLER, {})})
    fake.read()
    assert "/" not in fake.seen
    assert fake.seen == [WOO_V1]


def test_the_home_is_not_read_when_a_403_already_decided_the_outcome() -> None:
    fake = Fake(responses={WOO_V1: (403, WAF_BODY, {}), "/": (200, HOME_JUMPSELLER, {})})
    _, report = fake.read()
    assert report.outcome == "bloqueado_waf"
    assert "/" not in fake.seen


def test_a_transport_failure_on_every_route_yields_error_red() -> None:
    fake = Fake(responses={route: (STATUS_SIN_RESPUESTA, "", {}) for route, _ in PROBE_ROUTES})
    rows, report = fake.read()
    assert report.outcome == "error_red"
    assert rows == ()
    assert report.route_used is None
    assert report.routes_probed == tuple((route, STATUS_SIN_RESPUESTA) for route, _ in PROBE_ROUTES)
    assert "/" not in fake.seen


def test_a_transport_failure_does_not_cut_the_probe_either() -> None:
    fake = Fake(responses={WOO_V1: (STATUS_SIN_RESPUESTA, "", {}), WOO_FLAT: (200, WOO_PAGE, ONE_PAGE)})
    _, report = fake.read()
    assert report.outcome == "ok"
    assert report.route_used == WOO_FLAT


def test_a_report_is_emitted_on_all_five_no_read_paths() -> None:
    escenarios = (
        ({"/": (200, HOME_GENERIC, {})}, "plataforma_desconocida"),
        ({"/": (200, HOME_JUMPSELLER, {})}, "sin_feed_json"),
        ({WOO_V1: (403, WAF_BODY, {})}, "bloqueado_waf"),
        ({route: (STATUS_SIN_RESPUESTA, "", {}) for route, _ in PROBE_ROUTES}, "error_red"),
        # Cuatro 404 y una home que tampoco se pudo leer: no hay veredicto de
        # plataforma que dar, y decir "desconocida" seria afirmar de mas.
        ({}, "error_red"),
    )
    for responses, expected in escenarios:
        rows, report = Fake(responses=responses).read()
        assert isinstance(report, ReadReport)
        assert report.outcome == expected
        assert report.route_used is None
        assert rows == ()


def test_a_platform_without_a_json_feed_never_ends_up_as_ok() -> None:
    for responses in ({}, {"/": (200, HOME_JUMPSELLER, {})}, {"/": (200, HOME_GENERIC, {})}, {WOO_V1: (403, WAF_BODY, {})}):
        _, report = Fake(responses=responses).read()
        assert report.outcome != "ok"


def test_the_fingerprint_table_names_the_uncovered_platforms_explicitly() -> None:
    assert [platform for platform, _ in PLATFORM_FINGERPRINTS] == ["jumpseller", "bsale"]
    assert all(markers for _, markers in PLATFORM_FINGERPRINTS)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
PYTHONPATH=. C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m pytest tests/test_catalog_source.py -q
```

Salida esperada: error de coleccion, `ImportError: cannot import name 'PLATFORM_FINGERPRINTS' from 'src.catalog.source'`.

- [ ] **Step 3: Write minimal implementation**

En `src/catalog/source.py`: agregar la tabla de huellas despues de `PROBE_ROUTES`:

```python
# Plataformas reconocidas y NO cubiertas por v1. Marcadores nombrados, buscados
# case-insensitive en el HTML de la home. Reconocer la plataforma es lo que
# separa "no encontre nada" de "reconoci Jumpseller y no publica JSON".
PLATFORM_FINGERPRINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("jumpseller", ("cdn.jumpseller.com", "assets.jumpseller.com", "jumpseller")),
    ("bsale", ("cdn.bsale.io", "bsale.io", "bsale")),
)

# Familias de status, explicitas (C13). El 403 no es un caso especial: es un
# miembro de la familia de denegacion. Y "aca no hay feed" no es lo mismo que
# "algo anda mal": un 404 y un 200 de forma inesperada dicen lo primero, y solo
# esos dos habilitan la lectura de huella.
DENIAL_STATUSES: frozenset[int] = frozenset({401, 403, 407, 429, 451})
NO_FEED_HERE_STATUSES: frozenset[int] = frozenset({200, 404})
```

Y agregar antes de `read_catalog`:

```python
def _fingerprint_platform(html: str) -> str | None:
    """La plataforma cuya huella aparece en el HTML de la home, o None.

    Marcadores nombrados: esto reconoce una plataforma, no adivina un mapeo de
    campos. Es la unica lectura de HTML que hace el componente, y solo despues de
    que las cuatro rutas dieron 404.
    """
    lowered = html.lower()
    for platform, markers in PLATFORM_FINGERPRINTS:
        if any(marker.lower() in lowered for marker in markers):
            return platform
    return None


def _report_without_feed(probe: _Probe, base: str, requester: _Requester) -> ReadReport:
    """El desenlace cuando ninguna ruta entrego feed, por familia de status (C13).

    Precedencia, y cada rama tiene su razon:

    1. **Denegacion** (401/403/407/429/451): ``bloqueado_waf``, JAMAS
       ``sin_feed_json``. Un WAF que bloquea no dice nada sobre el catalogo, y ese
       falso negativo ya costo sitios reales.
    2. **Cualquier status que no sea 200 ni 404** (sin respuesta, 3xx, 5xx):
       ``error_red``. No saber no es un veredicto sobre la plataforma.
    3. **Todo en {404, 200}**: una sola lectura de la home para separar
       ``sin_feed_json`` (reconoci Jumpseller o Bsale) de
       ``plataforma_desconocida``. Un 200 con JSON de forma inesperada ya fue
       rechazado por el sondeo y significa "aca no hay feed", igual que un 404: no
       puede desactivar la huella, o ``sin_feed_json`` seria inalcanzable en campo.
    4. **La home tampoco se pudo leer**: ``error_red`` (o ``bloqueado_waf`` si su
       status es de denegacion). "No pude leer la home" no es "plataforma
       desconocida".

    El intento contra la home se registra en ``routes_probed`` como
    ``("/", status)``: la unica lectura de HTML del componente queda declarada.
    """
    statuses = {status for _, status in probe.probed}
    if statuses & DENIAL_STATUSES:
        return ReadReport(outcome="bloqueado_waf", routes_probed=probe.probed)
    if statuses - NO_FEED_HERE_STATUSES:
        return ReadReport(outcome="error_red", routes_probed=probe.probed)

    home = requester.get(base + "/")
    home_status = STATUS_SIN_RESPUESTA if home is None else home.status
    probed = probe.probed + (("/", home_status),)
    if home_status in DENIAL_STATUSES:
        return ReadReport(outcome="bloqueado_waf", routes_probed=probed)
    if home is None or home_status != 200:
        return ReadReport(outcome="error_red", routes_probed=probed)
    platform = _fingerprint_platform(home.text)
    if platform is not None:
        return ReadReport(outcome="sin_feed_json", platform=platform, routes_probed=probed)
    return ReadReport(outcome="plataforma_desconocida", routes_probed=probed)
```

Y cambiar en `read_catalog` la rama sin feed:

```python
    if probe.payload is None or probe.platform is None or probe.route is None:
        return (), _report_without_feed(probe, base, requester)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
PYTHONPATH=. C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m pytest tests/test_catalog_source.py -q
C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m ruff check src tests examples
```

Salida esperada: **cero fallos** y `All checks passed!`. Y `test_probing_walks_the_routes_in_order_one_to_four` (Tarea 10) sigue verde porque assertea el prefijo: la cardinalidad total la cuida `test_four_404s_trigger_exactly_one_home_read`.

- [ ] **Step 5: Commit**

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
git add src/catalog/source.py tests/test_catalog_source.py
git commit -m "feat(catalog): desenlaces sin feed por familia de status

La familia de denegacion entera (401/403/407/429/451) da bloqueado_waf, jamas
sin_feed_json, y no corta el sondeo. Cualquier status que no sea 200 ni 404 da
error_red: no saber no es un veredicto sobre la plataforma. Con todo en {404,200}
se lee la home una sola vez para separar una plataforma reconocida sin feed JSON
de una desconocida, y ese intento queda declarado en routes_probed. Una home
ilegible es error_red, no plataforma desconocida. Cero filas nunca se reporta como
exito."
```

---

### Task 12: Paginado Woo -- header `X-WP-TotalPages`, corte por arreglo vacio y tope duro

**Files:**
- Modify: `src/catalog/source.py`
- Test: `tests/test_catalog_source.py`

**Interfaces:**
- Consumes: `_Probe`, `_Requester`, `MAX_PAGES`, `adapter.TOTAL_PAGES_HEADER`, `adapter.next_page_url`, `adapter.recognizes`, `adapter.rows_of` (Tareas 6, 9, 10); `TRUNCATION_*` (Tarea 5).
- Produces:
  - `@dataclass(frozen=True) class _PageBatch` con `pages: tuple[tuple[str, Any], ...]` (pares URL-de-pagina / payload), `pages_read: int`, `truncations: tuple[str, ...] = ()`.
  - `def _total_pages(headers: Mapping[str, str], header_name: str | None) -> int | None`.
  - `def _declared_rows(headers: Mapping[str, str], header_name: str | None) -> int | None` -- el total de FILAS que declara la plataforma (C16).
  - `def _read_pages(adapter: ModuleType, base: str, probe: _Probe, requester: _Requester) -> _PageBatch` -- un solo bucle generico para las dos plataformas.
  - `read_catalog` ahora acumula filas y motivos sobre todas las paginas, expone `truncations`, `rows_declared` y `attribute_states`, y degrada el `outcome` ante **cualquier** truncamiento (C2).
  - `ROW_TOTAL_HEADER` entra al contrato de adaptador: `"X-WP-Total"` en `woo.py`, `None` en `shopify.py` (C16). Agregarlo tambien a la lista del contrato en el docstring de `adapters/__init__.py`.

Convencion de `pages_read` (declarada, C3): numero de paginas de feed cuyas filas se emitieron -- incluida la pagina 1 y una ultima pagina vacia, excluida una pagina repetida.

**Dos reglas que el bucle tiene que respetar y que son faciles de perder:**
1. **La pagina 1 vacia corta ahi mismo.** El corte por arreglo vacio vale para TODAS las paginas, incluida la primera: si `pages` se siembra con la pagina 1 y el bucle arranca en `page = 2` sin mirarla, una tienda con catalogo publicado en cero se reporta como lectura fallida o truncada (`error_red`) en vez del `ok` con cero filas que el spec pide, y encima gasta un pedido extra.
2. **Fin de feed y pagina fallida no se confunden, en ninguna de las dos direcciones.** Un 400/404 a mitad de la lectura **es** un corte y se declara: tratarlo como fin de feed normal es esconder cobertura, que es justo lo que este componente existe para no hacer. Por eso los fixtures terminan el feed de forma explicita (pagina vacia o header) en vez de dejar que el 404 del fake haga de terminador.

- [ ] **Step 1: Write the failing test**

Agregar a `tests/test_catalog_source.py` (los helpers `_woo_page` / `_shopify_page` y `ONE_PAGE` ya existen desde la Tarea 10: **no** se redefinen):

```python
def _woo_rows_page(count: int, first_id: int) -> str:
    return json.dumps(
        [
            {
                "id": first_id + offset,
                "name": f"Producto {first_id + offset}",
                "prices": {"price": "1990", "currency_minor_unit": 2, "currency_code": "CLP"},
            }
            for offset in range(count)
        ]
    )


def test_woo_paginates_by_the_total_pages_header() -> None:
    fake = Fake(
        responses={
            WOO_V1: (200, _woo_rows_page(2, 1), {"X-WP-TotalPages": "3"}),
            _woo_page(2): (200, _woo_rows_page(2, 3), {}),
            _woo_page(3): (200, _woo_rows_page(1, 5), {}),
        }
    )
    rows, report = fake.read()
    assert fake.seen == [WOO_V1, _woo_page(2), _woo_page(3)]
    assert report.pages_read == 3
    assert report.rows_emitted == 5
    assert len(rows) == 5
    assert report.truncations == ()
    assert report.outcome == "ok"


def test_each_row_carries_the_url_of_the_feed_page_it_came_from() -> None:
    fake = Fake(
        responses={
            WOO_V1: (200, _woo_rows_page(1, 1), {"X-WP-TotalPages": "2"}),
            _woo_page(2): (200, _woo_rows_page(1, 2), {}),
        }
    )
    rows, _ = fake.read()
    assert rows[0].url == BASE + WOO_V1
    assert rows[1].url == BASE + _woo_page(2)
    assert rows[0].site == "tienda.test"


def test_woo_without_the_header_paginates_until_an_empty_array() -> None:
    fake = Fake(
        responses={
            WOO_V1: (200, _woo_rows_page(2, 1), {}),
            _woo_page(2): (200, _woo_rows_page(2, 3), {}),
            _woo_page(3): (200, WOO_EMPTY, {}),
        }
    )
    rows, report = fake.read()
    assert fake.seen == [WOO_V1, _woo_page(2), _woo_page(3)]
    # pages_read cuenta la pagina vacia final: es una pagina leida con exito.
    assert report.pages_read == 3
    assert report.rows_emitted == 4
    assert len(rows) == 4
    assert report.truncations == ()


def test_woo_without_the_header_stops_at_the_hundred_page_cap() -> None:
    responses = {WOO_V1: (200, _woo_rows_page(1, 1), {})}
    responses.update({_woo_page(n): (200, _woo_rows_page(1, n), {}) for n in range(2, 205)})
    fake = Fake(responses=responses)
    rows, report = fake.read()
    assert len(fake.seen) == 100
    assert report.pages_read == 100
    assert report.rows_emitted == 100
    assert len(rows) == 100
    assert "TOPE_PAGINAS_ALCANZADO" in report.truncations
    assert fake.seen[-1] == _woo_page(100)
    assert _woo_page(101) not in fake.seen


def test_a_header_beyond_the_cap_is_honoured_only_up_to_the_cap_and_declared() -> None:
    responses = {WOO_V1: (200, _woo_rows_page(1, 1), {"X-WP-TotalPages": "150"})}
    responses.update({_woo_page(n): (200, _woo_rows_page(1, n), {}) for n in range(2, 205)})
    fake = Fake(responses=responses)
    _, report = fake.read()
    assert len(fake.seen) == 100
    assert report.pages_read == 100
    assert "TOPE_PAGINAS_ALCANZADO" in report.truncations


def test_a_non_integer_header_falls_back_to_the_empty_array_loop() -> None:
    fake = Fake(
        responses={
            WOO_V1: (200, _woo_rows_page(1, 1), {"X-WP-TotalPages": "muchas"}),
            _woo_page(2): (200, WOO_EMPTY, {}),
        }
    )
    _, report = fake.read()
    assert fake.seen == [WOO_V1, _woo_page(2)]
    assert report.pages_read == 2
    assert report.truncations == ()


def test_an_empty_first_page_reads_exactly_one_page() -> None:
    """El corte por arreglo vacio vale para la pagina 1 igual que para la N. Una
    tienda con catalogo publicado en cero es un `ok` con cero filas, no una
    lectura truncada, y no cuesta un pedido extra."""
    fake = Fake(responses={WOO_V1: (200, WOO_EMPTY, {})})
    rows, report = fake.read()
    assert fake.seen == [WOO_V1]
    assert all("page=" not in seen for seen in fake.seen)
    assert report.pages_read == 1
    assert rows == ()
    assert report.truncations == ()
    assert report.outcome == "ok"


def test_an_empty_first_page_on_shopify_reads_exactly_one_page_too() -> None:
    """Gemelo Shopify: sin header de total de paginas, la pagina 1 vacia es el
    unico corte posible. Si el lector pidiera la pagina 2, el endpoint devolveria
    el mismo cuerpo vacio y saldria PAGINA_REPETIDA sobre una lectura completa."""
    fake = Fake(responses={SHOPIFY: (200, SHOPIFY_EMPTY, {})})
    rows, report = fake.read()
    assert fake.seen[4:] == []
    assert report.pages_read == 1
    assert rows == ()
    assert report.truncations == ()
    assert report.outcome == "ok"


def test_any_truncation_degrades_the_outcome_never_stays_ok() -> None:
    """C2: los cuatro truncamientos significan lo mismo -- hay catalogo que no se
    leyo y no se sabe cuanto. Un consumidor que ramifica por outcome no puede leer
    una lectura truncada como completa."""
    responses = {WOO_V1: (200, _woo_rows_page(1, 1), {})}
    responses.update({_woo_page(n): (200, _woo_rows_page(1, n), {}) for n in range(2, 205)})
    _, report = Fake(responses=responses).read()
    assert report.truncations != ()
    assert report.outcome == "error_red"
    # Y la ruta que si entrego feed se conserva: es informacion cierta (C2).
    assert report.route_used == WOO_V1
    assert report.platform == "woo"


def test_a_declared_row_total_that_does_not_match_is_declared_as_a_truncation() -> None:
    """C16: el feed dice cuantas filas tiene. Recibir menos y no decirlo es
    cobertura escondida -- el invariante de conteos mide consistencia interna y se
    satisface perfecto con la mitad del catalogo."""
    fake = Fake(
        responses={WOO_V1: (200, _woo_rows_page(2, 1), {"X-WP-TotalPages": "1", "X-WP-Total": "250"})}
    )
    _, report = fake.read()
    assert report.rows_declared == 250
    assert report.rows_emitted == 2
    assert "TOTAL_DECLARADO_NO_COINCIDE" in report.truncations
    assert report.outcome == "error_red"


def test_a_declared_row_total_that_matches_is_not_a_truncation() -> None:
    fake = Fake(
        responses={WOO_V1: (200, _woo_rows_page(2, 1), {"X-WP-TotalPages": "1", "X-WP-Total": "2"})}
    )
    _, report = fake.read()
    assert report.rows_declared == 2
    assert report.truncations == ()
    assert report.outcome == "ok"


def test_dropped_rows_count_toward_the_declared_total() -> None:
    """El total declarado son filas CRUDAS: una fila descartada se recibio."""
    page = json.dumps(["no soy un objeto", {"id": 1, "name": "P", "prices": {"price": "1990", "currency_minor_unit": 2, "currency_code": "CLP"}}])
    fake = Fake(responses={WOO_V1: (200, page, {"X-WP-TotalPages": "1", "X-WP-Total": "2"})})
    _, report = fake.read()
    assert report.rows_emitted == 1
    assert report.rows_dropped == (("FILA_NO_DICT", 1),)
    assert report.truncations == ()


def test_the_report_declares_coverage_for_all_seven_attributes() -> None:
    """C17: una lectura donde ninguna fila trae sku no puede salir con el informe
    callado sobre sku."""
    fake = Fake(responses={WOO_V1: (200, _woo_rows_page(2, 1), ONE_PAGE)})
    _, report = fake.read()
    states = dict(report.attribute_states)
    assert states["sku"] == (0, 2, 0)
    assert states["title"] == (2, 0, 0)
    assert set(states) >= {"title", "price", "currency", "sku", "gtin", "weight_declared", "stock_status"}


def test_dropped_and_undeclared_reasons_are_summed_across_pages() -> None:
    page_one = json.dumps(
        [
            "no soy un objeto",
            {"id": 1, "name": "P1", "prices": {"price": "1990", "currency_code": "CLP"}},
        ]
    )
    page_two = json.dumps(
        [
            {"id": 2, "prices": {"price": "1990", "currency_minor_unit": 2, "currency_code": "CLP"}},
            {"id": 3, "name": "P3", "prices": {"price": "1990", "currency_code": "CLP"}},
        ]
    )
    fake = Fake(
        responses={
            WOO_V1: (200, page_one, {"X-WP-TotalPages": "2"}),
            _woo_page(2): (200, page_two, {}),
        }
    )
    _, report = fake.read()
    assert report.rows_dropped == (("FILA_NO_DICT", 1), ("SIN_TITULO", 1))
    assert ("MINOR_UNIT_AUSENTE", 2) in report.undeclared_reasons
    assert report.rows_emitted == 2
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
PYTHONPATH=. C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m pytest tests/test_catalog_source.py -q
```

Salida esperada: **rojo en los tests nuevos de esta tarea y en ninguno de las Tareas 10-11**, con `assert 1 == 3` en `test_woo_paginates_by_the_total_pages_header` (hoy `read_catalog` lee solo la pagina 1). Que los tests de las tareas anteriores sigan verdes es una condicion del paso: los fixtures de la Tarea 10 ya terminan su feed de forma explicita (`ONE_PAGE` en Woo, pagina 2 vacia en Shopify) precisamente para que este paso no obligue a ir a editarlos -- y editar el test de la tarea vecina para que pase es lo que TDD prohibe. Si igual aparece un rojo en las Tareas 10-11, es que a ese fixture le falta el terminador: se agrega el terminador, no se afloja la asercion.

Excepcion declarada: `test_an_empty_first_page_reads_exactly_one_page` y `test_ok_with_zero_rows_only_happens_when_a_valid_feed_answered_an_empty_array` (Tarea 10) tienen que quedar **verdes** al final del paso 3 y son la razon de la guarda de pagina 1 vacia. Si el paso 3 los pone rojos, el codigo esta mal, no el test.

- [ ] **Step 3: Write minimal implementation**

Primero el contrato de adaptador (C16): agregar `ROW_TOTAL_HEADER: str | None = "X-WP-Total"` a `src/catalog/adapters/woo.py` junto a `TOTAL_PAGES_HEADER`, `ROW_TOTAL_HEADER: str | None = None` a `shopify.py`, y la linea correspondiente a la lista del contrato en el docstring de `adapters/__init__.py`.

En `src/catalog/source.py`: agregar `from types import ModuleType` al bloque stdlib, agregar `ReadOutcome`, `TRUNCATION_PAGINA_FALLIDA`, `TRUNCATION_TOPE_PAGINAS`, `TRUNCATION_TOTAL_NO_COINCIDE`, `attribute_states_of` al import de `.record`, y agregar despues de `_probe_routes`:

```python
@dataclass(frozen=True)
class _PageBatch:
    """Las paginas de feed que se leyeron, con la URL de cada una para que cada
    fila cargue de que pagina salio."""

    pages: tuple[tuple[str, Any], ...]
    pages_read: int
    truncations: tuple[str, ...] = ()


def _total_pages(headers: Mapping[str, str], header_name: str | None) -> int | None:
    """El total de paginas que declara la plataforma, si lo declara y es usable."""
    if header_name is None:
        return None
    for key, value in headers.items():
        if key.lower() != header_name.lower():
            continue
        try:
            total = int(value)
        except (TypeError, ValueError):
            return None
        return total if total > 0 else None
    return None


def _declared_rows(headers: Mapping[str, str], header_name: str | None) -> int | None:
    """Cuantas FILAS declara la plataforma que tiene el feed, si lo declara.

    El feed dice el numero y hasta ahora el lector lo tiraba: sin esta
    reconciliacion, la unica garantia de completitud es "le crei al header de
    paginas" (C16).
    """
    if header_name is None:
        return None
    for key, value in headers.items():
        if key.lower() != header_name.lower():
            continue
        try:
            total = int(value)
        except (TypeError, ValueError):
            return None
        return total if total >= 0 else None
    return None


def _read_pages(adapter: ModuleType, base: str, probe: _Probe, requester: _Requester) -> _PageBatch:
    """Pagina un feed ya encontrado. Un solo bucle para las dos plataformas.

    Woo declara ``X-WP-TotalPages`` y ahi se piden exactamente esas paginas.
    Cuando el header no viene (o no es usable), se pagina hasta recibir un
    arreglo vacio, con el tope duro de ``MAX_PAGES``. Una pagina que falla corta
    la lectura sin reintentar y queda declarada como truncamiento: la lectura
    parcial se declara, no se esconde.
    """
    route = probe.route or ""
    pages: list[tuple[str, Any]] = [(probe.final_url or base + route, probe.payload)]
    truncations: list[str] = []

    # La pagina 1 vacia corta ahi mismo: el corte por arreglo vacio vale para
    # TODAS las paginas. Sin esta guarda, un catalogo publicado en cero se
    # reporta como lectura fallida o truncada y encima cuesta un pedido extra.
    if not adapter.rows_of(probe.payload):
        return _PageBatch(pages=tuple(pages), pages_read=1)

    total = _total_pages(probe.headers, adapter.TOTAL_PAGES_HEADER)
    if total is not None and total > MAX_PAGES:
        truncations.append(TRUNCATION_TOPE_PAGINAS)
    last_page = MAX_PAGES if total is None else min(total, MAX_PAGES)

    page = 2
    while page <= last_page:
        url = adapter.next_page_url(base, route, page)
        response = requester.get(url)
        if response is None or response.status != 200:
            truncations.append(TRUNCATION_PAGINA_FALLIDA)
            break
        payload = _parse_json(response.text)
        if payload is None or not adapter.recognizes(payload):
            truncations.append(TRUNCATION_PAGINA_FALLIDA)
            break
        pages.append((url, payload))
        if not adapter.rows_of(payload):
            break
        page += 1
    else:
        if total is None:
            truncations.append(TRUNCATION_TOPE_PAGINAS)

    return _PageBatch(pages=tuple(pages), pages_read=len(pages), truncations=tuple(truncations))
```

Y reemplazar el final de `read_catalog` (desde `adapter = ADAPTERS[probe.platform]`) por:

```python
    adapter = ADAPTERS[probe.platform]
    batch = _read_pages(adapter, base, probe, requester)

    rows: list[CatalogRow] = []
    dropped: list[str] = []
    undeclared: list[str] = []
    for page_url, payload in batch.pages:
        result = adapter.to_rows(payload, url=page_url, site=_site_of(page_url), read_at=read_at)
        rows.extend(result.rows)
        dropped.extend(result.dropped)
        undeclared.extend(result.undeclared)

    truncations = list(batch.truncations)

    # C16: el feed declaro cuantas filas tiene. Si recibimos otra cantidad, la
    # lectura no esta completa y hay que decirlo. Solo se compara cuando no hubo
    # ningun otro corte, para no reportar dos veces el mismo truncamiento.
    declared_rows = _declared_rows(probe.headers, adapter.ROW_TOTAL_HEADER)
    if declared_rows is not None and not truncations and declared_rows != len(rows) + len(dropped):
        truncations.append(TRUNCATION_TOTAL_NO_COINCIDE)

    # Una lectura cortada no es un exito, y los CUATRO motivos son cortes: el
    # desenlace lo dice y ``truncations`` dice donde se corto (C2).
    outcome: ReadOutcome = "error_red" if truncations else "ok"
    return tuple(rows), ReadReport(
        outcome=outcome,
        platform=probe.platform,
        route_used=probe.route,
        pages_read=batch.pages_read,
        rows_emitted=len(rows),
        rows_declared=declared_rows,
        routes_probed=probe.probed,
        rows_dropped=tally(dropped, DROP_REASONS),
        undeclared_reasons=tally(undeclared, UNDECLARED_REASONS),
        truncations=tuple(truncations),
        attribute_states=attribute_states_of(tuple(rows)),
    )
```

`TRUNCATION_PAGINA_FALLIDA` deja de usarse en `read_catalog` (el `outcome` ya no distingue motivos), pero sigue usandose dentro de `_read_pages`: no se saca del import.

- [ ] **Step 4: Run test to verify it passes**

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
PYTHONPATH=. C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m pytest tests/test_catalog_source.py -q
C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m ruff check src tests examples
```

Salida esperada: **cero fallos** y `All checks passed!`. Ademas de los nuevos, tienen que estar verdes `test_an_empty_first_page_reads_exactly_one_page`, `test_ok_with_zero_rows_only_happens_when_a_valid_feed_answered_an_empty_array`, `test_the_first_matching_route_costs_no_wait_at_all` y `test_the_home_is_never_read_when_a_feed_answered`.

- [ ] **Step 5: Commit**

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
git add src/catalog/source.py src/catalog/adapters/woo.py src/catalog/adapters/shopify.py src/catalog/adapters/__init__.py tests/test_catalog_source.py
git commit -m "feat(catalog): paginado Woo por header, por arreglo vacio y con tope duro

Con X-WP-TotalPages se piden exactamente esas paginas; sin header se pagina hasta
el arreglo vacio con tope de 100 paginas, y alcanzarlo queda declarado como
truncamiento para que nadie lea una lectura truncada como completa. La pagina 1
vacia corta ahi mismo: un catalogo publicado en cero es ok con cero filas, no una
lectura fallida. Cualquier truncamiento degrada el desenlace a error_red. Y
X-WP-Total se reconcilia contra las filas recibidas: recibir menos de lo que el
feed declara ya no pasa en silencio. Cada fila carga la URL de la pagina de la que
salio."
```

---

### Task 13: Paginado Shopify -- corte por arreglo vacio y deteccion de pagina repetida

Shopify no emite header de total de paginas, asi que el corte es por arreglo vacio. Pero `/products.json` de storefront no esta documentado y no hay base para afirmar que respeta `page`: si lo ignora y **repite la primera pagina**, el corte por arreglo vacio nunca dispara y la lectura da bucle o catalogo duplicado. Por eso se detecta la repeticion, se corta y se declara.

**Files:**
- Modify: `src/catalog/source.py`
- Test: `tests/test_catalog_source.py`

**Interfaces:**
- Consumes: `_read_pages`, `_PageBatch`, `_Probe.text` (Tareas 10 y 12); `TRUNCATION_PAGINA_REPETIDA` (Tarea 5).
- Produces: `_read_pages` corta y registra `PAGINA_REPETIDA` cuando el cuerpo crudo de una pagina **ya se leyo antes** (no solo cuando es identico al de la pagina anterior), y descarta sus filas. Se comparan hashes, no cuerpos: `hashlib.sha256(texto.encode())`.

Por que el conjunto y no solo la anterior: comparar contra la pagina inmediatamente anterior cubre el caso realista (el endpoint ignora `page` y devuelve siempre la primera) pero no un endpoint que alterna o cicla (A, B, A, B...). En ese caso el bucle corre hasta `MAX_PAGES` y emite filas duplicadas contadas como filas reales en `rows_emitted`, y lo unico que queda declarado es `TOPE_PAGINAS_ALCANZADO`, que le dice al operador "habia mas catalogo" cuando en realidad habia el mismo cien veces.

- [ ] **Step 1: Write the failing test**

Agregar a `tests/test_catalog_source.py` (`_shopify_page` ya existe desde la Tarea 10):

```python
def _shopify_rows_page(count: int, first_id: int) -> str:
    return json.dumps(
        {
            "products": [
                {
                    "handle": f"p{first_id + offset}",
                    "title": f"Producto {first_id + offset}",
                    "variants": [{"price": "12900"}],
                }
                for offset in range(count)
            ]
        }
    )


def test_shopify_paginates_with_page_and_limit_until_an_empty_array() -> None:
    fake = Fake(
        responses={
            SHOPIFY: (200, _shopify_rows_page(2, 1), {}),
            _shopify_page(2): (200, _shopify_rows_page(2, 3), {}),
            _shopify_page(3): (200, SHOPIFY_EMPTY, {}),
        }
    )
    rows, report = fake.read()
    assert fake.seen[:4] == [WOO_V1, WOO_STORE, WOO_FLAT, SHOPIFY]
    assert fake.seen[4:] == [_shopify_page(2), _shopify_page(3)]
    assert all("limit=250" in seen for seen in fake.seen if seen.startswith("/products.json"))
    assert report.platform == "shopify"
    assert report.pages_read == 3
    assert report.rows_emitted == 4
    assert len(rows) == 4
    assert report.truncations == ()


def test_shopify_repeating_the_first_page_cuts_the_read_and_is_declared() -> None:
    """Un endpoint que ignora ``page`` devuelve la primera pagina otra vez: sin
    esta guarda el corte por arreglo vacio nunca dispara."""
    first = _shopify_rows_page(2, 1)
    responses = {SHOPIFY: (200, first, {})}
    responses.update({_shopify_page(n): (200, first, {}) for n in range(2, 205)})
    fake = Fake(responses=responses)
    rows, report = fake.read()
    assert fake.seen[4:] == [_shopify_page(2)]
    assert report.pages_read == 1
    assert report.rows_emitted == 2
    assert len(rows) == 2
    assert "PAGINA_REPETIDA" in report.truncations
    # Una lectura cortada no es un exito: C2 vale para los cuatro truncamientos.
    assert report.outcome == "error_red"
    assert report.route_used == SHOPIFY
    assert report.platform == "shopify"


def test_pages_that_alternate_are_also_caught_not_just_consecutive_repeats() -> None:
    """Un endpoint que cicla A, B, A, B... no repite la pagina ANTERIOR, asi que
    comparar solo contra ella lo deja correr hasta el tope emitiendo duplicados
    contados como filas reales."""
    page_a = _shopify_rows_page(2, 1)
    page_b = _shopify_rows_page(2, 3)
    responses = {SHOPIFY: (200, page_a, {})}
    responses.update(
        {_shopify_page(n): (200, page_a if n % 2 else page_b, {}) for n in range(2, 205)}
    )
    fake = Fake(responses=responses)
    rows, report = fake.read()
    assert fake.seen[4:] == [_shopify_page(2), _shopify_page(3)]
    assert report.pages_read == 2
    assert report.rows_emitted == 4
    assert len(rows) == 4
    assert "PAGINA_REPETIDA" in report.truncations
    assert "TOPE_PAGINAS_ALCANZADO" not in report.truncations
    assert report.outcome == "error_red"


def test_shopify_also_stops_at_the_hundred_page_cap() -> None:
    responses = {SHOPIFY: (200, _shopify_rows_page(1, 1), {})}
    responses.update({_shopify_page(n): (200, _shopify_rows_page(1, n), {}) for n in range(2, 205)})
    fake = Fake(responses=responses)
    _, report = fake.read()
    assert len([seen for seen in fake.seen if seen.startswith("/products.json")]) == 100
    assert report.pages_read == 100
    assert "TOPE_PAGINAS_ALCANZADO" in report.truncations


def test_a_repeated_page_deeper_in_the_read_keeps_the_earlier_pages() -> None:
    page_two = _shopify_rows_page(2, 3)
    fake = Fake(
        responses={
            SHOPIFY: (200, _shopify_rows_page(2, 1), {}),
            _shopify_page(2): (200, page_two, {}),
            _shopify_page(3): (200, page_two, {}),
        }
    )
    rows, report = fake.read()
    assert report.pages_read == 2
    assert report.rows_emitted == 4
    assert len(rows) == 4
    assert "PAGINA_REPETIDA" in report.truncations
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
PYTHONPATH=. C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m pytest tests/test_catalog_source.py -q
```

Salida esperada: **rojo** en `test_shopify_repeating_the_first_page_cuts_the_read_and_is_declared` (`assert 100 == 1`: hoy pagina 100 veces la misma pagina), `test_a_repeated_page_deeper_in_the_read_keeps_the_earlier_pages` (`assert 100 == 2`) y `test_pages_that_alternate_are_also_caught_not_just_consecutive_repeats`.

- [ ] **Step 3: Write minimal implementation**

En `src/catalog/source.py`: agregar `import hashlib` al bloque stdlib y `TRUNCATION_PAGINA_REPETIDA` al import de `.record`. Y **reemplazar integramente** el cuerpo de `_read_pages` de la Tarea 12 por este -- es la version final de la funcion, y la unica que hay que leer para localizar la linea que la mutacion M7 toca:

```python
def _read_pages(adapter: ModuleType, base: str, probe: _Probe, requester: _Requester) -> _PageBatch:
    """Pagina un feed ya encontrado. Un solo bucle para las dos plataformas.

    Woo declara ``X-WP-TotalPages`` y ahi se piden exactamente esas paginas.
    Cuando el header no viene (o no es usable), se pagina hasta recibir un
    arreglo vacio, con el tope duro de ``MAX_PAGES``. Una pagina que falla corta
    la lectura sin reintentar y queda declarada como truncamiento: la lectura
    parcial se declara, no se esconde.

    Tres cortes, tres significados distintos y todos declarados:

    - **arreglo vacio** -> fin de feed, sin truncamiento (vale tambien para la
      pagina 1: un catalogo publicado en cero es una lectura completa de cero
      filas);
    - **pagina que falla** (sin respuesta, status != 200, JSON invalido o forma
      no reconocida) -> ``LECTURA_PARCIAL_PAGINA_FALLIDA``. Un 404 a mitad de la
      lectura ES un corte: tratarlo como fin de feed normal seria esconder
      cobertura;
    - **cuerpo ya visto** -> ``PAGINA_REPETIDA``, y sus filas se descartan.
    """
    route = probe.route or ""
    pages: list[tuple[str, Any]] = [(probe.final_url or base + route, probe.payload)]
    truncations: list[str] = []

    # La pagina 1 vacia corta ahi mismo (C3): el corte por arreglo vacio vale
    # para TODAS las paginas. Sin esta guarda, un catalogo publicado en cero se
    # reporta como lectura fallida o truncada y encima cuesta un pedido extra.
    if not adapter.rows_of(probe.payload):
        return _PageBatch(pages=tuple(pages), pages_read=1)

    def _digest(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()

    # Contra TODAS las paginas ya leidas, no solo la anterior: un endpoint que
    # alterna A, B, A, B... no repite la anterior y correria hasta el tope
    # emitiendo duplicados contados como filas reales.
    seen_digests = {_digest(probe.text)}

    total = _total_pages(probe.headers, adapter.TOTAL_PAGES_HEADER)
    if total is not None and total > MAX_PAGES:
        truncations.append(TRUNCATION_TOPE_PAGINAS)
    last_page = MAX_PAGES if total is None else min(total, MAX_PAGES)

    page = 2
    while page <= last_page:
        url = adapter.next_page_url(base, route, page)
        response = requester.get(url)
        if response is None or response.status != 200:
            truncations.append(TRUNCATION_PAGINA_FALLIDA)
            break
        payload = _parse_json(response.text)
        if payload is None or not adapter.recognizes(payload):
            truncations.append(TRUNCATION_PAGINA_FALLIDA)
            break
        digest = _digest(response.text)
        if digest in seen_digests:
            # El endpoint ignoro ``page`` y devolvio una pagina ya leida. Sus
            # filas son duplicados: se descartan y la lectura se corta.
            truncations.append(TRUNCATION_PAGINA_REPETIDA)
            break
        seen_digests.add(digest)
        pages.append((response.url or url, payload))
        if not adapter.rows_of(payload):
            break
        page += 1
    else:
        if total is None:
            truncations.append(TRUNCATION_TOPE_PAGINAS)

    return _PageBatch(pages=tuple(pages), pages_read=len(pages), truncations=tuple(truncations))
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
PYTHONPATH=. C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m pytest tests/test_catalog_source.py -q
C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m ruff check src tests examples
```

Salida esperada: **cero fallos** y `All checks passed!`.

- [ ] **Step 5: Commit**

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
git add src/catalog/source.py tests/test_catalog_source.py
git commit -m "feat(catalog): paginado Shopify con corte por vacio y pagina repetida

/products.json de storefront no esta documentado y no hay base para afirmar que
respeta page: si lo ignora y repite una pagina ya leida, el corte por arreglo
vacio nunca dispara. Se detecta comparando el hash del cuerpo contra TODAS las
paginas ya leidas (no solo la anterior, que dejaria pasar un endpoint que
alterna), se descartan los duplicados y se declara PAGINA_REPETIDA. El tope de 100
paginas aplica a las dos plataformas."
```

---

### Task 14: Lectura parcial sin reintentos, cero excepciones e invariante de conteos

**Files:**
- Modify: `src/catalog/source.py`
- Test: `tests/test_catalog_source.py`

**Interfaces:**
- Consumes: `_Requester.get`, `_read_pages`, `read_catalog` (Tareas 10-13).
- Produces: `_Requester.get` tambien absorbe `httpx.InvalidURL` y `UnicodeError`, asi que una URL mal formada que paso la validacion de frontera (puerto invalido, host no normalizado) sale como `error_red` en vez de escapar como excepcion inesperada.

Nota de honestidad sobre TDD en esta tarea: **el unico rojo genuino de arranque es el de la URL mal formada**; los otros seis tests de esta tarea pasan en la primera corrida, porque la garantia se construyo en las Tareas 10-13. Estan aca igual porque son las garantias que el componente promete, y el paso 2 incluye la verificacion de que **ninguno** es un test vacio: una mini-mutacion **por familia de garantia**, se ve el rojo, se revierte. Una sola mutacion generica dejaria tres garantias sin nada que las muerda, que es la misma falla que el paso intenta prevenir un nivel mas arriba.

- [ ] **Step 1: Write the failing test**

Agregar a `tests/test_catalog_source.py`:

```python
def test_a_malformed_port_never_escapes_as_an_unexpected_exception() -> None:
    """Paso la validacion de frontera (tiene esquema y host) pero httpx la
    rechaza con InvalidURL, que no es un HTTPError. Un problema de forma de la
    URL del llamador no puede reventar la lectura."""
    fake = Fake()
    rows, report = read_catalog(
        "https://tienda.test:puerto", client=fake.client(), sleeper=fake.sleeper, now=READ_AT
    )
    assert rows == ()
    assert report.outcome == "error_red"
    assert report.routes_probed == tuple((route, STATUS_SIN_RESPUESTA) for route, _ in PROBE_ROUTES)


def test_a_page_that_fails_mid_read_cuts_it_and_pages_read_says_how_far_it_got() -> None:
    responses = {
        WOO_V1: (200, _woo_rows_page(2, 1), {"X-WP-TotalPages": "5"}),
        _woo_page(2): (200, _woo_rows_page(2, 3), {}),
        _woo_page(3): (STATUS_SIN_RESPUESTA, "", {}),
        _woo_page(4): (200, _woo_rows_page(2, 5), {}),
        _woo_page(5): (200, _woo_rows_page(2, 7), {}),
    }
    fake = Fake(responses=responses)
    rows, report = fake.read()
    assert report.pages_read == 2
    assert report.rows_emitted == 4
    assert len(rows) == 4
    assert "LECTURA_PARCIAL_PAGINA_FALLIDA" in report.truncations
    assert report.outcome == "error_red"
    # La ruta que si entrego feed se conserva: es informacion cierta.
    assert report.route_used == WOO_V1
    assert report.platform == "woo"


def test_a_failing_page_is_requested_exactly_once_and_the_read_stops_there() -> None:
    responses = {
        WOO_V1: (200, _woo_rows_page(1, 1), {"X-WP-TotalPages": "5"}),
        _woo_page(2): (STATUS_SIN_RESPUESTA, "", {}),
        _woo_page(3): (200, _woo_rows_page(1, 3), {}),
    }
    fake = Fake(responses=responses)
    fake.read()
    assert fake.seen.count(_woo_page(2)) == 1
    assert _woo_page(3) not in fake.seen
    assert fake.seen == [WOO_V1, _woo_page(2)]


def test_an_http_error_mid_read_is_also_a_cut_not_a_retry() -> None:
    responses = {
        WOO_V1: (200, _woo_rows_page(1, 1), {"X-WP-TotalPages": "3"}),
        _woo_page(2): (500, "boom", {}),
    }
    _, report = Fake(responses=responses).read()
    assert report.pages_read == 1
    assert report.outcome == "error_red"
    assert "LECTURA_PARCIAL_PAGINA_FALLIDA" in report.truncations


@pytest.mark.parametrize(
    "body",
    [
        "",
        "<html><body>no json</body></html>",
        "{",
        "null",
        "123",
        '{"products": "not a list"}',
        json.dumps([None, 1, "dos", [], {}]),
        json.dumps([{"id": 1, "prices": None, "name": None}]),
    ],
)
def test_a_hostile_body_never_raises_and_always_yields_a_typed_outcome(body: str) -> None:
    rows, report = Fake(responses={WOO_V1: (200, body, {})}).read()
    assert isinstance(rows, tuple)
    assert isinstance(report, ReadReport)
    assert report.outcome in ("ok", "sin_feed_json", "bloqueado_waf", "plataforma_desconocida", "error_red")


def test_nothing_is_lost_without_being_counted() -> None:
    """Invariante: filas crudas recibidas == rows_emitted + suma de rows_dropped."""
    page_one = json.dumps(
        [
            "no soy un objeto",
            {"id": 1, "prices": {"price": "1990", "currency_minor_unit": 2, "currency_code": "CLP"}},
            {"id": 2, "name": "P2", "prices": {"price": "1990", "currency_minor_unit": 2, "currency_code": "CLP"}},
        ]
    )
    page_two = json.dumps(
        [
            {"id": 3, "name": "P3", "prices": {"price": "1990", "currency_minor_unit": 2, "currency_code": "CLP"}},
            42,
        ]
    )
    fake = Fake(
        responses={
            WOO_V1: (200, page_one, {"X-WP-TotalPages": "2"}),
            _woo_page(2): (200, page_two, {}),
        }
    )
    _, report = fake.read()
    raw_rows = len(json.loads(page_one)) + len(json.loads(page_two))
    assert report.rows_emitted + sum(count for _, count in report.rows_dropped) == raw_rows
    assert raw_rows == 5
    assert report.rows_emitted == 2
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
PYTHONPATH=. C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m pytest tests/test_catalog_source.py -q
```

Salida esperada: **exactamente un rojo**, `test_a_malformed_port_never_escapes_as_an_unexpected_exception`, con `httpx.InvalidURL: Invalid port: 'puerto'` propagandose fuera de `read_catalog`.

Los otros seis arrancan verdes, asi que hay que probar que muerden. **Cuatro mini-mutaciones, una por familia de garantia, una por vez, cada una con su reversion.** Aplicar cada una como el reemplazo de UNA linea:

```bash
# (a) declarar el corte -- rojo esperado: test_a_page_that_fails_mid_read..., test_an_http_error_mid_read...
#     en _read_pages, reemplazar la linea `truncations.append(TRUNCATION_PAGINA_FALLIDA)`
#     de la rama `if response is None or response.status != 200:` por `pass`
PYTHONPATH=. C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m pytest tests/test_catalog_source.py -q
git checkout -- src/catalog/source.py

# (b) no reintentar -- rojo esperado: test_a_failing_page_is_requested_exactly_once_and_the_read_stops_there
#     en _read_pages, reemplazar el `break` de esa misma rama por `page += 1; continue`
PYTHONPATH=. C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m pytest tests/test_catalog_source.py -q
git checkout -- src/catalog/source.py

# (c) cuerpo hostil -- rojo esperado: test_a_hostile_body_never_raises_and_always_yields_a_typed_outcome
#     en _parse_json, reemplazar la linea `except (TypeError, ValueError):` por `except KeyError:`
PYTHONPATH=. C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m pytest tests/test_catalog_source.py -q
git checkout -- src/catalog/source.py

# (d) contabilidad -- rojo esperado: test_nothing_is_lost_without_being_counted
#     en woo.to_rows, reemplazar la linea `dropped.append(DROP_FILA_NO_DICT)` por `pass`
PYTHONPATH=. C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m pytest tests/test_catalog_source.py -q
git checkout -- src/catalog/adapters/woo.py
```

Si alguna de las cuatro deja la suite verde, el test correspondiente no esta guardando nada: se arregla el test antes de seguir.

- [ ] **Step 3: Write minimal implementation**

En `src/catalog/source.py`, en `_Requester.get`, ampliar el `except`:

```python
        try:
            response = self._client.get(
                url, timeout=DEFAULT_TIMEOUT_SECONDS, headers={"User-Agent": USER_AGENT}
            )
        except (httpx.HTTPError, httpx.InvalidURL, UnicodeError):
            # httpx.InvalidURL no hereda de HTTPError, y un host que no pasa IDNA
            # levanta UnicodeError. Las dos son problemas de forma de la URL que
            # el llamador escribio: son dato para el informe, no una excepcion
            # que reviente la lectura. Y no se reintenta.
            return None
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
PYTHONPATH=. C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m pytest tests/test_catalog_source.py -q
C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m ruff check src tests examples
```

Salida esperada: **cero fallos**, `All checks passed!` y `git status --short` sin ninguna de las cuatro mini-mutaciones puesta.

- [ ] **Step 5: Commit**

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
git add src/catalog/source.py tests/test_catalog_source.py
git commit -m "test(catalog): garantias de lectura parcial, cero excepciones e invariante

Una pagina que falla se pide una sola vez, corta la lectura, deja pages_read en
lo que llego y conserva la ruta que si entrego feed. Ningun cuerpo hostil levanta
excepcion. Invariante: filas crudas == rows_emitted + suma de rows_dropped. Y una
URL con puerto invalido sale como error_red en vez de escapar como InvalidURL."
```

---

### Task 15: La propiedad central sobre todo el corpus de fixtures

Para todo `Attribution` en estado `declarado`, su `evidence` tiene que aparecer **literal** en el `raw` de esa fila. Es una propiedad sobre todo el corpus, no una lista de casos: descubre los fixtures por glob del directorio y los atributos por introspeccion del `CatalogRow`, asi que cubre tambien los atributos que todavia no existen y los fixtures que se agreguen despues.

Y es **separada** de la guarda del arbitraje (Tarea 3), porque la propiedad no atrapa el arbitraje silencioso: quedarse con la primera senal produce una evidencia literal perfectamente valida y la propiedad pasa. Evidencia inventada y arbitraje silencioso son dos fallas distintas y cada una necesita su guarda.

**Files:**
- Modify: `tests/test_catalog_record.py`

**Interfaces:**
- Consumes: `CANONICAL_ATTRIBUTES`, `iter_attributions`, `tally`, `DROP_REASONS` (Tareas 4-5); `woo`, `shopify` (Tareas 6-9); los fixtures de la Tarea 1.
- Produces: nada que otra tarea consuma (es la red de seguridad del componente).

- [ ] **Step 1: Write the failing test**

Agregar a `tests/test_catalog_record.py`:

```python
def _leaf_evidences(node: object) -> set[str]:
    """Todos los valores hoja del payload, en la misma forma que ``evidence_of``.

    Compara contra los valores, no contra el JSON serializado, para no romperse
    con el escapado de comillas o de acentos."""
    if isinstance(node, Mapping):
        found: set[str] = set()
        for value in node.values():
            found |= _leaf_evidences(value)
        return found
    if isinstance(node, (list, tuple)):
        found = set()
        for item in node:
            found |= _leaf_evidences(item)
        return found
    return {node if isinstance(node, str) else json.dumps(node)}


# DECISIONS.json no es un payload de feed: es el archivo de decisiones de la
# Tarea 1. Se excluye por nombre, no estrechando el glob, para que un fixture de
# feed nuevo siga entrando solo.
NON_FEED_FIXTURES = ("DECISIONS.json",)


def _feed_fixture_names() -> list[str]:
    return sorted(path.name for path in FIXTURES.glob("*.json") if path.name not in NON_FEED_FIXTURES)


def _corpus() -> list[tuple[str, object, object]]:
    """Cada fixture JSON de feed con el UNICO adaptador que lo reconoce.

    Por glob a proposito: un fixture nuevo entra en la propiedad sin que nadie
    tenga que acordarse de agregarlo a una lista. Y se recolectan TODOS los
    matches en vez de cortar en el primero: si el reconocimiento de una plataforma
    se aflojara y aceptara la forma de la otra, cortar en el primer match dejaria
    el sondeo ambiguo sin ninguna guarda."""
    cases: list[tuple[str, object, object]] = []
    for name in _feed_fixture_names():
        payload = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
        matches = [adapter for adapter in (woo, shopify) if adapter.recognizes(payload)]
        assert len(matches) == 1, f"{name}: reconocido por {[a.PLATFORM for a in matches]}"
        cases.append((name, matches[0], payload))
    return cases


def _corpus_rows() -> list[tuple[str, CatalogRow]]:
    rows: list[tuple[str, CatalogRow]] = []
    for name, adapter, payload in _corpus():
        result = adapter.to_rows(payload, url=FEED_URL, site="tienda.test", read_at=READ_AT)  # type: ignore[attr-defined]
        rows.extend((name, row) for row in result.rows)
    return rows


def test_every_json_fixture_is_recognized_by_exactly_one_adapter() -> None:
    files = _feed_fixture_names()
    assert files, "el corpus de fixtures no puede estar vacio"
    assert [name for name, _, _ in _corpus()] == files   # la exclusividad la assertea _corpus


def test_no_declared_attribution_in_the_corpus_carries_a_blank_value() -> None:
    """C14 sobre datos reales: Woo publica ``"sku": ""`` cuando no esta seteado, y
    declararlo seria afirmar donde el feed no dice nada."""
    for name, row in _corpus_rows():
        for attribute, attribution in iter_attributions(row):
            if attribution.state != "declarado":
                continue
            assert str(attribution.value).strip() != "", f"{name}:{attribute} declarado con valor vacio"
            assert str(attribution.evidence).strip() != "", f"{name}:{attribute} declarado sin evidencia"


def test_every_value_in_a_mapped_key_falls_inside_the_closed_vocabulary() -> None:
    """La guarda que hace admisible el descarte de C8. Cuando ``resolve`` no
    entiende el valor de una clave mapeada, lo descarta del arbitraje y no queda
    rastro en el informe: eso solo es honesto si un valor desconocido en datos
    reales aparece como test ROJO. Si esto se pone rojo, el arreglo es AMPLIAR el
    vocabulario del adaptador, nunca ensanchar el descarte."""
    for name, adapter, payload in _corpus():
        if adapter is not woo:
            continue
        for raw in adapter.rows_of(payload):   # type: ignore[attr-defined]
            if not isinstance(raw, Mapping):
                continue
            clase = dig(raw, woo.STOCK_CLASS_KEY)
            if clase is None or not str(clase).strip():
                continue
            assert clase in woo.WOO_STOCK_CLASSES, f"{name}: clase de stock fuera del vocabulario: {clase!r}"


def test_the_corpus_carries_rows_so_the_property_is_not_vacuous() -> None:
    rows = _corpus_rows()
    assert len(rows) >= 4
    assert sum(1 for _, row in rows if row.price.state == "declarado") >= 1


def test_every_declared_evidence_appears_literally_in_its_own_rows_raw() -> None:
    """La propiedad central, gemela de la de vedas."""
    checked = 0
    for name, row in _corpus_rows():
        literals = _leaf_evidences(row.raw)
        for attribute, attribution in iter_attributions(row):
            if attribution.state != "declarado":
                continue
            assert attribution.evidence in literals, f"{name}:{attribute} evidencia inventada: {attribution.evidence!r}"
            checked += 1
    assert checked >= 4


def test_the_property_visits_every_attribution_including_ones_that_do_not_exist_yet() -> None:
    for _, row in _corpus_rows():
        visited = {attribute for attribute, _ in iter_attributions(row)}
        assert set(CANONICAL_ATTRIBUTES) <= visited
        assert len(visited) == len(CANONICAL_ATTRIBUTES) + len(row.categories)


def test_every_declared_attribution_carries_a_source_from_the_allowed_prefixes() -> None:
    for name, row in _corpus_rows():
        for attribute, attribution in iter_attributions(row):
            if attribution.state != "declarado":
                continue
            assert attribution.source is not None, f"{name}:{attribute} declarado sin procedencia"
            assert attribution.source.startswith(ALLOWED_SOURCE_PREFIXES), f"{name}:{attribute} -> {attribution.source}"


def test_no_source_in_the_corpus_is_derived_from_the_title() -> None:
    """Ningun adaptador de esta rama deriva un valor del texto del titulo: el
    lector carga las fuentes, el normalizador de peso decide."""
    for _, row in _corpus_rows():
        for _, attribution in iter_attributions(row):
            assert attribution.source != "titulo"
            if attribution.source is not None:
                assert attribution.source.startswith("feed:")


def test_weight_declared_always_comes_from_a_feed_key_across_the_whole_corpus() -> None:
    for name, row in _corpus_rows():
        assert row.weight_declared.state != "contradicho", f"{name}: el lector no arbitra el peso"
        if row.weight_declared.state == "declarado":
            assert row.weight_declared.source is not None
            assert row.weight_declared.source.startswith("feed:")


def test_nothing_is_lost_without_being_counted_across_the_whole_corpus() -> None:
    for name, adapter, payload in _corpus():
        result = adapter.to_rows(payload, url=FEED_URL, site="tienda.test", read_at=READ_AT)  # type: ignore[attr-defined]
        raw_rows = len(adapter.rows_of(payload))  # type: ignore[attr-defined]
        counted = len(result.rows) + sum(count for _, count in tally(result.dropped, DROP_REASONS))
        assert counted == raw_rows, name
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
PYTHONPATH=. C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m pytest tests/test_catalog_record.py -q
```

Dos desenlaces posibles y los dos son informacion:

1. **Rojo:** hay un adaptador que inventa evidencia o pierde filas sin contarlas. Es un bug real de las Tareas 6-9. **Se arregla el adaptador, nunca el test.**
2. **Verde en la primera corrida:** hay que probar que la propiedad no es vacua antes de seguir. Mini-mutacion, en `src/catalog/record.py`: reemplazar la unica linea del `return` de `evidence_of` por

```python
    return "Z" + (raw if isinstance(raw, str) else json.dumps(raw))
```

La mutacion tiene que alterar **toda** evidencia. Una que solo toque ceros a la izquierda o espacios (`.strip().lstrip("0")`) deja identicos los valores tipicos del corpus grabado -- nombres de producto, `"1990"`, `"true"`, `"in-stock"` -- asi que la propiedad seguiria verde y el chequeo que existe para demostrar que la propiedad NO es vacua pasaria sin demostrar nada: la misma falla, un nivel mas arriba.

Correr los tests: `test_every_declared_evidence_appears_literally_in_its_own_rows_raw` tiene que ponerse rojo con `evidencia inventada:`, y el mensaje tiene que nombrar **al menos dos fixtures distintos** (si nombra uno solo, el corpus no tiene la masa que el propio paso declara). Despues revertir:

```bash
git checkout -- src/catalog/record.py
```

- [ ] **Step 3: Write minimal implementation**

Solo si el paso 2 dio rojo por un bug real: arreglar el adaptador culpable (nunca el test, nunca el fixture). La causa tipica es haber reformateado `evidence` en vez de guardar el crudo -- `evidence` sale siempre de `evidence_of(valor_crudo)`, jamas del valor ya normalizado.

Si el paso 2 dio verde y la mini-mutacion puso rojo, no hay implementacion en esta tarea y se pasa al paso 4.

- [ ] **Step 4: Run test to verify it passes**

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
PYTHONPATH=. C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m pytest tests/test_catalog_record.py tests/test_catalog_source.py -q
C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m ruff check src tests examples
```

Salida esperada: **cero fallos** y `All checks passed!`, con el arbol limpio (la mini-mutacion revertida).

- [ ] **Step 5: Commit**

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
git add tests/test_catalog_record.py
git commit -m "test(catalog): propiedad central de evidencia literal sobre todo el corpus

Para todo Attribution declarado, su evidence aparece literal en el raw de su
fila. Los fixtures se descubren por glob y los atributos por introspeccion, asi
que un fixture o un atributo nuevo entran solos. Separada de la guarda del
arbitraje a proposito: la propiedad no atrapa el arbitraje silencioso."
```

---

### Task 16: Guardas de frontera -- vocabulario, imports, perimetro y pureza

Lo que el componente promete NO hacer tiene que ser verificable por una maquina, no por una lectura atenta. Precedente exacto del repo: `tests/test_pricing_intel_boot_safety.py`.

**Files:**
- Create: `tests/test_catalog_boundaries.py`

**Interfaces:**
- Consumes: el arbol `src/catalog/` completo (Tareas 2-14).
- Produces: nada que otra tarea consuma.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_catalog_boundaries.py
"""Guardas de frontera de src/catalog/.

Lo que este componente promete NO hacer se verifica aca por maquina, no por
lectura atenta. Mismo espiritu que tests/test_pricing_intel_boot_safety.py.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from src.catalog.adapters import shopify

REPO_ROOT = Path(__file__).resolve().parents[1]
CATALOG = REPO_ROOT / "src" / "catalog"
CATALOG_FILES = sorted(CATALOG.rglob("*.py"))
NON_NETWORK_FILES = [path for path in CATALOG_FILES if path.name != "source.py"]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "catalog"

# Vocabulario de dominio. El componente no sabe que es una especie ni una veda,
# y esta lista es lo que impide que se le filtre de a poco.
DOMAIN_VOCABULARY = (
    "especie",
    "veda",
    "congelad",
    "decreto",
    "merluza",
    "reineta",
    "salmon",
    "jibia",
    "marisco",
    "pescado",
)

# Acoplamientos prohibidos: paquete de precios, gate legal del llamador, sistema
# de registro, y raspado de HTML.
FORBIDDEN_TOKENS = (
    "pricing_intel",
    "robots",
    "max_tier_allowed",
    "tos_decision",
    "writeback",
    "Changeset",
    "Approval",
    "ledger",
    "homolog",
    "extruct",
    "beautifulsoup",
    "bs4",
    "microdata",
    "json-ld",
    "jsonld",
    "selectolax",
    "lxml",
)

NETWORK_MODULES = ("httpx", "requests", "urllib", "socket", "http.client", "aiohttp")


def _source_of(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _imported_modules(path: Path) -> set[str]:
    """Modulos importados, con los puntos de un import relativo al frente."""
    tree = ast.parse(_source_of(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add("." * node.level + (node.module or ""))
    return modules


def test_the_catalog_package_actually_exists_so_these_guards_are_not_vacuous() -> None:
    assert CATALOG.is_dir()
    assert {path.name for path in CATALOG_FILES} >= {"__init__.py", "record.py", "source.py", "woo.py", "shopify.py"}


@pytest.mark.parametrize("path", CATALOG_FILES, ids=lambda path: path.name)
def test_no_domain_vocabulary_anywhere_in_the_catalog_package(path: Path) -> None:
    lowered = _source_of(path).lower()
    hits = [word for word in DOMAIN_VOCABULARY if word in lowered]
    assert hits == [], f"{path.name} trae vocabulario de dominio: {hits}"


@pytest.mark.parametrize("path", CATALOG_FILES, ids=lambda path: path.name)
def test_no_forbidden_coupling_anywhere_in_the_catalog_package(path: Path) -> None:
    lowered = _source_of(path).lower()
    hits = [token for token in FORBIDDEN_TOKENS if token.lower() in lowered]
    assert hits == [], f"{path.name} trae un acoplamiento prohibido: {hits}"


@pytest.mark.parametrize("path", NON_NETWORK_FILES, ids=lambda path: path.name)
def test_only_source_py_touches_the_network(path: Path) -> None:
    imported = _imported_modules(path)
    hits = [module for module in imported if module.split(".")[0] in NETWORK_MODULES or module in NETWORK_MODULES]
    assert hits == [], f"{path.name} importa red: {hits}"


def test_source_py_is_the_one_that_may_import_httpx() -> None:
    assert "httpx" in _imported_modules(CATALOG / "source.py")


@pytest.mark.parametrize("path", NON_NETWORK_FILES, ids=lambda path: path.name)
def test_the_pure_side_never_imports_the_network_side(path: Path) -> None:
    relatives = {module.lstrip(".") for module in _imported_modules(path)}
    assert "source" not in relatives, f"{path.name} importa source.py y arrastraria httpx"


def test_the_package_init_imports_no_submodule_so_prod_boot_stays_safe() -> None:
    """Prod arranca con .[web,mcp] y httpx es extra opcional: si __init__ re-exporta
    source.py, cualquier cadena de arranque que importe src.catalog revienta."""
    assert _imported_modules(CATALOG / "__init__.py") <= {"__future__"}


def test_source_py_never_wires_the_rate_limit_wait_to_the_clock() -> None:
    source = _source_of(CATALOG / "source.py")
    assert "time.sleep" not in source
    assert "time" not in _imported_modules(CATALOG / "source.py")


@pytest.mark.parametrize(
    "path", sorted((CATALOG / "adapters").glob("*.py")), ids=lambda path: path.name
)
def test_adapters_never_search_keys_fuzzily(path: Path) -> None:
    """Inferir el mapeo por parecido de nombre produce filas plausibles y
    erroneas sin fallar nunca."""
    imported = _imported_modules(path)
    assert not {"re", "difflib", "rapidfuzz", "fuzzywuzzy"} & imported, path.name


def test_the_v1_perimeter_is_exactly_woo_and_shopify() -> None:
    adapters = sorted(path.name for path in (CATALOG / "adapters").glob("*.py"))
    assert adapters == ["__init__.py", "shopify.py", "woo.py"]


def test_attribution_is_defined_exactly_once_in_the_whole_tree() -> None:
    """Un solo nombre para la idea de "un valor y de donde salio". Dos
    definiciones es el problema que este tipo existe para no crear."""
    definitions = [
        path
        for path in REPO_ROOT.rglob("*.py")
        if "__pycache__" not in path.parts
        and ".venv" not in path.parts
        and "class Attribution" in path.read_text(encoding="utf-8", errors="replace")
    ]
    assert len(definitions) == 1, definitions
    assert definitions[0] == CATALOG / "record.py"


@pytest.mark.parametrize("path", CATALOG_FILES, ids=lambda path: path.name)
def test_the_catalog_package_never_renames_attribution_to_claim(path: Path) -> None:
    """``src/seo/pdp_writer.py`` ya tiene un ``class Claim`` que es otra cosa
    (una afirmacion de copy de ficha), asi que el chequeo se limita a este
    paquete: aca la idea se llama Attribution y nada mas."""
    assert "class Claim" not in _source_of(path), path.name


@pytest.mark.parametrize("path", CATALOG_FILES, ids=lambda path: path.name)
def test_the_catalog_package_is_ascii_only(path: Path) -> None:
    """Los .py se mantienen ASCII: la consola de Windows es cp1252 y un grep de
    guarda sobre acentos deja de ser confiable en cuanto entra un no-ASCII."""
    assert _source_of(path).isascii(), path.name


@pytest.mark.parametrize("path", CATALOG_FILES, ids=lambda path: path.name)
def test_no_unfilled_placeholder_reaches_main(path: Path) -> None:
    """El comentario de evidencia de la Tarea 1 se commitea a src/ tal cual, y el
    guard de ASCII lo aprueba porque los placeholders son ASCII. Un
    ``# verificado contra <host>/products/<handle> el <fecha>`` sin rellenar es una
    evidencia que no existe."""
    hits = re.findall(r"<(?:host|handle|fecha|valor|plataforma)>", _source_of(path))
    assert hits == [], f"{path.name} tiene placeholders sin rellenar: {hits}"


def test_the_shopify_price_factor_is_never_asserted_as_two() -> None:
    """C12. El factor no viaja en el payload de Shopify: los unicos valores
    admisibles son 0 (con la forma del valor como evidencia) y None. Un 2 global
    derivado de una tienda es el bug de 100x hecho diseno, y sin esta guarda
    cambiar la constante de 0 a 2 deja la suite entera verde."""
    assert shopify.PRICE_MINOR_UNIT in (0, None)
    assert shopify.PRICE_MINOR_UNIT != 2
    decisions = json.loads((FIXTURES / "DECISIONS.json").read_text(encoding="utf-8"))
    assert decisions["shopify_price_minor_unit"] in (0, None)
    assert shopify.PRICE_MINOR_UNIT == decisions["shopify_price_minor_unit"]
    if shopify.PRICE_MINOR_UNIT == 0:
        assert decisions["shopify_price_evidence"]["payload_shape"] == "string decimal"


@pytest.mark.parametrize("path", CATALOG_FILES, ids=lambda path: path.name)
def test_no_adapter_declares_a_scale_factor_that_does_not_come_from_the_payload(path: Path) -> None:
    """Cualquier factor de escala nuevo tiene que salir de una CLAVE del payload
    (como ``currency_minor_unit`` en Woo) o de la forma declarada del valor. Esta
    guarda es un recordatorio ejecutable: el unico factor constante permitido en
    el arbol es el de Shopify, y esta acotado por el test de arriba."""
    constants = re.findall(r"^\s*([A-Z_]*MINOR_UNIT[A-Z_]*)\s*[:=]", _source_of(path), re.MULTILINE)
    assert set(constants) <= {"PRICE_MINOR_UNIT", "MINOR_UNIT_KEY", "UNDECLARED_MINOR_UNIT_AUSENTE"}, (
        path.name,
        constants,
    )
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
PYTHONPATH=. C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m pytest tests/test_catalog_boundaries.py -q
```

Si el arbol quedo limpio en las Tareas 2-14, esto pasa entero en la primera corrida. Antes de seguir, probar que **cada familia de guarda muerde**. Cuatro mini-mutaciones, una por vez, cada una con su reversion:

```bash
# 1) vocabulario de dominio
printf '\n# nota: esto es para congelados\n' >> src/catalog/record.py
PYTHONPATH=. C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m pytest tests/test_catalog_boundaries.py -q -k vocabulary   # rojo esperado
git checkout -- src/catalog/record.py

# 2) acoplamiento con el paquete de precios
printf '\nfrom src.pricing_intel.models import CompetitorOffer  # noqa: F401\n' >> src/catalog/record.py
PYTHONPATH=. C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m pytest tests/test_catalog_boundaries.py -q -k coupling   # rojo esperado
git checkout -- src/catalog/record.py

# 3) red fuera de source.py
printf '\nimport httpx  # noqa: F401\n' >> src/catalog/adapters/woo.py
PYTHONPATH=. C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m pytest tests/test_catalog_boundaries.py -q -k network   # rojo esperado
git checkout -- src/catalog/adapters/woo.py

# 4) espera cableada al reloj
printf '\nimport time  # noqa: F401\n' >> src/catalog/source.py
PYTHONPATH=. C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m pytest tests/test_catalog_boundaries.py -q -k clock   # rojo esperado
git checkout -- src/catalog/source.py

# 5) placeholder sin rellenar
printf '\n# verificado contra <host>/products/<handle> el <fecha>\n' >> src/catalog/adapters/shopify.py
PYTHONPATH=. C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m pytest tests/test_catalog_boundaries.py -q -k placeholder   # rojo esperado
git checkout -- src/catalog/adapters/shopify.py

# 6) factor de precio afirmado
#    editar a mano PRICE_MINOR_UNIT = 2 en src/catalog/adapters/shopify.py
PYTHONPATH=. C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m pytest tests/test_catalog_boundaries.py -q -k price_factor   # rojo esperado
git checkout -- src/catalog/adapters/shopify.py
```

El gate de `robots.txt` del llamador vive en `examples/`, no aca: la guarda de `FORBIDDEN_TOKENS` incluye `robots` y escanea **solo** `src/catalog/`, asi que sigue verde -- y eso es exactamente lo que D7 pide (el gate es del llamador, y el paquete no lo conoce).

Si en cambio la primera corrida da rojo, es un hallazgo real: **se arregla `src/catalog/`, nunca la guarda**. El caso mas probable es un docstring que menciona un token prohibido; reescribir el docstring sin nombrarlo.

- [ ] **Step 3: Write minimal implementation**

Solo si el paso 2 dio rojo por un hallazgo real: quitar del arbol `src/catalog/` el token, el import o el acento que la guarda encontro. No se relaja ninguna lista.

- [ ] **Step 4: Run test to verify it passes**

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
git status --short
PYTHONPATH=. C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m pytest tests/test_catalog_boundaries.py -q
C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m ruff check src tests examples
```

Salida esperada: `git status --short` muestra solo el test nuevo (las seis mini-mutaciones revertidas), **cero fallos** y `All checks passed!`.

- [ ] **Step 5: Commit**

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
git add tests/test_catalog_boundaries.py
git commit -m "test(catalog): guardas de frontera del paquete

Verifican por maquina lo que el componente promete no hacer: cero vocabulario de
dominio, cero acoplamiento con el paquete de precios, cero gate de robots o de
tier, cero sistema de registro, cero raspado de HTML, red solo en source.py,
__init__ sin submodulos, espera no cableada al reloj, perimetro v1 = woo +
shopify, una sola definicion de Attribution, ASCII en todo el paquete, ningun
placeholder de evidencia sin rellenar y ningun factor de escala de precio
afirmado sin base en el payload."
```

---

### Task 17: CLI de corrida real, offline y determinista

Esta tarea entrega el CLI y sus tests, y **nada mas**: no incluye la corrida contra tiendas vivas. La validacion en campo es la Tarea 18, separada a proposito -- un paso que depende de sitios vivos no es un gate determinista, y los arreglos que puede disparar pertenecen a las Tareas 6-14, no aca.

**Files:**
- Create: `examples/run_catalog_read.py`, `tests/test_catalog_cli.py`
- Modify: `tests/test_catalog_boundaries.py` (solo las dos guardas de frontera del CLI)

**Interfaces:**
- Consumes: `read_catalog`, `USER_AGENT`, `DEFAULT_RATE_LIMIT_SECONDS`, `PROBE_ROUTES` (Tareas 10-14); `ATTRIBUTION_STATES`, `CatalogRow`, `ReadReport` (Tareas 2-5).
- Produces:
  - `def format_report(report: ReadReport, rows: Sequence[CatalogRow]) -> str` -- el informe y el resumen de cobertura por atributo, ASCII puro. **Renderiza** `report.attribute_states` (C17): no lo calcula, porque la cobertura es parte del informe y no del renderizador.
  - `def probe_routes_allowed(robots_text: str, routes: Sequence[str], *, user_agent: str = USER_AGENT_TOKEN) -> tuple[bool, str]` -- funcion **pura** sobre el texto de `robots.txt`: el gate del llamador que D7 exige (ver abajo).
  - `def main(argv: Sequence[str] | None = None) -> int`.

**El gate de `robots.txt` se implementa ACA.** D7 no dice "el gate no existe": dice que es del **llamador**. El unico llamador que esta rama produce es este CLI, y sin esto el requisito no tiene ninguna tarea que lo implemente en ningun lado del diff -- `src/catalog/` no lo hace por diseno (y hay guarda que lo verifica) y el llamador tampoco lo hacia. Vive en `examples/`, asi que la guarda `FORBIDDEN_TOKENS` de la Tarea 16, que escanea solo `src/catalog/`, sigue verde.

**Este CLI queda FUERA de CI.** No se toca `.github/workflows/tests.yml`: el paso "Smoke examples" corre solo `run_part1_part2.py` y `run_batch.py`, y este archivo pega de verdad contra la red. Ruff si lo va a lintear (`ruff check src tests examples` incluye `examples`). Sus tests, en cambio, **si** corren en CI: son offline (funcion pura + renderizado).

- [ ] **Step 1: Write the failing test**

Primero las dos guardas que **si** son de frontera, en `tests/test_catalog_boundaries.py`:

```python
EXAMPLE_CLI = REPO_ROOT / "examples" / "run_catalog_read.py"


def test_the_cli_source_file_is_ascii_only() -> None:
    """La consola de Windows es cp1252: un acento en un literal de print la
    revienta con UnicodeEncodeError a mitad de la corrida real."""
    assert EXAMPLE_CLI.read_text(encoding="utf-8").isascii()


def test_the_cli_is_not_wired_into_ci() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    assert "run_catalog_read" not in workflow


def test_the_robots_gate_lives_in_the_caller_not_in_the_package() -> None:
    """D7 en las dos direcciones: el gate existe (en examples/) y el paquete no lo
    conoce."""
    assert "robots" in EXAMPLE_CLI.read_text(encoding="utf-8").lower()
    for path in CATALOG_FILES:
        assert "robots" not in _source_of(path).lower(), path.name
```

Y el resto en `tests/test_catalog_cli.py`, archivo nuevo (C10: `test_catalog_boundaries.py` es para guardas de frontera, y el renderizado no es una):

```python
# tests/test_catalog_cli.py
"""Tests del CLI de corrida real. Offline: ninguno pega contra la red.

Cubren el renderizado del informe (ASCII, todas las secciones) y el gate de
robots.txt del llamador, que es una funcion pura sobre el texto de robots.
"""

from __future__ import annotations

from datetime import datetime, timezone

from examples.run_catalog_read import format_report, probe_routes_allowed
from src.catalog.record import Attribution, CatalogRow, ReadReport
from src.catalog.source import PROBE_ROUTES

ROUTES = [route for route, _ in PROBE_ROUTES]


def test_the_gate_blocks_a_route_that_robots_disallows_for_our_agent() -> None:
    robots = "User-agent: *\nDisallow: /wp-json/\n"
    allowed, reason = probe_routes_allowed(robots, ROUTES)
    assert allowed is False
    assert "wp-json" in reason
    assert reason.isascii()


def test_the_gate_allows_everything_when_robots_allows_it() -> None:
    allowed, reason = probe_routes_allowed("User-agent: *\nDisallow: /admin/\n", ROUTES)
    assert allowed is True
    assert reason.isascii()


def test_an_empty_or_unreadable_robots_is_treated_as_allowed_and_says_so() -> None:
    """Decision escrita de antemano (Tarea 1): robots vacio, 404 o 5xx se permite,
    y queda anotado. Lo que no puede pasar es que el criterio se decida el dia de
    la corrida."""
    allowed, reason = probe_routes_allowed("", ROUTES)
    assert allowed is True
    assert reason.isascii()


def test_a_rule_aimed_at_our_agent_wins_over_the_wildcard_group() -> None:
    robots = "User-agent: *\nAllow: /\n\nUser-agent: KernCatalogReader\nDisallow: /products.json\n"
    allowed, reason = probe_routes_allowed(robots, ROUTES)
    assert allowed is False
    assert "products.json" in reason


def test_the_rendered_report_is_ascii_only_and_names_every_section() -> None:
    report = ReadReport(
        outcome="error_red",
        platform="woo",
        route_used="/wp-json/wc/store/v1/products?per_page=100",
        pages_read=3,
        rows_emitted=1,
        rows_declared=250,
        routes_probed=(("/wp-json/wc/store/v1/products?per_page=100", 200),),
        rows_dropped=(("SIN_TITULO", 2),),
        undeclared_reasons=(("MINOR_UNIT_AUSENTE", 5),),
        truncations=("TOPE_PAGINAS_ALCANZADO",),
        attribute_states=(("title", (1, 0, 0)), ("sku", (0, 1, 0))),
    )
    row = CatalogRow(
        url="https://tienda.test/wp-json/wc/store/v1/products?per_page=100",
        site="tienda.test",
        read_at=datetime(2026, 7, 25, tzinfo=timezone.utc),
        title=Attribution(value="Producto", source="feed:name", evidence="Producto"),
    )
    rendered = format_report(report, (row,))
    assert rendered.isascii()
    for expected in (
        "CATALOG READ",
        "outcome",
        "platform",
        "route_used",
        "pages_read",
        "rows_emitted",
        "rows_declared",
        "routes_probed",
        "rows_dropped",
        "undeclared",
        "truncations",
        "COVERAGE BY ATTRIBUTE",
        "SIN_TITULO",
        "MINOR_UNIT_AUSENTE",
        "TOPE_PAGINAS_ALCANZADO",
        "title",
        "sku",
        "declarado",
        "no_declarado",
        "contradicho",
    ):
        assert expected in rendered, expected


def test_the_coverage_block_comes_from_the_report_not_from_a_recount() -> None:
    """C17: la cobertura es parte del informe. Si el CLI la recalculara, un
    consumidor programatico veria un informe mudo sobre cuatro de los siete
    atributos."""
    report = ReadReport(outcome="ok", attribute_states=(("gtin", (0, 7, 0)),))
    rendered = format_report(report, ())
    assert "gtin" in rendered
    assert "no_declarado=7" in rendered


def test_the_rendered_report_stays_ascii_with_an_empty_read() -> None:
    rendered = format_report(ReadReport(outcome="plataforma_desconocida"), ())
    assert rendered.isascii()
    assert "plataforma_desconocida" in rendered
    assert "(none)" in rendered
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
PYTHONPATH=. C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m pytest tests/test_catalog_boundaries.py tests/test_catalog_cli.py -q
```

Salida esperada: error de coleccion, `ModuleNotFoundError: No module named 'examples.run_catalog_read'`.

- [ ] **Step 3: Write minimal implementation**

```python
# examples/run_catalog_read.py
"""Corrida real del lector de catalogo publico contra una URL de tienda.

    python examples/run_catalog_read.py --url https://tienda.example

Imprime el ReadReport y un resumen de cobertura por atributo. Fuera de CI: pega
de verdad contra la tienda.

El gate de robots.txt y de techo de tier es del LLAMADOR (D7), y el llamador es
este archivo: antes de leer el catalogo se lee el robots.txt de la tienda y, si
prohibe alguna de las rutas de sondeo para nuestro agente, la corrida termina sin
leer nada. El paquete src/catalog/ no conoce ese gate a proposito, y hay una
guarda de test que lo verifica en las dos direcciones.

Todo lo que se imprime es ASCII: la consola de Windows es cp1252.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Sequence
from urllib.robotparser import RobotFileParser

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.catalog.record import (  # noqa: E402
    ATTRIBUTION_STATES,
    CatalogRow,
    ReadReport,
)
from src.catalog.source import (  # noqa: E402
    DEFAULT_RATE_LIMIT_SECONDS,
    PROBE_ROUTES,
    USER_AGENT,
    read_catalog,
)

_LABEL_WIDTH = 13
_REASON_WIDTH = 28
_ATTRIBUTE_WIDTH = 18

# El token de agente que robots.txt matchea (el User-Agent completo lleva la URL
# de contacto, que no es parte del nombre del agente).
USER_AGENT_TOKEN = USER_AGENT.split("/")[0]


def probe_routes_allowed(
    robots_text: str,
    routes: Sequence[str],
    *,
    user_agent: str = USER_AGENT_TOKEN,
) -> tuple[bool, str]:
    """El gate del llamador (D7), como funcion pura y por lo tanto testeable.

    Usa ``urllib.robotparser``, que ya implementa la semantica que un print del
    archivo deja sin resolver: que grupo ``User-agent`` aplica, como resuelve
    ``Allow`` contra ``Disallow`` y los wildcards. Un robots.txt vacio o ilegible
    se trata como permitido, y el motivo se devuelve para que quede anotado -- la
    decision esta fijada de antemano, no se toma el dia de la corrida.
    """
    parser = RobotFileParser()
    parser.parse(robots_text.splitlines())
    blocked = [route for route in routes if not parser.can_fetch(user_agent, route)]
    if blocked:
        return False, "robots.txt disallows: " + ", ".join(blocked)
    return True, "robots.txt allows every probe route"


def _pairs(title: str, pairs: Sequence[tuple[str, int]]) -> list[str]:
    if not pairs:
        return [f"  {title:<{_LABEL_WIDTH}}: (none)"]
    lines = [f"  {title:<{_LABEL_WIDTH}}:"]
    lines.extend(f"    {reason:<{_REASON_WIDTH}} {count}" for reason, count in pairs)
    return lines


def format_report(report: ReadReport, rows: Sequence[CatalogRow] = ()) -> str:
    """El informe completo, en ASCII, listo para pegar en una nota de PR.

    ``rows`` solo aporta el host que efectivamente respondio (C15): la cobertura
    por atributo sale del informe, no de un recuento local.
    """
    lines = ["CATALOG READ"]
    if rows:
        lines.append(f"  {'answered_by':<{_LABEL_WIDTH}}: {rows[0].site}")
    for label, value in (
        ("outcome", report.outcome),
        ("platform", report.platform or "-"),
        ("route_used", report.route_used or "-"),
        ("pages_read", report.pages_read),
        ("rows_emitted", report.rows_emitted),
        ("rows_declared", "-" if report.rows_declared is None else report.rows_declared),
    ):
        lines.append(f"  {label:<{_LABEL_WIDTH}}: {value}")

    if report.routes_probed:
        lines.append(f"  {'routes_probed':<{_LABEL_WIDTH}}:")
        lines.extend(f"    {status:>3}  {route}" for route, status in report.routes_probed)
    else:
        lines.append(f"  {'routes_probed':<{_LABEL_WIDTH}}: (none)")

    lines.extend(_pairs("rows_dropped", report.rows_dropped))
    lines.extend(_pairs("undeclared", report.undeclared_reasons))
    if report.truncations:
        lines.append(f"  {'truncations':<{_LABEL_WIDTH}}:")
        lines.extend(f"    {reason}" for reason in report.truncations)
    else:
        lines.append(f"  {'truncations':<{_LABEL_WIDTH}}: (none)")

    # La cobertura por atributo se RENDERIZA, no se recalcula: vive en el informe
    # (C17), asi que un consumidor programatico ve lo mismo que esta consola.
    lines.append("COVERAGE BY ATTRIBUTE")
    if not report.attribute_states:
        lines.append("  (no rows)")
    for name, counts in report.attribute_states:
        rendered = "  ".join(f"{state}={count}" for state, count in zip(ATTRIBUTION_STATES, counts))
        lines.append(f"  {name:<{_ATTRIBUTE_WIDTH}} {rendered}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    # Una URL de tienda puede traer caracteres no-ASCII (IDN) y la consola
    # cp1252 no los puede encodear: nunca dejar que el print reviente la corrida.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    parser = argparse.ArgumentParser(description="Read a public store catalog and print its coverage.")
    parser.add_argument("--url", help="store base URL, e.g. https://tienda.example")
    parser.add_argument(
        "--rate-limit",
        type=float,
        default=DEFAULT_RATE_LIMIT_SECONDS,
        help="seconds to wait between requests",
    )
    args = parser.parse_args(argv)
    if not args.url:
        parser.error("pass --url <store base URL>")

    base = args.url.rstrip("/")
    with httpx.Client(follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
        # Gate del llamador (D7), antes de pedir una sola ruta de catalogo.
        try:
            robots = client.get(base + "/robots.txt", timeout=15.0)
            robots_text = robots.text if robots.status_code == 200 else ""
            robots_note = f"robots.txt status {robots.status_code}"
        except httpx.HTTPError as exc:
            robots_text = ""
            robots_note = f"robots.txt unreadable ({type(exc).__name__})"
        allowed, reason = probe_routes_allowed(robots_text, [route for route, _ in PROBE_ROUTES])
        print(f"  {'robots':<{_LABEL_WIDTH}}: {robots_note}; {reason}")
        if not allowed:
            print("STOP: the caller gate refuses this store, no catalog route was read", file=sys.stderr)
            return 2

        rows, report = read_catalog(
            base, client=client, sleeper=time.sleep, rate_limit_seconds=args.rate_limit
        )

    print(f"  {'url':<{_LABEL_WIDTH}}: {args.url}")
    print(format_report(report, rows))
    if report.outcome != "ok":
        print(f"note: outcome is {report.outcome}, not a complete read", file=sys.stderr)
        return 1
    if report.truncations:
        print(f"note: read was truncated: {', '.join(report.truncations)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
PYTHONPATH=. C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m pytest tests/test_catalog_boundaries.py tests/test_catalog_cli.py -q
C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m ruff check src tests examples
```

Salida esperada: **cero fallos** y `All checks passed!`. Todo offline: esta tarea no pega contra ninguna tienda.

- [ ] **Step 5: Commit**

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
git add examples/run_catalog_read.py tests/test_catalog_cli.py tests/test_catalog_boundaries.py
git commit -m "feat(catalog): CLI de corrida real con gate de robots del llamador

run_catalog_read.py --url lee el robots.txt de la tienda y aborta con exit 2 si
prohibe alguna ruta de sondeo para nuestro agente: el gate es del llamador (D7) y
este CLI es el llamador, asi que aca es donde tiene que existir. Imprime el
ReadReport y renderiza la cobertura por atributo que trae el informe. ASCII puro
por la consola cp1252 de Windows, y fuera de CI porque pega de verdad contra la
tienda."
```

---

### Task 18: Validacion en campo sobre cuatro tiendas

Tarea de **corrida real**, no de codigo. Esta separada de la Tarea 17 a proposito: depende de sitios vivos, asi que no es un gate determinista, y cualquier bug que encuentre pertenece a la tarea dueña del codigo, no a esta.

**Files:**
- Modify: ninguno. Esta tarea produce salidas, no diff. (Si encuentra un bug, el arreglo se hace en la tarea dueña y con su propio test.)

**Interfaces:**
- Consumes: el CLI de la Tarea 17.
- Produces: las cuatro salidas textuales que van en la nota del PR de la ultima tarea, y la tabla de plataforma / outcome / pages_read / rows_emitted por tienda.

Las cuatro tiendas: 2 de congelados CL (una Woo, una Shopify, las de feeds ya verificados en el Step 0 de la Tarea 1) y 2 de otro rubro o de otro pais. **El gate de robots.txt ya no es un paso manual: lo corre el CLI y aborta con exit 2 si no pasa.**

- [ ] **Step 1: Tienda 1 (congelados CL, Woo)**

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
PY=C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe
PYTHONPATH=. $PY examples/run_catalog_read.py --url https://TIENDA_1_CL_WOO
```

Anotar: `outcome`, `platform`, `pages_read`, `rows_emitted`, `rows_declared`, `truncations` y el bloque de cobertura. Guardar la salida completa.

- [ ] **Step 2: Tienda 2 (congelados CL, Shopify)**

Igual, con `--url https://TIENDA_2_CL_SHOPIFY`.

- [ ] **Step 3: Tiendas 3 y 4 (otro rubro / otro pais)**

Igual, con `--url https://TIENDA_3_OTRO_RUBRO` y `--url https://TIENDA_4_OTRO_PAIS`.

- [ ] **Step 4: Clasificar lo observado, sin parchear nada aca**

Regla dura de esta tarea: **cualquier bug que aparezca abre un paso de arreglo atribuido a la tarea dueña del codigo, con su test primero.** No se parchea dentro de esta tarea, porque un arreglo hecho aca no tiene test que lo guarde.

| Lo que se ve | Que es | Que se hace |
|---|---|---|
| una excepcion escapa del CLI | **bug**: el lector promete no levantar por problemas de red ni de forma | arreglo en la Tarea 14 (cero excepciones), con un caso nuevo en `test_a_hostile_body_never_raises...` o su gemelo |
| `plataforma_desconocida` sobre una tienda que sabemos Woo o Shopify | **bug** de reconocimiento de forma o de clasificacion de status | mirar `routes_probed`; arreglo en la Tarea 6/9 (forma) o en la Tarea 11 (familias de status), con test |
| `error_red` con `truncations` no vacio | **puede ser correcto**: una lectura truncada no es un exito (C2) | verificar contra `rows_declared`; si la lectura fue completa y el truncamiento es espurio, es bug de la Tarea 12/13 |
| `bloqueado_waf` | **desenlace correcto** | anotar y seguir |
| `sin_feed_json` con la plataforma nombrada | **desenlace correcto** | anotar y seguir |
| exit 2 por el gate de robots | **desenlace correcto** | reemplazar la tienda por otra y anotarlo |
| precio `no_declarado` en todas las filas de la Shopify | **esperado si `PRICE_MINOR_UNIT is None`** (C12) | anotar; menos cobertura declarada es el desenlace correcto, un precio 100x mal no lo es |

La cobertura se declara **por plataforma**, no por rubro: la nota enumera Woo / Shopify / no-cubiertas, nunca "congelados" ni ningun rubro.

- [ ] **Step 5: Nada que commitear**

Esta tarea no produce diff. Las cuatro salidas van al scratchpad (`.../scratchpad/campo.md`) y de ahi a la nota del PR.

---

### Task 19: Verificacion por mutacion -- las siete del spec, mas las cuatro del plan

Cada una de las siete mutaciones de la seccion 9 del spec tiene que poner rojo **al menos un test**. Una mutacion que deja la suite verde significa que la regla no esta guardada por nada. Este plan agrega cuatro reglas propias (C12, C2, C13, C14) y por lo tanto cuatro mutaciones mas: once en total.

**Files:**
- Modify (temporalmente, y se revierte cada vez): `src/catalog/record.py`, `src/catalog/source.py`, `src/catalog/adapters/woo.py`, `src/catalog/adapters/shopify.py`
- Test: ninguno nuevo. Esta tarea no escribe codigo: audita los tests que ya existen.

**Interfaces:**
- Consumes: todo lo de las Tareas 2-17.
- Produces: la tabla de mutaciones que va textual en la nota del PR de la ultima tarea.

Regla de oro de esta tarea: **una mutacion por vez, y se revierte antes de la siguiente.** Guardar la tabla en `C:/Users/Gamer/AppData/Local/Temp/claude/C--Users-Gamer-Music-scm/f7886b41-e072-4a3d-84da-8dd79cac168f/scratchpad/mutaciones.md` a medida que se corre.

**Como se escribe cada mutacion.** Como el **reemplazo de UNA linea por otra**, nunca como dos lineas consecutivas marcadas ANTES/DESPUES: aplicado literalmente, un par de `return` consecutivos deja el segundo inalcanzable y la mutacion es un no-op -- la suite queda verde y la auditoria registra "ninguna prueba en rojo" para una regla que **si** esta guardada, con lo cual el implementador se pone a escribir tests duplicados de reglas ya cubiertas.

Comando de suite que se usa en todos los pasos (abreviado `SUITE` de aca en adelante):

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
PYTHONPATH=. C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m pytest tests/test_catalog_record.py tests/test_catalog_source.py tests/test_catalog_boundaries.py tests/test_catalog_cli.py -q
```

- [ ] **Step 1: Baseline en verde y arbol limpio**

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
git status --short          # tiene que estar vacio
```

Correr SUITE: tiene que dar cero fallos. Si hay algo sin commitear, commitearlo antes: si no, el `git checkout --` de reversion se lleva trabajo real.

- [ ] **Step 2: Mutaciones 1 a 4**

**M1 -- Ignorar `currency_minor_unit`.** En `src/catalog/record.py`, dentro de `scale_price`, **reemplazar la linea**

```python
    scaled = amount.scaleb(-int(minor_unit))
```

**por**

```python
    scaled = amount
```

Correr SUITE. Rojo esperado (>= 1): `test_scale_price_scales_by_minor_unit_and_keeps_the_raw_string_as_evidence`, y ademas `test_woo_price_on_the_real_fixture_honours_that_stores_own_minor_unit` si la tienda grabada declara `currency_minor_unit != 0`, y `test_shopify_price_uses_the_named_minor_unit_constant_never_an_inference` si `PRICE_MINOR_UNIT == 2`. Revertir:

```bash
git checkout -- src/catalog/record.py
```

**M2 -- Asumir `currency_minor_unit = 2` cuando falta.** En `src/catalog/record.py`, dentro de `scale_price`, **reemplazar la linea**

```python
        reasons.append(UNDECLARED_MINOR_UNIT_AUSENTE)
```

(la del `if not unit_ok:`) **por**

```python
        minor_unit, unit_ok = 2, True
```

Correr SUITE. Rojo esperado: `test_scale_price_without_minor_unit_is_undeclared_and_never_assumes_two`, `test_scale_price_rejects_a_minor_unit_that_is_not_a_non_negative_int`, `test_woo_row_without_minor_unit_emits_the_row_with_the_price_undeclared`, `test_dropped_and_undeclared_reasons_are_summed_across_pages`. Revertir:

```bash
git checkout -- src/catalog/record.py
```

**M3 -- Tratar el 403 como `sin_feed_json`.** En `src/catalog/source.py`, dentro de `_report_without_feed`, **reemplazar la linea**

```python
        return ReadReport(outcome="bloqueado_waf", routes_probed=probe.probed)
```

(la del `if statuses & DENIAL_STATUSES:`) **por**

```python
        return ReadReport(outcome="sin_feed_json", routes_probed=probe.probed)
```

Correr SUITE. Rojo esperado: `test_a_403_on_any_route_yields_bloqueado_waf_never_sin_feed_json`, `test_a_403_verdict_never_depends_on_the_response_body`, `test_the_home_is_not_read_when_a_403_already_decided_the_outcome`, `test_a_report_is_emitted_on_all_five_no_read_paths`, `test_every_denial_status_is_bloqueado_waf_never_plataforma_desconocida`. Revertir:

```bash
git checkout -- src/catalog/source.py
```

**M4 -- Cortar el sondeo al primer 404.** En `src/catalog/source.py`, dentro de `_probe_routes`, **reemplazar la linea** `continue` del bloque

```python
        if response.status != 200:
            continue
```

**por** `break`.

Correr SUITE. Rojo esperado: `test_probing_does_not_stop_at_the_first_404`, `test_probing_walks_the_routes_in_order_one_to_four`, `test_every_attempt_is_recorded_successful_or_not`, `test_a_403_does_not_cut_the_probe_when_a_later_route_delivers`, `test_four_404s_trigger_exactly_one_home_read`, `test_one_rate_limit_wait_before_every_request_except_the_first`. Revertir:

```bash
git checkout -- src/catalog/source.py
```

- [ ] **Step 3: Mutaciones 5 a 7, y la asimetria de la propiedad**

**M5 -- Ante dos claves en conflicto, quedarse con la primera.** En `src/catalog/record.py`, dentro de `resolve`, **reemplazar la linea**

```python
    if len({reading for _, _, reading in usable}) > 1:
```

**por**

```python
    if False:  # mutacion 5
```

(asi la rama del conflicto queda inalcanzable sin tocar la indentacion, y el `key, raw, reading = usable[0]` de abajo se ejecuta siempre).

Correr SUITE. Rojo esperado: `test_resolve_arbitration_never_lets_the_first_key_win_a_disagreement` y `test_woo_stock_conflict_is_contradicho_not_first_wins`.

**Y verificar la asimetria, que es el punto:** correr solo la propiedad con la mutacion puesta:

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
PYTHONPATH=. C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m pytest tests/test_catalog_record.py -q -k evidence_appears_literally
```

Tiene que quedar **VERDE**: elegir la primera senal produce una evidencia literal valida, asi que la propiedad no atrapa el arbitraje silencioso. Anotar esa asimetria en la tabla del PR: es la razon por la que la guarda del arbitraje es un test aparte. Revertir:

```bash
git checkout -- src/catalog/record.py
```

**M6 -- Descartar la fila cuando falta el precio.** En `src/catalog/adapters/woo.py`, dentro de `to_rows`, **insertar** estas dos lineas inmediatamente despues de la que calcula `currency`:

```python
        if price.state != "declarado":
            continue
```

Correr SUITE. Rojo esperado: `test_woo_row_without_a_price_is_emitted_never_dropped`, `test_woo_row_without_minor_unit_emits_the_row_with_the_price_undeclared`, `test_a_missing_price_is_an_undeclared_attribute_and_a_missing_title_is_a_dropped_row`, `test_dropped_and_undeclared_reasons_are_summed_across_pages`, `test_nothing_is_lost_without_being_counted`, `test_nothing_is_lost_without_being_counted_across_the_whole_corpus`. Revertir:

```bash
git checkout -- src/catalog/adapters/woo.py
```

**M7 -- Paginar sin tope cuando falta `X-WP-TotalPages`.** En `src/catalog/source.py`, dentro de la version final de `_read_pages` (la de la Tarea 13), **reemplazar la linea**

```python
    last_page = MAX_PAGES if total is None else min(total, MAX_PAGES)
```

**por**

```python
    last_page = 10**9 if total is None else total
```

Correr SUITE. Rojo esperado: `test_woo_without_the_header_stops_at_the_hundred_page_cap`, `test_a_header_beyond_the_cap_is_honoured_only_up_to_the_cap_and_declared`, `test_shopify_also_stops_at_the_hundred_page_cap`, `test_any_truncation_degrades_the_outcome_never_stays_ok`. La suite no se cuelga porque las fixtures del paginado programan mas paginas de las que el tope permite y, pasadas esas, el fetcher falso devuelve 404, que corta la lectura como pagina fallida. Revertir:

```bash
git checkout -- src/catalog/source.py
```

- [ ] **Step 4: Mutaciones 8 a 11 -- las reglas que agrega este plan**

Las cuatro reglas nuevas del plan tambien tienen que estar guardadas. Mismo formato: reemplazo de una linea, una por vez, revertir.

**M8 -- Afirmar el factor de precio de Shopify (C12).** En `src/catalog/adapters/shopify.py`, **reemplazar la linea** `PRICE_MINOR_UNIT: int | None = ...` por `PRICE_MINOR_UNIT: int | None = 2`.

Correr SUITE. Rojo esperado: `test_the_shopify_price_factor_is_never_asserted_as_two`, `test_shopify_price_minor_unit_is_never_two_and_matches_the_committed_decision`. Sin esta guarda, cambiar el factor deja la suite entera verde y el bug de 100x se squash-mergea. Revertir:

```bash
git checkout -- src/catalog/adapters/shopify.py
```

**M9 -- Reportar `ok` con la lectura truncada (C2).** En `src/catalog/source.py`, dentro de `read_catalog`, **reemplazar la linea**

```python
    outcome: ReadOutcome = "error_red" if truncations else "ok"
```

**por**

```python
    outcome: ReadOutcome = "error_red" if TRUNCATION_PAGINA_FALLIDA in truncations else "ok"
```

Correr SUITE. Rojo esperado: `test_any_truncation_degrades_the_outcome_never_stays_ok`, `test_shopify_repeating_the_first_page_cuts_the_read_and_is_declared`, `test_a_declared_row_total_that_does_not_match_is_declared_as_a_truncation`. Revertir con `git checkout -- src/catalog/source.py`.

**M10 -- Tratar un 429 como `plataforma_desconocida` (C13).** En `src/catalog/source.py`, **reemplazar la linea** `DENIAL_STATUSES: frozenset[int] = frozenset({401, 403, 407, 429, 451})` por `DENIAL_STATUSES: frozenset[int] = frozenset({403})`.

Correr SUITE. Rojo esperado: `test_every_denial_status_is_bloqueado_waf_never_plataforma_desconocida` (los casos 401, 407, 429, 451). Revertir con `git checkout -- src/catalog/source.py`.

**M11 -- Aceptar la cadena vacia como valor declarado (C14).** En `src/catalog/record.py`, dentro de `is_present`, **reemplazar la linea**

```python
        return bool(raw.strip())
```

**por**

```python
        return True
```

Correr SUITE. Rojo esperado: `test_resolve_treats_a_blank_or_container_raw_as_absent_never_as_declared`, `test_is_present_is_the_single_definition_of_presence`, `test_woo_sku_published_as_an_empty_string_is_undeclared_not_declared`, `test_no_declared_attribution_in_the_corpus_carries_a_blank_value`. Revertir con `git checkout -- src/catalog/record.py`.

- [ ] **Step 5: Confirmar que no quedo ninguna mutacion puesta**

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
git status --short          # vacio
git diff                    # vacio
```

Correr SUITE: cero fallos. Y el lint:

```bash
C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m ruff check src tests examples
```

Si `git diff` no esta vacio, hay una mutacion sin revertir: revertirla antes de seguir. Nada de esta tarea se commitea.

- [ ] **Step 6: Cerrar la tabla de mutaciones para el PR**

Completar la tabla en el scratchpad con los nombres REALES de los tests que se pusieron rojos (no los esperados de este plan: los observados), en este formato:

```markdown
## Verificacion por mutacion

| # | Mutacion | Tests en rojo (observados) |
|---|---|---|
| 1 | Ignorar currency_minor_unit | ... |
| 2 | Asumir currency_minor_unit = 2 cuando falta | ... |
| 3 | Tratar el 403 como sin_feed_json | ... |
| 4 | Cortar el sondeo al primer 404 | ... |
| 5 | Ante dos claves en conflicto, quedarse con la primera | ... |
| 6 | Descartar la fila cuando falta el precio | ... |
| 7 | Paginar sin tope cuando falta X-WP-TotalPages | ... |
| 8 | Afirmar el factor de precio de Shopify (C12) | ... |
| 9 | Reportar ok con la lectura truncada (C2) | ... |
| 10 | Tratar un 429 como plataforma_desconocida (C13) | ... |
| 11 | Aceptar la cadena vacia como valor declarado (C14) | ... |

Asimetria registrada: con la mutacion 5 puesta, la propiedad de evidencia literal
queda VERDE y solo se rompe la guarda del arbitraje. Evidencia inventada y
arbitraje silencioso son dos fallas distintas y cada una necesita su guarda.
```

Las once filas tienen que tener al menos un test. Una fila vacia es un agujero de cobertura de reglas y **bloquea el PR**: hay que escribir el test que falta antes de seguir a la ultima tarea.

---

### Task 20: Cierre -- suite completa, cobertura, y draft PR con las notas obligatorias

**Files:**
- Modify: ninguno de codigo. Esta tarea audita y publica.

**Interfaces:**
- Consumes: todo lo de las Tareas 1-19, la tabla de once mutaciones de la Tarea 19 y las cuatro salidas de campo de la Tarea 18.
- Produces: el draft PR.

- [ ] **Step 1: Suite completa con cobertura, igual que CI**

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
PYTHONPATH=. C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m pytest tests/ -q --cov=src --cov-report=term-missing --cov-fail-under=80
```

Salida esperada: cero fallos y el gate de cobertura satisfecho. Mirar especificamente las lineas de `src/catalog/*` en el reporte `term-missing`: son codigo nuevo y cuentan contra el 80% global. Si alguna rama de `src/catalog/` aparece sin cubrir, agregar el test que falta en el archivo que corresponda (no bajar el gate, no agregar `# pragma: no cover`).

```bash
C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m ruff check src tests examples
```

Salida esperada: `All checks passed!`.

- [ ] **Step 2: Verificar que ningun test toca la red, y que el repo principal quedo intacto**

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
PYTHONPATH=. C:/Users/Gamer/Music/scm/supply-chain-optimization/.venv/Scripts/python.exe -m pytest tests/test_catalog_record.py tests/test_catalog_source.py tests/test_catalog_boundaries.py tests/test_catalog_cli.py -q
```

`test_catalog_record.py` y `test_catalog_source.py` corren con la guarda autouse de socket puesta: si algo intentara conectar, el fallo dice `a catalog test tried to open a real socket`. Cero fallos = ningun test toca la red. Los de frontera y de CLI son lectura de archivos y funciones puras: no hay cliente HTTP en ninguno de los dos.

```bash
git -C C:/Users/Gamer/Music/scm/supply-chain-optimization status --short
```

Salida esperada: sin cambios atribuibles a esta corrida. El repo principal esta en otra rama con trabajo concurrente ajeno y solo se uso su `.venv`.

- [ ] **Step 3: Auditar el diff contra lo que el spec permite**

El paso no es visual: se compara contra una lista escrita, porque los nueve fixtures son precisamente los archivos que un grabado puede ensuciar (un payload sin recortar, un fixture de prueba que quedo, un `.json` extra que despues rompe la exclusividad de adaptador de la Tarea 15).

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
SCRATCH=C:/Users/Gamer/AppData/Local/Temp/claude/C--Users-Gamer-Music-scm/f7886b41-e072-4a3d-84da-8dd79cac168f/scratchpad
cat > $SCRATCH/expected-files.txt <<'EOF'
docs/superpowers/plans/2026-07-25-lector-catalogo-publico.md
docs/superpowers/specs/2026-07-25-lector-catalogo-publico-design.md
examples/run_catalog_read.py
src/catalog/__init__.py
src/catalog/adapters/__init__.py
src/catalog/adapters/shopify.py
src/catalog/adapters/woo.py
src/catalog/record.py
src/catalog/source.py
tests/fixtures/catalog/DECISIONS.json
tests/fixtures/catalog/PROVENANCE.md
tests/fixtures/catalog/home_generic.html
tests/fixtures/catalog/home_jumpseller.html
tests/fixtures/catalog/shopify_products.json
tests/fixtures/catalog/waf_403.txt
tests/fixtures/catalog/woo_empty_page.json
tests/fixtures/catalog/woo_indexphp_route.json
tests/fixtures/catalog/woo_store_route.json
tests/fixtures/catalog/woo_v1_route.json
tests/test_catalog_boundaries.py
tests/test_catalog_cli.py
tests/test_catalog_record.py
tests/test_catalog_source.py
EOF
git diff --stat origin/main...HEAD
git diff --name-only origin/main...HEAD | sort | diff -u $SCRATCH/expected-files.txt -
```

`diff` tiene que salir vacio (exit 0). **Cualquier linea de diferencia bloquea el PR**: un archivo de mas es scope creep y sale, uno de menos es un entregable que falta. En particular **no** puede aparecer: nada de lexico, nada de decretos, ningun informe de distribuidoras, nada de outreach, ni `.github/workflows/*`, ni `pyproject.toml`. Las tiendas chilenas entraron solo como dataset de validacion del lector.

- [ ] **Step 4: Abrir el draft PR con las notas obligatorias**

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
git push -u origin feat/lector-catalogo-publico
gh pr create --draft --title "feat(catalog): lector de catalogo publico (Woo + Shopify)" --body-file C:/Users/Gamer/AppData/Local/Temp/claude/C--Users-Gamer-Music-scm/f7886b41-e072-4a3d-84da-8dd79cac168f/scratchpad/pr-body.md
```

El cuerpo del PR (`pr-body.md`) tiene que traer las cuatro notas, sin excepcion:

```markdown
## Que es

Componente 1 de la maquina: lee el catalogo publico de una tienda Woo o Shopify a
partir de su URL y devuelve filas canonicas donde cada valor carga de que clave
del feed salio, mas un informe que declara siempre lo que no se pudo leer.
Implementa `docs/superpowers/specs/2026-07-25-lector-catalogo-publico-design.md`
segun `docs/superpowers/plans/2026-07-25-lector-catalogo-publico.md`.

Tres estados por valor: `declarado` / `no_declarado` / `contradicho`. Un campo que
el feed no dice queda `no_declarado`; nunca se rellena con el valor mas probable.
Dos claves que mapean al mismo atributo y no coinciden dan `contradicho`; nunca
gana la primera.

## Constatacion empirica (spec 7.2)

- Peso en Woo: <SI/NO trae peso; clave mapeada>. La unidad no viaja en el payload
  de la Store API y `formatted_weight` no se parsea nunca (cadena localizada que
  devuelve el texto traducido de "N/A" cuando el peso no esta seteado).
  La unidad NO viaja en el payload: `weight_declared` en Woo es una magnitud sin
  unidad declarada y el consumidor no puede tratarla como kg (queda escrito en
  `DECISIONS.json` como `woo_weight_unit_declared: false`).
- Peso en Shopify: clave `<...>`; las otras claves de peso se dejan sin leer a
  proposito (unidades distintas).
- Unidad menor del precio en Shopify: `<0 | no declarada>`. El factor no viaja en
  el payload, asi que el valor `2` esta prohibido por test: afirmar un factor de
  plataforma desde una sola tienda es el bug de 100x hecho diseno. Con `0`, la
  evidencia es doble -- la forma del propio valor (string con separador decimal) y
  el precio mostrado en la ficha publica de `<handle>` -- y el precio se afirma
  solo sobre valores de esa misma forma. Sin eso queda `no_declarado` con
  `MINOR_UNIT_AUSENTE` contado, igual que Woo sin `currency_minor_unit`.

## Decisiones que el plan cerro sobre el spec (C1-C17)

Las cuatro que cambian comportamiento observable y merecen revision explicita:
`C12` (factor de precio de Shopify: 0 o None, nunca 2), `C2` (cualquier
truncamiento degrada el `outcome`), `C13` (familias de status: la familia de
denegacion entera es `bloqueado_waf`, y lo que no es 200 ni 404 es `error_red`),
`C14` (una clave presente pero vacia -- el `"sku": ""` de Woo -- cuenta como
ausente). Las cuatro tienen su mutacion en la tabla de abajo (M8-M11).

## Cobertura declarada POR PLATAFORMA

| Plataforma | Estado | Como |
|---|---|---|
| WooCommerce | cubierta | Store API, tres variantes de ruta (`wc/store/v1`, `wc/store`, `index.php/wc/store`) |
| Shopify | cubierta | `/products.json?limit=250` |
| Jumpseller | reconocida, sin feed JSON | huella en la home -> `sin_feed_json` con la plataforma nombrada |
| Bsale | reconocida, sin feed JSON | idem |
| cualquier otra | `plataforma_desconocida` | ningun feed y ninguna huella |

Un 403 sin feed en ninguna ruta da `bloqueado_waf`, jamas `sin_feed_json`.

## Validacion en campo (cuatro tiendas, fuera de CI)

| Tienda | Plataforma detectada | outcome | pages_read | rows_emitted | rows_declared |
|---|---|---|---|---|---|
| <url 1, congelados CL, Woo> | woo | | | | |
| <url 2, congelados CL, Shopify> | shopify | | | | |
| <url 3, otro rubro> | | | | | |
| <url 4, otro pais> | | | | | |

El gate de `robots.txt` lo corre el propio CLI antes de pedir cualquier ruta de
catalogo (D7: el gate es del llamador). Una tienda rechazada por el gate sale con
exit 2 y se reemplaza, anotandolo.

Salidas completas de `examples/run_catalog_read.py --url ...`:

<las cuatro salidas, textuales>

## Verificacion por mutacion

<la tabla de once filas de la penultima tarea, con los tests rojos observados,
incluida la nota de la asimetria de la mutacion 5 contra el test de propiedad>

## Lo que este PR NO hace

- No interpreta semantica de dominio (guarda de vocabulario en
  `tests/test_catalog_boundaries.py`).
- No compara el peso del feed contra el peso del titulo: carga las dos fuentes,
  decidir es del normalizador de peso, que no esta en esta rama. El enganche
  existente (`feed_weight_kg` -> `CONTRADICCION_FUENTE` con tolerancia del 2%) no
  se redisena aca.
- No lee HTML de fichas ni de listados. La unica lectura de HTML es una sola de
  la home, para huella de plataforma, solo cuando ninguna ruta fue denegada ni
  fallo, y queda declarada en `routes_probed` como `("/", status)`.
- `src/catalog/` no aplica ningun gate legal ni de techo de adquisicion: es del
  llamador, y el llamador de esta rama (`examples/run_catalog_read.py`) **si** lo
  aplica -- lee el `robots.txt` de la tienda y aborta con exit 2 si prohibe alguna
  ruta de sondeo. Hay guarda de test en las dos direcciones: cero apariciones de
  `robots` en el paquete, y el gate presente en el CLI.
- El paquete no importa nada del paquete de precios del repo. (El patron de
  `__init__.py` sin submodulos se copio de `src/pricing_intel/acquire/__init__.py`;
  la referencia va aca y no en el fuente, porque la guarda de acoplamiento escanea
  tambien los docstrings.)
- No escribe en ningun sistema de registro. 100% lectura.
- No toca el ledger de precios ni la cascada de homologacion, y no importa nada
  del paquete de precios del repo.
- No extiende el lexico, no agrega decretos, no termina ningun informe y no
  escribe nada de outreach. Eso sigue archivado.

## Test plan

- [x] `pytest tests/ -q --cov=src --cov-fail-under=80` con `PYTHONPATH=.`
- [x] `ruff check src tests examples`
- [x] Ningun test toca la red (guarda de socket autouse en los dos archivos que
      ejercitan el lector; los otros dos son lectura de archivos y funciones puras)
- [x] Las siete mutaciones del spec, mas las cuatro que agrega el plan (C12, C2,
      C13, C14), ponen rojo al menos un test cada una
- [x] Cuatro tiendas reales leidas con el CLI, con el gate de robots.txt corrido
      por el propio CLI
- [ ] CI verde en py3.11 / py3.12 / py3.13 antes de marcar ready
```

- [ ] **Step 5: CI verde y squash-merge**

```bash
cd C:/Users/Gamer/Music/scm/.wt-radiografia-cl
gh pr checks --watch
```

Cuando los checks estan verdes (incluido el job `prod-boot`, que instala solo
`.[web,mcp]` e importa `webapp.app`: es el que atrapa un import de extra opcional
en la cadena de arranque), marcar ready y squash-mergear:

```bash
gh pr ready
gh pr merge --squash --delete-branch
```

Nunca push directo a `main`. Si `prod-boot` falla, la causa casi seguro es que
`src/catalog/__init__.py` dejo de estar vacio de imports: la guarda
`test_the_package_init_imports_no_submodule_so_prod_boot_stays_safe` deberia
haberlo atrapado antes, y si no lo hizo, ampliarla en el mismo PR.
