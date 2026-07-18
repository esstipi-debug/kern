# Atlas de Sombreros SCM — Kern

> Addendum a [`KERN_NIVEL_REFERENCIA_SCM.md`](KERN_NIVEL_REFERENCIA_SCM.md) y [`KERN_AGENCIA_IA_TESIS_COMERCIAL.md`](KERN_AGENCIA_IA_TESIS_COMERCIAL.md). Mientras aquellos documentos mapean Kern contra 5 certificaciones "de marco completo" (CSCP/CPIM/CLTD/SCPro/CPSM), este atlas baja un nivel más: mapea Kern contra **29 roles/puestos reales** de la industria ("sombreros"), cada uno con su propia certificación específica, distribuidos en las 8 zonas de la cadena (6 de SCOR + 2 transversales: Riesgo/Calidad y Finanzas/Analytics).

**Metodología:** 8 investigaciones en paralelo (una por zona), cada una verificando por búsqueda web el nombre exacto de organismo + certificación (no de memoria), cruzadas ÚNICAMENTE contra la lista real de capacidades de Kern verificadas en código. Luego dos revisiones adversariales: (1) fact-check de que ninguna certificación esté inventada o mal atribuida, (2) anti-sobreventa, buscando cualquier lenguaje que implique "Kern cumple/está certificado/iguala" en vez de "se alinea con". **Ambas revisiones encontraron problemas reales, ya corregidos en este documento** — ver §7.

**Regla dura aplicada en todo el documento:** Kern nunca "cumple" ni está "certificado" en nada — no tiene ninguna certificación propia. Las filas de cobertura describen únicamente que una capacidad computacional de Kern se **alinea** (total o parcialmente) con lo que el temario de la certificación o el alcance del rol exige.

---

## 1. Tabla maestra (29 roles, correcciones de certificación aplicadas)

