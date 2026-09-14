# Qué hacer con la información de la competencia: guía de referencia

## Fuentes académicas consultadas

### Libros y papers descargados en `knowledge/scm-books/`

| Archivo | Fuente | Tema clave |
|---|---|---|
| `homburg-totzek-rules-for-successful-competitive-pricing.pdf` | Homburg & Totzek (2012), IMU Research Insights | 9 reglas para pricing competitivo en B2B |
| `wharton-smart-pricing.pdf` | Raju & Zhang, Wharton | Competencia-based pricing vs. cost-plus; price wars |
| `competitive-pricing-online-markets-literature-review.pdf` | Gerpott & Berends (2022), J. Revenue & Pricing Mgmt | 132-paper review: framework interdisciplinario |
| `jungmann-intelligent-pricing-b2b-thesis.pdf` | Aalto (2024), T. Jungmann | Sistemas de pricing inteligente en B2B |
| `price-management-springer.pdf` | Simon & Fassnacht | Price management: strategy → analysis → decision → implementation |

---

## 1. Qué ACCIONABLE se puede extraer del análisis de competencia

### De "The Value of Competitor Information" (Kim, 2025, Management Science)

**Hallazgo clave**: Un field experiment con 3,218 firmas de productos de cuidado personal mostró que:
- Al recibir información de precios de competidores, **el 50% de las firmas alinearon sus precios** con los competidores (en lugar de diferenciarse).
- Las empresas más desalineadas en precio/calidad son las que más se ven impactadas.
- El efecto es **performance-enhancing**: cambiar de comportamiento basado en info de competencia mejora resultados.

**Recomendación accionable**: La información de competencia no es solo observación pasiva — **accionar sobre ella (ajustar precios hacia el mercado)** mejora la rentabilidad. La barrera principal no es técnica, es **de atención gerencial**.

### De "Competition-Based Dynamic Pricing" (Fisher, Gallino, Li, 2015)

Un retailer responde al monitoreo de competencia respondiendo 4 preguntas:
1. **¿Debemos responder?** — Solo si nuestra posición es mala Y la corrección es ejecutable
2. **¿A quién responder?** — Competidores con relevancia significativa (no todos)
3. **¿Cuánto responder?** — Usando elasticidad de demanda no sesgada (no cost-plus)
4. **¿En qué productos?** — Priorizar SKUs donde la competencia explica >X% de la variación de precio

**Resultado**: **11% de aumento en revenue** manteniendo margen sobre el target, validado con field experiment de 5 semanas.

### De "Pricing with Bandits in the Long-tail" (Israeli & Anderson, 2025)

**Hallazgo**: El monitoreo de competidores es **especialmente valioso para productos de bajo volumen** (long-tail). Un retailer observó que:
- El **63% de las respuestas** a cambios de precio del competidor ocurren en los primeros 2 días.
- Monitorear un competidor **más grande** (mayor base de clientes) es más valioso que monitorear uno más pequeño.
- Frecuencia de monitoreo ↑ → revenue ↑ (relación positiva).
- Monitoreo y price exploration son **sustitutos** (no complementarios): si exploras mucho, monitoreas menos.

**Recomendación accionable**: Prioriza monitoreo de competidores grandes. Responde rápido (1-2 días). Para SKUs de bajo volumen, el monitoreo es más valioso que la exploración.

### De la literature review de Gerpott & Berends (2022)

**Framework interdisciplinario** (4 dimensiones clave):
1. **Product similarity**: identical vs. quality-differentiated — afecta si el precio es la única variable
2. **Product durability**: perishable vs. durable — afecta el horizonte de optimización
3. **Time dependence**: static vs. dynamic — **recomiendan dinámico para e-commerce** (demanda y competencia varían)
4. **Market structure**: monopolistic/duopolistic/oligopolistic/perfect — **online = oligopolistic o many-firms imperfect**

**Recomiendan**: "firms should develop dynamic pricing models in most e-commerce situations. Static models are only appropriate in market constellations with little time-varying demand and competitor behavior."

---

## 2. Decisiones accionables del código (Kern) vs. teoría

