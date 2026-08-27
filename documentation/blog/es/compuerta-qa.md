---
pair: qa-gate
lang: es
slug: compuerta-qa
title: "Compuerta de QA: qué pasa cuando la cifra de inventario no se sostiene"
description: "Un chatbot siempre devuelve una cifra. La compuerta del orquestador puede no devolver ninguna: si la verificación falla, no se escribe entregable."
date: 2026-08-25
updated: 2026-08-25
query: "IA inventario cifras sin fuente"
answer: >
  Una compuerta de QA es una verificación automática que corre entre el cálculo
  y el entregable: si encuentra una sola inconsistencia, el trabajo termina en
  estado qa_failed y no se escribe ningún archivo. La decisión de escribir no
  vive en la capacidad que calcula, vive en el corredor que la llama. Un chatbot
  siempre devuelve un párrafo; una compuerta puede devolverte la lista de
  problemas y ninguna cifra.
cta: diagnostico
offer: diagnostico-arranque
sources:
  - kind: code
    ref: scm_agent/orchestrator.py
  - kind: code
    ref: scm_agent/packages.py
  - kind: code
    ref: scm_agent/guided_bridge.py
  - kind: code
    ref: jobs/qa.py
  - kind: code
    ref: src/forecasting.py
  - kind: code
    ref: webapp/demo_scan.py
faq:
  - q: "¿Puedo igual recibir un número equivocado?"
    a: "Sí. Verifica coherencia interna, rangos y evidencia: que la inversión sume, que un buffer tenga dispersión atrás, que la historia llegue al mínimo de seis períodos. No verifica que tu costo unitario esté bien cargado ni que hayas exportado el canal correcto. Basura consistente entra y sale: QA no es omnisciencia."
  - q: "¿Qué pasa cuando la QA falla?"
    a: "El trabajo termina en qa_failed y el diccionario de entregables vuelve vacío. Recibís un escalamiento con la lista de problemas, el destinatario (dueño del dato o analista) y la recomendación de corregir los insumos marcados y volver a correr."
  - q: "¿Igual lo mira un humano?"
    a: "Sí. De los cuatro desenlaces posibles de un resultado guiado, tres se detienen en un humano por diseño. En el camino de escritura, lo clasificado como irreversible pide aprobación explícita, con caducidad y con rollback."
  - q: "¿Usan mi CSV para entrenar un modelo público?"
    a: "El escaneo del demo no toca ningún proveedor: webapp/demo_scan.py importa los tres jobs y pandas, nada más. En el motor completo, lo único que puede salir hacia un proveedor externo es el resumen ya calculado y el texto del pedido, nunca las filas del CSV, y solo con una clave configurada. El anexo de tratamiento de datos del repositorio (documentation/legal/dpa-lite.md) es un borrador pendiente de revisión legal: la versión que obliga se acuerda por escrito antes del diagnóstico."
  - q: "¿Esto es la Control Tower?"
    a: "No. Hoy corre el motor en producción con un operador humano: un orquestador que rutea, verifica y retiene la salida cuando no pasa. La torre de control que decide sola es plan, no producto."
noindex: false
---

# Compuerta de QA: qué pasa cuando la cifra de inventario no se sostiene

Una compuerta de QA es una verificación automática que corre entre el cálculo y el entregable: si encuentra una sola inconsistencia, el trabajo termina en estado qa_failed y no se escribe ningún archivo. La decisión de escribir no vive en la capacidad que calcula, vive en el corredor que la llama. Un chatbot siempre devuelve un párrafo; una compuerta puede devolverte la lista de problemas y ninguna cifra.

## El número plausible

Ya probaste esto. Pegaste un export de ventas en un chatbot, preguntaste cuánto comprar del SKU que más te preocupa, y volvió un párrafo ordenado con una cifra adentro. La cifra tenía la forma correcta, pero no traía definición de σ, no decía sobre cuántos períodos de historia se armó y, sobre todo, no existía ningún caso en que esa herramienta se hubiera negado a contestar. Un sistema que siempre responde emite la misma señal cuando el cálculo se sostiene y cuando no.

## Qué veta la compuerta

