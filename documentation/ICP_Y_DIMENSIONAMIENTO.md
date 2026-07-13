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
> agencias tributarias, bancos centrales) sobre contenido de vendors/blogs.
> Cobertura geográfica: **Argentina, México, Colombia y Chile** (actualizado
> 2026-07-13 — la primera versión de este documento solo cubría AR+MX;
> ver §2.6 para el detalle de las 4 geografías y §2.7 para la recomendación
> de alcance). Brasil y Perú se evalúan cualitativamente en §2.7 pero **no**
> se investigaron cuantitativamente — no inventar cifras de esas dos
> geografías a partir de este documento.

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

- Al momento de escribir esta sección (rama basada en `origin/main` previo
  a la PR #143), los conectores REALES con implementación funcional eran
  **solo Odoo** (`src/connectors/odoo.py`) y **Excel/xlsx**
  (`src/connectors/excel.py`). Mercado Libre no existía en ningún lado del
  código, y el brief original de esta tarea asumía "connectors Mercado
  Libre + Odoo" — ese supuesto era **falso** en ese momento.
- **Actualización 2026-07-13: PR #143 ("Linchpin 3.0", 25 PRs) se mergeó a
  `main` el mismo día, y agrega un conector de Mercado Libre real —
  cambia lo de arriba.** Son dos módulos distintos, no uno solo, y la
  distinción importa para no sobre-vender:
  - `src/pricing_intel/acquire/meli_api.py` (PR-15): **lectura** de
    listados públicos de un COMPETIDOR en Mercado Libre (inteligencia de
    precios/monitoreo), gateada por `require_approved_site` — no es un
    conector del cliente, es un fetcher de datos de mercado.
  - `src/connectors/meli_prices.py` (PR-18): conector de **escritura de
    precio, `[CRED]`-gated**, para que un cliente autentique con SU PROPIA
    cuenta de vendedor de Mercado Libre (OAuth del Developers Program
    oficial) y actualice el precio de SUS PROPIOS listados — mismo patrón
    de dos capas que `odoo.py` (staged, reversible, con `MeliPriceStore`
    testeado offline contra un `InMemoryMeli`).
  - **Lo que esto significa para pauta, a partir de ahora:** Mercado Libre
    SÍ es un conector real y auditable — pero acotado a **repricing**
    (parte del track "Kern Revenue" de precios), no un reemplazo del
    conector de inventario/reposición que sí tiene Odoo. No vender
    "integramos tu inventario de Mercado Libre" (falso) — sí se puede
    vender "actualizamos tus precios en Mercado Libre de forma segura y
    reversible" (verificado, requiere que el cliente aporte sus propias
    credenciales OAuth).
- Shopify y Amazon **siguen siendo solo comentarios de diseño futuro** —
  `src/connectors/__init__.py` documenta el patrón para que "un adaptador
  real de Shopify/Amazon después" sea fácil de sumar, y `emulator.py`/
  `simulator.py` son *stand-ins* sintéticos — a diferencia de Mercado
  Libre, todavía no hay ningún adaptador real de Shopify/Amazon. **No
  vender "integramos con Shopify/Amazon" — sigue siendo falso.**

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

**Lectura cruzada AR+MX:** el ICP de Kern por firmografía (excluye Micro —
sin presupuesto real; incluye Pequeña alta + Mediana) cae en,
aproximadamente, **USD 1M–15M de venta anual** en ambos países — consistente
con lo que el propio Starter/Growth one-pager ya vende, y más conservador
que el techo de Scale/Retainer (empresas con red real, normalmente ya sobre
USD 15-20M).

**Colombia — VERIFICADO** (Decreto 957 de 2019, vigente; UVT 2026 = COP
52.374, Resolución DIAN 000238; conversión a USD ~COP 3.250 = USD 1,
TRM Banrep, jul-2026):

| Categoría | Comercio (UVT) | ≈USD | Manufactura (UVT) | ≈USD |
|---|---|---|---|---|
| Micro | ≤44.769 | ~0,72M | ≤23.563 | ~0,38M |
| Pequeña | ≤431.196 | ~6,9M | ≤204.995 | ~3,3M |
| Mediana | ≤2.160.692 | ~34,8M | ≤1.736.565 | ~28,0M |

**Chile — VERIFICADO** (Ley 20.416, tramos SII 2025 en UF; UF jul-2026 ≈
CLP 40.845, Banco Central; conversión a USD ~CLP 927 = USD 1, dólar
observado jul-2026):

| Categoría | Ventas (UF) | ≈USD |
|---|---|---|
| Micro | ≤2.400 | ~0,11M |
| Pequeña | ≤25.000 | ~1,10M |
| Mediana | ≤100.000 | ~4,4M |

**Nota sobre Chile:** su techo de "Mediana" (~USD 4,4M) es notablemente más
bajo que el de AR/MX/CO (~USD 14-40M) — la Ley 20.416 (Estatuto PYME) traza
la frontera "PYME vs. gran empresa" en un punto más bajo que las
definiciones estadísticas de los otros tres países. Esto no necesariamente
implica un mercado más chico en cantidad de empresas (ver §2.6) — implica
que, en Chile, parte de lo que Kern targetea como "mid-market" (Growth/
Scale, USD 4-15M) cae formalmente en la categoría "Grande" chilena, no en
"Mediana." Útil para no perder tiempo buscando "medianas empresas
chilenas" como categoría de búsqueda — el ICP real de Kern en Chile abarca
Pequeña + Mediana + una porción de lo que el SII clasifica como Grande.

