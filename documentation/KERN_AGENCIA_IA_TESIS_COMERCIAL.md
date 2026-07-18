# Kern como agencia de IA — cómo se vende superando y expandiendo los estándares

> **Addendum comercial a** [`KERN_NIVEL_REFERENCIA_SCM.md`](KERN_NIVEL_REFERENCIA_SCM.md).
> Aquel documento prueba, módulo por módulo, la cobertura de Kern frente a CSCP/CPIM/CLTD/SCPro/CPSM y SCOR/ISO 9001/28000. Este toma esa prueba y construye el argumento de venta. **No repite las tablas de capacidad.**
>
> Esta versión pasó por revisión adversarial (un VP de Supply Chain certificado escéptico + un realista de pricing/GTM). Se eliminó toda sobrepromesa que ambos marcaron. **La mitad honesta del pitch es la ventaja competitiva; cada palabra-hype la destruye.**

---

## 0. Regla de oro (lo que hace creíble todo lo demás)

Dos disciplinas, sin excepción:

1. **Mecanismo verificable ≠ resultado demostrado.** Lo que está *en el código* (σ_e, QA-veto, writeback firmado) se afirma con confianza. Lo que *mueve un KPI de un cliente real* (fill rate ↑, cash liberado) **no se afirma como hecho hasta que un cliente real lo demuestre** — con 0 clientes pagando, todo resultado es *esperado*, no *demostrado*.
2. **Nunca una cifra fabricada, nunca una credencial que no tenemos.** Kern **no está certificado** en nada; no puede "cumplir ISO" ni "certificar SCOR" — solo *comportarse de forma consistente* con esas cláusulas. Toda cifra `[bracketed]` en un outreach sale de una corrida real antes de enviarse. Inventar un número o una credencial quema la lista y al comprador certificado.

Palabras prohibidas en copy de cara al cliente (cada una fue marcada por el comprador CSCP como "te atrapa exagerando"): *certificado, audit-grade, "cumple ISO 9001", EXCEED, 10×, digital twin, "la cadena entera operada".* Abajo, cada una está reemplazada por su versión honesta —que resulta ser **más fuerte**.

---

## 1. La tesis en una frase

> **El método correcto de supply chain —el que un planificador CPIM/CLTD aprende y luego salta bajo presión— operado como servicio: corriendo cada SKU cada ciclo, citando su fuente en cada decisión, negándose a entregar trabajo que falla su propia QA, y ejecutando cambios bajo control firmado y reversible. A una fracción del costo de un planificador de tiempo completo, más disciplinado que un SaaS que deja el método como opción, y con evidencia que tu propio auditor puede re-ejecutar.**

Para una diapositiva: **"Un planificador que nunca duerme, nunca salta el paso de simulación, y siempre muestra su fuente."** (Sin "certificado", sin "es el estándar".)

---

## 2. El hueco entre los tres sustratos — superar y expandir

El mercado se apoya en tres cosas, y cada una deja un hueco:

- **Una certificación** (CSCP/CPIM/CLTD/SCPro/CPSM) acredita *la memoria de una persona*. Esa persona hace el trabajo a mano, sobre los SKUs A, una vez por trimestre, y se lleva el conocimiento cuando renuncia.
- **Un estándar** (SCOR / ISO) es un *documento de referencia*. Dice cómo se ve lo bueno; no calcula nada.
- **Un SaaS** (Netstock / Inventory Planner / Cin7) entrega *herramienta*, con el método correcto como ajuste opcional que el usuario suele errar (σ cruda en vez de σ_e, sin simulación, sin cita, sin rollback) — **y tú sigues operándolo**.

### SUPERAR — hacer no-opcional lo que el temario enseña

No "superamos" una certificación haciendo bien lo que enseña; **la hacemos automática e inevitable**. Ese es el argumento honesto y más fuerte:

