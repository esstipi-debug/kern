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
- [RB-9 · Correr un ciclo bundlado (varias tools de una)](#rb-9--correr-un-ciclo-bundlado-varias-tools-de-una)
- [RB-10 · Escanear qué se puede correr con estos datos](#rb-10--escanear-qué-se-puede-correr-con-estos-datos)
- [RB-11 · Mapear la tensión entre áreas y reconciliar una decisión de reposición](#rb-11--mapear-la-tensión-entre-áreas-y-reconciliar-una-decisión-de-reposición)
- [RB-12 · Medir el costo de coordinación antes de convocar una reunión](#rb-12--medir-el-costo-de-coordinación-antes-de-convocar-una-reunión)
- [RB-13 · Auditar la fricción organizacional en tus propios controles](#rb-13--auditar-la-fricción-organizacional-en-tus-propios-controles)
- [RB-14 · Verificar el gate de disponibilidad antes de un lanzamiento](#rb-14--verificar-el-gate-de-disponibilidad-antes-de-un-lanzamiento)
- [RB-15 · Medir el servicio que realmente entregaste](#rb-15--medir-el-servicio-que-realmente-entregaste)
- [RB-16 · Auditar el registro de procedimientos (SOP)](#rb-16--auditar-el-registro-de-procedimientos-sop)
- [RB-17 · Redactar un SOP con control documental](#rb-17--redactar-un-sop-con-control-documental)
- [RB-18 · Detectar normas ISO de operaciones y evaluar la evidencia de cadena](#rb-18--detectar-normas-iso-de-operaciones-y-evaluar-la-evidencia-de-cadena)
- [RB-19 · Preparar el papel de trabajo MUS / conciliación GL](#rb-19--preparar-el-papel-de-trabajo-mus--conciliación-gl)
- [RB-20 · Emitir el pack de evidencia SCOR/ISO](#rb-20--emitir-el-pack-de-evidencia-scoriso)

---

## RB-1 · Onboarding de un cliente / dataset

**Objetivo:** pasar de "cliente nuevo" a "dato listo para correr un job".

> Si todavía no sabés qué archivos pedir ni qué tools van a correr con lo que
> tenés, empezá por [RB-10](#rb-10--escanear-qué-se-puede-correr-con-estos-datos)
> — ese escaneo te devuelve exactamente esa lista antes de prometer nada.

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
6. **Deja que Kern aplique** (`apply`, idempotente) y **archiva el `AuditEntry`**.
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

1. **Corre el ciclo**: `examples/run_sop_cycle.py` (o el tool `sop`). Kern
   proyecta el balance bajo *chase/level/hybrid* y emite `OPTIONS`.
2. **Convoca** a dueños de demanda, oferta y finanzas a la revisión ejecutiva.
3. **Presenta los gaps** que Kern cuantificó (costo / servicio / capital de
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

## RB-9 · Correr un ciclo bundlado (varias tools de una)

**Disparador:** te toca un ciclo recurrente que junta varias tools en un solo
entregable consolidado (por ejemplo, uno de los [8 bundles del catálogo
comercial](../paquetes/README.md), si operás Kern como agencia — pero el
mecanismo sirve igual para correr tu propio ciclo interno mensual sin bundle
comercial de por medio).

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
6. **Presenta el deck y fija la cadencia del próximo ciclo.** Los pasos con
   cadencia "QBR trimestral" (proveedores, riesgos, DEA) se corren igual que el
   ciclo mensual, agregando sus archivos al intake ese trimestre. Si operás
   Kern como agencia, esta es también la instancia en la que facturás el ciclo
   (ver [07 · Setup de Venta](07_setup_venta.md)); si operás tu propia cadena,
   es la instancia en la que reportás a quien decide.

---

## RB-10 · Escanear qué se puede correr con estos datos

**Disparador:** te llegó una carpeta de archivos (propios o de un dataset
nuevo) y no sabés todavía qué tools de Kern van a poder correr con lo que hay
adentro, ni qué falta para desbloquear el resto.

**Objetivo:** convertir "no sé qué tengo" en una lista accionable: qué corre
hoy, qué falta, y cuál es el único archivo o columna que más rápido destraba
más cosas.

1. **Apuntá la tool a la carpeta, no a un archivo suelto**: `intake_scan` toma
   `request.data_path` como directorio y clasifica cada archivo legible contra
   los perfiles conocidos (`maestro`, `ventas`, `stock`, `finanzas`).
2. **Corré el escaneo** (`examples/run_agent.py --brief "que puedo correr con
   esta carpeta" --data <carpeta>/`, o el intent en español "escanear la
   carpeta" / "qué datos necesito").
3. **Leé el semáforo por tool**: verde (corre hoy), amarillo (corre con
   huecos — el reporte dice cuáles), rojo (falta el archivo base), o
   no-declarada (esa tool todavía no expone su perfil de intake).
4. **Priorizá por el campo de mayor apalancamiento**, no por orden alfabético.
   El reporte ya rankea "la única columna o archivo que más tools desbloquea" —
   pedí ese primero, no la lista completa de una.
5. **Volvé a RB-1** con la lista en mano: ahora el paso "pide el dato mínimo"
   tiene respuesta concreta en vez de una plantilla genérica.

---

## RB-11 · Mapear la tensión entre áreas y reconciliar una decisión de reposición

**Disparador:** una decisión de orden de compra (cantidad + nivel de
servicio) genera desacuerdo visible entre compras, finanzas, planeación y
comercial, y necesitás una sola cifra para avanzar — no cuatro opiniones.

**Objetivo:** dejar explícito el costo en dólares de cada postura, elegir con
una política de pesos declarada, y dejar un acta de las concesiones.

1. **Corré `hat_tension`** sobre la demanda semanal del SKU en disputa
   (`product_id, quantity, unit_cost, lead_time_days`). Devuelve la misma
   decisión vista a través de 4 "sombreros" (comprador / planner / cfo /
   comercial), con el gap en $ entre posturas — **nada se reconcilia todavía**,
   solo se hace visible el desacuerdo.
2. **Llevá el mapa a la conversación** con las áreas en tensión — esto
   reemplaza la reunión de "cada uno defiende su número" por una tabla que ya
   las tiene todas puestas una al lado de la otra.
3. **Corré `hat_settlement`** con tu política de pesos explícita (cuánto pesa
   cada sombrero) para obtener UN plan reconciliado (Q*, SL*), con el acta de
   concesiones y el valor firmado vs. la línea base de hoy.
4. **`hat_settlement` termina en HANDOFF, nunca en EXECUTED** — el resultado es
   un paquete humano para vos: pasa a **RB-3** para ejecutarlo (emitir la
   orden, comunicar la decisión).
5. **Archivá el acta.** Es la prueba de que la decisión no fue arbitraria
   cuando alguien pregunte, tres meses después, por qué se pidió esa cantidad.

---

## RB-12 · Medir el costo de coordinación antes de convocar una reunión

**Disparador:** una de las áreas YA propuso una decisión de reposición y
estás por convocar a las otras tres a validarla — antes de agendar, querés
saber si hace falta la reunión.

**Objetivo:** no convocar cuando nadie va a objetar, y llegar con el
argumento cuantificado cuando alguien sí va a hacerlo.

1. **Corré `cross_impact`** con la decisión ya propuesta por el área de
   origen (`current`, el parámetro que vos completás — la tool nunca lo
   asume por defecto) sobre la misma demanda semanal.
2. **Leé el veredicto por área**: para cada una de las otras tres, ¿objetaría
   contra el umbral que vos declaraste, o no?
3. **Cero objeciones → no convocás la reunión.** Es el resultado explícito de
   esta tool, no una lectura tuya del reporte.
4. **Alguna objeción → convocás con el número en la mano**, no con "creo que a
   finanzas no le va a gustar". El reporte trae el $ y el motivo por área.
5. **Si el resultado empuja hacia una decisión reconciliada**, pasá a
   **RB-11** para llegar a UN plan en vez de quedarte en el diagnóstico.

---

## RB-13 · Auditar la fricción organizacional en tus propios controles

**Disparador:** cadencia trimestral, o cuando dos reportes del mismo período
no coinciden entre sí y sospechás que el problema no es el dato sino cómo se
gobierna.

**Objetivo:** sacar a la luz la capa política que ninguna herramienta de
calidad de datos ve — números que no cierran entre canales, metas que suben
apenas se cumplen, puntos de control sin dueño, workarounds informales nunca
clasificados.

1. **Juntá tus propios puntos de control** en un CSV/Excel: una fila por
   KPI o checkpoint, con las columnas que ya tengas de
   `official_value`, `shadow_value`, `owner`, `workaround_present` y
   `workaround_classification` (ninguna es obligatoria de arranque — la tool
   trabaja con las que existan). Un segundo archivo opcional de histórico
   target-vs-actual (`params['history_path']`) habilita la detección de metas
   que se ajustan ("ratchet") apenas se cumplen.
2. **Corré `org_friction_audit`.**
3. **Leé cada hallazgo como una pregunta rankeada, nunca como un veredicto.**
   La tool está diseñada para NO acusar a una persona — si el reporte suena a
   señalar a alguien, la pregunta a hacerte es qué tan bien se completaron las
   columnas de `owner`, no a quién citar.
4. **Llevá los hallazgos de mayor rango a la conversación 1:1 con el dueño del
   control**, no a un canal grupal — es una auditoría de gobierno, no una
   denuncia pública.
5. **Los controles sin dueño se convierten en un residuo** (RB-8): decidís
   quién los asume o los asumís conscientemente vos.

---

## RB-14 · Verificar el gate de disponibilidad antes de un lanzamiento

**Disparador:** marketing (o quien programe campañas) tiene una fecha de
lanzamiento fija y necesitás confirmar, con los tiempos de proveedor reales,
si el SKU va a estar en stock a tiempo.

**Objetivo:** un veredicto verde/amarillo/rojo por SKU, con la acción
concreta para cada uno que no llega a verde.

1. **Juntá dos archivos**: el calendario de campaña
   (`product_id, launch_date`) y el inventario/lead time real
   (`params['inventory_path']`) — sin el segundo, la tool no tiene con qué
   comparar el calendario y devuelve `needs_data`.
2. **Corré `launch_readiness`.**
3. **Leé el veredicto por SKU**: verde (cubierto), amarillo (ajustado, con la
   acción que lo destraba — expedite, PO adelantada), rojo (no va a llegar con
   el lead time actual).
4. **Reenviá el reporte a quien programa el calendario** — la tool está
   pensada para eso, no para integrarse sola al calendario de marketing
   ("no marketing-tool integration" es explícito en su diseño).
5. **Cada SKU en rojo o amarillo es una decisión** (adelantar PO, correr
   `sourcing` con un proveedor más rápido, o mover la fecha) — pasa a **RB-4**
   si `launch_readiness` ya te dejó opciones rankeadas, o a **RB-5** si la
   acción es emitir una orden.

---

## RB-15 · Medir el servicio que realmente entregaste

**Disparador:** necesitás fill rate y OTIF reales — no el nivel de servicio
que asume tu política de inventario — para un reporte, una revisión semanal,
o para confirmar si un transportista está cumpliendo.

**Objetivo:** un número medido, con la fuente y el hueco de medición
declarados — nunca un OTIF presentado sin decir de qué campo salió.

1. **Corré `service_scan`** (no pide archivo: lee una cuenta conectada —
   Unleashed o Cin7 Core por credenciales de entorno, con el stand-in offline
   como último recurso si no hay ninguna configurada; Shopify no está cableado
   a esta tool todavía, aunque el conector existe en el repo). Parámetros
   opcionales: `since` (ventana) y `stockout_days`.
2. **Leé fill rate y OTIF por separado** — pueden divergir, y el reporte los
   separa a propósito cuando lo hacen.
3. **Leé las notas de cobertura antes de citar el número.** El reporte declara,
   campo por campo, si algo es medido, derivado o no medible — y si tenés un
   transportista conectado (hoy, Starshipit es el único auto-resoluble por
   credenciales), si el OTIF es de **despacho** o de **llegada**. La brecha
   entre ambos, medida sobre 98.666 pedidos reales, es de 6,2 puntos — no es
   un detalle menor.
4. **Nunca repitas el número sin la nota de cobertura al lado** — es la
   diferencia entre un OTIF defendible y uno que un cliente o tu propio CFO
   puede desarmar con una pregunta.
5. **Si el número sorprende, cruzalo con `disruption_scan` o `reconciliation`**
   antes de escalarlo — un servicio que cae de golpe suele tener una causa
   rastreable, no solo un número que reportar.

---

## RB-16 · Auditar el registro de procedimientos (SOP)

**Disparador:** cadencia trimestral, cuando un cliente o una auditoría ISO
9001 piden evidencia de que el registro de SOPs está al día, o cuando
sospechás que un procedimiento vencido sigue guiando el trabajo diario sin
que nadie lo haya confirmado.

**Objetivo:** un veredicto por procedimiento — vencido, sin dueño, sin
segregación de funciones, sin KPI vinculado, sin versión — con un HANDOFF
que ya trae dueño y fecha límite por cada uno, nunca una planilla entera que
alguien tiene que releer.

1. **Exportá el registro** desde Notion/SweetProcess/Drive (o el sistema que
   use el cliente) con columnas mínimas: `procedimiento` (o `name`),
   `ultima_revision` (o `last_reviewed_at`); idealmente también
   `responsable`, `aprobador`, `version`, `estado`, `cadencia_dias`.
2. **Corré con `--as-of` fijo**:
   `examples/run_procedure_audit.py <registro.csv> --as-of AAAA-MM-DD`.
   Nunca dejes que el reloj del servidor decida la fecha de un acta — sin un
   `as_of` fijo, dos corridas en días distintos pueden marcar el mismo
   procedimiento como vencido o vigente según cuándo se corrió.
3. **Leé el HANDOFF** → **RB-3** por cada procedimiento vencido — el dueño y
   la fecha límite ya vienen resueltos en cada paso del paquete, no hace
   falta ir a buscarlos al CSV.
4. **Un hallazgo `NO_OWNER` no es un residuo aparte**: el mismo HANDOFF que
   leíste en el paso 3 trae un segundo `HandoffPacket`, titulado
   `Asignar dueno`, con un paso por cada procedimiento sin dueño — no hay
   que buscarlo entre los residuales. Aplicale la misma disciplina de
   **RB-8** a cada uno: cubrilo vos, delegalo, o asumí conscientemente que
   el procedimiento queda sin dueño.
5. **Un hallazgo `OWNER_IS_APPROVER`** significa que quien redacta también
   aprueba — nombrá un segundo firmante distinto antes de la próxima
   aprobación; la segregación de funciones es lo que le da valor de control
   a la firma.
6. **Leé las Fuentes L3 del deck con cautela**: el camino de una sola tool
   (`examples/run_agent.py`, o `POST /api/jobs` para una tool suelta) no
   filtra las citas por la compuerta de citas — a diferencia del camino de
   paquetes — así que esta tool puede traer ruido léxico en sus Fuentes. No
   las repitas frente al cliente sin revisarlas primero.
7. **Si el procedimiento vencido es el que gobierna el ciclo S&OP**, cruzalo
   con **RB-7** — un SOP de S&OP vencido puede estar sosteniendo una
   cadencia mensual con reglas que ya nadie confirmó.

> Kern audita el registro; no lo guarda. El sistema del cliente (Notion,
> SweetProcess, Drive, o lo que use) sigue siendo el sistema de registro del
> SOP — esta tool nunca lo reemplaza, solo lo audita.

---

## RB-17 · Redactar un SOP con control documental

**Disparador:** un procedimiento no tiene una versión escrita, o la que
existe no trae dueño, aprobador ni cadencia de revisión — necesitás partir
de un borrador estructurado (Hoja de Desglose de Trabajo + plan de control +
cronograma de entrenamiento) en vez de un documento en blanco.

**Objetivo:** un borrador listo para revisar y aprobar — nunca un documento
que se hace pasar por aprobado — con cada paso, punto clave y razón ya
redactados, y un HANDOFF que deja claro quién tiene que firmarlo y cuándo.

1. **Elegí `process`**: `replenishment`, `cycle_count` o `sop_cycle` — los
   tres templates disponibles hoy.
2. **Pasá `--owner` y `--approver` distintos**: la aprobación pierde su
   valor de control si la misma persona redacta y aprueba (la tool lo
   reporta como residuo si no lo hacés, nunca lo bloquea en silencio).
3. **Fijá la cadencia de revisión** (`--cadence-days`) — 365 días es la
   convención que usa esta tool (GDI, SweetProcess) cuando no le das una, no
   una norma: ISO 9001 no fija un intervalo. Confirmala por área de proceso
   antes de adoptarla como política.
4. **Corré**
   `examples/run_procedure_draft.py --process ... --owner ... --approver ... --as-of ...`
   (agregá `--lang en` para inglés, `--location` para dónde se va a publicar
   el borrador aprobado).
5. **Revisá el borrador por adecuación y suficiencia** (ISO 9001:2015
   §7.5.2c) — no solo que esté completo, sino que describa cómo se trabaja
   de verdad hoy.
6. **Aprobá y publicá** el borrador en el sistema del cliente, registrando
   `approved_at` y `version` ahí mismo → **RB-3** (el HANDOFF que trae esta
   tool es exactamente esos pasos, ya redactados).
7. **Volvé a RB-16** con el registro actualizado — el SOP recién aprobado
   ahora tiene una fecha de última revisión real para la próxima auditoría.

> Kern redacta el borrador; no lo guarda ni lo aprueba. El sistema del
> cliente es —y sigue siendo— el sistema de registro del SOP aprobado.

---

## RB-18 · Detectar normas ISO de operaciones y evaluar la evidencia de cadena

**Objetivo:** saber qué normas del paquete `iso-operaciones-cadena` aplican a
esta planta, qué tareas de piso ya están en el catálogo, y qué evidencia de
orden existe. **No** obtener ni afirmar un certificado.

1. **Junta la ficha** — sector, lo que pide el cliente (IATF, PCF, OTIF…),
   logística (3PL, RTI, export), planta (muestreo, maestro SKU, BCP) y módulos
   KERN (SMARTPRO, MONITOR, ENERGY, huella). Una ficha vacía produce **cero**
   normas: eso es correcto, no un bug.
2. **Si hay una orden abierta**, anotá si hay ciclo, scrap, kg y t·km. Sin
   t·km, 14083 queda en hueco; OEE no lo sustituye.
3. **Corré** `examples/run_iso_ops.py tests/fixtures/iso/planta.csv` (o el JSON
   de `POST /api/detectar`). También: brief "detectar normas iso de
   operaciones…" por el agente (`iso_ops`).
4. **Leé las tarjetas**: `claimPermitido` + `limite` + huecos. El claim
   permitido es "KERN tiene evidencia para…" (solo en las 6 normas de cadena
   marcadas `nativo`), "…aplica; la evidencia de KERN es parcial" o "14083
   aplica; falta t·km". Nunca "la planta cumple ISO…", y nunca "evidencia de
   piso": KERN no lee sensores de maquina.
5. **Elegí sobre OPTIONS** (RB-4) la norma a trabajar primero, o atendé el
   HANDOFF si la ficha estaba vacía (RB-3).
6. **Odoo y Excel no pintan un sello ISO.** El entregable es CSV/Excel de
   detectadas + tareas. La política de inventario sigue en esos sistemas.

> Kern detecta, propone y evalúa la evidencia de cadena que él mismo produce.
> Las magnitudes de planta (kWh, ciclo, scrap, molde) quedan fuera de alcance.
> El organismo certifica.

---

## RB-19 · Preparar el papel de trabajo MUS / conciliación GL

**Objetivo:** entregar un workpaper re-ejecutable (W-1..W-6 + `evidence.json`)
para que el auditor de registro haga fieldwork y firme. **No** un dictamen.

1. **Junta el listing** con `unit_id` y `book_value`. Si hay libro mayor y
   subledger, etiquetá ambas caras con `source=gl` / `source=subledger` en la
   misma tabla (MCP no acepta un segundo archivo).
2. **Pedí TM al auditor** (`tolerable_misstatement`). Kern no lo inventa.
3. **Corré** `examples/run_audit_evidence.py <listing.csv> --tm N` o el brief
   "prepare a monetary unit sampling workpaper…" (`audit_evidence`). MCP:
   `kern_audit_evidence` con `params.tolerable_misstatement`.
4. **Leé el HANDOFF**: testear selecciones W-3, refrescar UML, firmar
   Reviewed-by. Prepared-by queda como identidad del motor.
5. **Si sale ESCALATED** (UML > TM o tie-out fuera de tolerancia) → **RB-6**.
6. **No archivar esto como evidencia firmada.** El expediente del estudio (DMS)
   es el sistema de registro; Kern no lleva un ledger append-only de evidencia
   (Q7).

---

## RB-20 · Emitir el pack de evidencia SCOR/ISO

**Objetivo:** un scorecard SCOR + checklist ISO 9001/28000 envuelto en un
`EvidenceRecord` y hoja Lineage, para el auditor del organismo. **No** un
certificado.

1. **Armá un CSV con columna `dataset`**: `financial`, `cost_to_serve`,
   `sourcing`, `orders`, `procedures`, `risk`. Un panel reconocido alcanza;
   los ausentes salen `no_evaluable`, nunca "cumple por silencio".
2. **Corré** `examples/run_scor_audit.py <pack.csv> --as-of AAAA-MM-DD`
   (pasá `--dso` / `--dpo` si querés medir cash-to-cash). MCP:
   `kern_iso_scor_audit`. Brief: "run the scor iso audit pack…".
3. **Leé Scorecard + ISO checklist + Coverage + Lineage.** Statuses legales:
   `observed` / `gap` / `no_evaluable`.
4. **Cerrá gaps** (p.ej. SOPs vencidos → **RB-16**) antes de una visita
   externa. El HANDOFF nombra esos pasos.
5. **Nunca presentes el pack como sello ISO o certificación SCOR.**

> Kern prepara evidencia re-ejecutable. El auditor de registro concluye.

---

> Con estos runbooks cubres el 100% de las situaciones humano-únicas que Kern
> puede producir, sea que operes tu propia cadena o la de un cliente. El
> siguiente documento, [05 · Catálogo de Entregables](05_deliverable_catalog.md),
> lista qué puedes **producir** con cada tool — y, si operás como agencia, qué
> puedes vender — con tu valor agregado humano en cada uno.
