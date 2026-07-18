# Kern como nivel de referencia SCM

### Mapa de capacidades reales del repositorio contra SCOR Digital Standard, ISO 9001/28000 y las certificaciones CSCP · CPIM · CLTD · SCPro · CPSM

> **Propósito.** Documento de referencia técnica —no de marketing— que fija, para cada práctica de la industria, (a) qué exige el estándar, (b) qué capacidad de Kern ya lo cubre y con qué módulo/modelo, (c) dónde hay laguna, y (d) el mínimo aceptable auditable. Sirve como criterio para que cualquier empresa implemente o audite su cadena con Kern.

---

## 0. Nota de método (grounding y honestidad)

Todo lo marcado **EXISTE HOY** está verificado leyendo el código fuente del repositorio; se cita el módulo (`src/…`, `scm_agent/…`) y el modelo matemático real que implementa, con su fuente. No se atribuye a Kern ningún modelo que no esté en el código. Lo marcado **PROPUESTA** es una evolución que **aún no existe**; se diseña reutilizando estructuras ya presentes (patrón `register()` + `jobs/…` + ancla en `citation_gate` + salida `guided`) y anclándola en fuentes que **ya están** en el grafo de conocimiento L3, para no introducir matemática inventada.

**Conteos verificados (la fuente de verdad es el código, no los docs):**

| Métrica | Valor real (código) | Qué dicen los docs | Nota |
|---|---|---|---|
| Herramientas registradas | **40** (`scm_agent/tools.py`, 40 `register()`) | README/badge: 39 · `autonomy.py`: 37 · `package_specs.py`: 35 | Los docs van por detrás del código. |
| Fuentes en el grafo de libros | **33** `source_file` distintos, 1953 nodos, 3810 aristas (`knowledge/scm-books/graph.json`) | README: "25 fuentes" | El grafo real es mayor. |
| Paquetes comerciales | **8** `PackageSpec` (`package_specs.py`) | Docstring: "7 vendibles" | `retainer_ejecutivo` reusa el catálogo de `scale`. |

**Fuentes académicas realmente embebidas en el código** (citadas en docstrings): Vandeput *Inventory Optimization* (2020) y *Data Science for Supply Chain Forecasting* (2021); Jacobs & Chase *OSCM* 15e; Chopra & Meindl 7e; Silver, Pyke & Thomas 4e; Christopher; Cooper & Kaplan (ABC); Ellram (TCO) + ICC Incoterms 2020; Ptak & Smith *DDMRP v3*; Syntetos, Boylan & Croston (2005); Hyndman & Athanasopoulos; Rezaei (BWM 2015); Gallego & van Ryzin; Simchi-Levi (TTR/TTS); Grant *Sustainable Logistics & Supply Chain*; SCOR/ASCM (cash-to-cash AM.1.1, OTIF); AICPA AU-C 530 / PCAOB AS 1105/1215/2315 (evidencia de auditoría).

---

## 1. Resumen ejecutivo

Kern es, hoy, un **motor analítico SCM de nivel de referencia en inventario, planificación, red/logística y economía de la cadena**, gobernado por una capa de orquestación con garantías que la mayoría de las suites comerciales no ofrecen: **QA que veta el entregable, grounding obligatorio a fuentes, writeback en staging con firma y rollback, y "nunca sin salida" (never-unprotected)**. En términos de temario de certificación:

- **CPIM (inventario y planificación)** — cobertura **alta**. σ de error de pronóstico como base del safety stock, distribución auto (normal/gamma), Croston/TSB para demanda intermitente, `(s,Q)`/`(R,S)`, newsvendor, multi-echelon (GSM), DDMRP, ABC-XYZ, S&OP, simulación Monte Carlo. Es el núcleo más fuerte.
- **CLTD (logística, transporte, distribución)** — cobertura **alta**. DRP, ubicación de instalación (Weiszfeld/centro de gravedad), selección de modo + CVRP (Clarke-Wright/sweep), layout de almacén, slotting (COI + afinidad), FEFO, cost-to-serve, landed cost.
- **CSCP (end-to-end, SCOR, tecnología, métricas)** — cobertura **media-alta** en procesos y métricas; **la capa de orquestación es un diferenciador**. Lagunas en sostenibilidad computacional y SRM estratégico.
- **SCPro (CSCMP: análisis de red, KPIs, proyectos)** — cobertura **media-alta**. Facility location, digital twin, DEA, cost-to-serve, KPIs financieros, EVM, what-if.
- **CPSM (ISM: compras, sourcing, contratos)** — cobertura **parcial**. Sourcing (OTIF/PPM), landed cost, selección multicriterio (BWM/TOPSIS), muestreo de aceptación. **Faltan** SRM profundo, estrategia de categoría, ciclo de vida de contratos.

