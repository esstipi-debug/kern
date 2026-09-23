# La fecha de fin por SKU, construida — y qué hace con una fecha equivocada

> Construida el 2026-09-23 sobre `828b57c`. Motor en [`src/horizon.py`](../src/horizon.py),
> productor en `jobs/intake.py::sku_end_dates`, aplicación en
> `jobs/inventory_optimization.py`, compuerta en `jobs/qa.py::_horizon_issues`, y las
> superficies en `jobs/deliverables.py`, `jobs/inventory_deliverable.py` y
> `scm_agent/tool_options.py`. `tests/test_fecha_de_fin_por_sku.py` (16) la fija.
>
> **Lo que paga ya estaba medido** y no se re-mide acá:
> [`KERN_HORIZONTE_POR_SKU.md`](KERN_HORIZONTE_POR_SKU.md) y
> [`KERN_PALANCAS_SOBRE_25_MUNDOS.md`](KERN_PALANCAS_SOBRE_25_MUNDOS.md) —
> **+0,1309 de captura en 23 de 25 mundos, t = +5,81, p = 0,00001**, la palanca más
> grande de la rama y la única que resiste la muestra completa.

## La regla

**No acumules por encima de lo que todavía podés vender.** Con `restante` = la demanda que
queda hasta la fecha de fin del SKU, se topean las **dos** mitades de la política. Y
`restante` es **la media medida × los períodos que faltan** — no hace falta saber el
futuro: los dos carriles se midieron y son equivalentes (+0,1309 contra +0,1413, una
diferencia de +0,0104 con p = 0,378). La palanca necesita una **fecha**, no un pronóstico.

## La respuesta a lo que la medición dejó abierto

Los dos documentos de medición cerraron diciendo: *"falta medir qué pasa con una fecha
**equivocada**, que es la pregunta de seguridad de la capacidad"*. Construirla obliga a
contestarla, y la respuesta no es un número sino una regla:

> **El tope puede encoger una política; nunca puede anularla.**

Un tope de cero no es una política chica: es *"no compres nunca más"*, o sea una
**discontinuación** — una decisión de negocio que toma una persona, no un redondeo que cae
de una celda mal tipeada. Y una celda mal tipeada es el modo de falla realista: un
`2025-01-01` donde iba `2026-01-01` apagaría la reposición de un SKU que vende todas las
semanas.

Por eso `cap_policy` tiene **seis verdictos** y tres de ellos **se niegan a topear**:

| verdicto | qué pasó | qué hace |
|---|---|---|
| `no_date` | el SKU no trae fecha | intacto — **es el caso normal** |
| `inert` | la fecha está lejos | intacto |
| `applied` | al menos una mitad bajó | topea |
| **`expired`** | la fecha ya pasó, o es hoy | **intacto y declarado** |
| **`would_zero`** | la fecha no pasó pero el restante da 0 | **intacto y declarado** |
| **`unusable`** | hay fecha y no hay media medida | **intacto y declarado** |

Los tres últimos salen dimensionados **como si no hubiera fecha**, y toda superficie los
**nombra**: una fila del Excel con su `horizon_verdict`, un hallazgo en el deck, y un
residual en las OPCIONES con su dueño (`demand planning`) y su riesgo. Callarlos sería el
peor de los dos silencios posibles — el cliente creería que el tope cubrió un SKU que este
plan se negó a topear.

Es la misma disciplina que `tests/test_negativa_no_es_cero.py` fija en la salida —*una
negativa no puede salir vestida de número*— aplicada a la **entrada**.

## La traducción que hubo que declarar

La regla medida dice "las dos mitades de la política", y el arnés que la midió ejecuta todo
como `(R,S)`: **no tiene lote**. El motor real sí:

| política | mitad 1 | mitad 2 |
|---|---|---|
| `(R,S)` y cobertura provisional | `s` | `S` |
| **`(s,Q)`** | `s` | **`Q`** — `order_up_to` es `None` |

Así que la misma regla se aplica a **tres** cantidades y no a dos. **Esto es una traducción
declarada, no una equivalencia**, y por eso `HorizonCap` llama a sus campos `trigger` y
`target` en vez de `reorder_point`/`order_up_to`: ponerles el nombre de una de las dos
formas escondió un defecto real mientras se escribía esto — un `(s,Q)` decidía el tope
mirando **solo** el disparador y dejaba pasar una `Q` de 302 sobre un restante de 204.
`test_las_DOS_mitades_deciden_el_tope_no_solo_el_disparador` existe para que no vuelva.