1. **Safety stock sobre σ_e del error de pronóstico, no dispersión cruda.** El error más común del oficio, hecho el default (`src/forecasting.py`). Cae a σ cruda **solo** con muy pocos períodos — y lo dice cuando lo hace. *No es "imposible de cometer"; es no-opcional por defecto y transparente en su fallback.*
2. **Simular-antes-de-recomendar.** Ninguna política (s,Q)/(R,S)/multi-echelon se entrega sin Monte Carlo de fill rate, backorders y ventas perdidas (`src/simulation.py`).
3. **El patrón de demanda dicta el modelo.** Croston/TSB por ADI≥1.32 y CV²≥0.49; normal-vs-gamma por asimetría. Nunca se asume normalidad sobre demanda intermitente.
4. **La QA veta el entregable.** Un paso que falla `qa()` ⇒ `STATUS_QA_FAILED`, `deliverables={}`, en un solo lugar para las 40 herramientas. *Implementa el comportamiento que ISO 9001 §8.7 (control de salida no conforme) pide — como invariante de código. No estamos certificados en ISO; así ayudamos a que pases la tuya.*

### EXPANDIR — lo que ninguna certificación enseña ni ningún SaaS trae

Aquí está el terreno nuevo. Se describe **como mecanismo**, no como credencial:

1. **Grounding con cita forzada.** Cada resultado *que se entrega* cita ≥2 de 33 fuentes curadas (1953 nodos, `knowledge/scm-books/graph.json`); si no puede fundamentar, **entrega nada** en vez de una conjetura plausible.
2. **Citation gate anti-falso-amigo** (`MIN_CITATIONS=2`, `MAX_HOPS=2`, `EXCLUDED_CONCEPTS`). La explicabilidad como compuerta, no como feature de marketing.
3. **Writeback firmado + time-boxed + idempotente + reversible.** HMAC-SHA256 ligado al *hash de contenido* del changeset (no solo a la clave de idempotencia), TTL 900s, `compare_digest` de tiempo constante, `rollback()`-able; irreversible siempre exige humano (`src/writeback.py`). *Pregúntale a tu ERP si su rastro de auditoría es no-repudiable a nivel de changeset.*
4. **Autonomía ganada con evidencia + degradación inmediata.** El agente gana el derecho a actuar desde evidencia observada y lo pierde al instante ante drift.
5. **Never-unprotected.** Todo resultado es `EXECUTED` o carga un camino ejecutable (`OPTIONS` rankeadas / `HANDOFF` pre-llenado / `ESCALATED` con SLA); el verificador rechaza salidas sin camino y residuales sin `risk_if_skipped`.
6. **Simulación de escenarios de resiliencia** (TTR/TTS, exposición single-source) como capacidad viva — *no un "digital twin" live-conectado a tu WMS; es simulación de red por escenarios.*
7. **Evidencia re-ejecutable** (`src/audit_evidence.py`): SHA-256 de inputs/outputs, totales de control, `formula_versions`. Diseñada para resistir la re-ejecución de un auditor. *No afirmamos cumplir un estándar de auditoría; construimos la evidencia para que sea re-performable.*

**Respuesta a "podríamos contratar a alguien certificado":**
> "La certificación prueba que un humano *alguna vez supo* el método. Nosotros lo *corremos* —citándose, negándose a su propia mala salida, ejecutando bajo rollback firmado— cada día, sobre cada SKU, incluido el #4,000 que tu equipo no tiene tiempo de tocar. No compras conocimiento; compras el método corriendo a las 3am sin que nadie decida saltárselo."

---

## 3. Tabla: estándar → lo que certifica → cómo Kern lo hace no-opcional → capacidad de expansión

