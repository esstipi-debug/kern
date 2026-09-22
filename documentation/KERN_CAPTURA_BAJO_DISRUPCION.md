# Captura bajo disrupcion — linea base publicada

> **Estado al 2026-09-22 (tarde).** La columna LIMPIA se regrabo dos veces hoy, por dos
> arreglos distintos y **disjuntos**: la cobertura provisional movio el nivel 0 y el
> arreglo de lectura de `jobs/intake.py` movio los niveles sucios. La columna BAJO
> DISRUPCION sigue siendo la del 2026-09-18 @ `b1e3293`. Mientras las dos columnas sean
> de epocas distintas, **la resta entre ellas esta suspendida**: restar un limpio de hoy
> contra un shock de hace cuatro dias no mide el shock, mide dos epocas del codigo.
>
> Los archivos, y cada uno dice que es:
>
> | archivo | que es |
> |---|---|
> | `captura_limpia.json` | **vigente**, con los dos arreglos |
> | `captura_bajo_shock.json` | **vigente**, medida el 18-sep — no regrabable hoy |
> | `captura_limpia_b1e3293.json` · `captura_bajo_shock_b1e3293.json` | **congelados**: el 18-sep, antes de todo |
> | `captura_limpia_a7892b7.json` | **congelado**: con cobertura, SIN el arreglo de lectura |
> | `captura_limpia_pareada.json` | la captura **por mundo** en las dos primeras epocas |
> | `captura_bajo_shock_RECHAZADA.json` | la corrida con shock que el barrido **se nego a grabar**. Evidencia, no referencia |
>
> Desde hoy cada distribucion lleva ademas su **fidelidad**: que fraccion de la demanda
> verdadera le llego al motor por la lente. Sin ese numero, dos niveles solo se podian
> comparar por su ordinal — y un ordinal cuenta defectos, no distorsion.
>
> `tests/test_captura_bajo_disrupcion_publicada.py` falla si este documento y esos
> archivos dejan de decir lo mismo.

## Que se midio

Dos barridos de **25 mundos** (semillas 1000-1024), identicos en todo salvo una cosa:

| | escala | shock |
|---|---|---|
| limpio | 8 SKU · 24 semanas · replan 4 · niveles 0-3 · carril `forced` | — |
| bajo disrupcion | idem, mismas semillas | `lead_time_spike:*:8:3:3` |

El shock es un salto de lead time de **3 semanas de magnitud** sostenido **3 semanas**
desde la semana 8, sobre **todos** los SKU. Se elige `*` y no un SKU puntual a
proposito: un shock apuntado a un SKU que no se pide dentro de la ventana no llega al
carril, y sale `NO ALCANZADO` en vez de medir algo (ver `ShockImpact.reached`).

Reproducir:

```bash
PYTHONPATH=. python scripts/verify_fidelidad.py --mundos 25 \
    --grabar-linea-base documentation/lineas_base/captura_limpia.json
PYTHONPATH=. python scripts/verify_fidelidad.py --mundos 25 \
    --shock 'lead_time_spike:*:8:3:3' \
    --grabar-linea-base documentation/lineas_base/captura_bajo_shock.json
```

Tarda ~23 y ~45 minutos respectivamente: con shock cada mundo corre **dos veces**
(limpio y perturbado), que es el precio de poder atribuirle la diferencia al shock.

## Lo que se puede afirmar

Con datos limpios (**medido el 2026-09-22**):

```
L0/forced: captura -13.8% +/- 17.8% · peor mundo -59.6% · 25/25 mundos medibles
L1/forced: captura -28.3% +/- 20.4% · peor mundo -59.8% · 25/25 mundos medibles
L2/forced: captura -48.1% +/- 28.6% · peor mundo -111.7% · 21/25 mundos medibles
L3/forced: captura -26.3% +/- 25.5% · peor mundo -84.5% · 21/25 mundos medibles
```

Bajo `lead_time_spike:*:8:3:3` (**medido el 2026-09-18, NO regrabable hoy**):

