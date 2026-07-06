# Puente Finanzas y Marketing — roadmap técnico

> Complementa [`CAPABILITY_EXPANSION_PLAN.md`](CAPABILITY_EXPANSION_PLAN.md) (roadmap general
> de capacidades) y [`MONETIZATION_BRIEF.md`](MONETIZATION_BRIEF.md) (empaquetado comercial).
> Este documento responde una pregunta más estrecha: supply chain, finanzas y marketing chocan
> constantemente en la práctica (marketing quiere vender lo que no hay; finanzas quiere el
> capital que el stock atrapa) — ¿qué de ese puente **ya existe** en el código de Linchpin,
> qué es una **brecha real**, y qué dice la literatura canónica sobre cómo cerrarla?
>
> Metodología: grounding directo en el código (`src/`, `jobs/`, `scm_agent/tools.py`) +
> investigación web profunda con verificación adversarial de cada afirmación metodológica
> (jul 2026). Solo se citan afirmaciones que sobrevivieron la verificación; el detalle de
> confianza de cada una está en su cita.

---

## 1. Puente de Finanzas — del ciclo de caja estático al forecast de caja rotativo

### Estado actual (ya construido, no es una brecha)

- `src/working_capital.py`: `working_capital()` y `cash_release_plan()` ya calculan el ciclo
  cash-to-cash (DIO+DSO−DPO), el capital de trabajo neto, y el cash liberado por palanca
  (mejorar DIO/DSO o extender DPO).
- Ya está enganchado como **lente opcional** dentro del tool `cost_to_serve`
  (`jobs/cost_to_serve_job.py`, ~L131-137): cuando el cliente pasa parámetros DIO/DSO/DPO, el
  reporte de cost-to-serve ya incluye el cash liberado. Esto ya es vendible hoy, sin construir
  nada — ver `MONETIZATION_BRIEF.md`.
- Lo que entrega: una **foto fija** del ciclo de caja (un promedio en días), no una serie
  temporal.

### La brecha real

No existe un **forecast de caja rotativo** (semana a semana, horizonte móvil) atado al
calendario real de compromisos de compra que `inventory_optimization` / `odoo_replenishment` /
`excel_replenishment` ya calculan, ni a los cobros esperados de cuentas por cobrar (AR).

### Metodología canónica

- El **pronóstico rotativo de 13 semanas** (13-week rolling cash forecast, TWCF) es el estándar
  de facto de tesorería de corto plazo, con la lista de partidas del método directo (cobros de
  clientes, compras, nómina, impuestos, intereses, deuda) documentada por **AFP** (Association
  for Financial Professionals, "Selecting a Cash Forecasting Methodology") — la fuente
  profesional no comercial más sólida encontrada.
- Se construye casi siempre con el **método directo**: caja inicial + cobros AR esperados por
  semana − pagos AP/PO programados por semana − gastos fijos = caja final, en formato rolling
  (se descarta la semana cerrada y se agrega una nueva al final del horizonte). El **método
  indirecto** (derivado de P&L/balance proyectados, ajustando por partidas no monetarias y
  capital de trabajo) se usa para horizontes largos/estratégicos, no operativos semana a semana.
  — GTreasury/Ripple Treasury, corroborado independientemente por AFP, Deloitte y Kyriba
  (confianza media: contenido de práctica profesional/vendor, no paper académico, pero
  convergente entre múltiples fuentes independientes).
- Valor incremental sobre el ciclo cash-to-cash estático: expone el **timing real** semana a
  semana — incluyendo baches de liquidez intra-mes que un promedio de ciclo en días no puede
  mostrar.
- Por esto (junto con su dependencia directa de compromisos de compra y cobros esperados) se ha
  vuelto el **entregable central** que las firmas de CFO fraccional venden a marcas de
  e-commerce como puente entre supply chain y tesorería (retainers $3.000–15.000/mes, ver
  `MONETIZATION_BRIEF.md`).

### Build vs. buy

Se **construye** en el repo — la mecánica es una suma rolling simple, auditable, mismo patrón
determinista que `working_capital.py`:

- `src/cash_forecast.py` nuevo: `rolling_cash_forecast(opening_cash, weekly_receipts,
  weekly_disbursements, weeks=13)` — puro/determinista, mismo estilo de dataclasses congeladas.
- `weekly_disbursements` se alimenta de los compromisos de compra que `inventory_optimization` /
  `purchase_order.py` ya calculan (las PO recomendadas, con su fecha).
- `weekly_receipts` se alimenta de una proyección simple de AR (ventas del forecast × término
  de cobro del cliente) — no requiere un motor nuevo, solo el dato de término de pago.
- Un `jobs/cash_forecast_job.py` + un tool nuevo (o una extensión de `cost_to_serve`) que junte
  ambos.