| Certificación / estándar | Lo que certifica | Cómo Kern lo hace **no-opcional** (verificado en código) | Capacidad de **expansión** (mecanismo) |
|---|---|---|---|
| **CPIM** (ASCM) | Memoria humana de inventario/planeación | σ_e por defecto; Croston/TSB por patrón; simular-antes — forzado, cada SKU, cada ciclo | Corre la cola larga que un planificador no alcanza |
| **CLTD** (ASCM) | Memoria de logística/distribución | Multi-echelon validado por simulación; DRP; transportation/slotting con evidencia | Proyecto de red/almacén + escalón `scale` |
| **CSCP** (ASCM) | Visión end-to-end | Cobertura media-alta; grounded-or-nothing en plan-source-deliver | Plan-source-deliver operado; MES queda con tu ERP |
| **SCPro** (CSCMP) | Competencia end-to-end | Media-alta; KPIs SCOR L1 (RL/CO/AM) computados hoy | Cadencia S&OP institucionalizada (`scale`) |
| **CPSM** (ISM) | Sourcing y proveedores | Sourcing + landed cost + acceptance sampling vivos (**SRM profundo: parcial**) | Roadmap: programa de desempeño de proveedor |
| **SCOR DS** | Métricas RL/CO/AM/RS/AG/ES | RL/CO/AM computados con IDs de métrica; RS/AG parcial | Roadmap: mapa de resiliencia (RS/AG); módulo ES (hoy gap) |
| **ISO 9001** | §8.7 no conforme, §8.5.6 cambios, §8.5.2 trazabilidad | Comportamiento equivalente en código (QA-veto, writeback firmado, GTIN mod-10) — **no somos ISO-certificados** | Roadmap: pack de preparación para auditoría; trazabilidad GS1/lote |
| **ISO 28000** | Seguridad/continuidad | Never-unprotected; autorización de cambio firmada; fail-loud boot | Roadmap: mapa de resiliencia (single-source, TTR/TTS) |

*(La evidencia re-ejecutable se diseñó en el espíritu de estándares de documentación de auditoría; se menciona solo a compradores que **tienen** un auditor — ver §6/§7.)*

---

## 4. La economía del comprador (corregida)

**El principio, reencuadrado tras la revisión de realismo:** el retailer SMB objetivo (USD 1-15M, dueño/ops no-técnico) **no compara Kern contra un planificador que nunca contrató.** Compara contra *lo que hoy usa de verdad*: **su app de inventario (~AUD 100-300/mes) + sus propias horas.** Ese es el ancla honesto.

> **"No reemplazas tu app; reemplazas las horas que hoy pones tú operándola —y el método que un planificador correría pero tú no tienes tiempo de correr. El costo cargado de contratar ese planificador (~1/3 a 1/2 más caro que `growth`/`scale`) es la comparación aspiracional, no la inmediata."**

Correcciones duras de la revisión:
- **Cotizar y facturar en AUD/NZD, absorbiendo FX y GST.** La regla "USD ×1.5" *inflaba* cada número en la moneda del comprador — un aumento de precio auto-infligido sobre el comprador más sensible. Stripe cobra en USD internamente; el cliente ve moneda local.
- **La aritmética honesta:** `growth` (USD 4k/mes ≈ 48k/año) contra un salario cargado de planificador AU de ~USD 100-120k `[SUPUESTO de mercado]` es **~40-50%**, no "10-20%". El único "10-20%" real del catálogo es el fee contingente de liquidación. *(Un CFO hace esta cuenta de cabeza; equivocarla quema la credibilidad de todos los demás números.)*
- **Meta de ingreso con precios reales:** ≥ USD 8k/mes = **2× `growth`** o **4× `starter`** — no "3 retainers @ 2,700" (2,700 no es un precio del catálogo; era un promedio inventado).

### Resultado por paquete — *mecanismo* (seguro) vs *resultado* (por demostrar)

Precios verificados en `package_specs.py`. La columna de resultado es lo que el mecanismo **debería** mover; se marca como esperado hasta que un cliente fundador lo demuestre.

