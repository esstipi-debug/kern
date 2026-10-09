# Kern — Biblia de Marca y Producto

> **Archivo único para producir contenido** (guiones de video, infografías, pauta, reels, decks).
> Está escrito para *leerse como una historia*, no como una hoja de datos: cada bloque abre con **por qué importa**
> antes de dar los números. El eje es siempre relacional — Kern se explica **comparado contra las alternativas reales**
> que el comprador ya está evaluando (§2).
>
> Fuentes de verdad: `KERN_IDENTIDAD_Y_FILOSOFIA.md` (marca) · `KERN_AGENCIA_IA_TESIS_COMERCIAL.md` (posicionamiento
> vs. mercado) · `MONETIZATION_BRIEF.md` (precios de competidores, verificados 07-2026) · `README.md` +
> `CAPABILITY_EXPANSION_PLAN.md` (producto) · `KIT_PUBLICIDAD.md` + `paquetes/` (venta) · `UI_DESIGN_BRIEF.md` (visual).
>
> **Regla dura (aplica a TODO guion e infografía):** cada claim es auditable contra el código o una fuente citada, o
> no se afirma. Los **claims prohibidos** están en §10 — leerlos antes de escribir cualquier pieza.
>
> *v2.0 · 24 jul 2026 · Kern 2.9.0 · 45 capacidades · 93% coverage · 1100+ tests · 25 fuentes SCM*

---

## 0. TL;DR para el creador de contenido (30 segundos)

- **Qué es:** el núcleo (kernel) que **decide y produce** la operación de cadena de suministro de un retailer/distribuidor. Un brief en lenguaje plano entra; sale un entregable terminado (Excel + reporte + gráfico), con QA, fuentes citadas y un humano que firma.
- **Contra qué:** hoy ese negocio elige entre Excel, un SaaS de inventario que igual opera él mismo, una suite enterprise que no puede pagar, contratar un planificador caro, o "una IA" que inventa cifras. **Kern es el único que corre el método por vos y muestra su fuente.** (§2)
- **La frase:** *"Kern convierte tu inventario en un problema resuelto con números, no con corazonadas — y cada número tiene fuente, QA y un humano que lo firma."*
- **El diferenciador de una línea:** *"No promete magia. Promete núcleo."* La autonomía se **gana con evidencia**, no se declara.
- **Tono:** técnico y preciso, cero adjetivos inflados. Nunca "IA de última generación". Sí: "redujo el MAPE de 18% a 11% en 3 ciclos".

---

## 1. El problema (el contexto — el mundo antes de Kern)

Un negocio que factura **USD 1M–15M** —retail, distribución mayorista, manufactura liviana— llega a un punto donde el inventario deja de caber en la cabeza de una persona. Ya tiene **ERP, marketplace y contabilidad**. Lo que le falta no es otro sistema de registro: **le falta el cerebro que decide cuánto comprar, de qué, cuándo y a qué precio.**

Hoy eso se resuelve así (y así es como duele):

- **Se compra "a ojo"** — reglas min/max del ERP o intuición del dueño. Funciona hasta que hay un segundo almacén o 4.000 SKUs.
- **Hay plata atrapada** en stock muerto que nadie cuantificó.
- **No hay política de reposición gobernada** — cada mes es una decisión manual que se rehace desde cero.
- **No hay equipo de data science** ni presupuesto para SAP IBP / Blue Yonder.

Ese es el vacío que Kern ocupa: **no el lugar del ERP, sino el del planificador que el negocio todavía no puede pagar.** El posicionamiento mental es el del kernel de un sistema operativo — no es opcional, es la capa que hace que todo lo demás ejecute.

---

## 2. Contra qué compite Kern — el mapa competitivo ⭐

> **Por qué esta sección es el corazón del contenido:** nadie compra Kern "en abstracto". El comprador ya está eligiendo entre 5–6 alternativas reales. El guion/infografía que gana es el que dice *"conocés esta opción — acá está por qué se queda corta, y qué hace Kern distinto."* Kern **no existe sin el "comparado con qué".**

### 2.1 Las 6 alternativas que el comprador realmente evalúa