| Sombrero / Rol | Zona SCOR | Nivel | Certificación asociada (verificada) | Capacidad(es) de Kern que se alinean | Gap honesto |
|---|---|---|---|---|---|
| Demand Planner | Plan | Táctico | ASCM — APICS CPIM 8.0 (dominio Demand Planning); alt. IBF CPF® | `forecast` (Croston/TSB/AutoETS, clasificación SBC) | Sin consenso colaborativo humano ni demand sensing en tiempo real (POS/promos) |
| Inventory Planner | Plan | Táctico | ASCM — APICS CPIM (dominio Inventory Management) | `inventory_optimization`, `newsvendor`, `multi_echelon`, `excess_obsolete`, `abc_xyz`, `cycle_count`, `financial_kpis` | Sin ejecución física de conteo/reabastecimiento en almacén |
| Master Scheduler (S&OP→MPS) | Plan | Táctico | ASCM — APICS CPIM (Master Scheduling) | `sop` (agregado), `capacity_planning`, `scheduling` | Sin motor de MRP (explosión de BOM, netting time-phased) |
| S&OP Manager / Director | Plan | Gerencial | IBF — programa formativo S&OP/IBP (nombre exacto del credential por confirmar); alt. ASCM CPIM (módulo S&OP) | `sop`, `financial_kpis`, `working_capital`, orquestador `scm_agent`/`guided.py` (patrón de escalamiento) | Facilitación humana de reuniones/política organizacional no es computable |
| Buyer / Comprador Táctico | Source | Táctico | CIPS — Level 4 Diploma in Procurement and Supply | `supplier_scorecard`, `landed_cost`, `acceptance_sampling` | Sin ejecución transaccional de PO (emisión, aprobación, 3-way match) |
| Category Manager | Source | Gerencial | CIPS — Level 5/6 (Advanced/Professional Diploma, ruta a MCIPS) | `mcdm`, `landed_cost`, `decision_support`, `dea` | Sin spend analytics agregado ni should-cost engineering |
| Sourcing Manager | Source | Gerencial | ISM — CPSM® (Certified Professional in Supply Management) | `mcdm`, `acceptance_sampling`, `supplier_scorecard`, `risk`/`risk_period`, `audit_evidence` | Sin motor de e-sourcing/RFx ni gestión de ciclo de vida de contrato (CLM) |
| Chief Procurement Officer (CPO) | Source | Estratégico | CIPS — MCIPS (Chartered); alt. ISM CPSM® | `financial_kpis`, `working_capital`, `sop`, `risk`/`risk_period`, `leadership_chain`, orquestador + `autonomy`/`autonomy_promotion` | Sostenibilidad/ESG: gap total; sin spend-under-management agregado |
| Production Planner / Master Scheduler | Make | Táctico | ASCM — APICS CPIM | `scheduling` (Johnson, Húngara, FCFS/SPT/EDD), `capacity_planning`, `sop`, `inventory_optimization` | Sin MRP/explosión de BOM ni integración MES/WO |
| Plant Manager / Operations Manager | Make | Gerencial | ASQ — CMQ/OE (Certified Manager of Quality/Organizational Excellence) | `capacity_planning`, `earned_value`, `dea`, `risk`/`risk_period`, `financial_kpis`, `leadership_chain` | Sin OEE, sin TRIR/seguridad, sin gestión de headcount ni aprobación de CAPEX |
| Manufacturing Engineer / Process Engineer | Make | Táctico | SME — Certified Manufacturing Engineer (CMfgE) | `learning_curve`, `queuing`, `scheduling`, `capacity_planning` | Sin CAD/simulación de eventos discretos de piso, sin tiempos estándar (MTM), sin SPC |
| Continuous Improvement Manager / LSS Black Belt | Make | Gerencial | ASQ — CSSBB; alt. IASSC — ICBB | `kanban`, `risk`/`risk_period` (FMEA RPN), `simulation`, `dea` | Sin SPC (cartas de control, Cp/Cpk), sin DOE, sin cálculo directo de DPMO |
| Logistics Manager | Deliver | Gerencial | ASCM — CLTD | `drp`, `facility_location`, `logistics/modes`, `logistics/routing`, `cost_to_serve`, `reverse_logistics` | Sin ejecución en vivo (TMS, tracking de carriers, compliance aduanero) |
| Warehouse / DC Manager | Deliver | Táctico | IWLA — CWLP; alt. módulo ASCM CLTD | `warehouse`, `space` + `slotting_affinity`, `cycle_count` | Sin WMS de ejecución, sin pick-path en tiempo real, sin labor management |
| Transportation Manager | Deliver | Gerencial | AST&L — CTL (legacy, absorbida en ASCM CLTD); alt. CILT — CMILT | `logistics/modes`, `logistics/routing` | Sin tendering/cotización de carriers en vivo, sin telemática, sin freight audit & pay |
| Distribution Manager | Deliver | Gerencial | ASCM — CLTD; alt. CILT Diploma/Advanced Diploma | `drp`, `multi_echelon`, `facility_location` | Sostenibilidad/emisiones: gap total; sin integración ERP/WMS en vivo |
| Returns Manager | Return | Táctico | Sin credential dominante — ASCM CLTD Módulo 8 (parcial); referencia informal: Reverse Logistics Association | `reverse_logistics`, `liquidation`, `liquidation_calendar` | Sin workflow de intake/inspección/grading, sin política RMA, sin tracking de ciclo de reembolso |
| Reverse Logistics Manager / Director | Return | Gerencial | Igual que arriba — sin credential de red reversa dedicado y globalmente estandarizado | `facility_location`, `logistics/modes`, `logistics/routing`, `cost_to_serve`, `reverse_logistics` | Herramientas generales aplicadas manualmente, no un motor de red reversa nativo; sin scorecard 3PL reverso |
| Circular Economy Manager / Sustainability Manager | Return | Estratégico | ISCEA — CSSCP; sin consenso de organismo alternativo confirmado | Ninguna — solo conocimiento narrativo L3 | Gap total: sin motor de huella/emisiones, sin tasa de circularidad ni cumplimiento EPR |
| Supply Chain Risk Manager | Riesgo/Calidad | Gerencial | RIMS — CRMP (ANAB/ISO 17024); alt. PECB ISO 31000 Risk Manager | `risk` + `risk_period` (EMV, FMEA RPN, heatmap, TTR/TTS), `dea` | Sin inteligencia de riesgo externa en vivo, sin BCP, sin gestión de pólizas |
| Quality Manager | Riesgo/Calidad | Gerencial | ASQ — CQE; alt. ASQ CMQ/OE | `acceptance_sampling`, `supplier_scorecard`, `data_quality` (GTIN), `audit_evidence` | Sin CAPA, sin SPC, sin QMS documental tipo ISO 9001, sin COPQ |
| Trade Compliance Officer | Riesgo/Calidad | Gerencial | NCBFAA — CCS (import) / CES (export) | `landed_cost` (única capacidad tocante) | Sin clasificación HS, sin screening de sanciones, sin ITAR/EAR, sin drawback/FTZ |
| Supply Chain Financial Analyst / FP&A | Finanzas/Analytics | Gerencial | AFP — FPAC® | `financial_kpis`, `working_capital`, `cost_to_serve`, `reconciliation` (IRA) | Sin presupuestación P&L completa ni evaluación CAPEX (NPV/IRR) |
| Pricing / Revenue Manager | Finanzas/Analytics | Gerencial | Professional Pricing Society — CPP | `pricing`/`price_optimizer`/`elasticity_batch`, `pricing_guardrails`, `pricing_intel` (solo lectura), `liquidation`/`liquidation_calendar` | Sin diseño de estrategia de monetización; `pricing_intel` no ejecuta ajuste automático |
| Supply Chain Data Analyst / Data Scientist | Finanzas/Analytics | Táctico | INFORMS — CAP | `forecast`, `data_quality`, `simulation`, `whatif`, `dea`, `audit_evidence` | Sin ML supervisado general; `data_quality` acotado a GTIN (GS1) |
| VP Supply Chain | Ejecutivo/Estratégico | Estratégico | ASCM — CSCP | `inventory_optimization`, `forecast`, `financial_kpis`, `sop`, `multi_echelon`, `drp` | Ver nota de alcance en §2 — no implica cobertura del temario CSCP completo |
| Chief Supply Chain Officer (CSCO) | Ejecutivo/Estratégico | Estratégico | CSCMP — SCPro™ L1/L2; alt. ASCM SCOR-P | `risk`/`risk_period`, `sop`, `facility_location`; gobernanza: `scm_agent`, `autonomy`/`autonomy_promotion`, `guided.py`, `writeback`, `audit_evidence` | Sin M&A, sin relaciones regulatorias; sostenibilidad recae aquí también |
| COO (foco en operaciones) | Ejecutivo/Estratégico | Estratégico | Sin credential dedicada — proxy: ASCM APICS CPIM | `capacity_planning`, `dea`, `earned_value`, `scheduling`, `queuing`, `financial_kpis`, `working_capital` | Talento, EHS, cultura organizacional: puramente humano |
| Sustainability / ESG Lead | Ejecutivo/Estratégico | Estratégico | ISCEA — CSSCP; alt. GBCI (absorbió el antiguo programa ISSP-CSP; nombre exacto vigente del credential por confirmar) | Ninguna — solo un libro L3 | Gap total: sin motor de emisiones/scope 3, sin scoring ESG de proveedor |

