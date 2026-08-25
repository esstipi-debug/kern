# Radiografia del congelado chileno - diseno

**Fecha:** 2026-07-24
**Estado:** diseno aprobado, pendiente de plan de implementacion
**Rama base:** `origin/main` (no `feat/scm-books-khan-resilience`, que es donde se escribio esto)

## Problema

Kern tiene 45 herramientas y cero clientes. La friccion de arranque de todo
paquete actual es la misma: pedirle datos internos a una empresa que todavia no
confia en nosotros. Este diseno elimina esa friccion para un segmento concreto.

El segmento: **distribuidoras chilenas de alimentos congelados que venden a
HORECA** (restaurantes, hoteles, banqueteros, instituciones) y ademas tienen
tienda online. Su catalogo completo -- SKU, precio en CLP, disponibilidad -- es
publicamente legible sin credenciales ni permiso, porque corren WooCommerce o
Shopify con sus APIs de tienda abiertas.

Entregable: una **radiografia sectorial** que compara a 5 empresas entre si y
contra un set de referencia, y superpone el calendario legal de vedas sobre el
catalogo real de cada una. Cero datos del cliente. Un solo pipeline, cinco
prospectos.

## Alcance

### Sujetos (5, cada uno recibe su informe)

| Empresa | Feed | SKUs | Rol |
|---|---|---|---|
| Distribuidora Rapa-Nui | `wp-json/wc/store/v1/products` | 96 | caso ancla, HORECA + retail |
| Pulpo Chile | WooCommerce Store API | 44 | analogo estructural mas cercano (HORECA) |
| TodoFrio | WooCommerce Store API | 85 | mixto |
| Del Mar A Tu Hogar | WooCommerce Store API | 41 | retail online |
| Mariscos De La Concha | Shopify `/products.json` | 165 | retail online, catalogo ancho |

### Set de referencia (solo aporta precios, no recibe informe)

Frozt (Shopify, `/products.json`), South Wind (Shopify, 191 SKUs), IceFish
(Jumpseller, JSON-LD), Mardeli (JSON-LD). Se suman al universo de comparacion
para que la mediana de mercado tenga masa, sin convertirlos en sujetos.

### Explicitamente fuera

- **Unimarc y Lider**: bloqueo duro de Akamai. No se intentan.
- **MercadoLibre**: su `robots.txt` prohibe `ClaudeBot` y `Claude-User` por
  nombre. No se toca. Si algun dia hace falta, es via su API OAuth oficial.
- **SERNAPESCA scraping**: `sernapesca.cl/robots.txt` prohibe `anthropic-ai` y
  `Claude-Web`. El calendario de vedas se construye desde los decretos de
  SUBPESCA (normativa publica), curados a mano. La API no documentada
  `get_vedas_html` **no se usa**.
- **Datasets SUBPESCA en datos.gob.cl**: los 16 son CC BY-NC, sin uso
  comercial. No entran al producto.

## Arquitectura

Cuatro piezas nuevas, todo lo demas es reuso. El pipeline existente de
`price_intelligence` no se modifica: se le alimenta el refs CSV que ya consume.

```
feeds publicos --> [1] lectores de catalogo --> registros normalizados
                                                     |
                        [2] normalizador a CLP/kg <---+
                                                     |
                   match/ existente (fuzzy+probabilistic+adjudicate)
                        con [3] lexico es-CL de congelados
                                                     |
                                              refs CSV  --> price_intelligence (existente) --> matriz + deck
                                                     |
titulos de SKU --> [4] clasificador de especie --> calendario de vedas --> src/risk_period.py (existente)
```

### [1] Lectores de catalogo -- `src/pricing_intel/acquire/catalog_feeds.py`

Dos lectores que devuelven la misma forma normalizada:

- `read_woo_store_api(base_url)` -> `wp-json/wc/store/v1/products?per_page=100`,
  pagina por `X-WP-TotalPages`.
- `read_shopify_products_json(base_url)` -> `/products.json?limit=250`, pagina
  por `page`.

Ambos devuelven `Sequence[CatalogRecord]` (frozen dataclass: `source`, `sku`,
`title`, `price`, `currency`, `in_stock`, `url`, `raw_format_text`). El I/O de
red va detras del `http_client` existente; el parseo es puro y testeable con
fixtures.

**Por que feeds y no scraping de HTML**: es la misma informacion que el sitio
publica para su propio front-end, en un endpoint disenado para ser consumido,
sin costo de renderizado para el servidor. Se enruta por la logica de tiering de
adquisicion existente; no se inventa un bypass del techo L1 de `price_watch`.

### [2] Normalizador a precio por kilo -- extension de `src/pricing_intel/normalize.py`

Dos funciones puras nuevas, hermanas de `extract_pack_size`:

```python
def extract_weight_format(text: str) -> WeightFormat | None: ...
def price_per_kilogram(price: Decimal, fmt: WeightFormat) -> Decimal: ...
```