| Teoría académica | Implementación en Kern (`src/pricing_intel/`, `jobs/`) |
|---|---|
| "Competitor price monitoring provides valuable demand signal" (Israeli & Anderson) | `jobs/price_watch.py` — discovery-assisted, read-only, robots.txt-limited |
| "Filter agotados/preorders — no restringen precio real" | `jobs/price_intelligence.py` → `eligible_competitor_prices()` excluye stock-outs |
| "Monitorear competidores más grandes es más valioso" | `CompetitorOffer` model rank-ordera por market share |
| "Respuesta rápida (1-2 días)" | `run_pricing_intel.py` monitor recurrente |
| "Monitoreo + exploración son sustitutos" | `CircuitBreaker` en `acquire/base.py` regula frecuencia |
| "Dynamic pricing > static para e-commerce" | `run_repricing.py` — repricing playbook con precios objetivo |
| "No proponer movimiento si ya en optimal band" | `sku_entity_type()` con `no_change` como status propio (no filtered by `ok`) |
| "Margin floor AFTER deciding to match" | `revisar_margen` — separado de `igualar_precio`, actúa después |
| "11% revenue ↑ with margin guardrails" | `pricing_guardrails.gate_price_changeset` — filtra por Omnibus 30-day-lowest y MAP antes de escribir |

---

## 3. Qué HACER con la información de competencia (resumen accionable)

### Paso 1: Recolección (qué obtener)
- **Precios de competidores** por SKU
- **Stock/disponibilidad** (agotados no restringen)
- **Recency/freshness** dentro del SLA (fechas de observación)
- **Confianza en el match** (product matching reliability)
- **Market share del competidor** (más grande = más señal)

### Paso 2: Análisis (qué calcular)
- **Position index**: percentile de tu precio vs. competencia elegible
- **Elasticidad residual**: variación demanda / variación precio (usa stockouts como instrumento para endogeneidad)
- **Competitor significance**: qué % de la variación de tu demanda explica cada competidor
- **Band de precio óptimo**: propagado del CI de elasticidad → `[low, high]`

### Paso 3: Decisión (qué decidir)
- **no_change** si el precio actual está dentro de la banda (evita movimientos sin evidencia)
- **igualar_precio** si fuera bajo de la banda Y la posición es mala
- **oportunidad_subir** si el precio actual está arriba de la banda
- **revisar_margen** si `igualar_precio` propondría cortar por debajo del costo → elevar el piso

### Paso 4: Validación (qué verificar)
- **Legal guardrails**: Omnibus 30-day-lowest, MAP — `pricing_guardrails.gate_price_changeset`
- **Cross-area impact**: ¿el price cut afecta negocios en otros mercados?
- **Identification caveat**: ¿hubo variación de precio real? (no solo precisión del CI)

---

## 4. Referencias para el grafo de teoría (L3 books graph)

Estas fuentes deben indexarse en el **books graph** (necesita backend LLM — Kimi/Gemini):

1. **Homburg & Totzek (2012)** — "Rules for Successful Competitive Pricing in Business Markets"
   - 9 reglas empíricas de pricing competitivo (survey 230 firmas B2B)
   - Relevante para: `price_priority._assign_action`, `pricing_guardrails`

2. **Gerpott & Berends (2022)** — "Competitive pricing on online markets: a literature review"
   - 132 papers, framework interdisciplinario (economía/marketing/operaciones)
   - Relevante para: clasificación de mercado, static vs. dynamic pricing

3. **Israeli & Anderson (2025)** — "Pricing with Bandits in the Long-tail"
   - Monitoreo de competencia como señal de demanda para long-tail
   - Relevante para: `price_watch.py`, `CircuitBreaker`

4. **Fisher, Gallino & Li (2015)** — "Competition-Based Dynamic Pricing"
   - Field experiment: 11% revenue increase
   - Relevante para: `run_repricing.py`, `price_optimizer.py`

5. **Raju & Zhang (Wharton)** — "Smart Pricing"
   - Competencia-based pricing vs. cost-plus; price wars
   - Relevante para: filosofía de pricing, no seguir ciegamente a competencia

6. **Simon & Fassnacht** — "Price Management"
   - 4 fases: strategy → analysis → decision → implementation
   - Relevante para: arquitectura general del pricing system

7. **Nagle & Müller** — "The Strategy and Tactics of Pricing" (6th ed, 2018)
   - FREE download: `nagle-muller-strategy-and-tactics-of-pricing.pdf` (~4.2 MB)
   - Capítulos: Competitive Intelligence, Price Competition, Proactive vs Reactive
   - Relevante para: filosofía de pricing estratégico, anticipar reacciones de competencia

8. **Fleisher & Bensoussan** — "Business and Competitive Analysis" (2nd ed)
   - FREE download: `fleisher-business-and-competitive-analysis.pdf` (~700 KB)
   - Métodos: Actionable SWOT, Competitive Positioning, Cash Flow Analysis, FAROUT
   - Relevante para: análisis estructurado de competencia, frameworks de análisis