---

## 2. Sombreros donde Kern es fuerte hoy (lenguaje corregido — ver §7)

- **Inventory Planner** — cubre la decisión central (política de reabastecimiento) y sus KPIs asociados, aunque sin ejecutar el conteo o reabastecimiento físico en almacén.
- **S&OP Manager/Director** — `sop` como motor literal, `financial_kpis`/`working_capital`, y patrón de escalamiento del orquestador.
- **Logistics Manager** y **Distribution Manager** — `drp`, `facility_location`, `multi_echelon`, `logistics/modes`, `logistics/routing`, `cost_to_serve`, `reverse_logistics`.
- **Quality Manager** — `acceptance_sampling`, `supplier_scorecard`, `data_quality`, `audit_evidence`.
- **Supply Chain Financial Analyst/FP&A** y **Pricing/Revenue Manager** — bloques financiero y de pricing más completos del atlas.
- **Supply Chain Data Analyst/Data Scientist** — `forecast`, `simulation`, `whatif`, `dea`, `audit_evidence`, alineados con el ciclo de framing-modelado-trazabilidad que aparece en el temario de CAP.
- **VP Supply Chain** — no se identificó gap crítico entre las capacidades verificadas y el subconjunto cuantitativo del temario CSCP cubierto por este atlas; **esto no implica cobertura del temario CSCP completo**, que incluye dominios fuera del alcance computacional de Kern (relación con clientes, tecnología, diseño de producto, etc.).
- **CSCO** — no por volumen de motores, sino por la capa de gobernanza: QA gate, autonomía por tiers, `writeback` firmado/reversible, y `audit_evidence` con principios de trazabilidad inspirados en PCAOB AS 1215 (no constituye una certificación ni auditoría PCAOB real). Responde de forma alineada al mandato de "puedo delegar ejecución sin perder control" que define a este rol.
- **COO (ops)** — `capacity_planning`, `dea`, `earned_value`, `scheduling`, `queuing`, `financial_kpis`, `working_capital`.

## 3. Sombreros donde Kern es parcial

Demand Planner, Buyer, Category Manager, Sourcing Manager, CPO, Master Scheduler (Plan y Make), Plant Manager, Manufacturing Engineer, Warehouse/DC Manager, Transportation Manager, Returns Manager, Reverse Logistics Manager/Director, Supply Chain Risk Manager — en todos estos, Kern cubre una porción real de la decisión pero deja descubierta la función transaccional/ejecución/humana central del rol (ver tabla maestra para el gap específico de cada uno).

## 4. Sombreros donde Kern es débil o no aplica

