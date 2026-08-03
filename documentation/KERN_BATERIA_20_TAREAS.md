# Kern — Batería de 20 tareas del oficio

> **Vara común para medir cualquier alternativa del mercado, incluida la propia.** Medición del 02-ago-2026, corrida sobre el commit [`73b912d`](https://github.com/esstipi-debug/kern/commit/73b912d).
>
> **Qué es.** Una batería de 20 tareas del rol de supply chain manager, derivada de SCOR + CSCP + descripciones de puesto reales. Sirve como criterio único para puntuar Excel, un SaaS de planificación, un planificador humano y Kern con la misma regla.
>
> **Uso previsto: pieza de venta, no roadmap.** Como lista de pendientes se come meses sin acercar un cliente. Como scorecard de categoría — Excel / SaaS según su documentación pública con cita textual / Kern con su puntaje sin maquillar — es el argumento más honesto que se puede poner sobre una mesa.

---

## 0. Reglas de imparcialidad (lo que hace válida la vara)

Tres reglas, aplicadas antes de puntuar nada:

1. **La batería no se derivó de las capacidades de Kern.** El agente que la escribió tenía prohibido leer el repositorio. Si la vara sale del producto, mide el producto contra sí mismo.
2. **Cinco tareas son deliberadamente humanas** — T15 negociar cara a cara con el proveedor, T16 expediting telefónico, T17 racionar stock entre clientes, T18 sostener el plan frente a la presión interna, T19 conteos cíclicos y exactitud física del inventario — para que el techo de *cualquier* software quede visible en la misma tabla.
3. **Kern se puntuó corrido, no descrito.** Con el venv del repo sobre Superstore, Olist y 777 órdenes de compra reales. Una capacidad descrita es una promesa; una capacidad corrida es un hecho.
4. **El puntaje lo emite un harness, no un juicio.** `src/bateria/` corre las 20 tareas contra el criterio escrito por adelantado y devuelve cumple/parcial/no **con evidencia campo por campo**. Cualquiera con el repo obtiene el mismo número sobre el mismo commit.

---

## 1. Los puntajes

| Sujeto | Puntaje | Lectura |
|---|--:|---|
| **Techo humano** (planificador con experiencia) | **75%** · 15/20 | las 5 tareas humanas son el techo estructural de todo software |
| **Techo de Kern sin ningún cliente** | **42,5%** | ningún PR mueve esta cifra: falta el dato, no el código |
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

## 2. El hallazgo que reordena la estrategia

De las **15** tareas alcanzables por software:

| Bloqueo | Tareas | Qué significa |
|---|--:|---|
| **Bloqueadas por dato del cliente** | **11** | no se cumplen con **ninguna** cantidad de código sobre ningún archivo que exista hoy en el repo. Falta stock on-hand real, órdenes en tránsito, pedidos comprometidos, condiciones de pago |
| Código puro | 2 | T01 banda de incertidumbre · T02 columna de bias |
| Devuelven un número apoyado en un supuesto propio | 3 | `unit_cost=1.0` en las 9.826 filas del canónico → el ABC sale por unidades y se rotula "annual value"; margen imputado 0,6·precio |

**La conclusión comercial:** el cuello de botella no es el motor. Son **cuatro adjuntos por mail** — inventario, ventas, compras, proveedores — que desbloquean 12 de las 20 tareas. Eso convierte al intake en el producto, no en el trámite previo al producto.

**Este desglose es el del 25-jul y no se re-derivó. Los cubos ya no suman.** Dos motivos, ambos declarados en vez de maquillados:

- **T18 estaba contada como "código puro"** ("campos tipados del ledger"). La fuente la marca `TAREA HUMANA` y su enunciado real es *"sostener el plan frente a la presión interna"* — nada que ver. Sale del cubo, que baja de 3 a 2, y por eso 11 + 2 + 3 = 16 sobre 15 alcanzables: **el conteo original tenía al menos un error propio**, independiente del recuento de humanas.
- **El harness marca `SIN_DATO` en dos tareas (T03, T11), no en once**, pero mide otra cosa: sólo donde el check pide un dataset por ruta, mientras el conteo de arriba clasificó tarea por tarea a mano.

**No afirmar ninguno de los dos como el vigente hasta re-derivarlo**, y lo mismo vale para el 42,5% de "techo sin ningún cliente" de la tabla anterior, que sale de este desglose. La conclusión comercial de los cuatro adjuntos no depende de cuál gane.

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

---

*Kern — el núcleo que tu cadena no tiene.*
