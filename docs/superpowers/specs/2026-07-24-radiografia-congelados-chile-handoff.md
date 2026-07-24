# Handoff - Radiografia del congelado chileno

**Fecha:** 2026-07-24 · **Para:** la siguiente ventana, arrancando en frio.

## Estado en una linea

Diseno aprobado y commiteado. **Cero codigo escrito.** Falta el plan de
implementacion, el one-pager de la propuesta de entrada, y cerrar la lista de
prospectos.

## Donde esta todo

| Que | Donde |
|---|---|
| Worktree | `C:\Users\Gamer\Music\scm\.wt-radiografia-cl` |
| Rama | `feat/radiografia-congelados-cl`, basada en `origin/main` |
| Commit | `0fd00c4` |
| Spec | `docs/superpowers/specs/2026-07-24-radiografia-congelados-chile-design.md` |
| Repo principal | `C:\Users\Gamer\Music\scm\supply-chain-optimization` (esta en `feat/scm-books-khan-resilience` con trabajo concurrente ajeno - **no tocar**) |

## Que se decidio

1. **Alcance:** radiografia sectorial de **5 sujetos**, no informe de una
   empresa. Precio + calendario de vedas juntos en un solo entregable.
   Sujetos: Rapa-Nui, Pulpo Chile, TodoFrio, Del Mar A Tu Hogar, Mariscos De La
   Concha. Set de referencia (solo aporta precios): Frozt, South Wind, IceFish,
   Mardeli.
2. **Oferta de entrada: B + C.** B = auditoria pagada de **CLP $390.000**,
   entrega en 5 dias habiles, con garantia de "si no encuentro 3% de brecha
   accionable, no cobro". C = suscripcion **$180.000/mes** del escudo de veda.
   B es la puerta, C es el destino. Se descartaron el piloto gratis (no prueba
   disposicion a pagar) y el fee contingente (exige sus datos antes de que
   confien).
3. **Estilo de respuesta:** el usuario rechazo las respuestas de prosa larga.
   Liderar con visual (`show_widget`), opciones dentro del grafico, 3-6 lineas
   de texto alrededor. Ver memoria `graphical-answers-not-prose`.

## Lo que NO hay que volver a investigar

Costo tres agentes de investigacion. Todo esta verificado el 2026-07-24.

**Rapa-Nui no es lo que sugiere el nombre.** Distribuidora de alimentos
congelados HORECA, RUT 79.969.260-0, SII desde 1993, 3 sucursales (Santiago,
Nunoa, Villarrica), flota refrigerada propia. Proveedor ChileCompra de FACh,
Ejercito, Gendarmeria y SERVIU: 23 OC por ~$41M CLP, **ultima adjudicacion
feb-2025** (17 meses sin ganar nada nuevo).

**Los catalogos son abiertos.** WooCommerce Store API
(`wp-json/wc/store/v1/products`) en Rapa-Nui (96 SKUs), Pulpo Chile (44),
TodoFrio (85), Del Mar (41). Shopify `/products.json` en De La Concha (165),
South Wind (191), Frozt. JSON-LD en IceFish, Mardeli, Jumbo, Santa Isabel,
Tottus. El JSON-LD de Rapa-Nui declara `valueAddedTaxIncluded: true`, asi que la
comparacion no tiene el problema de neto vs bruto.

**Hallazgos comerciales de Rapa-Nui:**
- Precios retail **13% bajo la mediana** de competidores online (8 de 10 SKUs de
  la muestra). El retail publico canibaliza su propio mayorista: un restaurante
  compra al detalle mas barato que pidiendo cotizacion. Excepcion: reineta a
  **+21%**, justo donde el consumidor compara mas facil.
- **Peso variable unidireccional**: si pesa de mas, el cliente transfiere la
  diferencia por correo antes del despacho; si pesa de menos, **no hay politica
  publicada**. El schema no tiene campo `weight`.
- Despacho $5.990, gratis sobre $59.990, con flota propia - calibrado antes del
  alza de diesel de **+$580/L del 26-mar-2026** (mayor alza semanal en 36 anos;
  ~$1.302/L hoy) y de la tarifa electrica **+4,9%** de jul-2026.
- **Villarrica** es sucursal fisica sin despacho online, mientras hacen SEO y
  hashtags a Panguipulli, Licanray y Conaripe.
- Cero marca o proveedor declarado en los 96 SKUs. Sin `brand`, sin `gtin`.
  Casing de SKU inconsistente (`MAR1401` vs `Mar1004` vs `pes1903`).

