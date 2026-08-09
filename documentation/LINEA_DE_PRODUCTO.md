# Línea de producto de Kern

> Decidido el 2026-08-09. Este documento fija **qué compra un cliente** y, sobre todo,
> **qué no se le muestra**. Todo cambio al registry, a `scm_agent/package_specs.py` o a
> `documentation/paquetes/` se justifica contra este documento o lo corrige explícitamente.

## 0. El hallazgo que cambia la pregunta

La premisa con la que se encaró esta revisión era *"hay ~50 tools enrutables y nadie decidió
qué compra un cliente"*. **Es falsa.** Se decidió tres veces, y las tres decisiones son
incompatibles entre sí:

| Fuente | Fecha | Qué decide | Estado en código |
|---|---|---|---|
| `documentation/MONETIZATION_BRIEF.md` | jul-2026 (+ addendum 24-jul) | 10 secciones vendibles + 2 peldaños self-serve. Operador fraccional, retainers USD 250–4.500/mes. Meta USD 8.000/mes | **Implementado** — 9 de esas secciones son `PackageSpec` reales |
| `05-GTM-KERN.md` (repo nosilo) | **2026-08-07** | **Un solo precio: USD 290/mes.** Sin tramos, sin negociación, sin onboarding humano, sin personalización. Wedge = inventario. Diagnóstico gratis como vendedor | **No implementado** — no existe un tier de 290 |
| `02-ESTRATEGIA.md` (repo nosilo) | — | Enterprise: wedge source-to-pay documental, plataforma 120k–400k/año, piloto 75k–150k | No implementado (es Nosilo, no Kern) |

El código implementa la **más vieja** de las tres. La más reciente (05-GTM, dos días atrás)
la contradice de frente: su §8 prohíbe literalmente *"segmentación de precio por tramos ·
negociación de precio · cualquier personalización por cliente · onboarding con humano"* —
que es exactamente la forma de la escalera de 9 paquetes que hoy está en
`package_specs.py`.

El problema real de Kern no es un catálogo sin decisión. Es **una decisión vieja
congelada en código y una decisión nueva congelada en prosa**, sin nadie que las reconcilie.

### Un dato más, que ordena el resto

Con hoy cero clientes pagando, la escalera declara **cinco tiers recurrentes**
(Starter, Starter LatAm, Growth, Scale, Retainer) para un operador solo. `02-ESTRATEGIA.md`
§7 tiene la métrica de control que dictamina esto sin ambigüedad:

> `% ingreso por servicios: <30% sano · >40% → eres consultora`

Cinco tiers de retainer con revisión humana **son** ingreso por servicios. La escalera no es
una línea de producto: es una consultora con un motor bueno adentro.

---

## 1. La decisión

**Hay un producto vendible. Uno.** Es el bucle de inventario, y se vende en tres
peldaños del mismo producto, no en cinco productos distintos:

```
ESCANEO GRATIS  →  DIAGNÓSTICO  →  OPERACIÓN MENSUAL
  (el vendedor)     (la prueba)      (el producto)
```

Todo lo demás del catálogo es **capacidad**, no producto. La distinción es la que hace toda
la diferencia y es la que este documento existe para sostener: una capacidad que no está en
un paquete no se borra, no se muestra, y no se le nombra a un cliente.

### Por qué el bucle de inventario y no otra cosa

No es preferencia. Es lo único que cumple las cuatro condiciones a la vez:

1. **Prueba su valor antes de pedir nada** — dos CSV, 30 minutos, sin autorizar a nadie
   (05-GTM §2). El wedge S2P de `02-ESTRATEGIA` necesita acceso al proceso de AP; el de
   auditoría necesita un auditor practicante en la mesa. Ninguno de los dos es autoservible.
2. **Ya está construido y en línea** — `webapp/demo_lead_time_scan.py` y
   `webapp/demo_gap_value.py` corren la salida #1 y su valorización hoy, en producción.
3. **Tiene un motor que el ERP estructuralmente no puede replicar** — σ del lead time
   (05-GTM §5) y pooling multi-echelon. No es una opinión de marketing: es una fórmula que
   el campo estático del ERP no tiene de dónde sacar.
4. **Un operador solo lo sostiene** — es cómputo, no horas.

### Los tres peldaños

