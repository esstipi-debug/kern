# El horizonte de la vara: cuánto del residuo no es de Kern

> Medido el 2026-09-21 sobre `0471d92`. Línea base congelada en
> [`lineas_base/horizonte_de_la_vara.json`](lineas_base/horizonte_de_la_vara.json),
> regrabable con `scripts/horizonte_de_la_vara.py`. El documento no puede despegarse de
> ella: `tests/test_horizonte_publicado.py` lo compara contra su fuente.

## La pregunta

[`KERN_RESIDUO_DE_LA_BRECHA.md`](KERN_RESIDUO_DE_LA_BRECHA.md) cerró con una deuda
escrita en su propia sección de límites:

> **No diagnostica el efecto de fin de horizonte por separado.** Castiga a cualquier
> política contra un oráculo que sabe cuándo termina la corrida, y por eso parte del
> residuo es de la vara y no de Kern. Cuánta parte queda abierto.

El razonamiento era sólido de forma: el techo es un Wagner-Whitin sobre
`horizon = world.weeks`, o sea que **sabe la fecha de fin** y cierra con stock terminal
cero; ningún punto de reorden puede hacer eso. Suena a que una parte del −27,6 % es
artefacto de medición y no una decisión mala.

Esto lo mide. Importa el orden: **antes** de darle horizonte a Kern hay que saber cuánto
del residuo se lo debe la vara, porque si fuera casi todo, "darle noción de horizonte"
sería enseñarle a ganarle al marcador en vez de a comprar mejor.

## La respuesta corta

**El efecto de fin de horizonte no explica el residuo.** Con el borde de la ventana
acreditado, mover la fecha de fin del mundo —de "se acaba en la semana 24" a "quedan 24
semanas más"— mueve la captura de Kern **−0,0054**: el 2,0 % del residuo que se quería
explicar, y para el lado peor, no mejor.

Lo que sí es enorme es la **convención de puntuación**: sin acreditar el borde, ese
mismo eje mueve la captura **−0,4051**. Ese efecto aparente no es el horizonte — es
decidir que lo comprado y no vendido al cerrar la ventana vale cero.

> Con el borde acreditado, darle al techo 24 semanas de futuro en vez de la fecha de fin mueve la captura de Kern -0.0054 -- el 2.0% del residuo de -0.2762 que se queria explicar. Sin acreditarlo el mismo eje se mueve -0.4051, o sea que ese efecto aparente es la convencion de puntuacion y no el horizonte. El efecto de fin de horizonte NO explica el residuo: la vara no se lo debe a Kern. (La cota superior del credito se degenera en 1 de 3 fechas de fin y por eso no sostiene sola ninguna conclusion.)

## H1 · Por qué se cancela: la captura se normaliza contra el PISO

El mecanismo es más simple que el experimento y explica los dos caminos de abajo.

`capture = (piso − kern) / (piso − techo)`. El castigo de fin de horizonte lo sufren
**los dos** términos del numerador: el piso es tan ciego al horizonte como Kern. Lo que
sobrevive a la resta no es el castigo sino su **diferencia**, que es chica. La columna
`terminal` de la línea base anterior se recolectó justamente para exponer ese artefacto
—su docstring dice que está para "distinguir una decisión mala de un artefacto de medir
sobre un horizonte que se acaba"—, y resulta que la normalización ya lo había
neutralizado antes de que nadie mirara la columna.

## H2 · Camino 1: neutralizar el borde sobre las corridas ya publicadas

Aritmética sobre `residuo_de_la_brecha.json`: mismas corridas, otra convención de borde.
Se acredita el stock terminal a una tasa de rescate `s` y se recalcula la captura **por
semilla** antes de promediar, porque es un cociente.

| carril | s=0,00 | s=0,25 | s=0,50 | s=0,75 | s=1,00 |
|---|---:|---:|---:|---:|---:|
| **kern** | **−0,2762** | −0,2758 | −0,2758 | −0,2763 | **−0,2775** |
| plazo_obs | −0,2522 | −0,2324 | −0,2118 | −0,1905 | −0,1684 |
| sigma_obs | −0,2261 | −0,2062 | −0,1855 | −0,1640 | −0,1415 |
| proveedor_medido | −0,2278 | −0,1918 | −0,1538 | −0,1136 | −0,0709 |
| servicio_econ | −0,0875 | −0,0324 | +0,0255 | +0,0866 | +0,1513 |
| todo | −0,1862 | −0,0998 | −0,0085 | +0,0884 | +0,1914 |

Borrar por completo el castigo mueve al control **−0,0013**: cinco diezmilésimas de la
captura, el 0,5 % del residuo, y hacia abajo. Los carriles contrafácticos sí se mueven
—compran más, dejan más stock, y el crédito se los devuelve—, que es exactamente el
mecanismo que el documento anterior ya había medido: toda palanca que baja el quiebre
empuja el excedente al stock terminal.