**① Excel / la planilla propia** — *el status quo*
- Qué es: min/max a ojo, fórmulas que alguien mantiene… hasta que se va o se olvida.
- Costo: "gratis" (el costo real es el error y el cash atrapado).
- Dónde falla: sin método (σ cruda, no σ del error de pronóstico), sin simulación, sin memoria, se rompe al escalar, depende de una persona.
- **Contra-Kern:** *"Excel no tiene memoria ni método. Kern corre el método cada ciclo y recuerda cuánto se equivocó."*

**② SaaS de inventory planning** — Netstock, Inventory Planner, Cogsy, Prediko, StockTrim, Genie, Cin7, Stocky
- Qué es: una herramienta self-serve. Ancla de precio real (07-2026): **$49–349/mes** self-serve (Prediko $119, Genie $59/99/159, Sumtracker $49), y por cotización **$245+** (Inventory Planner ~$245, Cin7 Core $349+).
- Dónde falla: te dan la **herramienta**, el método correcto queda como **ajuste opcional que el usuario suele errar** (σ cruda en vez de σ_e, sin simular antes de recomendar, sin cita, sin rollback) — **y VOS seguís operándolo**. La mayoría **mira hacia atrás** (dashboard).
- **Contra-Kern:** *"Un SaaS te da la herramienta y te deja operándola. Kern la opera por vos — y no te deja saltarte el paso que el SaaS hace opcional."*

**③ Suites enterprise** — SAP IBP, Blue Yonder, o9
- Qué es: el arsenal completo para grandes empresas.
- Costo: **SAP IBP arranca en ~USD 100.000/año** (precio de lista, "medianas-grandes y grandes").
- Dónde falla: fuera del presupuesto del ICP por 10–100×. No es competencia real para USD 1–15M — recorta ~0% del mercado objetivo.
- **Contra-Kern:** *"SAP IBP arranca en $100k/año para empresas grandes. Kern es el cerebro que un negocio de $2M sí puede pagar."*

**④ Contratar un planificador / consultor** — la alternativa "humana"
- Qué es: un planificador CPIM/CLTD full-time, o fraccional ($3.000–15.000/mes; ejecutivo $100–300/h).
- Dónde falla: caro, no escala al SKU #4.000, el conocimiento se va cuando la persona se va, y **bajo presión salta el paso de simulación**.
- **Contra-Kern (la línea killer):** *"La certificación prueba que un humano alguna vez supo el método. Kern lo corre —citándose, negándose a su propia mala salida, bajo rollback firmado— cada día, sobre cada SKU, incluido el #4.000 que tu equipo no tiene tiempo de tocar. No comprás conocimiento; comprás el método corriendo a las 3am sin que nadie decida saltárselo."*

**⑤ "Herramientas de IA" genéricas / chatbots** — el ruido de 2026
- Qué es: pedirle a un chatbot o una "IA de inventario" que sugiera cuánto comprar.
- Dónde falla: **inventan cifras plausibles**, sin fuente, sin QA, sin reversibilidad.
- **Contra-Kern:** *"La IA genérica inventa un número plausible. Kern, si no puede citar su fuente, no entrega nada."*

**⑥ Odoo / reglas min-max del ERP** — lo que ya tiene instalado
- Qué es: las reglas de reabastecimiento nativas del ERP.
- Dónde falla: reglas estáticas, sin forecast del patrón de demanda, sin política gobernada ni auditoría.
- **Contra-Kern:** *"Kern no reemplaza tu Odoo — lo convierte en el brazo de ejecución de una política gobernada, staged y reversible."*

### 2.2 Matriz maestra — Kern vs. el mercado (infografía central)

> Ideal para una tabla comparativa de columnas con checks. ✅ = lo hace por defecto · ⚠️ = opcional/parcial · ❌ = no lo hace.

| Dimensión que le importa al comprador | Excel | SaaS inventario | Enterprise (SAP IBP) | Planificador/consultor | IA genérica | **Kern** |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| **¿Opera, o solo informa?** | Ninguno | Informa | Opera | Opera | Informa | **Opera** ✅ |
| **Método correcto por defecto** (σ_e, no σ cruda) | ❌ | ⚠️ opcional | ✅ | ⚠️ humano | ❌ | **✅ no-opcional** |
| **Simula antes de recomendar** | ❌ | ⚠️ | ✅ | ⚠️ | ❌ | **✅** |
| **Cita su fuente en cada decisión** | ❌ | ❌ | ❌ | ⚠️ | ❌ | **✅ (≥2 de 25 fuentes)** |
| **Writeback reversible + auditable** | ❌ | ⚠️ | ✅ | n/a | ❌ | **✅ firmado + rollback** |
| **Se auto-verifica (predicho vs. real)** | ❌ | ❌ | ⚠️ | ⚠️ | ❌ | **✅ (loop A4)** |
| **Se niega a entregar si falla QA** | ❌ | ❌ | ❌ | ⚠️ | ❌ | **✅ QA veta** |
| **Precio para USD 1–15M** | "gratis" | $49–349/mes | ~$100k/año | $3–15k/mes | bajo | **$300–5.000/mes** |
| **¿Quién lo opera?** | Vos | Vos | Equipo dedicado | Esa persona | Vos | **Kern + un operador que firma** |

