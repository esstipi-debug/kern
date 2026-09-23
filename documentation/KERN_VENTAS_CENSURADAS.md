# Kern ajusta contra ventas censuradas. Medido: arreglarlo no paga

> Medido el 2026-09-23 sobre `a859ae2`. Línea base congelada en
> [`lineas_base/ventas_censuradas.json`](lineas_base/ventas_censuradas.json), regrabable
> con `scripts/ventas_censuradas.py`. `tests/test_ventas_censuradas_publicado.py` ata el
> documento a su fuente.
>
> **25 mundos**, no cinco — y esa decisión es parte del resultado: sobre los cinco de
> siempre esta palanca habría salido publicada como buena. Ver H3.
>
> **Esto mide una idea; no construye una capacidad.** No se tocó el motor, ni el piso, ni
> el techo, ni el marcador. Se mide antes de construir, que es la disciplina de toda esta
> rama: si la palanca no paga, no se agrega una columna al intake ni una regla al motor.

## La pregunta

[`KERN_CAPTURA_BAJO_DISRUPCION.md`](KERN_CAPTURA_BAJO_DISRUPCION.md) cerró el colapso de
los niveles sucios y dejó escrito lo único de aquel hallazgo que **no** era un defecto de
lectura:

> `lens.history_frame` exporta `out.sold`, no la demanda. Política mala → quiebre → el
> histórico se **censura** → la política siguiente es más chica. El piso es inmune por ser
> congelado (`naive_policy`, "sin revisar nunca"): el bucle solo muerde al carril que
> replanifica.

El mecanismo es real y el agujero es grande: en estos 25 mundos el archivo con el que Kern
replanifica **no declara el 23,2 % de la demanda**, y el 34,8 % de sus filas salen de una
semana que cerró el estante en cero. El párrafo terminaba diciendo que *no está medido
cuánto vale*. Esto lo mide.

## Qué se midió

Una fila del histórico es una **(SKU, semana)**. Quedó **censurada** cuando el estante
cerró esa semana en cero: lo que ahí dice `quantity` es lo que se alcanzó a vender, no lo
que se quería comprar. Los cuatro carriles cambian qué hace Kern con esas filas, y nada
más.

| carril | clase | qué hace con la fila censurada |
|---|---|---|
| `kern` | **control** | nada — el archivo tal como sale hoy |
| `censura_omitida` | desplegable | la **borra**: no se sabe, no se inventa |
| `censura_corregida` | desplegable | la **levanta** a `max(vendido, media de las filas limpias del SKU)` |
| `sin_censura` | **diagnóstico** | la reemplaza por la **demanda verdadera** del mundo |

**La marca es observable, no la respuesta.** Los dos carriles desplegables marcan la fila
con `on_hand_end == 0` — el estante cerró vacío —, que es lo que un ERP sí tiene
(`jobs.intake` ya conoce el vocabulario de esa columna: `stock_position` lee `on_hand`,
aunque hoy solo la última fila por SKU). **Ninguno mira `out.lost`**: la venta perdida es
exactamente lo que el cliente no puede ver. Un estante en cero es un síntoma; una unidad
perdida es el diagnóstico, y confundirlos volvería desplegable a un carril que no lo es.
`test_los_carriles_desplegables_NO_miran_la_venta_perdida` lo prueba **mutando** la
columna de la verdad y exigiendo que su salida no se mueva — un grep se esquiva, eso no.

### El control reproduce las dos líneas base publicadas

| | aquí | publicado | |
|---|---:|---:|---|
| 25 mundos | **−0,137879** | −0,137879 | [`captura_limpia.json`](lineas_base/captura_limpia.json) L0 |
| 5 canónicos (1000-1004) | **−0,276239** | −0,276239 | [`horizonte_por_sku.json`](lineas_base/horizonte_por_sku.json) |

Seis decimales, dos arneses distintos. Es el mismo experimento; lo único que se movió es la
censura.

## El resultado

| carril | clase | captura | Δ vs kern | t | p | mejoran | fill | unidades | oculto |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| piso | vara | +0,0 % | — | — | — | — | 0,824 | 3.559 | 0,176 |
| **kern** | **control** | **−13,8 %** | — | — | — | — | 0,768 | 3.464 | **0,232** |
| censura_omitida | desplegable | −14,9 % | **−0,0107** | −0,47 | 0,645 | 12/25 | 0,715 | 2.919 | 0,285 |
| **censura_corregida** | **desplegable** | −11,1 % | **+0,0267** | +1,09 | 0,287 | 13/25 | 0,792 | 3.684 | 0,208 |
| **sin_censura** | **diagnóstico** | −9,9 % | **+0,0394** | +1,60 | 0,123 | 14/25 | 0,802 | 3.765 | 0,198 |

**El techo de la idea es +3,9 puntos, y no se distingue de cero.** Devolverle a Kern la
demanda verdadera —información que nadie tiene— mueve la captura +0,0394 con IC 95 %
[−0,0115, +0,0902] y mejora en **14 de 25** mundos, que es una moneda.

