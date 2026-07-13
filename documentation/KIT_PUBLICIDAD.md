# Kit de publicidad — Kern (LATAM)

> Assets listos para usar en pauta. Fundamento completo (ICP, fuentes,
> VERIFICADO vs. ESTIMADO) en [`ICP_Y_DIMENSIONAMIENTO.md`](ICP_Y_DIMENSIONAMIENTO.md) —
> este documento no repite el razonamiento, solo produce el material final.
> Todo lo que aparece acá cumple la regla dura del proyecto: **cada claim de
> venta es auditable contra el código, o no se afirma.**

---

## 1. One-pager de ICP — la ficha del cliente ideal

**Kern LATAM — Cliente Ideal**

| Dimensión | Perfil |
|---|---|
| **Facturación anual** | USD 1M – 15M |
| **Sector** | Retail, distribución mayorista, manufactura liviana — cualquier negocio que compra y almacena inventario físico |
| **Tamaño de operación** | Mono-almacén (entrada) a 2+ plantas/CDs (Scale) |
| **Stack tecnológico** | Excel/planillas propias, o Odoo (implementado o en migración) |
| **Dolor central** | Compra "a ojo", sospecha de stock muerto, sin política de reposición gobernada |
| **Lo que NO tiene** | Equipo propio de data science, ni presupuesto para SAP IBP/Blue Yonder |
| **Quién decide la compra** | Dueño/CEO (empresa chica) · Director de Operaciones/SC/Compras + COO/CFO (mid-market) |
| **Geografía** | **Fase 1 (piloto):** México, solo o + Argentina · **Fase 2 (tras calibrar CPL/cierre):** sumar Colombia y Chile · **A evaluar:** Perú · **Fuera de alcance:** Brasil, hasta que el producto tenga soporte de portugués (`src/i18n.py` solo tiene `es`/`en`) — ver `ICP_Y_DIMENSIONAMIENTO.md` §2.7 |
| **Trigger típico** | Segundo almacén, canal mayorista nuevo, migración a Odoo, o sospecha de plata atrapada en inventario |

**Disqualifiers — no targetear:**
Micro-empresas sin presupuesto real · negocios sin inventario físico (servicios/software) · empresas con SAP IBP/Blue Yonder ya desplegado · leads buscando "un chatbot de IA", no un entregable auditable.

---

## 2. Propuesta de valor central

**Frase única:**
> "Kern convierte tu inventario en un problema resuelto con números, no con
> corazonadas — y cada número tiene fuente, QA y un humano que lo firma."

**Variantes por persona:**

- **Dueño/CEO (PyME chica):** *"Sabé en 2 semanas cuánta plata tenés atrapada
  en tu inventario, con un informe que podés defender ante quien sea —
  no una corazonada más."*
- **Director de Operaciones/SC (mid-market en crecimiento):** *"Reemplazá la
  planilla que ya no alcanza por un ciclo mensual de reposición, pricing y
  costo de servir, conectado a tu Odoo, revisado por un operador que responde."*
- **COO/Director de Compras (empresa con red real):** *"Gobernás una red de
  plantas y proveedores con un ciclo S&OP real y un mandato ejecutivo
  fraccional — no con reportes que nadie fuerza a decidir."*

---

## 3. Ángulos de mensaje para pauta

Cada ángulo está atado a un trigger event (ver `ICP_Y_DIMENSIONAMIENTO.md`
§2.4) y a un claim auditable — nada de "IA mágica".

1. **"¿Cuánta plata tenés atrapada en stock muerto?"**
   Trigger: sospecha de excedente/obsoletos sin cuantificar.
   Claim auditable: `excess_value` es un campo real del motor
   (`src/excess_obsolete.py`) — el Diagnóstico lo entrega en 2 semanas.
   CTA: Diagnóstico de Arranque, USD 1.500-2.500.

2. **"Dejá de comprar a ojo en Excel."**
   Trigger: sin política de reposición, decisiones manuales mes a mes.
   Claim auditable: la política de reorden/stock de seguridad usa modelos
   citados en 25 fuentes curadas de la literatura de SC (`knowledge/scm-books/`),
   no una heurística inventada.
   CTA: Starter, USD 2.000/mes.

3. **"Tu operación ya es multi-almacén — tu planilla no."**
   Trigger: segundo almacén, canal mayorista nuevo, o migración a Odoo.
   Claim auditable: reposición conectada directo a Odoo, staged y reversible
   (`src/writeback.py`, `src/connectors/odoo.py` — conector real, no demo).
   CTA: Growth, USD 4.000/mes.