**Lectura cruzada de las 4 geografías:** el ancla "USD 1-15M" del Starter/
Growth one-pager sigue siendo razonable como filtro amplio en las 4
geografías, con la salvedad de Chile arriba.

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

### 2.6 Dimensionamiento bottom-up — 4 geografías (VERIFICADO base, ESTIMADO el embudo)

> **Actualizado 2026-07-13 (segunda ronda):** el filtro original (censo por
> tamaño × sector) era demasiado grueso — captaba poder adquisitivo y sector,
> pero no si la empresa puede operar Kern en la práctica. Esta versión agrega
> la cadena completa de calificación pedida: registro formal (ya implícito),
> digitalización mínima (el filtro que más recorta y el que faltaba),
> competencia enterprise instalada, y complejidad de inventario. Cada paso
> nuevo dice explícitamente si encontró fuente o no — no se inventa un
> número donde no lo hay, mismo criterio que ya se aplicó con el desglose de
> Chile en la primera ronda.

**Paso 0 — registro formal (YA APLICADO, no requiere investigación nueva):**
los 4 censos usados en el Paso 1 (RUES/Confecámaras Colombia, INEGI México,
UCEMA/Observatorio PyME sobre datos oficiales Argentina, SII Chile) cuentan
**solo empresas formalmente registradas** — ya excluyen por construcción el
peor caso (un negocio que no lleva ningún registro contable/tributario). Esto
no cambia ningún número de los pasos siguientes; se deja explícito porque
antes solo estaba implícito.

**Paso 1 — universo de empresas Pequeña+Mediana (VERIFICADO, fuentes
primarias):**

| País | Total unidades económicas | % Pequeña+Mediana | # Pequeña+Mediana | Fuente |
|---|---|---|---|---|
| Argentina | 549.100 PyMEs empleadoras (2023) | 12% Pequeña + 2,5% Mediana = 14,5% | **~79.600** | Dato citado en dossier BCN sep-2025 / Observatorio PyME, sobre datos oficiales 2023 |
| México | 5.468.180 unidades económicas (2023, Censo 2024) | ~3,7% Pequeña (por sustracción) + ~0,72% Mediana = ~4,4% | **~241.600** | INEGI, Censos Económicos 2024, comunicado 79/25 |
| Colombia | 1.734.636 empresas (2023, RUES) | 6,1% Pequeña + 1,6% Mediana = 7,7% | **~133.400** | Confecámaras/RUES, citado en La República (2025) — cifra exacta: 106.120 pequeñas + 27.238 medianas |
| Chile | 1.503.813-1.582.805 empresas (2024, Operación Renta) | MiPyme (micro+pequeña+mediana) = 76,3-77,4% del total; **Pequeña+Mediana aislada NO se encontró desglosada en fuentes públicas esta sesión** | **~100.000-175.000 (ESTIMADO, rango amplio)** | SII, Operación Renta 2025 (dato agregado MiPyme VERIFICADO); el rango de Pequeña+Mediana es una extrapolación propia usando la proporción interna Pequeña:Mediana:Micro observada en AR/CO (baja confianza — ver nota abajo) |

> **Nota sobre Chile:** el SII publica el total de empresas y el bloque
> agregado "MiPyme" (micro+pequeña+mediana = 1.194.430 en 2024), pero los
> resúmenes públicos encontrados esta sesión no desglosan pequeña y mediana
> por separado en conteo de empresas (sí lo hacen por participación en
> ventas/empleo, que es una métrica distinta). El rango 100.000-175.000 sale
> de aplicarle a ese bloque agregado la proporción interna que sí se
> verificó en Argentina (85% micro / 12% pequeña / 2,5% mediana dentro del
> universo PyME) — es una extrapolación cruzada de un país a otro, marcada
> **ESTIMADO de baja confianza**. Antes de comprometer presupuesto real en
> Chile, vale la pena buscar el desglose exacto directo en
> `sii.cl/estadisticas/empresas_tamano_ventas.htm` (la tabla existe, esta
> sesión no logró extraerla en detalle).

**Paso 2 — filtro a sectores con inventario físico (ESTIMADO — aplica el mix
sectorial NACIONAL de México, comercio 51,3% + industria 11,0% = 62,3%, como
proxy también para Argentina, Colombia y Chile a falta de un dato
equivalente por tamaño de empresa en cada país; esto es una extrapolación
cruzada, no un dato propio de cada país):**

| País | Pequeña+Mediana | × Sectores relevantes (~62%, ESTIMADO) | ICP-relevante |
|---|---|---|---|
| Argentina | 79.600 | 62% | ~49.300 |
| México | 241.600 | 62% | ~149.800 |
| Colombia | 133.400 | 62% | ~82.700 |
| Chile | ~100.000-175.000 | 62% | ~62.000-108.500 (punto medio ~85.000) |
| **Total AR+MX+CO+CL** | **~554.600-629.600** | | **~343.800-389.800 (punto medio ~366.800)** |

Es decir: sumar Colombia y Chile **casi duplica** la población ICP-relevante
frente a solo AR+MX (~199.100 → ~366.800). México sigue siendo, con margen
claro, el mercado individual más grande (~41% del total de 4 países);
Colombia y Chile son similares entre sí (~82.700 y ~85.000) y juntos casi
igualan a México solo.

**Paso 3 — digitalización mínima (el filtro que faltaba: ¿la empresa puede
siquiera producir un CSV de ventas/stock?).** Una empresa formal, del tamaño
correcto y del sector correcto puede seguir llevando el inventario en papel
o de memoria. Metodología: búsqueda por país de encuestas oficiales/
institucionales de TIC-en-empresas o transformación digital PyME,
priorizando fuentes con desglose por tamaño de empresa sobre agregados
"MiPyme":