```
L0/forced: captura bajo disrupcion -56.0% +/- 17.5% · peor mundo -88.3% · 25/25 mundos medibles
L1/forced: captura bajo disrupcion -92.0% +/- 41.3% · peor mundo -171.8% · 25/25 mundos medibles
L2/forced: captura bajo disrupcion -107.3% +/- 39.0% · peor mundo -166.1% · 21/25 mundos medibles
L3/forced: captura bajo disrupcion -99.6% +/- 39.5% · peor mundo -166.5% · 21/25 mundos medibles
```
## Tres epocas, dos palancas, y pegan en lugares disjuntos

La linea base del 18-sep era anterior a **dos** arreglos, y lo notable es que cada uno
movio lo que le tocaba y nada mas:

| | L0 | L1 | L2 | L3 |
|---|---:|---:|---:|---:|
| **18-sep** `b1e3293` | −40,6 % | −117,0 % | −132,8 % | −122,6 % |
| **+ cobertura provisional** `a7892b7` | **−13,8 %** | −117,0 % | −116,8 % | −91,9 % |
| **+ lectura arreglada** (hoy) | −13,8 % | **−28,3 %** | **−48,1 %** | **−26,3 %** |

La **cobertura provisional** arreglo el arranque muerto, que solo existe con datos
limpios: +26,8 puntos en L0 y casi nada en los sucios. El **arreglo de lectura** dejo L0
**bit a bit identico** — 48 campos, cero diferencias — y movio los tres sucios entre 65 y
89 puntos. Dos palancas que no se pisan, cada una con su control.

### La primera: la cobertura provisional (L0)

**−40,6 % → −13,8 %. +26,8 puntos**, y no es una media que esconda dispersion:

| | 18-sep | con cobertura | Δ |
|---|---:|---:|---:|
| media | −40,6 % | **−13,8 %** | **+26,8** |
| mediana | −36,4 % | −6,3 % | +30,1 |
| p90 | −26,3 % | **+1,0 %** | +27,2 |
| mejor mundo | −15,9 % | **+12,7 %** | +28,7 |
| peor mundo | −71,9 % | −59,6 % | +12,3 |

**Mejora en 25 de 25 mundos** (`captura_limpia_pareada.json`), minimo +9,4 puntos, maximo
+42,6, mediana +26,7; sd del delta pareado 10,0 puntos, t = 13,5. Y **cuatro mundos**
(1005, 1010, 1016, 1022) tienen captura **positiva**: ahi la politica de Kern cuesta MENOS
que el piso.

**El control.** El mismo barrido corrido en un worktree de `b1e3293` reproduce el archivo
congelado **bit a bit**: 48 campos, **cero diferencias**. Las dependencias de este repo se
resuelven frescas en cada corrida — ya se puso rojo por un release de upstream sin un
commit propio —, asi que la deriva del entorno era una explicacion viva. No lo es. Segundo
amarre: las dos capturas por semilla que `5408b2f` dejo escritas (1003 → −0,6392 y 1004 →
−0,7190) coinciden al digito con la mitad "antes".

Lo que decia el 18-sep, congelado en `captura_limpia_b1e3293.json`:

```
L0/forced: captura -40.6% +/- 14.5% · peor mundo -71.9% · 25/25 mundos medibles
L1/forced: captura -117.0% +/- 50.7% · peor mundo -213.9% · 25/25 mundos medibles
L2/forced: captura -132.8% +/- 45.5% · peor mundo -198.9% · 21/25 mundos medibles
L3/forced: captura -122.6% +/- 50.3% · peor mundo -202.3% · 21/25 mundos medibles
```

### La segunda: la suciedad COSMETICA cambiaba el dato (L1, L2, L3)

`src/simworld/lens.py` declara sus tres inyectores de nivel 1 como **"cosmetico puro"**.
No lo eran para `jobs/intake.py`, y el precio estaba a la vista sin que nadie lo leyera:
el nivel 1 mejoro **+0,0 puntos en los 25 mundos** cuando llego la cobertura provisional
— porque esa cobertura solo se dispara cuando el motor se NIEGA, y a nivel 1 el motor no
se negaba. Emitia una politica confiada sobre una serie que se habia inventado.

Dos causas, aisladas corriendo cada inyector por separado sobre el archivo real de la
semilla 1000 a la semana 20, donde el nivel "cosmetico" devolvia 158 filas, **16 SKU**
(son 8), **912 unidades de 2.596** y un calendario que terminaba el 1-dic en vez del
19-may:

