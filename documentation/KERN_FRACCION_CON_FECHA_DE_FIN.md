# Qué fracción de una cartera real trae la fecha de fin — medido

> Medido el 2026-09-23 con `scripts/fraccion_con_fecha_de_fin.py` sobre el dataset
> público de **Olist** (112.101 líneas de pedido, **32.729 SKU**, **3.053 vendedores**,
> 2016-08-29 → 2018-09-03, 105 semanas), que `scripts/fetch_olist.py` baja de un mirror
> público sin credenciales. Línea base congelada en
> [`lineas_base/fraccion_con_fecha_de_fin.json`](lineas_base/fraccion_con_fecha_de_fin.json);
> `tests/test_fraccion_con_fecha_de_fin_publicado.py` ata este documento a ese archivo.
>
> Cierra el último ítem abierto de [`KERN_FECHA_DE_FIN_POR_SKU.md`](KERN_FECHA_DE_FIN_POR_SKU.md):
> *"no mide qué fracción de una cartera real trae la fecha. Lo realizable es proporcional
> a eso, y sigue sin saberse."*

## El reencuadre, declarado antes de medir

**Ningún export real trae una columna `end_date`** — es una columna que Kern acaba de
inventar. Medir *"qué fracción de los archivos la trae"* da **cero** y no dice nada.

La pregunta medible es otra: **qué fracción de los SKU de una cartera real tiene un fin
de vida**, porque ésa es la población a la que la palanca podría aplicarse alguna vez. Y
se parte en dos mitades, una medible y otra no:

| | |
|---|---|
| **Medible** | cuántos SKU dejan de venderse dentro de la ventana, con un criterio que sobreviva a una validación fuera de muestra |
| **NO medible de un archivo de transacciones** | si el cliente **sabía** la fecha de antemano. Una discontinuación es una decisión de negocio; el archivo sólo muestra la consecuencia |

La segunda mitad **no se estima, se declara**. Es el límite del estudio y no se puede
cerrar con más datos del mismo tipo.

## La respuesta corta

**Entre 13,8 % y 26,1 % de los SKU, y entre 8,9 % y 21,6 % de la facturación** — pero el
número que importa para decidir no es ninguno de ésos, sino los tres de abajo.

## 1 · La fracción depende de a qué le llames "la cartera"

| cartera | SKU | con fin de vida | % SKU | % facturación |
|---|---:|---:|---:|---:|
| todo lo que Kern dimensiona (`n_periods>=6`) | 7.357 | 1.924 | **26,2 %** | 21,7 % |
| con ≥3 semanas con venta | 7.364 | 1.924 | 26,1 % | 21,6 % |
| rango recurrente (≥6 semanas con venta) | 2.724 | 610 | **22,4 %** | 16,8 % |
| núcleo (≥12 semanas con venta) | 856 | 118 | **13,8 %** | 8,9 % |

Dos cosas que la tabla dice y no eran obvias:

- **Cuanto más se parece a un rango que alguien repone de verdad, menos fracción muere.**
  El núcleo tiene la mitad de mortalidad que la cola. O sea que el techo publicado de la
  palanca (+13,1 puntos, medido donde **todos** los SKU mueren la misma semana) se aplica
  sobre la parte de la cartera que **menos** se parece a ese supuesto.
- **Pesada por plata siempre es MENOR que por conteo** (8,9 % contra 13,8 % en el núcleo;
  16,8 % contra 22,4 % en el recurrente). Los SKU que mueren son sistemáticamente los
  chicos. Como el valor de la palanca escala con **capital** y no con SKU, lo realizable
  es ~⅔ de lo que sugiere el head-count. Contar SKU sobre-estima.

**Las dos primeras filas son casi idénticas a propósito, y eso es un hallazgo sobre la
compuerta, no sobre la cartera**: `n_periods` mide el **span** desde la primera venta
hasta el fin del ARCHIVO, no la densidad (`src/sources.py::_series_from_frame`). Sobre
Olist esa compuerta admite el **95,1 %** del catálogo, y la mediana de lo admitido vendió
en **una** semana y lleva **28** en silencio. `MIN_PERIODS_FOR_POLICY` no filtra la cola
larga; filtra sólo lo que nació hace menos de seis semanas.

