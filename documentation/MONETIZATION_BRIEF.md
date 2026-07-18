# Monetización de Kern — reporte breve (meta: ≥USD 8.000/mes)

> Investigación web profunda (jul 2026): ~50 agentes de búsqueda/extracción + verificación
> adversarial de afirmaciones (3 votos por afirmación). Solo se citan datos que
> sobrevivieron la verificación; los refutados se corrigen abajo.

## Cuadro comparativo — vías de monetización

| Vía | Precio realista | Clientes para $8k/mes | Tiempo a $8k | Riesgo / evidencia | Veredicto |
|---|---|---|---|---|---|
| **1. Servicio productizado / operador fraccional de inventario** (Kern produce, tú vendes y firmas) | Retainer **$2.000–5.000/mes** por cliente (mercado fraccional US: $100–300/h ejecutivo, $70–120/h nivel ops; 69,5% de los fraccionales cobra $5k–10k/mes por cliente — Fractionus 2025; consultoría SC: retainers $3k–15k/mes — ShipSigma) | **2–4** | **3–6 meses** | Bajo-medio. Categoría "fractional supply chain" existe y se recluta activamente (SCM Talent, Cast USA, GoFractional). El riesgo es adquisición, no disposición a pagar | ✅ **Vía principal** |
| **2. SaaS del dashboard** (self-serve) | El mercado ancla **$49–349/mes**: Cogsy desde $49, Prediko desde $119 (repricing 2026), Inventory Planner ~$245–299 por cotización (Essentials $119,99 en Shopify) | **~50–80** | 12–24 meses | Alto: competir en self-serve contra 10+ tools maduras con marketing pago; inviable en solitario a corto plazo | ⚠️ Después, como piso de ingreso recurrente |
| **3. MCP server pago** (linchpin.fly.dev) | Casi nadie factura hoy: **~95% de los devs MCP no gana nada**; rails de cobro (x402 Cloudflare/Stripe, Apify ~80% rev-share) recién lanzados 2025-26; casos públicos $500–3k/mes long-tail | n/a | No llega solo | Canal inmaduro como ingreso | 🔁 Usar como **demo/distribución y lead-gen**, no como facturación |
| **4. Ecosistema Odoo** | Módulos de inventario en Apps Store se venden a **$50–200 one-time** (dev retiene 70%) → no sostiene $8k/mes. Pero los **servicios** Odoo facturan 1–2 órdenes más: implementaciones $800–7.200, proyectos custom $4.800–32.000 | Módulo: cientos de ventas. Servicios: 2–3 proyectos/mes | Servicios: 3–6 meses | Ecosistema grande y creciente (~3.800 partners, 13M usuarios, miles de clientes nuevos/mes, fuerte en LatAm/España); Apps Store saturada (40.000+ apps) | ✅ **Módulo gratis/barato como anzuelo → servicio de inventario sobre Odoo** |
| **5. Marketplaces (Upwork/Toptal)** | Mediana business consultants Upwork ~$55/h (rango $28–98); ~300 trabajos abiertos de inventory management | 4–6 contratos | 1–3 meses (rápido pero techo bajo) | Compite por precio; tarifa la fija la plataforma y la ubicación | 🔁 Solo para **primeros 1–2 clientes y casos de estudio**, luego salir |

**Combinación ganadora:** (1) como núcleo + (4) como canal en español + (3)/(5) como generadores de leads. (2) recién cuando haya 5+ clientes de servicio que financien el self-serve.

## Benchmarks de precio verificados (para posicionar tu oferta)

| Referencia | Cifra verificada |
|---|---|
| SaaS inventory planning e-commerce (Prediko, Cogsy, StockTrim, Inventory Planner) | $49–349/mes self-serve; por cotización ~$245–599/mes |
| Consultor supply chain (US) | $50–500/h; retainers $3.000–15.000/mes |
| Talento fraccional US (e-commerce/CPG, nivel ejecutivo) | $100–300/h; estructura típica ~10 h/semana → $4.300–13.000/mes |
| Nivel analista/ops (piso realista para empezar) | $70–120/h |
| Fractional CFO e-commerce (ámbito adyacente, techo de referencia) | $3.000–15.000/mes; la mayoría paga $5.000–7.500 |
| Pricing por valor (consultoría SC/procurement) | 10–20% del ahorro proyectado del primer año |
| España/LatAm freelance (Malt): consultor medio ~40€/h, senior 90–150€/h | Vender remoto a US/UK paga **2–4×** la misma hora |