**La lectura en una frase:** Excel y la IA genérica no tienen método; el SaaS lo deja opcional y te lo deja operando a vos; el enterprise es correcto pero impagable; el humano es correcto pero caro y no escala. **Kern es el único que corre el método correcto, por defecto, citándose, a un precio que este negocio sí paga.**

### 2.3 El argumento de dos movimientos: SUPERAR + EXPANDIR

Marco honesto para un video explicativo o un deck (nunca decir "certificado" ni "es el estándar"):

- **SUPERAR** — hacer *no-opcional* lo que el método enseña y el SaaS deja como casilla que el usuario no marca:
  1. Safety stock sobre σ_e (error de pronóstico), no dispersión cruda — el error más común del oficio, hecho el default.
  2. Simular-antes-de-recomendar: ninguna política se entrega sin Monte Carlo de fill rate / backorders / ventas perdidas.
  3. El patrón de demanda dicta el modelo (Croston/TSB para intermitente; nunca asume normalidad).
  4. La QA veta el entregable: un paso que falla ⇒ no se escribe archivo.
- **EXPANDIR** — lo que **ningún SaaS trae**:
  1. Grounding con **cita forzada** (≥2 fuentes o no entrega).
  2. Writeback **firmado, time-boxed, idempotente y reversible** (HMAC atado al hash del changeset).
  3. **Autonomía ganada con evidencia** + degradación inmediata ante drift.
  4. **Never-unprotected**: todo resultado ejecuta o carga un camino ejecutable.
  5. **Evidencia re-ejecutable** (SHA-256 de inputs/outputs) que un auditor puede re-correr.

**La diapositiva de una línea:** *"Un planificador que nunca duerme, nunca salta el paso de simulación, y siempre muestra su fuente."*

---

## 3. La marca

> **Por qué importa:** el nombre carga todo el posicionamiento. "Kern" comunica en 4 letras lo que la §1 explica en párrafos — que esto es infraestructura crítica, no un servicio más.

### 3.1 El nombre
**Kern** = *núcleo* (alemán) + *kernel* (el corazón de un sistema operativo) + *kerning* (el ajuste tipográfico que hace fluir la palabra). Tres disciplinas, un concepto: **el centro que hace que todo funcione como un solo cuerpo.** El cliente ya tiene ERP y marketplace; **le falta el cerebro.**

### 3.2 Qué Kern NO es (oro para guiones de contraste — conecta directo con §2)
| No es… | Porque… |
|---|---|
| un **dashboard** | los dashboards miran hacia atrás; Kern opera el presente y proyecta el siguiente ciclo |
| un **reporte** | los reportes explican qué pasó; Kern decide qué hacer y ejecuta con aprobación |
| un **asistente** | los asistentes esperan que preguntes; Kern vigila 24/7 y propone |
| **"IA" como categoría** | es autonomía ganada con evidencia, no automatización ciega ni cifra inventada |

### 3.3 Los tres tonos (elegí según la pieza)
- **Técnico:** *"El sistema redujo el MAPE de forecast del 18% al 11% en 3 ciclos."*
- **Comercial:** *"Tu baseline de inventario es $500k. El sistema liberará $100k–150k de caja en el primer trimestre, medido por A4."*
- **Filosófico:** *"No automatizamos para que no pienses. Verificamos para que puedas confiar."*

**Tagline maestra:** *Kern — el núcleo que tu cadena no tiene.*

---

## 4. La filosofía: quién opera y quién responde