| País | % con digitalización mínima (Pequeña+Mediana) | Fuente | Confianza |
|---|---|---|---|
| México | **~85-86%** (Pequeña ~85%: 84,6-86,2% con computadora+internet en dos fuentes; Mediana ~92-96%: 91,9-95,6% — promedio ponderado por el peso real de cada subgrupo dentro de Pequeña+Mediana) | INEGI, ENAPROCE y Censos Económicos 2024 (vía cobertura secundaria de Pymempresario) | **Alta** — fuente oficial, desglosada por tamaño |
| Colombia | **~70%** (ACOPI 2024: 74% de las pymes ya usa herramientas digitales para ventas/gestión contable/comunicación interna, muestra general; Innpulsa/Centro Nacional de Consultoría 2024, 4.107 empresas formales: 63% de medianas en niveles avanzados de transformación digital, 42% de micro — consistente en orden de magnitud con ACOPI) | ACOPI + estudio Innpulsa/Ministerio de Comercio (cifra propia del gobierno colombiano) | **Media** — dos fuentes institucionales convergentes, ninguna es el desglose exacto Pequeña vs. Mediana que se buscaba |
| Chile | **~55%** (adopción de e-commerce en MiPyme subió de 39% a 55% entre 2020 y 2021) | CORFO, Índice de Transformación Digital 2021 (~750 casos, cobertura vía CCS/prensa especializada) | **Media-baja** — es un índice agregado "MiPyme" sin desglose Pequeña/Mediana, y la muestra probablemente no es aleatoria |
| Argentina | **NO RELIABLE SOURCE FOUND específica de Argentina.** El único dato tamaño-segmentado encontrado fue un claim de un vendor de ERP (Acumatica: "5,9% usa ERP en la nube") — descartado por ser contenido comercial y por medir solo "ERP en la nube", una categoría mucho más angosta que "tiene algún sistema de gestión". El agregado regional del BID (Chequeo Digital, ~70% de empresas en etapa inicial de madurez digital en toda LATAM) tampoco es Argentina-específico ni mide la misma cosa (madurez vs. digitalización mínima). | — | **Sin fuente propia** — se usa el rango 55-85% observado en los otros 3 países como cota (punto medio 70%), marcado explícitamente como una extrapolación, no una medición |

**Resultado del Paso 3 (ESTIMADO en Colombia/Chile/Argentina, VERIFICADO en
México):**

| País | ICP tras Paso 2 | × Digitalización mínima | ICP tras Paso 3 |
|---|---|---|---|
| Argentina | 49.300 | ~70% (ESTIMADO, sin fuente propia — ver nota arriba) | ~34.500 |
| México | 149.800 | ~86% (VERIFICADO) | ~128.800 |
| Colombia | 82.700 | ~70% (ESTIMADO, dos fuentes convergentes) | ~57.900 |
| Chile | ~85.000 (punto medio) | ~55% (ESTIMADO, baja confianza) | ~46.750 |
| **Total AR+MX+CO+CL** | **~366.800** | | **~268.000** |

**Antes vs. después de este filtro:** ~367.000 → **~268.000** (retiene
~73%, recorta ~27%). Es un recorte real pero **más moderado de lo que se
podría temer** — y ese es en sí un hallazgo que vale la pena remarcar: dentro
de la banda Pequeña+Mediana específicamente (ya sin Micro, que es donde la
digitalización realmente se desploma — en México, por ejemplo, Micro tiene
solo ~20-23% de digitalización contra ~85% de Pequeña), la mayoría de las
empresas YA tiene la base tecnológica mínima. El filtro de tamaño del Paso 1
ya estaba haciendo buena parte de este trabajo indirectamente, sin que el
documento original lo dijera explícitamente.

**Paso 4 — competencia enterprise ya instalada (SAP IBP, Blue Yonder, o9):
impacto ~0%, confirmado indirectamente, no por censo.** No existe (ni se
esperaba que existiera) un dato censal de "% de empresas Pequeña+Mediana
que usa SAP IBP" — es una pregunta demasiado rara para que alguien la mida.
Confirmación indirecta encontrada: SAP IBP arranca en **~USD 100.000/año**
(precio de lista, orientado a "medianas-grandes y grandes empresas" según
el propio posicionamiento de SAP/Gartner), muy por encima de cualquier
paquete de Kern (techo USD 12.000/mes = USD 144.000/año en el Retainer
Ejecutivo, el más caro de los 8) y a años luz del presupuesto de una empresa
de USD 1-15M de facturación. SAP ofrece un producto totalmente distinto
para PyMEs (SAP Business One, un ERP general, no un motor de planificación
de SC) — lo que de hecho refuerza el punto: incluso la PyME con **algún**
ERP (Odoo, SAP Business One, un ERP local) generalmente no tiene la capa de
planificación/optimización avanzada que Kern ofrece. **No se aplica ningún
recorte numérico por este filtro** — el supuesto de impacto ~0% se sostiene
por evidencia de precio, no se refuta ni se inventa un %.

