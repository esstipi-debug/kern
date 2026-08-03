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
| **Techo de Kern sin ningún cliente** | **60%** · 12/20 | sólo 3 tareas dependen de que el cliente entregue algo (§2). Entre el 27,50% de hoy y este techo no hay dato de por medio: es todo código |
| **Kern, corrido** | **27,50%** · 5,5/20 | 2 tareas cumplen · 7 quedan a medias |

**Sobre el 27,50%, y por qué subió.** La cifra anterior publicada era **7,5%**, puntuada a mano el 25-jul: el primer juez había puesto 22,5% y dos lentes adversariales que auditaron fila por fila lo bajaron a 7,5% y ~5%. Se publicó el número bajo, no el alto.

Un número que sube en la vara que uno mismo propone merece desconfianza por defecto, así que la cadena está medida, no argumentada:

| Momento | Puntaje | Quién puntúa |
|---|--:|---|
| 25-jul, código de entonces | 7,5% · 1,5/20 | juicio manual adversarial |
| **#202**, mismo código, harness recién escrito | **10,0%** · 2,0/20 | harness mecánico |
| #203 en adelante | 27,50% · 5,5/20 | harness mecánico |

Sobre el **mismo código**, el harness cae a **media tarea** del veredicto adversarial. No es un juez más blando: la subida viene de [#203](https://github.com/esstipi-debug/kern/pull/203), que arregló los defectos de cableado que la propia batería destapó — la matemática que existía en `src/` y el job no recibía. El criterio no se movió: `tareas.json` está congelado por `tests/test_bateria_tareas.py`.

**Sigue siendo un puntaje bajo, y esa es la lectura.** 27,50% sobre un techo de 75% son dos tareas cumplidas de veinte.

**Vigencia.** Corrida el 02-ago-2026 sobre `73b912d`, posterior a #193/#194 (cinco degradaciones silenciosas cerradas, ver [`KERN_AUTOPSIA_FALLAS.md`](KERN_AUTOPSIA_FALLAS.md)) y a #203. Reproducible: dos corridas seguidas dan el mismo vector de estados. **Nada la congela** — ni un test ni la CI corren la batería, así que un cambio en `jobs/` puede moverla sin que nada se ponga rojo. Re-correr y sellar el commit antes de cada uso público.

---

## 2. Dónde está el bloqueo, tarea por tarea

Re-derivado el 02-ago-2026 sobre `73b912d`. Sustituye al desglose del 25-jul, cuyos cubos no cerraban.

**El discriminador, y por qué es el correcto:** *¿pasaría la tarea si el cliente entregara hoy el dato perfecto, sin tocar una línea de código?* Si no, el bloqueo es el entregable, no el dato.

El harness responde exactamente esa pregunta, porque corre 13 de las 15 tareas **sobre fixtures donde el dato sí está** — y aun así el campo que el criterio exige no sale. Eso es evidencia directa: no hace falta saber si el cliente tiene el dato para saber que entregárselo no alcanzaría.

### El desglose

| Bloqueo | Tareas | Qué significa |
|---|--:|---|
| **Cumple** | **2** | T01 · T04 |
| **El entregable no tiene el campo** | **10** | T02 · T05 · T06 · T07 · T08 · T09 · T12 · T13 · T14 · T20. Con el dato puesto, el campo sigue sin salir. **El código va primero**; varias necesitarán dato *después* |
| **Falta el dato y no hay sustituto** | **3** | T03 ventas 12m con costo · T11 stock on-hand · T10 líneas de pedido reales. Ningún PR las mueve |

2 + 10 + 3 = 15. **Cierra.**

**T10 está en el cubo de dato, pero arrastra un defecto propio que no espera al cliente:** hoy reporta el fill rate *del modelo* bajo la etiqueta de servicio *realizado*. Sin una sola línea de pedido, con SS=50 y sigma=40 sobre demanda de ciclo 500, `fill_rate_from_safety_stock` devuelve 99,6%. El criterio lo descalifica por nombre, y con razón: es un número que parece correcto y no lo es. Corregir la etiqueta no necesita dato de nadie.

### La evidencia, tarea por tarea

Cada línea es lo que el harness devolvió, no una opinión:

| Tarea | Qué falta en el entregable |
|---|---|
| T02 | el ciclo anterior congelado — y lo produce Kern, no el cliente |
| T05 | el motor descuenta el en-tránsito, pero sale una lista por SKU, no una OC por proveedor con fechas |
| T06 | ajusta MOQ 250 y múltiplo 48 (50 → 288 u) pero no emite recomendación sí/no |
| T07 | **ningún** campo del entregable lleva semana ni vencimiento. Las condiciones de pago no tendrían dónde entrar |
| T08 | mide el lead time real (ACME 22,0 · BETA 7,5) pero no lista qué SKU deben recalcular SS/ROP |
| T09 | el panel promedia **una sola columna de precio**: el desvío OC-vs-facturado no cabe en el modelo actual |
| T12 | el precio propuesto sale `[None, None]` — no propone precio, propone remate |
| T13 | rankea por EMV y ancla a proveedor, pero ninguna mitigación lleva **cuándo** |
| T14 | el acta registra cuánto cede cada sombrero, pero sin número de demanda acordado ni decisión fechada |
| T20 | el motivo sí cambia la disposición, pero no sale la tasa de devolución por motivo |

### Lo que esto cambia

**El desglose del 25-jul decía lo contrario: 11 de 17 bloqueadas por dato del cliente.** Sale casi invertido — **10 de 15 no las mueve el dato**. Tres cosas lo explican, y ninguna es que aquel análisis fuera deshonesto:

- **#203 arregló una tanda de cableado** entre medio, y el puntaje pasó de 10,0% a 27,50%. Lo que quedó al descubierto debajo es la capa de entregable.
- **Aquella corrida fue sobre datasets reales** (Superstore, Olist, 777 OC) que hoy **no están en el repo**: `data/kaggle/` es gitignored. Corriendo sobre ellos, la falta de dato tapa la falta de campo.
- **Los cubos eran excluyentes y la realidad está en capas.** Casi todas necesitan código *y después* dato. T07 y T09 son el caso puro: aunque el cliente entregue condiciones de pago y facturas hoy, no hay campo donde ponerlas.

**No sostiene la conclusión de los cuatro adjuntos.** Entregarlos hoy —inventario, ventas, compras, proveedores— mueve **3 tareas**, no 12, y aun ésas exigen que el entregable esté bien. El intake sigue importando, pero como segundo paso: **el cuello de botella hoy es la capa de entregable, que es exactamente el patrón de la §3.**

Es la lectura incómoda y por eso vale: dice que el trabajo pendiente es propio, no del cliente.

### Techo sin ningún cliente: 60%

Se re-deriva de lo anterior: 20 − 5 humanas − 3 bloqueadas por dato = **12 alcanzables sin que el cliente entregue nada** → **60% · 12/20**. Sustituye al 42,5% publicado, que salía del desglose viejo.

Dicho de otro modo: entre el 27,50% de hoy y el 60%, **no hay ningún dato del cliente de por medio**. Sólo código.

---

## 3. El patrón transversal (verificado 4+ veces)

**La matemática existe en `src/` y el job de `jobs/` no le pasa los datos — y la salida sale igual, con `confidence=1.00`.**

Casos verificados en la corrida del 25-jul:

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
- Esta batería mide **el entregable contra el criterio del oficio** — 27,50% corrido.

Un motor al 72% de su roadmap puede entregar el 27,50% de lo que el oficio pide, y las dos cosas ser ciertas: el roadmap fue escrito por nosotros y el criterio del oficio no.

La §2 explica **dónde** se pierde esa diferencia: no en la matemática, sino en la capa de entregable. Diez de las quince tareas alcanzables fallan porque el campo que el oficio exige no sale del job, con el dato ya puesto.

---

*Kern — el núcleo que tu cadena no tiene.*
