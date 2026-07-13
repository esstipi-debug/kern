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

## Estructura de empaquetado comercial (8 secciones vendibles por separado)

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
| 2 | **Starter** — Fundamentos de Inventario | $2.000/mes | Mensual, alcance fijo | 8: `forecast`, `abc_xyz`, `whatif`, `inventory_optimization`, `newsvendor`, `excel_replenishment`, `cycle_count`, `data_quality` | E-commerce/distribuidor mono-almacén, $1–10M, compra "a ojo" en Excel |
| 3 | **Growth** — Operación Completa de SC | $4.000/mes | Mensual + QBR trimestral | 26 (todo lo anterior + `multi_echelon`, `ddmrp`, `simulation`, `drp`, `odoo_replenishment`, `reconciliation`, `fefo`, `sourcing`, `landed_cost`, `acceptance_sampling`, `pricing`, `cost_to_serve`, `learning_curve`, `returns`, `risk`, `dea`) | Empresa en crecimiento, multi-almacén/canal, con o migrando a ERP (Odoo) |
| 4 | **Scale** — Red, S&OP y Mando Ejecutivo | $7.500/mes | Quincenal + S&OP mensual | Las 35 tools del catálogo completo (+ `facility_location`, `transportation`, `warehouse_layout`, `slotting`, `queuing`, `scheduling`, `sop`, `earned_value`, `leadership_chain`) | Mid-market con red real (2+ plantas/CDs) |
| 5 | **Retainer Ejecutivo Fraccional** | $9.000–12.000/mes | Mensual + cadencia semanal + escalamiento con SLA | Mismas 35 tools de Scale — la diferencia es gobierno, no capacidad | Cliente maduro (6–18 meses en Scale), mandato de VP/COO fraccional |
| 6 | **Proyecto de Red, Almacén y Operación** | $8.000–18.000 único | Único, 4–8 semanas | 6: `facility_location`, `transportation`, `warehouse_layout`, `slotting`, `queuing`, `scheduling` | Inflexión estructural: nueva bodega, rediseño de red/almacén |
| 7 | **Proyecto de Sourcing y Costo de Importación** | $5.000–10.000 único | Único, recurrible trimestral/anual | 3: `sourcing`, `landed_cost`, `acceptance_sampling` | Importadores / manufactura offshore |
| 8 | **Sprint de Liquidación** | 10–20% del cash recuperado, piso $1.500 (contingente, no fijo) | Único, sprint 2–3 semanas | 3-4: `data_quality`, `excess_obsolete`, `markdown_liquidation` (+ `pricing` opcional) | Stock muerto/excedente ya diagnosticado, decidido a liquidar, resiste pagar un fee fijo por algo no recuperado |

Nota sobre la sección 8: es la única con **precio contingente** — el
honorario se calcula sobre el cash efectivamente recuperado (nunca sobre una
proyección), con `src/contingent_fee.py` como calculadora única. Abre la
puerta a **Starter** para el cliente que no quiere que el stock muerto vuelva
a acumularse.

**Camino a $8.000/mes:** 2 clientes Growth ($4.000 × 2) es la ruta más corta y el mix
recomendado por defecto. Alternativas: 4 Starter, 1 Scale + 1 cliente pequeño, o 1 solo
Retainer Ejecutivo. Los 2 proyectos puntuales (única excepción a "nunca vender un tool
suelto") también cruzan el piso por sí solos en el mes que se cierran, aunque como caja
puntual, no como MRR. Techo por cuenta a lo largo de su ciclo de vida (Diagnóstico → tier
→ Scale → Retainer Ejecutivo + proyectos anuales): **$150.000–250.000+/año** en la cuenta
más grande y madura de la cartera.

## Qué hacer primero

**30 días:** (1) Publicar el Diagnóstico de Arranque ($1.500–2.500, 2 semanas) y el tier
Starter ($2.000/mes) como las dos ofertas de entrada — el modelo "assessment + sprint +
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