| Paquete | Precio verificado (USD) | Mecanismo (verificado) | Resultado esperado (a demostrar) |
|---|---|---|---|
| **Diagnóstico de Arranque** | 1,500-2,500 (único) | ABC-XYZ + E&O + baseline cash-to-cash/DIO sobre *sus* datos | Cuantificar cash muerto y exposición antes de cualquier mensual |
| **Starter — Fundamentos** | 2,000/mes | Forecast (σ_e) + reorden + safety stock forzado | Menos stockouts sin sobre-comprar |
| **Growth — Operación** | 4,000/mes (+QBR) | + demanda/oferta/sourcing/logística/cost-to-serve | OTIF, WAPE, cost-to-serve % — medidos, no proyectados |
| **Scale — Red, S&OP** | 7,500/mes | + facility location, transportation, slotting, SCOR L1 | Decisiones de red bajo cadencia ejecutiva |
| **Retainer Ejecutivo** | 9,000-12,000/mes | **Mismo motor que Scale** (35 pasos idénticos) | *No es más análisis* — ver premium abajo |
| **Proyecto Red/Almacén** | 8,000-18,000 (único) | Inflexión estructural (nueva CD, rediseño) | Alternativa a un SOW de consultora, con evidencia re-ejecutable |
| **Proyecto Sourcing** | 5,000-10,000 (único, recurrible) | Landed cost + acceptance sampling | Freight/duty visibles al board |

**El premium del Retainer** (nombrar qué compra, no asumirlo): comparte los 35 pasos de Scale *verbatim* (`package_specs.py` líneas 391-393: "Nothing to re-derive here"). El delta de USD 1.5-4.5k/mes **no es capacidad** — es **gobernanza**: SLA contractual de respuesta de escalamiento + autoridad de writeback autónomo-firmado. *"Pagas accountability y velocidad-de-acción, como un retainer de VP fraccional, no un tier de software."*

### La liquidación contingente como oferta de aterrizaje

Mecánica verificada (`src/contingent_fee.py`): **10-20% del cash realmente recuperado, piso USD 1,500, fee topado al cash recuperado, USD 0 si no se recupera nada.** Dos documentos: el anexo de estimación grita *"ESTA ES UNA ESTIMACIÓN, NO UNA FACTURA"*; se factura sobre el cash medido al cierre.

> **"Cobramos solo un % del cash que devolvemos a tu balance. Si no recuperamos nada, no pagas nada."**

Es la mejor línea del catálogo — pero **exige acceso a datos de inventario+ventas a nivel SKU, y un dueño no entrega eso a un proveedor sin referencias por un email frío.** Por eso el aterrizaje va precedido de confianza barata (§8): NDA mutuo, una primera pasada sobre datos de muestra o read-only, y precio de cliente fundador. La secuencia es **confianza → datos → oferta contingente**, nunca al revés.

---

## 5. Land → Expand: la escalera de precio ES la escalera de autonomía ganada

El catálogo ya es una rampa de confianza: cada peldaño se gana con *evidencia* (EvidenceRecord re-ejecutable, QA-gated). La confianza-de-autonomía se convierte en precio.

| Etapa | Paquete (precio verificado) | Quién opera | Autonomía | Qué desbloquea el siguiente peldaño |
|---|---|---|---|---|
| **LAND** | `liquidacion` (contingente) **o** `diagnostico` (1,500-2,500) | Nosotros (concierge) | Humano decide todo | Un número real de cash / problema cuantificado sobre *sus* datos |
| **PUENTE** ⚠️ | `starter` mes-a-mes, **primer mes con devolución** o piloto pagado 30-60d | Nosotros | Human-in-loop | *Cierra el salto de confianza donde mueren los tratos SMB* |
| **EXPAND 1** | `growth` 4,000/mes (+QBR) | Nosotros → el cliente lee el cockpit | Guided OPTIONS/HANDOFF | Multi-almacén/ERP; el QBR prueba la cadencia |
| **EXPAND 2** | `scale` 7,500/mes (+S&OP) | El cliente opera el cockpit | Aprobación one-click (TTL 900s) | Red real, cadencia S&OP |
| **EXPAND 3** | `retainer_ejecutivo` 9,000-12,000/mes | Cliente opera; Kern autónomo dentro de límites | Writeback autónomo firmado + escalamiento SLA | 6-18 meses de track record → se paga *gobernanza* |