| Peldaño | Precio | Comprador | Entregable |
|---|---|---|---|
| **Escaneo** | Gratis | Jefe de operaciones / dueño, cero confianza construida | Plazo real vs. declarado por proveedor, con histograma, y la brecha valorizada en las dos monedas (caja liberable + ahorro recurrente). Web, sin hablar con nadie |
| **Diagnóstico** | USD 1.500–2.500, único | El mismo, ya convencido de que el hecho es real | Sprint de 2 semanas: `data_quality`, `abc_xyz`, `excess_obsolete`, `financial_kpis` — Excel + informe + gráficos, con QA y citas |
| **Operación** | Un solo precio recurrente | El mismo, con presupuesto | El bucle mensual completo: 15 tools, plan de reposición staged y reversible sobre su propia planilla |

**El precio de la Operación queda declarado como decisión abierta del dueño, no mía** —
ver §5. Lo que sí decido acá es que es **uno**, no cinco.

---

## 2. Qué entra en cada paquete

De las **50 tools registradas**, 36 están hoy en algún paquete y **14 no están en ninguno**.
Esa es la medida exacta de "catálogo, no producto".

### Núcleo — la Operación mensual (15 tools)

Son las 15 del Starter actual. No las toco: la reclasificación del 2026-07-18 que bajó 7
tools "universales" de Growth a Starter fue correcta y está bien argumentada en el brief
(son cómputo, no horas-founder).

`data_quality` · `abc_xyz` · `forecast` · `inventory_optimization` · `whatif` ·
`newsvendor` · `excel_replenishment` · `cycle_count` · `pricing` · `excess_obsolete` ·
`financial_kpis` · `reconciliation` · `landed_cost` · `returns` · `risk`

**Un ascenso recomendado, y por qué no se ejecutó todavía:** `sourcing`. Hoy vive en
Growth (QBR trimestral), pero corre `src/supplier_scorecard.py`, que es **el motor de la
salida #1** — la que 05-GTM §5 llama *"si solo pudieras entregar una, entrega esta"*. Que
la salida que gana al cliente en el escaneo gratis luego solo esté disponible dos tiers
más arriba es un contrasentido comercial.

Se midió antes de moverlo y **no se movió**, por un riesgo concreto. El paso tendría que
entrar `required=False`, y el propio docstring de `package_specs.py` documenta que eso no
protege: *"a present-but-malformed file blocks the whole package the same way a missing
required file does, so a soft skip doesn't actually protect this one"* — la lección que
dejó `pricing`. Y `jobs/sourcing_job.py` usa `require_all_criteria=True` en `prepare`, o
sea **rechaza por nombre** cuando faltan columnas. Un cliente Starter que mande un
`proveedores.csv` parcial bloquearía su entregable mensual completo a cambio de un bonus.

Queda como recomendación con precondición: primero hacer que un paso opcional que rechaza
degrade a `skipped` en vez de bloquear el paquete, después mover `sourcing`. Mientras
tanto el cliente **ya ve** esa salida: está en el escaneo gratis, que es donde 05-GTM la
quiere.

### Segundo anillo — solo con complejidad real (21 tools)

Estas **no** son relleno: son correctas, tienen tests y citas. Pero exigen algo que un
cliente mono-almacén no tiene (2+ ubicaciones, flota propia, planta, proyectos activos).
Se ofrecen **cuando el cliente ya tiene el problema**, nunca en un menú de entrada:

`cost_to_serve` · `fefo` · `multi_echelon` · `ddmrp` · `simulation` · `drp` ·
`odoo_replenishment` · `acceptance_sampling` · `learning_curve` · `dea` · `sop` ·
`facility_location` · `transportation` · `warehouse_layout` · `slotting` · `queuing` ·
`scheduling` · `earned_value` · `leadership_chain` · `markdown_liquidation` ·
`supplier_management`

### Proyectos puntuales — la única excepción a "nunca un tool suelto"

Se mantienen los tres del brief, porque cada uno se cierra por caja puntual y no exige
sostener un tier mensual: **Proyecto de Red/Almacén** (6 tools), **Proyecto de Sourcing**
(3 tools) y **Sprint de Liquidación** (4 tools, fee contingente).

---

## 3. Qué queda AFUERA — la parte que da valor a la decisión

### 3.1 Tiers que se retiran del menú (no del código)