**Paso 5 — complejidad de inventario (# SKUs) — advertencia cualitativa, sin
cifra forzada.** Se buscó explícitamente una correlación censal entre banda
de facturación y # de SKUs típico en retail/distribución/manufactura
liviana — **no se encontró ningún dato de este tipo** (ni específico de
LATAM ni general). Es razonable: nadie levanta un censo de "SKUs por rango
de facturación," y la relación varía enormemente por categoría (un
distribuidor de repuestos con USD 3M puede tener 5.000 SKUs; una manufactura
liviana con USD 3M puede tener 50). **Advertencia explícita para lectura del
número final:** dentro del ~268.000 de empresas ICP-relevantes, hay
variación real e inobservable en complejidad de inventario — una fracción de
esa población probablemente tiene tan pocos SKUs que ni el Diagnóstico de
Arranque agrega valor real (ej. un solo producto, inventario trivial de
gestionar a ojo), y otra fracción tiene tanta complejidad (multi-SKU,
multi-variante) que encaja mejor en Growth/Scale directamente. No se
resta ni se suma nada al ~268.000 por este motivo — se señala como el
límite honesto de lo que un filtro estadístico puede capturar; la
calificación fina de complejidad de inventario solo puede hacerse en la
conversación de intake real con cada lead, no en un modelo de mercado.

**Paso 6 — el embudo hacia metas de ingreso (ESTIMADO desde aquí en
adelante — cada eslabón es una cadena de supuestos, no un dato medido).**
Arranca ahora del número post-Paso 3 (~268.000), no del pre-digitalización
(~367.000) — es la base correcta porque una empresa sin digitalización
mínima no puede completar el intake de ningún paquete sin trabajo previo
de puesta a punto que hoy no está cotizado:

1. **Población ICP calificada (4 países, post-digitalización):** ~268.000
   empresas (Paso 3).
2. **Alcanzable por pauta digital (LinkedIn/Google, filtros de tamaño +
   industria + geo):** sin benchmark LATAM de "% de una población B2B nicho
   que es efectivamente targeteable" — **supuesto conservador propio: 5-10%**
   tiene presencia digital suficiente para ser identificado y targeteado con
   precisión razonable → **~13.400-26.800 empresas alcanzables** en las 4
   geografías combinadas. Este es el eslabón más débil de la cadena — no
   está sostenido por una fuente externa.
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
"alcanzable" de 13.400-26.800 empresas del Paso 6. **Esta cadena completa
tiene la confianza más baja del documento** — es la primera cosa a
recalibrar con datos reales de una campaña piloto, no a tratar como
pronóstico firme.

### 2.7 Alcance recomendado — qué geografías targetear y en qué orden

Pregunta explícita: dado que el mercado combinado (~268.000 empresas ICP
calificadas — post-digitalización, §2.6 Paso 3-5 — en 4 países) es grande,
**¿tiene sentido lanzar pauta en las 4 geografías a la vez?** Recomendación:
**no.** Razones:

1. **Es una operación de un solo operador** (`MONETIZATION_BRIEF.md`,
   "operador solo" — VERIFICADO). Correr y optimizar campañas en 4 mercados
   con matices culturales/regulatorios distintos, sin datos de conversión
   propios todavía, diluye presupuesto y atención sin ganar aprendizaje más
   rápido — el cuello de botella no es cuántas geografías cubrís, es cuántos
   leads podés atender y cerrar vos mismo con calidad.
2. **El eslabón más débil de todo el documento** (§2.6, Paso 3: alcance
   digital real y tasa de cierre) **no tiene dato propio en ninguna de las 4
   geografías.** Diversificar el piloto en 4 países a la vez multiplica la
   incertidumbre en vez de resolverla — mejor calibrar en una geografía y
   replicar el aprendizaje.
3. **No hay señal de canal diferenciada entre países** salvo una: Odoo
   Community Days LATAM 2025 destacó públicamente a **México** como
   "referente en crecimiento dentro de la comunidad Odoo" (fuente:
   birtum.com, cobertura del evento — confianza media, es cobertura de
   prensa/blog de un partner, no un dato oficial de Odoo, pero es la única
   señal cualitativa encontrada que diferencia un país del resto en el
   ecosistema que Kern usa como canal de entrada).
4. El propio `MONETIZATION_BRIEF.md` ya recomienda, para los primeros 30-90
   días, **no** pauta paga sino canales cálidos (Upwork, red directa) para
   los primeros 1-2 clientes, y reserva el módulo Odoo como anzuelo de bajo
   costo — esta sección no contradice eso, lo complementa: cuando llegue el
   momento de invertir en pauta paga, esta es la secuencia recomendada.

**Fase 1 — piloto (0-3 meses): México, solo o + Argentina.**
México concentra el pool ICP calificado individual más grande (~128.800,
**~48% del total de 4 países tras el filtro de digitalización** — su peso
relativo subió frente al 41% pre-filtro, porque México es también la
geografía con MEJOR digitalización verificada dentro de Pequeña+Mediana,
~86%) y es la única geografía con una señal cualitativa de tracción en el
ecosistema Odoo. Sumar Argentina al piloto (en vez de ir solo con México)
tiene sentido si el operador ya tiene red/confianza local ahí (consistente
con el patrón "Upwork/red directa" que ya recomienda el brief de
monetización) — permite calibrar el canal pago en México mientras se
cierran los primeros contratos por canales cálidos en Argentina, sin
depender 100% de pauta fría para el primer caso de estudio. Nota: el ~70%
de digitalización usado para Argentina es una extrapolación sin fuente
propia (§2.6, Paso 3) — otra razón para apoyarse en redes cálidas ahí en
vez de pauta fría hasta tener una lectura local real.