⚠️ **El puente es la corrección crítica de la revisión GTM:** el salto de un diagnóstico único de 2k a un compromiso recurrente de 24k/año es donde mueren los tratos SMB. Insertar un peldaño barato de probar (mes con devolución / piloto pagado) es obligatorio, no opcional.

**Nombres de cara al cliente:** planos y en su idioma —*Diagnóstico / Gestionado / Co-Piloto Ejecutivo*, o "lo corremos nosotros / lo corres con nosotros / se corre solo"—. **Nunca T1/T2/T3** (esos son los tiers de autonomía del producto, un eje interno distinto).

**Oferta de cliente fundador — decidirla ahora, no improvisarla en vivo** `[SUPUESTO — a confirmar por el fundador]`: p.ej. *primeros 3 clientes, 50% off por 6 meses a cambio de referencia + derechos de caso de estudio*. La revisión marcó que el plan invoca "pricing fundador" como respuesta a la objeción de 0-clientes pero **no lo cotiza**; hay que fijarlo antes del primer pitch.

---

## 6. Roadmap de expansión (E1-E5) — nombrado, no pre-vendido

**Corrección de la revisión (YAGNI):** cataloguear 5 SKUs de upsell con pricing `[SUPUESTO]` **antes de tener 1 cliente pagando es procrastinación.** El pricing real de expansión se descubre *con* los primeros clientes. Aquí quedan como **roadmap nombrado** —para responder "¿y después?"— no como catálogo con precio.

| Evo | Cierra | Qué es | Reutiliza |
|---|---|---|---|
| **E4** ⭐ | SCOR + evidencia ISO/auditoría | **"Pack de Preparación para Auditoría"** (ver abajo) | `packages.py`, `audit_evidence`, `financial_kpis`, `cost_to_serve`, `sourcing` |
| **E1** | SCOR **ES** (gap hoy) | Módulo de sostenibilidad (emisiones-to-serve sobre `cost_to_serve`) | libro Grant (ya en L3) + `logistics/modes` |
| **E2** | CPSM SRM (parcial) | Programa de desempeño de proveedor (scorecard multidim + Kraljic) | `mcdm` (BWM/TOPSIS), `risk`, `supplier_scorecard` |
| **E3** | ISO §8.5.2 | Trazabilidad GS1/genealogía de lote | `data_quality` (GTIN), `lots/`, `event_ledger` |
| **E5** | SCOR **RS/AG**, ISO 28000 | Mapa de resiliencia (single-source, TTR/TTS) | `digital_twin`, `simulation`, `risk` |

### ⭐ E4 — "Pack de Preparación para Auditoría" (NO "Auditoría Certificada")

**Corrección más importante de la revisión:** vender una *"Auditoría Certificada SCOR/ISO"* es una tergiversación que cualquier CSCP detecta en tres segundos —**Kern no puede certificar SCOR (es de ASCM) ni conceder conformidad ISO (la dan organismos acreditados)**. Reencuadre honesto:

- **Qué es:** *preparación para auditoría*. Un ciclo trimestral/semestral que produce (a) un scorecard SCOR L1 (RL/CO/AM, computados hoy), (b) un pack de evidencia re-ejecutable (SHA-256, totales de control, `formula_versions`), y (c) un log de no-conformidades — para que el **auditor u organismo del propio cliente** haga su trabajo más rápido.
- **La frase:** *"No certificamos nada nosotros. Producimos evidencia re-ejecutable que tu propio auditor puede inspeccionar desde el SHA-256 hacia arriba. Eso no es un reporte — es material de auditoría."*
- **A quién:** solo a compradores que **tienen** un auditor o persiguen certificación. Para el dueño SMB no-técnico, este lenguaje es invisible (§7).

