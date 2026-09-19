# Por qué Kern queda por debajo del piso con datos limpios

> Medido el 2026-09-19 sobre `47c1a4a`. Línea base congelada en
> [`lineas_base/por_que_bajo_el_piso.json`](lineas_base/por_que_bajo_el_piso.json);
> se regraba con `scripts/por_que_bajo_el_piso.py`. El documento no puede despegarse
> de ella: `tests/test_por_que_bajo_el_piso_publicado.py` lo compara contra su fuente.

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

El dinero extra no se convierte en venta sino en **stock terminal**: **$26.191** contra
$7.525 del piso. Con el nivel de servicio económico encima (~0,998 en vez de 0,95),
peor todavía: −0,640 y $37.307 terminales.

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
último, cerrando con stock terminal **$0**. Kern hace exactamente lo contrario: **cero**
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
mecanismo de E3: todo lo que hace comprar más termina en stock terminal.

## Qué NO dice este documento

- **No mide los 25 mundos de la línea base, sino 5** (semillas 1000-1004, las primeras).
  Su promedio base es **−47,0 %**, peor que el **−40,6 %** publicado, porque entre esas
  cinco está el peor mundo del barrido (−0,7190). La *dirección* es consistente en los
  cinco; la *magnitud* no es la publicada y no debe citarse como si lo fuera.
- **No dice que el piso sea bueno.** Dice que su cobertura de 5 semanas está mejor
  calibrada para esta economía que la que Kern calcula, y que Kern no puede usar la suya
  durante el primer tercio de la corrida.
- **El efecto de fin de horizonte castiga a cualquier política** contra un oráculo que
  sabe cuándo termina. En parte es artefacto de la vara: ningún negocio real tiene
  semana 24. Pero castiga **más** a Kern que al piso —$4.572 contra $7.525 de terminal
  hoy, y $26.191 cuando se lo fuerza a comprar más— y esa parte sí es del producto.
- **No propone el arreglo.** Nombra la decisión: qué hacer entre la semana 4 y la 8,
  cuando el motor honestamente no puede dimensionar. Negarse está bien; no tener nada
  que ejecutar mientras tanto es lo que cuesta 26 puntos.
- **Queda sin explicar el residuo.** Con el arranque arreglado Kern sigue en −0,206:
  gasta casi lo mismo que el piso (−$2.931 de procurement) y aun así pierde $23.039 más
  de venta y deja $3.108 más de stock muerto. Eso ya es una ineficiencia de
  **asignación** entre SKU y semanas, y este documento no la resuelve.

## Cómo reproducirlo

```bash
PYTHONPATH=. python scripts/por_que_bajo_el_piso.py \
    --json documentation/lineas_base/por_que_bajo_el_piso.json --markdown
```

Decenas de minutos: cada semilla corre el piso, el oráculo y tres carriles de Kern
completos (~18 pasadas del orquestador). `--semillas 1004` corre uno solo, en unos
minutos, y reproduce −0,7190 / −0,3388 / −0,3943.