Correcciones surgidas en verificación: Cogsy **no** parte en $199 (parte en $49; $199 es el tier con WMS). Prediko subió de $49 a **$119** de entrada en 2026. Inventory Planner sí tiene un tier self-serve ($119,99 Essentials en Shopify) aunque su flagship es por cotización.

## Escenarios hacia $8.000/mes

| Escenario | Mix | Total |
|---|---|---|
| **A. Fraccional puro** | 3 retainers de inventario e-commerce/pyme a $2.700/mes (~8–10 h/sem c/u, Kern hace el análisis) | $8.100 |
| **B. Mixto Odoo** (hispano + anglo) | 2 retainers $2.500 + 1 proyecto Odoo/mes $3.000 (implementación módulo + política de inventario) | $8.000 |
| **C. Escalera** (arranque) | Mes 1–3: 2 contratos Upwork (~$2–3k) + 1 retainer $2.500 → mes 4–6: convertir a 3 retainers y subir precio | $8k en mes ~6 |

## Estructura de empaquetado comercial (10 secciones vendibles por separado)

> Diseñada con un panel de 3 jueces independientes que evaluó 3 estructuras (escalera de
> consultoría, **tiers fijos**, modular à la carte) en 5 criterios: claridad para el
> comprador, techo de ingreso, facilidad de upsell, defendibilidad frente a la competencia,
> viabilidad para un operador solo. Ganó **tiers fijos** (40,3/50 promedio de los 3 jueces,
> vs. 37,7 escalera y 32,3 modular — los 3 jueces lo prefirieron explícitamente por ser el
> más simple de vender y operar en solitario), injertando 3 ideas de la escalera perdedora:
> el diagnóstico de entrada de bajo riesgo, el techo superior vía retainer ejecutivo, y los
> 2 proyectos puntuales de alto ticket como única excepción a la regla de oro. **Ninguna
> sección vende un tool suelto** — siempre un paquete completo de varios tools.

| # | Sección | Precio | Cadencia | Tools incluidas | Cliente objetivo |
|---|---|---|---|---|---|
| 1 | **Diagnóstico de Arranque** | $1.500–2.500 único | Único, sprint 2 semanas | 4: `data_quality`, `abc_xyz`, `excess_obsolete`, `financial_kpis` | Primer contacto, cero confianza construida |
| 2 | **Starter** — Fundamentos de Inventario | $900/mes (piso ~500 SKUs, +$40/mes c/bloque de 250 SKUs, techo $1.500) | Mensual, alcance variable por catálogo | 15: `forecast`, `abc_xyz`, `whatif`, `inventory_optimization`, `newsvendor`, `excel_replenishment`, `cycle_count`, `data_quality`, `pricing`, `excess_obsolete`, `financial_kpis`, `reconciliation`, `landed_cost`, `returns`, `risk` | E-commerce/distribuidor mono-almacén, $1–10M, compra "a ojo" en Excel |
| 2L | **Starter LatAm** — Fundamentos de Inventario (alcance reducido) | $250–300/mes | Mensual, alcance fijo reducido | 8: `data_quality`, `abc_xyz`, `forecast`, `whatif`, `inventory_optimization`, `excel_replenishment`, `cycle_count`, `newsvendor` | E-commerce/distribuidor mono-almacén en LatAm, $1–10M, compra "a ojo" en Excel — alcance de entrega más liviano que el Starter Anglosfera, no el mismo producto más barato (ver nota debajo de la tabla) |
| 3 | **Growth** — Operación Completa de SC | $1.500/mes (piso ~2.000 SKUs, +$60/mes c/bloque de 500 SKUs, techo $3.200) | Mensual + QBR trimestral | 26 (todo Starter + `multi_echelon`, `ddmrp`, `simulation`, `drp`, `odoo_replenishment`, `fefo`, `sourcing`, `acceptance_sampling`, `cost_to_serve`, `learning_curve`, `dea`) | Empresa en crecimiento, multi-almacén/canal, con o migrando a ERP (Odoo) |
| 4 | **Scale** — Red, S&OP y Mando Ejecutivo | $3.200/mes (flat) | Quincenal + S&OP mensual | Las 35 tools del catálogo completo (+ `facility_location`, `transportation`, `warehouse_layout`, `slotting`, `queuing`, `scheduling`, `sop`, `earned_value`, `leadership_chain`) | Mid-market con red real (2+ plantas/CDs) |
| 5 | **Retainer Ejecutivo Fraccional** | $4.500/mes (flat = Scale × 1,4) | Mensual + cadencia semanal + escalamiento con SLA | Mismas 35 tools de Scale — la diferencia es gobierno, no capacidad | Upgrade para cliente Scale existente (6–18 meses), mandato de VP/COO fraccional — no se vende en frío como opción de entrada |
| 6 | **Proyecto de Red, Almacén y Operación** | $8.000–18.000 único | Único, 4–8 semanas | 6: `facility_location`, `transportation`, `warehouse_layout`, `slotting`, `queuing`, `scheduling` | Inflexión estructural: nueva bodega, rediseño de red/almacén |
| 7 | **Proyecto de Sourcing y Costo de Importación** | $5.000–10.000 único | Único, recurrible trimestral/anual | 3: `sourcing`, `landed_cost`, `acceptance_sampling` | Importadores / manufactura offshore |
| 8 | **Sprint de Liquidación** | 10–20% del cash recuperado, piso $1.500 (contingente, no fijo) | Único, sprint 2–3 semanas | 3-4: `data_quality`, `excess_obsolete`, `markdown_liquidation` (+ `pricing` opcional) | Stock muerto/excedente ya diagnosticado, decidido a liquidar, resiste pagar un fee fijo por algo no recuperado |
| 9 | **Diagnóstico de Posición de Precios** | $2.000–3.500 único | Único, sprint 2 semanas (one-shot) | 1: `price_intelligence` (nueva, Linchpin 3.0 — "el titán del pricing") | Vende productos comparables online, quiere saber dónde está caro/barato frente a la competencia con evidencia trazable, sin monitoreo continuo todavía |
| 10 | **Evidencia de Auditoría de Inventario** *(vertical nueva, requiere build — ver nota)* | $6.000–15.000 por ciclo de auditoría | Único, recurrente cada auditoría (anual) | 4: `audit_evidence` (nueva) + `abc_xyz`, `reconciliation`, `acceptance_sampling` (existentes) | Controller/CFO de empresa privada en su 1ª auditoría o pre-IPO; o firma mid-tier que subcontrata la preparación de papeles de inventario/COGS |