---

## 7. Objeciones y límites (la parte más fuerte del pitch — se dice el gap junto a la fortaleza)

- **Transform/MES es delgado → frontera de alcance.** "Kern opera *plan-source-deliver*: demanda, inventario, sourcing, logística, red. El scheduling de piso y la explosión MRP son de tu MES/ERP. Hago el plan contra el que esos sistemas ejecutan." *(Corregir la tabla §3 para que diga esto, no "la cadena entera operada".)*
- **Sostenibilidad es knowledge-only → venta de roadmap honesta.** "SCOR RL/CO/AM se computan hoy. Las métricas ambientales (ES) están en el grafo como conocimiento pero aún no computables — ítem de roadmap nombrado (E1), no algo que voy a falsear."
- **SRM parcial → frontera + roadmap.** "Sourcing y landed-cost están vivos y fuertes. El SRM profundo (scorecard multi-tier, riesgo de proveedor) es parcial; el grafo multi-tier es el foso de Fase 3, construido cuando la demanda lo financie. No sobre-vendo una suite SRM completa."
- **Sin key-person risk en el motor, concierge en la entrega.** *(Corrección: no decir "sin single-point-of-failure" y luego "techo 5-8 clientes por horas del fundador".)* "La *función analítica* no tiene riesgo de persona-clave — el método está codificado, no en la cabeza de alguien. Los clientes fundadores reciben atención directa del principal por diseño."

**Las 3 objeciones que más matan el trato — respuestas corregidas:**

1. **"Cero clientes pagando — no seré tu conejillo de indias."** Reencuadrar, no discutir. "Justo. Desriésgalo: `diagnostico`, 1,500-2,500, dos semanas, sin recurrencia, contra *tus* datos. Y serías cliente fundador —atención concierge, influencia en roadmap, precio fundador—, no cliente #500." *(Y liderar el outreach con una hipótesis real, no un número fabricado — ver §8.)*
2. **"Es una caja negra de IA."** "Es lo opuesto, por construcción. Cada resultado *entregado* cita ≥2 fuentes dentro de 2 saltos de un ancla real; si no puede fundamentar, entrega *nada* en vez de una conjetura. Nada escribe a tu sistema de registro sin un changeset firmado, time-boxed, aprobado-por-humano y reversible. Muéstrame el tool de LLM que se niega a responder cuando no puede citar su trabajo."
3. **"Mi equipo/ERP ya hace esto."** "'Hacerlo' y 'hacerlo bien, cada SKU, cada ciclo, con el paso equivocado hecho imposible' son distintos. Tu ERP dimensiona buffers sobre dispersión cruda; Kern usa σ_e. Tu equipo sabe simular antes de comprometer una política, pero no tiene tiempo para la cola larga. Me pagas para que el método correcto corra a las 3am sobre el SKU #4,000."

**Realismo operativo (de la revisión):** el gate de GTIN mod-10 es una fortaleza *pero* los catálogos SMB tienen GTINs sucios/faltantes — no puede ser un muro de rechazo en el intake. Onboarding con **degradación elegante**: proceder con IDs internos de SKU, marcar los huecos de GTIN como un hallazgo corregible, no como bloqueo. Ahí es donde van de verdad las horas del fundador; costearlas.

---

## 8. Motion AU/NZ-first (corregido)

### Comprador
Retailers inventory-heavy USD 1-15M, dueño/ops no-técnico. Industrias: moda, juguetes, homewares, outdoor/cycling, gourmet/suplementos, beauty, pet, auto/4WD (AU + NZ). *(De la lista objetivo `kern-au-nz-target-list.csv`.)*

### Un solo motion en Fase 1 — directo (concierge)
La revisión marcó lanzar dos audiencias a la vez (retailers directos + canal rev-share de implementadores Cin7/Xero/Shopify) como una división del tiempo escaso del fundador. **Fase 1 = directo.** El canal se difiere hasta tener referencias que los partners puedan defender.