4. **"Pagás solo si recuperamos cash."**
   Trigger: stock muerto ya identificado, resistencia a pagar un fee fijo.
   Claim auditable: `src/contingent_fee.py` — el honorario se calcula sobre
   el cash efectivamente recuperado, nunca sobre una proyección; anexo de
   cierre real-vs-estimado incluido.
   CTA: Sprint de Liquidación, 10-20% del recupero, piso USD 1.500.

5. **"Cada número que recibís tiene una fuente y una compuerta de calidad."**
   Trigger: desconfianza genérica hacia herramientas de "IA" que inventan
   cifras.
   Claim auditable: QA-gate real (`"si falla QA, no se entrega"`, verificado
   en el runner de paquetes) + citas a 25 fuentes curadas + todo writeback es
   staged/aprobado/reversible (`src/writeback.py`).
   CTA: cualquier paquete — mensaje de credibilidad, no de producto
   específico.

---

## 4. Tabla comprador × paquete × hook

| Paquete | Comprador | Hook de venta |
|---|---|---|
| Diagnóstico de Arranque | Dueño/CEO | "¿Cuánto dinero tenés atrapado en tu inventario? Lo sabés en 2 semanas." |
| Starter — Fundamentos | Dueño/CEO | "Dejá de comprar a ojo — una política de reposición gobernada, todos los meses." |
| Growth — Operación Completa | Director de Operaciones/SC | "Tu operación ya es multi-almacén o vive en Odoo — el análisis mensual completo." |
| Scale — Red y S&OP | Director de SC/COO | "Gobernás una red real con un ciclo S&OP que fuerza la decisión ejecutiva." |
| Retainer Ejecutivo | COO/VP de Supply Chain | "Necesitás un operador fraccional con presencia semanal, no otro reporte." |
| Proyecto Red y Almacén | COO/Director de Operaciones | "Vas a abrir una bodega o rediseñar tu red — decidilo con un estudio cuantitativo." |
| Proyecto Sourcing | Director de Compras | "Sabé cuánto cuesta REALMENTE cada proveedor puesto en destino." |
| Sprint de Liquidación | Dueño/CEO o Compras | "Pagás solo si recuperamos cash — nunca un fee fijo por lo que no se vendió." |

---

## 5. Claims PROHIBIDOS (no auditables — nunca afirmar)

- **"Integramos con Mercado Libre"** — no existe ese conector en el código
  (verificado por grep en todo el repo). Solo Odoo y Excel son conectores
  reales; Shopify/Amazon son placeholders de diseño futuro, no productos.
- **"Kern ahorra X horas de trabajo"** como cifra dura — no está instrumentado
  (§3.4 de `ICP_Y_DIMENSIONAMIENTO.md`). Puede mencionarse como estimación
  ("hasta ~40 h de análisis en 2 semanas"), nunca como medición real.
- **"IA que predice el futuro" / "inteligencia artificial de última
  generación"** — `KERN_IDENTIDAD_Y_FILOSOFIA.md` es explícito: "no hay 'IA
  predictiva' que adivine el futuro. Hay modelos con bias medible y varianza
  controlada." No prometer magia.
- **"Autonomía total" / "sin intervención humana"** — falso: 3 de los 4
  desenlaces posibles (`OPTIONS`/`HANDOFF`/`ESCALATED`) requieren humano por
  diseño (`src/guided.py`), y todo writeback irreversible necesita aprobación
  explícita.
- **Cualquier cifra de "% de autonomía end-to-end"** (82%/75-80%/40-50% u
  otra) — la última auditoría con esos números está desactualizada y no fue
  re-verificada esta sesión (§1.5). No usar en material público hasta
  re-auditar.
- **Cualquier "caso de éxito" o testimonio de cliente con $ ahorrados** — Kern
  no tiene case studies de clientes reales todavía (`case-studies/
  CASE_STUDIES.md` son ejercicios de libro, no corridas de producción). Usar
  rangos de referencia de mercado citados con fuente, nunca presentarlos como
  resultado propio.
- **"Panel de control ejecutivo para tu CEO" / "Kern decide por vos"** —
  describe el plan Kern 3.0 (Track A Control Tower, no construido). Hoy Kern
  es el motor de producción de un operador humano que vende y decide; el CEO
  del cliente recibe el entregable, no opera el sistema.
- **"Reemplaza a tu equipo de analistas"** — Kern explícitamente no reemplaza
  juicio, relación ni responsabilidad (`documentation/operator/01_role_charter.md`);
  reemplaza el trabajo mecánico de producir el análisis, no la decisión.
