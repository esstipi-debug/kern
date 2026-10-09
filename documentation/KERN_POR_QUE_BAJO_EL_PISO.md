# Por qué Kern queda por debajo del piso con datos limpios

> Medido el 2026-09-19 sobre `47c1a4a`; el arreglo y su medición, el 2026-09-20. **Dos**
> líneas base congeladas — [el diagnóstico](lineas_base/por_que_bajo_el_piso.json) y
> [el después](lineas_base/por_que_bajo_el_piso_con_cobertura.json) — las dos regrabables
> con `scripts/por_que_bajo_el_piso.py`. El documento no puede despegarse de ellas:
> `tests/test_por_que_bajo_el_piso_publicado.py` lo compara contra su fuente.
>
> El diagnóstico se deja tal como se midió, con los números de ANTES del arreglo: es lo
> que motivó la decisión y borrarlo dejaría la conclusión sin su evidencia.
>
> **Corregido el 2026-09-21.** El capital que queda pagado y sin vender al cierre son
> **dos** cosas —el **estante** (`on_hand_end`) y la **cañería** (lo ordenado que sigue
> viajando)—, y `stock_terminal_usd` medía solo la primera. El *después* se regrabó con
> las tres columnas y **ningún campo preexistente cambió de valor**. El *diagnóstico* no
> se puede regrabar —el código de antes del arreglo ya no existe—, así que sus cifras de
> cierre siguen siendo estante solo y se marcan como tales.

## La pregunta

[`KERN_CAPTURA_BAJO_DISRUPCION.md`](KERN_CAPTURA_BAJO_DISRUPCION.md) publicó, sobre 25
mundos con datos limpios, una captura de **−40,6 % ± 14,5 %**: Kern decide peor que un
punto de reorden fijo que nadie revisa. Y lo dejó escrito en su propia sección de *qué
no dice*:

> …que **no explica por qué** Kern queda por debajo del piso con datos limpios —queda
> abierto, ahora con una vara sana con la que medirse—…

Esto lo contesta. Tres experimentos sobre los **mismos** mundos y con los **mismos**
parámetros con los que se grabó esa línea base (8 SKU, 2 proveedores, 24 semanas, replan
4, L0, carril `forced`), porque medir con otros explicaría otra corrida.

## La respuesta corta

Kern no pierde por dimensionar mal el buffer. **Pierde por comprar tarde**, y la
hipótesis obvia —que le falta cobertura— está **refutada** por medición: darle la
cobertura exacta del piso lo deja *peor*.

> Sobre 5 mundos, la captura pasa de -47.0% a -20.6% cuando solo las semanas 4-7 usan la politica del piso: 56% de la brecha es el arranque muerto.

## E1 · Dónde está la diferencia

Promedios sobre los 5 mundos. `terminal` es el valor de compra del stock que queda al
cierre del horizonte, y no es un componente del costo: está para poder distinguir una
decisión mala de un artefacto de medir sobre un horizonte que se acaba.

| carril | procurement | holding | fixed | shortage | total | fill | unidades | terminal |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| piso | 144,753 | 992 | 10,095 | 74,880 | **230,721** | 0.817 | 3,064 | 7,525 |
| kern | 106,077 | 681 | 5,475 | 153,421 | **265,654** | 0.621 | 2,228 | 4,572 |
| arranque_piso | 141,822 | 1,090 | 4,755 | 97,920 | **245,588** | 0.778 | 3,115 | 10,633 |
| cobertura_piso | 151,706 | 1,961 | 5,610 | 106,605 | **265,882** | 0.711 | 3,100 | 26,191 |
| techo | 154,498 | 1,721 | 1,890 | 187 | **158,295** | 1.000 | 3,172 | 0 |

Contra el piso, **todo el déficit es una sola línea**:

| | procurement | holding | fixed | **shortage** | total |
|---|---:|---:|---:|---:|---:|
| Kern − piso | −38.676 | −311 | −4.620 | **+78.541** | **+34.933** |

Kern gasta **$43.607 menos** en las otras tres líneas y pierde **$78.541 más** en venta
perdida. Compra 2.228 unidades donde el piso compra 3.064 y el techo 3.172; su fill es
0,621 contra 0,817 del piso y 1,000 del techo.

Eso ubica la diferencia pero no la explica: "compra de menos" admite al menos dos
causas opuestas —un buffer chico, o el momento equivocado— y llevan a arreglos
distintos.

## E2a · El arranque muerto: el 56 % de la brecha