Este camino tiene un límite que el camino 2 corrige: la columna publicada cuenta **solo
el estante** (ver H4).

## H3 · Camino 2: la misma demanda, otra fecha de fin

El camino 1 es aritmética sobre corridas viejas. Este mueve la fecha de fin de verdad.

**El diseño.** Se sortea un mundo de `24 + 24` semanas y se lo **trunca** con
`dataclasses.replace` — transformación pura sobre un mundo ya sorteado, el mismo
contrato que `src/simworld/shocks.py`: no toca el `rng`. Volver a sortear con
`weeks=24` **no serviría**: `_seasonal_trend_demand` normaliza la tendencia por
`max(weeks, 1)` y sortea el ruido en un bloque `(n_skus, weeks)`, así que un mundo de 24
semanas y uno de 48 con la misma semilla difieren **también en sus primeras 24**. Con el
truncado, las tres fechas de fin comparten la realización de `[0,24)` unidad por unidad.
Se corren los tres carriles en cada una y se puntúa **siempre** la ventana `[0,24)`.

**El control que lo hace creíble.** Piso y Kern son ciegos al horizonte: sus costos sobre
`[0,24)` tienen que salir **idénticos** entre las tres corridas. Salen idénticos en las
cinco semillas, y el script se planta antes de publicar si alguna vez no lo fueran — si
difirieran, la fecha de fin les habría llegado por algún camino y la diferencia que este
experimento le atribuye al techo sería de cualquier otra cosa.

| cola K | costo del techo | borde del techo | captura s=0 | **captura s=1** | s=1 a precio |
|---|---:|---:|---:|---:|---:|
| 0 (se acaba en 24) | 154.136 | **0** | −0,2647 | **−0,3942** | −0,4627 (4/5) |
| 12 | 189.932 | 35.204 | −0,5074 | **−0,3990** | −0,4989 |
| 24 | 193.992 | 39.249 | −0,6698 | **−0,3996** | −0,3748 |

La columna que importa es la de `s=1`: **−0,3942 → −0,3990 → −0,3996**. Plana. La de
`s=0` va de −0,26 a −0,67 sobre las mismas corridas, y esa diferencia es íntegramente la
convención de borde.

Por semilla, con el borde acreditado, y al lado el mismo eje sin acreditarlo:

| semilla | K=0 | K=12 | K=24 | **Δ acreditado** | Δ sin acreditar |
|---|---:|---:|---:|---:|---:|
| 1000 | −0,2688 | −0,2733 | −0,2730 | **−0,0043** | −0,0814 |
| 1001 | +0,1407 | +0,1429 | +0,1426 | **+0,0019** | −0,1425 |
| 1002 | −0,0625 | −0,0637 | −0,0638 | **−0,0013** | −0,2741 |
| 1003 | −1,3057 | −1,3205 | −1,3228 | **−0,0171** | −0,5410 |
| 1004 | −0,4749 | −0,4803 | −0,4812 | **−0,0063** | −0,9865 |

El efecto acreditado es chico en los **cinco** mundos y no cambia de orden de magnitud;
el efecto sin acreditar va de −0,08 a −0,99. No es que el promedio esconda dispersión:
son dos fenómenos distintos.

Estas capturas **no son comparables** contra el −27,6 % publicado: el mundo de 48
semanas truncado a 24 no es el mundo de 24 de la línea base anterior, por lo mismo que
obliga a truncar en vez de re-sortear. La comparación que este camino sostiene es
**interna** — K=0 contra K=24 sobre la misma realización.

## H4 · El sesgo que apareció midiendo, y que alcanza a lo ya publicado

La primera versión de este experimento acreditaba el borde contando **solo el estante**,
y daba que darle futuro al techo empeoraba la captura de Kern en −0,20. El número no
cerraba: el techo con futuro gastaba 39.856 más dentro de la ventana y solo aparecían
14.679 de stock en el estante. **¿Dónde estaba el resto?**

En la **cañería**. `score_run` cobra `procurement` de toda unidad **ordenada**, arribe o
no dentro de la ventana; las que siguen viajando al cerrarla están pagadas, no vendidas,
y no aparecen en `on_hand_end`. Y el sesgo no es parejo entre carriles: un techo que sabe
que el mundo se acaba en 24 deja de ordenar y llega al borde con la cañería **vacía**
—medido: cero exacto en las cinco semillas—, mientras que uno con futuro por delante
ordena hasta la última semana y llega con ella llena. Sin ese término, el experimento
castigaba al techo por tener futuro y le atribuía la diferencia al horizonte.

Se reconstruye por identidad de flujo: `initial_state` arranca sin nada en tránsito, así
que lo que sigue viajando es lo ordenado menos lo recibido.

