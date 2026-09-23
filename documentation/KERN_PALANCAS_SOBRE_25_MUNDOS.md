# Cinco mundos no alcanzaban: cuatro de siete palancas cambian de signo

> Medido el 2026-09-23 sobre `2253d83`. Líneas base congeladas en
> [`lineas_base/horizonte_por_sku_25.json`](lineas_base/horizonte_por_sku_25.json),
> [`lineas_base/residuo_de_la_brecha_25.json`](lineas_base/residuo_de_la_brecha_25.json) y la
> comparación en [`lineas_base/palancas_sobre_25_mundos.json`](lineas_base/palancas_sobre_25_mundos.json).
> Regrabables con los mismos scripts de siempre más `--semillas 1000..1024`, y
> `scripts/palancas_sobre_25_mundos.py` para la comparación.
> `tests/test_palancas_sobre_25_mundos_publicado.py` ata el documento a su fuente.
>
> **No se tocó ni un carril.** Los dos barridos son los scripts publicados corridos con
> veinte semillas más. Lo único nuevo es la aritmética que compara las dos muestras.

## La pregunta

[`KERN_VENTAS_CENSURADAS.md`](KERN_VENTAS_CENSURADAS.md) H3 encontró que su propia palanca
se encogía **3,5 veces** al pasar de las cinco semillas canónicas a veinticinco, y dio la
causa (H1): una palanca paga **donde Kern ya va mal** —r = −0,61 contra la captura del
control— y las cinco canónicas contienen **tres de los ocho mundos donde Kern va peor**.
Ese documento cerró diciendo que lo que las palancas anteriores leerían sobre 25 mundos
**no estaba medido**, y se negó a suponerlo. Esto lo mide.

## La predicción, escrita antes de correr

El mecanismo de H1 es sobre **comprar más donde ya se compra poco**. De ahí salían tres
predicciones falsables, escritas antes de lanzar los barridos:

| | predicción | resultado |
|---|---|---|
| **P1** | `servicio_econ` (comprá más) se encoge al menos un 30 % | ✅ se encoge **3,69×** (−73 %) |
| **P2** | `horizonte_estimado` (comprá menos) se encoge **menos** que `servicio_econ` | ✅ se encoge **1,24×** (−19 %) |
| **P3** | la correlación con la captura del control separa a las dos familias | ✅ **sin solapamiento** |

Lo que la predicción **no** anticipó, y es lo más caro del resultado: que **cuatro de los
cinco carriles del residuo cambiaran de signo**.

## El resultado

| palanca | familia | en 5 mundos | **en 25** | encoge | t | p | mejoran | r con la captura del control |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| **horizonte_estimado** | comprá menos | +0,1618 (5/5) | **+0,1309** | 1,2× | **+5,81** | **0,00001** | **23/25** | −0,17 |
| **horizonte_real** | comprá menos | +0,1348 (5/5) | **+0,1413** | 0,95× | **+7,42** | **0,00000** | **25/25** | −0,23 |
| servicio_econ | comprá más | +0,1887 (4/5) | +0,0512 | 3,7× | +1,66 | 0,111 | 18/25 | −0,54 |
| censura_corregida | comprá más | +0,0930 (4/5) | +0,0267 | 3,5× | +1,09 | 0,287 | 13/25 | −0,77 |
| plazo_obs | comprá más | +0,0240 (3/5) | **−0,0015** | ⚠ signo | −0,15 | 0,882 | 12/25 | −0,52 |
| sigma_obs | comprá más | +0,0501 (4/5) | **−0,0136** | ⚠ signo | −1,09 | 0,285 | 8/25 | −0,42 |
| proveedor_medido | comprá más | +0,0485 (2/5) | **−0,0296** | ⚠ signo | −1,56 | 0,133 | 5/25 | −0,62 |
| todo (apiladas) | apiladas | +0,0901 (3/5) | **−0,0652** | ⚠ signo | −1,66 | 0,111 | 10/25 | −0,47 |

**El control reproduce las cinco líneas base congeladas al último dígito** en los tres
barridos (captura, costo total y unidades pedidas, semilla por semilla). Es la misma
medición; lo único que cambió es cuántos mundos entran.

Y la potencia dice que esto no es "no lo vimos": para `servicio_econ` la probabilidad de
haber detectado su +18,9 publicado era del **100 %**, y para la censura la de haber
detectado su +9,3 era del **95 %**. No están ahí.

## H1 · La correlación separa las dos familias sin solapamiento

| familia | rango de `corr(captura del control, Δ de la palanca)` |
|---|---|
| **comprá menos** (2 carriles) | **−0,23 … −0,17** |
| comprá más (5 carriles) | −0,77 … −0,42 |

Ni un solo carril de una familia cae en el rango de la otra. Es el mecanismo de
`KERN_VENTAS_CENSURADAS.md` H1 generalizado, y ahora se entiende **por qué** parte en dos:

- Una palanca de **comprá más** paga cuando Kern está sub-comprando, y eso pasa
  justamente en los mundos donde ya le va mal. Su ventaja está **concentrada** en la cola
  mala de la distribución — que es exactamente lo que las cinco canónicas
  sobre-representan.
- Una palanca de **comprá menos** paga porque Kern acumula por encima de lo que le queda
  por vender, y eso le pasa en **todos** los mundos. Su ventaja está **repartida**.

De ahí sale la regla operativa, más útil que "usá 25 mundos": **una palanca cuya ventaja
depende de que la política ya esté rota necesita una muestra que represente la
distribución entera. Una que arregla algo que pasa siempre, no.**

## H2 · El mecanismo: comprá más funciona, y aun así pierde