`MIN_PERIODS_FOR_POLICY` es **6**. El arnés replanifica cada 4 semanas con 4 de
calentamiento, así que la primera llamada le lleva a Kern **4** períodos —se niega,
correctamente— y la siguiente es la semana 8. El piso, en cambio, congela su punto de
reorden en la semana 4 y empieza a comprar ahí.

El diferencial son exactamente las semanas **4-7**. El contrafáctico reemplaza *solo*
esas por la política del piso y deja todo lo demás igual:

| semilla | Kern | arranque del piso | delta |
|---|---:|---:|---:|
| 1000 | -0.4245 | -0.3553 | +0.0692 |
| 1001 | -0.2739 | -0.0176 | +0.2563 |
| 1002 | -0.2946 | -0.0309 | +0.2636 |
| 1003 | -0.6392 | -0.2876 | +0.3516 |
| 1004 | -0.7190 | -0.3388 | +0.3802 |

Mejora en los **cinco** mundos. Cuatro semanas de veinticuatro valen **26 de los 47
puntos**, y el fill sube de 0,621 a 0,778 — casi el 0,817 del piso. Es irrecuperable
por construcción: la demanda de esas semanas es venta perdida, no backorder.

**Bajar `MIN_PERIODS_FOR_POLICY` no es el arreglo.** Medido: con `min_periods=4` la
corrida sale `qa_failed` —las invariantes de evidencia de `jobs/qa.py` son la segunda
línea detrás de la compuerta, y no se pueden saltear bajando un número— y la política
vuelve vacía igual. El arranque muerto es una decisión de producto, no un parámetro.

## E2b · La hipótesis del buffer, refutada

La lectura natural de "compra de menos" es "agrandale el buffer". Se midió dándole a
Kern la cobertura **exacta** del piso (`naive_policy` fija R = 5 × la demanda semanal
media, así que el carril pasa `lead_times = 5` períodos por SKU):

- compra **3.100** unidades — el piso compra 3.064, o sea sí compró lo que le faltaba;
- y captura **peor**: −0,483 contra −0,470.

El dinero extra no se convierte en venta sino en **capital inmovilizado**: **$26.191**
de estante contra $7.525 del piso. Con el nivel de servicio económico encima (~0,998 en
vez de 0,95), peor todavía: −0,640 y $37.307.

> Esas dos cifras son **estante solo** y no se pueden completar: el diagnóstico está
> congelado pre-arreglo. Pero el mecanismo sí se re-midió sobre la corrida de después,
> donde `cobertura_piso` compra **+1.068 unidades** más que Kern y deja **+26.908** más
> de capital pagado y sin vender. Y ahí el estante no engañaba: de esos +26.908, **+26.971
> son estante** —la cañería casi no se mueve (21.230 → 21.167)—, porque forzar la
> cobertura hace comprar **antes**, y lo que se compra antes llega antes.

Esto es lo que hace que el hallazgo sea útil: agrandar buffers no solo no cierra la
brecha, la empeora.

## E3 · El mecanismo: cuándo compra cada uno

Unidades pedidas por tercio del horizonte, promedio sobre los 5 mundos:

| carril | semanas 0-7 | 8-15 | 16-23 |
|---|---:|---:|---:|
| piso | 808 | 1,100 | 1,155 |
| kern | 0 | 1,128 | 1,101 |
| arranque_piso | 808 | 1,020 | 1,287 |
| techo | 1,359 | 1,366 | 447 |

El oráculo **carga adelante y corta**: 1.359 unidades en el primer tercio y 447 en el
último, cerrando en **$0** — y con el número completo se sabe que es cero **entero**: ni
estante ni cañería. Un Wagner-Whitin que sabe la fecha de fin no deja nada viajando. Kern hace exactamente lo contrario: **cero**
al principio y sigue pidiendo hasta la última semana.

Las dos mitades son la misma causa vista de los dos lados. Al principio no puede
decidir; al final decide sin saber que el horizonte se acaba, porque una política de
punto de reorden no tiene noción de horizonte. Por eso forzarlo a comprar más (E2b)
empuja el excedente justo hacia donde ya sobra.

## Causas de segundo orden

Medidas aparte y **no** congeladas en la línea base; reproducibles agregando un carril
con el `overrides` que se indica.

- **El lente le muestra el lead time prometido, no el real.** `src/simworld/lens.py`
  exporta `sup.promised_lead_weeks * 7` con `lead_time_std = 0.0` escrito explícito,
  mientras el mundo sortea `otif ~ U(0,55, 0,95)`. En la semilla 1004 los dos
  proveedores están en ~0,60: Proveedor-1 promete 3 semanas y entrega 3,80; Proveedor-2
  promete 1 y entrega 1,79. Darle el lead real recupera **~10 puntos**
  (`lead_times` con la media realizada y `lead_time_stds` con su desvío).