**Fase 2 — expansión (una vez calibrado CPL + tasa de cierre real, ~3-9
meses): sumar Colombia y Chile.**
Ambos tienen poblaciones ICP calificadas similares entre sí (~57.900 y
~46.750) y juntos siguen sumando un bloque relevante (~39% del total) — no
conviene ignorarlos a mediano plazo, pero tampoco se justifica entrar ahí
sin primero validar mensaje/creatividad/conversión en al menos un mercado.
Nota Chile: además del matiz de §2.2 (su techo de "Mediana" es más bajo —
parte del ICP real cae en lo que el SII llama "Grande"), es también la
geografía con la digitalización estimada más baja de las 4 (~55%, y la de
menor confianza) — vale la pena una lectura local antes de escalar
presupuesto ahí, no solo por el tamaño del mercado sino por la calidad del
dato que lo sostiene.

**Fase 3 — evaluar, no ejecutar todavía: Perú.**
Mismo idioma, economía y formalización PyME en crecimiento — **no
investigado esta sesión, sin cifras propias.** Candidato razonable para un
5º mercado una vez que Fase 2 esté en marcha; requiere repetir el ejercicio
de §2.2/§2.6 antes de comprometer presupuesto.

**Brasil: fuera de alcance mientras no exista soporte de portugués.**
Es, de lejos, el mercado LATAM más grande — pero es un **bloqueo de
producto real, no de investigación de mercado**: `src/i18n.py` (VERIFICADO)
solo tiene diccionarios `es`/`en`, sin `pt`. Los one-pagers, el deck
consolidado y la conversación comercial completa de Kern son en español.
Correr pauta en Brasil sin soporte de portugués generaría leads que no se
pueden atender con el mismo nivel de calidad que el resto del catálogo — es
la expansión de mayor potencial a largo plazo, pero condicionada a un
proyecto de traducción real (alcance comparable al que ya tomó el soporte
bilingüe es/en, según `HANDOFF.md`), no a más research de mercado.

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

~268.000 empresas ICP-**calificadas** en Argentina + México + Colombia +
Chile combinadas — es decir, no solo del tamaño y sector correctos
(~367.000, §2.6 Paso 1-2), sino con **digitalización mínima real**
verificada/estimada por país (§2.6 Paso 3): el filtro que más recortó, de
~367.000 a ~268.000 (retiene ~73%). Confirmado además que el filtro de
"competencia enterprise instalada" (SAP IBP/Blue Yonder) no recorta nada
(~0%, por precio — Paso 4) y que "complejidad de inventario/SKUs" no tiene
dato censal y queda como advertencia cualitativa, no como recorte numérico
(Paso 5). De esa población calificada, el tramo realmente alcanzable por
pauta digital hoy sigue siendo una fracción **no verificada con datos
propios** (supuesto de 5-10% → 13.400-26.800 empresas). Los primeros USD
10k/50k/100k son alcanzables con **1 a ~10 clientes** dependiendo del mix
de paquete (Diagnóstico de entrada vs. retainer sostenido) — el cuello de
botella no es el tamaño del mercado (sobra en las 4 geografías combinadas,
incluso después de calificarlo en serio), es la conversión de alcance →
lead → cierre, que hoy no tiene dato propio en ninguna de las 4.
**Recomendación operativa (alcance geográfico, §2.7): no lanzar en las 4 a
la vez.** Empezar el piloto en México (el mercado individual calificado
más grande, ~128.800 empresas, ~48% del total tras el filtro de
digitalización — su peso relativo SUBE porque además es la geografía mejor
digitalizada de las 4 — y la única señal cualitativa de tracción en el
ecosistema Odoo), opcionalmente sumando Argentina vía canales cálidos para
el primer caso de estudio (su cifra de digitalización es una extrapolación
sin fuente propia, otro motivo para no apostarle a pauta fría ahí todavía);
sumar Colombia y Chile en una Fase 2 una vez calibrado el CPL y la tasa de
cierre real. Perú queda como candidato a evaluar sin cifras propias
todavía; Brasil queda fuera de alcance hasta que exista soporte de
portugués en el producto (bloqueo de producto, no de mercado — ver §2.7).

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

## 4. Extensión: mercados de habla inglesa (EE.UU., Reino Unido, Australia)

> **Agregado 2026-07-13 (cuarta ronda), a pedido explícito.** Todo lo de
> arriba (§1-3) responde la pregunta que se hizo primero: LATAM. Pero
> `MONETIZATION_BRIEF.md` (investigación previa, no de esta sesión) ya había
> concluido que la **vía principal** de monetización de corto plazo es
> justamente marcas Shopify/DTC de EE.UU./UK de USD 1-10M, posicionando a
> Kern como "fractional supply chain operator" — LATAM/Odoo se documentó ahí
> como canal **secundario**. Esta sección hace por EE.UU./UK/Australia lo
> que §2 hizo por LATAM: un dimensionamiento bottom-up propio con fuentes
> oficiales, no solo los benchmarks de precio que ya existían. Mismo
> criterio VERIFICADO/ESTIMADO.

### 4.1 Definición firmográfica oficial

**EE.UU. — VERIFICADO** (SBA Table of Size Standards, por código NAICS):
no hay un único corte — varía por sector. Comercio minorista/mayorista usa
estándares de ingresos (USD 1M-47M según la subcategoría exacta);
manufactura usa estándares de empleados (100-1.500 según la subcategoría).
El ICP práctico de Kern (~USD 1-15M) cae cómodamente dentro de "small
business" en prácticamente todos los sectores relevantes — el techo SBA es
mucho más alto que el techo real de Kern (Retainer Ejecutivo, USD 12.000/
mes = USD 144.000/año).

