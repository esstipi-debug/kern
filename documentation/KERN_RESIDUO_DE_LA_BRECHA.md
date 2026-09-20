# El residuo de la brecha: qué queda después del arranque muerto

> Medido el 2026-09-20 sobre `58f5eed`. Línea base congelada en
> [`lineas_base/residuo_de_la_brecha.json`](lineas_base/residuo_de_la_brecha.json),
> regrabable con `scripts/residuo_de_la_brecha.py`. El documento no puede despegarse de
> ella: `tests/test_residuo_publicado.py` lo compara contra su fuente.

## La pregunta

[`KERN_POR_QUE_BAJO_EL_PISO.md`](KERN_POR_QUE_BAJO_EL_PISO.md) contestó por qué Kern
quedaba en −40,6 % y midió el arreglo: la cobertura provisional llevó la captura de
−47,0 % a −27,6 % sobre cinco mundos. Y dejó escrito lo que no resolvió:

> Con el arranque arreglado Kern sigue en −27,6 %: gasta casi lo mismo que el piso y aun
> así pierde más venta y deja más stock muerto. Eso ya es una ineficiencia de asignación
> entre SKU y semanas, sin diagnosticar.

Esto lo diagnostica, sobre los **mismos** cinco mundos y los **mismos** parámetros. El
carril de control sale en **−27,62 %**, o sea que reproduce el número publicado: es el
mismo experimento y no una medición nueva que se le parece.

## La respuesta corta

El residuo **no es una cosa que Kern hace mal**. Es que una política de punto de reorden
no tiene noción de horizonte, y **toda palanca que reduce el quiebre empuja el excedente
al stock terminal**. La palanca más grande con diferencia es el nivel de servicio
—+18,9 puntos— y es justamente la que Kern hoy **no puede accionar**, porque la lente no
le da el precio. Y apilar palancas es **peor** que la mejor palanca sola.

> Sobre 5 mundos, el residuo de -27.6% se mueve a -8.8% con el carril 'servicio_econ' (diagnostico), que mejora en 4 de 5 mundos.

## El defecto que apareció al medir

El primer carril que se escribió le pasaba a Kern la **dispersión del plazo** observada
en sus propias recepciones. Salió **idéntico, dígito por dígito**, al carril de control.
Eso no podía ser cierto: el período de riesgo es

```
sigma_x = sqrt(tau · sigma_d² + sigma_L² · mu_d²)        (Vandeput, ec. 6.5)
```

y el término `sigma_L² · mu_d²` es el **dominante** cuando la demanda es grande frente a
su propia dispersión, que es el caso normal de un SKU que vende todas las semanas.

La causa no era el modelo. `jobs/inventory_optimization.run` acepta `lead_time_stds`
desde que existe la columna `lead_time_std` del CSV, y `scm_agent/tools.py::_inventory_run`
**nunca lo reenviaba**. Un cliente que lo declaraba no recibía ni un error ni un aviso:
recibía una política dimensionada como si el proveedor no fallara nunca, con el
entregable diciendo igual *"95 % service level"*. Medido sobre la misma serie: llamando
al job, `lead_time_stds={"A": 3.0}` mueve el buffer de **11,9 a 238,4** unidades;
pasando por el orquestador quedaba en 11,9.

Es el defecto de esta rama **al revés**. En la negativa-como-cero la prosa avisaba y el
número mentía; acá la prosa estaba bien —`jobs/deliverables.py` ya declaraba
*"sigma_L defaulted to 0"* y hasta explicaba que eso *"asserts the supplier never varies,
which shrinks every buffer"*— y lo que fallaba era la cañería, así que **toda** corrida
decía "defaulted" aunque el cliente hubiera dado el número.

Arreglado, con guard estructural (`tests/test_overrides_no_se_tiran.py`): recorre la
firma del job por AST y se pone rojo si aparece un parámetro que el agente no reenvía ni
declara por escrito por qué no.

## R1 · Los carriles

Promedios sobre los 5 mundos. `terminal` es el valor de compra del stock que queda al
cierre, y no es un componente del costo: está para poder distinguir una decisión mala de
un artefacto de medir sobre un horizonte que se acaba.

| carril | clase | captura | Δ vs kern | mejoran | fill | quiebre | compra | terminal | unidades |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| piso | vara | +0,0 % | — | — | 0,817 | 74.880 | 144.753 | 7.525 | 3.064 |
| **kern** | **control** | **−27,6 %** | — | — | 0,750 | 109.567 | 135.359 | 9.209 | 2.987 |
| plazo_obs | desplegable | −25,2 % | +2,4 | 3/5 | 0,776 | 96.789 | 146.198 | 14.551 | 3.205 |
| sigma_obs | desplegable | −22,6 % | +5,0 | 4/5 | 0,766 | 100.920 | 140.532 | 13.773 | 3.088 |
| proveedor_medido | desplegable | −22,8 % | +4,9 | **2/5** | 0,790 | 87.542 | 153.716 | 19.026 | 3.326 |
| **servicio_econ** | diagnóstico | **−8,8 %** | **+18,9** | 4/5 | 0,812 | 77.657 | 152.879 | 22.794 | 3.353 |
| todo | diagnóstico | −18,6 % | +9,0 | 3/5 | 0,838 | 61.914 | 177.453 | 33.435 | 3.763 |
| techo | vara | +100,0 % | — | — | 1,000 | 187 | 154.498 | **0** | 3.172 |

Por semilla, porque el promedio esconde la dispersión —y la dispersión es parte del
hallazgo:

| carril | 1000 | 1001 | 1002 | 1003 | 1004 |
|---|---:|---:|---:|---:|---:|
| kern | −0,3306 | −0,0193 | −0,0471 | −0,3884 | −0,5958 |
| plazo_obs | −0,2612 | −0,0636 | −0,0882 | −0,3357 | −0,5125 |
| sigma_obs | −0,2476 | −0,0336 | **+0,0420** | −0,3157 | −0,5755 |
| proveedor_medido | −0,2012 | −0,0975 | −0,1160 | −0,3912 | −0,3330 |
| servicio_econ | −0,0367 | **+0,0061** | −0,1398 | −0,1525 | −0,1146 |
| todo | **+0,1106** | **−0,3029** | −0,0871 | −0,3735 | −0,2780 |

## R2 · Qué es desplegable y qué es diagnóstico

La distinción importa más que los números. Un carril que le pasa a Kern algo que un
cliente no puede darle mide una **causa**, no un arreglo, y presentarlo como arreglo
sería la misma confusión que este repositorio persigue en todas partes.

**Desplegables.** `plazo_obs`, `sigma_obs` y `proveedor_medido` usan solo las órdenes que
ya se emitieron **y ya llegaron** antes de la semana de replanificación: `t + L ≤ week`.
Nada del futuro. Y cuando un SKU todavía no vio llegar ninguna orden **no se le inventa
un número** —se lo omite del override—, que es la regla de ausencia declarada de siempre.
Un cliente puede correr esto hoy con sus propias recepciones; `webapp/demo_gap_value.py`
ya mide exactamente esa brecha entre el plazo declarado y el observado.

**Diagnóstico.** `servicio_econ` le pasa a Kern la razón crítica por SKU, que necesita el
**precio**: perder una unidad cuesta `price`, guardarla una semana cuesta
`unit_cost × 0,25/52`. Sobre esta economía da **0,9976** de promedio, no 0,95. La lente
del marcador exporta `unit_cost` y **no** exporta precio, así que hoy Kern no puede
calcularla de lo que lee. `jobs/intake.py` sí sabe leer `revenue`: la brecha es de
**canal, no de motor**.

## R3 · Las tres cosas que la medición dice y que no eran obvias

**1. La palanca grande es el nivel de servicio, no el plazo.** +18,9 puntos contra +2,4 a
+5,0 de las tres del proveedor. El 0,95 por omisión no es de esta economía, y esa
suposición cuesta más que toda la información de plazos que el cliente podría aportar.

**2. Medir mejor al proveedor ayuda poco y no es confiable.** `proveedor_medido` combina
las dos mediciones y **empeora en 3 de 5 mundos** —menos confiable que cualquiera de las
dos por separado—, incluida la semilla 1003, donde queda por debajo del control
(−0,3912 contra −0,3884). Un promedio de +4,9 puntos sobre esa dispersión no es una
recomendación.

**3. Apilar palancas es peor que la mejor palanca sola.** `todo` (−18,6 %) queda por
debajo de `servicio_econ` solo (−8,8 %), y su varianza explota: +0,1106 en la semilla
1000 y −0,3029 en la 1001, donde el control estaba en −0,0193. Eso confirma, y ahora con
el arranque ya arreglado, la lectura que
[`KERN_POR_QUE_BAJO_EL_PISO.md`](KERN_POR_QUE_BAJO_EL_PISO.md) había dejado escrita:
*"todo lo que hace comprar más termina en stock terminal"*.

El mecanismo está en una sola columna. El stock terminal sube monótonamente con cada
palanca: kern 9.209 → sigma_obs 13.773 → proveedor_medido 19.026 → servicio_econ 22.794
→ **todo 33.435**, contra 7.525 del piso y **0** del techo. Y `todo` compra **3.763
unidades cuando el oráculo, con información perfecta, compra 3.172**. Comprar más que el
oráculo es la definición de comprar de más.

## Lo que esto dice que hay que hacer

No es más buffer. Las tres palancas del proveedor y la del servicio son todas
"comprá más", y el marcador las castiga en cuanto se apilan. Lo que el residuo pide es
**noción de horizonte**, que es precisamente lo que un punto de reorden no tiene: el
oráculo carga adelante y **corta**, cerrando con terminal $0.

Eso **no está medido acá**. Este documento mide que las palancas conocidas no cierran el
residuo y por qué; no mide que una política con horizonte sí lo cerraría.

## Qué NO dice este documento

- **No mide los 25 mundos de la línea base, sino 5** (semillas 1000-1004). Y la
  dispersión entre mundos es enorme —el control va de −0,0193 a −0,5958—, así que las
  medias de arriba tienen mucho menos poder del que sugiere un promedio a un decimal.
- **`servicio_econ` no es un arreglo propuesto**, es una medición de cuánto vale la pena
  cerrar la brecha de canal. Subir el nivel de servicio a 0,998 sobre un cliente real,
  sin su precio y sin su horizonte, es exactamente el "comprá más" que el punto 3 mide
  que se paga en stock muerto.
- **No diagnostica el efecto de fin de horizonte por separado.** Castiga a cualquier
  política contra un oráculo que sabe cuándo termina la corrida, y por eso parte del
  residuo es de la vara y no de Kern. Cuánta parte queda abierto.
- **No toca la asignación entre SKU** como hipótesis propia. La razón crítica por SKU
  la aproxima —reasigna buffer según la economía de cada uno— pero no aísla "el mismo
  dinero, repartido mejor" de "más dinero".
