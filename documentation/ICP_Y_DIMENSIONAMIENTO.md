# ICP y dimensionamiento de mercado — Kern (LATAM)

> Responde una pregunta operativa: **¿a quién le vendemos, y cuánto vale ese
> mercado?** Insumo directo para `KIT_PUBLICIDAD.md` (los assets de pauta).
>
> **Regla de todo el documento:** cada afirmación está marcada **VERIFICADO**
> (auditable contra el código/docs de este repo, o contra una fuente externa
> primaria citada) o **ESTIMADO** (razonamiento propio, con la cadena de
> supuestos explícita). Donde una geografía o dato no tiene fuente confiable,
> se dice explícitamente — no se inventa un número para completar la tabla.
>
> Metodología de la parte de mercado: búsqueda web dirigida (jul 2026),
> priorizando fuentes primarias (boletines oficiales, censos económicos,
> bancos centrales) sobre contenido de vendors/blogs. Cobertura geográfica
> real de esta sesión: **Argentina y México** (los dos países con estadística
> pública más accesible y reciente). Colombia y Chile **no se investigaron
> de forma independiente esta sesión** — no extrapolar los números de
> AR/MX a esas geografías sin repetir el ejercicio.

---

## 1. Verdad del producto (VERIFICADO contra el código)

### 1.1 Los 8 paquetes comerciales

Fuente: `documentation/paquetes/*.md` (one-pagers reales, listos para prospecto)
+ `documentation/MONETIZATION_BRIEF.md`. Todos ejecutables de punta a punta
via `scm_agent/packages.py` + `scm_agent/package_specs.py`.

| # | Paquete | Precio | Cadencia | Tools | Qué resuelve (decisión del cliente) | Cliente objetivo (texto exacto del one-pager) |
|---|---|---|---|---|---|---|
| 1 | Diagnóstico de Arranque | USD 1.500–2.500 único | Sprint 2 semanas | 4: `data_quality`, `abc_xyz`, `excess_obsolete`, `financial_kpis` | "¿Cuánto dinero tengo atrapado en mi inventario, y dónde?" — primer contacto, cero confianza construida | "Empresas que compran y almacenan inventario y sospechan que algo se les escapa — pero no saben cuánto ni dónde." Sin banda de facturación explícita. |
| 2 | Starter — Fundamentos de Inventario | USD 2.000/mes | Mensual, alcance fijo | 8: + `forecast`, `whatif`, `inventory_optimization`, `newsvendor`, `cycle_count` | Reemplazar la compra "a ojo" en Excel por una política de reposición gobernada | **"E-commerce y distribuidores mono-almacén (USD 1–10M de venta) que hoy deciden compras 'a ojo' sobre una planilla de Excel."** (única banda de facturación explícita en todo el catálogo) |
| 3 | Growth — Operación Completa de SC | USD 4.000/mes + QBR trimestral | Mensual | 26: + `pricing`, `cost_to_serve`, `odoo_replenishment`, `multi_echelon`, `ddmrp`, `drp`, `sourcing`, `fefo`, `landed_cost`, `risk`, `dea`… | Gobernar multi-almacén/multi-canal, con o migrando a ERP (Odoo) | "Empresas en crecimiento, multi-almacén o multi-canal, operando con un ERP (Odoo) o migrando hacia uno." |
| 4 | Scale — Red, S&OP y Mando Ejecutivo | USD 7.500/mes | Quincenal + S&OP mensual | 35 (catálogo completo): + `facility_location`, `transportation`, `warehouse_layout`, `slotting`, `queuing`, `scheduling`, `sop`, `earned_value`, `leadership_chain` | Gobernar una red real (2+ plantas/CDs), no solo un almacén | "Empresas mid-market con red real (2+ plantas o centros de distribución)." |
| 5 | Retainer Ejecutivo Fraccional | USD 9.000–12.000/mes | Semanal + escalamiento SLA | Mismas 35 de Scale — la diferencia es **gobierno**, no capacidad analítica | Mandato de VP/COO de supply chain fraccional | "Cliente que ya pasó 6–18 meses en Scale y necesita un mandato ejecutivo, no solo el análisis mensual." |
| 6 | Proyecto de Red, Almacén y Operación | USD 8.000–18.000 único | 4–8 semanas | 6: `facility_location`, `transportation`, `warehouse_layout`, `slotting`, `queuing`, `scheduling` | Inflexión estructural: nueva bodega, rediseño de red | "El momento de inflexión estructural: abres una nueva bodega, rediseñas tu red, o tu operación ya no aguanta el volumen actual." |
| 7 | Proyecto de Sourcing y Costo de Importación | USD 5.000–10.000 único, recurrible | 2–3 semanas | 3: `sourcing`, `landed_cost`, `acceptance_sampling` | Saber el costo real puesto en destino de cada proveedor/contenedor | "Importadores y empresas con manufactura offshore." |
| 8 | Sprint de Liquidación | 10–20% del cash recuperado, piso USD 1.500 (**contingente**, no fijo) | Sprint 2–3 semanas | 3-4: `data_quality`, `excess_obsolete`, `markdown_liquidation` (+ `pricing` opcional) | Liquidar stock muerto ya diagnosticado sin pagar un fee fijo por algo no recuperado | Stock muerto/excedente ya diagnosticado, resiste pagar fee fijo. |