### El hook: ventana Stocky como *lead-gen*, no sprint de ingreso
Shopify Stocky cierra el **2026-08-31**. Real y urgente, pero:
- **No vender `starter` a 2k/mes en frío como "reemplazo de Stocky"** — Stocky viene efectivamente gratis en Shopify POS Pro (~USD 89/mes); anclar contra ~cero pierde. Anclar contra *el planificador/servicio*: "no otro dashboard que te dice qué pasó, sino la función de reorden que un planificador correría, como servicio."
- **Tratar la ventana como una ola de leads que alimenta un pipeline de 3-6 meses,** no un sprint de 6 semanas — sobre todo protegiendo capacidad de entrega para que los primeros clientes se vuelvan las referencias que desbloquean al resto.
- Activo vivo: la landing `/stocky-alternative` (https://linchpin.fly.dev/stocky-alternative, 200 verificado).

### El icebreaker honesto (la corrección operativa clave)
El plan original prometía "un hallazgo de cash muerto real desde datos públicos" — **inejecutable**: el inventario/sell-through a nivel SKU de un SMB privado **no es público**. Reemplazo:
- El icebreaker se construye sobre lo **observable en la tienda pública**: amplitud de surtido, tasa de out-of-stock, pricing, velocidad de reviews → una **hipótesis direccional** de sobre/sub-stock, *explícitamente marcada como hipótesis a confirmar con sus datos*.
- El **`diagnostico` pagado es el primer contacto real con datos** (bajo NDA). Ahí se produce el número dólar de verdad — no en un email frío.

### Primer paso concreto
1. Elegir los ~10 targets Stocky-exposed de mayor convicción de la lista.
2. Para cada uno, generar la **hipótesis desde señales de tienda pública** (no una cifra fabricada) como primera línea del email Variante A.
3. Fijar la **oferta de cliente fundador** (§5) antes de enviar.
4. Secuencia de aterrizaje: hipótesis → NDA/datos de muestra → `diagnostico` pagado → puente (`starter` con devolución) → expand.

Cadencia de 3 emails (de los templates GTM): Día 0 icebreaker → Día 4 ángulo Stocky → Día 9 breakup de baja presión (50-70% de respuestas vienen del toque 2-3). Texto plano, inbox real, un prospecto a la vez, sin adjuntos, sin name-dropping de IA.

> **La motion en una frase honesta:** *"Entramos ayudándote a recuperar cash que ya perdiste, crecemos ganando el derecho a actuar en tu nombre un paso respaldado-por-evidencia a la vez, y te retenemos volviéndonos la disciplina de planeación de la que ahora dependes — con evidencia que tu auditor puede re-ejecutar."*

---

## 9. Meta-nota para quien vende

La tensión que la revisión adversarial expuso: **la mitad honesta de este documento (§7, la regla `[SUPUESTO]`, el framing de cliente fundador, mecanismo-vs-resultado) se destruye cada vez que la mitad de marketing alcanza "certificado", "EXCEED", "audit-grade", "10×", "digital twin" o "la cadena entera".** Un comprador CSCP califica todo el pitch por su peor exageración. Quitadas esas ~7 palabras, la sustancia sobrevive al escrutinio —lo cual es raro— **y esa es la ventaja real.**

---

*Cifras verificadas (exactas de `package_specs.py` / `contingent_fee.py`): los 8 precios de paquete, la mecánica del fee contingente, los 35 pasos compartidos Scale/Retainer. Todo `[SUPUESTO]`: salario de planificador AU, pricing de SaaS/apps, precios de E1-E5 y de la oferta fundador, conversiones AUD — verificar contra una oferta/cotización real antes de usar un número específico en un pitch en vivo. Generado 2026-07-17 sobre `documentation/KERN_NIVEL_REFERENCIA_SCM.md` + revisión adversarial de 2 perspectivas.*