`WeightFormat` es un frozen dataclass `(quantity: Decimal, unit: Literal["kg","g","lt","cc"])`.

Formatos que debe reconocer, tomados de titulos reales de los 9 sitios:
`1kg`, `1 kg`, `1Kg`, `500g`, `500 grs`, `2,5kg`, `2.5 kg`, `1lt`, `500 cc`,
`1.36 kilos`, `bolsa 1kg`, `caja 10kg`.

**Disciplina de golden rule 14**: si el formato no se reconoce, la funcion
devuelve `None` y el registro entra a cuarentena. Nunca se asume 1kg por
defecto. Un SKU sin formato legible se reporta como "sin comparar", no se
adivina.

**Limite conocido y aceptado**: `lt` y `cc` no son masa. Para conservas
(`Locos al natural 1lt`, `Pulpo en conserva 1lt`) no existe conversion honesta a
CLP/kg. Esos SKUs se comparan solo contra otros del mismo formato volumetrico,
en un cluster separado, y se marcan como tal en el informe.

### [3] Lexico es-CL de congelados -- `config/lexicon/es_cl_congelados.yaml`

El cascade de homologacion existente (`gtin` -> `fuzzy` -> `probabilistic` ->
`adjudicate`) esta disenado para GTINs. En este mercado **no hay un solo GTIN**:
ninguno de los 9 sitios publica `gtin13` ni `brand`. Toda la homologacion cae en
fuzzy + probabilistic sobre texto libre en espanol.

El lexico aporta los atributos decisivos que `DECISIVE_ATTRIBUTE_KEYS` necesita:

- **especie**: camaron, langostino, merluza (comun/austral/hubbsi), reineta,
  congrio, salmon, trucha, albacora, atun, pangasius, tilapia, calamar, pulpo,
  chorito, almeja, ostion, macha, navajuela, loco, centolla, centollon
- **calibre**: convencion norteamericana de conteo por libra -- `16/20`,
  `21/25`, `26/30`, `31/40`, `36/40`, `71/90`, `100/200`
- **estado**: crudo | cocido
- **proceso**: pelado, desvenado, con caparazon, apanado, rebosado, IQF,
  porcionado, entero, filete, trozos, anillos, medallon
- **origen declarado**: ecuatoriano, nacional, importado, del norte, austral

Un match exige **al menos dos senales concordantes** (p. ej. especie + calibre),
nunca solo similitud de string.

**Gotcha verificado**: los handles de Frozt estan corridos --
`/products/camaron-26-30-crudo-pelada-desvenada-fish-box-copia` sirve
"Camaron 36/40 cocido pelado desvenada". **Nunca parsear el slug**; solo el
campo `title` del feed.

### [4] Calendario de vedas -- `config/regulatory/cl_vedas.yaml` + `src/regulatory/veda_calendar.py`

Dataset curado a mano, versionado en el repo. Una fila por decreto:

```yaml
- especie: loco
  tipo: extractiva
  desde: 2026-01-01
  hasta: 2028-12-31
  ambito: "Arica y Parinacota a Aysen (excluye Magallanes)"
  decreto: DEXE202600007
  fuente: https://www.subpesca.cl/portal/615/articles-...
  verificado_el: 2026-07-24
  prohibe: [captura, comercializacion, transporte, procesamiento, almacenamiento]
  exencion: "AMERB donde el loco es recurso principal (art. 3); acreditar origen legal (arts. 63/65 LGPA)"
```

`veda_calendar.py` es puro: recibe titulos de SKU + el YAML, devuelve
`Sequence[VedaExposure]`. Se conecta a `src/risk_period.py`, que ya modela
ventanas de riesgo.

**Distincion que el informe debe respetar**: una veda *extractiva* prohibe
tambien almacenar y comercializar; una *biologica* cierra la captura estacional.
El informe reporta **exposicion y obligacion documental**, nunca infraccion. La
exencion AMERB existe y el dato de si el proveedor la cumple no es observable
desde afuera.

**Frescura**: cada fila lleva `verificado_el`. El pipeline emite una advertencia
si alguna fila supera los 90 dias. Caso vivo: el cierre de macha en
Valparaiso/O'Higgins/Maule vence el 2026-08-02 y no hay decreto de renovacion en
el registro.

### Reuso sin modificar

`price_intelligence` (matriz + provenance por dato), `match/` completo,
`citation_gate`, `deliverable.py`, `excel_export.py`, `risk_period.py`,
`normalize.normalize_price_string` (ya maneja CLP explicitamente).

## Flujo de datos

1. Por cada uno de los 9 origenes: leer feed -> `CatalogRecord[]`.
2. Normalizar: precio a `Decimal` CLP (funcion existente) + formato a CLP/kg
   (funcion nueva). Los que no parsean van a cuarentena con motivo.