La lectura no es "la palanca es chica". Es: **el bucle de censura existe, y cerrarlo del
todo no alcanza para mover el marcador.**

**Y la comparación contra las otras palancas se hace con cuidado, porque H3 es sobre esto
mismo.** La fecha de fin por SKU (+16,2) y el nivel de servicio (+18,9) se midieron sobre
**cinco** mundos y ésta sobre veinticinco; restar un número de cinco contra uno de
veinticinco es justo el error que este documento documenta. Lo que sí se puede afirmar son
dos cosas, y ninguna necesita esa resta:

- **Sobre los mismos cinco mundos**, ésta lee +11,5 contra el +16,2 de aquélla — más chica,
  pero del mismo orden. Y por H3 ese +11,5 es la lectura **inflada**.
- **Sobre veinticinco**, ésta mejora en 14 de 25 mundos. Aquélla mejoraba en **5 de 5** de
  los suyos. Lo que aquellas palancas leerían sobre veinticinco mundos **no está medido**,
  y este documento no lo supone.

### Y no es que no se pudiera ver: el experimento tenía potencia de sobra

Un `p = 0,12` se lee mal de una forma específica — *"no lo detectamos"* en vez de *"no está
ahí"*—, y la diferencia se decide con la potencia, no con el p. Todos los `t` y `p` de este
documento son de una prueba **pareada por mundo** (el mismo mundo corrido por los dos
carriles) y **bilateral**. Con `sd = 0,1232` sobre 25 mundos, el efecto mínimo detectable
al 80 % de potencia y α = 0,05 es **+7,2 puntos**, y:

| si la palanca valiera… | probabilidad de haberlo detectado |
|---|---:|
| +16,2 (la fecha de fin por SKU) | **100 %** |
| +10,0 | 97 % |
| +7,2 | 80 % |

**Una palanca del tamaño de las que esta rama construye no se le habría escapado a esta
medición.** Lo que el intervalo deja abierto es si vale cero o vale cuatro puntos; lo que
cierra es que no vale dieciséis.

## H1 · La censura no es la causa. Es un síntoma — y los dos no correlacionan

Lo esperable sería que la palanca pague donde más demanda se esconde. **No es lo que pasa.**

| correlación con el Δ del techo | r |
|---|---:|
| cuánta demanda esconde el archivo (`fraccion_demanda_oculta`) | **+0,05** |
| qué tan mal le va ya a Kern (`capture` del control) | **−0,61** |

Cuánto se censura **no predice nada**. Lo que predice es qué tan rota está ya la política:

| | captura del control | Δ del techo |
|---|---:|---:|
| 8 mundos donde Kern va **peor** | −0,3553 | **+0,1035** |
| 8 mundos donde Kern va **mejor** | +0,0167 | **−0,0029** |

La semilla 1020 esconde el **38 %** de su demanda —la que más— y descensurarla le **cuesta**
0,0818. La 1023 esconde el 18 % —de las que menos— y gana +0,2580. Descensurar ayuda donde
la política ya está mal, y ahí el problema es la política.

## H2 · El mecanismo: un dólar de mercadería por cada dólar de quiebre

| componente | kern | sin_censura | Δ |
|---|---:|---:|---:|
| compra | 158.497 | 172.868 | **+14.371** |
| quiebre | 108.869 | 94.393 | **−14.476** |
| mantenimiento | 1.071 | 1.651 | +580 |
| costo fijo de ordenar | 5.388 | 2.583 | −2.805 |
| **total** | **273.824** | **271.495** | **−2.329** |
| capital pagado y sin vender al cierre | 33.919 | **41.086** | **+7.167** |

Descensurar **funciona mecánicamente**: Kern ve más demanda, compra 301 unidades más (+8,7 %)
y el fill sube 3,3 puntos (0,768 → 0,802). Lo que no hace es **pagar**: compra 14.371 de
mercadería para ahorrar 14.476 de quiebre. Es un empate. Todo el neto —2.329, el **2,5 %**
de los 92.448 que separan a Kern del techo, el **0,85 %** de su costo— sale del costo fijo
de ordenar, porque con más demanda declarada pide en lotes más grandes y menos veces.

Esto encaja exacto en la taxonomía de
[`KERN_RESIDUO_DE_LA_BRECHA.md`](KERN_RESIDUO_DE_LA_BRECHA.md): *toda palanca que baja el
quiebre deja más capital pagado y sin vender al cierre*. Descensurar es **otra palanca de
"comprá más"**, y aquel documento ya había medido que esa familia choca contra una pared.
La única que pagó fue la primera que era *"comprá menos de lo que no vas a vender"*.

## H3 · Cinco mundos habrían dicho que sí

Sobre las cinco semillas canónicas —las que miden `KERN_RESIDUO_DE_LA_BRECHA.md` y
`KERN_HORIZONTE_POR_SKU.md`— esta misma corrida dice otra cosa:

| | 5 mundos (1000-1004) | 25 mundos |
|---|---:|---:|
| `sin_censura` (techo) | **+0,1151** (3/5) | +0,0394 (14/25) |
| `censura_corregida` (desplegable) | **+0,0930** (4/5) | +0,0267 (13/25) |