**Reino Unido — VERIFICADO** (Companies Act 2006, actualizado abr-2025;
conversión a USD ~GBP 1,34 = USD 1, jul-2026):

| Categoría | Turnover (GBP) | ≈USD | Balance sheet (GBP) | Empleados |
|---|---|---|---|---|
| Micro | ≤£1M | ~$1,3M | ≤£500k | ≤10 |
| Small | ≤£15M | ~$20,1M | ≤£7,5M | ≤50 |
| Medium | ≤£50M | ~$67M | ≤£25M | ≤250 |

(Calificación: 2 de 3 criterios). El ICP práctico de Kern (~USD 1-15M) cae
en la mitad baja de "Small" y toca el piso de "Medium" para clientes
Growth/Scale.

**Australia — VERIFICADO** (ATO, definición de turnover; conversión a USD
~AUD 0,696 = USD 1, jul-2026): Small business entity <AUD $10M (~USD 7M);
Medium AUD $10M-50M (~USD 7M-35M). El ICP práctico de Kern cae casi
completo dentro de "Small" y roza "Medium" en el extremo superior.

### 4.2 Dimensionamiento bottom-up

**Paso 1 — universo de empresas en la banda de tamaño (VERIFICADO, fuentes
oficiales):**

| País | Banda de empleados | # Empresas | Fuente |
|---|---|---|---|
| EE.UU. | 10-499 | **~1.493.953** (809.398 de 10-19 + 409.391 de 20-49 + 151.979 de 50-99 + 89.573 de 100-249 + 33.612 de 250-499) | Compilación de datos censales (NAICS Association, todos los sectores) |
| Reino Unido | 10-249 | **~257.800** (220.000 de 10-49 + 37.800 de 50-249) | GOV.UK, Business Population Estimates 2024 |
| Australia | 5-199 | **~299.538** (231.681 de 5-19 + 67.857 de 20-199) | ABS, Counts of Australian Businesses, jun-2024 |

**Paso 2 — filtro a sectores con inventario físico (ESTIMADO — mismo método
de cross-tab nacional que en LATAM, con la salvedad de que estas 3
economías tienen una composición sectorial MUY distinta y mucho más
services-heavy que México/Argentina/Colombia/Chile):**

| País | % sector relevante (retail+wholesale+manufactura) | Fuente / cómo se armó |
|---|---|---|
| EE.UU. | **~24%** (retail ~12% + manufactura ~8% + mayorista ~4% estimado) | Retail/manufactura: cifras de participación de "small businesses" por sector (fuente secundaria agregando datos censales); mayorista estimado por analogía, sin cifra propia encontrada |
| Reino Unido | **~16%** (Wholesale and Retail Trade and Repair = 10% VERIFICADO de todas las PyMEs + manufactura ~6% ESTIMADO) | GOV.UK Business Population Estimates 2024 (wholesale/retail) + estimación propia para manufactura (no se encontró el % exacto de conteo, solo de turnover: 9%) |
| Australia | **~13%** (Retail Trade = 156.938 empresas de todos los tamaños = 5,9% del total VERIFICADO + mayorista/manufactura ~7% ESTIMADO, sin cifra propia) | ABS (retail) + estimación propia (mayorista/manufactura) |

**Nota importante, distinta a LATAM:** en México el mismo tipo de filtro
daba ~62% (retail+manufactura = 62,3% de TODAS las empresas) — acá da
13-24%. La diferencia no es un error: EE.UU./UK/Australia tienen economías
mucho más pesadas en servicios profesionales, salud, construcción y
tecnología en su universo de PyMEs, mientras que en México/Argentina el
comercio minorista/informal-adyacente pesa proporcionalmente mucho más.
Aplicar el 62% mexicano a estos 3 países habría sobreestimado el mercado
por 3-5x — otra razón para no reciclar un supuesto de una geografía a otra
sin verificarlo.

| País | Banda de tamaño | × Sector relevante | ICP pre-digitalización |
|---|---|---|---|
| EE.UU. | 1.493.953 | ~24% | **~358.600** |
| Reino Unido | 257.800 | ~16% | **~41.250** |
| Australia | 299.538 | ~13% | **~38.940** |
| **Total EE.UU.+UK+AU** | | | **~438.800** |

**Paso 3 — digitalización mínima (ESTIMADO, sin fuente country-específica
size-segmentada encontrada esta sesión — a diferencia de México/Colombia en
LATAM):** la única cifra dura encontrada es general y vieja (64,4% de PyMEs
de EE.UU. usaba software contable en **2016**, todos los tamaños incluidos,
sin desglose). Razonamiento propio: la banda 10+ empleados en estas 3
economías desarrolladas casi con certeza tiene un piso de digitalización
mucho más alto que en LATAM — QuickBooks/Xero/MYOB son casi universales a
partir de ese tamaño. **Supuesto propio, marcado ESTIMADO: ~90-95%** (se
usa 92% para el cálculo) — **más alto y con menos evidencia directa que la
cifra mexicana (86%, VERIFICADA), pero direccionalmente razonable.**

| País | ICP pre-digitalización | × ~92% (ESTIMADO) | ICP calificado |
|---|---|---|---|
| EE.UU. | 358.600 | 92% | **~329.900** |
| Reino Unido | 41.250 | 92% | **~37.950** |
| Australia | 38.940 | 92% | **~35.825** |
| **Total EE.UU.+UK+AU** | | | **~403.700** |