> **Por qué importa:** es la respuesta a la objeción #1 que recibimos — *"pero básicamente estás ofreciendo automatización"*. No se pelea la palabra: se concede y se mueve el eje. Sí, adentro hay automatización, igual que en los cinco competidores de §2. La diferencia es **qué** se automatiza (el criterio del oficio, no los clicks del cliente), que **rinde cuentas** (se niega a entregar su propia mala salida) y que **la entrega un humano que responde**, con lo que "responder" significa puesto por escrito. La autonomía, mientras tanto, se gana con evidencia — no se declara.

### 4.1 El arco de 4 escenas (storytelling / timeline animado)
- **Mes 1:** un monitor detecta un SKU en punto de reorden → el agente propone la orden → como es la primera vez, **escala a un humano** (T3). El humano aprueba.
- **Mes 2:** compara demanda predicha vs. real. MAPE 8%, bias +3%. Ajusta parámetros solo.
- **Mes 3:** mismo evento; la tool ya tiene 94% de precisión → baja a **un click** (T2).
- **Mes 6:** tras 4 ciclos con precisión >92% → **auto-ejecuta** (T1) y notifica: "Orden ejecutada. Evidencia en A4."

**Remate:** *La autonomía no se asume, se gana. No se configura, se demuestra.*

> **Dos aclaraciones que hacen que este arco no contradiga el mensaje madre:**
> 1. Las cifras (8%, 94%, >92%, 4 ciclos) son **ejemplo de diseño del arco**, no una medición de un cliente. Rotularlas así en pantalla y en copy.
> 2. La autonomía que se gana aplica a la **ejecución mecánica dentro de límites**, nunca al compromiso de plata: lo irreversible siempre exige un humano (`src/writeback.py`), y un monto por encima del umbral se re-rutea a `ESCALATED` (`src/escalation.py`). T1 no significa "sin humano" — significa "sin humano para el paso mecánico ya probado".

### 4.2 "Eso es automatización" — la respuesta (carrusel de 2 columnas)

**La línea de apertura, textual:** *"Sí. Igual que Netstock, que Stocky y que la regla min/max de tu ERP: la hay en los cinco. La pregunta útil no es si hay automatización — es quién la opera y quién responde cuando el número sale mal."*

| Automatización que **operás vos** | Una función que **rinde cuentas y firma un humano** |
|---|---|
| Ejecuta la regla que escribiste vos, hace años, y nadie volvió a mirar | Corre el criterio del oficio: el colchón sobre el error del pronóstico, y el patrón de demanda elige el modelo |
| Siempre entrega algo, aunque no aguante | Si no pasa su propio control de calidad, **no se escribe ningún archivo** |
| Te da un número sin decir de dónde salió | Si no puede fundamentarlo con al menos dos fuentes, no te muestra ninguna — nunca rellena con una cita conveniente |
| Escribe en tu sistema y después te enterás | Nada irreversible sin tu firma; lo reversible queda con rollback y bitácora |
| Si sale mal, la consecuencia es tuya y no hay a quién llamar | Hay alguien con nombre, y está escrito qué pasa si nos equivocamos |
| Cuesta USD 49-349/mes: a ese precio no entra el tiempo de nadie, entra un login | Cuesta lo que cuesta una función, porque incluye a quien la opera |

**El segundo movimiento (el que convierte la amenaza de plataforma en argumento):** *"Tu Shopify o tu Odoo ya te sugieren reponer, gratis, hace dos años. ¿Cuándo fue la última vez que abriste esa pantalla y cambiaste algo con lo que te dijo? El problema nunca fue que falte automatización — falta quien la opere."*

**Disciplina de copy (obligatoria):**
- Nunca decir "no automatizamos" ni "no usamos IA": es falso (§5.4) y suena a esquive.
- Nunca decir "3 de cada 4 veces para y pide permiso" (frecuencia no medida, roza §10). Sí: **"de los cuatro finales posibles, tres paran en un humano por diseño"**.
- Nunca decir "si no puede citar, no entrega nada". Sí: **"si no puede fundamentar con dos fuentes, no te muestra ninguna"**.
- Nunca decir "nada toca tu sistema sin tu firma". Sí: **"nada irreversible sin tu firma"**.
- Nunca decir la palabra "SLA" fuera del Retainer Ejecutivo.

### 4.3 La metáfora del organismo (motion graphics)
Kern no es un edificio que se construye planta por planta. Es un **organismo que crece añadiendo vasos sanguíneos:** cada sensor es un capilar, cada tool un músculo, cada verificación un reflejo más fuerte. Crece porque **nunca deja de medirse.**

