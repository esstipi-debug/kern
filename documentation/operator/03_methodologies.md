# 03 · Metodologías — Los Métodos que Debes Dominar

> El *cómo*. No necesitas derivar los modelos (Kern lo hace y los valida),
> pero sí necesitas **operar las metodologías** que enmarcan, validan y entregan
> ese trabajo. Esto es lo que un cliente espera que sepas.

---

## 1. 🎭 Trabajar en modo y persona

Kern opera en uno de dos **modos** (`scm_agent/modes.py`), cada uno con una
persona, un catálogo de entregables y un set de KPIs. **Eliges el modo según el
encargo** y adoptas su persona frente al cliente.

### Modo Inventory — *Especialista en Inventario E-commerce*
> "Dueño del stock propio de la marca: niveles, puntos de reorden, stock de
> seguridad, ABC-XYZ, reconciliación/conteos cíclicos, reporte de inventario — no
> sourcing, ni logística, ni estrategia de red."

Úsalo cuando el cliente vende online y el problema es **su propio inventario**.
Alcance acotado: subset de herramientas de stock.

### Modo SCM — *Supply Chain Manager / Consultor*
> "Dueño del flujo extremo a extremo: planeación de demanda y oferta (S&OP),
> sourcing y desempeño de proveedores, procurement y *landed cost*, logística,
> estrategia de inventario, *cost-to-serve*, riesgo y resiliencia, sostenibilidad
> y liderazgo de cadena de suministro."

Úsalo para diagnósticos y consultoría de cadena completa. Superset: todas las
herramientas, incluyendo las del modo Inventory.

**Regla práctica:** si dudas, empieza en el modo más acotado que cubra el problema.
Subir de Inventory a SCM es ampliar alcance (y honorarios); bajar es difícil sin
parecer que prometiste de más.

---

## 2. 🔄 Cadencia S&OP / IBP (el ritmo mensual)

El modo SCM ofrece el "**S&OP/IBP deck + cadencia mensual**". La metodología
(implementada en `src/sop.py`, `run_sop_cycle`) es un ciclo mensual que **tú
facilitas**:

```
demanda  →  oferta  →  reconciliación  →  revisión ejecutiva
(plan)     (capacidad)  (balance/gaps)     (decisión + opciones rankeadas)
```

- Kern proyecta el balance de inventario bajo estrategias agregadas
  **chase / level / hybrid**, evalúa costo / servicio / capital de trabajo, y
  emite un desenlace `OPTIONS` protegido.
- **Tu rol humano en la cadencia:**
  1. **Convocar** a los dueños de demanda, oferta y finanzas.
  2. **Facilitar** la conversación sobre los *gaps* que Kern cuantificó.
  3. **Forzar la decisión ejecutiva** sobre las opciones rankeadas (nadie sale de
     la junta sin elegir).
  4. **Registrar** la decisión y disparar los *handoffs* que de ella deriven.

S&OP sin un humano que fuerce la decisión es solo un reporte. Ese humano eres tú.

---

## 3. 🧮 Leer y defender un entregable (las "Fuentes" / L3)

Cada entregable de Kern viene **fundamentado**: cita el capítulo del libro **y**
la función `src/` detrás de cada número (la sección *Fuentes* en la consola). Esto
es tu **munición para defender la recomendación** ante el cliente.

**Metodología de revisión antes de enviar** (tu compuerta de QA):

1. **Supuestos** — ¿los supuestos declarados aplican a *este* cliente? (p. ej.
   *lead time* fijo vs. estocástico, demanda normal vs. intermitente).
2. **Dato de entrada** — ¿el CSV venía limpio? ¿unidades, fechas, SKUs canónicos?
   Kern marca calidad de dato, pero el contexto es tuyo.
3. **σ_e, no σ_demanda** — el stock de seguridad se calcula sobre el **error de
   pronóstico**, no sobre la dispersión cruda de la demanda (el error #1 del
   gremio). Si un cliente cuestiona tus buffers, esta es la respuesta.
4. **Factibilidad** — ¿el plan respeta MOQ, *case packs*, *shelf-life* y
   presupuesto? Kern lo construye factible; confírmalo contra la realidad
   operativa del cliente.