**Reprecio 2026-07-18 (Anglosfera: AU/NZ/US/UK/Canadá cotizan el mismo número
en USD, convertido al tipo de cambio del día; ancla en datos reales de salario
UK):** Starter y Growth pasan a **precio variable con techo** — el precio
sube en bloques a medida que crece el catálogo del cliente, pero nunca más
allá del techo (que coincide con el precio del tier siguiente) sin una
conversación formal de upgrade, nunca como sorpresa en la factura. Scale y el
Retainer Ejecutivo quedan **flat** (Scale porque el cliente mid-market quiere
certidumbre de presupuesto sobre facturación incremental; el Retainer porque
es Scale × 1,4 y dejó de venderse como 4º tier de menú — solo como upgrade
ofrecido a un cliente Scale existente). También se reclasificaron 7
herramientas "universales" (aplican a cualquier negocio sin importar tamaño/
estructura) de Growth a Starter: `excess_obsolete`, `financial_kpis`,
`pricing`, `reconciliation`, `landed_cost`, `returns`, `risk` — a diferencia
de `multi_echelon`/`drp`/`dea`/`facility_location`/`sop`/`transportation`/
`warehouse_layout`/`slotting`/`scheduling`/`queuing`, que sí requieren
complejidad organizacional real (2+ ubicaciones, equipos cross-funcionales)
que un cliente Starter mono-almacén todavía no tiene. Starter pasa de 8 a 15
tools sin subir de precio base: son cómputo, no horas-founder, así que el
costo marginal de entrega es bajo.

**LatAm (solo equivalente a Starter — fila 2L):** USD 250–300/mes — **alcance de
entrega más liviano que el Starter Anglosfera, no el mismo producto más
barato**: un salario real de analista LatAm (~USD 400–650/mes) es demasiado
bajo para cubrir horas-founder a la lógica de %-de-salario que ancla el
precio Anglosfera, así que esto debe ser una oferta de alcance reducido, no
un descuento sobre el Starter completo. Growth/Scale/Retainer **no** se
extienden a LatAm (confirma la decisión previa en `ICP_Y_DIMENSIONAMIENTO.md`
de que LatAm es un canal de auditoría/SaaS-lite, no la escalera completa de
retainers). Diagnóstico, Proyecto de Red/Almacén, Proyecto de Sourcing y
Sprint de Liquidación no cambian esta ronda.

