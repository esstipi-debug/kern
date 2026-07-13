# 05 · Catálogo de Entregables — Qué Produces y Vendes

> Lo que puedes ofrecer a un cliente, con la **herramienta de Kern** que lo
> genera y tu **valor agregado humano** en cada uno. Kern produce el artefacto;
> tú lo enmarcas, lo defiendes y cierras el paso humano.

Catálogo basado en `scm_agent/modes.py` (entregables por modo) y las **35
herramientas de agente** ya cableadas en `scm_agent/tools.py` (la fuente de
verdad es el registry: `build_default_registry()`).

---

## 📦 Modo Inventory — Especialista en Inventario E-commerce

| Entregable | Herramienta / motor | Tu valor agregado |
|---|---|---|
| **Documento de política de inventario** (targets, reorder points, safety stock, niveles de servicio) | `inventory_optimization` | Aterrizar los targets al apetito de riesgo del cliente |
| **Modelo de reorder-point & safety-stock** | `inventory_optimization` (`policies`, `safety_stock`) | Validar σ_e y *lead times* contra la realidad |
| **Clasificación ABC-XYZ + política por segmento** | `abc_xyz` | Traducir la matriz 9-celdas a acciones de compra |
| **Plan de reconciliación / conteo cíclico (IRA)** | motor `reconciliation` / `cycle_count` | Firmar y coordinar el conteo físico (HANDOFF) |
| **Reporte de excedente y obsoletos (E&O) / dead-stock** | motor `alerting` | Negociar liquidación/devolución con proveedor |
| **Paquete de pronóstico de demanda** | motor `forecasting` (MA/SES/Croston, σ_e) | Juzgar sesgo y eventos que el modelo no ve |
| **Dashboard de KPIs de inventario** | webapp (Portfolio/Detail/Budget/Forecast) | Contar la historia frente al cliente |
| **Plan de compra / reabastecimiento (PO)** | `landed_cost` + writeback | **Aprobar y emitir la PO** (writeback irreversible) |

**KPIs del cliente que reportas en este modo:** IRA, tasa de stockout, fill rate,
inventory turns, DIO, costo de mantener, sell-through, valor de excedente/obsoletos.

---

## 🌐 Modo SCM — Supply Chain Manager / Consultor

| Entregable | Herramienta / motor | Tu valor agregado |
|---|---|---|
| **Diagnóstico / health assessment de cadena** | orquestador (multi-tool) | Priorizar hallazgos por impacto al negocio |
| **Roadmap 30/60/90 días ligado a KPIs** | deck del modo SCM | Comprometer dueños y fechas reales |
| **S&OP / IBP deck + cadencia mensual** | `sop` (`run_sop_cycle`) | **Facilitar la junta y forzar la decisión** (RB-7) |
| **Paquete de plan de demanda** (forecast value-add, sesgo) | motor `forecasting` / `forecastability` | Juzgar sesgo y aporte de valor del pronóstico |
| **Supplier scorecard + QBR trimestral** | `sourcing` (OTIF/PPM, TOPSIS) | Conducir la revisión de negocio con el proveedor |
| **Análisis de cost-to-serve** (por cliente/canal/SKU) | `cost_to_serve` | Decidir a qué clientes/canales servir distinto |
| **Estudio de sourcing & landed-cost / selección de proveedor** | `landed_cost` + `sourcing` (MCDM) | **Adjudicar** y negociar el contrato |
| **Estudio de red / fulfillment (3PL)** | motor `multi_echelon` / `space` | Validar contra contratos y geografía reales |
| **Plan de capital de trabajo / cash-release** (cash-to-cash) | motor `working_capital` | Alinear con finanzas y tesorería |
| **Mapa de riesgo y resiliencia** (single-source, TTR/TTS) | motor `risk` | Priorizar mitigaciones y dueños |
| **Evaluación de sostenibilidad / logística inversa** | motor `reverse_logistics` | Traducir a cumplimiento y costo |
| **Diagnóstico de liderazgo (modelo CHAIN)** | `leadership_chain` | Coaching y plan de desarrollo del equipo |
| **Política + dashboard de inventario** (heredado de Inventory) | (ver tabla anterior) | (ídem) |

**KPIs del cliente que reportas en este modo:** OTIF/DIFOT, perfect order rate,
forecast accuracy (WAPE/MAPE) + sesgo, ciclo cash-to-cash (CCC), cost-to-serve /
costo de SC como % de ingreso, SCOR Nivel-1 (confiabilidad, capacidad de
respuesta, agilidad, costo, activos).

---