- **Trade Compliance Officer** — solo `landed_cost` toca el dominio; clasificación HS, screening de sanciones, ITAR/EAR, drawback/FTZ son gap limpio.
- **Circular Economy Manager (Return)** y **Sustainability/ESG Lead (Ejecutivo)** — cero motor computacional, solo un libro L3 narrativo. Hoy es investigación/roadmap, no producto.
- **Manufacturing Engineer** (dimensión de ingeniería de detalle: CAD, DES, MTM, SPC) — gap total en esa capa, aunque el rol en general es "parcial".
- **Dimensión humana transversal** — facilitación de reuniones, negociación, liderazgo de equipo/headcount, M&A, relaciones regulatorias, cultura organizacional, EHS: no computable en ningún rol; `leadership_chain` no reemplaza esta ejecución.

## 5. El hallazgo estructural (el más importante del atlas)

Patrón repetido en las 8 zonas, sin excepción: **Kern es sistemáticamente una capa de decisión/diseño, nunca un sistema de ejecución transaccional en vivo.** PO issuance, MRP/explosión de BOM, WMS/pick-path en tiempo real, TMS/tracking de carriers, MES/órdenes de trabajo, CAPA/QMS documental — ninguno de los 29 roles tiene esta pieza cubierta, en ningún nivel.

Esto confirma con evidencia sistemática (29 roles, 8 zonas) lo mismo que ya se estableció al descartar el posicionamiento "Kern como 5PL": Kern decide, no ejecuta. No es una limitación de logística — es la naturaleza del producto en toda la cadena. La posición correcta es "la capa de decisión que se sienta arriba de quien ejecuta" (el sistema de registro, el 3PL, el operador de planta), nunca "el sistema que ejecuta".

## 6. Propuesta para `scm_agent/modes.py`

Hoy `modes.py` solo distingue Inventory vs. SCM — una partición por dominio de datos, no por quién pregunta. Se propone una tercera dimensión: un modo de "lente" (hat) que el orquestador adopta al enmarcar una respuesta, seleccionable por el usuario o inferido del brief (p. ej. "respóndeme como para un CSCO" activa la lente CSCO: prioriza `risk`, `sop`, gobernanza de autonomía y framing de resiliencia, citando el temario de CSCMP SCPro™ como marco de referencia — **nunca como certificación de Kern**).

Cada lente aportaría: (1) qué capacidades priorizar, (2) qué KPIs encabezan el resumen, (3) el marco de la certificación asociada citado explícitamente como "esto se alinea con lo que exige el temario de X", jamás "Kern cumple/está certificado en X". El `guided.py` existente (EXECUTED/OPTIONS/HANDOFF/ESCALATED) ya es compatible: una lente Buyer táctico auto-ejecutaría más; una lente CPO estratégico escalaría antes a HANDOFF por el peso de la decisión.

**Riesgo a vigilar:** que el usuario confunda la lente con una acreditación real — el copy debe decir "marco de referencia" en cada respuesta que la use.

## 7. Correcciones aplicadas (transparencia del proceso)

Este documento pasó por dos revisiones adversariales antes de publicarse. Ambas encontraron problemas reales:

**Certificaciones marcadas "VERIFICAR" y corregidas:** CRLP atribuido a un organismo llamado "Laurels Institute" (dudoso — el credential real de logística inversa está asociado informalmente a la Reverse Logistics Association, corregido en la tabla); "CUSECO" como alternativa de compliance aduanero (no es una sigla reconocida, eliminada); nombre exacto del credential GBCI para sostenibilidad (suavizado a "por confirmar"); "CEA" como organismo competidor en economía circular (eliminado por ambigüedad); nombre exacto del programa IBF de S&OP/IBP (suavizado a "programa formativo").

**Sobreventa encontrada y corregida — las 5 concentradas en §2, la sección de mayor riesgo comercial:** lenguaje de cobertura completa ("de cabo a rabo", "exactamente", "cubre lo que exige X") reemplazado por lenguaje de alineación parcial, consistente con la regla dura del documento. Ver el detalle de cada corrección en el registro de la sesión que generó este atlas.

---

## 8. Próximo paso propuesto (no ejecutado aún)

El usuario planteó: usar los gaps de este atlas para proponer **nuevas capacidades** de Kern (no solo documentar lo que falta). Los candidatos con mayor señal —porque aparecen repetidos en varios sombreros, no en uno solo— son: (a) sostenibilidad/emisiones (aparece en Return, CPO, Distribution, CSCO — ya es la evolución **E1** del documento de estándares), (b) SRM/gestión de proveedor profunda (aparece en Category Manager, Sourcing Manager, CPO — ya es **E2**), y (c) trazabilidad/genealogía (aparece en Quality Manager, Trade Compliance — ya es **E3**). El atlas no encontró ningún gap de alta señal que NO estuviera ya cubierto por las evoluciones E1-E5 existentes — lo que valida esas propuestas con evidencia nueva, en vez de requerir una sexta.