Además: **Programa de Partners** (`partner-odoo.md`) — no es un 9º paquete,
es el canal para integradores Odoo/consultoras (rev-share 20% o white-label),
relevante como canal de distribución, no como producto propio.

**Camino típico documentado** (`growth-operacion.md`): "La mayoría de nuestros
clientes arranca con el Diagnóstico... opera 3–6 meses en Starter y sube a
Growth cuando se activa el segundo almacén, el ERP o el canal mayorista."

### 1.2 Lo que Kern ya dolariza (la métrica de valor que existe hoy)

VERIFICADO por grep directo sobre `src/` y `jobs/`:

| Métrica en dólares | Módulo | Qué mide |
|---|---|---|
| `excess_value` | `src/excess_obsolete.py` | Cash atrapado en stock excedente/muerto, por SKU |
| `working_capital()` / `cash_release_plan()` | `src/working_capital.py` | Ciclo cash-to-cash (DIO+DSO−DPO), capital de trabajo neto, cash liberado por palanca |
| `cost_to_serve` | `src/cost_to_serve.py` | Margen real por cliente/canal/SKU (quién deja plata, quién la come) |
| recupero real vs. estimado | `src/liquidation.py`, `src/contingent_fee.py` | Cash efectivamente recuperado en una liquidación, medido contra la proyección |
| costo de flete / landed cost | `src/logistics/freight.py`, `src/decision_support.py` | Ahorro de modo de transporte, costo puesto en destino |

Esto es lo que sostiene "vender el resultado en dólares, no las horas"
(`MONETIZATION_BRIEF.md`) — no es una promesa, son campos reales en el código
que ya alimentan los deliverables del Diagnóstico/Starter/Growth.

### 1.3 Autonomía "nunca desprotegida" — el contrato auditable

VERIFICADO directamente en `src/guided.py` (líneas 24-29):

```
EXECUTED  = "executed"   # el agente lo hizo con seguridad
OPTIONS   = "options"    # opciones rankeadas y ejecutables
HANDOFF   = "handoff"    # paso humano-único, preparado por el agente
ESCALATED = "escalated"  # enrutado al humano correcto con contexto
_NON_EXECUTED = (OPTIONS, HANDOFF, ESCALATED)   # 3 de 4 necesitan humano
```

Esto confirma exactamente el claim de `CLAUDE.md`: **3 de los 4 desenlaces
posibles requieren intervención humana explícita.** `verify_guided()` rechaza
por diseño cualquier resultado que no lleve al menos una vía ejecutable — no
puede llegar un "callejón sin salida" al cliente ni al operador
(`documentation/operator/02_division_of_labor.md`, confirmado contra el código).

**Quién opera esto — el nudo de las tres personas (VERIFICADO):**
`documentation/operator/01_role_charter.md` y `02_division_of_labor.md` son
explícitos: existe un **"operador"** (el consultor/agencia que corre Kern,
decide, aprueba lo irreversible y **vende**: *"El cliente te contrata a ti"*)
entre el motor y el cliente final. El cliente final (comprador) recibe el
entregable + una sesión de revisión ejecutiva (45–90 min según el paquete),
pero no toca directamente la capa `OPTIONS/HANDOFF/ESCALATED` — eso lo cierra
el operador. Ver más en la sección 3.3 (¿herramienta para CEO?).

