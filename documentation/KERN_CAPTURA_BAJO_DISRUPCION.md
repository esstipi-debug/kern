# Captura bajo disrupcion — linea base publicada

> **Estado al 2026-09-22 (tarde).** Las **dos** columnas se regrabaron hoy, en la misma
> epoca del codigo, asi que **la resta entre ellas vuelve a significar el shock** — estuvo
> suspendida cuatro dias. La limpia se movio por dos arreglos distintos y **disjuntos**:
> la cobertura provisional movio el nivel 0 y el arreglo de lectura de `jobs/intake.py`
> movio los niveles sucios.
>
> Los archivos, y cada uno dice que es:
>
> | archivo | que es |
> |---|---|
> | `captura_limpia.json` | **vigente**, con los dos arreglos |
> | `captura_bajo_shock.json` | **vigente**, misma epoca que la limpia |
> | `captura_limpia_b1e3293.json` · `captura_bajo_shock_b1e3293.json` | **congelados**: el 18-sep, antes de todo |
> | `captura_limpia_a7892b7.json` | **congelado**: con cobertura, SIN el arreglo de lectura |
> | `captura_limpia_pareada.json` | la captura **por mundo** en las dos primeras epocas |
> | `captura_bajo_shock_RECHAZADA.json` | la corrida con shock que el barrido **se nego a grabar** el 22-sep a la manana. Historia del episodio, no referencia |
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

Bajo `lead_time_spike:*:8:3:3` (**medido el 2026-09-22**, misma epoca que la limpia):

```
L0/forced: captura bajo disrupcion -20.9% +/- 13.7% · peor mundo -43.5% · 25/25 mundos medibles
L1/forced: captura bajo disrupcion -33.0% +/- 16.6% · peor mundo -59.7% · 25/25 mundos medibles
L2/forced: captura bajo disrupcion -46.9% +/- 22.1% · peor mundo -94.6% · 21/25 mundos medibles
L3/forced: captura bajo disrupcion -24.6% +/- 23.7% · peor mundo -81.9% · 21/25 mundos medibles
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
## La mitad con disrupcion estuvo bloqueada cuatro dias, y por que

Vale contarlo porque el episodio es el argumento entero a favor de tener la compuerta.

El 22-sep a la manana el barrido con shock **salio con codigo 1 y se nego a grabar**
porque `degrada_con_la_suciedad` disparo: `L3 captura -70.0% > L2 -92.9%`. La compuerta
no se puenteo, y la corrida quedo en `captura_bajo_shock_RECHAZADA.json`: **no es una
linea base** y no se puede comparar contra ella, es la historia del episodio.

Durante esos cuatro dias la columna vigente fue la del 18-sep, congelada en
`captura_bajo_shock_b1e3293.json`:

```
L0/forced: captura bajo disrupcion -56.0% +/- 17.5% · peor mundo -88.3% · 25/25 mundos medibles
L1/forced: captura bajo disrupcion -92.0% +/- 41.3% · peor mundo -171.8% · 25/25 mundos medibles
L2/forced: captura bajo disrupcion -107.3% +/- 39.0% · peor mundo -166.1% · 21/25 mundos medibles
L3/forced: captura bajo disrupcion -99.6% +/- 39.5% · peor mundo -166.5% · 21/25 mundos medibles
```

El diagnostico de entonces fue correcto y ademas encontro un defecto de verdad: **en los
niveles sucios Kern colapsaba a casi-no-pedir** — compraba 466.062 unidades en el nivel 2
contra 888.718 en el 3, y en la semilla 1013 compraba **19.780 donde el piso compraba
202.167**, sirviendo el 22 % de la demanda contra el 79 %. La suciedad del nivel 3, que
infla la serie, corregia ese colapso por accidente.

Perseguir ese colapso dio las **dos** cosas que desbloquearon esto:

1. **La causa del colapso era LECTURA**, no politica (ver *La segunda*, arriba). Con la
   lectura arreglada los tres niveles sucios mejoran entre 45 y 89 puntos.
2. **Aun arreglado, la inversion persiste** — y ahi se vio que la premisa de la compuerta
   era la que estaba mal, no el producto. Los niveles no son monotonos en distorsion.

Con las dos, el barrido volvio a grabar. **No porque la compuerta se aflojara**: la
inversion sigue superando el margen de ruido, o sea que la version vieja habria disparado
igual. Lo que cambio es que ahora hay con que explicarla, y se explica por escrito.

## El costo del shock, que estuvo cuatro dias sin poder afirmarse

Las dos columnas comparten epoca, semillas, escala y carril, asi que la resta vuelve a
significar el shock y no la diferencia entre dos versiones del codigo:

| nivel | limpia | bajo disrupcion | costo del shock |
|---|---:|---:|---:|
| **L0** | −13,8 % | −20,9 % | **−7,1 puntos** |
| L1 | −28,3 % | −33,0 % | −4,6 |
| L2 | −48,1 % | −46,9 % | **+1,2** |
| L3 | −26,3 % | −24,6 % | **+1,8** |

**El shock cuesta 7,1 puntos de captura en el nivel 0.** El 18-sep ese numero era 15,4, y
las dos cifras NO son comparables como "el shock duele menos ahora": el denominador es
otro — la brecha piso-techo se normaliza contra un Kern que hoy captura mucho mejor.

**En los niveles sucios el costo sale POSITIVO**, y eso no es que la disrupcion ayude. La
captura es una razon contra el piso, y el piso tambien sufre el shock: cuando el
denominador se degrada, una politica mala se ve menos mala. Es la misma lectura que este
documento ya hacia el 18-sep, ahora con las dos columnas medibles al mismo tiempo. Es la
razon por la que se publican las dos y no una.

## Como se lee esto, sin adornos

Captura 0 es "igual que el piso" y 1 es "igual a un oraculo que ve el futuro". Un −13,8 %
con datos limpios quiere decir que, en promedio sobre 25 mundos, la politica que Kern
propone cuesta un 13,8 % de la brecha piso-techo MAS que el piso.

**Con datos limpios Kern ya no decide uniformemente peor que un punto de reorden fijo.**
El mejor mundo sale **+12,7 %** y el p90 **+1,0 %**: hay mundos donde su politica cuesta
menos que el piso. Sigue costando mas en promedio.

**Con datos sucios sigue costando bastante mas, y bajo disrupcion tambien.** Ningun nivel
sale positivo con el shock encima. El titular honesto de hoy no es "Kern gana": es que la
distancia se acorto mucho —de −40,6 % a −13,8 % en limpio, y de −117,0 % a −28,3 % en el
primer nivel de suciedad— y que en limpio el reparto ya incluye mundos donde gana.

**Lo que este numero NO incluye** esta en *Que puede y que no puede incluir este numero*,
arriba: dos de las tres palancas medidas en la rama viven en `scripts/` y no en el motor.

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