3. Extraer atributos con el lexico -> enriquecer cada registro.
4. Homologar entre origenes -> clusters de producto comparable.
5. Por cluster: calcular mediana de mercado y brecha de cada sujeto.
6. Emitir refs CSV -> `price_intelligence` produce matriz y deck.
7. En paralelo: titulos de SKU -> especie -> exposicion a vedas -> capa de
   riesgo del informe.
8. Ensamblar: 5 informes por empresa + 1 benchmark sectorial.

## Manejo de errores

- **Feed caido o con rate limit**: falla ese origen, no la corrida. El informe
  declara que origenes entraron y cuales no.
- **Formato de peso ilegible**: cuarentena con motivo, contabilizado. El informe
  publica la **cobertura** ("82 de 96 SKUs comparables") en vez de esconder el
  resto.
- **Match ambiguo**: `adjudicate` decide; si no alcanza el umbral, el par no
  entra a la matriz. Cada fila lleva su confianza de match.
- **Precio dudoso**: la regla existente manda -- "un precio dudoso es peor que
  ningun precio". Cuarentena, no estimacion.
- **Soft-404 con JSON-LD vacio**: Jumbo emite `"price": "undefined"`. Validar
  que el precio sea numerico, no solo que el bloque exista.

## Testing

- **Unit, prioridad maxima**: `extract_weight_format` contra una tabla de
  titulos reales capturados de los 9 sitios, incluyendo los casos raros
  (`1.36 kilos`, `500 cc`, `2,5kg` con coma decimal). Este es el componente de
  mayor riesgo del diseno.
- **Unit**: lectores de feed contra fixtures JSON congeladas. Sin red en tests.
- **Unit**: `veda_calendar` -- un SKU dentro de ventana, uno fuera, uno en
  especie sin veda, uno con dato vencido.
- **Golden**: un cluster completo (camaron 36/40 crudo, 4 origenes) de feed
  crudo a fila de matriz, con numeros verificados a mano.
- **Guard**: `tests/test_pricing_intel_boot_safety.py` ya existe -- los modulos
  nuevos no pueden importar extras opcionales a nivel de modulo.

## Restricciones legales, resueltas

| Fuente | Licencia | Veredicto |
|---|---|---|
| Feeds de los 9 sitios | robots.txt permite rutas de producto | usar |
| Decretos SUBPESCA | normativa publica | usar |
| Aduanas registro de importacion | `odc-by` | usar (fase 2) |
| SERNAPESCA desembarques | CC-BY | usar |
| ODEPA comercio exterior | CC-BY | usar |
| INE IPC | CC BY-SA 4.0 | usar con cuidado: share-alike contamina derivados |
| Datasets SUBPESCA en datos.gob.cl | CC BY-NC | **no usar** |
| sernapesca.cl (crawling) | robots.txt bloquea AI | **no usar** |
| MercadoLibre | robots.txt bloquea ClaudeBot | **no usar** |

## Fuera de alcance (YAGNI)

- **Reconstruccion de costo landed via Aduanas.** Es el diagnostico pagado
  (fase 2), no la radiografia gratuita. Archivos de 269 MB en latin-1
  posicional, sin nombre de importador.
- **Monitoreo recurrente.** `price_watch` ya existe para eso. Esta es una
  fotografia, no una suscripcion.
- **Recomendacion de precio.** `price_optimizer` existe, pero recomendar un
  precio sin conocer volumenes ni costos seria inventar. La radiografia dice
  donde esta la brecha; no dice cuanto subir.
- **Cualquier envio a las empresas analizadas.** Producir el analisis y
  enviarlo son decisiones distintas. La segunda requiere aprobacion explicita.

## Riesgos

1. **El normalizador a CLP/kg es punto unico de falla.** Un formato mal leido
   invierte una conclusion completa. Mitigacion: cuarentena agresiva + reportar
   cobertura + tests sobre titulos reales.
2. **Homologacion sin GTIN sobre texto libre en espanol producira falsos
   positivos.** Mitigacion: dos senales concordantes minimo, confianza por fila,
   y el cluster ancla (camaron 36/40) verificado a mano.
3. **El dataset de vedas envejece.** Mitigacion: `verificado_el` por fila +
   advertencia a los 90 dias.
4. **Nombrar a 5 empresas reales en un informe comparativo.** Los precios son
   publicos y verificables, y el informe se limita a lo observable -- no afirma
   margenes, costos ni infracciones. Aun asi, distribuirlo es una decision
   aparte de producirlo.

## Decomposicion sugerida

- **PR-1**: lectores de feed + normalizador CLP/kg + lexico -> emite refs CSV.
  CLI `examples/run_cl_catalog_scan.py`.
- **PR-2**: dataset de vedas + `veda_calendar.py` + integracion con
  `risk_period.py`.
- **PR-3**: job `market_radiography` que compone PR-1 + `price_intelligence` +
  PR-2 en el entregable sectorial, registrado como herramienta y tocando los
  7 lugares fijados (incluido `citation_gate.TOOL_CONCEPTS`).
