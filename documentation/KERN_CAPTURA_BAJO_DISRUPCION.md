# Captura bajo disrupcion — linea base publicada

> Medido el 2026-09-18 sobre `claude/keen-goodall-jfnqo9` @ `b1e3293`, corriendo el
> barrido, no copiado de una corrida previa. Fuente de todos los numeros de aca:
> `documentation/lineas_base/captura_limpia.json` y
> `documentation/lineas_base/captura_bajo_shock.json`.
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

Tarda ~27 y ~53 minutos respectivamente: con shock cada mundo corre **dos veces**
(limpio y perturbado), que es el precio de poder atribuirle la diferencia al shock.

## Lo que se puede afirmar

Con datos limpios:

```
L0/forced: captura -40.6% +/- 14.5% · peor mundo -71.9% · 25/25 mundos medibles
L1/forced: captura -117.0% +/- 50.7% · peor mundo -213.9% · 25/25 mundos medibles
L2/forced: captura -132.8% +/- 45.5% · peor mundo -198.9% · 21/25 mundos medibles
L3/forced: captura -122.6% +/- 50.3% · peor mundo -202.3% · 21/25 mundos medibles
```

Bajo `lead_time_spike:*:8:3:3`:

```
L0/forced: captura bajo disrupcion -56.0% +/- 17.5% · peor mundo -88.3% · 25/25 mundos medibles
L1/forced: captura bajo disrupcion -92.0% +/- 41.3% · peor mundo -171.8% · 25/25 mundos medibles
L2/forced: captura bajo disrupcion -107.3% +/- 39.0% · peor mundo -166.1% · 21/25 mundos medibles
L3/forced: captura bajo disrupcion -99.6% +/- 39.5% · peor mundo -166.5% · 21/25 mundos medibles
```

## Como se lee esto, sin adornos

**Kern decide peor que un punto de reorden fijo en las dos condiciones.** Captura 0 es
"igual que el piso" y 1 es "igual a un oraculo que ve el futuro". Un −40,6 % con datos
limpios quiere decir que, en promedio sobre 25 mundos, la politica que Kern propone
cuesta un 40 % de la brecha piso-techo MAS que el piso. Bajo disrupcion, −56,0 %.

El shock cuesta **15,4 puntos de captura** en el nivel 0, que es la comparacion limpia
porque las dos corridas comparten semillas, escala y carril.

En los niveles sucios la captura bajo disrupcion sale MENOS mala que la limpia (−92,0 %
contra −117,0 % en L1). No es que la disrupcion ayude: es que el piso tambien sufre el
shock, y la captura es una razon contra el piso. Cuando el denominador se degrada, una
politica mala se ve menos mala. Es la razon por la que este documento publica las dos
columnas y no solo una.

**Mas suciedad, peor captura, en las dos condiciones** (−40,6 → −117,0 → −132,8 en
limpio). `degrada_con_la_suciedad` no disparo en ninguno de los dos barridos, que es el
requisito para que `verify_fidelidad.py` acepte grabar una linea base.

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

## Que NO dice este documento

- **No dice que Kern sea peor que un punto de reorden fijo en un cliente real.** Dice
  que lo es *en este arnes*: mundos sinteticos, un solo carril (`forced`), una politica
  por replanificacion cada 4 semanas, y un piso que es un reorden fijo bien calibrado
  sobre la misma demanda. Sirve para detectar regresiones y para no venderse de mas, no
  para estimar el resultado de una implantacion.
- **No explica POR QUE** Kern queda por debajo del piso con datos limpios. Eso queda
  abierto: es la pregunta que sigue, y ahora tiene una vara sana con la que medirse.
- **No cubre otros shocks.** `supplier_outage` y `demand_surge` existen en
  `src/simworld/shocks.py` y no tienen linea base grabada.
