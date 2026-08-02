# Kern — Batería de 20 tareas del oficio

> **Vara común para medir cualquier alternativa del mercado, incluida la propia.** Medición del 25-jul-2026.
>
> **Qué es.** Una batería de 20 tareas del rol de supply chain manager, derivada de SCOR + CSCP + descripciones de puesto reales. Sirve como criterio único para puntuar Excel, un SaaS de planificación, un planificador humano y Kern con la misma regla.
>
> **Uso previsto: pieza de venta, no roadmap.** Como lista de pendientes se come meses sin acercar un cliente. Como scorecard de categoría — Excel / SaaS según su documentación pública con cita textual / Kern con su puntaje sin maquillar — es el argumento más honesto que se puede poner sobre una mesa.

---

## 0. Reglas de imparcialidad (lo que hace válida la vara)

Tres reglas, aplicadas antes de puntuar nada:

1. **La batería no se derivó de las capacidades de Kern.** El agente que la escribió tenía prohibido leer el repositorio. Si la vara sale del producto, mide el producto contra sí mismo.
2. **Tres tareas son deliberadamente humanas** — T15 negociar con proveedor, T16 expediting telefónico, T17 racionar stock entre clientes — para que el techo de *cualquier* software quede visible en la misma tabla.
3. **Kern se puntuó corrido, no descrito.** Con el venv del repo sobre Superstore, Olist y 777 órdenes de compra reales. Una capacidad descrita es una promesa; una capacidad corrida es un hecho.

---

## 1. Los puntajes

| Sujeto | Puntaje | Lectura |
|---|--:|---|
| **Techo humano** (planificador con experiencia) | **85%** · 17/20 | las 3 tareas humanas son el techo estructural de todo software |
| **Techo de Kern sin ningún cliente** | **42,5%** | ningún PR mueve esta cifra: falta el dato, no el código |
| **Kern, corrido** | **7,5%** · 1,5/20 | cero tareas en "cumple" |

**Sobre el 7,5%.** El primer juez había puesto 22,5%. Dos lentes adversariales que auditaron fila por fila lo bajaron a 7,5% y ~5%. Se publica el número bajo, no el alto. Un puntaje inflado en una vara que uno mismo propone destruye la vara.

**Advertencia de vigencia.** La medición es del 25-jul-2026, anterior a los merges #193 y #194, que cerraron cinco degradaciones silenciosas (ver [`KERN_AUTOPSIA_FALLAS.md`](KERN_AUTOPSIA_FALLAS.md)). El puntaje **no fue re-corrido** después de esos merges. Antes de usar esta cifra en una pieza pública, re-correr la batería y sellar el commit.

---

## 2. El hallazgo que reordena la estrategia

De las 17 tareas alcanzables por software:

| Bloqueo | Tareas | Qué significa |
|---|--:|---|
| **Bloqueadas por dato del cliente** | **11** | no se cumplen con **ninguna** cantidad de código sobre ningún archivo que exista hoy en el repo. Falta stock on-hand real, órdenes en tránsito, pedidos comprometidos, condiciones de pago |
| Código puro | 3 | T01 banda de incertidumbre · T02 columna de bias · T18 campos tipados del ledger |
| Devuelven un número apoyado en un supuesto propio | 3 | `unit_cost=1.0` en las 9.826 filas del canónico → el ABC sale por unidades y se rotula "annual value"; margen imputado 0,6·precio |

**La conclusión comercial:** el cuello de botella no es el motor. Son **cuatro adjuntos por mail** — inventario, ventas, compras, proveedores — que desbloquean 12 de las 20 tareas. Eso convierte al intake en el producto, no en el trámite previo al producto.

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

Lo que hace que funcione no es el puntaje de Kern: es que las tres tareas humanas queden visibles y que Kern aparezca con un número bajo. Una vara donde el que la propone gana no convence a nadie que sepa del oficio.

---

## 5. Relación con las otras mediciones

No se contradicen porque miden cosas distintas:

- La auditoría de completitud (16-jul) mide **el motor contra su propio roadmap** — ~72% motor, ~50% e2e.
- Esta batería mide **el entregable contra el criterio del oficio** — 7,5% corrido.

Un motor al 72% de su roadmap puede entregar el 7,5% de lo que el oficio pide, y las dos cosas ser ciertas: el roadmap fue escrito por nosotros y el criterio del oficio no.

---

*Kern — el núcleo que tu cadena no tiene.*