5. **Narrativa** — ¿suena a la persona del modo? ¿el resumen ejecutivo dice algo
   que el cliente pueda accionar?

> Nunca envíes un número que no puedas explicar con su *Fuente*. Si no la
> entiendes, está el documento [06 · Competencias](06_competency_map.md) y la
> consulta directa: `python examples/query_knowledge.py --explain <concepto>`.

---

## 4. 🤝 Metodología de compromiso con el cliente (el ciclo de venta)

"El humano vende, Kern produce 10x." Tu método de *gig*:

| Fase | Tú | Kern |
|---|---|---|
| **Calificar** | Entiendes el problema real, el dato disponible y el presupuesto | — |
| **Enmarcar** | Eliges modo + entregable del catálogo (doc 05) | Te dice qué necesita de entrada (`needs_data`/`needs_clarification`) |
| **Producir** | Cargas el dato, corres el job | Clasifica → ejecuta → QA → entrega |
| **Revisar** | Aplicas tu compuerta de QA (sección 3) | Adjunta *Fuentes* y bloque de cobertura |
| **Presentar** | Cuentas la historia, defiendes la recomendación | — |
| **Ejecutar** | Cierras *handoffs*/escalaciones, apruebas *writebacks* | Prepara cada paquete |
| **Cobrar y reactivar** | Facturas, propones la cadencia recurrente (S&OP, QBR) | Re-produce cada ciclo |

**Estados que verás del agente** y qué significan para ti:
`ok` (listo para revisar) · `needs_clarification` (falta enmarcar) ·
`needs_data` (falta dato) · `qa_failed` (no se entrega — investiga el dato/supuesto) ·
`error` (falla técnica). Solo `ok` llega a tu compuerta de revisión.

---

## 5. ⚖️ Metodología de decisión sobre opciones (`OPTIONS`)

Cuando Kern te da opciones rankeadas:

1. Hay una `recommended` con el mejor `score` — es tu **default informado**, no tu
   obligación.
2. Lee los `tradeoffs` de cada una: el ranking optimiza una función objetivo
   (costo, servicio, capital). El **contexto de negocio** que el modelo no ve
   (una promo, una restricción de proveedor, una directriz del CEO) es tuyo.
3. Elige, documenta **por qué** (sobre todo si te apartas de la recomendada), y
   dispara la `action` de la opción elegida.

> Apartarte de la recomendación es legítimo y a veces correcto — pero **siempre
> documentado**. Esa nota es lo que te protege si la decisión se cuestiona después.

---

## 6. 🔐 Metodología de aprobación de *writeback* (lo irreversible)

Antes de aprobar un `Changeset` (ver mecánica en [02](02_division_of_labor.md)):

1. **Lee el `summary`**: *"N cambio(s) a `target` [`risk_tier`] key=`...`"*.
2. **Confirma el `risk_tier`**: si es `irreversible`, no hay deshacer limpio —
   trátalo como definitivo.
3. **Lee la `reason`**: ¿el cambio se sigue de la decisión que tomaste?
4. **Verifica el alcance**: ¿la lista de cambios es exactamente lo que esperas?
   ¿algún campo de más?
5. **Aprueba dentro de la ventana** (15 min). Si te interrumpen y expira, vuelve a
   revisar y re-aprueba — la expiración es a propósito.
6. **Archiva el `AuditEntry`**: tu evidencia de qué se aplicó y quién lo aprobó.

---

## 📐 Resumen: las seis metodologías

1. **Modo y persona** — elige el alcance correcto y habla como el rol.
2. **Cadencia S&OP** — facilita el ritmo mensual y fuerza la decisión.
3. **Lectura de Fuentes (L3)** — defiende cada número con su cita.
4. **Compromiso con el cliente** — el ciclo de venta de extremo a extremo.
5. **Decisión sobre opciones** — elige informado, documenta la desviación.
6. **Aprobación de writeback** — revisa antes de autorizar lo irreversible.

> El siguiente documento, [04 · Runbooks](04_runbooks.md), convierte estas
> metodologías en pasos concretos para cada situación que enfrentarás.
