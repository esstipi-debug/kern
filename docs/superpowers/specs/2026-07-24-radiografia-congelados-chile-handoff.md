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

## Pendiente, en orden

1. **Plan de implementacion** con `superpowers:writing-plans` apuntando al spec.
   Se descompone en 3 PRs: (1) lectores de feed + normalizador CLP/kg + lexico
   -> refs CSV, con CLI `examples/run_cl_catalog_scan.py`; (2) dataset de vedas
   + `veda_calendar.py` + integracion con `risk_period.py`; (3) job
   `market_radiography` registrado como herramienta, tocando los 7 lugares
   fijados incluido `citation_gate.TOOL_CONCEPTS`.
2. **One-pager de la propuesta B+C** en espanol, dirigido al dueno.
3. **Lista de prospectos estrategicos**: habia un agente de investigacion
   corriendo al cierre de la ventana anterior buscando 15-25 distribuidoras
   chilenas adicionales que calcen con el ICP, con score de encaje, feed
   publico y decision-maker. Si no quedo su resultado, relanzarlo.

## Recordatorio

Producir el informe y **enviarselo** a las empresas analizadas son decisiones
distintas. Lo segundo esta fuera de alcance hasta que el usuario lo pida
explicitamente.
