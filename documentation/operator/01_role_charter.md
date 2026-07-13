# 01 · Carta del Rol — El Operador de Kern

> Lo que **eres**, lo que se **espera de ti**, y cómo se mide tu éxito.

---

## 🎯 Misión

**Convertir la producción de Kern en decisiones ejecutadas y entregables
cobrados.** Kern calcula la decisión, la fundamenta en la literatura (L3) y
prepara el siguiente paso seguro. Tu misión es **cerrar el lazo**: validar,
decidir lo que solo un humano puede decidir, ejecutar lo irreversible, y poner el
entregable frente al cliente con tu nombre y tu juicio detrás.

No eres un analista que produce números desde cero — Kern hace eso en
segundos y con 600+ tests detrás. Eres el **dueño del juicio, la relación y la
responsabilidad**: las tres cosas que no se pueden automatizar.

---

## 🧭 Tu posición en el sistema

```
   Cliente / Brief
        │
        ▼
   ┌─────────────┐      calcula · fundamenta · QA · prepara
   │  LINCHPIN   │  ──────────────────────────────────────►  Entregable + siguiente paso seguro
   └─────────────┘                                                      │
        ▲                                                                ▼
        │                                              ┌───────────────────────────────┐
        └──────────────  TÚ (el operador)  ◄───────────│ OPTIONS · HANDOFF · ESCALATED │
                 decide · ejecuta · enruta · vende      └───────────────────────────────┘
```

Operas Kern en uno de dos **modos** (ver [03 · Metodologías](03_methodologies.md)),
y adoptas la **persona** correspondiente frente al cliente:

- **Modo Inventory** — *Especialista en Inventario / Inventario E-commerce.*
  Dueño del stock de la marca: niveles, puntos de reorden, stock de seguridad,
  ABC-XYZ, reconciliación / conteos cíclicos, reportes de inventario.
- **Modo SCM** — *Supply Chain Manager / Consultor.* Dueño del flujo extremo a
  extremo: planeación de demanda y oferta (S&OP), sourcing y desempeño de
  proveedores, procurement y *landed cost*, logística, estrategia de inventario,
  *cost-to-serve*, riesgo y resiliencia, sostenibilidad, liderazgo.

---

## 📋 Responsabilidades (lo que se espera de ti)

### 1. Custodia del lazo "nunca desprotegido"
Toda salida de Kern con desenlace `OPTIONS`, `HANDOFF` o `ESCALATED` es una
tarea **abierta a tu nombre**. Se espera que la cierres dentro de su plazo
(`deadline` / `sla`) o la reasignes explícitamente. Ningún desenlace consecuente
debe quedar sin dueño.

### 2. Aprobación de cambios irreversibles (*writeback*)
Kern **nunca** muta un sistema de registro a ciegas. Calcula el cambio como
un *changeset* en seco (*dry-run*) y lo clasifica por **risk tier**. Los cambios
**irreversibles** (p. ej. emitir una PO, enviar a un proveedor) **siempre**
requieren tu aprobación explícita, con un *time-box* de 15 minutos. Tú eres el
punto humano de control sobre el dinero y los compromisos.

### 3. Ejecución de pasos humano-únicos (*handoffs*)
Llamar a un proveedor, firmar un conteo físico, negociar una disputa, enviar el
email que Kern ya redactó. El agente te entrega el artefacto **prellenado**;
tú lo ejecutas en el mundo real y registras el resultado.

### 4. Enrutamiento de escalaciones
Disputas, exposición legal, gastos sobre el umbral. Kern arma el paquete con
contexto, opciones, recomendación, citas y SLA, y propone la ruta. Tú confirmas
que llegue a la persona correcta y das seguimiento hasta su cierre.

### 5. Revisión de QA antes de enviar
Kern aplica "**si falla QA, no se entrega**". Pero **tú** eres la última
compuerta de sentido común y contexto: ¿los supuestos aplican a *este* cliente?
¿el dato de entrada estaba limpio? ¿la narrativa suena al cliente? Nunca envías
algo que no entiendes o no defenderías.

### 6. Relación y venta
El humano vende. Calificar el *gig*, enmarcar el problema, presentar el
entregable, defender la recomendación y cobrar. Kern es tu fábrica; el
cliente te contrata **a ti**.

---

## 📊 KPIs que **tú** posees

Kern calcula los KPIs *del cliente* (IRA, fill rate, OTIF, CCC, …). Estos
otros miden **tu desempeño como operador**:

| KPI del operador | Qué mide | Meta saludable |
|---|---|---|
| **Tiempo de cierre de desenlace** | Horas entre que Kern emite un `HANDOFF`/`ESCALATED` y tú lo cierras | Dentro del `deadline`/`sla` del paquete |
| **Cobertura de residuos** | % de `Residual` con dueño y acción registrada (vs. ignorados) | 100% |
| **Tasa de aprobación informada** | % de *writebacks* irreversibles aprobados con revisión documentada (no a ciegas) | 100% |
| **Rechazo en QA propio** | % de entregables que **tú** devuelves antes de enviar al cliente | >0% (si es 0%, no estás revisando) |
| **Aceptación del cliente** | % de entregables aceptados sin re-trabajo mayor | ≥ 90% |
| **Trazabilidad** | % de decisiones consecuentes con su evidencia archivada (changeset / audit entry / paquete) | 100% |

---

## ✅ Cómo se ve el éxito

- **Ningún callejón sin salida humano.** Cada opción, *handoff* y escalación que
  Kern produce queda cerrada, enrutada o convertida en una decisión
  registrada — a tiempo.
- **Cero cambios irreversibles a ciegas.** Toda PO, todo compromiso de gasto pasa
  por tu revisión documentada dentro de su ventana de aprobación.
- **Entregables que el cliente firma.** Lo que envías está revisado, contextualizado
  y lo puedes defender, con sus *Fuentes* (L3) a la mano.
- **Una bitácora auditable.** Cualquiera puede reconstruir *qué se decidió, quién
  lo aprobó y por qué* a partir de los *changesets*, *audit entries* y paquetes.

---

## 🚫 Lo que **no** es tu trabajo

- Recalcular a mano lo que el motor ya calculó y validó contra simulación.
- Aprobar *writebacks* sin leer el *changeset*.
- Enviar un entregable que no entiendes "porque el agente lo generó".
- Dejar una escalación esperando sin ruta ni dueño.

> El siguiente documento, [02 · División del Trabajo](02_division_of_labor.md),
> traza la línea exacta entre lo que hace Kern y lo que te toca a ti, con los
> mecanismos de código (desenlaces, residuos, *risk tiers*) que la sostienen.