### 1.4 Cobertura del motor y correcciones a supuestos previos

VERIFICADO por conteo directo (`scm_agent/tools.py`, `webapp/mcp_tool_specs.py`):

- **37 tools** registradas en el motor (no 38 — la cifra de 38 no aparece en
  ningún archivo de esta rama; `CLAUDE.md` local dice 37 y el conteo por
  `grep -c 'key="'` lo confirma exacto).
- **33 de 37** expuestas vía MCP. Las 4 no expuestas: `excel_replenishment`,
  `leadership_chain`, `odoo_replenishment`, `warehouse_layout` — mismo gap que
  reportaba la auditoría previa (`linchpin-verified-audit`), sin cambios.
- `citation_gate.py`'s `TOOL_CONCEPTS` mapea las 37 tools 1:1 (confirmado por
  conteo de claves).

**Corrección importante — conectores reales (VERIFICADO por grep en
`src/connectors/`, `scm_agent/`, `jobs/`, `webapp/`, case-insensitive):**

- Conectores REALES con implementación funcional: **solo Odoo**
  (`src/connectors/odoo.py`) y **Excel/xlsx** (`src/connectors/excel.py`).
- **Mercado Libre / MELI: cero menciones en todo el código.** No existe ese
  conector, ni en forma de emulador. El brief original de esta tarea asumía
  "connectors Mercado Libre + Odoo" — ese supuesto es **falso** y no debe
  repetirse en material de venta.
- Shopify y Amazon aparecen **solo como comentarios de diseño futuro**:
  `src/connectors/__init__.py` documenta el patrón para que "una adaptador
  real de Shopify/Amazon después" sea fácil de sumar, y `emulator.py`/
  `simulator.py` son *stand-ins* sintéticos para testear ese patrón — no hay
  ningún adaptador real de marketplace hoy. **No vender "integramos con
  Shopify/Amazon/Mercado Libre" — es falso.**

### 1.5 Autonomía end-to-end (%) — STALE, no re-verificable esta sesión

