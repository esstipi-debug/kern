# 02 · División del Trabajo — Kern vs. Tú

> La línea exacta entre lo que hace el agente y lo que **solo un humano puede
> hacer**, con los mecanismos de código que la sostienen
> (`src/guided.py`, `src/escalation.py`, `src/writeback.py`).

---

## 🧱 El principio: "nunca desprotegido", pero nunca sin dueño

Kern implementa la **Guided Execution Layer** (`src/guided.py`): *ninguna
tarea termina en un callejón sin salida.* Un resultado consecuente es **ejecutado
con seguridad por el agente**, o lleva **al menos un camino ejecutable** que tú
puedes accionar — opciones rankeadas, un paquete de *handoff* prellenado, o una
escalación — **más** una declaración explícita de cualquier **residuo** que el
humano debe cubrir y el riesgo de omitirlo.

Tu contraparte del contrato: **nunca dejas un desenlace sin cerrar.**

---

## 🔀 Los cuatro desenlaces (`GuidedOutcome.status`)

Cada resultado consecuente termina en exactamente uno de estos cuatro estados.
Saber **cuál te toca a ti** es el núcleo de tu rol.

| Estado | Significado | ¿Humano? | Tu acción |
|---|---|---|---|
| `EXECUTED` | El agente lo hizo con seguridad (o lo dejó listo para aplicar con un clic) | Opcional | Revisar la evidencia y archivar |
| `OPTIONS` | El agente ofrece opciones rankeadas y ejecutables | **Sí** | Elegir (hay una `recommended`) y autorizar |
| `HANDOFF` | Un paso humano-único, ya preparado por el agente | **Sí** | Ejecutar el paquete en el mundo real |
| `ESCALATED` | Enrutado al humano correcto con contexto (disputa / legal / $) | **Sí** | Confirmar ruta y dar seguimiento hasta cierre |

> La QA del agente (`verify_guided`) **rechaza** cualquier resultado que diga ser
> consecuente sin opciones, *handoff* ni escalación — es imposible, por diseño,
> que llegue a ti un callejón sin salida. Pero también es imposible que el sistema
> *cierre por ti* las tres últimas filas.

---

## 📦 Las piezas que recibes (estructuras de `src/guided.py`)

### `ExecutionOption` — una opción ejecutable
`label`, `summary`, `score`, `recommended`, `action` (la acción concreta lista
para correr, p. ej. el id de un *changeset* en etapa), `tradeoffs`.
→ **Tu trabajo:** comparar *tradeoffs*, validar la `recommended` contra el
contexto del cliente, y disparar la `action`.

### `HandoffPacket` — un paso humano, ya preparado
`title`, `steps` (la lista de pasos), `artifact` (el borrador prellenado: texto
de PO, email, hoja de conteo, formulario de reclamo…), `data`, `deadline`,
`risk_if_skipped`.
→ **Tu trabajo:** ejecutar los `steps` con el `artifact` listo, antes del
`deadline`. El borrador ya está escrito; tú lo envías/firmas/llamas.

### `EscalationPacket` — todo para que un humano actúe
`reason`, `route_to`, `recommendation`, `options`, `citations`, `sla`.
→ **Tu trabajo:** confirmar que llegue a `route_to` dentro del `sla`, con la
`recommendation` y las `citations` como soporte.

### `Residual` — lo que el agente **no** hizo
`description`, `owner` (por defecto `human`), `risk_if_skipped`.
→ **Tu trabajo:** cubrir el residuo, o registrar conscientemente que lo asumes y
por qué. Un residuo sin `risk_if_skipped` ni siquiera pasa la QA del agente — así
que siempre sabrás qué te juegas al omitirlo.

---

## 🔐 El plano de *writeback*: dónde el humano controla el dinero

Kern **nunca muta un sistema de registro a ciegas** (`src/writeback.py`).
Todo cambio se calcula como un `Changeset` en seco y se clasifica por **risk
tier** según su reversibilidad:

| Risk tier | Qué es | ¿Requiere tu aprobación? |
|---|---|---|
| `read` | Solo lectura | No |
| `reversible` | Una escritura que se puede deshacer limpiamente (p. ej. fijar un campo) | **Sí**, salvo que se habilite *auto-apply* explícito |
| `irreversible` | Una escritura que **no** se puede deshacer con seguridad (p. ej. enviar una PO) | **Siempre sí** |

Mecánica de la aprobación humana (la que **tú** ejerces):

1. Kern **prepara** (`stage`) el *changeset*: objetivo, lista de cambios,
   `risk_tier`, `idempotency_key` (clave idempotente), y la **razón**.
2. Tú lo revisas y, si procede, **apruebas** (`approve`) — la aprobación queda
   ligada a esa `idempotency_key` y **expira en 15 minutos** (`ttl_seconds=900`).
   No es un cheque en blanco: aprobaste *ese* cambio, *ahora*.
3. Kern **aplica** (`apply`) de forma **idempotente** (re-aplicar la misma
   clave no duplica) y deja un `AuditEntry`.
4. Si algo sale mal, hay **`rollback()`** por `idempotency_key`.

Si falta o no es válida la aprobación, el sistema **rechaza** con
`WritebackRefused`. **Tú eres la única razón por la que un cambio irreversible
ocurre.**

---

## 🧭 Las escalaciones, con ruta y SLA por defecto (`src/escalation.py`)

Cuando un resultado debe ir a un humano, Kern lo enruta con un SLA. Conoce
estos disparadores y a quién van — son tu mapa de *quién decide qué*:

| Disparador | Ejemplos | Ruta por defecto | SLA por defecto |
|---|---|---|---|
| `dispute` | OS&D, facturación, referencias de booking | claims / account manager | mismo día hábil |
| `legal` | clasificación aduanera, responsabilidad, contratos | legal / agente aduanal licenciado | **antes de cualquier acción** |
| `financial_threshold` | gasto/compromiso por encima del límite de auto-aprobación | aprobador de finanzas | **antes del compromiso** |
| `operational` | decisión operativa genérica que necesita humano | dueño de operaciones | mismo día hábil |

> Para `legal` y `financial_threshold`, el SLA es **bloqueante**: ninguna acción
> ocurre hasta que el humano correcto responde. Si **tú** eres ese rol, eres el
> cuello de botella deliberado del sistema; si no lo eres, tu trabajo es que
> llegue a quien sí.

---

## 🗂️ Tabla maestra: ¿Kern o tú?

| Actividad | Kern | Tú |
|---|---|---|
| Pronosticar demanda, calcular `(s,Q)`/`(R,S)`, EOQ, safety stock | ✅ | — |
| Clasificar ABC-XYZ, calcular buffers DDMRP, *landed cost*, *cost-to-serve* | ✅ | — |
| Correr QA numérica contra simulación Monte-Carlo | ✅ | — |
| Fundamentar cada número en la literatura (L3, 23 libros) | ✅ | — |
| Preparar el *changeset*, el *handoff*, la escalación | ✅ | — |
| **Aprobar un cambio irreversible** (emitir PO, comprometer gasto) | Prepara | ✅ **Decide** |
| **Elegir entre opciones rankeadas** cuando hay *tradeoffs* de negocio | Rankea | ✅ **Elige** |
| **Llamar al proveedor, firmar el conteo físico, negociar la disputa** | Redacta | ✅ **Ejecuta** |
| **Enrutar una escalación legal/financiera** y dar seguimiento | Enruta + SLA | ✅ **Confirma** |
| **Juzgar si los supuestos aplican a *este* cliente** | Declara supuestos | ✅ **Valida** |
| **Presentar al cliente, defender la recomendación, cobrar** | Produce el artefacto | ✅ **Vende** |
| **Asumir la responsabilidad de la decisión** | — | ✅ **Responde** |

---

> El siguiente documento, [03 · Metodologías](03_methodologies.md), te da el
> *cómo*: las metodologías que debes dominar para ejecutar bien tu mitad de esta
> tabla.