Los cinco carriles del residuo **mejoran el servicio**. Todos. Y cuatro de cinco terminan
con **peor** captura que no hacer nada:

| carril | captura | fill | unidades | compra | quiebre | capital al cierre |
|---|---:|---:|---:|---:|---:|---:|
| piso | +0,0 % | 0,824 | 3.559 | 166.236 | 86.761 | 30.828 |
| **kern** (control) | **−13,8 %** | 0,768 | 3.464 | 158.497 | 108.869 | 33.919 |
| plazo_obs | −13,9 % | 0,787 | 3.674 | 167.387 | 99.872 | 38.481 |
| sigma_obs | −15,1 % | 0,780 | 3.619 | 165.105 | 103.208 | 37.785 |
| proveedor_medido | −16,7 % | 0,794 | 3.793 | 173.470 | 95.908 | 42.699 |
| servicio_econ | −8,7 % | 0,817 | 3.838 | 177.380 | 84.238 | 41.291 |
| **todo** (apiladas) | **−20,3 %** | **0,832** | **4.189** | 194.580 | 75.691 | **54.452** |
| techo | +100,0 % | 0,998 | 3.678 | 176.100 | 1.337 | 0 |

`todo` es la lectura más limpia que dio esta rama de esa pared: es el carril con **el
mejor fill de todos los de Kern** (0,832) y el **peor resultado** (−20,3 %). Compra 4.189
unidades — **más que el oráculo con información perfecta**, que compra 3.678 — y deja
54.452 de capital pagado y sin vender. Servir mejor no es el objetivo; el objetivo es el
costo total, y esta economía castiga el inventario más de lo que premia el servicio.

La palanca de **comprá menos** hace lo contrario y por eso escala:

| | kern | horizonte_estimado | Δ |
|---|---:|---:|---:|
| unidades | 3.464 | 3.122 | **−9,9 %** |
| compra | 158.497 | 146.493 | **−12.003** |
| quiebre | 108.869 | 110.268 | +1.399 |
| fill | 0,768 | 0,764 | −0,004 |
| capital al cierre | 33.919 | **22.556** | **−33 %** |
| **captura** | **−13,8 %** | **−0,7 %** | **+13,1 puntos** |

Gasta 12.003 menos para pagar 1.399 más de quiebre, y el servicio no se mueve. Sobre 25
mundos **Kern queda prácticamente a la par del piso** (−0,7 %), y el carril de diagnóstico
—que topea con la demanda futura verdadera— lo pasa: **+0,3 %**, la primera vez que un
carril de Kern en esta rama sale por encima de un punto de reorden fijo.

## H3 · Qué cambia en lo ya publicado

Ninguna línea base congelada se toca: siguen siendo la medición correcta **de cinco
mundos**. Lo que cambia son las afirmaciones que se apoyaban en ellas.

**Se corrige el orden de las palancas.** `KERN_HORIZONTE_POR_SKU.md` dice que la fecha de
fin por SKU es *"la segunda palanca más grande de la rama después del nivel de servicio
(+18,9)"*. Sobre 25 mundos es **la más grande, por un factor de 2,6**, y el nivel de
servicio no se distingue de cero (p = 0,111). Y como esa es además la única que Kern **sí
puede accionar**, el orden se invirtió a favor de lo desplegable.

**Se refuerza "saber el futuro verdadero no compra nada".** Sobre cinco mundos el carril
desplegable parecía **ganarle** al de diagnóstico (−0,0270), una inversión que aquel
documento tuvo que explicar. Sobre 25 la ventaja del diagnóstico es **+0,0104 con
p = 0,378**: los dos son equivalentes, ahora sin el artefacto. La conclusión sobrevive y
pierde su rareza.

**Se refuerzan dos afirmaciones del residuo, y se derrumban tres carriles.**
`KERN_RESIDUO_DE_LA_BRECHA.md` ya decía que medir mejor al proveedor *"vale poco y empeora
en 3 de 5 mundos"* y que *"apilar palancas es peor que la mejor palanca sola"*. Las dos se
sostienen y se vuelven más duras: `proveedor_medido` mejora en **5 de 25** y neta
**−0,0296**, y `todo` neta **−0,0652**, o sea peor que no hacer nada. Lo que **no**
sobrevive son sus tres carriles desplegables: `plazo_obs`, `sigma_obs` y
`proveedor_medido` cambian de signo. **Sobre 25 mundos el residuo no tiene ninguna palanca
desplegable que pague.**

## Qué **NO** dice este documento

- **No dice que las mediciones de cinco mundos estuvieran mal hechas.** El control
  reproduce cada una al último dígito. Estaban bien medidas sobre una muestra que resultó
  no ser representativa para una de las dos familias.
- **⚠️ No dice que 25 mundos sean suficientes.** Cuatro carriles quedan con el cero adentro
  del intervalo; de esos, lo único afirmable es que **no valen lo que decían valer**, no
  que valgan cero. Los dos que sí quedan afirmados son los del horizonte, con p < 0,00001.
- **No mide bajo disrupción ni sobre datos sucios.** Todo es nivel 0, sin shocks, 8 SKU,
  24 semanas, replanificación cada 4.
- **No convierte a la fecha de fin por SKU en una capacidad.** Sigue siendo un
  post-proceso sobre la `Policy`; construirla toca las ocho partes de siempre, y **sigue
  sin medirse qué pasa con una fecha equivocada**. Lo que cambió es que ahora es, por
  lejos, la que más paga de las medidas.
- **No re-mide el arranque muerto** (`KERN_POR_QUE_BAJO_EL_PISO.md`): ésa no es una palanca
  candidata sino un arreglo que ya está en el motor, y su efecto vive dentro del control.