- `" sku-1003"` y `"SKU-1003"` eran **dos productos**. `.str.strip()` limpia el espacio y
  no la caja, asi que cada serie perdia la mitad de su historia.
- `"06/01/2025"` (6 de enero, `dd/mm/yyyy`) se parseaba **month-first** → 1 de junio. Una
  sola fecha estiraba la grilla semanal de 4 periodos a 21 rellenos de ceros fabricados, y
  con eso cruzaba `MIN_PERIODS_FOR_POLICY`: **el motor dejaba de negarse.**

La especificacion del arreglo es el invariante, no las piezas: **si el nivel 1 es
cosmetico de verdad, tiene que leerse IGUAL que el nivel 0.** Ahora se cumple.

| nivel | antes | con la lectura arreglada | mundos que mejoran |
|---|---:|---:|---|
| L1 | −117,0 % | **−28,3 %** | 24/25 · t = 10,1 |
| L2 | −116,8 % | **−48,1 %** | **21/21** · minimo +20,5 |
| L3 | −91,9 % | **−26,3 %** | 20/21 · t = 8,7 |

Y el control de que toca solo lo que debe: **L0 sale bit a bit identico** en las dos
condiciones. Lo que decia antes del arreglo, congelado en `captura_limpia_a7892b7.json`:

```
L1/forced: captura -117.0% +/- 50.7% · peor mundo -213.9% · 25/25 mundos medibles
L2/forced: captura -116.8% +/- 42.0% · peor mundo -192.3% · 21/25 mundos medibles
L3/forced: captura -91.9% +/- 40.7% · peor mundo -154.8% · 21/25 mundos medibles
```

### Que puede y que no puede incluir este numero

De las tres mejoras medidas en esta rama antes de hoy, la linea base incluye **una**: la
cobertura provisional, que esta en el motor. El **nivel de servicio** (+18,9 puntos) es un
CARRIL de `scripts/residuo_de_la_brecha.py` y la **fecha de fin por SKU** (+16,2) un
POST-PROCESO en `scripts/horizonte_por_sku.py`; ninguna existe fuera de `scripts/`.

**La prediccion escrita antes de medir se quedo corta.** Extrapolando desde los cinco
primeros mundos (−47,0 % → −27,6 %, +19,4) y sabiendo que esos cinco eran 6,4 puntos
peores que el promedio, la estimacion era **−21,2 %**. Salio −13,8 %: la extrapolacion
desde cinco mundos **subestimaba** la palanca en 7,4 puntos.

## La tabla no esta ordenada por su ordinal, y la compuerta lo dice

L3 captura mejor que L2 (**−26,3 %** contra **−48,1 %**) y eso **no es una anomalia**.
Cada distribucion lleva desde hoy su fidelidad — que fraccion de la demanda verdadera le
llego al motor:

| nivel | captura | fidelidad | error \|1−f\| |
|---|---:|---:|---:|
| L0 | −13,8 % | 1,000 | 0,000 |
| L1 | −28,3 % | 1,000 | 0,000 |
| L2 | −48,1 % | 0,634 | **0,366** |
| L3 | −26,3 % | 0,857 | **0,143** |

**El archivo de nivel 3 se equivoca la mitad que el de nivel 2**, porque sus inyectores
INFLAN la serie (`inject_duplicate_rows` 101,2 %, `inject_unmarked_promo` 114,9 %) mientras
los del 2 la DESINFLAN (`inject_mixed_units` 79,9 %, `inject_calendar_gaps` 95,0 %). Kern
comprando mas al leer L3 es la respuesta CORRECTA a un archivo mas fiel. Los niveles son
monotonos en **cantidad de defectos**, no en **distorsion**, y hasta hoy
`degrada_con_la_suciedad` confundia las dos cosas: acusaba por el ordinal.

La alarma conserva sus dientes — sigue acusando si la captura mejora con un archivo igual
de fiel o peor, que es la forma exacta del defecto que atrapo el 18-sep, y tambien si no
hay fidelidad medida. Y la exencion **no se traga en silencio**: el barrido imprime la
inversion aparte, con las dos fidelidades nombradas.

## La mitad con disrupcion no se pudo regrabar