### Cómo se vende

Es el add-on de finanzas del tier **Growth** ($4.000/mes) en `MONETIZATION_BRIEF.md`
(`cost_to_serve` ya está en su alcance). Con esto, Linchpin puede vender el "board pack" de 13
semanas de caja que hoy solo vive en retainers fraccionales de finanzas puros.

---

## 2. Puente de Marketing — de "tenés stock muerto" a "vendelo así, en este plazo, y recuperás $X"

### Estado actual (ya construido, no es una brecha)

- `src/excess_obsolete.py`: `classify_excess_obsolete()` ya clasifica sano/excedente/muerto y
  dimensiona el cash en riesgo (`excess_value`). `jobs/excess_obsolete_job.py` devuelve la
  clasificación + una acción en **texto** ("liquidate / return / draw down") — nunca calcula un
  precio.
- `src/pricing.py`: `markdown_price(remaining_units, periods_left, fit, current_price)` **ya
  existe** y resuelve el precio que agota el stock remanente en N periodos dada una curva de
  elasticidad constante (`q(p) = scale · p^elasticidad`). Solo lo usa el propio tool `pricing`.
- `jobs/fefo_job.py` tiene un `markdown_price_pct` (**% fijo configurable**, no una curva de
  elasticidad) y solo aplica a perecederos (FEFO) — es un mecanismo distinto y más simple.

### La brecha real

Ningún job cruza `classify_excess_obsolete()` con `markdown_price()` para producir un
**calendario de liquidación por SKU** (precio + semanas para vaciar + $ recuperado vs. escrito a
cero) para stock **no perecedero**.

### Metodología canónica

- La cadena teórica: **Gallego & van Ryzin (1994)**, *Management Science* 40(8):999-1020, da el
  marco de dynamic/clearance pricing bajo stock y horizonte finitos — pero en su forma original
  es control estocástico continuo, no una ecuación determinística de una sola pasada. La fórmula
  simple "resolver p tal que demanda proyectada = stock remanente" (la que ya implementa
  `markdown_price()`) es el **caso límite/fluido** de ese modelo cuando inventario y horizonte
  crecen — no la formulación completa (confianza alta).
- **Smith & Achabal (1998)**, *Management Science* 44(3):285-300, DOI 10.1287/mnsc.44.3.285 — la
  referencia retail-específica — usa un modelo **más rico**: `d(p,I,t) = d_p(p)·d_I(I)·s(t)`,
  donde `d_I(I)` es un "efecto de agotamiento de inventario" (cuando cae el stock, faltan
  tallas/colores/variantes y la venta cae aunque el precio no cambie). Esto es exactamente lo
  que **le falta** a la simplificación que ya tiene `src/pricing.py`.
- **Feng & Gallego (1995)** y **Bitran, Caldentey & Mondschein (1998)**: el markdown real es
  **multi-etapa** (timing óptimo de varios recortes escalonados), no de una sola pasada.
- **Caro & Gallien (2012)**, *Operations Research* 60(6):1404-1422 — Zara reemplazó un proceso
  manual por forecasting + optimización precisamente porque la elasticidad simple no captura
  bien el clearance de fast-fashion; el cambio subió ~6% el revenue de liquidación. Además, la
  literatura de "strategic/rational consumers" muestra que el propio anuncio del calendario de
  descuentos desplaza la demanda que la elasticidad constante asume fija (los clientes posponen
  la compra anticipando el markdown).
- Cruzar clasificación de exceso/obsolescencia con elasticidad para generar un calendario de
  liquidación por SKU es **práctica real desplegada a escala**, no solo teoría: **Chen et al.
  (2021)**, *INFORMS Journal on Applied Analytics* 51(1):76-89, DOI 10.1287/inte.2020.1065 —
  Walmart desplegó en **todas** sus tiendas de EE.UU. un sistema de markdown multiobjetivo cuyo
  primer objetivo explícito es "liquidar el exceso de inventario para una fecha específica"
  (confianza alta). Productos comerciales (RELEX, Churchill Systems) venden exactamente esto
  como feature de catálogo.

### Build vs. buy

Se **construye** — el 90% del trabajo ya existe y está testeado:

1. `jobs/markdown_liquidation_job.py` nuevo que: (a) corre `classify_excess_obsolete()`, (b)
   para cada SKU excedente/muerto con historial de precio, corre `estimate_elasticity()` +
   `markdown_price()` de `src/pricing.py`, (c) para SKUs sin historial de precio, aplica un
   descuento por defecto documentado como heurística (no como óptimo), (d) devuelve precio +
   semanas para vaciar + $ recuperado vs. escrito a cero, rankeado por $ en riesgo.