Cada capacidad corre el mismo contrato de cuatro etapas: `prepare → run → qa → deliver`. Aporta las cuatro, pero no elige cuándo se llama a `deliver`: eso lo decide el corredor que la invoca, después de la compuerta. Fuera de los tests hay exactamente dos corredores que llaman `tool.qa(...)` —`scm_agent/orchestrator.py` y `scm_agent/packages.py`— y ninguno escribe si la compuerta devuelve algo.

En el trabajo suelto el orden es literal (`scm_agent/orchestrator.py`, líneas 204-211):

```
issues = tool.qa(produced.report)
if issues:
    return JobResult(status=STATUS_QA_FAILED, deliverables={}, qa_issues=issues, ...)
```

`deliver()` está más abajo, después de ese `return`: un solo problema termina la función antes de escribir nada. No existe entregable "con observaciones". De los cinco estados posibles solo `ok` llega al código que escribe. El corredor de paquetes es más estricto: calcula contra un directorio temporal y recién escribe si ningún paso volvió en `qa_failed`.

Lo que mira `jobs/qa.py` sobre un reporte de política es igual de concreto:

- que la inversión declarada iguale la de ciclo más la de seguridad, dentro de tolerancia;
- que ningún stock de seguridad ni punto de reorden sea negativo;
- que una `(s, Q)` traiga cantidad positiva, y una `(R, S)` un nivel objetivo por encima de su punto de reorden;
- que ninguna política con cantidad se haya armado sobre menos de seis períodos;
- que ningún buffer positivo salga de un SKU cuya dispersión no era estimable.

Las últimas dos le contestan al chatbot. Seis es `MIN_PERIODS_FOR_POLICY` en `src/forecasting.py`, y el comentario del archivo lo llama umbral de negativa, no perilla. El campo `sigma_source` deja escrito de dónde vino la dispersión; si dice `unavailable` y aun así hay buffer, ese número está fabricado.

## La falla que dejó el campo

El campo que lee esa última regla salió de una falla propia, documentada en `documentation/KERN_AUTOPSIA_FALLAS.md`. Croston y TSB emiten un `NaN` de calentamiento en sus valores ajustados; como `NaN > 0` es falso, se propagaba y el motor caía al respaldo de desviación cruda sin una sola advertencia. Sobre el portafolio de ejemplo del repositorio se usó una sigma de 2,626 donde correspondía 2,753: 57,17 unidades de buffer donde iban 62,09 (cifras del propio documento, sobre datos de ejemplo, no de un cliente). Se cerró con un test que reproduce la falla primero, y de ahí salieron `n_error_obs_dropped` y `sigma_source`.

Apareció corriendo el código contra sus propios datos, no leyendo la salida y encontrándola convincente: que es lo que produce esta clase de error.

## Los cuatro desenlaces

Cuando la QA falla el trabajo no se evapora. `scm_agent/guided_bridge.py` mapea cada estado a un desenlace con un camino ejecutable:

| Estado | Desenlace | Qué recibís |
|---|---|---|
| `ok` | `EXECUTED` | los entregables |
| `needs_clarification` | `OPTIONS` | las capacidades candidatas, para elegir |
| `needs_data` | `HANDOFF` | qué archivo falta y qué se rompe sin él |
| `qa_failed` | `ESCALATED` | los problemas, ruteados al dueño del dato |
| `error` | `ESCALATED` | ruteado a soporte |

El paquete de `qa_failed` lleva la razón —la compuerta falló, la salida se retiene—, el destinatario, la recomendación de corregir los insumos y volver a correr, y la lista completa de problemas. Es una orden de trabajo, no un mensaje de error.

De los cuatro desenlaces posibles, tres se detienen en un humano por diseño. Esa es la frase entera: no hay una frecuencia medida detrás.

Del lado de la escritura rige lo mismo. Un writeback se estagea como changeset de simulación campo por campo, se clasifica por riesgo (`read`, `reversible`, `irreversible`), la aprobación caduca a los 900 segundos y existe `rollback()`; lo irreversible siempre pide aprobación explícita. El plano de control agnóstico de dominio bajo esos desenlaces se llama Nosilo.