## 2 · La distribución por cartera tiene 10× de dispersión

Cada vendedor de Olist es **una cartera real** — es la unidad que correría Kern. Así que
la respuesta no es un número, es una distribución:

| carteras | p10 | p25 | mediana | p75 | p90 |
|---|---:|---:|---:|---:|---:|
| vendedores con ≥10 SKU (177) | 0,0 % | 8,3 % | **23,1 %** | 45,5 % | 59,3 % |
| vendedores con ≥20 SKU (61) | 4,2 % | 9,7 % | **26,1 %** | 40,7 % | 55,4 % |
| vendedores con ≥50 SKU (18) | 6,0 % | 11,8 % | **17,6 %** | 29,8 % | 41,4 % |

Hay carteras reales con **0 %** y carteras reales con **59 %**. Prometer "la fracción" de
un cliente que todavía no mandó su archivo es prometer la mediana de una distribución que
se extiende un orden de magnitud. **Lo que se le puede decir a un cliente es que se mide
en su archivo en cinco minutos, no cuánto le va a dar.**

## 3 · Ese porcentaje **no** es donde la palanca paga — y ahí está el resultado

Una fecha de fin **que ya pasó** sale `expired` y el tope **no topea**: lo dice
`src/horizon.py` y lo hace. Corriendo `cap_policy` con la fecha de fin real de los 1.924
SKU muertos:

```
{'expired': 1924}
```

**Los 1.924, sin excepción.** O sea que la fracción con fin de vida *observable* es
exactamente la fracción donde la palanca **no hace nada**. Y está bien que así sea: la
serie ya refleja esa muerte.

Se puede medir cuánto la refleja. El relleno de ceros de `_series_from_frame` llega hasta
el fin del archivo, así que la media de un SKU muerto **ya viene dividida por su span
completo**:

| | factor mediano sobre la media |
|---|---:|
| SKU muertos | **0,28** (p25 0,17 · p75 0,39) |
| SKU vivos | 0,74 |

El motor ya achica **2,6 veces más** la política de un SKU muerto que la de uno vivo,
antes de que exista ninguna fecha. Buena parte de *"no acumules por encima de lo que
todavía podés vender"* ya estaba hecha para las muertes **pasadas**.

**La palanca sólo puede pagar sobre muertes FUTURAS** — que es, por construcción, lo único
que un archivo de transacciones no puede mostrar.

## 4 · Lo que sí es deployable: la tasa por año

Si lo pasado no sirve y lo futuro no está en el archivo, lo que queda es el **ritmo** al
que una cartera real discontinúa, que es lo que un cliente puede anticipar de su propio
plan comercial:

| cartera | en la ventana (105 semanas) | **por año** |
|---|---:|---:|
| con ≥3 semanas con venta | 26,1 % | **12,9 %** |
| rango recurrente (≥6) | 22,4 % | **11,1 %** |
| núcleo (≥12) | 13,8 % | **6,8 %** |

**Entre 7 % y 13 % del rango por año.** Ése es el tamaño de la población que un cliente
podría declarar por adelantado si quisiera — el techo de lo realizable, no una promesa.

## 5 · El límite duro: **no se puede deducir la fecha del archivo**

Un hueco no es una muerte. El criterio se escala a la intermitencia de cada SKU —muerto
si su silencio final supera `k` veces su **propio** gap típico, con un piso— y se valida
reservando las últimas **26 semanas**: un SKU llamado muerto que después vende es un
**falso positivo**.

| k | piso | llamados muertos | vendieron después | FP |
|---:|---:|---:|---:|---:|
| 2 | 8 | 1.662 | 354 | 21,3 % |
| 4 | 13 | 1.056 | 171 | 16,2 % |
| **4** | **26** | **594** | **63** | **10,6 %** |
| 6 | 26 | 525 | 59 | 11,2 % |

**El falso positivo hace piso en ~11 % y no baja.** Ni exigiendo medio año de silencio
sobre seis veces el gap propio del SKU: **1 de cada 9 SKU "obviamente muertos" vuelve a
vender**. Y apretar el criterio no sale gratis — pasar de piso 8 a piso 26 corta los
falsos positivos a la mitad pero también corta los hallazgos a un tercio (1.662 → 594).
No hay ajuste que sea preciso y completo a la vez.