**Vedas que tocan su catalogo** (decretos, no scraping):
`loco` extractiva DEXE202600007 hasta dic-2028 · `ostion del norte` extractiva
DEXE202200104 hasta ago-2027 + marea roja Tongoy ene-2026 · `merluza austral`
biologica 1-31 ago · `merluza comun` biologica 1-30 sep + **cuota 2026 -15%**
(DEXE202500260) · `camaron nailon` 1-30 sep, permanente desde jul-2025
(DEXE202500128) · `langostinos` ene-feb y sep · `chorito` 1 nov-31 dic ·
`macha` Valpo/O'Higgins/Maule **vence 2026-08-02 sin renovacion en el registro**.

Una veda **extractiva** prohibe tambien almacenar y comercializar, no solo
pescar. El informe reporta **exposicion y obligacion documental** (arts. 63/65
LGPA), **nunca infraccion** - la exencion AMERB existe y su cumplimiento no es
observable desde afuera.

**Licencias y robots.txt, ya resueltos:**
- Usar: feeds de los 9 sitios, decretos SUBPESCA (normativa publica), Aduanas
  registro de importacion (`odc-by`), SERNAPESCA desembarques (CC-BY), ODEPA
  (CC-BY).
- **No usar:** los 16 datasets SUBPESCA en datos.gob.cl (CC BY-NC, sin uso
  comercial) · crawling de sernapesca.cl (robots.txt bloquea `anthropic-ai` y
  `Claude-Web`) · MercadoLibre (robots.txt nombra a `ClaudeBot`) · Unimarc y
  Lider (bloqueo Akamai duro).
- INE IPC es CC BY-SA 4.0: share-alike contamina derivados, cuidado.

## Trampas tecnicas descubiertas

1. **`extract_pack_size` en `normalize.py` solo entiende conteo** (`6-pack`,
   `caja de 12`), no formatos de peso. Sin un normalizador a CLP/kg, la reineta
   de TodoFrio a $10.090 parece mas barata que la de Rapa-Nui a $11.768 cuando
   en realidad es de 500g, o sea $20.180/kg. **Es el punto unico de falla del
   diseno.**
2. **No hay un solo GTIN en este mercado.** Homologacion sobre texto libre en
   espanol; hace falta el lexico es-CL (especie, calibre 36/40, crudo/cocido,
   apanado/IQF).
3. **Los slugs de Frozt estan corridos.** `/products/camaron-26-30-crudo-...`
   sirve "Camaron 36/40 cocido". Leer siempre `title` del feed, nunca el slug.
4. **Jumbo emite JSON-LD con `"price": "undefined"`** en soft-404. Validar que
   el precio sea numerico, no solo que el bloque exista.
5. **A Cuenta** escapa los precios dentro del stream RSC; un `grep '"price"'`
   plano no devuelve nada y parece renderizado en cliente.

## Ventana 2 (2026-07-24, tarde) - lo que se hizo

**El plan de implementacion existe pero esta INCOMPLETO.**
`docs/superpowers/plans/2026-07-24-radiografia-congelados-chile.md` tiene
escrito: header + Global Constraints + las 4 desviaciones del spec (D1-D4, ver
abajo) + File Structure + PR-1 Task 0/1/2 completos con codigo y tests.
**Falta escribir:** PR-1 Tasks 3-6, PR-2 completo, PR-3 completo, y la
seccion Self-Review del skill. Cero codigo de produccion escrito todavia.

### Contratos ya verificados - NO volver a explorar el codigo

Costo tres agentes Explore con dumps verbatim. Todo esta en el plan, en
`## Spec deviations` y `## Global Constraints`. Resumen de lo critico:

- **D1: `src/risk_period.py` NO modela ventanas de riesgo.** Son 49 lineas de
  matematica escalar de Vandeput (`demand_over_risk_period(...) ->
  RiskPeriodStats`), sin fechas ni intervalos, con 6 callers. El spec se
  equivoca. `veda_calendar.py` va autocontenido; no se toca `risk_period.py`.
- **D2: el matching cruzado era matematicamente imposible.**
  `ProductAttributes.__post_init__` (`match/fuzzy.py:67`) exige `brand` no
  vacio y ninguno de los 9 sitios declara marca. Peor: con
  `score = 0.55*title + 0.15*brand + 0.30*attr` y `CONFIRM_THRESHOLD = 0.96`,
  si `brand_sim = 0` el techo es 0.85 -> todo cae en `suspect` -> `adjudicate`
  con `llm=None` -> `deferred`. **La matriz habria salido vacia.** Solucion:
  sentinela `NO_BRAND_DECLARED = "sin marca declarada"` identico en las 9
  fuentes (mueve todos los pares igual, no altera ningun ranking; es la misma
  disciplina del `1.0` neutro de `_attribute_similarity`).