- **El 0,95 de nivel de servicio no es el de esta economía.** Perder una unidad cuesta
  `price`; guardarla, `unit_cost × 0,25/52`. La razón crítica real es **~0,998**.
  Corregirlo recupera **~12 puntos** (`service_levels` por SKU).

Ninguna de las dos cierra la brecha, y **juntas con el resto empeoran**, por el
mecanismo de E3: todo lo que hace comprar más termina en **capital pagado y sin vender**.
La dirección se re-midió con el número completo y se sostiene —los dos carriles que
compran más que Kern dejan más inmovilizado, +1.307 y +26.908—, pero el nombre no: no es
solo stock terminal, es estante **más** cañería.

## El arreglo, y lo que midió

El diagnóstico se construyó para decidir, así que la decisión está tomada y medida sobre
los mismos cinco mundos. Línea base del después en
[`lineas_base/por_que_bajo_el_piso_con_cobertura.json`](lineas_base/por_que_bajo_el_piso_con_cobertura.json).

**Negarse a dimensionar no obliga a no decir nada.** Son dos preguntas distintas y hasta
el 2026-09-20 se contestaban las dos con silencio:

- *cuánto buffer* — necesita σ, y con menos de `MIN_PERIODS_FOR_POLICY` observaciones no
  hay σ honesta. **Sigue sin contestarse.**
- *cuánto hace falta para llegar a la próxima reposición* — necesita la media de lo que
  se vendió y el plazo del proveedor, y los dos son hechos **medidos** que el archivo ya
  trae.

`inventory_optimization` emite ahora una **cobertura provisional** para el SKU que tiene
entre `MIN_PERIODS_FOR_COVER` (2) y `MIN_PERIODS_FOR_POLICY` (6) períodos. Es `mu_x` de
la ecuación 6.4 —la identidad del propio motor, `src/risk_period.py`— **con el término de
seguridad omitido**: `cobertura = media medida × (plazo + revisión)`. Debajo de dos
períodos ni siquiera hay una media que citar y la negativa se queda entera.

**No es una política, y el registro lo declara en la FORMA de sus campos**: disparador sí,
buffer no, z no, pronóstico no. Cubre la media, o sea que se espera que se quede corta
cerca de la mitad de las veces, y su capital se reporta **aparte** del titular porque no
pasa por el allocator de presupuesto. El deck, el resumen y un residual de las OPTIONS
dicen esas tres cosas; y no se cuela en la planilla de pedidos del cliente, que es donde
una política manda de verdad.