**Ese 11 % es la justificación de campo de la regla de seguridad de la capacidad.** Un
falso positivo acá es *exactamente* una fecha equivocada en producción; y el diseño de
`src/horizon.py` —el tope puede encoger una política, **nunca anularla**— se tomó en
principio, antes de esta medición. La medición dice que era necesaria: una capacidad que
dedujera fechas del archivo apagaría la reposición de 1 de cada 9 SKU que sigue vendiendo.

## 6 · Punta a punta, por el pipeline real

336 SKU reales de 60 vendedores de Olist, por `intake` → `inventory_optimization` de
verdad. El cliente planifica en el corte; las muertes posteriores son fechas **verdaderas
y sólo conocibles después**, o sea el mejor caso posible — un cliente que declara bien
(el análogo de campo del carril `horizonte_real` del marcador).

| veredicto | SKU | |
|---|---:|---|
| `no_date` | 166 | 49,4 % — no trae fecha, intacto |
| `inert` | 63 | la fecha está lejos, no ata |
| **`applied`** | **45** | **topeado** |
| `expired` | 62 | fecha vencida, declarado y sin topear |

**El 50,6 % trae fecha y sólo el 26,5 % de ésos termina topeado.** Traer la fecha y que la
fecha mueva algo son dos cosas distintas, y entre una y otra se pierden tres cuartos.

## ⚠️ Y un defecto que esta medición encontró

**El tope no llega al titular de inversión.** En esa corrida el tope liberaría
**12.018 usd — el 11,2 % del titular** — y `requested_investment` sale **idéntico al
centavo** en las dos corridas: 107.495,50.

La causa está en `jobs/inventory_optimization.py`: `ss` y `cycle_units` se leen de la
política **sin topear** (L1023-1024), el tope se aplica después (L1041-1048), y el
`InventoryItem` que alimenta la inversión se construye con los valores **previos**
(L1096). El tope mueve `reorder_point`, `order_up_to` y `Q`, y no mueve el capital.

Es el defecto de firma de esta rama, otra vez: **la prosa dice una cosa y el número dice
otra.** El deck anuncia *"el tope saca X unidades de compra"* y la OPCIÓN se describe como
*"la única de esta tool que resta capital"*, mientras el titular que el cliente lee no se
mueve. Lo encontró correr la capacidad sobre datos reales; los tests de la capacidad usan
fixtures sintéticos y no lo veían.

**No está arreglado acá**, y a propósito: arreglarlo obliga a decidir **cómo se propaga a
capital un tope sobre el objetivo** —si se escala el ciclo, el safety stock, o los dos, y
con qué identidad— y eso es una decisión de modelo, no una cañería. Inventar una segunda
fórmula para el capital sería justo lo que el resto de la rama evita.

## Qué NO dice este estudio

- **No dice que un cliente de Kern tenga estas fracciones.** Olist es un *marketplace*
  brasileño de cola larga; el ICP de Kern es distribución con rango repuesto. Por eso el
  documento reporta la fila del **núcleo** además del total, y la **distribución por
  vendedor** además de la mediana: es lo más parecido a una cartera de distribuidor que
  este dataset contiene.
- **No dice qué fracción el cliente CONOCE por adelantado.** Es la mitad no medible, y no
  se estima.
- **No es una segunda medición del valor de la palanca.** El valor vive en
  [`KERN_PALANCAS_SOBRE_25_MUNDOS.md`](KERN_PALANCAS_SOBRE_25_MUNDOS.md) (+0,1309 en 23/25
  mundos). Esto mide **a cuántos SKU se le puede aplicar**, que es el otro factor del
  producto.
- **Una sola fuente.** No hay un segundo dataset real en el repo con catálogo y dos años
  de historia: DataCo pide credenciales de Kaggle (y trae PII, que la regla del repo
  prohíbe tocar salvo agregado) y M5 también. Lo que hay es una fuente, declarada.

## El control

`n_periods` se calcula acá con aritmética vectorizada por velocidad, y se **verifica
contra el lector real de Kern** (`src.sources._series_from_frame`) sobre 250 SKU
muestreados: **250/250 coinciden exacto**. Sin eso, todo el estudio mediría una grilla
que no es la que el motor ve.