| Se retira | Por qué |
|---|---|
| **Starter LatAm** (USD 250–300) | Es un cuarto tier cuyo único diferencial es geográfico. Un solo precio no puede tener una variante regional sin volverse negociación — lo que 05-GTM §8 prohíbe. Su alcance (8 tools) queda como el **límite inferior del núcleo**, no como un SKU |
| **Growth** y **Scale** como tiers de menú | Son el segundo anillo con etiqueta de precio. Vender "26 tools" o "35 tools" es vender catálogo. El cliente que necesita multi-echelon lo necesita por un hecho de su operación, no por estar en un tier |
| **Retainer Ejecutivo** (USD 4.500) | El propio código lo declara: *"Same 35 tools as Scale, zero new capability — the delta is governance"*. Cobrar 1,4× por gobierno y no por capacidad es honorario de consultoría. Es la línea que `02-ESTRATEGIA` §7 marca como frontera de consultora |

**Nada de esto se borra.** Los `PackageSpec` siguen existiendo y siguen corriendo: son la
forma en que el operador arma un alcance para un cliente concreto. Lo que se retira es su
condición de **opción que un comprador frío elige de una lista**.

### 3.2 Las 14 tools registradas que no están en ningún paquete

Medidas, no estimadas:

| Tool | Veredicto |
|---|---|
| `price_intelligence` | **SKU declarado sin paquete.** El brief §9 lo vende a USD 2.000–3.500 y tiene one-pager. Existe la tool, no existe el `PackageSpec`. Es la brecha doc↔código más cara del catálogo |
| `price_watch` | Add-on de monitoreo continuo del anterior. Misma brecha |
| `org_friction_audit` | Ídem: one-pager `auditoria-friccion-operacional.md`, sin `PackageSpec` |
| `intake_scan` | **Infraestructura de onboarding.** Correctamente no vendible. Se queda |
| `cross_impact`, `hat_tension`, `hat_settlement`, `flow_map` | **Capa Nosilo, no Kern.** Miden impacto cross-área y tensión organizacional. Comprador distinto (el que compra `02-ESTRATEGIA`), no el jefe de operaciones. Capacidad interna |
| `digital_twin`, `network_design`, `vehicle_routing`, `launch_readiness`, `disruption_scan` | **Capacidad sin comprador identificado.** Ninguna aparece en ninguna de las tres fuentes comerciales. Se quedan registradas y sin vender |

### 3.3 Capacidad interna que nunca se le muestra a un cliente

| Módulo | Por qué nunca es una tool |
|---|---|
| `src/commercial_pricing.py` | Calcula lo que **Kern le cobra al cliente**. Exponerlo como tool enrutable le entregaría al cliente la calculadora de su propia factura |
| `src/contingent_fee.py` | Ídem, para el fee del Sprint de Liquidación |
| `src/retention.py` + `jobs/retention_job.py` | Política de retención de datos. Cumplimiento, no producto |
| `src/client_profile.py` | Ya es infraestructura del orquestador (`orchestrator.py:20`, `packages.py:45`). Registrarlo sería duplicarlo |

---

## 4. Corrección a la lista de "ocho módulos construidos sin registrar"

La lista de candidatos a enchufar estaba mal formada. **Seis de los ocho no son huérfanos**,
y enchufarlos habría duplicado capacidad existente en vez de agregar alguna. Medido:

| Módulo | Estado real |
|---|---|
| `supplier_scorecard.py` | **Ya consumido** por `jobs/sourcing_job.py` → tool `sourcing`. Registrarlo aparte duplicaría la salida #1 |
| `working_capital.py` | **Ya consumido** por `jobs/cost_to_serve_job.py` y su deliverable → tool `cost_to_serve` |
| `client_profile.py` | **Ya cableado** en el orquestador y en el runner de paquetes |
| `commercial_pricing.py` | Interno por diseño (§3.3) |
| `contingent_fee.py` | Interno por diseño (§3.3) |
| `retention.py` | Interno por diseño (§3.3), con su job |
| `kanban.py` | **Huérfano real.** 27 líneas, dos funciones de dimensionamiento pull |
| `src/seo/` + `jobs/seo_audit.py` + `jobs/seo_priority.py` | **Huérfano real.** ~900 líneas de orquestación |

Los huérfanos reales son dos, no ocho.

**`kanban.py`: no se registra.** Son 27 líneas que dimensionan un lazo pull. Kern ya tiene
DDMRP y `(s,Q)` para la misma pregunta, ambos en el segundo anillo. Una tercera respuesta a
la misma pregunta es catálogo.