## De dónde sale la fecha, y qué se declara del archivo

`jobs/intake.py::sku_end_dates` lee una columna opcional propia (`end_date`), con alias
en en/es/pt y **ninguno ambiguo**: no acepta un `fecha` ni un `end` pelados, porque
confundir *"la fecha de esta fila"* con *"la fecha en que este producto deja de venderse"*
apagaría la reposición de toda una cartera.

Tres reglas de ausencia, las tres con test:

- **Sin columna → `None`, nunca `{}`.** Un mapa vacío afirma que se midió.
- **Celda vacía → el SKU no entra**, y no cuenta como defecto: la mayoría de una cartera
  real no trae fecha y eso está bien.
- **Celda ilegible → se descarta y se CUENTA** (`.attrs['unparsed']`). No se reemplaza por
  hoy: el reloj como fallback silencioso convertiría un typo en un tope.

Y un SKU con **dos fechas distintas** se cuenta en `.attrs['conflicting']` — el archivo se
contradice sobre cuándo muere un producto, y quedarse con una callada es peor que decirlo.
Gana la última fila fechada, la misma regla que `stock_position`: una sola para todo el
archivo.

**El `as_of` es el fin del histórico, no el reloj.** Un archivo que termina hace tres meses
se mediría contra un horizonte que no es el suyo, y con una fecha de fin intermedia todo el
catálogo saldría `expired`.

## Qué ve el operador

- **Excel, por fila**: `end_date`, `horizon_verdict`, `remaining_demand` y los tres
  `*_before_horizon`. Un `*_before_horizon` aparece **si y solo si ese número se movió** —
  en blanco, nunca en cero, porque un cero afirmaría un cambio que no hubo.
- **Deck**: dos hallazgos, y son dos porque dicen cosas opuestas — *"N SKU topeados, el tope
  saca X unidades de compra"* y *"N fechas NO aplicadas"*.
- **OPCIONES**: `Stop buying past the end date on N SKU(s)` — la única opción de esta tool
  que **resta** capital en vez de pedirlo, con las unidades liberadas para poder compararla
  contra el titular de inversión. Más el residual de las fechas negadas.
- **QA**: cuatro invariantes (`jobs/qa.py::_horizon_issues`), y la primera es que un tope
  jamás anule. Si un cambio futuro dejara pasar un cero, el entregable no sale.

## Qué **NO** hace

- **No discontinúa nada.** Ni con una fecha vencida ni con un restante en cero. Esa decisión
  no es del motor.
- **No mide de nuevo cuánto paga.** El número vive en las dos líneas base ya congeladas, y
  esas se midieron con un post-proceso sobre la `Policy`; esta implementación aplica la
  misma regla dentro del motor y a tres cantidades en vez de dos. **No son byte a byte la
  misma cosa**, y por eso lo declara en vez de suponerlo.
- **No toca ninguna otra tool.** Solo `inventory_optimization` lee la columna. Los otros
  trece emisores de cantidad a pedir siguen sin horizonte.
- **⚠️ No cambia nada para un archivo sin la columna.** Es el control que fija
  `test_sin_fechas_el_job_sale_IDENTICO_a_antes_de_la_capacidad`, y es lo que hace que esto
  sea seguro de desplegar sobre la base instalada.
- **~~No mide qué fracción de una cartera real trae la fecha~~ — medido el 2026-09-23**,
  en [`KERN_FRACCION_CON_FECHA_DE_FIN.md`](KERN_FRACCION_CON_FECHA_DE_FIN.md) sobre Olist
  (32.729 SKU, 3.053 vendedores). **13,8 %–26,1 % de los SKU**, y siempre **menos pesado
  por plata** (8,9 %–21,6 %), con una dispersión de 10× entre carteras (p10 0 %, p90 59 %).
  Dos resultados cambian cómo se lee el techo de +13,1 puntos: ese techo se midió donde
  **todos** los SKU mueren la misma semana, y en una cartera real la mortalidad **baja**
  cuanto más se parece a un rango repuesto de verdad (núcleo 13,8 %, cola 26,1 %); y **el
  fin de vida observable es justo donde el tope NO actúa** — los 1.924 SKU muertos salen
  `expired`, sin excepción. Lo deployable es la tasa por año (**6,8 %–12,9 %**), y depende
  de que el cliente declare fechas **futuras**, que ningún archivo puede traer.