**Esto alcanza a la línea base anterior.** `stock_terminal_usd` en
`residuo_de_la_brecha.json` y en los `por_que_bajo_el_piso*.json` sale de
`por_que_bajo_el_piso._componentes`, que medía `outs[-1].on_hand_end` — el estante y nada
más. Medida sobre piso y Kern en estos mundos, la cañería es **1,8× el estante**
(18.407 contra 10.061), o sea que la columna publicada se perdía cerca de dos tercios de
lo pagado y no vendido. La conclusión del camino 1 no cambia por eso (la cañería también
viaja en los dos términos del numerador), pero **la sección R3.3 de aquel documento
—"el stock terminal sube monótonamente con cada palanca"— estaba medida sobre la mitad
chica del número**.

**Medido después, el 2026-09-21, y la monotonía es falsa.** `_componentes` gana
`canieria_usd` y `pagado_no_vendido_usd` —sin redefinir `stock_terminal_usd`, que mide el
estante y eso es lo que su nombre dice— y la línea base del residuo se regrabó. Control:
**los 0 campos que ya existían cambiaron de valor**, resumen y por semilla, así que es el
mismo experimento con una lente más completa. Con el número completo la secuencia
publicada no es monótona en **ninguno de los cinco mundos**, y la palanca más grande
(`servicio_econ`, +18,9 puntos) deja **menos** capital inmovilizado que una cuatro veces
más chica. El estante subía porque las palancas **mueven capital de la cañería al
estante**: la razón cañería/estante cae de 2,3 a 0,5. Detalle en
[R3.3 del residuo](KERN_RESIDUO_DE_LA_BRECHA.md).

## H5 · Por qué la cota superior no sostiene sola ninguna conclusión

Puntuar una ventana finita **no tiene una convención neutra**: lo que queda en el borde
vale su margen futuro, que la ventana no ve. Acreditarlo a `unit_cost` devuelve lo pagado
y nada más (cota inferior); acreditarlo a `price` regala el ingreso completo sin el costo
de servirlo (cota superior). Por eso se publican las dos.

La cota superior se degenera justo donde más falta haría. Con la fecha de fin conocida el
techo llega al borde **sin nada que acreditar**, mientras el piso cobra el suyo a precio
de venta, y en la semilla 1003 la vara se da vuelta: el piso pasa a costar 7.647 **menos**
que el techo y el span se vuelve negativo. `captura` devuelve `None` ahí, nunca un `nan`
—un `nan` promediado sale publicado como un número— y el resumen lleva el conteo de
mundos medibles: **4 de 5** en esa fila. Por eso todo lo de arriba se apoya en la
valuación a costo, que está definida en los quince casos.

## Lo que esto dice que hay que hacer

**Darle noción de horizonte a Kern ya no se puede justificar como cerrar un artefacto de
medición.** Ese era el argumento disponible y la medición lo saca de la mesa: la vara no
le debe el residuo. Si se construye, hay que justificarlo como comprar mejor.

Y ahí aparece la pregunta de producto que el marcador no puede contestar: **la cadena de
un cliente real no se acaba**. No hay una fecha de fin que conocer, así que una política
que apunte a la del marcador estaría optimizando contra el arnés. La versión desplegable
de "horizonte" es otra cosa: una **fecha de fin real por SKU** —discontinuación, fin de
temporada, cierre de ventana promocional— que el archivo del cliente tendría que traer, y
que sin el dato se declara ausente en vez de suponerse. Es la forma de todas las demás
capacidades de esta rama, y `launch_readiness` ya netea contra una fecha.

## Qué NO dice este documento

- **No mide los 25 mundos de la línea base, sino 5** (semillas 1000-1004), y en mundos
  generados a 48 semanas, no los 24 de la línea base publicada. Las capturas absolutas de
  H3 no son comparables contra el −27,6 %; lo comparable es el eje interno K=0 → K=24.
- **No re-mide el residuo con el borde completo.** El camino 1 corre sobre la columna
  publicada, que es solo el estante. La conclusión aguanta porque la cañería se cancela
  en la normalización igual que el estante, pero el número −0,0013 es de un crédito
  incompleto.
- **No dice que una política con horizonte no sirva.** Dice que su justificación no puede
  ser el artefacto de medición. Cuánto ganaría una política con una fecha de fin real
  sigue sin medirse.
- **No revisó la monotonía del stock terminal de R3.3** — eso se midió después, el
  2026-09-21, y está en H4: la monotonía es falsa en los cinco mundos.
- **`KERN_POR_QUE_BAJO_EL_PISO.md` se corrigió después**, el 2026-09-21: su línea base
  del *después* se regrabó con las tres columnas (cero cambios en los campos
  preexistentes) y sus cuatro afirmaciones de cierre se re-escribieron. Una de ellas
  estaba **al revés** —citaba el Kern de antes del arreglo llamándolo "hoy", y con ese
  número la conclusión se invertía—; con el número completo se sostiene y refuerza lo de
  acá: Kern inmoviliza **más** que el piso (30.440 contra 25.017, en 4 de 5 mundos), que
  es exactamente por qué acreditar el borde lo deja peor. Lo que **no** se pudo completar
  es el *diagnóstico*: está congelado pre-arreglo y ese código ya no existe.