Nota sobre la sección 8: es la única con **precio contingente** — el
honorario se calcula sobre el cash efectivamente recuperado (nunca sobre una
proyección), con `src/contingent_fee.py` como calculadora única. Abre la
puerta a **Starter** para el cliente que no quiere que el stock muerto vuelva
a acumularse.

Nota sobre la sección 9: junto con los 2 proyectos puntuales (6, 7), es la
única otra excepción a "ningún tool suelto se vende" — capacidad neta nueva
que ninguna de las otras 8 secciones cubre (`documentation/paquetes/
diagnostico-posicion-precios.md`, plan `LINCHPIN_3.0_PLAN.md` sección 10).
Modo **one-shot**: el cliente entrega el mapeo SKU↔URL de competidor, que ya
es el match confirmado — no depende del pipeline de matching automático
(PR-14). Abre la puerta al **add-on de monitoreo continuo** (Growth/Scale)
una vez que exista el Control Tower conectado (PR-15).

**Camino a $8.000/mes:** con el reprecio 2026-07-18, la vía más corta ya no es
2 Growth — hoy es **3 Scale ($3.200 × 3 ≈ $9.600)** o **6 Growth ($1.500 × 6 =
$9.000)** al piso de cada plan, o una mezcla (ej. 2 Scale + 4 Starter al piso
≈ $10.000). Con clientes en el techo de su banda variable (Starter $1.500,
Growth $3.200) el número de cuentas necesarias baja considerablemente. 1 solo
Retainer Ejecutivo ($4.500) no alcanza el piso solo — necesita un segundo
cliente Scale o Retainer. Los 2 proyectos puntuales (única excepción a "nunca vender un tool
suelto") también cruzan el piso por sí solos en el mes que se cierran, aunque como caja
puntual, no como MRR. Techo por cuenta a lo largo de su ciclo de vida (Diagnóstico → tier
→ Scale → Retainer Ejecutivo + proyectos anuales): **$150.000–250.000+/año** en la cuenta
más grande y madura de la cartera.

### Nota — Sección 10: vertical de auditoría (comprador distinto, mayor poder de precio)

Por qué se añade y por qué mueve el techo: las secciones 1–7 le venden al **comprador de
operaciones** (ahorro discrecional; si no compra, sigue igual). La Sección 10 le vende al
**comprador de cumplimiento**, cuyo gasto es **obligatorio y con fecha límite** — nadie pasa
una auditoría sin preparar la evidencia, y el cambio de PCAOB AS 1215 reduce la ventana de
ensamblaje de papeles de trabajo de 45 a 14 días desde dic-2026. Es la única sección del
catálogo con demanda **no discrecional**, y su comprador (controller/CFO, o una firma de
auditoría) paga por hora 2–4× lo que el gerente de inventario de las secciones 1–7.

**Benchmarks verificados (jul-2026) que anclan el precio:**

| Referencia | Cifra verificada |
|---|---|
| Auditoría de EEFF de empresa privada | **$7.000–50.000** (pyme <$5M: $7–15k; mediana $5–50M: $15–35k). El **inventario complejo añade horas facturables** — es exactamente el costo que la Sección 10 reduce |
| Readiness SOX 404 | Mid-market **$15.000–75.000**; pre-IPO completo **$75.000–250.000** sobre el periodo (12–24 meses) |
| Que las firmas **sí** pagan por tooling de auditoría | DataSnipper $64–175/usuario/mes (mín. 5 asientos → **$3.840–10.500/año**); AuditBoard **$40.000–150.000/año** por contrato |

**Precio propuesto (hipótesis a validar — ver condiciones abajo):** **$6.000–15.000 por ciclo
de auditoría**, recurrente cada año, vendido a la gerencia como *"preparamos el papel de trabajo
de inventario/COGS — muestreo estadístico defendible, tie-out al libro mayor y linaje con hash
por número; tu auditor factura menos horas y tú pasas la revisión sin sobresaltos"*. Es una
fracción del honorario total de auditoría, lo que lo hace fácil de aprobar. **No viola la regla
de oro:** es un paquete de 4 tools, no un tool suelto.

- **Add-on SOX** (fase 2 del diseño: matriz de riesgo-control + test de controles):
  **$2.000–4.000/mes** sobre Growth/Scale, solo empresas públicas/pre-IPO — MRR de nicho pero
  ticket alto.
- **Efecto en el portafolio:** el techo por cuenta madura sube **$6k–15k/año por cada cuenta que
  se audita**; una sola temporada de auditoría con 2 clientes cruza el piso de $8.000/mes de ese
  mes. La variante de más techo —vender capacidad *white-label* a firmas mid-tier (rango
  AuditBoard $40k–150k/año)— existe pero es venta enterprise lenta, no la ruta fraccional corta
  del resto del brief.

**Dos condiciones antes de facturar esto (por eso es hipótesis, no oferta activa):**

1. **No existe el código todavía.** Hoy es memo + diseño
   ([`AUDIT_EVIDENCE_MEMO.md`](AUDIT_EVIDENCE_MEMO.md),
   [`AUDIT_EVIDENCE_DESIGN.md`](AUDIT_EVIDENCE_DESIGN.md)). Falta implementar
   `src/audit_evidence.py` + `jobs/audit_evidence_job.py` — esfuerzo acotado que reutiliza los
   patrones de `reconciliation_job` (tie-out) y `acceptance_sampling_job` (muestreo). Es
   probablemente la mejor relación esfuerzo/precio del backlog: matemática que ya casi existe,
   vendida a la tarifa más alta que soporta el catálogo.
2. **Linchpin no firma la auditoría.** El contrato guiado lo encierra por diseño
   (HANDOFF/ESCALATED, nunca EXECUTED): se vende *preparación de evidencia* del lado de la
   gerencia, o capacidad white-label, **no** la opinión del auditor. Antes de escribir código hay
   que resolver con un auditor practicante las tablas AICPA exactas y a quién se le vende dentro
   del engagement (memo §7, preguntas Q1–Q2).

## Qué hacer primero

**30 días:** (1) Publicar el Diagnóstico de Arranque ($1.500–2.500, 2 semanas) y el tier
Starter ($900/mes, piso variable) como las dos ofertas de entrada — el modelo "assessment + sprint +
retainer" es el patrón recomendado en pricing de consultoría SC. (2) Publicar el módulo
Odoo (gratis o barato) como anzuelo y ejecutar las submissions ya preparadas en
`GTM_SUBMISSIONS.md`. (3) Conseguir los 2 primeros clientes vía Upwork/red directa aunque
sea con el Diagnóstico a precio bajo: el objetivo es el caso de estudio con ahorro medido
en $ que convierte a Starter o directo a Growth.

**90 días:** (4) Con 1–2 casos con ahorro cuantificado, vender en inglés a marcas Shopify/DTC de $1–10M (el segmento que ya paga $3k–7k/mes por roles fraccionales) posicionándote como *fractional inventory/supply-chain operator*, no como freelancer. (5) En español, atacar pymes con Odoo en LatAm/España vía el módulo + partners. (6) Ofrecer pricing por valor (10–20% del ahorro año 1) en cuentas con inventario grande. **No** invertir en SaaS self-serve ni esperar ingresos del MCP todavía.

**Riesgo principal:** la competencia no es otro software, es "ChatGPT + Excel" y el status quo. La defensa es la que Kern ya tiene: entregables con QA, fundamentados en literatura, con writeback seguro a Odoo/Excel — y un humano que firma. Vender el resultado (ahorro, servicio, stockouts evitados), nunca las horas.

---

### Fuentes principales (verificadas 3-0 o 2-1)

- fractionaljobs.io — tarifas y estructura de retainers fraccionales e-commerce/CPG
- fractionus.com — encuesta 2025: $213/h promedio; 69,5% en retainers $5k–10k/mes
- shipsigma.com — tarifas consultoría supply chain $50–500/h
- pricinglink.com — pricing por valor 10–20% del ahorro; paquetes productizados
- k38consulting.com / eightx.co — retainers fractional CFO e-commerce ($3k–15k/mes; £3k–7k UK)
- prediko.io, cogsy.com/pricing, forthcast.io, revenuegeeks.com — precios SaaS 2026
- apps.odoo.com (vendor guidelines 70/30), cloudpepper.io (estadísticas Odoo 2026), itransition.com (costos implementación)
- blog.cloudflare.com (x402), apify.com, dev.to — estado real de monetización MCP 2026
- malt.es (barómetro España), upwork.com, ziprecruiter.com — tarifas por región
- **Sección 10 (vertical auditoría):** guzmangray.com / citrusauditgroup.com — honorarios de auditoría de empresa privada ($7k–50k); consultfees.com / assurancedimensions.com / ridgewayfs.com — readiness SOX 404 ($15k–75k mid-market; $75k–250k pre-IPO); softwarefinder.com / pricingnow.com (DataSnipper $64–175/usuario/mes) y vendr.com (AuditBoard $40k–150k/año) — prueba de disposición a pagar por tooling de auditoría