**+9,3 puntos en 4 de 5** se habría publicado como una palanca de tamaño medio, comparable
con las que sí valen. Sobre 25 mundos ese mismo carril desplegable es **3,5 veces más
chico** (+0,0267) y no se distingue de cero; el techo se encoge 2,9 veces.

No es casualidad ni mala suerte: por H1 la palanca paga donde Kern ya va mal, y **tres de
las cinco semillas canónicas (1000, 1003 y 1004) están entre los ocho peores mundos**. El
subconjunto está sesgado hacia exactamente la condición que infla esta medición en
particular.

Los cinco mundos alcanzaron para las palancas anteriores porque aquéllas mejoraban en **5
de 5**; una palanca que mejora en poco más de la mitad de los mundos necesita más mundos
para decidirse. La regla que queda: **el tamaño de la muestra se elige por la dispersión
del efecto, no por costumbre.** Acá los 25 mundos costaron 17 minutos (41 s por mundo, los
cuatro carriles).

## H4 · Borrar la fila censurada es **peor** que no hacer nada

`censura_omitida` es la respuesta que esta rama aplica en todas partes —*la ausencia se
declara*— y acá **pierde**: −0,0107, y su fill cae 5,3 puntos (0,768 → 0,715).

El motivo no es ruido, es **selección**, y la distinción importa. Una fila que **no** se
agotó trae la demanda **exacta** —`advance` hace `sold = min(disponible, demanda)`, así que
si sobró stock se vendió todo lo que se pidió—, o sea que cada observación superviviente es
una medición perfecta. Lo que está sesgado es **cuáles sobreviven**: las semanas que
agotaron el estante son sistemáticamente las de demanda alta.

Medido sobre los 200 SKU-mundos del carril del **piso** (200 = 25 × 8 SKU; se usa el piso
porque es el carril congelado, así que su censura no depende de lo que la palanca haga):
la media de las supervivientes queda en el **95,9 %** de la verdadera, contra el **82,6 %**
de la media censurada cruda. Borrar recupera **tres cuartos del sesgo de nivel** — y aun
así pierde, porque lo que se lleva es la **cola**, que es justo de lo que se hace el
buffer.

Ese 95,9 % es además un **piso del daño**, no el daño: se midió sobre el piso, que esconde
el 17,6 % de su demanda, y el carril que borra terminó escondiendo el 28,5 % de la suya. La
selección que sufrió de verdad fue peor que la que se muestra acá.

Y el bucle corre para atrás: el carril que borra termina con **42,7 %** de sus filas
agotadas, contra 34,8 % del control. Compró menos, quebró más, se censuró más.

Declarar la ausencia sigue siendo correcto cuando el consumidor es **una persona** que va a
decidir con ese número. Cuando el consumidor es **un estimador**, borrar no es neutral:
cambia la distribución de la que estima. Son dos cosas distintas y esta rama las había
tratado como una sola.

## Lo que sí queda: el método, ya resuelto y gratis

`censura_corregida` recupera el **67,8 %** del techo de la idea con datos que el cliente ya
tiene. Es un resultado menor dado el tamaño del techo, pero es el que evita repetir el
trabajo: si algún día hay otra razón para descensurar, **la forma ya está medida** y es la
barata — `max(vendido, media de las filas limpias del SKU)`, marcando con el estante en
cero y no con la venta perdida. Un SKU sin ninguna fila limpia se deja intacto.

## Qué **NO** dice este documento

- **No dice que la censura no exista.** Existe, es grande (23,2 % de la demanda) y el bucle
  está confirmado: el carril que borra filas se censura todavía más. Dice que **cerrarlo no
  mueve el marcador**.
- **No mide una implementación.** La transformación es un post-proceso sobre el archivo que
  Kern lee, no un cambio de motor. Si alguna vez se construyera, toca las ocho partes de
  siempre.
- **⚠️ No mide una cartera con censura mucho más alta.** El techo se midió con el 23,2 % de
  la demanda oculta. Una operación con 50 % de quiebre está en otro régimen, y por H1 —donde
  Kern ya va mal la palanca paga— ahí podría valer más. Este número no se le promete a esa
  cartera.
- **No prueba que los 25 mundos alcancen.** Con t = +1,60 el intervalo sigue incluyendo
  tanto al cero como a +9 puntos. Lo que sí está probado es que **cinco no alcanzaban**.
- **⚠️ No re-midió las palancas anteriores sobre 25 mundos.** `+16,2` y `+18,9` siguen
  siendo números de cinco. Por H3, el candidato natural a encogerse al ampliar la muestra
  es cualquier palanca de "comprá más"; la fecha de fin por SKU no lo es —es la única de
  "comprá menos"— y mejoraba en 5 de 5, que es una dispersión distinta. Pero eso es una
  expectativa, no una medición, y no se publica como tal.
- **No mide bajo disrupción ni sobre datos sucios.** Todo es nivel 0, sin shocks.
