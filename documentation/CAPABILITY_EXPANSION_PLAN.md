# Linchpin — Plan Maestro de Expansión de Capacidades

**Versión:** 1.0 · **Fecha:** 22 jun 2026
**Objetivo:** convertir a Linchpin de un *vertical profundo de planificación de inventario* en el **agente SCM agéntico end-to-end** capaz de ejecutar los 6 perfiles, los 16 módulos y los 8 playbooks de la `Base de Conocimiento`, en todos los stacks reales (Excel/Sheets, Shopify, Amazon FBA, ERPs, contabilidad, carriers).

> **Nota (2026-07-02):** la columna **Hoy** de la tabla en §4 es un snapshot congelado al 22 jun 2026 y ya está desactualizada — no se ha vuelto a certificar desde entonces. Desde esa fecha se enviaron 10 tools más al registry del agente (PRs #73-#80): `abc_xyz`, `ddmrp`, `cycle_count`, `multi_echelon`, `newsvendor`, `simulation`, `slotting`, `transportation`, `fefo`, `excess_obsolete`, `facility_location`, `drp` — todas ya wireadas como capacidades ruteables por brief (ver `build_default_registry()` en `scm_agent/tools.py`, 34 tools hoy). Los porcentajes de M4/M5/M6/M7/M9/M10 etc. en la tabla de abajo por lo tanto subestiman el estado real; no los re-certifiqué con precisión (requeriría re-derivar la metodología de scoring del audit original) — tómalos como referencia histórica del gap analysis, no como el estado actual. Fuente de verdad para "qué está wireado hoy": el registry, no esta tabla.

> Fundamento: auditoría de código real (Linchpin hoy ≈ 27% de amplitud, ~85% del núcleo analítico) + investigación profunda de 14 líneas en paralelo (metodología SOTA, libros/papers, librerías y APIs con versiones verificadas a jun 2026). Las citas están en §6.

---

## 0. Cómo está construido este plan

Cada capacidad faltante se resolvió eligiendo: **(a)** la mejor metodología actual (con su libro/paper de respaldo), **(b)** *build vs buy* (qué se implementa en el repo vs qué se envuelve de una librería/API madura), y **(c)** una o más **skills** concretas en formato `SKILL.md` que envuelven módulos nuevos de `src/` o clientes de API — siguiendo la convención de las skills `vandeput-*` existentes.

**Regla transversal de diseño:** el núcleo determinista y auditable se **construye** en el repo (debe pasar el QA gate y resolverse contra el knowledge graph); la "plomería" (forecasting estadístico, record-linkage, multi-carrier, voz) se **compra/envuelve**; las dependencias pesadas van en **extras opcionales** de `pyproject` con *fallback* a lo que ya existe.

**Total: ~64 skills** en 16 capacidades (15 de dominio + la Capa de Ejecución Guiada transversal), agrupadas en 6 fases por ROI (§7).

---

## 1. Principios de arquitectura

Adoptamos el patrón **2026 Agent Skills** (estándar abierto desde 2025-12-18), que es exactamente lo que las skills `vandeput-*` ya usan. Cuatro capas:

```
SKILL.md  ─▶  @tool / MCP tool  ─▶  módulo determinista src/  ─▶  conector (API/archivo/DB)
(disparador,    (Claude Agent SDK     (matemática auditable,         (Shopify, SP-API, ERP,
 contrato)       in-process; MCP        QA-gated, KG-grounded)         carriers, Excel)
                 solo en el borde
                 de conectores)
```

Más un **plano de control de escritura segura** separado (el M15 que faltaba):

- **Risk-tiered:** cada acción se etiqueta (lectura / propuesta / escritura reversible / escritura irreversible).
- **Dry-run + staging:** todo cambio se calcula como *changeset* contra un clon/staging; nunca toca el original.
- **Idempotente + audit log:** claves de idempotencia (IETF Idempotency-Key) y bitácora.
- **HITL gate:** las acciones consecuentes esperan aprobación (token TTL); el QA gate actual se mantiene.

**Principio rector — sin callejón sin salida (nunca desprotegido):** el agente **jamás** responde "no puedo" ni se detiene en seco. Para todo lo que no puede ejecutar de forma autónoma (acto físico, negociación, decisión legal/financiera, integración específica del cliente) entrega un **paquete listo para ejecutar**: opciones rankeadas con trade-offs, la recomendación por defecto, el artefacto pre-llenado (PO/email/hoja de conteo/reclamo), el dato, el plazo y el riesgo si no se actúa. Lo implementa la **Capa de Ejecución Guiada** (§2.14), sobre el plano de escritura segura. Así el residual humano queda *cubierto y protegido*, no como hueco — y el piso de cada capacidad sube a ≥91%.

**Stack agéntico:** `claude-agent-sdk==0.2.105` (loop + `@tool`), `mcp==1.28.0` (solo conectores externos), `anthropic==0.111.0` (ya usado por `scm_agent/llm.py`).

**Skills base de arquitectura:** `linchpin-agentic-tooling` [M] · `linchpin-system-connectors` [L] · `linchpin-safe-staging-writeback` [L] · `linchpin-agent-eval` [M] · `linchpin-decision-options` [M] · `linchpin-guided-handoff` [M] · `linchpin-escalation-packet` [M] · `linchpin-coverage-gate` [S].

---

## 2. Catálogo de skills por capacidad

### 2.1 Forecasting moderno  (cierra el gap de estacionalidad, MAPE/WAPE, auto-modelo, probabilístico)

**Metodología:** *Nixtla-first tiered auto-forecasting* — `StatsForecast` (AutoARIMA/AutoETS/AutoTheta/AutoCES + MSTL/Holt-Winters estacional) como motor por defecto; ruteo intermitente a **SBA/TSB** (TSB decae demanda en obsolescencia, lo que Croston clásico no hace); **LightGBM global** vía MLForecast para paneles multi-SKU con drivers (el ganador del M5); reconciliación jerárquica **MinT**; intervalos **conformales** que alimentan el safety stock. Se conserva el MA/SES/Croston actual como *fallback* sin dependencias.

**Tech:** `statsforecast==2.0.3`, `utilsforecast==0.2.16`, `mlforecast==1.0.31`, `lightgbm==4.6.0`, `hierarchicalforecast==1.5.1`, (`neuralforecast==3.1.9` opcional futuro). Extra `[forecast]`.

| Skill | Effort | Envuelve |
|---|---|---|
| `linchpin-forecast-metrics` | S | `utilsforecast.losses` (MAPE/WAPE/RMSSE/MASE/pinball/CRPS) → `src/forecast_metrics.py` |
| `linchpin-forecast-auto` | M | `StatsForecast` auto-selección + estacionalidad → `src/forecasting_auto.py` |
| `linchpin-forecast-intermittent` | M | Croston/SBA/TSB/ADIDA/IMAPA |
| `linchpin-forecast-probabilistic` | M | `ConformalIntervals` → puente a `safety_stock.py`/`policies.py` |
| `linchpin-forecast-hierarchical` | L | `hierarchicalforecast` (MinT) — opcional |
| `linchpin-forecast-ml-global` | L | `mlforecast`+`lightgbm` (M5) — opcional |

### 2.2 Clasificación ABC-XYZ  (M4 — hoy ausente)

**Metodología:** matriz **ABC-XYZ** como columna vertebral (ABC = valor de uso acumulado/Pareto; XYZ = CV de demanda), cada una de las 9 celdas mapea a *política + nivel de servicio + modelo de buffer + método de forecast*. *Upgrade* opcional multi-criterio (Ng/TOPSIS) cuando importan criticidad/lead time/margen. Se **construye** en el repo (auditable, ~200 líneas); pymcdm solo para el modo multi-criterio.

**Tech:** numpy/pandas/scipy (ya presentes); `pymcdm==1.4.0` (extra `[mcdm]`).

| Skill | Effort | Envuelve |
|---|---|---|
| `linchpin-abc-xyz-classification` | M | `src/abc_classification.py` + `xyz_classification.py` + `abc_xyz_matrix.py` (tabla de políticas/SL por celda) |
| `linchpin-multi-criteria-classification` | M | `pymcdm` (TOPSIS/entropía/CRITIC/AHP) — opcional |

### 2.3 DDMRP  (M5 — diferenciador metodológico, hoy ausente)

**Metodología:** **DDMRP v3** (Ptak & Smith) — único método aquí con cuerpo normativo (Demand Driven Institute). Zonas rojo/amarillo/verde, ecuación de net flow, qualified demand, ajuste dinámico (DAF), prioridad de planeación/ejecución, posicionamiento de puntos de desacople. Aritmética pública → se **construye** en el repo; referencia de port: `odoo-addon-ddmrp` (OCA).

**Tech:** numpy/pandas/scipy/openpyxl/matplotlib (ya presentes). Sin dependencias nuevas.

| Skill | Effort | Envuelve |
|---|---|---|
| `linchpin-ddmrp-buffer-sizing` | M | `src/ddmrp/buffers.py` + `adu.py` (zonas, ADU/DLT, TOR/TOY/TOG) |
| `linchpin-ddmrp-net-flow-planning` | M | `net_flow.py` + `priority.py` (net flow, order spikes) |
| `linchpin-ddmrp-decoupling-dlt` | M | `decoupling.py` (longest-path DLT en el BOM) |
| `linchpin-ddmrp-buffer-simulation` | L | reusa `simulation.py`/`simulation_opt.py` |
| `linchpin-ddmrp-vs-classic-comparison` | M | compara DDMRP vs (s,Q)/(R,S) |
| `linchpin-dds-and-op-adaptive` | L | Demand-Driven / Adaptive S&OP (DAF, revisión de perfiles) |

### 2.4 Exactitud de inventario y conteo cíclico  (M6 — hoy ausente)

**Metodología:** **cycle counting por ABC + control-group + reconciliación con códigos de razón y SPC** (marco Piasecki/APICS IRA, CPIM v8). El consenso 2020-2026 es que los problemas de exactitud son de *proceso*, no de conteo masivo. Determinista; se **construye** en el repo.

**Tech:** pandas/numpy/scipy/openpyxl (ya presentes). Sin ML, sin API.

| Skill | Effort | Envuelve |
|---|---|---|
| `linchpin-abc-pareto-classification` | S | `src/abc_classification.py` (compartido con §2.2) |
| `linchpin-cycle-count-plan` | M | `src/cycle_count.py` (frecuencia por clase, balanceo de carga) |
| `linchpin-ira-reconciliation` | L | `src/inventory_accuracy.py` (IRA, tolerancias, razones) + `count_sources.py` |
| `linchpin-ira-control-monitor` | M | `src/ira_control.py` (SPC/3-sigma, control group) |

### 2.5 Espacio (m³) y slotting  (M7 — "módulo distintivo", hoy ausente)

**Metodología:** slotting en dos etapas: **zonificación por clase con COI** (cube-per-order index, Bartholdi & Hackman) + **3D bin packing** para cartonización/paletización; overlay de **afinidad** (co-ocurrencia/lift). Núcleo analítico **construido** en repo; bin packing **envuelto** (py3dbp/rectpack/OR-Tools).

**Tech:** `py3dbp==1.1.2`, `rectpack==0.2.2`, `ortools==9.15.6755` (opcional), numpy/pandas. Extra `[space]`.

| Skill | Effort | Envuelve |
|---|---|---|
| `linchpin-space-cubing-sizing` | M | `src/space_cubing.py` (convierte plan de stock → plan de espacio) |
| `linchpin-slotting-coi-classbased` | M | `src/slotting.py` (COI, zonas, asignación) |
| `linchpin-slotting-affinity` | M | `src/slotting_affinity.py` (lift de co-ocurrencia) |
| `linchpin-cartonization-3dpack` | L | `py3dbp`+`rectpack` (+OR-Tools) |
| `linchpin-space-plan-deliverable` | S | extiende `export.py`/`excel_export.py`/`powerbi_export.py` |

### 2.6 Procurement, proveedores y landed cost  (M8 — hoy ~15%)

**Metodología:** **BWM (pesos) + Fuzzy-TOPSIS (ranking)** con **TCO/landed cost** como criterio de costo dominante. Landed cost = unidad+flete+seguro+arancel+manejo+MPF/HMF+broker, consciente de Incoterms 2020 y base de derecho FOB/CIF. Motor de costo y MCDM **construidos** (auditables); APIs de aranceles **opcionales** (Zonos/Avalara/SimplyDuty + tablas HTS de USITC).

**Tech:** `pymcdm==1.4.0`, `ahpy==2.1` (opcional), `httpx==0.28.1`, scipy (`linprog` para BWM). APIs: USITC HTS, Zonos, Avalara (trae MCP), SimplyDuty. Extra `[procurement]`.

| Skill | Effort | Envuelve |
|---|---|---|
| `linchpin-landed-cost` | L | `src/landed_cost.py` + `duty_rates.py` (HTS offline + adaptadores API) |
| `linchpin-supplier-scorecard` | M | `src/supplier_scorecard.py` (OTIF/DIFOT/PPM, reusa `fill_rate.py`) |
| `linchpin-supplier-selection` | L | `src/mcdm.py` (BWM vía `linprog` + pymcdm TOPSIS/fuzzy + ahpy) |
| `linchpin-purchase-order` | M | `src/purchase_order.py` (PO dataclass + state machine) |

### 2.7 Shopify + Amazon SP-API  (M9 / §5 — hoy ausente, el mayor desbloqueo de mercado)

**Metodología:** sync multicanal **pull-based** sobre **Shopify GraphQL Admin API** + **Amazon SP-API** (LWA-only), con un **ledger canónico indexado por SKU** y detección de *stranded/restock* por reportes. Se **envuelven** las dos librerías cliente; los conectores son adaptadores finos. No depender de MCP para el plano de datos.

**Tech:** `ShopifyAPI==12.7.0` (GraphQL Admin `2026-04`), `python-amazon-sp-api==2.1.8` (FBA Inventory v1, Reports `2021-06-30`, Fulfillment Inbound `2024-03-20`). Extra `[ecommerce]`.

| Skill | Effort | Envuelve |
|---|---|---|
| `linchpin-shopify-admin-connector` | L | `src/connectors/shopify_client.py` (catálogo, inventario por ubicación, órdenes) |
| `linchpin-amazon-spapi-connector` | M | `src/connectors/amazon_client.py` (FBA inventory, LWA auth) |
| `linchpin-amazon-reports-stranded-restock` | M | `amazon_reports.py` (stranded + restock) |
| `linchpin-multichannel-stock-sync` | L | `canonical.py` (ledger) + `stock_sync.py` |
| `linchpin-amazon-inbound-replenishment` | XL | `amazon_inbound.py` (crea envíos FBA desde el optimizador) |

### 2.8 ERP + contabilidad  (M12 / §5 — hoy ausente)

**Metodología:** arquitectura de conectores en dos niveles: protocolos finos `InventorySource`/`AccountingSink` (extienden el `DemandSource` existente) sobre adaptadores por sistema, con escape a **API unificada (Merge.dev)** y puente MCP de lectura. Contabilidad (QuickBooks/Xero) vía SDK oficial; ERPs vía adaptadores `httpx` (~150 líneas c/u).

**Tech:** `python-quickbooks==0.9.12`+`intuit-oauth`, `xero-python==14.0.0`, `OdooRPC`, `netsuite==0.12.0`, `httpx` (Cin7 Core v2, Zoho v1, Sortly, Dynamics 365 BC API v2.0/OData v4); `Merge.dev` opcional. Extra `[erp]`.

| Skill | Effort | Envuelve |
|---|---|---|
| `linchpin-connector-core` | M | `src/connectors/__init__.py` (protocolos `InventorySource`/`WritebackSink`/`TokenStore`) |
| `linchpin-erp-inventory-read` | XL | Odoo, NetSuite, Business Central, Cin7, Zoho, Sortly |
| `linchpin-accounting-read` | L | QuickBooks Online, Xero |
| `linchpin-writeback-po` | L | crea POs / ajusta stock (bajo el plano de escritura segura) |
| `linchpin-unified-merge-fallback` | M | Merge.dev (cola larga de sistemas) |

### 2.9 Logística, carriers y lotes  (M10 — hoy ~5%)

**Metodología:** capa de **abstracción de carrier (EasyPost primario, 100+ carriers)** + modelo **lote/caducidad con llave GS1** y emisión **FEFO** forzada por el sistema. Multi-carrier siempre **envuelto** (nunca a mano); FEFO/FIFO **construido**.

**Tech:** `easypost==10.6.0`, `shippo==3.9.0` (alterno), `aftership-tracking-sdk==9.0.0`, `httpx`; estándar GS1 (AI 10 lote, AI 17 caducidad). Extra `[logistics]`.

| Skill | Effort | Envuelve |
|---|---|---|
| `linchpin-logistics-carrier-rates` | M | `src/logistics/carrier_provider.py` + `easypost_adapter.py` (rate shopping) |
| `linchpin-logistics-labels-tracking` | M | labels + tracking agregado (AfterShip) |
| `linchpin-fefo-lot-expiry` | L | `src/lots/` (modelo GS1, motor FEFO/FIFO, reporte de caducidad) |

### 2.10 Calidad de datos y MDM  (M11 — hoy ~35%)

**Metodología:** **record linkage probabilístico (Fellegi-Sunter + EM, Splink)** para dedup de SKU/master, con paso determinista de identidad **GTIN/UPC/EAN** y gate de validación **Pandera**; comparadores fuzzy **rapidfuzz** en el blocking. Se **compran** los motores; se **construye** la orquestación SCM-específica.

**Tech:** `splink==4.0.16`, `rapidfuzz==3.14.5`, `python-stdnum==2.2`, `pandera==0.32.0`, `biip` (GS1). Extra `[dataquality]`.

| Skill | Effort | Envuelve |
|---|---|---|
| `linchpin-data-cleaning` | M | `src/data_quality/cleaning.py` + validación Pandera |
| `linchpin-sku-dedup` | L | `identity.py` (GTIN check-digit) + Splink |
| `linchpin-import-mapping` | M | mapeo fuzzy de columnas a esquema canónico |

### 2.11 KPIs financieros de inventario  (M13/§4.5 — hoy ~30%)

**Metodología:** capa de KPIs anclada a estándares — **SCOR DS** (cash-to-cash AM.1.1), **retail-math GMROI**, **DIO (GAAP)** — calculados de forma determinista desde el COGS/inventario promedio/margen que Linchpin ya produce. Se **construye** (no hay librería autoritativa; las cifras deben ser exactas/auditables).

**Tech:** pandas/numpy/scipy/openpyxl (ya presentes); `great_tables==0.22.0` opcional (scorecards).

| Skill | Effort | Envuelve |
|---|---|---|
| `linchpin-financial-kpis-core` | M | `src/financial_kpis.py` (turns, DIO, GMROI, weeks of supply, inv/sales) |
| `linchpin-cash-to-cash-cycle` | S | C2C (DIO+DSO−DPO) |
| `linchpin-excess-obsolete-stranded` | M | E&O / dead stock / stranded |
| `linchpin-executive-kpi-dashboard` | L | scorecard ejecutivo (reusa export/PowerBI/dashboard) |

### 2.12 Alertas, cadencia y aprobación  (M14 — hoy ~15%)

**Metodología:** **APScheduler in-process** (cadencia) + detección de eventos por funciones puras + **Apprise** (email/Slack/webhook) con ledger de idempotencia SQLite y digests narrados por LLM detrás del QA gate; escalamiento = HITL. Mínima dependencia, encaja en el repo tal cual.

**Tech:** `APScheduler==3.11.2`, `apprise==1.11.0`, `slack-sdk==3.42.0`, `Jinja2==3.1.6`; (`Prefect==3.7.5` solo como ruta de upgrade documentada). Extra `[alerting]`.

| Skill | Effort | Envuelve |
|---|---|---|
| `linchpin-inventory-alerting` | L | `src/alerting.py` (eventos stockout/reorder/excess) + ledger idempotente |
| `linchpin-scheduled-digest` | M | `jobs/scheduler.py` (APScheduler + jobstore) |
| `linchpin-alert-approval-hitl` | M | `src/approval.py` + rutas FastAPI de aprobación |

### 2.13 Voice agent — *agente logístico documental*  (M16 — hoy ausente)

> **La capacidad que pediste elevar:** no un marcador de teléfono, sino un agente que **lee y entiende documentos logísticos** y **domina el lenguaje de la logística** para negociar de igual a igual con carriers, forwarders, 3PLs y proveedores. Tres capas: **(A) inteligencia documental → (B) conocimiento logístico → (C) llamada + cumplimiento.**

**(A) Inteligencia documental** — pipeline tipo *router*, no una sola herramienta:
- **Extractor primario: Claude multimodal (PDF/visión) vía Anthropic Messages API con `citations` activadas**, emitiendo un esquema `pydantic` por tipo de documento. Las citas devuelven la página de cada valor extraído → *audit trail* + anti-alucinación (cuando el agente dice "contenedor MSKU1234567 según su B/L", el dato es trazable a una página). Generaliza sobre cientos de plantillas sin entrenar por plantilla.
- **Pre-procesador local: IBM Docling** (layout + tablas TableFormer) para bundles enormes, tablas densas o lanes on-prem/ZDR donde el documento no puede salir de la red.
- **Piso OCR: PaddleOCR (PP-OCRv6)** para escaneos pobres, fotos de papel, manuscritos y documentos CJK.
- **Reserva: Azure AI Document Intelligence (prebuilt-invoice)** solo para lanes de altísimo volumen de *facturas comerciales* (líder en line-items).
- **EDI:** ASN=X12 856, estado de embarque=X12 214 / EDIFACT IFTSTA (AT7: `D1`=entregado), tender response=990, PO=850, factura=810, booking=IFTMIN/IFTMBF → parsers `pyx12` (X12), `pydifact` (EDIFACT), `bots-edi-parser` (multi).
- **Límites de diseño (Anthropic, verificado):** 32 MB / 100–600 págs por request; Files API (beta `files-api-2025-04-14`) hasta 500 MB y `file_id` para ZDR; ~1.5–3k tokens/página + imagen → usar prompt caching + Batch API. **Validar cada campo con check-digit (AWB, contenedor ISO 6346) antes de que el agente lo pronuncie.**

**Documentos cubiertos (12)** → un `pydantic` base compartido (shipper/consignee/notify/HS/peso/Incoterm) + modelo por tipo: **BOL** (MBL/HBL/Sea Waybill/inland), **factura comercial**, **packing list**, **PO**, **ASN (856)**, **delivery note/POD**, **entrada de aduana/CBP 7501**, **arrival notice** (¡last free day!), **freight invoice (210)**, **certificado de origen** (incl. USMCA 9 elementos), **AWB** (MAWB/HAWB), **booking confirmation** (cut-offs VGM/SI). Mapa de campos completo en `src/voice/doc_schemas`.

**(B) Pericia logística** — cuatro capas (de profunda a superficial):
1. **KB curada (RAG) en ElevenLabs Agents** — corpus autoritativo compacto (Incoterms 2020, demurrage/detention/per-diem, tipos de contenedor, accesoriales, OS&D, glosario) con embeddings `multilingual_e5_large_instruct` para carriers multi-región. KB ceñida (cap 20 MB / 300k chars), RAG añade ~250 ms/turno.
2. **Persona + guardrails** con el framework de 6 bloques de ElevenLabs (*Personality, Environment, Tone, Goal, Guardrails, Tools*). Persona = "coordinador de fletes experimentado llamando en nombre de [shipper]". Guardrails en su bloque: no cotizar/negociar tarifas, no admitir responsabilidad, no dar clasificación arancelaria, escalar en disputa.
3. **Playbooks por tipo de llamada** compilados como pasos numerados en el bloque Goal.
4. **Tools (server/webhook) para verdad en vivo + L3** — el conocimiento *general* vive en RAG; los hechos *específicos del embarque* llegan por (a) dynamic variables pre-llamada (`{{container_no}}`, `{{bl_no}}`, `{{eta}}`) y (b) server tools que consultan el TMS/tracking + el **L3 knowledge graph** a mitad de llamada (mantiene la PII fuera del prompt estático). Multilingüe: 31 idiomas + Language Detection tool.

**Los 7 playbooks de llamada** (objetivo · campos · captura · escalamiento):

| # | Llamada | Escala a humano cuando |
|---|---|---|
| 1 | ETA / estado de embarque | roll/blank-sailing afecta entrega comprometida |
| 2 | Confirmación + expedición de PO | discrepancia precio/cantidad; expedición requiere aprobar costo |
| 3 | Reagendar cita de entrega | sin ventana antes del deadline; términos no autorizados |
| 4 | Free-time / demurrage | cargos ya corriendo; disputa de inicio de free-time |
| 5 | Estado de aduana / clearance | examen/hold CBP o disputa de clasificación → broker licenciado |
| 6 | Intake OS&D / daño | **siempre** delega la decisión de responsabilidad; el agente solo levanta el reporte |
| 7 | Solicitud de POD | POD con excepciones/rechazo → enruta a OS&D |

**Cumplimiento (riesgo más alto — requiere visto bueno legal antes de marcar):**
- **Voz IA = "artificial/prerecorded" bajo TCPA** (FCC 24-17, feb 2024); clasificar cada tipo de llamada (operacional/transaccional vs telemarketing) define el estándar de consentimiento.
- **Divulgación de IA prácticamente obligatoria:** EU AI Act Art. 50 (aplica **2 ago 2026**) + CA B.O.T. Act → declarar IA al inicio de cada llamada.
- **Consentimiento de grabación:** anunciar y consentir en cada llamada (estados all-party: CA, FL, IL, MD, MA, MT, NV, NH, PA, WA, DE — verificar lista); interestatal sigue el estado más estricto.
- **GDPR:** base de interés legítimo documentada (LIA) para llamadas B2B; minimizar y fijar retención de grabaciones/transcripciones con PII.

**Tech (verificada jun 2026):** `elevenlabs==2.53.0`, `twilio==9.10.9`, `fastapi>=0.110` (ConvAI REST v1, native Twilio, voicemail detection GA 2025, post-call webhooks); `anthropic` (PDF+citations, Files API beta), `docling==2.105.0`, `paddleocr==3.7.0`, `pydantic==2.13.4`, `pyx12==4.0.0`, `pydifact==0.2.3` / `bots-edi-parser==1.1.1`; reserva Azure Document Intelligence. Extras `[voice]` + `[docai]`.

**Skills (11):**

| Skill | Effort | Envuelve |
|---|---|---|
| `linchpin-doc-schemas` | M | `src/voice/doc_schemas/` (pydantic, 12 tipos + base) |
| `linchpin-logistics-doc-reader` | L | Claude PDF+citations + Docling/PaddleOCR → esquema validado |
| `linchpin-edi-parser` | M | `pyx12`/`pydifact`/`bots` → eventos normalizados al L3 |
| `linchpin-logistics-kb-builder` | S | corpus RAG (Incoterms/reglas/glosario) → ElevenLabs KB |
| `linchpin-shipment-context-brief` | M | dynamic-variables pre-llamada desde L3 + docs |
| `linchpin-live-shipment-lookup` | M | server tool mid-call → TMS/tracking + L3 |
| `linchpin-voice-logistics-agent` | L | persona + guardrails + tools (config del agente) |
| `linchpin-call-playbooks` | M | los 7 flujos (pasos Goal + captura + escalamiento) |
| `linchpin-voice-followup` | L | `src/voice/client.py` (outbound + batch + transcript sync) |
| `linchpin-call-compliance-guard` | L | TCPA/EU AI Act/grabación/DNC/ventana — *gate* antes de marcar |
| `linchpin-osd-intake` | M | reporte OS&D estructurado (sin decisión de responsabilidad) |

**Orden de build:** doc-schemas → doc-reader + edi-parser → kb-builder + call-playbooks → live-lookup + shipment-context-brief → voice-logistics-agent → compliance-guard + osd-intake + voice-followup.

**Preguntas abiertas (bloquean parte del diseño):** (1) ¿qué TMS/tracking integra Linchpin hoy y exponen status-API los carriers, o solo EDI 214/IFTSTA? (2) ¿requisito on-prem/ZDR? → si los docs no salen de la red, liderar con Docling+PaddleOCR local y Claude solo bajo ZDR. (3) clasificación legal por tipo de llamada (transaccional vs telemarketing). (4) idiomas en alcance. (5) esquema del L3 para mapear campos de documentos/EDI a sus nodos/aristas.

### 2.14 Capa de Ejecución Guiada — *nunca dejar al usuario desprotegido* (transversal)

> Envuelve a **todas** las capacidades anteriores y garantiza que ninguna tarea termine en un hueco. Redefine "cubierto": una tarea está cubierta cuando el agente **la ejecuta de forma segura** *o* **entrega una opción lista para ejecutar con guardas**. Sin esto el techo honesto es ~80-90%; con esto el piso de cada capacidad sube a **≥91%**.

**Cómo funciona** — todo resultado consecuente pasa por cuatro pasos: (1) **opciones** (N escenarios ejecutables con trade-offs y default recomendado), (2) **guarda de cobertura** (declara confianza + "lo que NO pude y lo que debes hacer tú" — anti silent-failure), (3) **traspaso guiado** (artefacto pre-llenado + pasos + plazo + riesgo), (4) **escalamiento** (empaqueta y rutea al humano correcto con SLA ante disputa/legal/umbral $). Se apoya en el plano de escritura segura (§1) y el QA gate.

| Skill | Effort | Envuelve |
|---|---|---|
| `linchpin-decision-options` | M | motor de escenarios sobre los engines (p.ej. 3 planes de reorden, 3 adjudicaciones de proveedor) + staging |
| `linchpin-coverage-gate` | S | extiende `jobs/qa.py`: confianza + bloque "residual humano" en cada entregable |
| `linchpin-guided-handoff` | M | paquete de acción: artefacto pre-llenado (PO/email/hoja de conteo/POD/reclamo) + pasos + deadline + riesgo |
| `linchpin-escalation-packet` | M | bundle contexto+opciones+respuesta recomendada+citas → ruteo a humano con SLA |

**El residual humano convertido en opción ejecutable:**
- *Conteo físico (M6):* no cuenta, pero entrega **hoja de conteo priorizada + cronograma + ajuste propuesto** para aprobar.
- *Negociación (M8):* no cierra el precio, pero entrega **BATNA, 3 escenarios de adjudicación y el PO/email redactado**.
- *Responsabilidad OS&D (M16):* no decide culpa, pero entrega **reporte estructurado + paquete de reclamo + recomendación**.
- *Clasificación arancelaria (M8/M16):* no asesora legalmente, pero **propone HS code con confianza + fuentes** y escala a broker.
- *Movimiento físico de slotting (M7):* no mueve cajas, pero entrega **lista de movimientos + plan de zona**.

---

## 3. Stack tecnológico consolidado (`pyproject` extras)

El base se mantiene `numpy/pandas/scipy/openpyxl/matplotlib`. Todo lo nuevo va en extras opcionales con *fallback*:

```toml
[project.optional-dependencies]
forecast     = ["statsforecast>=2.0,<3","utilsforecast>=0.2","mlforecast>=1.0","lightgbm>=4.3","hierarchicalforecast>=1.0"]
mcdm         = ["pymcdm>=1.4","ahpy>=2.1"]
space        = ["py3dbp>=1.1","rectpack>=0.2","ortools>=9.15"]
procurement  = ["httpx>=0.28"]            # + APIs aranceles opcionales
ecommerce    = ["ShopifyAPI>=12.7","python-amazon-sp-api>=2.1"]
erp          = ["python-quickbooks>=0.9","intuit-oauth","xero-python>=14","OdooRPC","netsuite>=0.12","httpx>=0.28"]
logistics    = ["easypost>=10.6","aftership-tracking-sdk>=9","shippo>=3.9"]
dataquality  = ["splink>=4","rapidfuzz>=3.14","python-stdnum>=2.2","pandera>=0.32","biip"]
alerting     = ["APScheduler>=3.11,<4","apprise>=1.11","slack-sdk>=3.42","Jinja2>=3.1"]
voice        = ["elevenlabs>=2.53","twilio>=9.10","fastapi>=0.110"]
docai        = ["anthropic>=0.111","docling>=2.105","paddleocr>=3.7","pydantic>=2.13","pyx12>=4.0","pydifact>=0.2.3"]   # lectura de documentos logísticos + EDI
agentic      = ["claude-agent-sdk>=0.2.105","mcp>=1.28","anthropic>=0.111","jsonschema"]
```

⚠️ **Verificar en Windows/py3.11** las wheels de `numba/llvmlite` (statsforecast) y `lightgbm` (VC++ runtime). `pandas 3.0`/`numpy 2.5` requieren py≥3.12 — **no** subir el piso del repo.

---

## 4. Cómo sube el cumplimiento (vs auditoría)

Tres niveles: **Hoy** (código actual), **Con el plan** (skills de dominio implementadas), y **+ Capa Guiada** (§2.14 — el residual humano queda cubierto con opción ejecutable → piso ≥91%).

| Capacidad / Perfil | Hoy | Con plan | + Capa Guiada |
|---|---|---|---|
| M1 Ingesta/Normalización | 60% | 90% | 93% |
| M2 Forecasting | 70% | 95% | 96% |
| M3 Replenishment | 90% | 97% | 98% |
| M4 ABC/XYZ | 10% | 90% | 93% |
| M5 DDMRP | 5% | 85% | 91% |
| M6 Exactitud/conteo | 0% | 85% | 92% |
| M7 Espacio m³ | 0% | 85% | 91% |
| M8 Procurement | 15% | 85% | 92% |
| M9 Multicanal/FBA | 0% | 85% | 92% |
| M10 Logística | 5% | 80% | 91% |
| M11 Datos/MDM | 35% | 90% | 93% |
| M12 Setup/ERP | 5% | 80% | 91% |
| M13 Dashboards | 85% | 95% | 96% |
| M14 Alertas | 15% | 90% | 93% |
| M15 Staging seguro | 30% | 90% | 94% |
| M16 Voice documental-logístico | 0% | 85% | 92% |
| **Perfil 1 Planner** | 80% | 97% | 98% |
| **Perfil 2 Control** | 10% | 85% | 92% |
| **Perfil 3 Procurement** | 20% | 85% | 92% |
| **Perfil 4 E-commerce** | 10% | 85% | 92% |
| **Perfil 5 Logística** | 5% | 82% | 91% |
| **Perfil 6 Systems/Tech** | 55% | 90% | 94% |

> El último <9% es el **acto humano irreducible** (firmar, contar físicamente, negociar, decidir responsabilidad, asesoría legal/arancelaria). El agente lo deja **preparado, recomendado y con el riesgo señalado** — ese es el sentido de "nunca desprotegido": no que el software firme por ti, sino que nunca te deja sin un camino listo y seguro.

---

## 5. Roadmap por fases (priorizado por ROI)

**Fase 0 — Cimientos agénticos (habilita todo lo demás).** `linchpin-agentic-tooling`, `linchpin-system-connectors`, `linchpin-safe-staging-writeback`, `linchpin-agent-eval`. Sin esto, el writeback a sistemas de clientes no es seguro.

**Fase 1 — Profundizar el núcleo (Perfil 1, lo más demandado).** Forecasting moderno (metrics→auto→intermittent→probabilistic) + ABC-XYZ + KPIs financieros + Alertas. *Quick wins* que elevan el perfil que ya vendes.

**Fase 2 — Desbloqueo de mercado e-commerce.** Shopify + Amazon SP-API + multichannel sync + stranded/restock. Es el mayor multiplicador de clientes (DTC/FBA).

**Fase 3 — Operación y exactitud.** Exactitud/conteo cíclico (M6) + calidad de datos/dedup (M11) + DDMRP. Convierte "planificador" en "operador".

**Fase 4 — Procurement + ERP + escritura.** Landed cost + scorecards + selección + PO + conectores ERP/contabilidad + writeback-PO. Cierra el ciclo de compra.

**Fase 5 — Logística + espacio + voz.** Carriers/FEFO + espacio/slotting + **voice agent documental-logístico**. El "último eslabón" físico y telefónico.

> Cada fase es vendible por separado y entra por un *quick-win* (limpieza de datos, dashboard, ABC) que abre el contrato continuo.

---

## 6. Bibliografía, estándares y metodologías (citas verificadas)

**Forecasting**
- Hyndman & Athanasopoulos (2021). *Forecasting: Principles and Practice* (3ª ed.). OTexts. ISBN 978-0-9875071-3-6. https://otexts.com/fpp3/
- Vandeput (2021). *Data Science for Supply Chain Forecasting* (2ª ed.). De Gruyter. ISBN 978-3-11-067110-0.
- Makridakis, Spiliotis & Assimakopoulos (2022). *M5 accuracy competition*. IJF 38(4):1346-1364. DOI 10.1016/j.ijforecast.2021.11.013
- Wickramasuriya, Athanasopoulos & Hyndman (2019). *MinT reconciliation*. JASA 114(526). DOI 10.1080/01621459.2018.1448825
- Teunter, Syntetos & Babai (2011). *TSB / intermittent & obsolescence*. EJOR 214(3):606-615. DOI 10.1016/j.ejor.2011.05.018

**Clasificación / inventario**
- Silver, Pyke & Thomas (2017). *Inventory and Production Management in Supply Chains* (4ª ed.). CRC Press. ISBN 978-1466558618.
- Axsäter (2015). *Inventory Control* (3ª ed.). Springer. DOI 10.1007/978-3-319-15729-0
- Ramanathan (2006) R-model / Ng (2007) — multi-criteria ABC. DOI 10.1016/j.cor.2004.07.014 / 10.1016/j.ejor.2005.11.018
- Paredes-Rodríguez et al. (2023). *Fuzzy AHP-TOPSIS multicriteria ABC*. DOI 10.1155/2023/7661628

**DDMRP**
- Ptak & Smith (2019). *Demand Driven Material Requirements Planning (DDMRP), Version 3*. Industrial Press. ISBN 9780831136512.
- Ptak & Smith (2018). *The Demand Driven Adaptive Enterprise*. ISBN 9780831136352.
- Ptak & Smith (2023). *Orlicky's MRP* (4ª ed.). McGraw Hill. ISBN 9781264264575.
- Fernandes et al. (2025). *The DDMRP Replenishment Model: An Assessment by Simulation*. Mathematics 13(21):3483. DOI 10.3390/math13213483
- Demand Driven Institute — metodología y criterios "DDMRP Compliant".

**Exactitud / cycle counting**
- Piasecki (2003). *Inventory Accuracy: People, Processes, and Technology*. ISBN 9780972763103.
- APICS/ASCM *CPIM ECM v8.0*, Módulo V.

**Espacio / slotting**
- Bartholdi & Hackman (2019). *Warehouse & Distribution Science* (Rel. 0.98.1). warehouse-science.com (libre).
- Kallina & Lynn (1976). *Cube-Per-Order Index*. Interfaces 7(1):37-46. DOI 10.1287/inte.7.1.37
- Hausman, Schwarz & Graves (1976). *Optimal Storage Assignment*. Mgmt Sci 22(6). DOI 10.1287/mnsc.22.6.629

**Procurement**
- Rezaei (2015). *Best-Worst Method*. Omega 53:49-57. DOI 10.1016/j.omega.2014.11.009
- Ellram (1995). *Total Cost of Ownership*. IJPDLM 25(8). DOI 10.1108/09600039510099928
- ICC *Incoterms 2020*; USITC *HTS* (datos oficiales); CIPS *Procurement & Supply Cycle*.

**Integraciones (docs oficiales)**
- Shopify GraphQL Admin API (versionado `2026-04`); Amazon SP-API (FBA Inventory v1, Reports `2021-06-30`, Fulfillment Inbound `2024-03-20`, LWA).
- Odoo 19.0 External API; Dynamics 365 BC API v2.0/OData v4; NetSuite SuiteTalk REST/SuiteQL; Cin7 Core v2; Zoho Inventory v1; Sortly; QuickBooks Online OAuth2; Xero (`xero-python`); Merge.dev.
- EasyPost / Shippo / AfterShip; GS1 General Specifications (AI 10/17); FDA FSMA §204 (trazabilidad de lote).

**Datos / MDM**
- Christen (2012). *Data Matching*. Springer. ISBN 978-3-642-31163-5.
- Splink (Fellegi-Sunter + EM); GS1 GTIN check-digit; Pandera; RapidFuzz.

**KPIs financieros**
- SCOR Digital Standard (ASCM) — AM.1.1 Cash-to-Cash; ASCM Supply Chain Dictionary (19ª ed.).
- Chopra & Meindl (2019). *Supply Chain Management* (7ª ed.). ISBN 9780134731889.

**Agéntico / voz / cumplimiento**
- Anthropic — *Agent Skills* (estándar abierto 2025-12-18); Claude Agent SDK; MCP Spec 2025-11-25.
- ElevenLabs Agents (ConvAI), native Twilio, voicemail detection, post-call webhooks; Twilio Programmable Voice.
- FCC 24-17 (feb 2024) — *TCPA aplica a voces generadas por IA*. EU AI Act Art. 50 (aplica 2 ago 2026).

**Lectura de documentos logísticos / EDI**
- Anthropic — *PDF support* (límites páginas/tamaño, Files API) y *Citations* (extracción anclada a página). platform.claude.com/docs.
- IBM Docling (AAAI 2025; arXiv 2501.17887) — layout DocLayNet + tablas TableFormer, on-prem. PaddleOCR PP-OCRv6.
- ICC *Incoterms 2020* (Pub. No. 723E). CSCMP *Glossary of Terms*. ASCM *Supply Chain Dictionary* (19ª ed.).
- US CBP — *ISF 10+2* y *Form 7501* (Entry Summary). IATA *Resolution 600a* (Air Waybill). FIATA *FBL / Model Rules*; BIFA STC.
- Rushton, Croucher & Baker (2021). *Handbook of Logistics and Distribution Management* (7ª ed.). Kogan Page. ISBN 978-1-3986-9487-3.
- Christopher (2016). *Logistics & Supply Chain Management* (5ª ed.). Pearson. ISBN 978-1-292-08379-7.
- Estándares EDI: X12 856 (ASN), 214 (estado), 990 (tender), 850 (PO), 810 (factura); EDIFACT IFTSTA / IFTMIN / DESADV.

---

## 7. Riesgos transversales

1. **Peso de dependencias** — mitigado con extras opcionales + *fallback* al núcleo numpy/pandas/scipy. Verificar wheels en Windows/py3.11.
2. **Auth/seguridad de conectores** — OAuth2/LWA, almacenamiento de secretos, rotación; RDT para PII en SP-API; consentimiento/PII en voz.
3. **Escritura a sistemas de clientes** — exclusivamente vía el plano de escritura segura (dry-run, idempotencia, audit, HITL). Nunca tocar el original.
4. **Determinismo / QA gate** — fijar seeds (AutoARIMA/LightGBM), métricas correctas para demanda intermitente (WAPE/RMSSE, no MAPE), y citas que resuelvan en el knowledge graph.
5. **Estabilidad de APIs** — versiones fijadas (`statsforecast>=2,<3`, Shopify `2026-04`, etc.); re-verificar antes de release.

---

*Mantenimiento: actualizar versiones de librerías/APIs cada release; las secciones §2.13 (voice documental-logístico) se completan con la investigación complementaria en curso.*
