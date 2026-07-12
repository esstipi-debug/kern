# 04 · Runbooks — Playbooks Paso a Paso

> Procedimientos concretos para las situaciones que enfrentarás como operador.
> Cada runbook asume que ya leíste [02 · División del Trabajo](02_division_of_labor.md)
> y [03 · Metodologías](03_methodologies.md).

Índice:
- [RB-1 · Onboarding de un cliente / dataset](#rb-1--onboarding-de-un-cliente--dataset)
- [RB-2 · Producir y revisar un entregable](#rb-2--producir-y-revisar-un-entregable)
- [RB-3 · Atender un HANDOFF](#rb-3--atender-un-handoff)
- [RB-4 · Elegir sobre OPTIONS](#rb-4--elegir-sobre-options)
- [RB-5 · Aprobar un writeback (changeset)](#rb-5--aprobar-un-writeback-changeset)
- [RB-6 · Enrutar una ESCALACIÓN](#rb-6--enrutar-una-escalación)
- [RB-7 · Facilitar un ciclo S&OP](#rb-7--facilitar-un-ciclo-sop)
- [RB-8 · Cubrir un residuo](#rb-8--cubrir-un-residuo)
- [RB-9 · Correr un paquete comercial](#rb-9--correr-un-paquete-comercial)

---

## RB-1 · Onboarding de un cliente / dataset

**Objetivo:** pasar de "cliente nuevo" a "dato listo para correr un job".

1. **Califica el problema** y elige el **modo** (Inventory vs. SCM) y el
   **entregable** del [catálogo (05)](05_deliverable_catalog.md).
2. **Pide el dato mínimo** del entregable. Para inventario, el formato base es:
   `date, product_id, quantity, unit_cost, lead_time_days`
   (ver `data/sample_demand.csv`). Para pricing: precio/cantidad. Para sourcing:
   registros de entrega. Cada job sabe pedir lo suyo.
3. **Levanta los parámetros del cliente, no solo el CSV.** El dato transaccional
   (columna 2) no trae costo de mantener inventario, costo de ordenar, nivel de
   servicio objetivo ni capacidad de bodega — sin esto el agente usa defaults
   genéricos (95% / 25% / $75) iguales para todos los clientes. Pregúntaselos una
   vez y guárdalos con `client_profile.upsert_profile("<nombre del cliente>",
   "<nombre>", holding_rate=..., service_level=..., lead_time_days=...,
   warehouse_capacity=WarehouseCapacity(value=..., unit="m3"))`
   (`src/client_profile.py`) — el nombre se normaliza solo (acentos incluidos:
   "Café Cliente" -> `clients/cafe-cliente/profile.json`) y el orquestador los
   reusa automáticamente en cada corrida futura para ese cliente (nunca
   sobrescriben un override explícito de esa llamada; el `lead_time_days` del
   perfil solo rellena donde el CSV no trae lead time propio). Corre la primera
   vez con `--strict-params` (`examples/run_agent.py`) para que el agente te
   devuelva `needs_clarification` con exactamente lo que falta, en vez de asumir
   un default silenciosamente. *Notas:* (a) `warehouse_capacity` queda guardado
   en el perfil pero el motor **todavía no la aplica** como restricción física —
   es dato de referencia para ti, no un límite que el cálculo respete; (b) en el
   webapp/MCP desplegado los perfiles vienen **desactivados** (el campo "client"
   ahí es solo una etiqueta que escribe el visitante); actívalos en una
   instalación local propia con la variable de entorno `LINCHPIN_CLIENTS_ROOT`.
4. **Maneja datos sensibles con cuidado.** Si el dato trae PII (email, nombre,
   dirección), el análisis es **solo agregado** — nunca leas ni expongas PII en un
   entregable.
5. **Corre una pasada de prueba** con `examples/run_agent.py --brief "..." --data <csv>`.
   Si vuelve `needs_data` o `needs_clarification`, ajusta el dato/brief antes de
   prometer nada al cliente.
6. **Registra** el alcance acordado, el modo, el entregable y la cadencia (única
   vs. recurrente).

---

## RB-2 · Producir y revisar un entregable

**Objetivo:** generar un entregable y pasarlo por **tu** compuerta de QA antes de
que lo vea el cliente.

1. **Corre el job** (CLI `examples/run_agent.py` o `POST /api/jobs` desde la
   consola). Espera `status: ok`. Si es `qa_failed`, **no se entregó nada** —
   investiga el dato o el supuesto, no fuerces el envío.
2. **Abre los entregables**: `report.md` + `.xlsx` (+ chart / 3D según el caso).
3. **Aplica la compuerta de QA propia** (metodología 3 del doc 03):
   - [ ] ¿Los **supuestos** aplican a este cliente?
   - [ ] ¿El **dato de entrada** estaba limpio (unidades, fechas, SKUs)?
   - [ ] ¿El **stock de seguridad** se basa en σ_e (error de pronóstico)?
   - [ ] ¿El plan es **factible** (MOQ, *case packs*, presupuesto)?
   - [ ] ¿La **narrativa** suena a la persona del modo y es accionable?
   - [ ] ¿Puedes **defender cada número** con su *Fuente* (L3)?
4. **Si algo no cuadra**, devuélvelo (ajusta dato/parámetros y re-corre). Registrar
   un rechazo propio aquí es señal de que estás haciendo tu trabajo.
5. **Si pasa**, presenta. Lleva las *Fuentes* a la mano por si el cliente cuestiona.

---

## RB-3 · Atender un HANDOFF

**Disparador:** desenlace `status = handoff`. Llega un `HandoffPacket`.

1. **Lee el `title`** y el `risk_if_skipped` — entiende qué pasa si no lo haces.
2. **Revisa el `deadline`** — programa el trabajo dentro de la ventana.
3. **Abre el `artifact`** — el borrador ya está prellenado (texto de PO, email,
   hoja de conteo, formulario de reclamo). **No lo reescribas desde cero**; revísalo.
4. **Ejecuta los `steps`** en orden, en el mundo real (enviar el email, llamar al
   proveedor, hacer el conteo físico).
5. **Si el handoff implica un cambio en un sistema de registro** (p. ej. emitir la
   PO), pasa a **RB-5** para la aprobación del *writeback*.
6. **Registra el resultado** y marca el handoff como cerrado. Si no pudiste
   ejecutarlo, escala (RB-6) en vez de dejarlo abierto.

---

## RB-4 · Elegir sobre OPTIONS

**Disparador:** desenlace `status = options`. Llega una lista de `ExecutionOption`.

1. **Identifica la `recommended`** (la marcada, o la de mayor `score`). Es tu
   default informado.
2. **Lee los `tradeoffs`** de todas. Pregúntate qué optimiza el ranking (costo /
   servicio / capital) y **qué contexto de negocio no ve el modelo**.
3. **Elige.** Si te apartas de la recomendada, **documenta por qué** (una promo,
   restricción de proveedor, directriz ejecutiva).
4. **Dispara la `action`** de la opción elegida (a menudo es el id de un
   *changeset* en etapa → RB-5).
5. **Archiva** la decisión y su justificación.

---

## RB-5 · Aprobar un writeback (changeset)

**Disparador:** una `action`/handoff requiere aplicar un `Changeset` a un sistema
de registro.

1. **Lee el `summary`**: *"N cambio(s) a `target` [`risk_tier`] key=`...`"*.
2. **Clasifica por `risk_tier`:**
   - `read` → no requiere aprobación. Sigue.
   - `reversible` → requiere aprobación (salvo *auto-apply* explícito). Revisa.
   - `irreversible` → **siempre** tu aprobación. Trátalo como definitivo (no hay
     *undo* limpio).
3. **Lee la `reason`** — ¿se sigue de la decisión que tomaste (RB-4)?
4. **Verifica la lista de cambios** — ¿exactamente lo esperado? ¿ningún campo de más?
5. **Aprueba** (`approve`) — queda ligada a la `idempotency_key` y **expira en 15
   min**. Si expira, re-revisa y re-aprueba.
6. **Deja que Linchpin aplique** (`apply`, idempotente) y **archiva el `AuditEntry`**.
7. **Si algo salió mal**, usa `rollback(idempotency_key)` y documenta.

> 🚫 Nunca apruebes sin leer el changeset. Si falta o expira la aprobación, el
> sistema **rechaza** con `WritebackRefused` — eso es el diseño protegiéndote.

---

## RB-6 · Enrutar una ESCALACIÓN

**Disparador:** desenlace `status = escalated`. Llega un `EscalationPacket`.

1. **Lee el `reason`** y clasifica el disparador:
   `dispute` · `legal` · `financial_threshold` · `operational`.
2. **Confirma la ruta** (`route_to`) y el **`sla`**:
   | Disparador | Ruta por defecto | SLA |
   |---|---|---|
   | `dispute` | claims / account manager | mismo día hábil |
   | `legal` | legal / agente aduanal licenciado | **antes de cualquier acción** |
   | `financial_threshold` | aprobador de finanzas | **antes del compromiso** |
   | `operational` | dueño de operaciones | mismo día hábil |
3. **Para `legal` y `financial_threshold`**: el SLA es **bloqueante** — ninguna
   acción ocurre hasta que el rol responda. No avances por tu cuenta.
4. **Entrega el paquete completo** a `route_to`: `recommendation`, `options`,
   `citations`. No lo resumas de más; el rol necesita el contexto.
5. **Da seguimiento** hasta el cierre. Una escalación sin respuesta sigue siendo
   tu tarea abierta.

---

## RB-7 · Facilitar un ciclo S&OP

**Disparador:** cadencia mensual (modo SCM).

1. **Corre el ciclo**: `examples/run_sop_cycle.py` (o el tool `sop`). Linchpin
   proyecta el balance bajo *chase/level/hybrid* y emite `OPTIONS`.
2. **Convoca** a dueños de demanda, oferta y finanzas a la revisión ejecutiva.
3. **Presenta los gaps** que Linchpin cuantificó (costo / servicio / capital de
   trabajo).
4. **Fuerza la decisión** sobre las opciones rankeadas (RB-4). Nadie sale sin elegir.
5. **Registra** la decisión y **dispara los handoffs** derivados (RB-3) y las
   aprobaciones (RB-5).
6. **Agenda** el siguiente ciclo. La cadencia recurrente es ingreso recurrente.

---

## RB-8 · Cubrir un residuo

**Disparador:** cualquier desenlace puede traer `Residual` (cosas que el agente
**no** hizo).

1. **Lee la `description`** y el `risk_if_skipped` (siempre está; sin él no pasa la
   QA del agente).
2. **Decide:** ¿lo cubres tú, lo delegas, o lo asumes conscientemente?
3. **Si lo asumes** (no lo haces), **documenta la decisión y el riesgo aceptado**.
   Un residuo ignorado en silencio es la única forma en que este sistema falla.
4. **Si lo cubres**, ejecútalo y márcalo cerrado.

---

## RB-9 · Correr un paquete comercial

**Disparador:** un cliente contrató alguna de las [8 secciones de la escalera
comercial](../paquetes/README.md) y toca producir el ciclo.

1. **Pide el intake con el checklist del paquete**:
   `python examples/run_package.py --package <clave> --checklist` imprime
   exactamente qué archivos (y columnas) pedirle al cliente. Junta todo en una
   carpeta (p. ej. `intake/<cliente>/`) con los nombres esperados
   (`ventas.csv`, `maestro.csv`, `planilla.xlsx`, ...).
2. **Releva los parámetros una sola vez** (RB-1 paso 3) — `holding_rate`,
   `service_level`, `lead_time_days` — y guárdalos en el perfil del cliente. La
   primera corrida hazla con `--strict-params` para que falte nada en silencio.
3. **Corre el paquete**:
   `python examples/run_package.py --package <clave> --intake intake/<cliente> --client "<Cliente>"`.
   Estados posibles:
   - `needs_data` → la salida lista exactamente qué archivo requerido falta
     (con sus columnas). Nada se escribió; pídelo y re-corre.
   - `qa_failed` → **un análisis ejecutado no pasó su QA y NO se escribió ningún
     entregable** (la garantía por-tool, elevada al paquete). Investiga el dato,
     no fuerces el envío.
   - `error` en un paso opcional → también bloquea; el escape es quitar ese
     archivo opcional del intake (el paso queda "omitido") y re-correr.
   - `ok` → se escribió `deliverables/paquetes/<clave>/`: el deck consolidado
     (`deliverable.md` + `.xlsx`) en la raíz y una subcarpeta por herramienta.
4. **Revisa el deck consolidado primero** (resumen ejecutivo, tabla de cobertura,
   recomendaciones) y aplica tu compuerta de QA (RB-2). La tabla de cobertura
   dice qué se ejecutó, qué se omitió y por qué — los pasos omitidos son
   conversación de upsell ("mándame los conteos y el próximo ciclo incluye
   exactitud de inventario").
5. **Atiende los desenlaces por herramienta** como siempre: OPTIONS → RB-4,
   planilla/Odoo staged → RB-5, residuos → RB-8.
6. **Presenta y factura la cadencia.** Los pasos con cadencia "QBR trimestral"
   (proveedores, riesgos, DEA) se corren igual que el ciclo mensual, agregando
   sus archivos al intake ese trimestre.

---

> Con estos runbooks cubres el 100% de las situaciones humano-únicas que Linchpin
> puede producir. El siguiente documento, [05 · Catálogo de Entregables](05_deliverable_catalog.md),
> lista qué puedes **producir y vender**, con tu valor agregado en cada uno.
