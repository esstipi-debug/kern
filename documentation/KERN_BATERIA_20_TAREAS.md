# Kern — Batería de 20 tareas del oficio

> **Vara común para medir cualquier alternativa del mercado, incluida la propia.** Medición del 02-ago-2026, corrida sobre el commit [`73b912d`](https://github.com/esstipi-debug/kern/commit/73b912d).
>
> **Qué es.** Una batería de 20 tareas del rol de supply chain manager, derivada de SCOR + CSCP + descripciones de puesto reales. Sirve como criterio único para puntuar Excel, un SaaS de planificación, un planificador humano y Kern con la misma regla.
>
> **Uso previsto: pieza de venta, no roadmap.** Como lista de pendientes se come meses sin acercar un cliente. Como scorecard de categoría — Excel / SaaS según su documentación pública con cita textual / Kern con su puntaje sin maquillar — es el argumento más honesto que se puede poner sobre una mesa.

---

## 0. Reglas de imparcialidad (lo que hace válida la vara)

Cuatro reglas, aplicadas antes de puntuar nada:

1. **La batería no se derivó de las capacidades de Kern.** El agente que la escribió tenía prohibido leer el repositorio. Si la vara sale del producto, mide el producto contra sí mismo.
2. **Cinco tareas son deliberadamente humanas** — T15 negociar cara a cara con el proveedor, T16 expediting telefónico, T17 racionar stock entre clientes, T18 sostener el plan frente a la presión interna, T19 conteos cíclicos y exactitud física del inventario — para que el techo de *cualquier* software quede visible en la misma tabla.
3. **Kern se puntuó corrido, no descrito.** Una capacidad descrita es una promesa; una capacidad corrida es un hecho. La corrida manual del 25-jul usó Superstore, Olist y 777 órdenes de compra reales; el harness de hoy corre sobre fixtures versionados, porque `data/kaggle/` es gitignored y no viaja con el repo. **Eso importa para leer la §2**: con fixtures el dato está, así que lo que falta es del entregable.
4. **El puntaje lo emite un harness, no un juicio.** `src/bateria/` corre las 20 tareas contra el criterio escrito por adelantado y devuelve cumple/parcial/no **con evidencia campo por campo**. Cualquiera con el repo obtiene el mismo número sobre el mismo commit.

---

## 1. Los puntajes

| Sujeto | Puntaje | Lectura |
|---|--:|---|
| **Techo humano** (planificador con experiencia) | **75%** · 15/20 | las 5 tareas humanas son el techo estructural de todo software |
| **Techo de Kern sin ningún cliente** | **65%** · 13/20 | sólo T03 y T11 dependen de que el cliente entregue algo (§2). Alcanzado el 2026-09-05: entre el puntaje de hoy y el techo humano sólo quedan esas dos |
| **Kern, corrido** | **65,00% · 13/20** | 13 tareas cumplen · 0 a medias · 2 sin dato del cliente. Emitido por `scripts/bateria_puntaje.py` y congelado por `tests/test_bateria_puntaje_publicado.py` |

**Sobre el 37,50%, y por qué subió.** La cifra anterior publicada era **7,5%**, puntuada a mano el 25-jul: el primer juez había puesto 22,5% y dos lentes adversariales que auditaron fila por fila lo bajaron a 7,5% y ~5%. Se publicó el número bajo, no el alto.

Un número que sube en la vara que uno mismo propone merece desconfianza por defecto, así que la cadena está medida, no argumentada:

| Momento | Puntaje | Quién puntúa |
|---|--:|---|
| 25-jul, código de entonces | 7,5% · 1,5/20 | juicio manual adversarial |
| **#202**, mismo código, harness recién escrito | **10,0%** · 2,0/20 | harness mecánico |
| #203 en adelante | 27,50% · 5,5/20 | harness mecánico |
| #218 | 37,50% · 7,5/20 | harness mecánico |
| **main @ `1aa5098`, 2026-09-05** | **65,00% · 13/20** | harness mecánico, ahora congelado por test |

Sobre el **mismo código**, el harness cae a **media tarea** del veredicto adversarial. No es un juez más blando: la subida viene de [#203](https://github.com/esstipi-debug/kern/pull/203), que arregló los defectos de cableado que la propia batería destapó — la matemática que existía en `src/` y el job no recibía. El criterio no se movió: `tareas.json` está congelado por `tests/test_bateria_tareas.py`.

**La lectura de hoy.** 65,00% sobre un techo humano de 75% son trece tareas cumplidas de veinte; las dos que faltan (T03 y T11) no dependen de código sino de que un cliente entregue su historial de ventas y su stock. El 37,50% que este documento publicó desde agosto quedó 160 commits atrás del harness: siete tareas que decía "a medias" o "no cumple" ya cumplían.

**Vigencia.** Corrida el 2026-09-05 sobre `1aa5098`. Reproducible: dos corridas seguidas dan el mismo vector de estados. **La congela un test:** `tests/test_bateria_puntaje_publicado.py` corre la batería en la CI, fija el vector de estados y exige que este documento y el README de la batería digan lo mismo que `python scripts/bateria_puntaje.py --markdown`. Un cambio en `jobs/` que mueva el marcador pone rojo ese test.

---

## 2. Dónde está el bloqueo, tarea por tarea

Re-derivado el 02-ago-2026 sobre `73b912d`. Sustituye al desglose del 25-jul, cuyos cubos no cerraban.

**El discriminador, y por qué es el correcto:** *¿pasaría la tarea si el cliente entregara hoy el dato perfecto, sin tocar una línea de código?* Si no, el bloqueo es el entregable, no el dato.

El harness responde exactamente esa pregunta, porque corre 13 de las 15 tareas **sobre fixtures donde el dato sí está** — y aun así el campo que el criterio exige no sale. Eso es evidencia directa: no hace falta saber si el cliente tiene el dato para saber que entregárselo no alcanzaría.

### El desglose

| Bloqueo | Tareas | Qué significa |
|---|--:|---|
| **Cumple** | **6** | T01 · T04 · T06 · T08 · T09 · T20 |
| **El entregable no tiene el campo** | **6** | T02 · T05 · T07 · T12 · T13 · T14. Con el dato puesto, el campo sigue sin salir. **El código va primero**; varias necesitarán dato *después* |
| **Falta el dato y no hay sustituto** | **3** | T03 ventas 12m con costo · T11 stock on-hand · T10 líneas de pedido reales. Ningún PR las mueve |

6 + 6 + 3 = 15. **Cierra.**

**T10 no tiene ningún campo de servicio realizado** — ni OTIF de salida, ni fill rate de línea, ni días de quiebre. El único OTIF que Kern calcula es de **entrada** (proveedor → nosotros, ver T09). Y el motor devuelve un fill rate sin una sola línea de pedido: con SS=50 y sigma=40 sobre demanda de ciclo 500, `fill_rate_from_safety_stock` da 99,6%. Es la beta que implica la política, no lo servido; el criterio lo descalifica por nombre.

### La evidencia, tarea por tarea

Cada línea es lo que el harness devolvió, no una opinión:

| Tarea | Qué falta en el entregable |
|---|---|
| T02 | el ciclo anterior congelado — y lo produce Kern, no el cliente |
| T05 | el motor descuenta el en-tránsito, pero sale una lista por SKU, no una OC por proveedor con fechas |
| T07 | **ningún** campo del entregable lleva semana ni vencimiento. Las condiciones de pago no tendrían dónde entrar |
| T12 | el precio propuesto sale `[None, None]` — no propone precio, propone remate |
| T13 | rankea por EMV y ancla a proveedor, pero ninguna mitigación lleva **cuándo** |
| T14 | el acta registra cuánto cede cada sombrero, pero sin número de demanda acordado ni decisión fechada |

### Lo que esto cambia

**El desglose del 25-jul decía lo contrario: 11 de 17 bloqueadas por dato del cliente.** Sale casi invertido — **6 de 15 no las mueve el dato**, y eran 10 antes de #218. Tres cosas lo explican, y ninguna es que aquel análisis fuera deshonesto:

- **#203 arregló una tanda de cableado** entre medio, y el puntaje pasó de 10,0% a 27,50%. Lo que quedó al descubierto debajo es la capa de entregable. **#218 atacó esa capa** y cerró cuatro (T06 · T08 · T09 · T20): 27,50% → 37,50%, sin un solo dato nuevo del cliente.
- **Aquella corrida fue sobre datasets reales** (Superstore, Olist, 777 OC) que hoy **no están en el repo**: `data/kaggle/` es gitignored. Corriendo sobre ellos, la falta de dato tapa la falta de campo.
- **Los cubos eran excluyentes y la realidad está en capas.** Casi todas necesitan código *y después* dato. T07 y T09 son el caso puro: aunque el cliente entregue condiciones de pago y facturas hoy, no hay campo donde ponerlas.

**No sostiene la conclusión de los cuatro adjuntos.** Entregarlos hoy —inventario, ventas, compras, proveedores— mueve **3 tareas**, no 12, y aun ésas exigen que el entregable esté bien. El intake sigue importando, pero como segundo paso: **el cuello de botella hoy es la capa de entregable, que es exactamente el patrón de la §3.**

Es la lectura incómoda y por eso vale: dice que el trabajo pendiente es propio, no del cliente.

### Techo sin ningún cliente: 60%

Se re-deriva de lo anterior: 20 − 5 humanas − 3 bloqueadas por dato = **12 alcanzables sin que el cliente entregue nada** → **60% · 12/20**. Sustituye al 42,5% publicado, que salía del desglose viejo.

Dicho de otro modo: entre el 37,50% de hoy y el 60%, **no hay ningún dato del cliente de por medio**. Sólo código — y #218 ya recorrió 10 puntos de ese tramo.

---

## 3. El patrón transversal (verificado 4+ veces)

**La matemática existe en `src/` y el job de `jobs/` no le pasa los datos — y la salida sale igual, con `confidence=1.00`.**

Casos verificados en la corrida del 25-jul:

- el **resumen ejecutivo** del entregable de inventario citaba `params['service_level']`, el *argumento por defecto*, no lo que corrió. Con niveles diferenciados por SKU abría con "at a 95% cycle service level" mientras la sección de supuestos —tres secciones más abajo, en el mismo documento— listaba los reales (90% y 99%). El comentario del propio código advertía del riesgo junto a la línea que lo cometía. Corregido; el resumen ahora los lee de los SKU
- `lead_time_std` nunca pasado, aunque `src/policies.py` lo acepta
- `bias()` calculado dentro de `compute_metrics` y descartado en la misma línea que lo recibe
- `resale_value` y `unit_cost` defaulteados sin declararlo

Es **cableado, no capacidad**. Pero la lección de alcance importa: ningún ítem de esta lista es "XS / solo cablear" si toca un contrato compartido — esquema CSV, reporte de tool, gate de QA, fixture del harness. Hay 274 archivos de test que definen esos contratos.

**Ítem que requiere re-verificación:** en la corrida del 25-jul, `sourcing` sobre 777 OC reales devolvía el scorecard entero en 0.0 y aun así adjudicaba ganador con `confidence=1.00` y QA limpio. Desde entonces `jobs/sourcing_job.py` incorporó un allowlist de tokens explícito y un mapeo `in_full_status_col` / `in_full_ok_values`. **No se re-corrió**: no afirmar que está cerrado ni que sigue abierto hasta volver a correrlo sobre el mismo dataset.

---

## 4. Cómo se usa como pieza de venta

Tres columnas, misma vara, cita textual en cada celda que no sea propia:

| Columna | Regla de evidencia |
|---|---|
| **Excel** | lo que la tarea exige hacer a mano, sin exagerar |
| **SaaS de planificación** | **solo** lo que su documentación pública dice, con cita textual y enlace. Nunca inferido |
| **Kern** | el puntaje corrido, sin maquillar, con el commit |

Lo que hace que funcione no es el puntaje de Kern: es que las cinco tareas humanas queden visibles y que Kern aparezca con un número bajo. Una vara donde el que la propone gana no convence a nadie que sepa del oficio.

---

## 5. Relación con las otras mediciones

No se contradicen porque miden cosas distintas:

- La auditoría de completitud (16-jul) mide **el motor contra su propio roadmap** — ~72% motor, ~50% e2e.
- Esta batería mide **el entregable contra el criterio del oficio** — 37,50% corrido.

Un motor al 72% de su roadmap puede entregar el 37,50% de lo que el oficio pide, y las dos cosas ser ciertas: el roadmap fue escrito por nosotros y el criterio del oficio no.

La §2 explica **dónde** se pierde esa diferencia: no en la matemática, sino en la capa de entregable. Diez de las quince tareas alcanzables fallan porque el campo que el oficio exige no sale del job, con el dato ya puesto.

---

*Kern — el núcleo que tu cadena no tiene.*