**Comparación directa con LATAM:** ~403.700 (3 países anglo) vs. ~268.000
(4 países LATAM) — el bloque anglo, con **una geografía menos**, tiene un
pool calificado **~50% más grande**. No es una sorpresa (son economías más
grandes), pero cuantificarlo confirma que la conclusión previa de
`MONETIZATION_BRIEF.md` (vía principal = EE.UU./UK) no dependía solo del
ticket de precio más alto — el mercado de base también es mayor.

### 4.3 La diferencia estructural: acá SÍ hay competencia real instalada

En LATAM, el Paso 4 (competencia enterprise) daba ~0% porque SAP IBP/Blue
Yonder son inalcanzables en precio para esta banda. **En EE.UU./UK/
Australia esto es distinto — VERIFICADO:** existen herramientas SaaS de
gestión de inventario dirigidas específicamente a esta banda de tamaño:
**Cin7** (con Cin7 Core, ex-DEAR Systems), **Katana**, **Netstock**,
**Unleashed**, **Fishbowl**. Cin7 por sí solo reporta 8.500+ clientes,
concentrados desproporcionadamente en exactamente estas 3 geografías —
36,6% de sus clientes en Australia, 32,9% en EE.UU., 14,5% en Nueva Zelanda
(fuente: 6sense, perfil de mercado de Cin7) — y su segmento de cliente
típico son empresas de **20-49 empleados**, el corazón mismo del ICP de
Kern. Esto **no invalida el mercado** (8.500 clientes de Cin7 es una
fracción minúscula de las ~400.000 empresas calificadas) pero cambia el
mensaje de venta: en LATAM la competencia real es "Excel/ChatGPT" (status
quo manual); acá la competencia real incluye **herramientas SaaS ya
establecidas y con base de clientes real** — el argumento de venta no
puede ser solo "somos la primera herramienta digital," tiene que ser
**desplazamiento por profundidad analítica** (Cin7/Katana/Netstock hacen
seguimiento y alertas de inventario; ninguno de los tres, por lo que se
encontró en esta investigación, ofrece el paquete completo de Kern de
forecasting econométrico + DDMRP + S&OP + pricing por elasticidad +
citas a literatura + QA-gate — son herramientas de *tracking*, no el motor
analítico de 37 tools que vende Kern). No se encontró (ni se esperaba
encontrar) una cifra de "% de mercado ya cubierto por estas herramientas"
— con 8.500 clientes de contra ~400.000 empresas calificadas, la
penetración de categoría sigue siendo baja en términos absolutos.

**Otra ventaja estructural, VERIFICADA directamente:** cero fricción de
idioma. El motor de Kern es **nativamente en inglés** — `src/i18n.py`
confirma que `Deliverable.lang` default es `"en"` porque es el mismo
idioma que usan los ~37 decks individuales de cada tool (no hicieron falta
traducir para servir a EE.UU./UK/Australia; al revés, fue LATAM el que
necesitó el trabajo adicional de E4/bilingüismo para llegar a "es"). Esto
es lo opuesto al bloqueo real que sí existe para Brasil (§2.7) — acá no
hay ningún proyecto de producto pendiente para vender en estos 3 países,
el producto ya habla su idioma por defecto.

### 4.4 Síntesis: ¿EE.UU./UK/Australia o LATAM primero?

Esta pregunta ya tenía una respuesta parcial antes de esta sesión —
`MONETIZATION_BRIEF.md` señaló a EE.UU./UK como vía principal — pero sin el
dimensionamiento propio de esta sección. Con datos bottom-up de los dos
lados, la recomendación se puede afinar:

**A favor de EE.UU. como Fase 1 (no LATAM):**
1. Pool calificado ~50% más grande (~330.000 solo EE.UU. vs. ~268.000 las
   4 geografías LATAM combinadas).
2. **Cero fricción de idioma** (motor nativamente en inglés) vs. LATAM,
   que sí necesitó trabajo de producto (E4) para llegar a español.