### 4.4 Cita de cierre (frase final de video)
> *"No es que la máquina sea perfecta. Es que la máquina sabe cuánto se equivoca y lo dice antes de que tú lo preguntes."* — Principio de diseño Kern A4

### 4.5 Qué significa "responde" (la Carta de Responsabilidad)

"Hay un humano que firma" no se dice sin esto detrás. Se entrega por escrito antes de la primera propuesta:

| Compromiso | Alcance |
|---|---|
| Retrabajo sin cargo | Error de método en un entregable ⇒ se rehace sin cargo dentro de la ventana acordada |
| Ciclo sin entregable = ciclo sin factura | Si la QA veta y el ciclo no sale, ese ciclo no se cobra |
| Fee contingente | Sprint de Liquidación: % del cash recuperado, piso USD 1.500, **USD 0 si no se recupera nada** (`src/contingent_fee.py`) |
| Una persona con nombre | En todos los peldaños. **SLA contractual sólo en el Retainer Ejecutivo** (`package_specs.py`) |
| Lo que **no** es | No hay garantía de resultado ni indemnización por pérdidas. No hay matrícula profesional. Se dice primero, de frente. |

**Y la frontera de alcance, en la misma frase que la promesa:** *"Vos seguí hablando con tus proveedores. Lo que te sacamos de encima es decidir qué pedirles, cuánto y cuándo."* Kern no negocia con proveedores, no persigue la orden, no recibe mercadería y no camina el depósito.

> ⚠️ **Sin firmar** al 24-jul-2026. Hasta que estos 5 compromisos estén acordados, la frase "hay alguien que responde" no se usa en material público.

---

## 5. El producto — cómo funciona

> **Por qué importa:** es el "así se ve" que hace creíble todo lo anterior. Traducí esto a un explainer de 60–90s.

### 5.1 El loop de una frase
**brief → clasifica → corre → valida (QA) → entrega.** Si QA falla, no se entrega nada.

```
Brief (+ datos opcionales)
   → Clasifica intención
   → Corre 1 de 45 capacidades
   → QA gate  ── falla ⟶ NO hay entregable
        │ pasa
   → Aterriza en fuentes (cita libro + código)
   → Entregables: Excel · Reporte · Gráfico + Fuentes
```

### 5.2 El contrato "Nunca desprotegido" (infografía de dona — 4 desenlaces)
Todo resultado consecuente termina en uno de cuatro estados. **Tres requieren un humano por diseño** — esto es lo que hace a Kern confiable en vez de caja negra (y lo que lo separa de la "IA genérica" de §2):

| Desenlace | Qué significa | ¿Humano? |
|---|---|---|
| **EXECUTED** | El agente lo hizo | Autónomo |
| **OPTIONS** | Opciones rankeadas para elegir | Sí |
| **HANDOFF** | Paso humano ya preparado (PO / email / hoja de conteo pre-llenada) | Sí |
| **ESCALATED** | Ruteado a un humano con SLA | Sí |

### 5.3 Las 3 garantías (las que ningún competidor de §2 combina)
- **Nunca desprotegido** — cada resultado ejecuta *o* entrega un siguiente paso ejecutable. Sin callejones sin salida.
- **Writeback en staging seguro** — dry-run por tier de riesgo (`read`/`reversible`/`irreversible`), gateado por aprobación con tiempo límite, aplicado idempotente y con `rollback()`. **Nunca muta un sistema de registro a ciegas.**
- **Protección de datos por defecto** — cada upload se valida y se procesa en un directorio aislado y auto-purgado por job.

### 5.4 Corre con o sin LLM
El núcleo determinístico funciona solo. Un `LLMProvider` (Claude) opcional afina el ruteo y la narrativa — pero **los números no dependen de él.**

---

## 6. Las 45 capacidades — "el método", en catálogo

> **Por qué importa:** esto es exactamente lo que los SaaS de §2 dejan opcional o no traen. No es "features"; es el temario de un planificador, corriendo por defecto. Banco de ideas para reels temáticos.