## Comparado con qué

**Un chatbot sobre tu export de ventas.** Produce un párrafo fluido, y lo produce siempre. No tiene un estado en el que se niegue ni una constante de historia mínima. La comparación no es quién redacta mejor: es cuál de los dos puede devolverte cero cifras.

**Las reglas min/max de Odoo.** Estáticas: disparan siempre, y por eso tampoco se niegan nunca. Un mínimo mal puesto reordena con la misma convicción que uno bien puesto. Kern no las reemplaza: las convierte en el brazo de ejecución de una política que sí se verifica antes de salir.

**Un SaaS de planificación.** Suele traer el método correcto adentro y dejarlo como casilla opcional que nadie marca, y ninguna etapa se niega a emitir el informe con eso apagado. El problema no es el algoritmo: es que el paso correcto sea salteable.

**Un consultor o planificador.** La certificación prueba que alguien alguna vez supo el método; bajo presión, el paso de verificación es justo el que se saltea, y el conocimiento se va cuando se va la persona.

**La planilla.** Excel calcula lo que le pidas, incluso lo que no debería calcularse: una celda armada con años de historia y una con dos semanas se ven idénticas.

## Lo que Kern no hace

La compuerta no es omnisciencia. Verifica coherencia interna, rangos, finitud y evidencia; no verifica que tu costo unitario esté bien cargado ni que la demanda exportada sea la del canal correcto. Sí garantiza que una salida internamente incoherente no llegue a tu escritorio con cara de reporte.

Tampoco hay compra autónoma: Kern prepara el trabajo y una persona firma lo irreversible. Y no hay un "confiá, es IA": si una afirmación no se puede apuntar a un archivo del repositorio, no se dice. Con las citas rige lo mismo: en el corredor de paquetes una compuerta aparte exige que cada cita resuelva a un nodo real del grafo, a dos saltos de un concepto ancla de la herramienta que corrió, y si sobreviven menos de dos se descarta el lote entero. No es "cada celda lleva pie de página", y no corre en todos los caminos.

## FAQ

### ¿Puedo igual recibir un número equivocado?

Sí. Verifica coherencia interna, rangos y evidencia: que la inversión sume, que un buffer tenga dispersión atrás, que la historia llegue al mínimo de seis períodos. No verifica que tu costo unitario esté bien cargado ni que hayas exportado el canal correcto. Basura consistente entra y sale: QA no es omnisciencia.

### ¿Qué pasa cuando la QA falla?

El trabajo termina en `qa_failed` y el diccionario de entregables vuelve vacío. Recibís un escalamiento con la lista de problemas, el destinatario (dueño del dato o analista) y la recomendación de corregir los insumos marcados y volver a correr.

### ¿Igual lo mira un humano?

Sí. De los cuatro desenlaces posibles de un resultado guiado, tres se detienen en un humano por diseño. En el camino de escritura, lo clasificado como irreversible pide aprobación explícita, con caducidad y con rollback.

### ¿Usan mi CSV para entrenar un modelo público?

El escaneo del demo no toca ningún proveedor: `webapp/demo_scan.py` importa los tres jobs y pandas, nada más. En el motor completo, lo único que puede salir hacia un proveedor externo es el resumen ya calculado y el texto del pedido, nunca las filas del CSV, y solo con una clave configurada. El anexo de tratamiento de datos del repositorio (`documentation/legal/dpa-lite.md`) es un borrador pendiente de revisión legal: la versión que obliga se acuerda por escrito antes del diagnóstico.

### ¿Esto es la Control Tower?

No. Hoy corre el motor en producción con un operador humano: un orquestador que rutea, verifica y retiene la salida cuando no pasa. La torre de control que decide sola es plan, no producto.

## Próximo paso

Traé el CSV de stock que ya tenés: el [Diagnóstico de Arranque](/paquetes/diagnostico-arranque) empieza por revisar de qué dato disponés y qué se puede sostener con él, antes de proponerte una cifra. El escaneo gratuito del sitio corre esta misma verificación: cuando no pasa, marca "QA falló — sin reporte".