La cifra previa (~82% clasificación / 75-80% producción de análisis / 40-50%
end-to-end) viene de una auditoría con verificación de código de una sesión
anterior (memoria `linchpin-verified-audit`). Esta sesión **no encontró un
documento de auditoría más reciente en `main`** que re-calcule el % después
de cerrar los 5 gaps (#130/#132/#133/#135/#139) — un re-cálculo real requiere
re-correr el workflow de auditoría contra el código actual, no solo leerlo.
**No usar el número viejo como si fuera vigente en material de venta público**
(sí es válido para uso interno, marcado explícitamente como "cifra a
re-validar"). Lo que SÍ es 100% verificable hoy y no cambia con el tiempo es
el contrato estructural de la sección 1.3 (4 desenlaces, 3 necesitan humano) —
**ese es el claim de venta seguro, no el %.**

### 1.6 Case studies reales — no existen todavía

VERIFICADO: `case-studies/CASE_STUDIES.md` dice literalmente *"Previous
marketing case studies in this file were placeholders"* y hoy contiene
ejercicios de libro de texto (Vandeput 2020), no corridas de clientes reales.
**Kern no tiene un solo case study de cliente real, pagado, con ahorro medido,
todavía.** Los únicos números "reales" disponibles son de corridas demo sobre
datasets sintéticos/de prueba (`--demo`, `examples/run_package.py`) — útiles
para mostrar el formato del entregable, no como prueba social de resultados
en producción. Esto es una brecha real, no un detalle: el plan de 30 días de
`MONETIZATION_BRIEF.md` ya lo identifica ("el objetivo es el caso de estudio
con ahorro medido en $ que convierte a Starter o Growth") — hasta que exista
un cliente real medido, cualquier cifra de "$ liberados" en pauta debe ser
del **rango de referencia de mercado** (ver §2), nunca presentada como
resultado propio de Kern.

---

## 2. Investigación de mercado — LATAM PyME/mid-market

### 2.1 Tensión con la investigación de monetización previa (léase primero)

`MONETIZATION_BRIEF.md` (jul 2026, ~50 agentes de research + verificación
adversarial) ya concluyó que la **vía principal** de monetización de corto
plazo es marcas Shopify/DTC de USD 1-10M en EE.UU./UK (categoría "fractional
supply chain operator", en inglés), y que el canal LatAm/España vía Odoo es
**secundario** (anzuelo: módulo gratis/barato → servicio). Esta tarea pide
específicamente dimensionar y targetear el ángulo LATAM para pauta — se
investiga en profundidad abajo **sin sobreescribir esa conclusión previa**.
Si el resultado de esta sección es más débil de lo esperado, es consistente
con "secundario", no una contradicción.

### 2.2 Definición firmográfica PyME/mid-market — bandas oficiales

**Argentina — VERIFICADO** (Resolución 1/2026, SICYPYME, Boletín Oficial
31-mar-2026; conversión a USD al tipo de cambio oficial vendedor ~ARS 1.510
= USD 1, jul-2026, BNA vía indicadores.ar):

| Categoría | Comercio (ARS/año) | ≈USD | Servicios (ARS/año) | ≈USD | Personal (Comercio) |
|---|---|---|---|---|---|
| Micro | hasta 1.738.060.000 | ~1,15M | hasta 374.060.000 | ~0,25M | hasta 7 |
| Pequeña | hasta 12.380.800.000 | ~8,2M | hasta 2.255.110.000 | ~1,49M | hasta 35 |
| Mediana T1 | hasta 58.848.790.000 | ~39,0M | hasta 18.664.740.000 | ~12,4M | hasta 125 |
| Mediana T2 | hasta 84.070.280.000 | ~55,7M | hasta 26.655.990.000 | ~17,7M | hasta 345 |

Nota: la conversión a USD es orientativa (el ARS es volátil; la definición
legal vigente es en pesos). Coincide con lo que el propio Starter one-pager
ya usa como ancla ("USD 1-10M de venta") — cae dentro de la banda Pequeña de
Comercio argentina casi exacta.

**México — VERIFICADO** (Acuerdo DOF 2009, estratificación vigente
estándar — score combinado 90% ventas / 10% personal; conversión a USD
~MXN 17,5 = USD 1, jul-2026, Banxico FIX):

| Categoría | Ventas anuales (MXN) | ≈USD | Personal (Comercio/Servicios) |
|---|---|---|---|
| Micro | hasta 4,0M | ~228k | hasta 10 |
| Pequeña | 4,01M – 100M | ~229k – 5,7M | 11–50 |
| Mediana | 100,01M – 250M | ~5,7M – 14,3M | 31–100 (Comercio/Servicios), 51–250 (Industria) |

**Lectura cruzada:** el ICP de Kern por firmografía (excluye Micro — sin
presupuesto real; incluye Pequeña alta + Mediana) cae en, aproximadamente,
**USD 1M–15M de venta anual** en ambos países — consistente con lo que el
propio Starter/Growth one-pager ya vende, y más conservador que el techo de
Scale/Retainer (empresas con red real, normalmente ya sobre USD 15-20M).

### 2.3 Persona compradora — benchmark general (no LATAM-específico)

**ESTIMADO / benchmark general de B2B**, no LATAM-específico (fuentes:
close.com "SMB vs Mid-Market vs Enterprise", instantly.ai "Decision Maker
Job Titles" — confianza media, contenido de práctica comercial, no
investigación académica):

- **PyME pequeña (≤50 empleados):** el CEO/dueño toma ~98% de las decisiones
  de compra tecnológica — comité de compra de 1-3 personas.
- **Mid-market (Growth/Scale, empresas con red):** decisión repartida entre
  un director funcional (Ops/Supply Chain/Compras) como *champion técnico* y
  un C-level (COO/CFO) como *economic buyer* — comité de 5-6 personas, ciclo
  de venta más largo (4-12 meses vs. <4 meses en PyME chica).

**El nudo de las tres personas, resuelto (VERIFICADO contra §1.3):**

| Rol | Quién es | Qué hace |
|---|---|---|
| **Opera la herramienta** | El "operador" de Kern — la agencia/consultor (vos) | Corre el motor, decide OPTIONS/HANDOFF/ESCALATED, aprueba writebacks, vende |
| **Compra** | En PyME: dueño/CEO. En mid-market: director de Ops/SC/Compras + COO/CFO | Firma el contrato con el operador, no toca el motor |
| **Consume el entregable** | El comprador (o su equipo, si delega la lectura) | Recibe el deck/Excel, participa en la sesión de revisión |

Para **pauta**, el target es el **comprador**: dueño/CEO en PyME, director
Ops/SC/Compras en mid-market — nunca "cualquiera que use Excel" (ese es
demasiado ancho, sin poder de firma).

### 2.4 Trigger events (ESTIMADO — patrón razonado, sin fuente LATAM directa)

No se encontró investigación LATAM-específica sobre qué dispara la compra de
consultoría de inventario. El patrón abajo es razonamiento propio a partir de
(a) lo que cada uno de los 8 one-pagers dice explícitamente que resuelve
(§1.1, columna "qué resuelve"), y (b) el sentido general de la categoría
"consultoría operativa" — **márquese como ESTIMADO**, no verificado con una
fuente externa:

| Trigger | Paquete que calza | Evidencia |
|---|---|---|
| "No sé cuánto stock muerto tengo" / sospecha de plata atrapada | Diagnóstico de Arranque | Texto literal del one-pager |
| Compra "a ojo" en Excel, sin política | Starter | Texto literal del one-pager |
| Segundo almacén / canal mayorista / migración a Odoo | Growth | "Camino típico" documentado en `growth-operacion.md` |
| Red real (2+ plantas/CDs) que ya no se gestiona con una planilla | Scale | Texto literal del one-pager |
| Apertura de nueva bodega / rediseño de red | Proyecto Red y Almacén | Texto literal del one-pager |
| Migración de proveedor / entrada a importación | Proyecto Sourcing | Texto literal del one-pager |
| Stock muerto ya diagnosticado, decisión de liquidar | Sprint de Liquidación | Texto literal del one-pager |

### 2.5 Willingness-to-pay y canales — lo verificado y lo no encontrado

- **Odoo en LATAM (footprint):** se buscó explícitamente. **NO RELIABLE
  SOURCE FOUND** — Odoo no publica cifras públicas de partners/clientes por
  país LATAM. No usar un número inventado aquí; el canal Odoo App Store sigue
  siendo la apuesta de `GTM_SUBMISSIONS.md` (módulo `linchpin_dry_run`,
  listo para publicar), pero su alcance real es desconocido hasta que se
  publique y se mida.
- **Conversión lead→cierre en ads B2B (ESTIMADO, benchmark general no
  LATAM-específico):** profesional services en LinkedIn Ads cierra
  aproximadamente 2-5% de los leads generados por formulario (fuente:
  compilado de benchmarks de agencias de performance B2B 2026 — confianza
  media, contenido de vendors de marketing, convergente entre varias fuentes
  pero ninguna es LATAM-específica ni de la categoría "consultoría SC").
- **Precio ancla ya validado (VERIFICADO, `MONETIZATION_BRIEF.md`):**
  consultoría SC en EE.UU. factura USD 50-500/h, retainers USD 3.000-15.000/
  mes; freelance España/LatAm (Malt) ~€40-150/h — Kern LATAM se posiciona
  deliberadamente **por debajo** de la referencia US/UK (Starter USD 2.000,
  Growth USD 4.000) para el mercado local, y como upsell hacia el techo (USD
  9.000-12.000 Retainer Ejecutivo) igualando el punto medio de fractional
  CFO/COO US.

### 2.6 Dimensionamiento bottom-up (VERIFICADO base, ESTIMADO el embudo)

**Paso 1 — universo de empresas Pequeña+Mediana (VERIFICADO, fuentes
primarias):**

| País | Total unidades económicas | % Pequeña+Mediana | # Pequeña+Mediana | Fuente |
|---|---|---|---|---|
| Argentina | 549.100 PyMEs empleadoras (2023) | 12% Pequeña + 2,5% Mediana = 14,5% | **~79.600** | Dato citado en dossier BCN sep-2025 / Observatorio PyME, sobre datos oficiales 2023 |
| México | 5.468.180 unidades económicas (2023, Censo 2024) | ~3,7% Pequeña (por sustracción) + ~0,72% Mediana = ~4,4% | **~241.600** | INEGI, Censos Económicos 2024, comunicado 79/25 |

**Paso 2 — filtro a sectores con inventario físico (ESTIMADO — aplica el mix
sectorial NACIONAL de México, comercio 51,3% + industria 11,0% = 62,3%, como
proxy también para Argentina a falta de un dato equivalente por tamaño de
empresa; esto es una extrapolación cruzada, no un dato propio de cada país):**

| País | Pequeña+Mediana | × Sectores relevantes (~62%, ESTIMADO) | ICP-relevante |
|---|---|---|---|
| Argentina | 79.600 | 62% | ~49.300 |
| México | 241.600 | 62% | ~149.800 |
| **Total AR+MX** | **321.200** | | **~199.100** |

**Colombia y Chile: sin dato propio esta sesión — no incluidos en el total.**
Extender el ejercicio a esos dos países es el primer paso lógico de
seguimiento antes de correr pauta ahí.

**Paso 3 — el embudo hacia metas de ingreso (ESTIMADO desde aquí en
adelante — cada eslabón es una cadena de supuestos, no un dato medido):**

1. **Población ICP (AR+MX):** ~199.000 empresas (Paso 2).
2. **Alcanzable por pauta digital (LinkedIn/Google, filtros de tamaño +
   industria + geo):** sin benchmark LATAM de "% de una población B2B nicho
   que es efectivamente targeteable" — **supuesto conservador propio: 5-10%**
   tiene presencia digital suficiente para ser identificado y targeteado con
   precisión razonable → **~10.000-20.000 empresas alcanzables.** Este es el
   eslabón más débil de la cadena — no está sostenido por una fuente externa.
3. **Leads generados:** depende del presupuesto de pauta (no modelado aquí —
   requiere una campaña real para calibrar CPL).
4. **Cierre sobre leads (benchmark general no-LATAM, §2.5): 2-5%.**

**Deals necesarios para metas de ingreso** (usando el mix recomendado en
`MONETIZATION_BRIEF.md`, que ya modela esto con precios reales):

| Meta | Camino más corto (ESTIMADO, con precios VERIFICADOS del §1.1) | Interpretación |
|---|---|---|
| **Primeros USD 10.000** | 4-5 Diagnósticos de Arranque (USD 1.500-2.500 c/u) **o** 1 cliente Growth × 2,5 meses | Ingreso acumulado — el Diagnóstico es la puerta de entrada de menor fricción |
| **Primeros USD 50.000** | ~10 Diagnósticos (convirtiendo 2-3 a Starter/Growth en el camino) **o** 2 clientes Growth sostenidos ~6 meses (USD 8.000/mes × 6 = 48.000) | Acumulado a 6 meses, mezcla realista de entrada + retención |
| **Primeros USD 100.000** | El propio escenario "B. Mixto" de `MONETIZATION_BRIEF.md` (2 retainers + 1 proyecto Odoo/mes ≈ USD 8.000/mes) sostenido ~12-13 meses | Equivale al objetivo de MRR ya fijado internamente (USD 8k/mes) alcanzando ingreso acumulado anual |

Con el cierre de 2-5% (§2.5) y precios de USD 1.500-4.000 por primer contrato,
alcanzar los primeros USD 10k requiere del orden de **80-250 leads
calificados** — que, a su vez, requieren una fracción no modelada del
"alcanzable" de 10.000-20.000 empresas del Paso 2. **Esta cadena completa
tiene la confianza más baja del documento** — es la primera cosa a
recalibrar con datos reales de una campaña piloto, no a tratar como
pronóstico firme.

---

## 3. Síntesis — respuestas explícitas

### 3.1 ICP primario, secundario y disqualifiers

**ICP primario (LATAM, canal Odoo/pauta digital):**
- **Firmográfico:** retailer/distribuidor/manufactura liviana, USD 1-15M de
  venta anual, mono o multi-almacén, opera o migra a Odoo, sin equipo propio
  de data science. VERIFICADO contra el ancla explícita del Starter one-pager
  (USD 1-10M) + bandas oficiales AR/Mediana T1 y MX/Mediana (§2.2).
- **Persona compradora:** dueño/CEO (empresa chica) o director de
  Operaciones/Supply Chain/Compras + COO/CFO como aprobador (empresa
  mediana). ESTIMADO por benchmark general de B2B (§2.3), no LATAM-específico.
- **Triggers:** sospecha de stock muerto, compra "a ojo" en Excel, segundo
  almacén/canal, migración a Odoo (§2.4, ESTIMADO razonado desde los propios
  one-pagers).

**ICP secundario:** empresas mid-market con red real (2+ plantas/CDs) que ya
superaron Growth — target de Scale/Retainer Ejecutivo. Mismo canal, ticket
más alto, ciclo de venta más largo (consistente con el benchmark de §2.3).

**Disqualifiers (a quién NO targetear — quema presupuesto de pauta):**
- Micro-empresas (bajo el piso Micro de AR/MX, §2.2) — sin presupuesto real
  para ningún paquete, ni siquiera el Diagnóstico.
- Empresas sin inventario físico (servicios puros, software) — cero
  encaje con las 37 tools del motor.
- Empresas que ya tienen SAP IBP/Blue Yonder u otro suite enterprise
  desplegado — resuelto el problema que Kern ataca, ciclo de venta
  desperdiciado.
- Empresas buscando "un dashboard" o "un chatbot de IA" — Kern promete
  entregables con QA + humano en el loop, no un juguete de IA (choca contra
  la lista de claims prohibidos, ver `KIT_PUBLICIDAD.md`).

### 3.2 Tamaño de la oportunidad

~199.000 empresas ICP-relevantes en Argentina + México combinados (VERIFICADO
la base censal, ESTIMADO el filtro sectorial — §2.6). De esa población, el
tramo realmente alcanzable por pauta digital hoy es una fracción **no
verificada con datos propios** (supuesto de 5-10% → 10.000-20.000 empresas).
Los primeros USD 10k/50k/100k son alcanzables con **1 a ~10 clientes**
dependiendo del mix de paquete (Diagnóstico de entrada vs. retainer
sostenido) — el cuello de botella no es el tamaño del mercado (sobra), es
la conversión de alcance → lead → cierre, que hoy no tiene dato propio.
**Recomendación operativa:** correr una campaña piloto pequeña y medir CPL +
tasa de cierre real antes de comprometer presupuesto grande — el resto del
embudo (§2.6, Paso 2-4) es la parte del documento con menos confianza.

### 3.3 ¿Es una herramienta para CEO?

**No, hoy no.** VERIFICADO contra `documentation/operator/01_role_charter.md`
y `02_division_of_labor.md`: Kern es el **motor de producción del operador**
(la agencia/consultor) — el CEO/decisor del cliente final nunca toca
`OPTIONS/HANDOFF/ESCALATED` directamente, recibe el entregable ya curado y
participa de una sesión de revisión ejecutiva. El ángulo "superficie de
decisión para el CEO" (Kern 3.0, Track A "Control Tower", A1-A5, tiers
T1/T2/T3) es la **evolución planeada, no el estado actual** — ese plan vive
en `documentation/LINCHPIN_3.0_PLAN.md` y está marcado en memoria del
proyecto como "docs only, not implemented." **No vender hoy "Kern es tu panel
de control ejecutivo de IA" — eso describe el plan 3.0, no el producto que
se factura.** En la pauta actual, comunicar a Kern como lo que produce el
entregable que el CEO/director firma y usa — el CEO es el destinatario del
resultado, no el operador de la herramienta.

### 3.4 ¿Cuántas horas de trabajo libera? (ESTIMADO — no instrumentado)

El producto **no mide** hoy cuánto tiempo humano reemplaza — no hay
telemetría de "horas-analista ahorradas" en ningún job ni deliverable
(confirmado: no aparece ese campo en `src/` ni `jobs/`). Lo siguiente es una
estimación propia, por paquete, del tiempo que un analista tardaría en
producir manualmente el mismo alcance (Excel + validación + reporte),
**marcada ESTIMADO en su totalidad**, no una cifra del producto:

| Paquete | Alcance manual equivalente | Estimación (horas-analista) |
|---|---|---|
| Diagnóstico de Arranque | Auditoría de datos + ABC-XYZ + E&O + KPIs financieros, 4 análisis completos | ~24-40 h (una semana de analista) |
| Starter | Lo anterior + pronóstico SKU + política de reposición + conteo cíclico, mensual | ~30-50 h/mes |
| Growth | 26 análisis, incluida integración Odoo y pricing | ~80-120 h/mes (equivalente a un analista senior full-time) |
| Scale/Retainer | 35 análisis + S&OP + red + bodega | ~150-200+ h/mes (equivalente a un pequeño equipo) |

**Úsese solo como argumento secundario, nunca como claim verificado en
pauta** ("hasta 40 h de análisis en 2 semanas", no "ahorra 40 h" — Kern no
mide el contrafactual real).

### 3.5 ¿Se cuantifica hora-hombre o valor?

**Recomendación: liderar con VALOR EN DÓLARES, horas-hombre como argumento
secundario.** Justificación:
- El valor en dólares (§1.2) es **VERIFICADO, auditable, y ya vive en el
  código** — es el moat real (QA-gate + citas + writeback reversible sobre
  ese número). Las horas-hombre son una estimación no instrumentada (§3.4).
- El comprador LATAM PyME/mid-market (§2.3) decide sobre impacto de negocio
  ("¿cuánta plata libero?"), no sobre productividad de un analista que
  probablemente no tiene contratado todavía — vender "ahorra horas" asume un
  analista que el ICP primario, por definición (§2.2, "no puede pagar un
  equipo de data science"), no tiene.
- Regla dura del proyecto: cada claim de venta debe ser auditable. El valor
  en dólares lo es hoy; las horas no.

### 3.6 Mapa comprador-por-paquete

| Paquete | Quién compra | Mensaje de venta |
|---|---|---|
| Diagnóstico de Arranque | Dueño/CEO (PyME chica) | "¿Cuánto dinero tenés atrapado en tu inventario, y dónde? Lo sabés en 2 semanas por USD 1.500-2.500." |
| Starter | Dueño/CEO, a veces con un encargado de compras | "Dejá de comprar a ojo en Excel — una política de reposición gobernada, todos los meses, por USD 2.000." |
| Growth | Director de Operaciones/SC (empresa en crecimiento) | "Tu operación ya es multi-almacén o vive en Odoo — el análisis mensual completo, con pricing y costo de servir incluidos." |
| Scale | Director de SC/COO (mid-market, red real) | "Gobernás una red de 2+ plantas con un ciclo S&OP real, no una planilla que ya no alcanza." |
| Retainer Ejecutivo | COO/VP de Supply Chain (cliente maduro, 6-18 meses en Scale) | "Necesitás un operador fraccional con presencia semanal y SLA, no otro reporte mensual." |
| Proyecto Red y Almacén | COO/Director de Operaciones (evento puntual) | "Vas a abrir una bodega nueva o rediseñar tu red — decidilo con un estudio cuantitativo, no a ojo." |
| Proyecto Sourcing | Director de Compras/Procurement | "Sabé cuánto cuesta REALMENTE cada proveedor puesto en destino antes de tu próxima negociación." |
| Sprint de Liquidación | Dueño/CEO o Director de Compras con stock muerto ya identificado | "Pagás solo si recuperamos cash — nunca un fee fijo por algo que no se vendió." |

---

## Fuentes citadas (mercado, jul 2026)

- Argentina — Resolución 1/2026 (SICYPYME), Boletín Oficial 31-mar-2026;
  resumen en contadoresenred.com y argentina.gob.ar/noticias.
- Argentina — tipo de cambio oficial ~ARS 1.510/USD, indicadores.ar / BNA,
  jul-2026.
- Argentina — 549.100 PyMEs empleadoras 2023, 85% micro/12% pequeña/2,5%
  mediana — dossier BCN "Micro, Pequeña y Mediana Empresa" sep-2025, sobre
  datos oficiales/UCEMA/Observatorio PyME 2023-2025.
- México — Acuerdo DOF (estratificación PyME, score 90% ventas/10% personal)
  vía gbconsulting.com.mx (resumen del acuerdo oficial).
- México — 5.468.180 unidades económicas 2023 (95,4% micro / 0,72% mediana /
  0,2% grande), 51,3% comercio / 34,7% servicios / 11% manufactura — INEGI
  Censos Económicos 2024, comunicado de prensa 79/25 (24-jul-2025).
- México — tipo de cambio ~MXN 17,5/USD, Banxico FIX, jul-2026.
- Persona compradora SMB vs. mid-market — close.com "SMB vs Mid-Market vs
  Enterprise Sales", instantly.ai "Decision Maker Job Titles" (benchmark
  general B2B, no LATAM-específico, confianza media).
- Conversión lead→cierre profesional services en LinkedIn Ads (~2-5%) —
  compilado de benchmarks de agencias de performance B2B 2026 (confianza
  media, no LATAM-específico).
- Odoo footprint LATAM — buscado, sin fuente pública confiable
  (NO RELIABLE SOURCE FOUND).
- Colombia, Chile — no investigados de forma independiente esta sesión.