| Área | Capacidades |
|---|---|
| **Demanda y clasificación** (3) | `abc_xyz` · `forecast` · `whatif` |
| **Inventario y reposición** (11) | `inventory_optimization` · `newsvendor` · `multi_echelon` · `ddmrp` · `simulation` · `digital_twin` · `drp` · `odoo_replenishment` (lee/escribe en Odoo en vivo) · `excel_replenishment` (lee/escribe la planilla del cliente) · `hat_tension` (Mapa de Tensión de Decisión, 5 sombreros) · `hat_settlement` (Plan de Reposición Conciliado) |
| **Control y salud de inventario** (6) | `cycle_count` · `reconciliation` · `excess_obsolete` · `markdown_liquidation` · `fefo` · `data_quality` |
| **Compras y sourcing** (4) | `sourcing` · `supplier_management` (Kraljic) · `landed_cost` · `acceptance_sampling` |
| **Red y logística** (8) | `facility_location` · `network_design` (p-median multi-planta) · `transportation` · `vehicle_routing` · `warehouse_layout` (bodega 3D navegable) · `slotting` · `queuing` · `scheduling` |
| **Pricing y finanzas** (6) | `pricing` · `price_intelligence` (posición vs. competencia) · `price_watch` (recurrente, solo lectura) · `financial_kpis` · `cost_to_serve` · `learning_curve` |
| **Devoluciones, riesgo y benchmarking** (3) | `returns` · `risk` · `dea` |
| **Cadencia de planeación y proyectos** (3) | `sop` · `earned_value` · `launch_readiness` |
| **Liderazgo** (1) | `leadership_chain` (modelo CHAIN) |

**4 ejemplos entrada → salida** (para "así se ve" en video):
| Capacidad | Entrada | Entregable |
|---|---|---|
| 📦 `inventory_optimization` | CSV de demanda | Excel + reporte + CSV: forecast → política `(s,Q)`/`(R,S)` → ajuste a presupuesto |
| 💲 `pricing` | CSV precio/cantidad | Excel + reporte: elasticidad → precio que maximiza margen |
| 🧭 `leadership_chain` | un brief / scores | radar + reporte: perfil de liderazgo CHAIN |
| 🏗️ `warehouse_layout` | parámetros / brief | HTML 3D + layout.json + reporte: bodega navegable |

> ⚠️ **Contexto de honestidad:** el **Control Tower autónomo** (Track A: Sense/Decide/Execute/Verify/Balance) es el **plan 3.0, no está construido**. Hoy Kern es el **motor de producción de un operador humano** que vende y decide. No vender "el CEO opera el sistema".

---

## 7. A quién le habla (ICP) + value props

> **Por qué importa:** el mismo producto se cuenta distinto según quién decide la compra. Tres guiones de 15s listos.

**El cliente ideal:** factura **USD 1M–15M** · retail / distribución / manufactura liviana · de mono-almacén a 2+ CDs · vive en Excel u Odoo · compra "a ojo", sospecha stock muerto · **sin** equipo de data science ni presupuesto SAP · decide el Dueño/CEO (PyME) o el Director de Ops/SC + COO/CFO (mid-market). Geografía Fase 1: México (± Argentina); Fase 2: Colombia, Chile. **Fuera:** Brasil (sin portugués aún).

**No targetear:** micro-empresas sin presupuesto · negocios sin inventario físico · empresas con SAP IBP/Blue Yonder ya desplegado · leads buscando "un chatbot de IA".

**Value prop por persona:**
- **Dueño/CEO (PyME):** *"Sabé en 2 semanas cuánta plata tenés atrapada en tu inventario, con un informe que podés defender ante quien sea — no una corazonada más."*
- **Director de Operaciones/SC:** *"Reemplazá la planilla que ya no alcanza por un ciclo mensual de reposición, pricing y costo de servir, conectado a tu Odoo, revisado por un operador que responde."*
- **COO/Director de Compras:** *"Gobernás una red de plantas y proveedores con un ciclo S&OP real y un mandato ejecutivo fraccional — no con reportes que nadie fuerza a decidir."*

---

## 8. Los 5 ángulos de pauta (cada uno = 1 pieza)