3. Ancla de precio ya más alta y ya validada (`MONETIZATION_BRIEF.md`:
   consultoría SC en EE.UU. USD 50-500/h, retainers USD 3.000-15.000/mes;
   el mismo documento ya recomienda vender "en inglés a marcas Shopify/DTC
   de USD 1-10M" en la ventana de 90 días).
4. El plan de 30-90 días que YA existe (`MONETIZATION_BRIEF.md`) arranca
   justamente con Upwork — un marketplace global/anglo por diseño — para
   el primer caso de estudio, antes de mencionar el módulo Odoo LATAM.

**A favor de mantener México como Fase 1 (no EE.UU.):**
1. Competencia real instalada en EE.UU./UK/Australia (Cin7/Katana/
   Netstock) exige un mensaje de desplazamiento más sofisticado — en
   México, "ChatGPT + Excel" sigue siendo la competencia real, ciclo de
   venta más simple de ganar para un operador que recién empieza.
2. Canal Odoo ya identificado y con checklist lista para ejecutar
   (`GTM_SUBMISSIONS.md`) — un activo concreto y ya preparado, mientras que
   el canal anglo (Upwork → caso de estudio → posicionamiento fractional)
   es más genérico y depende más de la habilidad comercial del operador en
   inglés.
3. Si el operador tiene más red/confianza en español que en inglés (no
   verificable desde el código — es una pregunta sobre la persona, no
   sobre el mercado), el costo de adquisición real puede ser más bajo en
   LATAM aunque el mercado bruto sea más chico.

**Recomendación:** los dos caminos no son mutuamente excluyentes y el plan
de 30-90 días ya existente los combina — pero si hay que elegir **un solo**
mercado para la primera pauta paga (no el primer cliente, que ya se
recomienda cerrar vía Upwork/red antes de gastar en ads), los datos
bottom-up de esta sesión inclinan la balanza hacia **EE.UU.**, no México:
mercado más grande, cero fricción de producto, y precio ancla más alto ya
validado. México sigue siendo la recomendación correcta **dentro del
universo LATAM** (§2.7) si el criterio de decisión es otro (menor
competencia, canal Odoo ya armado, o ventaja de red del operador en
español) — este documento no puede resolver esa última variable por vos,
solo dimensionar ambos lados con el mismo rigor.

**Reino Unido y Australia:** de menor prioridad que EE.UU. dentro del
propio bloque anglo (pools calificados ~8-9x más chicos: ~38.000 y ~36.000
respectivamente vs. ~330.000 de EE.UU.), pero de alta calidad de dato (las
3 cifras de tamaño son VERIFICADAS con fuente oficial). Candidatos
naturales de Fase 2 anglo, en cualquier orden — UK si importa cercanía
horaria/cultural a Europa, Australia porque es, literalmente, donde nació
Cin7 (mercado ya educado en la categoría, con ventajas y desventajas: más
fácil explicar qué es un "motor de inventario," más difícil desplazar a un
jugador local).

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
- Odoo footprint LATAM (partners/clientes por país) — buscado, sin fuente
  pública confiable (NO RELIABLE SOURCE FOUND). Única señal cualitativa
  encontrada: cobertura de Odoo Community Days LATAM 2025 (birtum.com)
  destacando a México como referente en crecimiento dentro de la comunidad
  Odoo — confianza media, no es un dato oficial de Odoo.
- Colombia — Decreto 957 de 2019 (clasificación por ingresos/UVT, vía
  funcionpublica.gov.co y resúmenes secundarios); UVT 2026 = COP 52.374
  (DIAN, Resolución 000238); TRM ~COP 3.250/USD (Banrep, jul-2026);
  1.734.636 empresas 2023, 91,8% micro / 6,1% pequeña / 1,6% mediana / 0,5%
  grande — Confecámaras/RUES, citado en La República (2025).
- Chile — Ley 20.416 (Estatuto PYME), tramos SII 2025 en UF (sii.cl); UF
  jul-2026 ≈ CLP 40.845 (Banco Central de Chile); dólar observado ~CLP
  927/USD (jul-2026); 1.503.813-1.582.805 empresas 2024, MiPyme (micro+
  pequeña+mediana) = 76,3-77,4% del total, grandes = 1,2-1,3% — SII,
  Operación Renta 2025 (cobertura vía asimet.cl/df.cl). Desglose exacto
  Pequeña vs. Mediana por conteo de empresas: no encontrado esta sesión,
  estimado por extrapolación de la proporción interna de Argentina — ver
  nota en §2.6.
- Perú, Brasil — no investigados cuantitativamente esta sesión (ver §2.7
  para la evaluación cualitativa de ambos).
- Digitalización mínima (§2.6, Paso 3) — México: INEGI ENAPROCE + Censos
  Económicos 2024, por tamaño de empresa (vía cobertura secundaria de
  Pymempresario, dos cifras consistentes: 84,6-86,2% Pequeña, 91,9-95,6%
  Mediana). Colombia: ACOPI (2024, 74% pymes con herramientas digitales) +
  estudio "Caracterización de las mipymes y su apropiación digital"
  (Centro Nacional de Consultoría, 4.107 empresas, para Innpulsa/Ministerio
  de Comercio, Industria y Turismo, 2024). Chile: CORFO, Índice de
  Transformación Digital 2021 (~750 casos, cobertura vía Cámara de Comercio
  de Santiago). Argentina: sin fuente propia size-segmentada encontrada
  esta sesión (se descartó un claim de Acumatica por ser contenido de
  vendor y medir solo "ERP en la nube", categoría más angosta que la
  buscada).
- Competencia enterprise instalada (§2.6, Paso 4) — precio de lista de SAP
  IBP (~USD 100.000/año de entrada, orientado a medianas-grandes/grandes
  empresas), contrastado contra el techo de precio de Kern (USD 12.000/mes
  = USD 144.000/año en su paquete más caro).

**Mercados anglo (§4, jul 2026):**
- EE.UU. — SBA, Table of Size Standards (por NAICS); conteo de empresas
  10-499 empleados vía compilación de datos censales (NAICS Association,
  "US Business Firmographics"); % sectorial retail/manufactura vía cifras
  de participación de "small businesses" por sector (fuente secundaria);
  mayorista estimado por analogía sin cifra propia.
- Reino Unido — Companies Act 2006 (umbrales actualizados abr-2025, vía
  ICAEW/RSM UK); GOV.UK, Business Population Estimates for the UK and
  Regions 2024 (conteo por tamaño y % sectorial wholesale/retail);
  GBP/USD ~1,34 (jul-2026).
- Australia — ATO (definición de turnover small/medium business); ABS,
  Counts of Australian Businesses including Entries and Exits, jul-2024;
  AUD/USD ~0,696 (jul-2026).
- Competencia SaaS de inventario (Cin7/Katana/Netstock/Unleashed/
  Fishbowl) — perfil de mercado y conteo de clientes de Cin7 vía 6sense
  ("Cin7 - Market Share, Competitor Insights").
- Digitalización mínima EE.UU./UK/Australia — sin fuente country-específica
  size-segmentada encontrada esta sesión; único dato duro es general y
  desactualizado (64,4% de PyMEs de EE.UU. usaba software contable en 2016,
  sin desglose por tamaño) — el ~90-95% usado en el cálculo es un supuesto
  propio razonado, no una medición directa.
