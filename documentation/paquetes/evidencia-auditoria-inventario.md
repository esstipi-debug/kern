# Evidencia de Auditoría de Inventario

> **USD 6.000–15.000 por ciclo de auditoría** · único, recurrente cada auditoría (anual)
> Para el controller/CFO de una empresa privada en su primera auditoría o
> pre-IPO, o una firma de auditoría mid-tier que subcontrata la preparación
> de papeles de trabajo de inventario/COGS.

> **Estado: en desarrollo — este one-pager describe la oferta, todavía no es
> un paquete ejecutable.** El motor matemático (`src/audit_evidence.py`) ya
> está construido y probado (30 tests, verificados contra las mismas
> referencias que usa un manual de auditoría — ver más abajo). Falta el
> cableado a un paquete corrible por `run_package.py`
> (`jobs/audit_evidence_job.py` + registro del tool en `scm_agent/tools.py`)
> y la validación de las tablas AICPA con un auditor practicante — ver el
> estado archivo por archivo en
> [`AUDIT_EVIDENCE_DESIGN.md`](../AUDIT_EVIDENCE_DESIGN.md), sección 6.
> Mientras tanto, esta sección **no** se ofrece activamente a un prospecto.

## Por qué esta sección es distinta a las otras 7

Las secciones 1–7 le venden al **comprador de operaciones**: ahorro
discrecional, si no compra sigue igual. Esta le vende al **comprador de
cumplimiento**, cuyo gasto es obligatorio y con fecha límite — nadie pasa una
auditoría sin preparar la evidencia de inventario, y el inventario es una de
las áreas con más hallazgos de deficiencia en las inspecciones PCAOB. El
cambio de norma AS 1215 además reduce la ventana de ensamblaje de papeles de
trabajo de 45 a 14 días desde diciembre de 2026 — lo que hoy se arma a mano
en semanas tiene que quedar listo casi al cierre.

## Qué vas a recibir (una vez cerrado el cableado)

Un papel de trabajo listo para el expediente de auditoría, no solo un reporte
ejecutivo:

1. **Plan de muestreo defendible** — muestreo de atributos (tests de
   controles) o Monetary Unit Sampling / MUS (pruebas sustantivas de
   detalle), con el tamaño de muestra justificado y citado por tabla y
   norma — no "seleccionamos 25 ítems al ojo".
2. **Tie-out al libro mayor** — tu listado de inventario conciliado contra
   la cuenta de control del libro mayor, con las partidas de conciliación
   clasificadas (timing vs. no explicadas) y la diferencia no explicada a
   la vista.
3. **Linaje verificable por hash** — cada número del papel de trabajo queda
   trazado hasta el archivo de origen (hash SHA-256), los parámetros
   realmente usados y la versión de la fórmula, para que un revisor
   experimentado pueda re-derivar el resultado exacto sin preguntarte nada
   (el estándar de re-desempeño de AS 1215).
4. **Papel de trabajo de 6 hojas (W-1 a W-6)** — alcance, plan de muestreo,
   listado de selección, evaluación, tie-out y linaje, con espacio para
   tickmarks y la firma del auditor de registro.

## Lo que Linchpin no hace, a propósito

Linchpin nunca firma la auditoría. El resultado siempre termina en un
`HANDOFF` (papel listo para que el auditor haga el trabajo de campo, concluya
y firme) o un `ESCALATED` (cuando el límite superior de error supera la
materialidad tolerable, o el tie-out queda fuera de tolerancia) — nunca en un
`EXECUTED` autónomo. Se vende preparación de evidencia del lado de la
gerencia (o capacidad white-label para una firma), no la opinión del auditor.

## Qué te vamos a pedir

| Archivo / insumo | Contenido |
|---|---|
| `libro_mayor.csv` | Extracto del libro mayor / balance de comprobación de la cuenta de inventario |
| `subledger.csv` | Listado de inventario (o la salida de clasificación ABC-XYZ ya corrida) — la población a muestrear |
| Materialidad y riesgo | Error tolerable (TM) y riesgo de aceptación incorrecta (RIA) — son juicio del auditor de registro; Linchpin nunca los infiere ni los deriva |

## Por qué la matemática ya es confiable, aunque falte el cableado

`src/audit_evidence.py` reproduce en forma cerrada (Poisson/gamma para MUS,
binomial exacto para atributos) los mismos números que trae cualquier tabla
de referencia de la guía AICPA de muestreo de auditoría: factor de
confiabilidad al 5% = 3,00; tamaño de muestra de atributos al 95% de
confianza con 10% de tasa tolerable de desviación = 29; al 5% = 59. Los 30
tests del módulo anclan cada uno de estos valores contra la cita externa, no
contra el propio código. Es motivo por el que esta sección está calificada
como la de mejor relación esfuerzo/precio del backlog: la parte matemática
más difícil de defender frente a un revisor ya está resuelta y probada: falta
conectarla al flujo de intake → entregable que ya usan las otras 7 secciones.

## Qué sigue después

Fase 2 (diseño ya escrito, no empezada): **add-on SOX 404** — matriz de
riesgo-control, test de controles y clasificación propuesta de deficiencias,
USD 2.000–4.000/mes sobre Growth o Scale, solo para empresas públicas o
pre-IPO. Reutiliza el mismo motor de muestreo de atributos y el registro de
evidencia de esta sección.