**Lagunas de contenido reales** (ver §6): sostenibilidad/huella de carbono computable, SRM/segmentación de proveedores, trazabilidad/genealogía de lote, compliance regulatorio amplio, y **cuantificación de resiliencia** (agilidad SCOR). Todas son cerrables con el patrón de extensión existente (ver §7).

---

## 2. Mapa maestro — Capacidad Kern ↔ Proceso SCOR DS ↔ Certificación ↔ Módulo

SCOR Digital Standard organiza la cadena en **Plan · Source · Transform · Order/Fulfill · Return · Orchestrate**. Abajo, cada capacidad **que existe hoy** con su módulo real, el modelo que implementa y las certificaciones cuyo temario toca.

### 2.1 PLAN — planificación de demanda, inventario y S&OP

| Capacidad (tool key) | Módulo(s) real(es) | Modelo / método (fuente) | Certif. |
|---|---|---|---|
| `forecast` | `src/forecasting.py`, `forecasting_auto.py`, `forecast_metrics.py`, `forecastability.py` | MA, SES, **Croston**; **AutoETS/TSB** (Nixtla); métricas MAE/RMSE/**WAPE/MASE/RMSSE**; clasificación **SBC** por ADI≥1.32 y CV²≥0.49 (Syntetos-Boylan-Croston; Hyndman) | CPIM, CSCP |
| `inventory_optimization` | `src/eoq.py`, `safety_stock.py`, `policies.py`, `cost_optimization.py`, `risk_period.py`, `fill_rate.py` | EOQ (+descuentos por volumen), **Ss = z·σ·√τ**, `(s,Q)`/`(R,S)`, α* óptimo (Vandeput Ch.2-8); loss function normal + inversa de Andrade-Sikorski | CPIM |
| `newsvendor` | `src/newsvendor.py` | Modelo de período único, ratio crítico cu/(cu+co), discreto y normal (Vandeput Ch.11) | CPIM, SCPro |
| `multi_echelon` | `src/multi_echelon.py` | **Guaranteed-Service Model serial** + simulación base-stock por eslabón (Vandeput Ch.10) | CPIM, CSCP |
| `ddmrp` | `src/ddmrp.py`, `ddmrp_decoupling.py` | Zonas buffer rojo/amarillo/verde, Net Flow Position, lead time desacoplado ASR (Ptak & Smith *DDMRP v3*) | CPIM |
| `abc_xyz` | `src/classification.py`, `multi_criteria_classification.py` | ABC por Pareto valor-uso + XYZ por CV; matriz 9-celdas → política por celda; ABC multicriterio (TOPSIS) (Silver, Pyke & Thomas) | CPIM, CSCP |
| `sop` | `src/sop.py`, `src/sop_engine/` | Planeación agregada Chase/Level/Hybrid; motor integrado demanda→compra→coherencia (Chopra & Meindl; Heizer & Render) | CPIM, CSCP, SCPro |
| `simulation` | `src/simulation.py`, `simulation_opt.py` | **Monte Carlo** de `(R,S)`/`(s,Q)`: fill rate, backorders, **lost sales**, costos (Vandeput Ch.5, 13) | CPIM |
| `cycle_count` | `src/cycle_count.py`, `reconciliation.py` | Conteo cíclico por clase ABC + **IRA** (inventory record accuracy) (Piasecki/APICS) | CPIM, CLTD |
| `excess_obsolete` | `src/excess_obsolete.py` | Clasificación E&O por días-de-cobertura y días-sin-venta; cash en riesgo | CPIM, SCPro |
| `whatif` | `src/whatif.py` | Sensibilidad (tornado, mejor/peor caso, break-even por bisección) | SCPro, CSCP |

### 2.2 SOURCE — abastecimiento, proveedores, importación

| Capacidad | Módulo(s) | Modelo / método (fuente) | Certif. |
|---|---|---|---|
| `sourcing` | `src/supplier_scorecard.py` | Scoring **OTIF/DIFOT** + defectos **PPM** (ASCM/CIPS) | CPSM, CSCP |
| `landed_cost` | `src/landed_cost.py` | **Costo total en destino** con base arancelaria por **Incoterm 2020** (Ellram TCO; ICC) | CPSM, CLTD |
| `acceptance_sampling` | `src/acceptance_sampling.py` | Plan de muestreo simple por AQL/LTPD, **curva OC binomial** (Jacobs & Chase Ch.13) | CPSM, CPIM |
| (selección proveedor) | `src/mcdm.py` | **Best-Worst Method** (Rezaei 2015) + **TOPSIS** | CPSM, SCPro |
| (negociación/cambio) | `src/decision_support.py`, `contingent_fee.py` | Break-even de cambio de proveedor (bisección); tarjetas de decisión | CPSM |

### 2.3 TRANSFORM — producción / operaciones (cobertura ligera, honesto)

| Capacidad | Módulo(s) | Modelo / método (fuente) | Certif. |
|---|---|---|---|
| `scheduling` | `src/scheduling.py` | **Johnson** (2 máquinas), asignación **húngara** (scipy), reglas FCFS/SPT/LPT/EDD (Jacobs & Chase Ch.22) | CPIM |
| `queuing` | `src/queuing.py` | M/M/1, M/D/1, **M/M/c Erlang-C**, G/G/c (Kingman), fuente finita (Jacobs & Chase Ch.10) | CPIM, SCPro |
| `learning_curve` | `src/learning_curve.py` | Curva de aprendizaje de **Wright** Yx=K·xⁿ (Jacobs & Chase Ch.6) | CPIM |
| `earned_value` | `src/earned_value.py` | **EVM**: SV/CV/**SPI/CPI** (Jacobs & Chase Ch.4) | SCPro |
| (kanban lean) | `src/kanban.py`, `capacity_planning.py` | Nº tarjetas kanban, takt time; cushion de capacidad (Jacobs & Chase) | CPIM |

> Nota honesta: Kern **no** cubre ejecución de manufactura (MES, ruteo de planta, MRP-II detallado). Transform es su área más delgada.

### 2.4 ORDER / FULFILL — almacén, transporte, distribución, entrega

| Capacidad | Módulo(s) | Modelo / método (fuente) | Certif. |
|---|---|---|---|
| `warehouse_layout` | `warehouse/` (model, generator, qa), `src/space.py` | Twin espacial paramétrico + validación geométrica; utilización de cubo | CLTD |
| `slotting` | `src/slotting_affinity.py`, `space.py` | **COI** (Cube-per-Order Index, Kallina & Lynn 1976) + **slotting por afinidad** (lift/market-basket, componentes conexos) | CLTD |
| `fefo` | `src/lots/fefo.py`, `expiry.py` | **FEFO**, informe de caducidad, markdown-vs-scrap | CLTD |
| `transportation` | `src/logistics/modes.py`, `freight.py` | Selección de **modo** parcel/LTL/FTL/intermodal + **break-even LTL→FTL**; cost-to-serve por lane (Vandeput & Christopher) | CLTD |
| `vehicle_routing` | `src/logistics/routing.py` | **CVRP**: **Clarke-Wright savings (1964)** + **sweep (Gillett & Miller 1974)** + nearest-neighbor (Ballou) | CLTD, SCPro |
| `facility_location` | `src/facility_location.py` | **Centro de gravedad** + **Weiszfeld** (1-mediana ponderada) (Heizer, Ballou) | CLTD, SCPro |
| `drp` | `src/drp.py` | **DRP** malla temporizada + roll-up multi-eslabón al DC central (Vollmann MPC) | CLTD, CPIM |
| `cost_to_serve` | `src/cost_to_serve.py` | **Cost-to-serve ABC** por segmento/cliente + **curva ballena** (Christopher; Cooper & Kaplan) | CLTD, SCPro, CSCP |

### 2.5 RETURN — logística inversa y liquidación

| Capacidad | Módulo(s) | Modelo / método (fuente) | Certif. |
|---|---|---|---|
| `returns` | `src/reverse_logistics.py` | Disposición (restock/refurbish/liquidate/scrap) por recuperación neta + Pareto de causas | CLTD, CSCP |
| `markdown_liquidation` | `src/liquidation.py`, `liquidation_calendar.py` | Precio de clearance (Gallego-van Ryzin) + calendario con **gate Omnibus UE/UK** y piso competitivo | CLTD |

### 2.6 ORCHESTRATE — la capa agéntica (diferenciador; base de la auditabilidad)

| Capacidad | Módulo(s) | Qué hace (verificado) | Certif. |
|---|---|---|---|
| Orquestador | `scm_agent/orchestrator.py` | Pipeline **brief → classify → tool → prepare → run → QA → ground → deliver**; QA falla ⇒ `STATUS_QA_FAILED`, **cero entregables** | CSCP |
| Sensar→actuar→verificar | `scm_agent/monitors.py`, `autonomy.py`, `verify/`, `autonomy_promotion.py` | Monitores (ROP breach, stockout, E&O, error de pronóstico, deriva de lead time); tiers T1/T2/T3; backtest/reliability; **autonomía ganada con evidencia, degradación inmediata** | CSCP |
| Never-unprotected | `src/guided.py`, `scm_agent/guided_bridge.py`, `src/escalation.py` | Todo resultado es EXECUTED u ofrece OPTIONS/HANDOFF/ESCALATED con SLA; `verify_guided` marca "unprotected" | CSCP |
| Change control | `src/writeback.py`, `writeback_store.py`, `src/connectors/` | Stage→approve (**HMAC, TTL 900s, ligado a idempotency+content hash**)→apply idempotente→**rollback**; irreversible siempre requiere humano | CSCP |
| Grounding | `scm_agent/knowledge.py`, `citation_gate.py`, `knowledge/scm-books/` | Citas L3 obligatorias; `citation_gate` veta citas fuera de tema (MIN=2, MAX_HOPS=2, `EXCLUDED_CONCEPTS` anti-falso-amigo) | CSCP |
| Digital twin | `src/digital_twin.py` | Fábrica de escenarios de red que alimenta la suite | SCPro, CSCP |
| Riesgo/resiliencia | `src/risk.py`, `risk_period.py` | Registro de riesgo: **EMV**, **FMEA RPN**, heatmap 5×5, brecha **TTR>TTS** (Simchi-Levi) | SCPro, CSCP |
| Benchmarking eficiencia | `src/dea.py` | **DEA CCR** input-oriented (linprog HiGHS) | SCPro |
| KPIs financieros | `src/financial_kpis.py`, `working_capital.py` | Turns, DIO, **GMROI**, **cash-to-cash (SCOR AM.1.1)**, capital de trabajo en $ | CSCP, SCPro |
| Pricing/revenue | `src/pricing.py`, `price_optimizer.py`, `elasticity_batch.py`, `pricing_guardrails.py`, `src/pricing_intel/` | Elasticidad log-log, p*=c·ε/(ε+1), shrinkage empírico-Bayes; **guardrails jurisdiccionales**; inteligencia de precio competidor (solo-lectura) | CSCP |
| Calidad de datos | `src/data_quality.py`, `reconciliation.py`, `sanitize.py` | **GTIN check-digit GS1**, mapeo de columnas, IRA, **defusing de fórmula-injection** | CSCP, CLTD |

---

## 3. Cobertura por certificación (cubierto vs. laguna)

### CPIM — *Certified in Production and Inventory Management* (ASCM)
**Cubierto (alto):** pronóstico (incl. intermitente), EOQ y descuentos, safety stock por **σ de error**, `(s,Q)`/`(R,S)`, newsvendor, multi-echelon GSM, DDMRP, ABC-XYZ, S&OP agregado, DRP, simulación de políticas, conteo cíclico/IRA, kanban, secuenciación, curva de aprendizaje.
**Lagunas:** MRP-II detallado / BOM multinivel con explosión de requerimientos de material (solo hay lead time desacoplado ASR en `ddmrp_decoupling.py`, no un MRP completo); CRP/programación maestra de producción (MPS) formal; gestión de capacidad de planta a nivel de orden.

### CLTD — *Certified in Logistics, Transportation and Distribution* (ASCM)
**Cubierto (alto):** DRP, ubicación de instalación, selección de modo + break-even, CVRP, layout y slotting (COI + afinidad), FEFO/caducidad, cost-to-serve, landed cost, logística inversa.
**Lagunas:** gestión de aduanas/comercio internacional más allá del Incoterm+arancel (denied-party, clasificación HS, documentación); WMS/TMS operacional; optimización de red multi-instalación (solo 1-mediana, no p-mediana/MILP); global trade compliance.

### CSCP — *Certified Supply Chain Professional* (ASCM)
**Cubierto (medio-alto):** integración end-to-end vía orquestador + modos inventory/SCM; SCOR (mapeo §4); tecnología (digital twin, agente, writeback); métricas (cash-to-cash, cost-to-serve, DEA); riesgo TTR/TTS.
**Lagunas:** **sostenibilidad computable** (existe como conocimiento L3 —libro Grant, 177 nodos— pero sin motor de cálculo; `modes.py` lista "sustainability" en el persona SCM **sin engine detrás**); estrategia de red y diseño de cadena a nivel MILP; CRM/colaboración con cliente; gestión de relación con proveedor estratégica.

### SCPro — *SCPro Certification* (CSCMP)
**Cubierto (medio-alto):** análisis de red (facility location, digital twin), KPIs y benchmarking (DEA, financial_kpis), resolución de problemas (decision_support, mcdm, what-if), gestión de proyecto (EVM).
**Lagunas:** análisis de red a escala (optimización conjunta), integración de datos externos en vivo (la mayoría es cálculo offline por diseño), sostenibilidad.

### CPSM — *Certified Professional in Supply Management* (ISM)
**Cubierto (parcial):** desempeño de proveedor (OTIF/PPM), landed cost/TCO, selección multicriterio (BWM/TOPSIS), muestreo de aceptación, análisis de cambio/negociación, riesgo de suministro.
**Lagunas (las mayores del proyecto):** **SRM profundo** (segmentación Kraljic, matriz de poder, desarrollo de proveedor); **ciclo de vida de contratos** (cláusulas, SLA, renovación); estrategia de categoría; abastecimiento estratégico end-to-end; ética/diversidad de proveedores; gestión de gasto (spend analysis).

---

## 4. Alineación con estándares técnicos

### 4.1 SCOR Digital Standard — procesos y métricas

**Procesos:** el mapeo §2 cubre **Plan, Source, Order/Fulfill, Return y Orchestrate con densidad alta**; **Transform es delgado** (honesto). La capa **Orchestrate** de SCOR DS —que el estándar añadió precisamente para lo digital (twins, analítica, agentes, resiliencia)— es donde Kern está **por encima** del promedio: sense→decide→act→verify real (`monitors`→`autonomy`→`verify`→`autonomy_promotion`).

**Métricas (atributos de rendimiento SCOR):**

| Atributo SCOR | Métrica típica | ¿Kern la calcula hoy? | Módulo |
|---|---|---|---|
| **RL** Fiabilidad | Perfect Order / OTIF, IRA, fill rate | **Sí** | `sourcing`, `reconciliation`, `fill_rate` |
| **RS** Responsividad | Tiempo de ciclo, lead time | **Parcial** (combinador de riesgo de lead time; DRP temporizado) | `risk_period`, `drp` |
| **AG** Agilidad/Resiliencia | TTR, TTS, flexibilidad | **Parcial** (brecha TTR>TTS cualitativa; falta cuantificación de recuperación) | `risk` → **evolución E5** |
| **CO** Costo | Costo total, cost-to-serve, landed cost | **Sí** | `cost_to_serve`, `landed_cost`, `cost_optimization` |
| **AM** Gestión de activos | **Cash-to-cash**, turns, GMROI, DIO | **Sí** (cita explícita SCOR AM.1.1) | `financial_kpis`, `working_capital` |
| **ES** Sostenibilidad/Ambiental | GHG/huella, Scope 1-2-3 | **No** (solo conocimiento L3) | — → **evolución E1** |

### 4.2 ISO 9001 (gestión de calidad) — dónde Kern ya se comporta alineado

| Cláusula ISO 9001 | Comportamiento equivalente en Kern (verificado) |
|---|---|
| **4.4 / 8.1 Enfoque a procesos** | Pipeline fijo y auto-descrito: cada capacidad es un `Tool` con `prepare/run/qa/deliver`; misma secuencia para las 40. |
| **7.5 Información documentada** | `EvidenceRecord` (SHA-256 de inputs, control totals, `formula_versions`, atestaciones QA); `AuditEntry` de writeback; CHANGELOG (Keep-a-Changelog); citas L3 en cada entregable. |
| **8.5.1 Control de la producción** | Grounding + confidence + opciones `guided` en cada deliverable; personas/KPIs por modo. |
| **8.5.2 Trazabilidad** | **Parcial**: `data_quality` valida GTIN GS1; `lots/` da FEFO+caducidad; **falta genealogía de lote** → **evolución E3**. |
| **8.6 Liberación de producto** | Doble gate: QA del tool + (en paquetes) `citation_gate`. |
| **8.7 Control de salidas no conformes** | **QA falla ⇒ `STATUS_QA_FAILED`, cero entregables**; el runner de paquetes es dos fases (calcula todo, escribe solo si todos pasan). |
| **8.5.6 Control de cambios** | Writeback stage→approve(firmado, TTL)→apply→rollback; config de autonomía solo vía `Changeset` firmado; `CONTRIBUTING`: feature branch→draft PR→CI verde py3.11-3.13→squash. |
| **9.1 Seguimiento y medición** | `forecast_metrics`, `financial_kpis`, `dea`, `reliability.py`. |
| **10 Mejora continua** | Lazo `verify/backtest` → `ToolReliabilityReport` → `autonomy_promotion` (promoción con evidencia). |

### 4.3 ISO 28000 (seguridad y resiliencia de la cadena) — alineación

| Elemento ISO 28000 / 31000 | Control real en Kern |
|---|---|
| **Evaluación de riesgo** | `risk.py`: EMV, FMEA RPN, heatmap, TTR>TTS; `risk_period` para riesgo de lead time. |
| **Controles de seguridad del sistema** | `SECURITY.md`: params acotados, whitelist de cliente, defensa path-traversal, cap 25 MB, rate-limit, API-key (comparación de tiempo constante), CSP, defusing fórmula-injection (`sanitize.py`). |
| **Autorización de cambios** | Aprobaciones **HMAC firmadas, time-boxed**, ligadas a idempotency+content hash; tiers de autonomía con gates humanos; escalación con SLA. |
| **Continuidad / fail-safe** | `LINCHPIN_REQUIRE_SECURE=1` **rehúsa arrancar** sin controles; never-unprotected; apply idempotente + rollback; degradación elegante sin claves LLM. |

### 4.4 Mejores prácticas técnicas de inventario/pronóstico (la "norma mínima")

Las tres que la industria considera no-negociables **ya son el comportamiento por defecto** de Kern:

1. **Safety stock sobre σ del error de pronóstico, no dispersión bruta.** `forecasting.ForecastResult.error_std` = std de errores un-paso (ddof=1); `to_engine_inputs()` lo mapea como la σ del safety stock. Docstring: *"σ_e —no la std bruta de demanda— es la dispersión teóricamente correcta"* (Vandeput 2021 §4.2.5). Fallback a std bruta solo con muy pocos períodos.
2. **Demanda intermitente y sesgada tratadas con su modelo.** Croston + **TSB** (Nixtla) con ruteo por ADI≥1.32; selección normal-vs-**gamma** por asimetría (`distributions.select_distribution`, γ₁>σ/μ). *(Nota: no hay SBA ni lognormal/negbin/Poisson — ver §6.)*
3. **Validar la política por simulación antes de recomendar.** `simulation.py`/`simulation_opt.py` corren Monte Carlo y reportan fill rate, backorders, **lost sales** y costos; `multi_echelon.simulate_serial_gsm` simula base-stock por eslabón.

---

## 5. Mínimos de industria que Kern puede imponer HOY

Estos son criterios **auditables** que la arquitectura actual ya hace cumplir (o puede, activando un flag). Un cliente puede exigirlos como cláusula de servicio:

1. **σ_e, no σ bruta.** Todo safety stock parte del error de pronóstico (`forecasting.to_engine_inputs`). *(Vandeput 2021 §4.2.5.)*
2. **Distribución correcta por patrón.** Normal solo si la asimetría lo justifica; gamma / Croston / TSB según ADI y skew. Prohibido asumir normalidad en demanda lumpy o sesgada.
3. **Simular antes de recomendar.** Ninguna política `(s,Q)`/`(R,S)`/multi-echelon se entrega sin fill rate/backorders/lost sales simulados.
4. **QA veta el entregable.** Si el tool no pasa su `qa()`, no se escribe nada (`STATUS_QA_FAILED`). En paquetes, un solo paso QA-fallido bloquea el paquete completo.
5. **Grounding obligatorio + anti-falso-amigo.** Cada resultado cita fuentes L3 curadas; `citation_gate` (MIN=2, MAX_HOPS=2, `EXCLUDED_CONCEPTS`) descarta citas fuera de tema en vez de "cerezas".
6. **Nunca sin salida (never-unprotected).** Todo resultado es EXECUTED o entrega OPTIONS/HANDOFF/ESCALATED; cada `Residual` debe declarar `risk_if_skipped`.
7. **Escritura solo en staging, firmada y reversible.** Cambios a sistemas de registro pasan por dry-run→aprobación firmada con TTL→apply idempotente→rollback; irreversible siempre requiere humano.
8. **Autonomía ganada con evidencia; degradación inmediata.** T1/T2/T3 según `event_routing.yaml`; promoción solo con N ciclos de fiabilidad y firma humana; cualquier fallo degrada al instante (Golden Rule 11).
9. **Gate de calidad de datos en la entrada.** GTIN GS1 check-digit, mapeo de columnas, y escalación si `dropped_fraction` supera umbral. No se calcula sobre maestros sin validar.
10. **Costo de importación y precio con base legal.** Landed cost consciente de Incoterm; cambios de precio pasan por `pricing_guardrails` (gate duro Omnibus UE/UK, evidencia "menor precio 30 días").
11. **Evidencia re-ejecutable.** `EvidenceRecord` con SHA-256 de inputs, control totals, `formula_versions` y atestaciones QA — reproducible por un tercero (alineado a PCAOB AS 1215).
12. **Seguridad de entrada por defecto.** Formula-injection defused en exports; PII nunca en `/api/metrics`; el `brief` de texto libre se parsea, nunca se ejecuta.

---

## 6. Lagunas de contenido (honestas)

| Laguna | Estado real hoy | Estándar/certif. que lo pide |
|---|---|---|
| **Sostenibilidad computable** | Solo conocimiento L3 (libro Grant, 177 nodos). `modes.py` la nombra en el persona SCM pero **no hay motor** de huella/GHG. | SCOR **ES**, CSCP, ISO 14001 |
| **SRM profundo / segmentación** | `supplier_scorecard.py` es solo OTIF/DIFOT+PPM; no hay Kraljic, poder, desarrollo, gasto. | **CPSM**, CSCP |
| **Trazabilidad / genealogía de lote** | GTIN GS1 + FEFO/caducidad; **sin** cadena de custodia one-up/one-down ni EPCIS. | ISO 9001 §8.5.2, food/pharma |
| **Compliance regulatorio amplio** | Fuerte en pricing (Omnibus) y aduana (Incoterm); **falta** trade compliance, HS, denied-party, seguridad de producto. | CLTD, CPSM |
| **Cuantificación de resiliencia (agilidad)** | TTR>TTS cualitativo en `risk.py`; **falta** stress-test de red con tiempo/impacto de recuperación. | SCOR **AG** |
| **Modelos ausentes en inventario** | Sin **SBA** (solo Croston puro), sin lognormal/negbin/Poisson; multi-echelon solo serial (no ensamblaje/distribución). | CPIM avanzado |
| **Optimización de red** | Solo 1-mediana (Weiszfeld); sin p-mediana/MILP multi-instalación. | CLTD, SCPro |

---

## 7. Evoluciones concretas del repo (para subir el estándar)

Cada una respeta la filosofía (never-unprotected, QA gate, staging, grounding) y **reutiliza el patrón de extensión existente**: `src/<x>.py` (funciones puras) → `jobs/<x>_job.py` → `register()` en `tools.py` → ancla en `citation_gate.TOOL_CONCEPTS` → salida `guided` → tests con ejemplos numéricos. Ninguna introduce matemática no anclada: todas se fundan en fuentes que **ya están** (o se añaden) al grafo L3.

### E1 — Motor de sostenibilidad (`carbon_footprint`) — **prioridad alta**
- **Qué:** `src/sustainability.py` — huella por actividad: **tonne-km × factor de emisión** por modo (reusa los tonne-km de `logistics/modes.py`), energía de almacenamiento, y **costo del carbono** inyectable en `landed_cost`. Buckets Scope 1/2/3.
- **Reusa:** `logistics/modes`, `landed_cost`, patrón `register()`, `citation_gate`.
- **Grounding:** libro Grant *Sustainable Logistics* (ya 177 nodos en L3) + añadir **GLEC Framework** como fuente L3. Sin modelos inventados.
- **Cierra:** SCOR **ES**, CSCP sostenibilidad, y honra lo que `modes.py` ya promete.

### E2 — SRM y segmentación de proveedores (`supplier_management`) — **prioridad alta**
- **Qué:** `src/srm.py` — scorecard **multidimensional ponderado** (calidad/entrega/costo/riesgo/sostenibilidad) con pesos de **`mcdm.bwm_weights`** (ya existe), **matriz Kraljic** (impacto en beneficio × riesgo de suministro) y segmentación → estrategia.
- **Reusa:** `mcdm` (BWM/TOPSIS), `risk.py`, `supplier_scorecard` (OTIF como una dimensión).
- **Grounding:** Kraljic (añadir a L3), Christopher, práctica CIPS/CPSM.
- **Cierra:** la mayor laguna de **CPSM**; SCOR Source.

### E3 — Trazabilidad / genealogía de lote (`lot_traceability`) — **prioridad media**
- **Qué:** `src/lots/traceability.py` — log de eventos estilo **EPCIS** one-up/one-down sobre GTIN (ya validado en `data_quality`) + lotes FEFO; trazado de recall/mock-recall y cuarentena de batch.
- **Reusa:** `data_quality` (GTIN), `lots/`, `event_ledger` existente.
- **Grounding:** GS1 EPCIS (añadir a L3); ISO 9001 §8.5.2.
- **Cierra:** trazabilidad ISO 9001, compliance food/pharma.

### E4 — Paquete "Auditoría SCOR/ISO" integrado a jobs (`scor_iso_audit`) — **prioridad alta, esfuerzo bajo**
- **Qué:** nuevo `PackageSpec` + `jobs/scor_audit_job.py` que corre los datos del cliente por los tools relevantes y emite (a) **scorecard de métricas SCOR** (Perfect Order/OTIF, cost-to-serve, cash-to-cash —ya calculado— y brecha TTR/TTS) y (b) **checklist de controles ISO 9001/28000**, todo envuelto en `EvidenceRecord`.
- **Reusa:** runner `packages.py`, `PackageSpec`, `audit_evidence`, `financial_kpis`, `cost_to_serve`, `sourcing`, `citation_gate`. **Es casi todo composición de lo existente.**
- **Cierra:** exactamente el pedido de "plantillas de auditoría SCOR/ISO integradas al flujo de jobs"; ancla el posicionamiento de nivel de referencia.

### E5 — Cuantificación de resiliencia (`resilience_stress_test`) — **prioridad media**
- **Qué:** inyectar una disrupción (proveedor/lane/nodo) en un escenario de `digital_twin` + `simulation`, medir **time-to-recover**, impacto financiero y un **índice de resiliencia** (TTR vs TTS ya en `risk.py`).
- **Reusa:** `digital_twin` (fábrica de escenarios), `simulation`, `risk` TTR/TTS.
- **Grounding:** Simchi-Levi TTR/TTS (ya en L3), Ivanov (ya en L3).
- **Cierra:** SCOR **AG** (agilidad/resiliencia), ISO 28000 continuidad.

**Orden recomendado:** E4 (habilita la venta de auditoría con lo que ya existe) → E1 (cierra el hueco de sostenibilidad más visible) → E2 → E5 → E3.

---

## 8. Ejemplos: cómo un cliente audita su cadena con Kern

Cada ejemplo usa **solo tools que existen hoy** (marcados) e indica dónde encaja una evolución propuesta.

### Ejemplo A — Auditoría de inventario (CPIM + SCOR Plan / RL·AM)
**Brief:** *"Aquí está mi maestro de SKUs y 24 meses de ventas. ¿Están bien mis niveles y políticas?"*
**Pipeline (existe hoy):**
1. `data_quality` — valida GTIN, mapea columnas, reporta `dropped_fraction`.
2. `abc_xyz` — segmenta; asigna nivel de servicio y política por celda.
3. `forecast` — produce **σ_e** y clasifica intermitencia (SBC).
4. `inventory_optimization` — EOQ, safety stock sobre σ_e, `(s,Q)`/`(R,S)`.
5. `simulation` — valida fill rate/backorders/lost sales.
6. `reconciliation` — IRA vs conteo físico.
7. `financial_kpis` — turns, DIO, GMROI, **cash-to-cash**.
**Entregable:** documento de política + modelo de reorden + IRA + KPIs, con citas L3, envuelto en `EvidenceRecord`.
**Estándar satisfecho:** CPIM (política de inventario), SCOR **RL** (fill rate/IRA) y **AM** (cash-to-cash AM.1.1). *(Con **E4**, esto se emite como scorecard SCOR/ISO formal.)*

### Ejemplo B — Auditoría de red y logística (CLTD + SCOR Order/Fulfill / CO)
**Brief:** *"Tengo 3 CDs y 400 tiendas. ¿Está bien mi red y mi transporte?"*
**Pipeline (existe hoy):** `facility_location` (centro de gravedad/Weiszfeld) → `drp` (malla temporizada + roll-up) → `transportation` (modo + break-even LTL/FTL) → `vehicle_routing` (CVRP) → `warehouse_layout` + `slotting` (COI/afinidad) → `cost_to_serve` (curva ballena por cliente).
**Entregable:** diagnóstico de red + plan de reabasto + selección de modo + slotting + ranking de rentabilidad por cliente.
**Estándar satisfecho:** CLTD, SCOR **CO** (cost-to-serve). *(Con **E1**, se añade huella de carbono por lane; con **E5**, stress-test de la red.)*

### Ejemplo C — Auditoría de sourcing y costo de importación (CPSM + SCOR Source)
**Brief:** *"Evalúa a mis 12 proveedores y mi costo real de importación."*
**Pipeline (existe hoy):** `sourcing` (OTIF/DIFOT/PPM) → `landed_cost` (Incoterm-aware) → `mcdm` (BWM/TOPSIS para ranking) → `acceptance_sampling` (plan de inspección entrante) → `risk` (registro de riesgo de suministro, TTR/TTS) → `dea` (frontera de eficiencia de proveedores).
**Entregable:** scorecard de proveedores + costo total en destino por SKU + plan de muestreo + mapa de riesgo.
**Estándar satisfecho:** CPSM (desempeño y selección), SCOR Source. *(Con **E2**, se añade segmentación Kraljic y estrategia de categoría — cierra la laguna CPSM.)*

---

### Cierre

Kern ya impone, por arquitectura, varios **mínimos de industria** que muchas herramientas comerciales dejan como opcionales (σ_e, simular-antes-de-recomendar, QA que veta, grounding, staging firmado). Su núcleo de **inventario/planificación (CPIM)** y **logística/distribución (CLTD)** es de nivel de referencia; su capa de **orquestación** cumple el espíritu de SCOR **Orchestrate** e ISO 9001/28000 mejor que el promedio. Las cinco evoluciones (§7) —empezando por el **paquete de auditoría SCOR/ISO (E4)**, que es casi pura composición— cierran las lagunas de sostenibilidad, SRM, trazabilidad y resiliencia sin romper la filosofía ni inventar matemática.

*Documento generado a partir de la lectura directa del código en `supply-chain-optimization/` (2026-07-17). Todo lo "EXISTE HOY" está verificado; todo lo "PROPUESTA" está marcado como tal.*