Cada ángulo = trigger + claim auditable + CTA:
1. **"¿Cuánta plata tenés atrapada en stock muerto?"** — `excess_value` es un campo real del motor. → Diagnóstico de Arranque (USD 1.500–2.500).
2. **"Dejá de comprar a ojo en Excel."** — reorden/stock de seguridad con modelos citados de 25 fuentes, no heurística inventada. → Starter.
3. **"Tu operación ya es multi-almacén — tu planilla no."** — reposición conectada a Odoo, staged y reversible. → Growth.
4. **"Pagás solo si recuperamos cash."** — honorario sobre cash efectivamente recuperado (`contingent_fee.py`). → Sprint de Liquidación.
5. **"Cada número que recibís tiene una fuente y una compuerta de calidad."** — QA-gate real + citas + writeback reversible. → cualquier paquete.

---

## 9. Paquetes comerciales (la escalera)

| # | Paquete | Precio | Cadencia | Hook |
|---|---|---|---|---|
| 1 | **Diagnóstico de Arranque** | USD 1.500–2.500 único | Sprint 2 sem | "¿Cuánto tenés atrapado en tu inventario? Lo sabés en 2 semanas." |
| 2 | **Starter — Fundamentos** | USD 900/mes (techo 1.500) | Mensual | "Dejá de comprar a ojo — política de reposición gobernada, todos los meses." |
| 3 | **Growth — Operación Completa** | USD 1.500/mes (techo 3.200) | Mensual + QBR | "Tu operación ya vive en Odoo — el análisis mensual completo." |
| 4 | **Scale — Red, S&OP y Mando** | USD 3.200/mes flat | Quincenal + S&OP | "Gobernás una red real con un ciclo S&OP que fuerza la decisión." |
| 5 | **Retainer Ejecutivo Fraccional** | USD 4.500/mes (upgrade) | Mensual + semanal + SLA | "Un operador fraccional con presencia semanal, no otro reporte." |
| 6 | **Proyecto Red, Almacén y Operación** | USD 8.000–18.000 único | 4–8 sem | "Vas a abrir bodega o rediseñar tu red — decidilo con un estudio cuantitativo." |
| 7 | **Proyecto Sourcing y Costo de Importación** | USD 5.000–10.000 único | Proyecto | "Sabé cuánto cuesta REALMENTE cada proveedor puesto en destino." |
| 8 | **Sprint de Liquidación** | 10–20% del cash recuperado (piso 1.500) | Sprint 2–3 sem | "Pagás solo si recuperamos cash — nunca un fee fijo." |
| 9 | **Diagnóstico de Posición de Precios** | USD 2.000–3.500 único | Sprint 2 sem | "Sabé dónde está tu precio frente a la competencia — con evidencia." |

**Peldaños self-serve (embudo, no ingreso principal):** *Kern Alerts* $49–99/mes · *Chequeo de migración Stocky* $350–400 (ventana muere 31-ago-2026).

---

## 10. ⛔ CLAIMS PROHIBIDOS — leer antes de escribir cualquier pieza

Ninguno de estos es auditable. **Nunca afirmar:**
- ❌ **"IA que predice el futuro" / "IA de última generación"** → hay modelos con bias medible, no magia.
- ❌ **"Autonomía total" / "sin intervención humana"** → 3 de 4 desenlaces requieren humano por diseño.
- ❌ **Cualquier "% de autonomía end-to-end"** (82%, 75-80%…) → cifras desactualizadas, no re-auditadas.
- ❌ **"Kern ahorra X horas"** como cifra dura → no instrumentado. Solo como estimación suave ("hasta ~40 h de análisis en 2 semanas").
- ❌ **Casos de éxito/testimonios con $ ahorrados de clientes reales** → todavía no existen. Los rangos de mercado se citan **como referencia**, nunca como resultado propio.
- ❌ **"Integramos tu inventario de Mercado Libre"** → el conector MELI es de **repricing** (precios de tus propios listados, tu cuenta OAuth), no de inventario. Sí: *"actualizamos tus precios en Mercado Libre de forma segura y reversible."*
- ❌ **"Integración con Shopify/Amazon"** → placeholders de diseño futuro, no productos.
- ❌ **"Panel de control para tu CEO" / "Kern decide por vos"** → es el plan 3.0 (no construido).
- ❌ **"Reemplaza a tu equipo de analistas"** → reemplaza el trabajo mecánico de producir el análisis, no la decisión.

**Regla de oro:** si no lo podés apuntar a un archivo de código o una fuente citada, no lo digas. La credibilidad ES el producto.
**Sobre §2:** los precios de competidores son **referencia de mercado verificada 07-2026** — preséntalos como "ancla de mercado", no como cotización garantizada de un rival hoy.