Se corrio. `verify_fidelidad.py` **salio con codigo 1 y se nego a grabar**, porque
`degrada_con_la_suciedad` disparo:

```
forced: L3 captura -70.0% > L2 -92.9% (margen 20.8%) -- ensuciar mejoro el resultado
```

Esa compuerta existe para eso y no se puentea: *"investigar antes de citar ningun numero
de esta tabla"*. La corrida rechazada queda en
`captura_bajo_shock_RECHAZADA.json` como evidencia del diagnostico — **no es una linea
base y no se puede comparar contra ella**. Lo que habria dicho:

```
L0/forced: captura bajo disrupcion -20.9% +/- 13.7% · peor mundo -43.5% · 25/25 mundos medibles
L2/forced: captura bajo disrupcion -92.9% +/- 35.0% · peor mundo -166.1% · 21/25 mundos medibles
L3/forced: captura bajo disrupcion -70.0% +/- 32.2% · peor mundo -128.1% · 21/25 mundos medibles
```

**El diagnostico, y no es una fuga.** La lente no toca el marcador en ningun punto; la
suciedad entra por el camino disenado (el dato que Kern lee). Lo que pasa es esto, medido
sobre los 21 mundos medibles del barrido limpio:

| | compra total | quiebre total | fill medio |
|---|---:|---:|---:|
| L2 | 466.062 | 7.145.259 | 0,251 |
| L3 | **888.718** (+91 %) | 6.288.795 (−857.000) | 0,334 |

La suciedad que agrega el nivel 3 (`inject_duplicate_rows`, `inject_unmarked_promo`)
**infla la demanda aparente**, Kern compra casi el doble, y eso le ahorra mas quiebre del
que le cuesta comprar. Mejora porque **el error que corrige es enorme**: en los niveles
sucios Kern colapsa a casi-no-pedir. En la semilla 1013 compra **19.780 donde el piso
compra 202.167** — una decima parte — y sirve el 22 % de la demanda contra el 79 % del
piso. Cualquier cosa que infle la demanda ayuda.

**No es nuevo, es mas grande.** El mismo orden invertido ya estaba en las dos lineas base
del 18-sep: L3 por encima de L2 en **+10,2 puntos** limpio — y ahi el pareado por mundo
dice que ya pasaba en **15 de 21** — y **+7,7** bajo shock, donde solo hay agregados. Lo
que hizo la cobertura provisional fue llevarlo a +24,9 y +22,9, y ahi cruzo el umbral de
ruido.

**⚠️ Y la columna limpia paso la misma compuerta por 0,6 puntos.** Su L2→L3 es +24,9
contra un margen de 25,5. O sea que el aviso vale igual para las filas L2 y L3 de la
tabla limpia: son numeros sobre un carril que ya esta colapsado. **La fila L0 no esta
implicada** — de L0 a L1 la captura se desploma 103 puntos, que es la direccion correcta
— y el titular de este documento es L0.

## Como se lee esto, sin adornos

Captura 0 es "igual que el piso" y 1 es "igual a un oraculo que ve el futuro". Un
−13,8 % con datos limpios quiere decir que, en promedio sobre 25 mundos, la politica que
Kern propone cuesta un 13,8 % de la brecha piso-techo MAS que el piso.

**Con datos limpios Kern ya no decide uniformemente peor que un punto de reorden fijo.**
El mejor mundo sale +12,7 % y el p90 +1,0 %: hay mundos donde su politica cuesta menos
que el piso. Sigue costando mas en promedio, y en los niveles sucios sigue costando
mucho mas.

El costo del shock **no se puede afirmar hoy**, por las dos razones de arriba: la columna
con disrupcion es de otra epoca, y su regrabacion esta bloqueada por la anomalia.
## Lo que la regrabacion encontro sin buscarlo, y donde quedo

Las dos cosas que aparecieron midiendo **ya estan arregladas**, y las dos salieron de
interrogar un numero que no cerraba (el +0,0 de L1 en los 25 mundos):

1. **Una fecha ambigua apagaba la negativa del motor.** Arreglado: `_slash_date_order`
   lee el orden de la propia columna. Detalle en *La segunda*, arriba.
2. **Dos grafias del mismo codigo eran dos productos.** Arreglado: `_fold_ids` pliega por
   identidad y declara lo plegado.