2. **Limitación a documentar honestamente en el job**: esto es el caso límite determinístico de
   Gallego-van Ryzin (heurística validada en la práctica), no un motor multi-etapa con efecto de
   agotamiento como el de Walmart/Zara — pero es muy superior a "liquidar" en texto plano, y
   cuesta ~1 día de trabajo porque ambos motores ya existen.

### Cómo se vende

No es un tier nuevo — `excess_obsolete` y `pricing` ya están en el alcance de Starter/Growth en
`MONETIZATION_BRIEF.md`. Esto convierte el mismo entregable de "diagnóstico" a "plan con plata",
subiendo el valor percibido al mismo precio.

### Nota aparte: promotional demand uplift (relacionado, pero NO construir todavía)

Investigado en paralelo porque es adyacente, con conclusión explícita de **no priorizarlo**:

- Modelo canónico: **Cooper et al. (1999)** "PromoCast™", *Marketing Science* 18(3):301-316 —
  regresión transversal con ~67 variables por combinación SKU-tienda (tipo/profundidad de
  promoción + desempeño histórico). **Van Donselaar et al. (2016)**, *International Journal of
  Production Economics* 172:65-75 — para perecederos, muestra que los efectos de
  umbral/saturación **no son universales** entre categorías: el "lift" no es un número único y
  estable (confianza alta).
- Riesgo de no modelarlo, cuantificado: tasa global de quiebre de stock ~8,3% (Corsten & Gruen /
  ECR Europe 2003), con artículos en promoción sufriendo el doble de quiebres que los no
  promocionados; errores de forecast de 30%-140% del volumen durante campañas de precio
  (Huchzermeier & Iyer 2006). El caso P&G Pampers del efecto bullwhip (**Lee, Padmanabhan &
  Whang 1997**, *Sloan Management Review* 38:93-102) muestra el mecanismo concreto de sobre-stock
  post-promo cuando el forward buying del retailer no está coordinado con un modelo de demanda
  promocional.
- Por qué no construirlo ahora: a diferencia del markdown (donde ambos motores ya existen), acá
  no hay building blocks reutilizables, y requiere datos históricos de promoción estructurados
  que la mayoría de clientes pyme no tiene. Dejarlo en el roadmap de fase 2.

---

## 3. S&OP como pegamento — de la reconciliación de unidades a la reconciliación financiera + marketing

### Estado actual (ya construido, no es una brecha)

- `src/sop.py`: `run_sop_cycle()` ya modela las tres estrategias clásicas de aggregate planning
  (chase/level/hybrid) con trade-offs de costo/servicio/inventario cuantificados
  (`PlanEvaluation`: holding/shortage/capacity-change cost, fill rate, peak/average inventory).
  El núcleo analítico es sólido y determinista.
- La demanda que alimenta el ciclo es un input dado (un plan de demanda por período) — sin
  ajuste explícito por marketing.

### La brecha real

`run_sop_cycle()` no toma un calendario de promociones/marketing como ajuste a la demanda en la
etapa de "demand review" del ciclo.

### Metodología canónica

- S&OP tradicional e IBP (Integrated Business Planning) comparten el **mismo núcleo formal**: un
  ciclo mensual de 5 etapas — product/portfolio review, demand review, supply review,
  integrated/financial reconciliation, management business review — documentado por **Oliver
  Wight** y corroborado por el **ASCM/APICS Supply Chain Dictionary** y **Grimson & Pyke (2007)**,
  *International Journal of Logistics Management*.
- La diferencia formal **no es inventar etapas nuevas**: es el grado de rigor de dos de ellas:
  - El **demand review** ya incorpora en el diseño canónico (Lapide; Oliver Wight) el calendario
    de promociones, lanzamientos y cambios de precio como **ajuste explícito** al forecast
    estadístico baseline — esto confirma que el puente marketing→demanda no es una invención de
    este documento, es la etapa formal donde siempre debió vivir.
  - La **Integrated Reconciliation** es el punto **exacto** — antes del Management Business
    Review — donde el plan de unidades se traduce a P&L, balance y flujo de caja, y se concilia
    contra presupuesto (fuente: supplychainmath.com IBP Guide + Oliver Wight, confianza media).
    Es aquí, no en demand ni supply review, donde encaja el puente de working-capital/cash-flow
    de la sección 1.
- IBP = S&OP donde las etapas 4 (reconciliación financiera con impacto en P&L/balance/cash) y 5
  (MBR con autoridad de decisión ejecutiva) se ejecutan con disciplina y alcance estratégico
  plenos — Grimson & Pyke identifican la reconciliación financiera formal (vs. informal/ausente)
  como el marcador de la madurez alta que la literatura posterior bautiza IBP.

### Build vs. buy

Se **construye**, cambio pequeño y no invasivo:

- `run_sop_cycle()` recibe un parámetro opcional de ajuste de demanda por período (un calendario
  de multiplicadores/deltas por período — la "promo calendar") aplicado al plan de demanda de
  entrada **antes** de correr chase/level/hybrid. Mantiene compatibilidad con la firma actual.
- Conectar la salida de `sop.py` con `financial_kpis.py` y con el forecast de caja de la sección
  1 (cuando exista) para que `PlanEvaluation` incluya explícitamente el impacto en $ / cash —
  esto es, literalmente, la Integrated Reconciliation de IBP.

### Cómo se vende

Es el ingrediente que sube el tier **Growth** ($4.000/mes, `sop` no está en su alcance) al tier
**Scale** ($7.500/mes, `sop` sí está) en `MONETIZATION_BRIEF.md` — confirma la lógica ya
documentada: `sop` es el salto de "analista de inventario" a "operador de supply chain
fraccional" con una reunión mensual ejecutiva real.

---

## Prioridad de implementación

1. **Cruce markdown + E&O** (sección 2) — el más barato (~1 día), ambos motores ya existen y
   están testeados, sube el valor percibido sin crear un tier nuevo.
2. **Forecast de caja rotativo** (sección 1) — esfuerzo medio, alto valor percibido, vendible de
   inmediato como el add-on de finanzas del tier Growth.
3. **S&OP con input de marketing** (sección 3) — mayor esfuerzo (cambio de firma + requiere que
   el cliente tenga un calendario de promociones estructurado), pero es el salto de tier más
   grande (Growth → Scale).

**No construir todavía:** el modelo completo de promotional demand uplift — no hay building
blocks reutilizables y requiere datos que la mayoría de clientes pyme no tiene. Queda en el
roadmap de fase 2 (ver también `CAPABILITY_EXPANSION_PLAN.md` para el roadmap general de
capacidades).

---

## Bibliografía citada (verificada por revisión adversarial, jul 2026)

**Cash forecasting**
- AFP (Association for Financial Professionals) — *Selecting a Cash Forecasting Methodology*.
- GTreasury / Ripple Treasury — *Differences between Direct and Indirect Cash Forecasting*
  (contenido de práctica profesional, corroborado independientemente por AFP, Deloitte y
  Kyriba).
- Growth Lab Financial, PCE Companies, Iris Finance — guías de construcción práctica del
  13-week rolling cash flow.

**Markdown / clearance pricing**
- Gallego, G. & van Ryzin, G. (1994). *Optimal Dynamic Pricing of Inventories with Stochastic
  Demand over Finite Horizons*. Management Science 40(8):999-1020.
- Smith, S.A. & Achabal, D.D. (1998). *Clearance Pricing and Inventory Policies for Retail
  Chains*. Management Science 44(3):285-300. DOI 10.1287/mnsc.44.3.285
- Feng, Y. & Gallego, G. (1995); Bitran, G., Caldentey, R. & Mondschein, S. (1998) — markdown
  óptimo multi-etapa.
- Caro, F. & Gallien, J. (2012). *Clearance Pricing Optimization for a Fast-Fashion Retailer*.
  Operations Research 60(6):1404-1422.
- Chen, Y. et al. (2021). *A Multiobjective Optimization for Clearance in Walmart
  Brick-and-Mortar Stores*. INFORMS Journal on Applied Analytics 51(1):76-89.
  DOI 10.1287/inte.2020.1065
- RELEX Solutions, Churchill Systems — productos comerciales de markdown optimization
  (evidencia de práctica de industria).

**Promotional demand uplift**
- Cooper, L.G. et al. (1999). *PromoCast™: A New Forecasting Method for Promotion Planning*.
  Marketing Science 18(3):301-316.
- van Donselaar, K.H., Peters, J., de Jong, A. & Broekmeulen, R.A.C.M. (2016). *Analysis and
  forecasting of demand during promotions for perishable items*. International Journal of
  Production Economics 172:65-75.
- Fisher, M. & Raman, A. (2010). *The New Science of Retailing*. Harvard Business Press.
- Lee, H.L., Padmanabhan, V. & Whang, S. (1997). *The Bullwhip Effect in Supply Chains*. Sloan
  Management Review 38:93-102.
- Corsten, D. & Gruen, T. / ECR Europe (2003) — tasa global de quiebre de stock.
- Huchzermeier, A. & Iyer, A. (2006) — error de forecast en campañas de precio.

**S&OP / IBP**
- Oliver Wight — *Integrated Business Planning*.
- ASCM/APICS *Supply Chain Dictionary*.
- Grimson, J.A. & Pyke, D.F. (2007). *Sales and operations planning: an exploratory study*.
  International Journal of Logistics Management.
- Lapide, L. — sobre el demand review y el calendario de promociones en S&OP/IBP.
- supplychainmath.com — *IBP Guide*.