**SEO: no se registra `seo_audit`, se anota `seo_priority`.** `seo_audit` es un crawl técnico
de sitio — marketing, no cadena de suministro; ningún comprador de los tres documentos lo
pide. `seo_priority` es distinto: cruza ABC-XYZ × excess/obsolete × forecast para decir
push/cut/hold por página de producto, o sea **es una salida de inventario disfrazada de SEO**,
y para un cliente e-commerce del núcleo es genuinamente útil. Queda **anotado como candidato
al núcleo, sin registrar todavía**, por una razón concreta que se detalla en §6: no tiene
teoría que citar en el corpus, y ese problema se resuelve antes de exponerlo, no después.

---

## 5. Lo que este documento NO decide

Sería deshonesto cerrar acá una decisión que el dueño dejó abierta por escrito hace dos días.
`05-GTM-KERN.md` §1 lo dice sin rodeos: *"**No está cerrada.** Son dos empresas distintas y
con una persona no se corren las dos."*

**El precio y el motion de la Operación mensual quedan abiertos**, entre:

- **USD 290/mes, autoservicio** (05-GTM): escala sin contratar, necesita 144 clientes,
  riesgo = distribución, y exige el salto multi-tenant que el brief declara "decisión
  separada con presupuesto propio".
- **USD 900–3.200/mes con humano** (brief): 3–6 clientes para la meta, riesgo = es
  consultoría con otro nombre, techo en las horas del operador.

Lo que **sí** queda decidido, y vale para las dos ramas por igual, es todo lo anterior: un
solo bucle de producto, tres peldaños, un núcleo de 16 tools, un segundo anillo que no se
muestra en un menú, y 14 tools fuera de la línea. Las dos ramas comparten el mismo embudo,
el mismo núcleo y el mismo escaneo gratis; se bifurcan **después** del Diagnóstico. Por eso
el trabajo de ingeniería no está bloqueado por esa decisión comercial.

---

## 6. Consecuencias operativas de este documento

1. **No se registra ninguna tool nueva** salvo la que un paquete justifique. Hoy eso es
   ninguna: `kanban` y `seo_audit` quedan fuera por decisión, `seo_priority` queda pendiente
   del problema de citas.
2. **`sourcing` NO sube al núcleo todavía** — recomendado, bloqueado por la semántica de
   paso opcional que rechaza (§2). Precondición antes de tocar `package_specs.py`.
3. **Tres SKU declarados en `documentation/paquetes/` no tienen `PackageSpec`**
   (`price_intelligence`, `org_friction_audit`, y `evidencia-auditoria-inventario` que
   además no tiene código). O se implementan o se retiran del directorio: un one-pager que
   promete algo que el runner no sabe correr es la forma más cara de deuda que hay acá.
4. **El gate de citas manda sobre el registro.** `scm_agent/citation_gate.py` exige ≥2 citas
   reales contra el grafo de libros para toda tool anclada, y
   `tests/test_packages_citations.py` fija el estado actual: 49 tools ancladas, 40 que deben
   mantener citas y 9 con cero deliberado. El corpus es SCM/OR/inventario/pronóstico/pricing.
   `seo_priority` no tiene teoría ahí. El precedente correcto ya está documentado en
   `vehicle_routing`: **anchor-islanded con cero honesto, nunca anclado a la fuerza a un
   falso amigo léxico** ("Logistics as the Vehicle for Change" no habla de vehículos).
   Cualquier registro futuro de SEO sigue ese camino, no el de inventarle un ancla.

---

## 7. Cómo discrepar con esto

Cada decisión de arriba es separable. Si alguien quiere revertir una sola:

| Decisión | Qué habría que sostener para revertirla |
|---|---|
| Un solo bucle de producto | Que un operador solo puede vender y operar 5 tiers + 3 proyectos con 0 clientes hoy |
| Retirar Growth/Scale del menú | Que un comprador frío elige entre "26 tools" y "35 tools" por criterio propio |
| Retirar el Retainer Ejecutivo | Que 1,4× por gobierno sin capacidad nueva no es honorario de consultoría |
| Dejar `sourcing` fuera del núcleo por ahora | Que un `proveedores.csv` parcial bloqueando el entregable mensual completo es un precio aceptable por adelantar la salida #1 |
| No registrar `kanban` | Que una tercera respuesta a la pregunta que ya responden DDMRP y `(s,Q)` agrega algo |
| No registrar `seo_audit` | Que un crawl técnico de sitio es cadena de suministro |