**Lo que NO se arreglo, y es la capa que queda.** `lens.history_frame` exporta
`out.sold` — *"lo efectivamente vendido"* —, no la demanda. Una politica mala causa
quiebre, el quiebre **censura** el historico, y la politica siguiente sale mas chica
todavia. Se ve en el dato: antes del arreglo, a la semana 4 el nivel 1 traia el 100 % de
las unidades del nivel 0 y a la semana 20 el **35 %**. El piso no sufre eso porque
`naive_policy` es **congelada** (*"sin revisar nunca"*): lee el archivo censurado una sola
vez, en el calentamiento. **El bucle solo muerde al carril que replanifica**, y es inmune
por estar congelado, no por ser mas listo.

Ajustar demanda contra ventas censuradas tiene nombre propio en la teoria de inventarios,
y cerrarlo es una capacidad — no un arreglo de lectura. No esta medido cuanto vale.

## Que NO dice este documento

- **No dice que Kern sea mejor o peor que un punto de reorden fijo en un cliente real.**
  Dice que lo es *en este arnes*: mundos sinteticos, un solo carril (`forced`), una
  politica por replanificacion cada 4 semanas, y un piso que es un reorden fijo bien
  calibrado sobre la misma demanda. Sirve para detectar regresiones y para no venderse
  de mas, no para estimar el resultado de una implantacion.
- **No explica POR QUE** Kern queda por debajo del piso con datos limpios. Eso lo
  contesta [`KERN_POR_QUE_BAJO_EL_PISO.md`](KERN_POR_QUE_BAJO_EL_PISO.md) (2026-09-19),
  sobre los cinco primeros mundos de esta misma linea base: **el 56 % de la brecha es
  que Kern no puede dimensionar durante el primer tercio de la corrida**, y la
  hipotesis de que le falta buffer quedo REFUTADA -- con la cobertura exacta del piso
  captura peor, porque el dinero extra termina en stock terminal. Lo que sigue abierto
  es el residuo: con el arranque arreglado Kern queda en −27,6 % y esa parte ya es una
  ineficiencia de asignacion entre SKU y semanas.
- **No cubre otros shocks.** `supplier_outage` y `demand_surge` existen en
  `src/simworld/shocks.py` y no tienen linea base grabada.
- **No mide las dos palancas que no estan en el motor** (nivel de servicio, fecha de fin
  por SKU), como dice *Que puede y que no puede incluir este numero*.

## Por que estos numeros no se parecen a los que este repo publicaba antes

Hasta el 2026-09-18 el repo publicaba **−0,446 (L0) y +0,437 (L1)** a otra escala
(12 semanas, semilla unica). Dos cosas cambiaron, y conviene no mezclarlas:

1. **La escala.** Aquellos son 12 semanas y una semilla; estos son 24 semanas y 25
   semillas. No son comparables entre si, y citar uno en lugar del otro es un error de
   lectura, no una regresion.
2. **La vara.** `shortage` valuaba la venta perdida a `margin = price - unit_cost`
   mientras `procurement` ya cobraba `unit_cost`: el costo unitario se restaba **dos
   veces** de cada unidad servida, asi que servir era contablemente peor que no servir
   en todo SKU con `price < 2*unit_cost` — el 42,86 % del catalogo que `generate_world`
   sortea. El oraculo pedia exactamente cero en esos SKU, el techo servia MENOS que el
   piso, y una politica de **no comprar nunca nada** sacaba captura +0,375. Corregido en
   `7aa08c5` (ver la nota larga de `src/simworld/scorer.py::score_run`).

El efecto de (2) sobre la lectura es el que importa: el numero viejo era **mas favorable
a Kern de lo que el arnes soporta**, porque premiaba sub-servir. Con la vara arreglada,
a la escala del loop la captura pasa de −0,446 a **−0,6019** (L0) y de +0,437 a
**−0,0180** (L1). El arreglo no empeoro el producto; dejo de halagarlo.

Y el −40,6 % del 18-sep, que este documento publico durante cuatro dias como el titular,
tampoco era comparable con el de hoy por una tercera razon, la unica buena: **el producto
cambio**. Queda congelado en `captura_limpia_b1e3293.json`.
