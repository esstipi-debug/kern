# Captura bajo disrupcion — linea base publicada

> **Estado al 2026-09-22.** La columna LIMPIA se regrabo hoy. La columna BAJO
> DISRUPCION **no se pudo regrabar**: el propio barrido se nego (ver *La mitad con
> disrupcion no se pudo regrabar*), asi que la vigente sigue siendo la del 2026-09-18
> @ `b1e3293`. Mientras las dos columnas sean de epocas distintas, **la resta entre
> ellas esta suspendida**: restar un limpio de hoy contra un shock de hace cuatro dias
> no mide el shock, mide dos epocas del codigo.
>
> Seis archivos, y cada uno dice que es:
>
> | archivo | que es |
> |---|---|
> | `captura_limpia.json` | **vigente**, medida hoy |
> | `captura_bajo_shock.json` | **vigente**, medida el 18-sep — no regrabable hoy |
> | `captura_limpia_b1e3293.json` · `captura_bajo_shock_b1e3293.json` | **congelados**: lo que el repo publicaba el 18-sep. Nadie los regraba |
> | `captura_limpia_pareada.json` | la captura **por mundo** en las dos epocas: el pareado |
> | `captura_bajo_shock_RECHAZADA.json` | la corrida con shock que el barrido **se nego a grabar**. Evidencia, no referencia |
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
L1/forced: captura -117.0% +/- 50.7% · peor mundo -213.9% · 25/25 mundos medibles
L2/forced: captura -116.8% +/- 42.0% · peor mundo -192.3% · 21/25 mundos medibles
L3/forced: captura -91.9% +/- 40.7% · peor mundo -154.8% · 21/25 mundos medibles
```

Bajo `lead_time_spike:*:8:3:3` (**medido el 2026-09-18, NO regrabable hoy**):

```
L0/forced: captura bajo disrupcion -56.0% +/- 17.5% · peor mundo -88.3% · 25/25 mundos medibles
L1/forced: captura bajo disrupcion -92.0% +/- 41.3% · peor mundo -171.8% · 25/25 mundos medibles
L2/forced: captura bajo disrupcion -107.3% +/- 39.0% · peor mundo -166.1% · 21/25 mundos medibles
L3/forced: captura bajo disrupcion -99.6% +/- 39.5% · peor mundo -166.5% · 21/25 mundos medibles
```

## Que cambio desde el 2026-09-18, y por que se puede atribuir

La linea base anterior era **anterior a la cobertura provisional** (`e2b4435`, 20-sep),
o sea que el titular vendible del repo describia un codigo que ya no existe.

Lo que decia entonces, congelado en `captura_limpia_b1e3293.json`:

```
L0/forced: captura -40.6% +/- 14.5% · peor mundo -71.9% · 25/25 mundos medibles
L1/forced: captura -117.0% +/- 50.7% · peor mundo -213.9% · 25/25 mundos medibles
L2/forced: captura -132.8% +/- 45.5% · peor mundo -198.9% · 21/25 mundos medibles
L3/forced: captura -122.6% +/- 50.3% · peor mundo -202.3% · 21/25 mundos medibles
```

**L0 limpio: −40,6 % → −13,8 %. +26,8 puntos.** Y no es una media que esconda
dispersion — es el criterio que esta rama se exige, mundo por mundo:

| | 18-sep | hoy | Δ |
|---|---:|---:|---:|
| media | −40,6 % | **−13,8 %** | **+26,8** |
| mediana | −36,4 % | −6,3 % | +30,1 |
| p90 | −26,3 % | **+1,0 %** | +27,2 |
| mejor mundo | −15,9 % | **+12,7 %** | +28,7 |
| peor mundo | −71,9 % | −59,6 % | +12,3 |

**Mejora en 25 de 25 mundos** (`captura_limpia_pareada.json`), minimo +9,4 puntos,
maximo +42,6, mediana +26,7; sd del delta pareado 10,0 puntos, t = 13,5. Y **cuatro
mundos** (1005, 1010, 1016, 1022) tienen captura **positiva**: ahi la politica de Kern
cuesta MENOS que el piso.

**El control, que es lo que hace creible el delta.** El mismo barrido corrido hoy en un
worktree de `b1e3293` reproduce el archivo congelado **bit a bit**: 48 campos (media,
sd, p10, mediana, p90, peor, mejor, desviacion x 4 niveles), **cero diferencias**. Las
dependencias de este repo se resuelven frescas en cada corrida — ya se puso rojo por un
release de upstream sin un commit propio --, asi que la deriva del entorno era una
explicacion viva para cualquier delta. No lo es: los +26,8 son producto. Como segundo
amarre, las dos capturas por semilla que `5408b2f` dejo escritas en su mensaje (1003 →
−0,6392 y 1004 → −0,7190) coinciden al digito con la mitad "antes".

### Que puede y que no puede incluir este numero

De las tres mejoras medidas en esta rama desde el 18-sep, el regrabado mueve **una**:

- **La cobertura provisional** (`e2b4435` + `8c6ff33`) — esta en el motor, y es lo que
  se mide aca.
- El **nivel de servicio** (+18,9 puntos) es un CARRIL de
  `scripts/residuo_de_la_brecha.py`, y ademas declarado no accionable.
- La **fecha de fin por SKU** (+16,2 puntos) es un POST-PROCESO sobre la `Policy` en
  `scripts/horizonte_por_sku.py`.

Ninguna de las dos ultimas esta en el motor: ni `servicio_econ` ni el tope por SKU
existen fuera de `scripts/`. Y `5408b2f` ya habia medido que la negativa-que-no-es-cero
no mueve la captura. Citar +26,8 como si incluyera las tres seria sumar dos palancas que
ningun cliente puede correr.

**La prediccion escrita antes de medir se quedo corta.** Extrapolando desde los cinco
primeros mundos (−47,0 % → −27,6 %, +19,4) y sabiendo que esos cinco eran 6,4 puntos
peores que el promedio, la estimacion era **−21,2 %**. Salio −13,8 %: la extrapolacion
desde cinco mundos **subestimaba** la palanca en 7,4 puntos.

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

## Dos cosas que la regrabacion encontro sin buscarlas

**1. Una fecha ambigua apaga la negativa del motor.** L1 salio **identico bit a bit** en
los 25 mundos: delta exactamente +0,0. Eso no es ruido, es un mecanismo. `06/01/2025`
(6 de enero, escrito `dd/mm/yyyy` por `inject_mixed_date_formats`) es ambigua y se parsea
**month-first**, o sea 1 de junio; su lunes es el 2025-05-26. Con eso la grilla semanal
pasa de **4 semanas a 21**, rellenas de ceros fabricados — y 21 ≥
`MIN_PERIODS_FOR_POLICY` (6), asi que **el motor deja de negarse**. Donde en L0 emite
ocho `provisional_cover` honestas, en L1 emite quince `(R, S)` con pronostico 0,22 para
un SKU que vende ~16 por semana (quince y no ocho porque `inject_whitespace_case` parte
`SKU-1000` en `SKU-1000` y `sku-1000`). Por eso la cobertura provisional compra
exactamente cero a L1: **solo se dispara cuando el motor se niega, y a L1 el motor no se
niega**.

La ambiguedad en si **ya estaba documentada y aceptada a proposito** en
`jobs/intake.py::_parse_dates` — se revirtio un `dayfirst=True` que corrompia el ~39 % de
las fechas ISO, que era peor. Lo que ese comentario acota mal no es la causa sino el
**radio**: dice, con razon, que la limitacion "nunca tira una fila y nunca corrompe una
fecha ya inequivoca". Lo que no dice es que una sola fecha ambigua **estira la grilla**, y
con eso cruza la compuerta que protege al motor. El alcance declarado es "una fila mal
etiquetada"; el alcance medido es "la negativa deja de existir".

**2. En los niveles sucios Kern compra una decima parte de lo que compra el piso.** Es el
diagnostico de la anomalia, arriba, y vale por si mismo: un fill de 0,25 contra 0,79 del
piso no es "captura mala", es un carril colapsado.

Ninguna de las dos se arregla aca. Que deba hacer el intake con una fecha ambigua
(negarse, avisar, pedir locale) y por que el motor colapsa con suciedad semantica son dos
decisiones de producto, cada una con sus ocho partes.

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