- **D3: alimentar refs CSV a `price_intelligence` re-descarga cada PDP**
  (~700 fetches, 9 `config/sites/*.yaml` aprobados) para re-aprender precios
  que el feed ya dio en 9 requests. Y **no hay modulo de matriz reusable** -- la
  matriz vive dentro de `jobs/price_intelligence.py`, acoplada a la
  adquisicion. Solucion: PR-3 calcula clusters/mediana/brecha en un modulo
  puro nuevo `src/market_radiography.py`; el refs CSV queda como subproducto
  para la suscripcion fase 2. El job registrado lee un CSV y es offline.
- **D4: `DECISIVE_ATTRIBUTE_KEYS = ("pack_size","size","model","capacity","color")`**
  (`match/probabilistic.py:103`) no incluye especie/calibre/estado/proceso.
  Hay que extenderlo; es aditivo y seguro (solo compara claves presentes en
  ambos lados, y ninguna oferta actual las lleva).
- **Anclas de citacion para `market_radiography`, YA VERIFICADAS por BFS sobre
  el grafo committeado (2026-07-24):**
  `("competition_oriented_pricing", "price_positioning", "regulated_supply_chain_environment")`
  -> clausura de 54 nodos, **cero magnets** (grado>50), asi que **no hace falta
  entrada en `EXCLUDED_CONCEPTS`**. Descartada
  `price_transparency` (arrastra `book_nagle` como magnet).
- **Los "7 lugares fijados" reconstruidos** (no existen listados en ningun
  archivo; se derivaron del commit `a2b137f` de `disruption_scan`):
  1. `jobs/<x>_job.py` nuevo · 2. bloque import en `scm_agent/tools.py:32`
  (alfabetico) · 3. `_<x>_prepare`/`_<x>_run`/`<x>_tool()` en `tools.py` ·
  4. `reg.register(...)` en `build_default_registry()` (`tools.py:2571`) ·
  5. `scm_agent/citation_gate.py:54` `TOOL_CONCEPTS` ·
  6. `src/i18n.py:260` `TOOL_TITLES` (es != en, hay test) ·
  7. `tests/test_scm_agent.py:123` (set de keys) **y**
  `tests/test_price_watch_tool.py:165` (bump 46 -> 47).
  **No tocar** `webapp/how_it_works_data.py` (congelado en 41 por 3 tests) ni
  `webapp/mcp_tool_specs.py` (exposicion MCP diferida, mismo criterio que
  `launch_readiness`).
- **`config/**` NO va en el wheel** (sin `MANIFEST.in`, `packages = ["src"]`).
  Se lee relativo al CWD con override por env, patron de
  `acquire/base.py:55-57`.
- **`normalize_price_string` exige `currency="CLP"` explicito**: `"$"` no esta
  en `_UNAMBIGUOUS_SYMBOLS`, asi que sin el kwarg levanta excepcion.
- El cliente HTTP de `pricing_intel` es una **funcion sync**, no una clase:
  `acquire/pdp_fetcher.py:66` `fetch_pdp_html(url, *, client: httpx.Client, ...)
  -> RawObservation | FetchError`. `CatalogRecord` no existe; hay que crearlo.

## Pendiente, en orden

1. **Terminar el plan de implementacion** (PR-1 Tasks 3-6, PR-2, PR-3 +
   Self-Review). El archivo ya existe y arranca bien; solo hay que continuar
   apendando con el mismo formato de pasos TDD bite-sized.
2. **One-pager de la propuesta B+C** en espanol, dirigido al dueno.
3. **Lista de prospectos estrategicos**: se relanzo el agente en esta ventana.
   Escribe en `docs/gtm/2026-07-24-prospectos-congelados-cl.md` (15-25
   distribuidoras con score, feed publico verificado, especies en veda,
   ultima adjudicacion ChileCompra y decision-maker solo si esta publicado).
   **Revisar si el archivo existe antes de relanzar.**

## Recordatorio

Producir el informe y **enviarselo** a las empresas analizadas son decisiones
distintas. Lo segundo esta fuera de alcance hasta que el usuario lo pida
explicitamente.