**Cómo la ejecuta un cliente**, que es la mitad sin la cual esta medición no se
reproduce. La cobertura dimensiona *cuánto*; la posición dice *cuándo*, con la misma
aritmética de un `(R,S)` —es eso: un objetivo sin lote—, así que entra en `order_now` y
en la OPCIÓN *"Release the N orders due now"*, que la nombra aparte (*"no safety stock
behind it"*). Sin esa mitad el marcador compraba la cobertura y la lista que lee el
cliente no la nombraba: los números de abajo describirían una corrida que nadie fuera
del arnés podría repetir. El Control Tower, en cambio, la deja pasar **callado a
propósito** (`forecast` en `None`): `order_now` es una lista que el operador va a
buscar, un `rop_breach` es un evento que se empuja, y sin buffer el estante cae por
debajo la mitad de las semanas.

| | antes | después | |
|---|---:|---:|---|
| **captura** | **−47,0 %** | **−27,6 %** | **+19,4 puntos** |
| fill | 0,621 | 0,750 | el piso está en 0,817 |
| unidades compradas | 2.228 | 2.987 | el piso compra 3.064 |
| quiebre (usd) | 153.421 | 109.567 | |
| compra (usd) | 106.077 | 135.359 | |
| cierre — estante (usd) | 4.572 | 9.209 | el piso deja 7.525 |
| cierre — **pagado y sin vender** (usd) | *no medible* | **30.440** | el piso deja **25.017** |
| **unidades, semanas 0-7** | **0** | **543** | el piso 808, el techo 1.359 |

Mejora en los **cinco** mundos: +0,0939 / +0,2546 / +0,2475 / +0,2508 / +0,1232.

**Dos controles que hacen que esta comparación signifique algo.** Los carriles `piso`,
`techo` y `arranque_piso` salen **idénticos** en las dos corridas —el contrafáctico
reemplaza las semanas 4-7 en ambas, y en las 8+ ya hay seis períodos, así que ahí no hay
coberturas—, o sea que las dos mediciones son el mismo experimento y lo único que se movió
es el código. Y el contrafáctico que midió el defecto **se encogió**: explicaba el 56 % de
la brecha y ahora explica el 25 % de una brecha más chica. Un arreglo que cierra la causa
que se diagnosticó tiene que achicar la medición de esa causa, y lo hace.

> Sobre 5 mundos, la captura pasa de -27.6% a -20.6% cuando solo las semanas 4-7 usan la politica del piso: 25% de la brecha es el arranque muerto.

**⚠️ Cambio de comportamiento.** Cualquier cliente con un SKU de entre dos y cinco
períodos empieza a recibir un número donde antes recibía silencio. Es el arreglo, no un
efecto secundario — pero es una compra que antes no se proponía, así que sale como OPCIÓN
con su residual y nunca como algo que Kern ya decidió.

## Qué NO dice este documento

- **No mide los 25 mundos de la línea base, sino 5** (semillas 1000-1004, las primeras).
  Su promedio base es **−47,0 %**, peor que el **−40,6 %** publicado, porque entre esas
  cinco está el peor mundo del barrido (−0,7190). La *dirección* es consistente en los
  cinco; la *magnitud* no es la publicada y no debe citarse como si lo fuera.
- **Las cifras de cierre del DIAGNÓSTICO son estante solo y no se pueden completar.**
  El código de antes del arreglo ya no existe, así que $4.572, $26.191 y $37.307 se
  quedan como se midieron. Lo que sí se re-midió con estante + cañería es la corrida de
  **después**, y cada afirmación de arriba dice sobre cuál de las dos descansa.
- **No dice que el piso sea bueno.** Dice que su cobertura de 5 semanas está mejor
  calibrada para esta economía que la que Kern calcula, y que Kern no puede usar la suya
  durante el primer tercio de la corrida.
- **El efecto de fin de horizonte castiga a cualquier política** contra un oráculo que
  sabe cuándo termina. En parte es artefacto de la vara: ningún negocio real tiene
  semana 24. **Corregido el 2026-09-21:** esta viñeta decía *"castiga más a Kern que al
  piso —$4.572 contra $7.525 de terminal hoy"*, y tenía **dos** errores encadenados. El
  $4.572 es el Kern de **antes** del arreglo, no el de hoy (hoy es $9.209); y con ese
  número la conclusión sale al revés, porque 4.572 es **menos** que los 7.525 del piso.
  Con el número correcto y completo la conclusión **sí** se sostiene, y más fuerte: Kern
  deja **$30.440** contra **$25.017** del piso, en **4 de 5** mundos (con el estante solo
  eran 3 de 5). Esa parte sí es del producto — y es coherente con
  [`KERN_HORIZONTE_DE_LA_VARA.md`](KERN_HORIZONTE_DE_LA_VARA.md), que midió que acreditar
  el borde deja a Kern **peor**, no mejor, justamente porque inmoviliza más que el piso.
- **El arreglo no cierra la brecha, la achica.** Kern sigue por debajo del piso
  (−27,6 % contra 0 %). Lo que se cerró es la causa principal del diagnóstico, no el
  problema entero.
- **Queda sin explicar el residuo.** Con el arranque arreglado Kern sigue en −0,206:
  gasta casi lo mismo que el piso (−$2.931 de procurement) y aun así pierde $23.039 más
  de venta y deja $3.108 más de stock muerto. Eso ya es una ineficiencia de
  **asignación** entre SKU y semanas, y este documento no la resuelve.
  → Diagnosticado el 2026-09-20 en
  [`KERN_RESIDUO_DE_LA_BRECHA.md`](KERN_RESIDUO_DE_LA_BRECHA.md), sobre estos mismos
  cinco mundos y con el carril de control idéntico al de acá. La respuesta corta: no es
  una cosa que Kern haga mal, es que un punto de reorden no tiene horizonte, y **toda**
  palanca que baja el quiebre empuja el excedente al stock terminal. La palanca más
  grande (+18,9 puntos) es el nivel de servicio, y es la que Kern hoy no puede accionar.

## Cómo reproducirlo

```bash
PYTHONPATH=. python scripts/por_que_bajo_el_piso.py \
    --json documentation/lineas_base/por_que_bajo_el_piso.json --markdown
```

Decenas de minutos: cada semilla corre el piso, el oráculo y tres carriles de Kern
completos (~18 pasadas del orquestador). `--semillas 1004` corre uno solo, en unos
minutos, y reproduce −0,7190 / −0,3388 / −0,3943.
