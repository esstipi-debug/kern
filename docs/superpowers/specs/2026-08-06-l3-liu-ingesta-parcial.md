# Spec — Liu, *Supply Chain Analytics* (2022) al grafo L3: **ingesta parcial**

**Fecha:** 2026-08-06 · **Estado:** **IMPLEMENTADO el 2026-10-26** por
`scripts/merge_liu_supply_chain_analytics.py` (obra curada #28: +7 nodos, +28 aristas, comunidad
206) · **Decisión del operador:** opción 2 (parcial — solo lo que el motor puede citar), sobre
ingesta completa y sobre no ingerirlo.

**Desvío respecto de esta spec, y por qué.** La pieza 1 pedía *enriquecer* el nodo
`knowledge::tracking_signal` que ya existía (Gilliland). Un nodo que se enriquece conserva su
`source_file`, así que el umbral ±6 habría quedado en la prosa de un nodo cuya cita sigue
diciendo Gilliland — y una cita no renderiza la prosa. Se agregó en su lugar
`tracking_signal_band` ("Tracking Signal Band (+-6)", p.312), con la arista de autoridad
`cites → book_chopra_meindl` y la de concepto `→ tracking_signal`: el número es citable, y la
atribución no se le regala a Liu. Las otras cuatro piezas entraron como estaban especificadas.
La restricción de orden ("no arranca hasta que #233 esté en main") se cumplió: #233 entró como
`1a10e6c` (obra #27) el 2026-09-15, y por eso esto se pudo hacer recién ahora.

## Contexto

El libro (Palgrave Macmillan, 2022, 388 pág.) se leyó completo el 2026-08-05/06: caps. 1, 7 y 8 de
primera mano, el resto por lectura delegada con informes verificados, más un barrido regex de 35
términos sobre las 388 páginas. Cambió el posicionamiento del producto y validó los sombreros como
argumento de venta (ver memoria `liu-supply-chain-analytics-findings`).

**Nada de eso está en el grafo.** Verificado contra `knowledge/scm-books/graph.json` en `main`:
3002 nodos, 6141 aristas, 35 `source_file` distintos, y **cero** de Liu — los únicos matches son
`gallego-revenue-management-pricing-analytics.pdf` y `ch09-hu-liu.pdf`, que es otro Liu.

El resultado incómodo: se leyeron 388 páginas, salieron siete contradicciones y un cambio de
posicionamiento, y el motor no puede citar ni una línea de eso.

## Por qué parcial y no completo

El grafo L3 **no es un archivo de lo que leímos: es el respaldo de lo que el motor afirma.** Esa es
la vara, y bajo esa vara la mayor parte de Liu no califica:

- ~40% del volumen es tutorial de pandas/matplotlib/sklearn sin contenido de dominio (caps. 3 y 4
  enteros, y ~62 de las 105 pág. de los caps. 5-6).
- Su cap. de inventarios enseña **cuatro** métodos — EOQ, safety stock, ROP, revisión periódica — y
  Kern ya tiene los cuatro, con fuentes mejores ya en el grafo.
- Trae **tres errores verificados** (ver abajo). Meter una fuente con errores a un grafo que existe
  para respaldar citas tiene un costo que hay que pagar a propósito, no por inercia.

## Qué entra — cinco piezas, ninguna decorativa

El criterio para cada una: **el motor hoy afirma o podría afirmar esto, y no puede citarlo.**

### 1. Enriquecer `knowledge::tracking_signal` (NO crear)

**Verificado: el nodo ya existe.** Viene de `gilliland-business-forecasting.pdf` (Ch.3-4), community 4,
y tiene **solo 2 aristas**: `book_gilliland_business_forecasting -> references` y
`mean_error_bias -> semantically_similar_to`. Su `rationale` describe el concepto pero **no da umbral
operativo**.

Liu no aporta el concepto: aporta **el número que lo vuelve ejecutable**. p.312: fuera del rango
**±6** el pronóstico está sesgado, y la lectura es binaria y accionable — *"either the model is
flawed, or the underlying demand pattern has shifted"*.

**Atribución correcta, y es un detalle que importa:** el ±6 **no es de Liu**. Liu lo atribuye a
**Chopra & Meindl 2016**, y `knowledge::book_chopra_meindl` **ya existe en el grafo**. Así que la
arista de autoridad va a Chopra & Meindl; Liu entra como fuente secundaria que lo reporta.

Esta es la única pieza de Liu que habilita una **capacidad nueva del motor** (el guard de degradación
silenciosa de la tarea 5). Sin ella, esa tool no puede citar lo que hace.

### 2. Nodo nuevo: niveles de analítica, con foco en el hueco diagnóstico

Descriptivo → **diagnóstico** → predictivo → prescriptivo (Liu p.25-26, Fig. 1.6).

**Verificado: `diagnostic` está AUSENTE del grafo entero.** Existen `maturity_model_descriptive_purpose`
y `maturity_model_prescriptive_purpose`, pero son de otro marco y no cubren el nivel intermedio.

Importa porque Kern hace descriptivo (el deliverable) y prescriptivo (el motor), y el nivel que
responde *"por qué pasó"* está vacío — en el producto y en el grafo. Nombrarlo es el primer paso para
decidir si se llena.

Definición citable de prescriptivo, textual de p.26, que además nombra a Kern sin saberlo:
*"safety stock and inventory level recommendations for different SKUs"*.

### 3. Nodo nuevo: frontera Operations Research vs Supply Chain Analytics

**Verificado: `operations research` está AUSENTE del grafo entero.**

Liu p.30 traza la frontera a propósito: *"OR places strong emphases on mathematical modelling and
optimization, whilst SCA explores and learns from data"*. Y el Prefacio (p.6): *"no further topics
from OR are included in the book"*. Su definición de SCA (p.27) arranca con *"the application of
**machine learning** and data analytics techniques"*.

Es lo que sostiene la corrección de vocabulario del producto: medido con esa vara, EOQ, safety stock,
(s,Q)/(R,S), p-median, VRP y DRP **son OR, no analytics**. Sin este nodo, esa decisión de
posicionamiento no tiene respaldo citable.

### 4. Nodo nuevo: metas SMART de SCA + condición de adopción organizacional

Simple · Meaningful · Actionable · Reliable · Timely (p.28-30, Fig. 1.7). Lo citable de verdad son
tres cosas:

- **La regla de adopción**: cuanto más simple y entendible la solución, mayor la probabilidad de que
  se adopte. Cita de Coca-Cola: *"models must be simple and transparent, yet effective"*. Respalda el
  determinismo explicable como propiedad de producto, no como limitación.
- **El modo de falla**: sin conocimiento profundo del proceso, el output es *"nothing more than fun
  and interesting facts"* — correcto e irrelevante. Es el riesgo nombrado del self-serve.
- **Timely = proactivo**, no on-demand: *"effective SCA should take a more proactive stance to prevent
  problems, rather than being reactive to fix problems"*.

### 5. Nodo nuevo: cuándo una PyME SÍ necesita el software

**Verificado: `small firm` está AUSENTE del grafo.** Y es, de todo el libro, lo más directamente
accionable para GTM.

Liu dice **cuatro veces** que una empresa chica puede manejar el inventario a mano (p.240, p.241,
p.245, p.284). Pero las cuatro traen la misma condición: *providing that demand is stable and
predictable* · *providing they do not have complex SKUs* · *when their managed SKUs are not too
complex* · *providing supply chains are not complex*.

O sea el canon no dice que la PyME no necesita esto: dice **bajo qué condiciones no lo necesita**, y
por lo tanto bajo cuáles sí. Las tres condiciones de calificación, citables:

1. Muchos SKUs, o SKUs complejos
2. Demanda inestable o impredecible
3. Proveedores sin reposición ad hoc rápida (p.241: si el proveedor entrega rápido y flexible,
   *"even if you get it wrong on some occasions, ad hoc deliveries can be made"*)

Esto convierte una objeción en criterio de ICP. El que no cumple las tres, el libro tiene razón, no
necesita a Kern.

## Qué NO entra, y por qué

| Fuera | Motivo |
|---|---|
| EOQ, safety stock, ROP, revisión periódica | Kern ya los tiene, con fuentes mejores ya en el grafo. Duplicar concepto sin agregar rigor **diluye** el pool de citas |
| Todo el aparato sklearn (caps. 5-7) | Tutorial de librería, no dominio SCM |
| El LP de almacén (p.252) | Antiejemplo: ignora la demanda por decreto (*"for simplicity reason, ignore the demand pattern"*) y devuelve 254 unidades de un solo SKU. No se cita un antiejemplo como si fuera método |
| El clasificador de stockout (7.4) | Exige un target etiquetado y pronósticos a 3/6/9 meses como features. Ningún cliente self-serve los tiene |
| RFR/XGBoost sobre Rossmann (8.4) | Sus features top (CompetitionDistance, Promo histórico) no existen en ningún ERP PyME |

## Política de errata — la parte que hay que decidir bien

Liu trae **tres errores verificados de primera mano**. Ninguno entra como concepto:

1. **p.246** — *"to potentially satisfy 90% of the demand, you will need a Z-score about 1.28"*. Falso:
   Z=1,28 da 90% de **cycle service level**, no 90% de demanda satisfecha (fill rate). Kern es más
   correcto que el libro acá (`src/safety_stock.py:25` separa ambos).
2. **Ec. 8.13, p.311** — el RMSE está impreso como `sqrt(1/n · Σ|E_t|/A_t)`, que es el cuerpo del MAPE
   bajo una raíz. La fórmula correcta usa `E_t²`.
3. **p.265** — *"solely relying on accuracy score is sufficient, especially when the dataset is
   extremely imbalanced"*. Dice lo contrario de la sección entera; falta un "in".

**Regla:** los errata van como propiedad del **nodo de la fuente** (`known_errata`), nunca como nodo
de concepto ni como arista hacia un concepto. Un grafo de citas no puede tener un nodo citable que
diga algo falso, aunque lo diga el libro.

El #1 tiene un uso positivo aparte, y vale explicitarlo en el nodo de la fuente: es evidencia
documentada de que **la enseñanza estándar confunde CSL con fill rate**. `knowledge::service_level`
ya se llama "Service Level (Cycle Service Level / Fill Rate)", así que la distinción está; lo que
Liu aporta es la prueba de que el cliente formado con este material la tiene mal.

## Puentes — ids verificados contra `graph.json` de `main`

Todos existen hoy. El guard `BRIDGES` del script debe re-verificarlos y **fallar fuerte** si alguno
no está, para que un typo no forkee en silencio un concepto ya existente:

```
knowledge::tracking_signal                    (enriquecer, no crear)
knowledge::mean_error_bias
knowledge::book_chopra_meindl                 (autoridad real del umbral +-6)
knowledge::book_gilliland_business_forecasting
knowledge::service_level
knowledge::forecast_error
knowledge::mean_absolute_percentage_error
knowledge::demand_forecasting
knowledge::forecastability
knowledge::safety_stock
```

## Implementación

- Script: `scripts/merge_liu_supply_chain_analytics.py`, siguiendo el patrón de
  `merge_vandeput_forecasting.py` (rama de #233): **extracción inline**, así se reproduce desde un
  clone limpio sin working tree gitignoreado ni clave viva.
- **Sin clave de LLM.** El paso que la necesitaría es la extracción, y la hizo quien leyó el libro.
- `source_file`: `liu-supply-chain-analytics.pdf`. Fuente **#29** (Vandeput es la #28).
- Actualizar en el mismo PR: conteos de CLAUDE.md, README y los guards de `/how-it-works` (ese guard
  ya atrapó drift antes; es para lo que existe).
- Re-medir el techo del pool de citas (`_CANDIDATE_POOL`) y **reportar el número aunque no se mueva** —
  que no se mueva también es un resultado.

## Orden — restricción dura

`graph.json` es un archivo único. **El PR #233 (Vandeput, fuente #28) tiene que entrar primero**, o
las dos ramas reescriben el mismo archivo y chocan. Y #233 no puede entrar sin CI, y la CI no vuelve
sin el secret `NOSILO_CORE_TOKEN`.

**Esta spec no arranca hasta que #233 esté en `main`.**

## Nota de proceso

Esta spec se escribió desde un `git worktree` aparte: el worktree compartido estaba en
`fix/valorizar-excepciones-ap`, de otra sesión concurrente. Nunca hay que sacarle la rama de abajo a
otra sesión — ver la memoria `linchpin-concurrent-sessions`.