## 🧰 Toolkit transversal (módulos del motor disponibles)

Más allá de las herramientas ruteadas, el motor incluye módulos que enriquecen un
entregable:

| Área | Módulos |
|---|---|
| **Planeación** | ABC-XYZ · buffers DDMRP + net-flow · métricas de exactitud (MAPE/WAPE/RMSSE/MASE) |
| **Control** | reconciliación / IRA + plan de conteo cíclico · alertas de stockout/excedente/reorden |
| **Procurement** | *landed cost* (consciente de Incoterm) · supplier scorecards (OTIF/PPM) · máquina de estados de PO |
| **Almacén** | dimensionamiento cube/m³ + slotting COI · *twin* espacial 3D del almacén |
| **Finanzas** | inventory turns · DIO · GMROI · cash-to-cash · sell-through |
| **Calidad de dato** | dígito verificador GTIN/UPC · dedup de SKU · mapeo canónico de columnas |

---

## 📦 Paquetes comerciales (multi-tool, ejecutables de punta a punta)

Los entregables de arriba se venden **empaquetados**, no sueltos. Cada paquete
corre todas sus herramientas en un solo flujo (`examples/run_package.py`) y emite
un **deck consolidado** + el entregable completo de cada herramienta, con la
garantía "QA falla ⇒ no hay entregable" elevada a nivel de paquete. One-pagers
listos para enviar a un prospecto: [documentation/paquetes/](../paquetes/README.md).

| Paquete | Precio (brief) | Herramientas | Tu valor agregado |
|---|---|---|---|
| **Diagnóstico de Arranque** (sprint 2 semanas) | USD 1.500–2.500 único | 4: calidad de datos, ABC-XYZ, E&O, KPIs financieros | Convertir el hallazgo cuantificado en la propuesta del retainer |
| **Starter — Fundamentos de Inventario** | USD 2.000/mes | 8: + pronóstico, política de inventario, what-if, planilla staged, conteo cíclico, newsvendor | Revisar y aprobar el plan de compra staged (RB-5); la revisión mensual |
| **Growth — Operación Completa de SC** | USD 4.000/mes + QBR | 26: + pricing, cost-to-serve, Odoo, red (multi-echelon/DDMRP/DRP/simulación), IRA, FEFO, proveedores, riesgo, DEA | Facilitar la revisión mensual y el QBR; adjudicar y negociar |
| **Scale — Red, S&OP y Mando Ejecutivo** | USD 7.500/mes | 35 (catálogo completo): + S&OP, ubicación de instalaciones, transporte, bodega 3D, slotting, personal (queuing), secuenciación, EVM, liderazgo | Facilitar el ciclo S&OP; forzar la decisión ejecutiva mensual |
| **Retainer Ejecutivo Fraccional** | USD 9.000–12.000/mes | Mismas 35 de Scale — gobierno, no capacidad | Presencia semanal, mandato ejecutivo, escalamiento con SLA (RB-6) |
| **Proyecto de Red, Almacén y Operación** | USD 8.000–18.000 único | 6: ubicación de instalaciones, transporte, bodega 3D, slotting, personal, secuenciación | Facilitar el diseño iterativo en las semanas 3–6 del proyecto |
| **Proyecto de Sourcing y Costo de Importación** | USD 5.000–10.000 único, recurrible | 3: sourcing, landed cost, muestreo de aceptación | Llevar la adjudicación recomendada a la mesa de negociación |

El procedimiento paso a paso está en [RB-9](04_runbooks.md#rb-9--correr-un-paquete-comercial).
Las 8 secciones de la escalera están todas empaquetadas como runner ejecutable
(`scm_agent/package_specs.py`).

---

## 💲 Cómo se traduce en honorarios

| Formato | Entregable típico | Cadencia |
|---|---|---|
| **Proyecto único** | **Diagnóstico de Arranque**, estudio de sourcing, política de inventario | Una vez |
| **Retainer recurrente** | **Paquetes Starter/Growth**, cadencia S&OP mensual, QBR de proveedores | Mensual / trimestral |
| **Por evento** | E&O cleanup, selección de proveedor, plan de cash-release | Según gatillo |

> Cada entregable recurrente (S&OP, QBR, dashboard) es **ingreso recurrente**.
> Kern re-produce el artefacto cada ciclo en segundos; tú vendes la **relación
> y el juicio** que lo acompaña.

---

> El último documento, [06 · Mapa de Competencias](06_competency_map.md), te dice
> qué necesitas saber para entregar este catálogo con credibilidad, y cómo cerrar
> brechas usando la biblioteca L3.