---

## 11. Identidad visual

### 11.1 Paleta (semántica, calma — el color significa estado, no decora)
| Rol | Light (oklch) | Dark (oklch) |
|---|---|---|
| Superficie | `98% 0 0` | `20% 0.01 260` |
| Texto | `20% 0 0` | `94% 0 0` |
| **Acento** | `62% 0.17 250` (azul) | `70% 0.16 250` |
| **OK / verde** | `64% 0.16 150` | `72% 0.16 150` |
| **Warn / ámbar** | `72% 0.16 80` | `80% 0.15 85` |
| **Riesgo / rojo** | `60% 0.20 25` | `68% 0.19 25` |

Teal de marca (badge): `#5eead4`.

### 11.2 Tipografía
- **UI/títulos:** `Inter`. **Números/tablas:** `IBM Plex Mono` con `tabular-nums` — cifras siempre alineadas. *(Data-dense y calmo es marca, no solo UI.)*

### 11.3 Dirección de arte
Calmo, no ruidoso. Cifras como protagonistas. **Nada de "glow de IA" ni gradientes mágicos** — la estética refuerza *"núcleo invisible que hace funcionar la cadena"*. Assets de logo en `../linchpin-logos/` (marca previa "Linchpin"; de cara al público es **Kern**).

---

## 12. Números de rigor (stat-cards de credibilidad — todos verificables)

- **45** capacidades agente-ruteables, un solo router.
- **25** fuentes SCM curadas como base de citas.
- **1100+** tests · **93%** coverage.
- **4** desenlaces del contrato "nunca desprotegido" (3 con humano).
- **3** tiers de riesgo de writeback, todo con `rollback()`.
- **<1%** de las herramientas de pricing del mercado explican por qué cambian un precio — Kern lo hace en cada decisión.
- **~$100k/año** el piso de SAP IBP vs. **$300–5.000/mes** Kern (§2 — el argumento de accesibilidad).

---

## 13. Banco de copy (frases-gancho listas)

**Hooks de apertura:**
- "Tu cadena ya tiene ERP, marketplace y contabilidad. Le falta el cerebro."
- "El 90% de tu operación de inventario son decisiones que hoy tomás a ojo."
- "¿Cuánta plata tenés atrapada, ahora mismo, en stock que no se mueve?"
- "Los dashboards te dicen qué pasó. Kern decide qué hacer."

**Diferenciación (vs. §2):**
- "No promete magia. Promete núcleo."
- "Un SaaS te da la herramienta y te deja operándola. Kern la opera por vos."
- "SAP IBP arranca en $100k/año. Kern es el cerebro que un negocio de $2M sí puede pagar."
- "La IA genérica inventa un número plausible. Kern, si no puede citar su fuente, no entrega nada."
- "Si falla QA, no hay entregable. Punto."

**Cierres / CTA:**
- "No automatizamos para que no pienses. Verificamos para que puedas confiar."
- "El humano vende y decide. Kern produce 10×."
- "Un planificador que nunca duerme, nunca salta el paso de simulación, y siempre muestra su fuente."

**Tagline maestra:** *Kern — el núcleo que tu cadena no tiene.*

---

## 14. Mapa pieza → sección (dónde buscar para cada formato)

| Pieza | Secciones a usar |
|---|---|
| **Reel 15s "dolor"** | §1 problema + §8 ángulo + §7 persona + tagline |
| **Carrusel "vs el mercado"** | §2.1 (6 alternativas) o §2.2 (matriz) |
| **Video comparativo** | §2 completa + §2.3 SUPERAR/EXPANDIR |
| **Explainer 60–90s** | §5.1 loop + §5.2 contrato + §12 stats |
| **Carrusel "automatización vs autonomía"** | §4.2 |
| **Infografía de pricing** | §9 escalera + §2.2 fila de precio |
| **Video storytelling** | §4.1 arco de 4 meses (T3→T2→T1) |
| **Motion graphics de marca** | §4.3 organismo + §11 paleta |
| **Post de credibilidad** | §5.3 garantías + §12 números + §10 (mostrar que NO exagera) |
| **Thumbnail / stat-card** | §12 números + §11 visual |

---

*Fin. Si un claim no está acá o en las fuentes citadas al inicio, verificarlo contra el código antes de publicarlo.*
